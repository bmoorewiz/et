# -*- coding: utf-8 -*-
"""Playback: resolve a chosen source through Real-Debrid, play it, and
scrobble progress back to Trakt."""

import threading

import xbmc
import xbmcgui

from resources.lib import control
from resources.lib import realdebrid
from resources.lib import trakt


def _magnet_for(source):
	url = source.get('url', '')
	if url.startswith('magnet:'):
		return url
	info_hash = source.get('hash')
	if info_hash:
		name = source.get('name', '')
		return 'magnet:?xt=urn:btih:%s&dn=%s' % (info_hash, name)
	return url


def play(source, entry):
	"""Resolve and play a single source, then hand off to the scrobbler."""
	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33013))
	try:
		magnet = _magnet_for(source)
		resolved = realdebrid.resolve_magnet(
			magnet,
			source.get('hash', ''),
			entry.get('season'),
			entry.get('episode'),
			entry.get('show_title', ''))
	finally:
		pd.close()

	if not resolved:
		control.notify(33014)
		control.resolve_failed()
		return

	item = xbmcgui.ListItem(path=resolved)
	label = '%s - %sx%02d - %s' % (
		entry.get('show_title', ''), entry.get('season', 0),
		int(entry.get('episode', 0) or 0), entry.get('ep_title', ''))
	item.setLabel(label)
	info = {
		'mediatype': 'episode',
		'tvshowtitle': entry.get('show_title', ''),
		'title': entry.get('ep_title', ''),
		'season': entry.get('season'),
		'episode': entry.get('episode'),
		'plot': entry.get('plot', ''),
		'aired': (entry.get('first_aired') or '')[:10],
	}
	try:
		tag = item.getVideoInfoTag()
		control._apply_info_tag(tag, info)
	except Exception:
		item.setInfo('video', info)
	item.setArt({'icon': control.addon_icon, 'thumb': control.addon_icon,
				 'fanart': control.addon_fanart})

	control.resolve(item)

	# Scrobble in the background so we don't block the resolved-url handoff.
	if control.get_bool('scrobble.enabled', True) and trakt.authorized():
		threading.Thread(target=_scrobble_monitor, args=(entry,)).start()


def _scrobble_monitor(entry):
	"""Watch playback and send Trakt scrobble start/stop events."""
	player = xbmc.Player()
	monitor = xbmc.Monitor()

	# Wait for playback to actually begin (up to 30s).
	waited = 0
	while not player.isPlaying() and waited < 30:
		if monitor.waitForAbort(1):
			return
		waited += 1
	if not player.isPlaying():
		return

	started = False
	last_percent = 0.0
	try:
		while player.isPlaying():
			try:
				total = player.getTotalTime()
				current = player.getTime()
				percent = (current / total * 100) if total else 0.0
				last_percent = percent
				if not started and percent > 0:
					trakt.scrobble(entry, 'start', percent)
					started = True
			except Exception:
				pass
			if monitor.waitForAbort(10):
				break
	except Exception:
		control.error('scrobble monitor failed')

	# Playback ended - send a stop event and optionally mark watched.
	try:
		trakt.scrobble(entry, 'stop', last_percent)
		if last_percent >= 80 and control.get_bool('scrobble.markwatched', True):
			# Trakt marks it watched automatically on a stop above ~80%, but we
			# also add to history explicitly so the next-up list advances.
			trakt.add_to_history(entry)
	except Exception:
		control.error('scrobble stop failed')
