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


def _episode_providers(coco):
	"""Return the enabled providers that can scrape episodes."""
	try:
		providers = coco.sources()
	except Exception:
		control.error('cocoscrapers.sources() failed')
		return []
	episode_providers = []
	for name, source_cls in providers:
		try:
			if getattr(source_cls, 'hasEpisodes', True):
				episode_providers.append((name, source_cls))
		except Exception:
			continue
	return episode_providers


def _build_data(entry):
	"""Build the `data` dict the CocoScrapers episode scrapers expect."""
	title = entry.get('ep_title') or ''
	tvshowtitle = entry.get('show_title') or ''
	data = {
		'title': title,
		'tvshowtitle': tvshowtitle,
		'year': entry.get('show_year'),
		'imdb': entry.get('show_imdb') or '',
		'tvdb': entry.get('show_tvdb') or '',
		'tmdb': entry.get('show_tmdb') or '',
		'season': str(entry.get('season')),
		'episode': str(entry.get('episode')),
		'premiered': (entry.get('first_aired') or '')[:10],
		'aliases': [],
	}
	if control.setting('rd.token'):
		data['debrid_service'] = 'Real-Debrid'
		data['debrid_token'] = control.setting('rd.token')
	return data


def scrape(entry, progress_cb=None):
	"""Scrape episode sources for a next-up entry. Returns a ranked list."""
	coco = _load()
	if coco is None:
		return None  # signals "module missing" to the caller

	cache_key = 'sources_%s_s%se%s' % (
		entry.get('show_imdb') or entry.get('show_trakt'),
		entry.get('season'), entry.get('episode'))
	cached = cache.get(cache_key)
	if cached is not None:
		return _filter_and_rank(cached)

	providers = _episode_providers(coco)
	if not providers:
		return []

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

	cache.set(cache_key, unique, hours=max(1, control.get_int('cache.hours', 6)))
	return _filter_and_rank(unique)


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
		try:
			seeders = int(item.get('seeders', 0) or 0)
		except (ValueError, TypeError):
			seeders = 0
		if min_seeders and seeders and seeders < min_seeders:
			continue
		filtered.append(item)

	filtered.sort(key=lambda i: (
		_QUALITY_RANK.get(i.get('quality', 'SD'), 0),
		int(i.get('seeders', 0) or 0),
		float(i.get('size', 0) or 0),
	), reverse=True)

	limit = control.get_int('results.limit', 150)
	return filtered[:limit] if limit else filtered
