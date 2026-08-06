# -*- coding: utf-8 -*-
"""Request router: maps plugin:// actions to behaviour and builds menus."""

import sys
import json
import base64

import xbmc

from resources.lib import control
from resources.lib import trakt
from resources.lib import realdebrid
from resources.lib import scrapers
from resources.lib import cache
from resources.lib import player
from resources.lib import updater


def _encode(obj):
	return base64.urlsafe_b64encode(json.dumps(obj).encode('utf-8')).decode('ascii')


def _decode(text):
	return json.loads(base64.urlsafe_b64decode(text.encode('ascii')).decode('utf-8'))


def dispatch():
	params = control.parse_params(sys.argv[2] if len(sys.argv) > 2 else '')
	action = params.get('action')

	if action == 'next_episodes':
		return next_episodes_menu(refresh=params.get('refresh') == '1')
	if action == 'sources':
		return sources_menu(_decode(params['entry']))
	if action == 'autoplay':
		return autoplay(_decode(params['entry']))
	if action == 'play':
		payload = _decode(params['data'])
		return player.play(payload['source'], payload['entry'])
	if action == 'mark_watched':
		return mark_watched(_decode(params['entry']))
	if action == 'trakt_auth':
		return _auth(trakt.authenticate)
	if action == 'trakt_revoke':
		return _revoke(trakt.revoke)
	if action == 'rd_auth':
		return _auth(realdebrid.authenticate)
	if action == 'rd_revoke':
		return _revoke(realdebrid.revoke)
	if action == 'check_updates':
		return updater.check_and_prompt()
	if action == 'install_update':
		return updater.prompt_install(params['version'])
	if action == 'clear_cache':
		return clear_cache()
	if action == 'coco_settings':
		return open_coco_settings()
	if action == 'refresh':
		return xbmc.executebuiltin('Container.Refresh')
	if action == 'settings':
		return control.open_settings()

	return main_menu()


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------

def main_menu():
	# A previously-cached update result is shown immediately; the refresh for
	# the next visit happens on a background thread so the menu never stalls.
	pending = updater.pending_update()
	if pending:
		control.add_directory_item(
			'[COLOR lime]%s[/COLOR]' % (control.langf(33040, pending)),
			{'action': 'install_update', 'version': pending}, is_folder=False)

	if not scrapers.available():
		control.add_directory_item(
			'[COLOR red]%s[/COLOR]' % control.lang(33008),
			{'action': 'settings'}, is_folder=False)

	control.add_directory_item(
		'[B]%s[/B]' % control.lang(33001),
		{'action': 'next_episodes'},
		art={'icon': control.addon_icon},
		info={'plot': control.lang(33001)})

	if not trakt.authorized():
		control.add_directory_item(
			'[COLOR orange]%s[/COLOR]' % control.lang(33004),
			{'action': 'trakt_auth'}, is_folder=False)
	if not realdebrid.authorized():
		control.add_directory_item(
			'[COLOR orange]%s[/COLOR]' % control.lang(33005),
			{'action': 'rd_auth'}, is_folder=False)

	if not pending:
		control.add_directory_item(
			control.lang(33029), {'action': 'check_updates'}, is_folder=False)

	control.add_directory_item(
		control.lang(33003), {'action': 'settings'}, is_folder=False)

	control.end_directory(content='')

	# fire-and-forget refresh of the cached update state
	updater.auto_check()


def next_episodes_menu(refresh=False):
	if not trakt.authorized():
		control.notify(33006)
		control.add_directory_item(control.lang(33004),
								   {'action': 'trakt_auth'}, is_folder=False)
		control.end_directory(content='')
		return

	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33009))
	try:
		entries = trakt.next_episodes(refresh=refresh)
	finally:
		pd.close()

	if not entries:
		control.add_directory_item(control.lang(33010),
								   {'action': 'refresh'}, is_folder=False)
		control.end_directory(content='')
		return

	autoplay = control.get_bool('results.autoplay', False)
	for entry in entries:
		_add_episode_item(entry, autoplay)

	control.end_directory(content='episodes')


def _add_episode_item(entry, autoplay):
	try:
		season = int(entry.get('season') or 0)
		episode = int(entry.get('episode') or 0)
	except (ValueError, TypeError):
		season, episode = 0, 0
	label = '%s - %dx%02d - %s' % (
		entry.get('show_title', ''), season, episode,
		entry.get('ep_title', ''))

	encoded = _encode(entry)
	info = {
		'mediatype': 'episode',
		'tvshowtitle': entry.get('show_title', ''),
		'title': entry.get('ep_title', ''),
		'season': season,
		'episode': episode,
		'plot': entry.get('plot', ''),
		'aired': (entry.get('first_aired') or '')[:10],
		'premiered': (entry.get('first_aired') or '')[:10],
	}
	context = [
		(control.lang(33022),
		 'RunPlugin(%s)' % control.build_url(
			 {'action': 'mark_watched', 'entry': encoded})),
		(control.lang(33002),
		 'Container.Refresh'),
	]
	action = 'autoplay' if autoplay else 'sources'
	control.add_directory_item(
		label,
		{'action': action, 'entry': encoded},
		is_folder=not autoplay,
		is_playable=autoplay,
		art={'icon': control.addon_icon},
		info=info,
		context=context)


def sources_menu(entry):
	if not _preflight():
		control.end_directory(content='')
		return

	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33011))

	def cb(percent, count):
		pd.update(percent, control.langf(33025, count))

	try:
		sources = scrapers.scrape(entry, progress_cb=cb)
	finally:
		pd.close()

	if sources is scrapers.MODULE_MISSING:
		control.ok_dialog(33008)
		control.end_directory(content='')
		return
	if sources == scrapers.NO_PROVIDERS:
		if control.yesno_dialog(33042):
			scrapers.open_settings()
		control.end_directory(content='')
		return
	if not sources:
		control.notify(33012)
		control.end_directory(content='')
		return

	sources, cache_known = scrapers.annotate_cached(sources)
	if not sources:
		control.notify(33045)
		control.end_directory(content='')
		return

	for source in sources:
		_add_source_item(source, entry, cache_known)

	control.end_directory(content='files')


def _add_source_item(source, entry, cache_known=False):
	quality = source.get('quality', 'SD')
	provider = source.get('provider', '')
	seeders = source.get('seeders', 0)
	try:
		size = float(source.get('size', 0) or 0)
	except (ValueError, TypeError):
		size = 0
	size_gb = '%.2f GB' % size if size else '?'
	info_line = source.get('info', '')
	prefix = ''
	if cache_known:
		prefix = ('[COLOR lime]+[/COLOR] ' if source.get('rd_cached')
				  else '[COLOR grey]-[/COLOR] ')
	label = '%s[B]%s[/B] | %s | S:%s | %s | [I]%s[/I]' % (
		prefix, quality, size_gb, seeders, provider,
		source.get('name', '')[:80])
	if info_line:
		label += ' | %s' % info_line

	payload = _encode({'source': source, 'entry': entry})
	control.add_directory_item(
		label,
		{'action': 'play', 'data': payload},
		is_folder=False,
		is_playable=True,
		art={'icon': control.addon_icon},
		info={'mediatype': 'episode',
			  'title': entry.get('ep_title', ''),
			  'tvshowtitle': entry.get('show_title', '')})


def autoplay(entry):
	if not _preflight():
		control.resolve_failed()
		return
	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33011))
	try:
		sources = scrapers.scrape(entry)
	finally:
		pd.close()
	if sources == scrapers.NO_PROVIDERS:
		sources = []
	if not sources:
		control.notify(33012)
		control.resolve_failed()
		return
	player.play(sources[0], entry)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _preflight():
	"""Ensure prerequisites are met before scraping/playing."""
	if not scrapers.available():
		control.ok_dialog(33008)
		return False
	if not realdebrid.authorized():
		control.notify(33007)
		return False
	if not trakt.authorized():
		control.notify(33006)
		return False
	return True


def mark_watched(entry):
	if trakt.add_to_history(entry):
		control.notify(33023)
		xbmc.executebuiltin('Container.Refresh')
	else:
		control.notify(33018)


def _auth(fn):
	fn()
	xbmc.executebuiltin('Container.Refresh')


def _revoke(fn):
	fn()
	control.notify(33019)
	xbmc.executebuiltin('Container.Refresh')


def clear_cache():
	if cache.clear():
		control.notify(33020)


def open_coco_settings():
	if not scrapers.open_settings():
		control.ok_dialog(33008)
