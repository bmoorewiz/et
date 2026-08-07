# -*- coding: utf-8 -*-
"""Persistent playback watcher.

This has to live in a service, not in the plugin. Kodi tears the plugin's
Python process down once its request returns - shortly after
setResolvedUrl() - and force-kills any thread still running in it. A
monitor started there never survives long enough to see playback finish, so
the stop scrobble and the mark-watched never happen.

The plugin therefore hands the entry over through a window property just
before resolving, and this service - which lives for as long as Kodi does -
picks it up and follows the playback through to the end.
"""

import json
import threading

import xbmc
import xbmcgui

from resources.lib import control
from resources.lib import player as player_lib
from resources.lib import trakt

# Where the plugin leaves the entry it is about to play.
PLAYBACK_PROPERTY = 'plugin.video.episodetracker.playback'
RESUME_PROPERTY = 'plugin.video.episodetracker.resume'
_HOME = xbmcgui.Window(10000)


def announce(entry):
	"""Called by the plugin: publish the item it is handing to Kodi."""
	try:
		_HOME.setProperty(PLAYBACK_PROPERTY, json.dumps(entry))
	except Exception:
		control.error('could not announce playback')


def announce_resume(percent, seconds):
	"""Called by the plugin: where this playback should start from.

	The seek happens here rather than through the ListItem's resume point
	because Trakt stores a percentage, and the file's real duration is only
	known once Kodi has it open. ``seconds`` is the plugin's estimate from
	the Trakt runtime, kept as a fallback for when Kodi reports no duration
	(live-ish streams, some containers).
	"""
	try:
		_HOME.setProperty(RESUME_PROPERTY,
						  json.dumps({'percent': float(percent),
									  'seconds': int(seconds)}))
	except Exception:
		control.error('could not announce resume point')


def _take_resume():
	try:
		raw = _HOME.getProperty(RESUME_PROPERTY)
		if not raw:
			return None
		_HOME.clearProperty(RESUME_PROPERTY)
		return json.loads(raw)
	except Exception:
		return None


def _take_announced():
	try:
		raw = _HOME.getProperty(PLAYBACK_PROPERTY)
		if not raw:
			return None
		_HOME.clearProperty(PLAYBACK_PROPERTY)
		return json.loads(raw)
	except Exception:
		control.error('could not read announced playback')
		return None


class ScrobblePlayer(xbmc.Player):
	"""Tracks one playback at a time and reports it to Trakt."""

	def __init__(self):
		super(ScrobblePlayer, self).__init__()
		self._lock = threading.Lock()
		self._entry = None
		self._percent = 0.0
		self._started = False
		self._resume = None
		self._pending_next = None

	# -- Kodi callbacks ---------------------------------------------------

	def onAVStarted(self):
		self._begin()

	def onPlayBackStarted(self):
		# Older Kodi builds do not emit onAVStarted.
		self._begin()

	def onPlayBackStopped(self):
		self._end(natural=False)

	def onPlayBackEnded(self):
		self._end(natural=True)

	def onPlayBackError(self):
		self._end(natural=False)

	def onPlayBackPaused(self):
		# Trakt keeps a resume point from pause events, which is what makes
		# "carry on where you left off" work on another device.
		with self._lock:
			entry, percent = self._entry, self._percent
		if entry and percent > 0:
			trakt.scrobble(entry, 'pause', percent)

	def onPlayBackResumed(self):
		with self._lock:
			entry, percent = self._entry, self._percent
		if entry and percent > 0:
			trakt.scrobble(entry, 'start', percent)

	# -- internals --------------------------------------------------------

	def _begin(self):
		entry = _take_announced()
		resume = _take_resume()
		if entry is None:
			return  # something else is playing; not ours
		with self._lock:
			self._entry = entry
			self._percent = 0.0
			self._started = False
			self._resume = resume
		control.log('tracking playback: %s' % player_lib.display_label(entry))

	def _seek(self, resume, total):
		"""Jump to the agreed resume point, now that the duration is known.

		Trakt's resume point is a percentage, so the real duration Kodi
		reports gives a far better second count than the runtime the plugin
		had to estimate from - that only serves as a fallback.
		"""
		try:
			percent = float(resume.get('percent') or 0)
		except (TypeError, ValueError):
			percent = 0.0
		seconds = int(total * percent / 100.0) if percent > 0 \
			else int(resume.get('seconds') or 0)
		# Never land in the closing seconds; that would instantly "finish" it.
		if seconds <= 0 or seconds >= total - 30:
			return
		try:
			self.seekTime(seconds)
			control.log('resumed at %ds of %ds (%.1f%%)' % (seconds, total, percent))
		except Exception:
			control.error('resume seek failed')

	def sample(self):
		"""Called from the service loop while something is playing."""
		with self._lock:
			entry, resume = self._entry, self._resume
		if not entry:
			return
		try:
			total = self.getTotalTime()
			current = self.getTime()
		except Exception:
			return
		if not total:
			return
		if resume is not None:
			with self._lock:
				self._resume = None
			self._seek(resume, total)
			return  # let the next tick read the position we landed on
		percent = max(0.0, min(100.0, current / total * 100.0))
		with self._lock:
			self._percent = percent
			first = not self._started
			if first and percent > 0:
				self._started = True
		if first and percent > 0:
			trakt.scrobble(entry, 'start', percent)

	def _end(self, natural):
		with self._lock:
			entry, percent, started = self._entry, self._percent, self._started
			self._entry, self._percent, self._started = None, 0.0, False
			self._resume = None
		if not entry:
			return
		# Playing to the very end is 100%, whatever the last sample caught.
		if natural:
			percent = 100.0
		finished = False
		try:
			if started or percent > 0:
				trakt.scrobble(entry, 'stop', percent)
			threshold = player_lib.watched_threshold(entry)
			if percent >= threshold and control.get_bool('scrobble.markwatched', True):
				control.log('marking watched at %.1f%% (threshold %d%%): %s'
							% (percent, threshold, player_lib.display_label(entry)))
				if trakt.add_to_history(entry):
					finished = True
					control.notify(33023)
			else:
				control.log('not marking watched, reached %.1f%% of %d%%: %s'
							% (percent, threshold, player_lib.display_label(entry)))
		except Exception:
			control.error('scrobble on playback end failed')

		# The prompt itself is deliberately not raised from here: this runs on
		# a Kodi player callback, which must return promptly and must not block
		# on a modal dialog. The service loop picks it up on its next tick.
		if finished and not player_lib.is_movie(entry) \
				and control.get_bool('playback.playnext', True):
			with self._lock:
				self._pending_next = entry

	def take_pending_next(self):
		with self._lock:
			entry, self._pending_next = self._pending_next, None
		return entry


def _same_episode(one, two):
	"""Are these two entries the same episode?

	Guards against Trakt not having registered the just-finished episode yet,
	which would otherwise offer it up again as the "next" one.
	"""
	for key in ('ep_trakt', 'ep_imdb', 'ep_tvdb', 'ep_tmdb'):
		if one.get(key) and two.get(key):
			return one[key] == two[key]
	return (one.get('show_trakt') == two.get('show_trakt')
			and one.get('season') == two.get('season')
			and one.get('episode') == two.get('episode'))


def offer_next(scrobble_player, entry):
	"""Ask whether to carry on with the next episode of the same show."""
	if scrobble_player.isPlayingVideo():
		return  # the user has already started something else
	try:
		following = trakt.next_episode_after(entry)
	except Exception:
		control.error('next episode lookup failed')
		return
	if not following or _same_episode(entry, following):
		return
	label = player_lib.display_label(following)
	if not control.yesno_dialog(control.langf(33074, label),
								heading=control.lang(33075)):
		return
	url = control.build_url({'action': 'autoplay',
							 'entry': control.encode_obj(following)})
	control.log('playing next: %s' % label)
	xbmc.executebuiltin('PlayMedia(%s)' % url)


def run():
	"""Service entry point: live until Kodi shuts down."""
	control.log('Episode Tracker service started (v%s)' % control.addon_version)
	monitor = xbmc.Monitor()
	scrobble_player = ScrobblePlayer()
	try:
		while not monitor.abortRequested():
			try:
				if scrobble_player.isPlayingVideo():
					scrobble_player.sample()
				else:
					pending = scrobble_player.take_pending_next()
					if pending is not None:
						offer_next(scrobble_player, pending)
			except Exception:
				control.error('playback sampling failed')
			# Frequent enough that the last reading is close to where
			# playback actually stopped, cheap enough to run all day.
			if monitor.waitForAbort(2):
				break
	finally:
		control.log('Episode Tracker service stopped')
