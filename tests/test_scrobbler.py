# -*- coding: utf-8 -*-
"""The playback service: scrobbling, marking watched, resume and play-next.

Regression home for "it doesn't mark watched at 80%". Kodi destroys the
plugin process shortly after setResolvedUrl() and force-kills any thread
still running in it, so a watcher started there never lived long enough to
see playback end. The handover through a window property, and the service
that outlives the plugin, are what these tests protect.
"""

import json
from unittest import mock

import xbmc
import xbmcgui

from support import AddonTestCase

from resources.lib import control
from resources.lib import scrobbler

EPISODE = {
	'media_type': 'episode', 'show_title': 'Severance', 'show_trakt': 111,
	'season': 2, 'episode': 3, 'ep_title': 'Who Is Alive?', 'ep_trakt': 999,
	'runtime': 45,
}
NEXT = dict(EPISODE, episode=4, ep_title='Woe\'s Hollow', ep_trakt=1000)
MOVIE = {'media_type': 'movie', 'title': 'Dune', 'movie_trakt': 7, 'runtime': 155}


class FakePlayer(scrobbler.ScrobblePlayer):
	"""A ScrobblePlayer wired to a scripted Kodi player."""

	def __init__(self, total=2700.0, position=0.0, playing=True):
		super(FakePlayer, self).__init__()
		self.total = total
		self.position = position
		self.playing = playing
		self.seeks = []

	def isPlayingVideo(self):
		return self.playing

	def getTotalTime(self):
		return self.total

	def getTime(self):
		return self.position

	def seekTime(self, seconds):
		self.seeks.append(seconds)
		self.position = seconds


class ServiceBase(AddonTestCase):
	def setUp(self):
		super(ServiceBase, self).setUp()
		self.set(**{'trakt.token': 'trakt-token', 'scrobble.enabled': True,
					'scrobble.markwatched': True})
		self.scrobbles = []
		self.history = []
		patcher = mock.patch('resources.lib.trakt.scrobble',
							 side_effect=lambda e, a, p: self.scrobbles.append(
								 (a, round(p, 1))))
		patcher.start()
		self.addCleanup(patcher.stop)
		patcher = mock.patch('resources.lib.trakt.add_to_history',
							 side_effect=lambda e: self.history.append(e) or True)
		patcher.start()
		self.addCleanup(patcher.stop)

	def start(self, entry, **kwargs):
		player = FakePlayer(**kwargs)
		scrobbler.announce(entry)
		player.onAVStarted()
		return player


class Handover(ServiceBase):
	def test_the_plugin_hands_the_entry_to_the_service(self):
		# The two Window objects are separate instances, exactly as they are
		# separate processes in Kodi; the property is what connects them.
		scrobbler.announce(EPISODE)
		self.assertTrue(xbmcgui.Window(10000).getProperty(
			scrobbler.PLAYBACK_PROPERTY))
		self.assertEqual(scrobbler._take_announced(), EPISODE)

	def test_the_property_is_consumed_exactly_once(self):
		scrobbler.announce(EPISODE)
		self.assertIsNotNone(scrobbler._take_announced())
		self.assertIsNone(scrobbler._take_announced())

	def test_foreign_playback_is_ignored(self):
		player = FakePlayer()
		player.onAVStarted()
		player.position = 2700.0
		player.onPlayBackEnded()
		self.assertEqual(self.scrobbles, [])
		self.assertEqual(self.history, [])

	def test_corrupt_handover_data_does_not_take_the_service_down(self):
		xbmcgui.Window(10000).setProperty(scrobbler.PLAYBACK_PROPERTY, 'not json')
		self.assertIsNone(scrobbler._take_announced())


class Scrobbling(ServiceBase):
	def test_a_start_is_sent_on_the_first_sample(self):
		player = self.start(EPISODE, position=27.0)
		player.sample()
		self.assertEqual(self.scrobbles, [('start', 1.0)])

	def test_start_is_only_sent_once(self):
		player = self.start(EPISODE, position=27.0)
		player.sample()
		player.position = 270.0
		player.sample()
		self.assertEqual([a for a, _p in self.scrobbles], ['start'])

	def test_pausing_tells_trakt(self):
		# Pause scrobbles are what create Trakt's resume point in the first
		# place; without them "carry on where you left off" has no data.
		player = self.start(EPISODE, position=1350.0)
		player.sample()
		player.onPlayBackPaused()
		self.assertIn(('pause', 50.0), self.scrobbles)

	def test_unpausing_tells_trakt_too(self):
		player = self.start(EPISODE, position=1350.0)
		player.sample()
		player.onPlayBackPaused()
		player.onPlayBackResumed()
		self.assertEqual([a for a, _p in self.scrobbles],
						 ['start', 'pause', 'start'])

	def test_pause_before_any_progress_is_not_sent(self):
		player = self.start(EPISODE)
		player.onPlayBackPaused()
		self.assertEqual(self.scrobbles, [])

	def test_a_stop_is_sent_when_playback_ends(self):
		player = self.start(EPISODE, position=1350.0)
		player.sample()
		player.onPlayBackStopped()
		self.assertEqual(self.scrobbles[-1], ('stop', 50.0))

	def test_no_duration_yet_is_simply_skipped(self):
		player = self.start(EPISODE, total=0.0)
		player.sample()
		self.assertEqual(self.scrobbles, [])

	def test_a_kodi_error_during_sampling_is_survived(self):
		player = self.start(EPISODE)
		with mock.patch.object(FakePlayer, 'getTotalTime',
							   side_effect=RuntimeError('not playing')):
			player.sample()
		self.assertEqual(self.scrobbles, [])


class MarkWatched(ServiceBase):
	def test_playing_to_the_end_marks_watched(self):
		player = self.start(EPISODE, position=2700.0)
		player.sample()
		player.onPlayBackEnded()
		self.assertEqual(len(self.history), 1)

	def test_stopping_past_the_threshold_marks_watched(self):
		player = self.start(EPISODE, position=2200.0)  # 81%
		player.sample()
		player.onPlayBackStopped()
		self.assertEqual(len(self.history), 1)

	def test_stopping_short_of_the_threshold_does_not(self):
		player = self.start(EPISODE, position=1350.0)  # 50%
		player.sample()
		player.onPlayBackStopped()
		self.assertEqual(self.history, [])

	def test_the_threshold_is_configurable(self):
		self.set(**{'scrobble.threshold.episode': '40'})
		player = self.start(EPISODE, position=1350.0)  # 50%
		player.sample()
		player.onPlayBackStopped()
		self.assertEqual(len(self.history), 1)

	def test_marking_watched_can_be_turned_off(self):
		self.set(**{'scrobble.markwatched': False})
		player = self.start(EPISODE, position=2700.0)
		player.sample()
		player.onPlayBackEnded()
		self.assertEqual(self.history, [])
		self.assertEqual(self.scrobbles[-1][0], 'stop', 'stop is still sent')

	def test_a_playback_error_does_not_mark_watched(self):
		player = self.start(EPISODE, position=27.0)
		player.sample()
		player.onPlayBackError()
		self.assertEqual(self.history, [])

	def test_a_trakt_failure_at_the_end_is_survived(self):
		player = self.start(EPISODE, position=2700.0)
		player.sample()
		with mock.patch('resources.lib.trakt.add_to_history',
						side_effect=RuntimeError('trakt down')):
			player.onPlayBackEnded()  # must not raise

	def test_the_reason_is_always_logged(self):
		player = self.start(EPISODE, position=1350.0)
		player.sample()
		player.onPlayBackStopped()
		self.assertIn('not marking watched, reached 50.0% of 80%', self.logged())


class ResumeApplication(ServiceBase):
	def test_the_service_seeks_once_the_duration_is_known(self):
		scrobbler.announce_resume(42.0, 1134)
		player = self.start(EPISODE, total=2700.0)
		player.sample()
		self.assertEqual(player.seeks, [1134])

	def test_kodis_duration_beats_the_plugins_estimate(self):
		# Trakt stores a percentage; the plugin can only guess seconds from
		# the Trakt runtime, which is often wrong for the actual file.
		scrobbler.announce_resume(50.0, 1350)
		player = self.start(EPISODE, total=3000.0)
		player.sample()
		self.assertEqual(player.seeks, [1500])

	def test_the_estimate_is_used_when_no_percentage_survives(self):
		xbmcgui.Window(10000).setProperty(scrobbler.RESUME_PROPERTY,
										  json.dumps({'seconds': 600}))
		player = self.start(EPISODE, total=2700.0)
		player.sample()
		self.assertEqual(player.seeks, [600])

	def test_it_only_seeks_once(self):
		scrobbler.announce_resume(42.0, 1134)
		player = self.start(EPISODE, total=2700.0)
		player.sample()
		player.sample()
		self.assertEqual(len(player.seeks), 1)

	def test_a_resume_point_in_the_closing_seconds_is_refused(self):
		# Landing there would instantly "finish" the episode.
		scrobbler.announce_resume(99.0, 99)
		player = self.start(EPISODE, total=100.0)
		player.sample()
		self.assertEqual(player.seeks, [])

	def test_a_stale_resume_point_cannot_leak_into_other_playback(self):
		scrobbler.announce_resume(42.0, 1134)
		player = FakePlayer()
		player.onAVStarted()  # nothing announced - not ours
		player.sample()
		self.assertEqual(player.seeks, [])
		self.assertEqual(
			xbmcgui.Window(10000).getProperty(scrobbler.RESUME_PROPERTY), '')

	def test_the_first_scrobble_reports_the_resumed_position(self):
		scrobbler.announce_resume(42.0, 1134)
		player = self.start(EPISODE, total=2700.0)
		player.sample()   # seeks
		player.sample()   # first real reading
		self.assertEqual(self.scrobbles, [('start', 42.0)])


class PlayNext(ServiceBase):
	def _next(self, value):
		return mock.patch('resources.lib.trakt.next_episode_after',
						  return_value=value)

	def _finish(self, entry, natural=True, position=None):
		player = self.start(entry, position=position if position is not None
							else 2700.0)
		player.sample()
		player.onPlayBackEnded() if natural else player.onPlayBackStopped()
		return player

	def test_finishing_an_episode_queues_the_offer(self):
		player = self._finish(EPISODE)
		self.assertEqual(player.take_pending_next(), EPISODE)

	def test_the_queue_drains(self):
		player = self._finish(EPISODE)
		player.take_pending_next()
		self.assertIsNone(player.take_pending_next())

	def test_an_unfinished_episode_queues_nothing(self):
		player = self._finish(EPISODE, natural=False, position=270.0)
		self.assertIsNone(player.take_pending_next())

	def test_a_movie_queues_nothing(self):
		player = self._finish(MOVIE)
		self.assertIsNone(player.take_pending_next())

	def test_the_setting_turns_it_off(self):
		self.set(**{'playback.playnext': False})
		player = self._finish(EPISODE)
		self.assertIsNone(player.take_pending_next())

	def test_the_offer_launches_the_next_episode(self):
		player = FakePlayer(playing=False)
		self.answer_yes(True)
		with self._next(NEXT):
			scrobbler.offer_next(player, EPISODE)
		self.assertIn('2x04', self.last_dialog('yesno')[2])
		launched = [b for b in self.builtins() if b.startswith('PlayMedia(')]
		self.assertEqual(len(launched), 1)
		query = control.parse_params(launched[0].split('?', 1)[1].rstrip(')'))
		self.assertEqual(query['action'], 'autoplay')
		self.assertEqual(control.decode_obj(query['entry']), NEXT)

	def test_declining_launches_nothing(self):
		player = FakePlayer(playing=False)
		self.answer_yes(False)
		with self._next(NEXT):
			scrobbler.offer_next(player, EPISODE)
		self.assertEqual([b for b in self.builtins() if 'PlayMedia' in b], [])

	def test_it_never_re_offers_the_episode_just_watched(self):
		# Trakt does not always have the history add registered yet.
		player = FakePlayer(playing=False)
		with self._next(dict(EPISODE)):
			scrobbler.offer_next(player, EPISODE)
		self.assertEqual(self.dialogs('yesno'), [])

	def test_a_caught_up_show_offers_nothing(self):
		player = FakePlayer(playing=False)
		with self._next(None):
			scrobbler.offer_next(player, EPISODE)
		self.assertEqual(self.dialogs('yesno'), [])

	def test_it_does_not_interrupt_something_already_playing(self):
		player = FakePlayer(playing=True)
		with self._next(NEXT):
			scrobbler.offer_next(player, EPISODE)
		self.assertEqual(self.dialogs('yesno'), [])

	def test_a_trakt_failure_is_survived(self):
		player = FakePlayer(playing=False)
		with mock.patch('resources.lib.trakt.next_episode_after',
						side_effect=RuntimeError('trakt down')):
			scrobbler.offer_next(player, EPISODE)  # must not raise

	def test_same_episode_falls_back_to_show_and_numbers(self):
		without_ids = {k: v for k, v in EPISODE.items() if k != 'ep_trakt'}
		self.assertTrue(scrobbler._same_episode(without_ids, dict(without_ids)))
		self.assertFalse(scrobbler._same_episode(EPISODE, NEXT))


class ServiceLoop(ServiceBase):
	def test_it_stops_when_kodi_shuts_down(self):
		xbmc.ABORT[0] = True
		scrobbler.run()  # returns rather than hanging
		self.assertIn('service started', self.logged())
		self.assertIn('service stopped', self.logged())
