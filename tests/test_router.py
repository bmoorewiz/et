# -*- coding: utf-8 -*-
"""Menus, context items and action dispatch."""

import sys
from unittest import mock

import xbmcplugin

from support import AddonTestCase

from resources.lib import control
from resources.lib import router

EPISODE = {
	'media_type': 'episode', 'show_title': 'Severance', 'show_trakt': 111,
	'season': 2, 'episode': 3, 'ep_title': 'Who Is Alive?', 'ep_trakt': 999,
	'runtime': 45, 'first_aired': '2025-01-31T13:00:00.000Z',
}
SHOW = {'show_title': 'Severance', 'show_year': 2022, 'show_trakt': 111}
MOVIE = {'media_type': 'movie', 'title': 'Dune', 'year': 2021, 'movie_trakt': 7}


def query_of(url):
	return control.parse_params(url.split('?', 1)[1])


def command_query(command):
	return control.parse_params(command.split('?', 1)[1].rstrip(')'))


class Codec(AddonTestCase):
	def test_round_trips_an_entry(self):
		self.assertEqual(control.decode_obj(control.encode_obj(EPISODE)), EPISODE)

	def test_the_blob_is_url_safe(self):
		blob = control.encode_obj({'title': 'Über/Show?&=#'})
		self.assertNotIn('/', blob)
		self.assertNotIn('+', blob)

	def test_the_router_and_the_service_share_one_codec(self):
		# The service builds play-next callback URLs without importing the
		# whole router; both must agree on the encoding.
		self.assertIs(router._encode, control.encode_obj)
		self.assertIs(router._decode, control.decode_obj)


class MainMenu(AddonTestCase):
	def _render(self):
		with mock.patch('resources.lib.updater.auto_check'), \
				mock.patch('resources.lib.updater.pending_update', return_value=None):
			router.main_menu()
		return [item['item'].label for item in self.items()]

	def test_lists_the_core_entries(self):
		labels = self._render()
		self.assertTrue(any('Next' in label for label in labels), labels)
		self.assertIn(control.lang(33050), labels)
		self.assertIn(control.lang(33051), labels)

	def test_prompts_to_authorize_when_signed_out(self):
		labels = self._render()
		self.assertTrue(any(control.lang(33004) in label for label in labels))
		self.assertTrue(any(control.lang(33005) in label for label in labels))

	def test_hidden_shows_appears_only_once_trakt_is_linked(self):
		self.assertNotIn(control.lang(33079), self._render())
		xbmcplugin.reset()
		self.set(**{'trakt.token': 'token'})
		self.assertIn(control.lang(33079), self._render())

	def test_an_available_update_is_offered_first(self):
		with mock.patch('resources.lib.updater.auto_check'), \
				mock.patch('resources.lib.updater.pending_update', return_value='9.9.9'):
			router.main_menu()
		first = self.items()[0]
		self.assertIn('9.9.9', first['item'].label)
		self.assertEqual(query_of(first['url'])['action'], 'install_update')


class EpisodeItems(AddonTestCase):
	def _context(self, entry=EPISODE):
		router._add_episode_item(entry, autoplay=False)
		return dict(self.items()[0]['item'].context)

	def test_the_label_matches_the_players(self):
		router._add_episode_item(EPISODE, autoplay=False)
		from resources.lib import player
		self.assertEqual(self.items()[0]['item'].label,
						 player.display_label(EPISODE))

	def test_mark_watched_is_offered(self):
		query = command_query(self._context()[control.lang(33022)])
		self.assertEqual(query['action'], 'mark_watched')
		self.assertEqual(control.decode_obj(query['entry']), EPISODE)

	def test_hide_show_is_offered(self):
		query = command_query(self._context()[control.lang(33076)])
		self.assertEqual(query['action'], 'hide_show')
		self.assertEqual(control.decode_obj(query['entry']), EPISODE)

	def test_hide_show_is_withheld_without_a_show_id(self):
		entry = {k: v for k, v in EPISODE.items() if k != 'show_trakt'}
		self.assertNotIn(control.lang(33076), self._context(entry))

	def test_autoplay_makes_the_item_playable_not_a_folder(self):
		router._add_episode_item(EPISODE, autoplay=True)
		item = self.items()[0]
		self.assertFalse(item['folder'])
		self.assertEqual(item['item'].getProperty('IsPlayable'), 'true')
		self.assertEqual(query_of(item['url'])['action'], 'autoplay')

	def test_without_autoplay_it_opens_the_source_list(self):
		router._add_episode_item(EPISODE, autoplay=False)
		item = self.items()[0]
		self.assertTrue(item['folder'])
		self.assertEqual(query_of(item['url'])['action'], 'sources')


class SourceItems(AddonTestCase):
	def _label(self, source, cache_known=False):
		router._add_source_item(source, EPISODE, cache_known)
		return self.items()[-1]['item'].label

	def _source(self, quality='1080p', **extra):
		return dict({'quality': quality, 'name': 'Some.Release.x265',
					 'seeders': 42, 'size': 5.5, 'provider': 'Site',
					 'hash': 'ABC'}, **extra)

	def test_each_quality_gets_its_own_colour(self):
		colours = {q: self._label(self._source(q))
				   for q in ('4K', '1080p', '720p', 'SD', 'CAM', 'SCR')}
		codes = [router._QUALITY_COLOR[q] for q in colours]
		self.assertEqual(len(set(codes)), len(codes), 'colours must be distinct')
		for quality, label in colours.items():
			self.assertIn(router._QUALITY_COLOR[quality], label, quality)

	def test_an_unknown_quality_still_gets_a_colour(self):
		self.assertIn(router._DEFAULT_QUALITY_COLOR, self._label(self._source('???')))

	def test_the_label_carries_size_seeders_and_provider(self):
		label = self._label(self._source())
		self.assertIn('5.50 GB', label)
		self.assertIn('S:42', label)
		self.assertIn('Site', label)

	def test_cached_sources_are_flagged_with_the_holding_service(self):
		label = self._label(self._source(cached_by=['TorBox']), cache_known=True)
		self.assertIn('[TB+]', label)

	def test_uncached_sources_say_so_when_the_answer_is_known(self):
		self.assertIn('[Download]',
					  self._label(self._source(cached_by=[]), cache_known=True))

	def test_nothing_is_claimed_when_the_answer_is_unknown(self):
		label = self._label(self._source(), cache_known=False)
		self.assertNotIn('[Download]', label)
		self.assertNotIn('+]', label)

	def test_a_junk_size_does_not_break_the_label(self):
		self.assertIn('?', self._label(self._source(size='huge')))


class Actions(AddonTestCase):
	def test_marking_watched_refreshes_the_list(self):
		with mock.patch('resources.lib.trakt.add_to_history', return_value=True):
			router.mark_watched(EPISODE)
		self.assertIn('Container.Refresh', self.builtins())

	def test_a_failed_mark_says_so(self):
		with mock.patch('resources.lib.trakt.add_to_history', return_value=False):
			router.mark_watched(EPISODE)
		self.assertNotIn('Container.Refresh', self.builtins())
		self.assertTrue(self.dialogs('notification'))

	def test_hiding_asks_before_acting(self):
		self.answer_yes(False)
		with mock.patch('resources.lib.trakt.hide_show') as hide:
			router.hide_show(EPISODE)
		hide.assert_not_called()
		self.assertIn('Severance', self.last_dialog('yesno')[2])

	def test_confirming_hides_and_refreshes(self):
		self.answer_yes(True)
		with mock.patch('resources.lib.trakt.hide_show', return_value=True) as hide:
			router.hide_show(EPISODE)
		hide.assert_called_once_with(111)
		self.assertIn('Container.Refresh', self.builtins())

	def test_unhiding_refreshes(self):
		with mock.patch('resources.lib.trakt.unhide_show', return_value=True):
			router.unhide_show(SHOW)
		self.assertIn('Container.Refresh', self.builtins())

	def test_hidden_shows_are_listed_and_unhide_on_click(self):
		with mock.patch('resources.lib.trakt.hidden_shows', return_value=[SHOW]):
			router.hidden_shows_menu()
		item = self.items()[0]
		self.assertEqual(item['item'].label, 'Severance (2022)')
		self.assertEqual(query_of(item['url'])['action'], 'unhide_show')
		self.assertFalse(self.items()[0]['folder'])

	def test_an_empty_hidden_list_says_so(self):
		with mock.patch('resources.lib.trakt.hidden_shows', return_value=[]):
			router.hidden_shows_menu()
		self.assertEqual(self.items()[0]['item'].label, control.lang(33081))

	def test_search_results_are_not_cached_to_disc(self):
		with mock.patch('resources.lib.trakt.hidden_shows', return_value=[SHOW]):
			router.hidden_shows_menu()
		self.assertFalse(xbmcplugin.ENDED[0]['cacheToDisc'])


class Preflight(AddonTestCase):
	def _run(self):
		with mock.patch('resources.lib.scrapers.available', return_value=True):
			return router._preflight()

	def test_refuses_without_cocoscrapers(self):
		with mock.patch('resources.lib.scrapers.available', return_value=False):
			self.assertFalse(router._preflight())
		self.assertTrue(self.dialogs('ok'))

	def test_refuses_without_a_debrid_provider(self):
		self.set(**{'trakt.token': 'token'})
		self.assertFalse(self._run())

	def test_refuses_without_trakt(self):
		self.set(**{'rd.token': 'token'})
		with mock.patch('resources.lib.debrid.account_status',
						return_value=(True, 'fine')):
			self.assertFalse(self._run())

	def test_an_unhealthy_account_is_reported_up_front(self):
		# Otherwise it fails once per source instead of once.
		self.set(**{'rd.token': 'token', 'trakt.token': 'token'})
		with mock.patch('resources.lib.debrid.account_status',
						return_value=(False, 'Real-Debrid: expired')):
			self.assertFalse(self._run())
		self.assertIn('expired', self.last_dialog('ok')[2])

	def test_passes_when_everything_is_set_up(self):
		self.set(**{'rd.token': 'token', 'trakt.token': 'token'})
		with mock.patch('resources.lib.debrid.account_status',
						return_value=(True, 'fine')):
			self.assertTrue(self._run())


class Dispatch(AddonTestCase):
	def _dispatch(self, **params):
		query = '?' + '&'.join('%s=%s' % pair for pair in params.items())
		with mock.patch.object(sys, 'argv',
							   ['plugin://plugin.video.episodetracker/', '1', query]):
			router.dispatch()

	def test_every_action_reaches_its_handler(self):
		entry = control.encode_obj(EPISODE)
		cases = [
			('next_episodes', {}, 'next_episodes_menu'),
			('sources', {'entry': entry}, 'sources_menu'),
			('autoplay', {'entry': entry}, 'autoplay'),
			('mark_watched', {'entry': entry}, 'mark_watched'),
			('hide_show', {'entry': entry}, 'hide_show'),
			('unhide_show', {'entry': entry}, 'unhide_show'),
			('hidden_shows', {}, 'hidden_shows_menu'),
			('search_shows', {}, 'search_menu'),
			('search_movies', {}, 'search_menu'),
			('show_seasons', {'entry': entry}, 'seasons_menu'),
			('season_episodes', {'entry': entry, 'season': '2'}, 'episodes_menu'),
			('torbox_check', {}, 'torbox_check'),
			('upload_logs', {}, 'upload_logs'),
			('clear_cache', {}, 'clear_cache'),
			('coco_settings', {}, 'open_coco_settings'),
		]
		for action, params, handler in cases:
			with mock.patch.object(router, handler) as target:
				self._dispatch(action=action, **params)
			self.assertTrue(target.called, '%s did not reach %s' % (action, handler))

	def test_the_play_action_carries_source_and_entry_together(self):
		payload = control.encode_obj({'source': {'hash': 'ABC'}, 'entry': EPISODE})
		with mock.patch('resources.lib.player.play') as play:
			self._dispatch(action='play', data=payload)
		play.assert_called_once_with({'hash': 'ABC'}, EPISODE)

	def test_an_unknown_action_falls_back_to_the_main_menu(self):
		with mock.patch.object(router, 'main_menu') as main:
			self._dispatch(action='no_such_action')
		main.assert_called_once()

	def test_no_action_shows_the_main_menu(self):
		with mock.patch.object(router, 'main_menu') as main:
			with mock.patch.object(sys, 'argv',
								   ['plugin://plugin.video.episodetracker/', '1', '']):
				router.dispatch()
		main.assert_called_once()
