# -*- coding: utf-8 -*-
"""Menus, context items and action dispatch."""

import sys
from unittest import mock

import xbmcgui
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

	def test_next_episodes_is_never_cached_to_disc(self):
		# Kodi would redisplay the copy it made before playback started, so
		# an episode finished mid-playback would still be sitting there when
		# you back out of the player.
		self.set(**{'trakt.token': 'token'})
		with mock.patch('resources.lib.trakt.next_episodes', return_value=[EPISODE]):
			router.next_episodes_menu()
		self.assertFalse(xbmcplugin.ENDED[0]['cacheToDisc'])

	def test_an_empty_next_episodes_list_is_not_cached_either(self):
		self.set(**{'trakt.token': 'token'})
		with mock.patch('resources.lib.trakt.next_episodes', return_value=[]):
			router.next_episodes_menu()
		self.assertFalse(xbmcplugin.ENDED[0]['cacheToDisc'])

	def test_the_signed_out_prompt_is_not_cached_either(self):
		with mock.patch('resources.lib.trakt.next_episodes', return_value=[]):
			router.next_episodes_menu()
		self.assertFalse(xbmcplugin.ENDED[0]['cacheToDisc'])

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


class ContinueWatching(AddonTestCase):
	PART_WATCHED = dict(EPISODE, progress=42.5,
						paused_at='2026-08-01T00:00:00.000Z')
	PART_MOVIE = dict(MOVIE, progress=12.0,
					  paused_at='2026-08-05T00:00:00.000Z')

	def _menu(self, entries):
		with mock.patch('resources.lib.trakt.in_progress', return_value=entries):
			router.continue_watching_menu()

	def test_it_lists_what_is_part_watched(self):
		self._menu([self.PART_WATCHED])
		self.assertEqual(len(self.items()), 1)
		self.assertIn('Severance', self.items()[0]['item'].label)

	def test_the_position_is_shown(self):
		self._menu([self.PART_WATCHED])
		self.assertIn('(42%)', self.items()[0]['item'].label)

	def test_movies_and_episodes_both_render(self):
		self._menu([self.PART_WATCHED, self.PART_MOVIE])
		labels = [i['item'].label for i in self.items()]
		self.assertTrue(any('Severance' in l for l in labels), labels)
		self.assertTrue(any('Dune' in l for l in labels), labels)

	def test_an_empty_list_says_so(self):
		self._menu([])
		self.assertEqual(self.items()[0]['item'].label, control.lang(33087))

	def test_it_is_never_cached_to_disc(self):
		# Finishing something changes this list while you are away from it.
		self._menu([self.PART_WATCHED])
		self.assertFalse(xbmcplugin.ENDED[0]['cacheToDisc'])

	def test_entries_open_their_sources(self):
		self._menu([self.PART_WATCHED])
		self.assertEqual(query_of(self.items()[0]['url'])['action'], 'sources')


class RefreshSources(AddonTestCase):
	def test_it_clears_only_this_items_cache_then_reopens(self):
		from resources.lib import cache, scrapers
		cache.set(scrapers._cache_key(EPISODE), [{'hash': 'A'}])
		other = dict(EPISODE, episode=9)
		cache.set(scrapers._cache_key(other), [{'hash': 'B'}])
		with mock.patch.object(router, 'sources_menu') as reopen:
			router.refresh_sources(EPISODE)
		self.assertEqual(cache.get(scrapers._cache_key(EPISODE)), None)
		self.assertEqual(cache.get(scrapers._cache_key(other)), [{'hash': 'B'}])
		reopen.assert_called_once_with(EPISODE)

	def test_it_is_offered_on_episode_items(self):
		router._add_episode_item(EPISODE, autoplay=False)
		command = dict(self.items()[0]['item'].context)[control.lang(33086)]
		self.assertEqual(command_query(command)['action'], 'refresh_sources')

	def test_it_is_offered_on_movie_items(self):
		router._add_movie_item(MOVIE)
		command = dict(self.items()[0]['item'].context)[control.lang(33086)]
		self.assertEqual(command_query(command)['action'], 'refresh_sources')


class SeriesProgress(AddonTestCase):
	def test_the_counter_is_shown(self):
		router._add_episode_item(dict(EPISODE, aired_count=10,
									  completed_count=3), autoplay=False)
		self.assertIn('(3/10)', self.items()[0]['item'].label)

	def test_it_can_be_turned_off(self):
		self.set(**{'list.show_progress': False})
		router._add_episode_item(dict(EPISODE, aired_count=10,
									  completed_count=3), autoplay=False)
		self.assertNotIn('(3/10)', self.items()[0]['item'].label)

	def test_missing_counts_show_nothing(self):
		router._add_episode_item(EPISODE, autoplay=False)
		self.assertNotIn('/', self.items()[0]['item'].label.split(' - ')[-1])

	def test_junk_counts_do_not_crash(self):
		router._add_episode_item(dict(EPISODE, aired_count='x',
									  completed_count=None), autoplay=False)
		self.assertEqual(len(self.items()), 1)

	def test_a_resume_suffix_replaces_the_counter(self):
		# Continue Watching wants "how far into this episode", not
		# "how far through the series".
		router._add_episode_item(dict(EPISODE, aired_count=10, completed_count=3),
								 autoplay=False, suffix='  (42%)')
		label = self.items()[0]['item'].label
		self.assertIn('(42%)', label)
		self.assertNotIn('(3/10)', label)


class Artwork(AddonTestCase):
	ART = {'poster': 'https://m/p.jpg', 'fanart': 'https://m/f.jpg',
		   'thumb': 'https://m/s.jpg'}

	def test_episode_items_carry_their_artwork(self):
		router._add_episode_item(dict(EPISODE, art=self.ART), autoplay=False)
		self.assertEqual(self.items()[0]['item'].art['poster'], 'https://m/p.jpg')

	def test_movie_items_carry_their_artwork(self):
		router._add_movie_item(dict(MOVIE, art=self.ART))
		self.assertEqual(self.items()[0]['item'].art['thumb'], 'https://m/s.jpg')

	def test_an_entry_without_artwork_still_gets_the_addon_icon(self):
		router._add_episode_item(EPISODE, autoplay=False)
		self.assertTrue(self.items()[0]['item'].art['icon'])

	def test_source_rows_inherit_the_items_artwork(self):
		router._add_source_item({'quality': '1080p', 'hash': 'A', 'name': 'x'},
								dict(EPISODE, art=self.ART))
		self.assertEqual(self.items()[0]['item'].art['fanart'], 'https://m/f.jpg')


class SeasonPackBadge(AddonTestCase):
	def test_a_pack_is_flagged_in_the_list(self):
		router._add_source_item({'quality': '1080p', 'hash': 'A',
								 'name': 'Severance.S01.1080p', 'size': 9.85,
								 'package': 'season'}, EPISODE)
		self.assertIn(control.lang(33089), self.items()[0]['item'].label)

	def test_a_single_episode_is_not_flagged(self):
		router._add_source_item({'quality': '1080p', 'hash': 'A',
								 'name': 'Severance.S01E03.1080p'}, EPISODE)
		self.assertNotIn(control.lang(33089), self.items()[0]['item'].label)

	def test_a_cached_pack_shows_both_badges(self):
		router._add_source_item({'quality': '1080p', 'hash': 'A', 'name': 'x',
								 'package': 'season', 'cached_by': ['TorBox']},
								EPISODE, cache_known=True)
		label = self.items()[0]['item'].label
		self.assertIn('[TB+]', label)
		self.assertIn(control.lang(33089), label)


class NoBlankScreens(AddonTestCase):
	"""A folder must never be closed with nothing in it.

	Reported: cancelling a search "errors out to the main screen instead
	of going back". By the time a handler knows it has nothing to show,
	Kodi has already committed to navigating into the folder - it cannot
	be told to stay put. An empty listing is a blank screen, and closing
	the folder as *failed* is worse: CGUIMediaWindow::Update() logs an
	error and falls back to the add-on's root.
	"""

	def _folders(self):
		"""Every handler that closes a directory, and how to reach a dead end."""
		return [
			('cancelled show search', lambda: router.search_menu('show')),
			('cancelled movie search', lambda: router.search_menu('movie')),
			('search with no results', self._empty_search),
			('no seasons', self._no_seasons),
			('no episodes', self._no_episodes),
			('no next episodes', self._no_next_episodes),
			('nothing part-watched', self._nothing_in_progress),
			('no hidden shows', self._no_hidden),
			('preflight refuses', self._preflight_fails),
			('cocoscrapers missing', self._no_module),
			('no sources', self._no_sources),
			('no cached sources', self._no_cached_sources),
		]

	def _empty_search(self):
		xbmcgui.INPUT_QUEUE.append('nothing matches this')
		with mock.patch('resources.lib.trakt.search_shows', return_value=[]):
			router.search_menu('show')

	def _no_seasons(self):
		with mock.patch('resources.lib.trakt.show_seasons', return_value=[]):
			router.seasons_menu(SHOW)

	def _no_episodes(self):
		with mock.patch('resources.lib.trakt.season_episodes', return_value=[]):
			router.episodes_menu(SHOW, '1')

	def _no_next_episodes(self):
		self.set(**{'trakt.token': 'token'})
		with mock.patch('resources.lib.trakt.next_episodes', return_value=[]):
			router.next_episodes_menu()

	def _nothing_in_progress(self):
		with mock.patch('resources.lib.trakt.in_progress', return_value=[]):
			router.continue_watching_menu()

	def _no_hidden(self):
		with mock.patch('resources.lib.trakt.hidden_shows', return_value=[]):
			router.hidden_shows_menu()

	def _preflight_fails(self):
		with mock.patch.object(router, '_preflight', return_value=False):
			router.sources_menu(EPISODE)

	def _no_module(self):
		from resources.lib import scrapers
		with mock.patch.object(router, '_preflight', return_value=True), \
				mock.patch.object(scrapers, 'scrape',
								  return_value=scrapers.MODULE_MISSING):
			router.sources_menu(EPISODE)

	def _no_sources(self):
		from resources.lib import scrapers
		with mock.patch.object(router, '_preflight', return_value=True), \
				mock.patch.object(scrapers, 'scrape', return_value=[]):
			router.sources_menu(EPISODE)

	def _no_cached_sources(self):
		from resources.lib import scrapers
		with mock.patch.object(router, '_preflight', return_value=True), \
				mock.patch.object(scrapers, 'scrape', return_value=[{'hash': 'A'}]), \
				mock.patch.object(scrapers, 'annotate_cached', return_value=([], True)):
			router.sources_menu(EPISODE)

	def test_no_dead_end_hands_kodi_an_empty_listing(self):
		for name, run in self._folders():
			xbmcplugin.reset()
			xbmcgui.reset()
			run()
			self.assertTrue(self.items(), '%s produced a blank screen' % name)
			self.assertTrue(self.items()[0]['item'].label,
							'%s produced an unlabelled item' % name)

	def test_every_dead_end_still_succeeds(self):
		# Closing the folder as failed makes Kodi fall back to the root and
		# show an error - exactly the reported symptom.
		for name, run in self._folders():
			xbmcplugin.reset()
			xbmcgui.reset()
			run()
			self.assertTrue(xbmcplugin.ENDED, '%s never closed the folder' % name)
			self.assertTrue(xbmcplugin.ENDED[-1]['succeeded'],
							'%s closed the folder as failed' % name)


class CancelledSearch(AddonTestCase):
	def test_it_offers_another_go(self):
		router.search_menu('show')
		self.assertEqual(self.items()[0]['item'].label, control.lang(33090))

	def test_that_offer_reopens_the_same_search(self):
		router.search_menu('movie')
		self.assertEqual(query_of(self.items()[0]['url'])['action'],
						 'search_movies')

	def test_no_search_request_is_made(self):
		with mock.patch('resources.lib.trakt.search_shows') as search:
			router.search_menu('show')
		search.assert_not_called()

	def test_nothing_found_lands_in_the_same_place(self):
		xbmcgui.INPUT_QUEUE.append('zzzz')
		with mock.patch('resources.lib.trakt.search_movies', return_value=[]):
			router.search_menu('movie')
		self.assertEqual(self.items()[0]['item'].label, control.lang(33090))
		# A notification, not a modal the user has to dismiss first.
		self.assertEqual(self.dialogs('ok'), [])


class DispatchSafetyNet(AddonTestCase):
	"""A crash must not leave the directory unclosed.

	Kodi treats a plugin that never calls endOfDirectory as a failure and
	falls back to the add-on's root, so a bug anywhere becomes "it dumped
	me on the main screen" with no explanation.
	"""

	def _dispatch(self, handle=1, **params):
		# control.handle is read from sys.argv once at import, exactly as it
		# is in Kodi, so a test that changes argv has to move it too.
		query = '?' + '&'.join('%s=%s' % pair for pair in params.items())
		with mock.patch.object(sys, 'argv',
							   ['plugin://plugin.video.episodetracker/',
								str(handle), query]), \
				mock.patch.object(control, 'handle', handle):
			router.dispatch()

	def test_a_runplugin_action_reports_through_a_dialog_instead(self):
		# RunPlugin invocations get handle -1; there is no directory to close.
		with mock.patch.object(router, 'mark_watched',
							   side_effect=RuntimeError('boom')):
			self._dispatch(handle=-1, action='mark_watched',
						   entry=control.encode_obj(EPISODE))
		self.assertEqual(xbmcplugin.ENDED, [])
		self.assertIn('boom', self.last_dialog('ok')[2])

	def test_a_crashing_handler_still_closes_the_folder(self):
		with mock.patch.object(router, 'next_episodes_menu',
							   side_effect=RuntimeError('boom')):
			self._dispatch(action='next_episodes')
		self.assertTrue(xbmcplugin.ENDED)
		self.assertTrue(xbmcplugin.ENDED[-1]['succeeded'])

	def test_the_reason_is_shown_where_the_user_is_looking(self):
		with mock.patch.object(router, 'next_episodes_menu',
							   side_effect=RuntimeError('boom')):
			self._dispatch(action='next_episodes')
		self.assertIn('boom', self.items()[0]['item'].label)

	def test_a_malformed_request_does_not_crash_out(self):
		self._dispatch(action='sources', entry='not-valid-base64!!')
		self.assertTrue(xbmcplugin.ENDED[-1]['succeeded'])

	def test_a_missing_parameter_does_not_crash_out(self):
		self._dispatch(action='season_episodes')
		self.assertTrue(xbmcplugin.ENDED[-1]['succeeded'])

	def test_the_failure_is_logged(self):
		with mock.patch.object(router, 'next_episodes_menu',
							   side_effect=RuntimeError('boom')):
			self._dispatch(action='next_episodes')
		self.assertIn('request failed', self.logged())

	def test_a_working_request_is_untouched(self):
		with mock.patch.object(router, 'next_episodes_menu') as handler:
			self._dispatch(action='next_episodes')
		handler.assert_called_once()
