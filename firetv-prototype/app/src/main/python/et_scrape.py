# -*- coding: utf-8 -*-
"""Episode Tracker scraping core, running outside Kodi.

This is the piece the Fire TV prototype exists to prove: the unmodified
CocoScrapers module, driven from an ordinary Android app via Chaquopy, with
a small package of Kodi API stand-ins in place of the real ones.

Everything here is deliberately plain Python with no Android dependency, so
the exact same file runs headlessly on a desktop (see tests/run_headless.py).

Entry points, all returning JSON so Kotlin can consume them without a
Python object bridge:

    configure(data_dir, cocoscrapers_dir)  -> set up paths, load settings
    list_providers()                       -> which scrapers exist / are on
    set_providers(names)                   -> enable exactly these
    scrape_episode(imdb, season, episode, show_title, year)
    scrape_movie(imdb, title, year)
"""

import json
import os
import sys
import threading
import time

_STATE = {'ready': False, 'cocoscrapers_dir': None}

# Quality ordering used to rank results, mirroring the Kodi add-on.
_QUALITY_RANK = {'4K': 4, '1080p': 3, '720p': 2, 'SD': 1, 'SCR': 0, 'CAM': 0}


def _shim_dir():
	return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kodi_shim')


def configure(data_dir, cocoscrapers_dir):
	"""Put the Kodi stand-ins and CocoScrapers on the import path.

	`data_dir`          somewhere writable (Android: context.getFilesDir())
	`cocoscrapers_dir`  the folder that CONTAINS the `cocoscrapers` package
	"""
	shim = _shim_dir()
	# The shim must win over anything else claiming these module names.
	if shim in sys.path:
		sys.path.remove(shim)
	sys.path.insert(0, shim)

	if cocoscrapers_dir and cocoscrapers_dir not in sys.path:
		sys.path.insert(1, cocoscrapers_dir)

	import xbmcaddon
	xbmcaddon.configure(data_dir)

	_STATE['cocoscrapers_dir'] = cocoscrapers_dir
	_STATE['ready'] = True
	return json.dumps({'ok': True, 'shim': shim, 'cocoscrapers': cocoscrapers_dir})


def _require_ready():
	if not _STATE['ready']:
		raise RuntimeError('call configure() first')


def _import_cocoscrapers():
	_require_ready()
	import cocoscrapers
	return cocoscrapers


def list_providers():
	"""Every provider on disk, and whether it is currently enabled."""
	_require_ready()
	import xbmcaddon
	base = os.path.join(_STATE['cocoscrapers_dir'] or '', 'cocoscrapers',
						'sources_cocoscrapers')
	found = []
	for root, _dirs, files in os.walk(base):
		folder = os.path.basename(root)
		for name in files:
			if name.endswith('.py') and name != '__init__.py':
				found.append({'name': name[:-3], 'group': folder})
	settings = xbmcaddon.all_settings()
	for item in found:
		item['enabled'] = settings.get('provider.' + item['name']) == 'true'
	found.sort(key=lambda i: (i['group'], i['name']))
	return json.dumps(found)


def set_providers(names):
	"""Enable exactly `names` (a list or comma-separated string), disable the rest."""
	_require_ready()
	import xbmcaddon
	if isinstance(names, str):
		names = [n.strip() for n in names.split(',') if n.strip()]
	wanted = set(names or [])
	addon = xbmcaddon.Addon('script.module.cocoscrapers')
	enabled = []
	for item in json.loads(list_providers()):
		on = item['name'] in wanted
		addon.setSetting('provider.' + item['name'], 'true' if on else 'false')
		if on:
			enabled.append(item['name'])
	return json.dumps({'enabled': sorted(enabled)})


def _text(value):
	"""Scrapers hand these straight to string operations - never pass a number."""
	return '' if value is None else str(value)


def _providers_for(coco, movie):
	attribute = 'hasMovies' if movie else 'hasEpisodes'
	usable = []
	for name, source_cls in coco.sources():
		try:
			if getattr(source_cls, attribute, True):
				usable.append((name, source_cls))
		except Exception:
			continue
	return usable


def _run(data, movie, timeout=45):
	coco = _import_cocoscrapers()
	providers = _providers_for(coco, movie)
	if not providers:
		return {'sources': [], 'providers': 0,
				'error': 'no providers enabled - call set_providers() first'}

	results, lock = [], threading.Lock()

	def worker(name, source_cls):
		try:
			found = source_cls().sources(data, []) or []
			if found:
				with lock:
					for item in found:
						item.setdefault('provider', name)
						results.append(item)
		except Exception as exc:  # a broken provider must not sink the run
			import xbmc
			xbmc.log('provider %s failed: %s' % (name, exc), xbmc.LOGWARNING)

	threads = []
	for name, source_cls in providers:
		thread = threading.Thread(target=worker, args=(name, source_cls), name=name)
		thread.daemon = True
		threads.append(thread)
		thread.start()

	deadline = time.time() + timeout
	while time.time() < deadline and any(t.is_alive() for t in threads):
		time.sleep(0.25)

	# de-duplicate on info hash, then rank best-first
	seen, unique = set(), []
	for item in results:
		key = (item.get('hash') or item.get('url') or '').lower()
		if not key or key in seen:
			continue
		seen.add(key)
		unique.append({
			'provider': item.get('provider', ''),
			'quality': item.get('quality', 'SD'),
			'name': item.get('name', ''),
			'hash': item.get('hash', ''),
			'url': item.get('url', ''),
			'seeders': int(item.get('seeders') or 0),
			'size': float(item.get('size') or 0),
			'info': item.get('info', ''),
		})
	unique.sort(key=lambda i: (_QUALITY_RANK.get(i['quality'], 0),
							   i['seeders'], i['size']), reverse=True)
	return {'sources': unique, 'providers': len(providers)}


def scrape_episode(imdb, season, episode, show_title, episode_title='',
				   year='', tvdb='', tmdb='', premiered=''):
	"""Scrape one episode.

	`episode_title` is not optional in practice. Measured against the live
	providers, passing an empty title drops the result count from 27 to 0
	for the same episode - the scrapers use it when matching release names -
	so always supply it.
	"""
	data = {
		'title': _text(episode_title),
		'tvshowtitle': _text(show_title),
		'year': _text(year),
		'imdb': _text(imdb),
		'tvdb': _text(tvdb),
		'tmdb': _text(tmdb),
		'season': _text(season),
		'episode': _text(episode),
		'premiered': _text(premiered)[:10],
		'aliases': [],
	}
	return json.dumps(_run(data, movie=False))


def scrape_movie(imdb, title, year=''):
	# No tvshowtitle: its presence is what puts a scraper into episode mode.
	data = {
		'title': _text(title),
		'year': _text(year),
		'imdb': _text(imdb),
		'premiered': '',
		'aliases': [],
	}
	return json.dumps(_run(data, movie=True))
