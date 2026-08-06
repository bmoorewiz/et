# -*- coding: utf-8 -*-
"""Playback: resolve a chosen source through Real-Debrid, play it, and
scrobble progress back to Trakt."""

import threading

import xbmc
import xbmcgui

from resources.lib import control
from resources.lib import realdebrid
from resources.lib import trakt


def is_movie(entry):
	return (entry or {}).get('media_type') == 'movie'


def display_label(entry):
	"""Human label for an episode or movie entry."""
	if is_movie(entry):
		year = entry.get('year')
		return '%s (%s)' % (entry.get('title', ''), year) if year \
			else entry.get('title', '')
	try:
		season = int(entry.get('season') or 0)
		episode = int(entry.get('episode') or 0)
	except (TypeError, ValueError):
		season, episode = 0, 0
	return '%s - %dx%02d - %s' % (entry.get('show_title', ''), season,
								  episode, entry.get('ep_title', ''))


def media_info(entry):
	"""Kodi info-label dict for an episode or movie entry."""
	if is_movie(entry):
		return {
			'mediatype': 'movie',
			'title': entry.get('title', ''),
			'plot': entry.get('plot', ''),
			'premiered': (entry.get('released') or '')[:10],
			'duration': (entry.get('runtime') or 0) * 60 or None,
		}
	return {
		'mediatype': 'episode',
		'tvshowtitle': entry.get('show_title', ''),
		'title': entry.get('ep_title', ''),
		'season': entry.get('season'),
		'episode': entry.get('episode'),
		'plot': entry.get('plot', ''),
		'aired': (entry.get('first_aired') or '')[:10],
		'premiered': (entry.get('first_aired') or '')[:10],
	}


def watched_threshold(entry):
	"""Percentage of playback after which an item counts as watched.

	Trakt's own /scrobble/stop marks an item watched at 80% server-side, so
	setting this above 80 does not stop Trakt doing that - it only controls
	whether this add-on also posts it to /sync/history.
	"""
	key = ('scrobble.threshold.movie' if is_movie(entry)
		   else 'scrobble.threshold.episode')
	return min(100, max(1, control.get_int(key, 80)))


def _magnet_for(source):
	url = source.get('url', '')
	if url.startswith('magnet:'):
		return url
	info_hash = source.get('hash')
	if info_hash:
		name = source.get('name', '')
		return 'magnet:?xt=urn:btih:%s&dn=%s' % (info_hash, name)
	return url


def _resolve_one(source, entry):
	"""Resolve a single source, returning ``(url, error)``.

	Movies pass no season/episode, so Real-Debrid falls through to picking
	the largest video file in the torrent rather than episode-matching.
	"""
	if is_movie(entry):
		season, episode, title = None, None, entry.get('title', '')
	else:
		season = entry.get('season')
		episode = entry.get('episode')
		title = entry.get('show_title', '')
	return realdebrid.resolve_magnet(
		_magnet_for(source), source.get('hash', ''), season, episode, title)


def _candidates(source, entry):
	"""The chosen source, followed by the remaining ranked ones."""
	queue = [source]
	if not control.get_bool('playback.autonext', True):
		return queue
	try:
		from resources.lib import scrapers
		ranked = scrapers.cached_sources(entry) or []
	except Exception:
		return queue
	chosen = (source.get('hash') or source.get('url') or '').lower()
	seen = {chosen}
	started = False
	for item in ranked:
		key = (item.get('hash') or item.get('url') or '').lower()
		if key == chosen:
			started = True
			continue
		if not started or key in seen:
			continue
		seen.add(key)
		queue.append(item)
	return queue


def play(source, entry):
	"""Resolve and play, falling through to the next ranked source on failure."""
	attempts = max(1, control.get_int('playback.max_attempts', 25))
	queue = _candidates(source, entry)[:attempts]

	pd = control.progress_bg
	pd.create(control.addon_name, control.lang(33013))
	resolved, error = None, None
	try:
		for position, candidate in enumerate(queue, 1):
			pd.update(int((position - 1) * 100 / len(queue)),
					  control.langf(33043, position, len(queue),
									candidate.get('quality', '')))
			resolved, error = _resolve_one(candidate, entry)
			if resolved:
				source = candidate
				break
			control.log('source %d/%d failed: %s' % (position, len(queue), error))
			if control.aborted():
				break
	finally:
		pd.close()

	if not resolved:
		# Show why, rather than a generic failure. The underlying reason is the
		# whole value here, so never let a missing translation swallow it.
		message = error or control.lang(33014) or 'Could not resolve a playable link.'
		if len(queue) > 1:
			wrapped = control.langf(33044, len(queue), message)
			message = (wrapped if wrapped.strip() and message in wrapped
					   else '%s (tried %d sources)' % (message, len(queue)))
		control.ok_dialog(message,
						  heading=control.lang(33014) or control.addon_name)
		control.resolve_failed()
		return

	item = xbmcgui.ListItem(path=resolved)
	item.setLabel(display_label(entry))
	info = media_info(entry)
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
		threading.Thread(target=_scrobble_monitor,
						 args=(entry, resolved)).start()


def _playing_file(player):
	try:
		return player.getPlayingFile() if player.isPlaying() else None
	except Exception:
		return None


def _scrobble_monitor(entry, resolved_url=None):
	"""Watch playback and send Trakt scrobble start/stop events.

	The monitor binds to the file that is actually playing. Kodi keeps
	isPlaying() true across consecutive items, so without this a monitor
	would keep attributing a *different* video's progress to this entry.
	"""
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

	# Remember which file this monitor owns; stop when it changes or ends.
	my_file = _playing_file(player)

	started = False
	last_percent = 0.0
	try:
		while player.isPlaying():
			current_file = _playing_file(player)
			if my_file and current_file and current_file != my_file:
				break  # a different video started - this one has ended
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
			# Sample often enough that the final reading is close to where
			# playback actually stopped - that value decides "watched".
			if monitor.waitForAbort(5):
				break
	except Exception:
		control.error('scrobble monitor failed')

	# Playback ended - send a stop event and optionally mark watched.
	try:
		trakt.scrobble(entry, 'stop', last_percent)
		threshold = watched_threshold(entry)
		if last_percent >= threshold and control.get_bool('scrobble.markwatched', True):
			# Trakt already marks it watched on a stop above 80%, but we also
			# add to history explicitly so the next-up list advances at once.
			control.log('marking watched at %.1f%% (threshold %d%%): %s'
						% (last_percent, threshold, display_label(entry)))
			trakt.add_to_history(entry)
		else:
			control.log('not marking watched, reached %.1f%% of %d%%: %s'
						% (last_percent, threshold, display_label(entry)))
	except Exception:
		control.error('scrobble stop failed')
