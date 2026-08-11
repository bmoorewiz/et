# -*- coding: utf-8 -*-
"""Playback: the fallback queue, the resume offer, and what reaches Kodi."""

from unittest import mock

import xbmcgui
import xbmcplugin

from support import AddonTestCase

from resources.lib import player
from resources.lib import scrobbler

EPISODE = {
	'media_type': 'episode', 'show_title': 'Severance', 'show_trakt': 111,
	'season': 2, 'episode': 3, 'ep_title': 'Who Is Alive?', 'ep_trakt': 999,
	'runtime': 45, 'first_aired': '2025-01-31T13:00:00.000Z',
}
MOVIE = {'media_type': 'movie', 'title': 'Dune', 'year': 2021,
		 'movie_trakt': 7, 'runtime': 155, 'released': '2021-10-22'}


def source(quality='1080p', name='release', info_hash=None):
	return {'quality': quality, 'name': name,
			'hash': info_hash or ('%s-%s' % (quality, name)),
			'seeders': 10, 'size': 5.0, 'provider': 'test'}


class Labels(AddonTestCase):
	def test_episode_label_is_padded(self):
		self.assertEqual(player.display_label(EPISODE),
						 'Severance - 2x03 - Who Is Alive?')

	def test_movie_label_carries_the_year(self):
		self.assertEqual(player.display_label(MOVIE), 'Dune (2021)')

	def test_a_broken_entry_does_not_crash_the_label(self):
		player.display_label({'media_type': 'episode', 'season': 'x'})

	def test_media_info_is_typed_for_kodi(self):
		info = player.media_info(EPISODE)
		self.assertEqual(info['mediatype'], 'episode')
		self.assertEqual(info['aired'], '2025-01-31')
		self.assertEqual(player.media_info(MOVIE)['mediatype'], 'movie')

	def test_provider_tags_are_short(self):
		self.assertEqual(player.provider_tag('Real-Debrid'), 'RD')
		self.assertEqual(player.provider_tag('TorBox'), 'TB')
		self.assertEqual(player.provider_tag(None), '?')


class WatchedThreshold(AddonTestCase):
	def test_defaults_to_eighty_percent(self):
		self.assertEqual(player.watched_threshold(EPISODE), 80)
		self.assertEqual(player.watched_threshold(MOVIE), 80)

	def test_episodes_and_movies_are_configured_separately(self):
		self.set(**{'scrobble.threshold.episode': '90',
					'scrobble.threshold.movie': '50'})
		self.assertEqual(player.watched_threshold(EPISODE), 90)
		self.assertEqual(player.watched_threshold(MOVIE), 50)

	def test_nonsense_values_are_clamped(self):
		self.set(**{'scrobble.threshold.episode': '0'})
		self.assertEqual(player.watched_threshold(EPISODE), 1)
		self.set(**{'scrobble.threshold.episode': '900'})
		self.assertEqual(player.watched_threshold(EPISODE), 100)


class FallbackQueue(AddonTestCase):
	"""The per-tier budgets: 5 x 4K, then 5 x 1080p, then 10 x 720p."""

	def _ranked(self, items):
		return mock.patch('resources.lib.scrapers.cached_sources',
						  return_value=items)

	def test_the_chosen_source_is_always_first(self):
		chosen = source('720p', 'chosen')
		with self._ranked([source('4K', 'other'), chosen]):
			queue = player._candidates(chosen, EPISODE)
		self.assertEqual(queue[0]['name'], 'chosen')

	def test_each_tier_gets_its_own_budget(self):
		ranked = ([source('4K', '4k-%d' % n) for n in range(20)] +
				  [source('1080p', 'hd-%d' % n) for n in range(20)] +
				  [source('720p', 'sd-%d' % n) for n in range(20)])
		with self._ranked(ranked):
			queue = player._candidates(source('4K', '4k-0'), EPISODE)
		tiers = [player._tier(item) for item in queue]
		self.assertEqual(tiers.count('4K'), 5)
		self.assertEqual(tiers.count('1080p'), 5)
		self.assertEqual(tiers.count('720p'), 10)
		self.assertEqual(len(queue), 20)

	def test_the_chosen_source_counts_against_its_own_tier(self):
		ranked = [source('4K', '4k-%d' % n) for n in range(20)]
		with self._ranked(ranked):
			queue = player._candidates(source('4K', '4k-0'), EPISODE)
		self.assertEqual(len(queue), 5)

	def test_tiers_are_spent_best_first(self):
		ranked = ([source('720p', 'sd-%d' % n) for n in range(20)] +
				  [source('4K', '4k-%d' % n) for n in range(20)])
		with self._ranked(ranked):
			queue = player._candidates(source('4K', '4k-0'), EPISODE)
		self.assertEqual([player._tier(i) for i in queue[:5]], ['4K'] * 5)

	def test_sd_is_off_by_default(self):
		ranked = [source('SD', 'sd-%d' % n) for n in range(20)]
		with self._ranked(ranked):
			queue = player._candidates(source('1080p', 'chosen'), EPISODE)
		self.assertEqual(len(queue), 1)

	def test_cam_and_screener_count_as_sd(self):
		self.assertEqual(player._tier(source('CAM')), 'SD')
		self.assertEqual(player._tier(source('SCR')), 'SD')
		self.assertEqual(player._tier(source('UNKNOWN')), 'SD')

	def test_the_same_source_is_never_queued_twice(self):
		chosen = source('1080p', 'chosen', info_hash='DUP')
		duplicate = source('1080p', 'other', info_hash='dup')
		with self._ranked([duplicate]):
			queue = player._candidates(chosen, EPISODE)
		self.assertEqual(len(queue), 1)

	def test_autonext_off_tries_only_what_was_picked(self):
		self.set(**{'playback.autonext': False})
		with self._ranked([source('4K', 'other')]):
			queue = player._candidates(source('1080p', 'chosen'), EPISODE)
		self.assertEqual(len(queue), 1)

	def test_a_scraper_failure_still_leaves_the_chosen_source(self):
		with mock.patch('resources.lib.scrapers.cached_sources',
						side_effect=RuntimeError('boom')):
			queue = player._candidates(source(), EPISODE)
		self.assertEqual(len(queue), 1)

	def test_budgets_are_configurable(self):
		self.set(**{'playback.try.4k': '2', 'playback.try.1080p': '0',
					'playback.try.720p': '1'})
		ranked = ([source('4K', '4k-%d' % n) for n in range(5)] +
				  [source('1080p', 'hd-%d' % n) for n in range(5)] +
				  [source('720p', 'sd-%d' % n) for n in range(5)])
		with self._ranked(ranked):
			queue = player._candidates(source('4K', '4k-0'), EPISODE)
		tiers = [player._tier(i) for i in queue]
		self.assertEqual(tiers.count('4K'), 2)
		self.assertEqual(tiers.count('1080p'), 0)
		self.assertEqual(tiers.count('720p'), 1)


class Resume(AddonTestCase):
	def setUp(self):
		super(Resume, self).setUp()
		self.home = xbmcgui.Window(10000)

	def _progress(self, percent):
		return mock.patch('resources.lib.trakt.playback_progress',
						  return_value=percent)

	def _announced(self):
		raw = self.home.getProperty(scrobbler.RESUME_PROPERTY)
		return __import__('json').loads(raw) if raw else None

	def test_ask_mode_prompts_and_publishes_the_point(self):
		self.answer_yes(True)
		with self._progress(42.0):
			player._apply_resume(EPISODE)
		self.assertEqual(self.last_dialog('yesno')[0], 'yesno')
		self.assertEqual(self._announced()['percent'], 42.0)

	def test_the_prompt_shows_a_timestamp(self):
		self.answer_yes(True)
		with self._progress(42.0):
			player._apply_resume(EPISODE)
		# 42% of a 45-minute episode.
		self.assertIn('00:18:54', self.last_dialog('yesno')[2])

	def test_declining_starts_over_and_clears_trakts_position(self):
		self.answer_yes(False)
		with self._progress(42.0), \
				mock.patch('resources.lib.trakt.clear_playback') as clear:
			player._apply_resume(EPISODE)
		self.assertIsNone(self._announced())
		clear.assert_called_once()

	def test_always_mode_does_not_prompt(self):
		self.set(**{'playback.resume': '1'})
		with self._progress(42.0):
			player._apply_resume(EPISODE)
		self.assertEqual(self.dialogs('yesno'), [])
		self.assertIsNotNone(self._announced())

	def test_never_mode_does_nothing_at_all(self):
		self.set(**{'playback.resume': '2'})
		with self._progress(42.0) as progress:
			player._apply_resume(EPISODE)
		self.assertIsNone(self._announced())
		progress.assert_not_called()

	def test_the_extremes_are_ignored(self):
		self.set(**{'playback.resume': '1'})
		for percent in (0.0, 0.9, 95.1, 100.0):
			self.home.clearProperty(scrobbler.RESUME_PROPERTY)
			with self._progress(percent):
				player._apply_resume(EPISODE)
			self.assertIsNone(self._announced(), percent)

	def test_an_entry_with_no_runtime_is_skipped(self):
		self.set(**{'playback.resume': '1'})
		with self._progress(42.0):
			player._apply_resume(dict(EPISODE, runtime=None))
		self.assertIsNone(self._announced())

	def test_a_trakt_failure_never_blocks_playback(self):
		self.set(**{'playback.resume': '1'})
		with mock.patch('resources.lib.trakt.playback_progress',
						side_effect=RuntimeError('trakt down')):
			player._apply_resume(EPISODE)
		self.assertIsNone(self._announced())


class Play(AddonTestCase):
	def setUp(self):
		super(Play, self).setUp()
		self.set(**{'rd.token': 'token', 'scrobble.enabled': True,
					'trakt.token': 'trakt-token'})

	def _resolve(self, results):
		"""results: list of (url, error, provider) returned in order."""
		return mock.patch('resources.lib.debrid.resolve_magnet',
						  side_effect=list(results))

	def test_a_working_source_is_handed_to_kodi(self):
		with self._resolve([('https://rd/file.mkv', None, 'Real-Debrid')]), \
				mock.patch('resources.lib.trakt.playback_progress', return_value=0):
			player.play(source(), EPISODE)
		self.assertEqual(len(xbmcplugin.RESOLVED), 1)
		self.assertTrue(xbmcplugin.RESOLVED[0]['succeeded'])
		self.assertEqual(xbmcplugin.RESOLVED[0]['item'].path, 'https://rd/file.mkv')

	def test_it_falls_through_to_the_next_source(self):
		ranked = [source('1080p', 'first'), source('1080p', 'second')]
		with mock.patch('resources.lib.scrapers.cached_sources', return_value=ranked), \
				self._resolve([(None, 'not cached', None),
							   ('https://rd/file.mkv', None, 'TorBox')]), \
				mock.patch('resources.lib.trakt.playback_progress', return_value=0):
			player.play(ranked[0], EPISODE)
		self.assertTrue(xbmcplugin.RESOLVED[0]['succeeded'])

	def test_the_underlying_reason_reaches_the_user(self):
		with self._resolve([(None, 'Traffic exhausted (Real-Debrid error 23)', None)]):
			player.play(source(), EPISODE)
		self.assertIn('Traffic exhausted', self.last_dialog('ok')[2])
		self.assertFalse(xbmcplugin.RESOLVED[0]['succeeded'])

	def test_the_service_is_told_what_is_playing(self):
		with self._resolve([('https://rd/file.mkv', None, 'Real-Debrid')]), \
				mock.patch('resources.lib.trakt.playback_progress', return_value=0):
			player.play(source(), EPISODE)
		announced = xbmcgui.Window(10000).getProperty(scrobbler.PLAYBACK_PROPERTY)
		self.assertIn('Severance', announced)

	def test_nothing_is_announced_when_scrobbling_is_off(self):
		self.set(**{'scrobble.enabled': False})
		with self._resolve([('https://rd/file.mkv', None, 'Real-Debrid')]):
			player.play(source(), EPISODE)
		self.assertEqual(
			xbmcgui.Window(10000).getProperty(scrobbler.PLAYBACK_PROPERTY), '')

	def test_resume_is_not_offered_when_the_service_will_not_act_on_it(self):
		# Prompting and then never seeking would be worse than not asking.
		self.set(**{'scrobble.enabled': False, 'playback.resume': '0'})
		with self._resolve([('https://rd/file.mkv', None, 'Real-Debrid')]), \
				mock.patch('resources.lib.trakt.playback_progress',
						   return_value=42.0) as progress:
			player.play(source(), EPISODE)
		self.assertEqual(self.dialogs('yesno'), [])
		progress.assert_not_called()

	def test_the_serving_provider_is_named(self):
		with self._resolve([('https://rd/file.mkv', None, 'TorBox')]), \
				mock.patch('resources.lib.trakt.playback_progress', return_value=0):
			player.play(source(), EPISODE)
		self.assertIn('TorBox', self.last_dialog('notification')[2])

	def test_the_provider_can_be_added_to_the_playing_title(self):
		self.set(**{'playback.label_provider': True})
		with self._resolve([('https://rd/file.mkv', None, 'TorBox')]), \
				mock.patch('resources.lib.trakt.playback_progress', return_value=0):
			player.play(source(), EPISODE)
		self.assertIn('[TB]', xbmcplugin.RESOLVED[0]['item'].label)


class UntriedTiers(AddonTestCase):
	"""A film that only exists as a CAM fails with the default budgets.

	Not because nothing is available - because the SD allowance is zero.
	Saying so turns a mysterious failure into an actionable one.
	"""

	def _ranked(self, items):
		return mock.patch('resources.lib.scrapers.cached_sources',
						  return_value=items)

	def test_a_tier_with_sources_but_no_budget_is_reported(self):
		cams = [source('CAM', 'cam-%d' % n) for n in range(3)]
		with self._ranked(cams):
			self.assertEqual(player.untried_tiers(cams[0], EPISODE),
							 [('SD', 3)])

	def test_a_tier_with_budget_is_not_reported(self):
		with self._ranked([source('1080p', 'hd')]):
			self.assertEqual(player.untried_tiers(source('1080p'), EPISODE), [])

	def test_a_tier_with_no_sources_is_not_reported(self):
		with self._ranked([source('1080p', 'hd')]):
			self.assertEqual(player.untried_tiers(source('1080p'), EPISODE), [])

	def test_giving_sd_a_budget_removes_the_notice(self):
		self.set(**{'playback.try.sd': '3'})
		cams = [source('CAM', 'cam-%d' % n) for n in range(3)]
		with self._ranked(cams):
			self.assertEqual(player.untried_tiers(cams[0], EPISODE), [])

	def test_a_scraper_failure_reports_nothing_rather_than_raising(self):
		with mock.patch('resources.lib.scrapers.cached_sources',
						side_effect=RuntimeError('boom')):
			self.assertEqual(player.untried_tiers(source(), EPISODE), [])

	def test_the_failure_dialog_mentions_them(self):
		cams = [source('CAM', 'cam-%d' % n) for n in range(3)]
		with self._ranked(cams), \
				mock.patch('resources.lib.debrid.resolve_magnet',
						   return_value=(None, 'not cached', None)):
			player.play(cams[0], EPISODE)
		message = self.last_dialog('ok')[2]
		self.assertIn('3 SD', message)


class PlayedItemArtwork(AddonTestCase):
	def setUp(self):
		super(PlayedItemArtwork, self).setUp()
		self.set(**{'rd.token': 'token', 'trakt.token': 'trakt-token'})

	def test_the_items_own_artwork_reaches_kodi(self):
		art = {'poster': 'https://m/p.jpg', 'fanart': 'https://m/f.jpg',
			   'thumb': 'https://m/s.jpg'}
		with mock.patch('resources.lib.debrid.resolve_magnet',
						return_value=('https://rd/file.mkv', None, 'TorBox')), \
				mock.patch('resources.lib.trakt.playback_progress', return_value=0):
			player.play(source(), dict(EPISODE, art=art))
		played = xbmcplugin.RESOLVED[0]['item'].art
		self.assertEqual(played['thumb'], 'https://m/s.jpg')
		self.assertEqual(played['fanart'], 'https://m/f.jpg')

	def test_without_artwork_the_addon_icon_is_used(self):
		with mock.patch('resources.lib.debrid.resolve_magnet',
						return_value=('https://rd/file.mkv', None, 'TorBox')), \
				mock.patch('resources.lib.trakt.playback_progress', return_value=0):
			player.play(source(), EPISODE)
		self.assertTrue(xbmcplugin.RESOLVED[0]['item'].art['thumb'])

	def test_the_sources_cache_flags_choose_the_provider(self):
		asked = []
		with mock.patch('resources.lib.debrid.resolve_magnet',
						side_effect=lambda *a, **k: asked.append(k.get('cached_by'))
						or ('https://tb/f', None, 'TorBox')), \
				mock.patch('resources.lib.trakt.playback_progress', return_value=0):
			player.play(dict(source(), cached_by=['TorBox']), EPISODE)
		self.assertEqual(asked, [['TorBox']])
