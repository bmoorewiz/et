# -*- coding: utf-8 -*-
"""Source scraping via the script.module.cocoscrapers module.

Loads the CocoScrapers providers the user has enabled, runs each provider's
episode scraper concurrently, then normalises, filters and ranks the results.
"""

import os
import sys
import time
import threading

import xbmcaddon
import xbmcvfs

from resources.lib import control
from resources.lib import cache

COCO_ID = 'script.module.cocoscrapers'

_QUALITY_RANK = {'4K': 4, '1080p': 3, '720p': 2, 'SD': 1, 'CAM': 0, 'SCR': 0}

# scrape() sentinels, distinct from an ordinary empty result
MODULE_MISSING = None
NO_PROVIDERS = 'no_providers'


def available():
	return _load() is not None


def _load():
	"""Import and return the cocoscrapers package, or None if unavailable."""
	try:
		addon = xbmcaddon.Addon(COCO_ID)
	except Exception:
		return None
	try:
		lib_path = xbmcvfs.translatePath(
			os.path.join(addon.getAddonInfo('path'), 'lib'))
		if lib_path not in sys.path:
			sys.path.append(lib_path)
		import cocoscrapers  # noqa: E402  (path is set up above)
		return cocoscrapers
	except Exception:
		control.error('failed to import cocoscrapers')
		return None


def open_settings():
	try:
		xbmcaddon.Addon(COCO_ID).openSettings()
		return True
	except Exception:
		return False


def is_movie(entry):
	return (entry or {}).get('media_type') == 'movie'


def _providers(coco, movie=False):
	"""Enabled providers that can scrape the requested media type."""
	try:
		providers = coco.sources()
	except Exception:
		control.error('cocoscrapers.sources() failed')
		return []
	attribute = 'hasMovies' if movie else 'hasEpisodes'
	usable = []
	for name, source_cls in providers:
		try:
			if getattr(source_cls, attribute, True):
				usable.append((name, source_cls))
		except Exception:
			continue
	return usable


def _build_data(entry):
	"""Build the `data` dict the CocoScrapers episode scrapers expect.

	Every value here must be a *string*. The scrapers hand these straight to
	string operations - source_utils.check_title() does
	``title.replace('&', 'and').replace(year, '')`` - so passing Trakt's
	JSON-decoded integers (year, tvdb, tmdb) makes that call fail and every
	candidate release gets discarded, which looks exactly like "no sources
	found".
	"""
	def text(value):
		return '' if value is None else str(value)

	if is_movie(entry):
		# Movie scrapers key off title/year/imdb and must NOT see a
		# tvshowtitle - its presence is what switches them to episode mode.
		data = {
			'title': text(entry.get('title')),
			'year': text(entry.get('year')),
			'imdb': text(entry.get('imdb')),
			'tmdb': text(entry.get('tmdb')),
			'premiered': text(entry.get('released'))[:10],
			'aliases': [],
		}
	else:
		data = {
			'title': text(entry.get('ep_title')),
			'tvshowtitle': text(entry.get('show_title')),
			'year': text(entry.get('show_year')),
			'imdb': text(entry.get('show_imdb')),
			'tvdb': text(entry.get('show_tvdb')),
			'tmdb': text(entry.get('show_tmdb')),
			'season': text(entry.get('season')),
			'episode': text(entry.get('episode')),
			'premiered': text(entry.get('first_aired'))[:10],
			'aliases': [],
		}
	# A few scrapers take a debrid hint; give them the highest-priority
	# provider that is actually set up.
	try:
		from resources.lib import debrid
		active = debrid.providers()
		if active:
			data['debrid_service'] = active[0]
			if active[0] == 'Real-Debrid':
				data['debrid_token'] = control.setting('rd.token')
			else:
				data['debrid_token'] = control.setting('torbox.api_key')
	except Exception:
		pass
	return data


def scrape(entry, progress_cb=None):
	"""Scrape episode sources for a next-up entry. Returns a ranked list."""
	coco = _load()
	if coco is None:
		return None  # signals "module missing" to the caller

	cache_key = _cache_key(entry)
	cached = cache.get(cache_key)
	if cached is not None:
		return _filter_and_rank(cached)

	providers = _providers(coco, movie=is_movie(entry))
	if not providers:
		# CocoScrapers only returns providers whose "provider.<name>" setting
		# is enabled, so an empty list means none are turned on - a different
		# problem from having scraped and found nothing.
		control.log('CocoScrapers returned no enabled episode providers')
		return NO_PROVIDERS

	data = _build_data(entry)
	host_dict = []  # torrent providers ignore this; hosters would use RD domains
	results = []
	results_lock = threading.Lock()

	def run_provider(name, source_cls):
		try:
			found = source_cls().sources(data, host_dict) or []
			if found:
				with results_lock:
					for item in found:
						item.setdefault('provider', name)
						results.append(item)
		except Exception:
			control.debug('provider "%s" raised' % name)

	threads = []
	for name, source_cls in providers:
		t = threading.Thread(target=run_provider, args=(name, source_cls),
							  name=name)
		t.daemon = True
		threads.append(t)
		t.start()

	# Bounded wait so a single slow provider cannot hang playback.
	deadline = time.time() + 45
	total = len(threads)
	while time.time() < deadline:
		alive = [t for t in threads if t.is_alive()]
		if progress_cb:
			done = total - len(alive)
			progress_cb(int(done * 100 / total) if total else 100, len(results))
		if not alive or control.aborted():
			break
		control.sleep(500)

	# de-duplicate on info hash / url
	seen = set()
	unique = []
	for item in results:
		key = (item.get('hash') or item.get('url') or '').lower()
		if key and key in seen:
			continue
		seen.add(key)
		unique.append(item)

	# Only cache a real result. Caching an empty list would hide sources for
	# the whole cache window after one bad scrape - a transient network
	# failure, or providers not yet enabled - and look like a permanent
	# "nothing found".
	if unique:
		cache.set(cache_key, unique,
				  hours=max(1, control.get_int('cache.hours', 6)))
	else:
		control.log('no sources returned by %d provider(s) for %s'
					% (len(providers), _describe(entry)))
	return _filter_and_rank(unique)


def _describe(entry):
	if is_movie(entry):
		return '%s (%s)' % (entry.get('title'), entry.get('year'))
	return '%s S%sE%s' % (entry.get('show_title'),
						  entry.get('season'), entry.get('episode'))


def _cache_key(entry):
	if is_movie(entry):
		return 'sources_movie_%s' % (entry.get('imdb') or entry.get('movie_trakt'))
	return 'sources_%s_s%se%s' % (
		entry.get('show_imdb') or entry.get('show_trakt'),
		entry.get('season'), entry.get('episode'))


def cached_sources(entry):
	"""Return the ranked source list already cached for this episode, if any.

	Used to fall through to the next source on a playback failure without
	re-scraping.
	"""
	items = cache.get(_cache_key(entry))
	return _filter_and_rank(items) if items else []


def annotate_cached(sources):
	"""Flag which debrid providers report each source as already cached.

	Returns ``(sources, usable)``. Real-Debrid deprecated its cache-check
	endpoint and answers with nothing useful; TorBox's works. When no
	provider gives a usable answer every source is left unflagged rather
	than being wrongly marked uncached.
	"""
	if not sources or not control.get_bool('sources.check_cache', True):
		return sources, False
	try:
		from resources.lib import debrid
		hashes = [s.get('hash') for s in sources if s.get('hash')]
		mapping, usable = debrid.cached_hashes(hashes)
	except Exception:
		control.error('cache check failed')
		return sources, False
	if not usable:
		control.log('no debrid provider returned usable cache information; '
					'listing all sources unflagged')
		return sources, False
	for item in sources:
		holders = mapping.get((item.get('hash') or '').lower(), [])
		item['cached_by'] = holders
		item['rd_cached'] = bool(holders)
	if control.get_bool('sources.only_cached', False):
		sources = [s for s in sources if s.get('cached_by')]
	else:
		sources.sort(key=lambda s: not s.get('cached_by'))
	return sources, True


def _number(value, default=0):
	"""Coerce a provider-supplied number, which is not always a number.

	Providers are third-party code; seeders and size arrive as ints, strings,
	None, and occasionally something like 'lots'. Anything unparseable has to
	sort as zero rather than take the whole source list down with it.
	"""
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def _filter_and_rank(items):
	allowed = set()
	if control.get_bool('quality.4k', True):
		allowed.add('4K')
	if control.get_bool('quality.1080p', True):
		allowed.add('1080p')
	if control.get_bool('quality.720p', True):
		allowed.add('720p')
	if control.get_bool('quality.sd', True):
		allowed.update(('SD', 'CAM', 'SCR'))

	min_seeders = control.get_int('filter.min_seeders', 0)

	filtered = []
	for item in items:
		# only torrent/magnet sources are resolvable through this addon
		magnet = item.get('url', '')
		if not (item.get('hash') or magnet.startswith('magnet:')):
			continue
		quality = item.get('quality', 'SD')
		if quality not in allowed:
			continue
		seeders = _number(item.get('seeders'))
		if min_seeders and seeders and seeders < min_seeders:
			continue
		filtered.append(item)

	filtered.sort(key=lambda i: (
		_QUALITY_RANK.get(i.get('quality', 'SD'), 0),
		_number(i.get('seeders')),
		_number(i.get('size')),
	), reverse=True)

	limit = control.get_int('results.limit', 150)
	return filtered[:limit] if limit else filtered
