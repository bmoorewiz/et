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

# How many times to retry a failed mark-watched before letting it go. The
# sample loop runs every two seconds, so without a cap a Trakt outage would
# mean a request every two seconds until the episode ends.
_MARK_ATTEMPTS = 3

# Kodi does not know a stream's real duration the instant playback starts.
# For an HTTP stream it can report a handful of seconds and only settle once
# the demuxer has worked the file out - and 25 seconds of a "30 second"
# episode reads as 83%, which is how a 45-minute episode was being marked
# watched half a minute in. Progress is only believed once the duration is
# in the same country as the runtime Trakt gives for the item.
_RUNTIME_RATIO = 0.5
# Fallback when Trakt has no runtime for the item: nothing this add-on plays
# is under five minutes, so a shorter duration has not settled yet.
_MIN_BELIEVABLE_SECONDS = 300.0
# Consecutive samples that must agree before marking watched.
_CONFIRMATIONS = 2


def believable_duration(total, entry):
	"""Could this be the real length of what is playing?

	Checked against the runtime Trakt reports, which is the only
	independent idea of how long the item should be. Where Trakt has no
	runtime, an absolute floor stands in.
	"""
	try:
		total = float(total or 0)
	except (TypeError, ValueError):
		return False
	if total <= 0:
		return False
	runtime = player_lib.runtime_seconds(entry)
	if runtime:
		return total >= runtime * _RUNTIME_RATIO
	return total >= _MIN_BELIEVABLE_SECONDS


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
		self._marked = False
		self._mark_attempts = 0
		self._duration = 0.0
		self._confirmations = 0
		self._warned_duration = False

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
			self._marked = False
			self._mark_attempts = 0
			self._duration = 0.0
			self._confirmations = 0
			self._warned_duration = False
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

		# Everything below is a percentage of `total`, so a duration that
		# cannot be right poisons all of it - position, the resume seek, the
		# scrobbles, and whether this counts as watched.
		if not believable_duration(total, entry):
			with self._lock:
				self._confirmations = 0
				announced = self._warned_duration
				self._warned_duration = True
			if not announced:
				control.log('ignoring a duration of %.0fs for %s (runtime says '
							'%ds); waiting for Kodi to settle'
							% (total or 0, player_lib.display_label(entry),
							   player_lib.runtime_seconds(entry)))
			return

		if resume is not None:
			with self._lock:
				self._resume = None
			self._seek(resume, total)
			return  # let the next tick read the position we landed on

		percent = max(0.0, min(100.0, current / total * 100.0))
		threshold = player_lib.watched_threshold(entry)
		with self._lock:
			self._percent = percent
			self._duration = total
			first = not self._started
			if first and percent > 0:
				self._started = True
			# Crossing the line has to hold for more than one reading. A
			# single odd sample should not mark a whole episode watched.
			if percent >= threshold:
				self._confirmations += 1
			else:
				self._confirmations = 0
			confirmed = self._confirmations >= _CONFIRMATIONS
		if first and percent > 0:
			trakt.scrobble(entry, 'start', percent)
		if confirmed:
			self._mark_watched(entry, percent)

	def _mark_watched(self, entry, percent):
		"""Add to the Trakt history as soon as the threshold is passed.

		Deliberately not left until playback stops. An episode you are still
		watching - sitting through the credits, or leaving it running - had
		already been watched by any reasonable definition, but stayed in Next
		Episodes until you pressed stop, because that was the only moment
		this ran.

		Returns True if this call is what marked it. A failed attempt is
		retried by the next sample rather than lost, but only a few times, so
		a Trakt outage cannot turn into a request every two seconds for the
		rest of the episode.
		"""
		if not control.get_bool('scrobble.markwatched', True):
			return False
		with self._lock:
			if self._marked or self._mark_attempts >= _MARK_ATTEMPTS:
				return False
			self._mark_attempts += 1
		control.log('marking watched at %.1f%% (threshold %d%%): %s'
					% (percent, player_lib.watched_threshold(entry),
					   player_lib.display_label(entry)))
		if not trakt.add_to_history(entry):
			control.log('mark watched failed, will retry (attempt %d of %d)'
						% (self._mark_attempts, _MARK_ATTEMPTS))
			return False
		with self._lock:
			self._marked = True
		control.notify(33023)
		return True

	def _end(self, natural):
		with self._lock:
			entry, percent, started = self._entry, self._percent, self._started
			finished = self._marked
			measured = self._duration > 0
			self._entry, self._percent, self._started = None, 0.0, False
			self._resume = None
		if not entry:
			return
		try:
			# Nothing here is worth saying if no believable duration ever
			# arrived. "Played to the end" is not evidence on its own - a
			# source that turned out to be thirty seconds long reaches its
			# end too, and reporting that as progress would have Trakt mark
			# the episode watched by itself at 80%.
			if not measured:
				control.log('not marking watched: never saw a believable '
							'duration for %s' % player_lib.display_label(entry))
				return
			# Playing to the very end is 100%, whatever the last sample caught.
			if natural:
				percent = 100.0
			if started or percent > 0:
				trakt.scrobble(entry, 'stop', percent)
			threshold = player_lib.watched_threshold(entry)
			if finished:
				pass  # already done mid-playback, when the threshold passed
			elif percent >= threshold:
				# Stopped between two samples, or skipped straight to the end.
				finished = self._mark_watched(entry, percent)
			else:
				control.log('not marking watched, reached %.1f%% of %d%%: %s'
							% (percent, threshold, player_lib.display_label(entry)))
		except Exception:
			control.error('scrobble on playback end failed')
		finally:
			with self._lock:
				self._marked, self._mark_attempts = False, 0
				self._duration, self._confirmations = 0.0, 0
				self._warned_duration = False

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
