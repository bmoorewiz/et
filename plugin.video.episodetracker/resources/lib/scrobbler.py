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
_HOME = xbmcgui.Window(10000)


def announce(entry):
	"""Called by the plugin: publish the item it is handing to Kodi."""
	try:
		_HOME.setProperty(PLAYBACK_PROPERTY, json.dumps(entry))
	except Exception:
		control.error('could not announce playback')


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

	# -- internals --------------------------------------------------------

	def _begin(self):
		entry = _take_announced()
		if entry is None:
			return  # something else is playing; not ours
		with self._lock:
			self._entry = entry
			self._percent = 0.0
			self._started = False
		control.log('tracking playback: %s' % player_lib.display_label(entry))

	def sample(self):
		"""Called from the service loop while something is playing."""
		with self._lock:
			entry = self._entry
		if not entry:
			return
		try:
			total = self.getTotalTime()
			current = self.getTime()
		except Exception:
			return
		if not total:
			return
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
		if not entry:
			return
		# Playing to the very end is 100%, whatever the last sample caught.
		if natural:
			percent = 100.0
		try:
			if started or percent > 0:
				trakt.scrobble(entry, 'stop', percent)
			threshold = player_lib.watched_threshold(entry)
			if percent >= threshold and control.get_bool('scrobble.markwatched', True):
				control.log('marking watched at %.1f%% (threshold %d%%): %s'
							% (percent, threshold, player_lib.display_label(entry)))
				if trakt.add_to_history(entry):
					control.notify(33023)
			else:
				control.log('not marking watched, reached %.1f%% of %d%%: %s'
							% (percent, threshold, player_lib.display_label(entry)))
		except Exception:
			control.error('scrobble on playback end failed')


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
			except Exception:
				control.error('playback sampling failed')
			# Frequent enough that the last reading is close to where
			# playback actually stopped, cheap enough to run all day.
			if monitor.waitForAbort(2):
				break
	finally:
		control.log('Episode Tracker service stopped')
