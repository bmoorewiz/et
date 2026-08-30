# -*- coding: utf-8 -*-
"""Trakt: next-up episodes, scrobbles, playback position and hidden shows."""

from unittest import mock

import xbmcgui

from support import AddonTestCase, FakeResponse

from resources.lib import control
from resources.lib import trakt

EPISODE = {
	'media_type': 'episode', 'show_title': 'Severance', 'show_trakt': 111,
	'season': 2, 'episode': 3, 'ep_title': 'Who Is Alive?', 'ep_trakt': 999,
	'ep_imdb': 'tt1', 'runtime': 45,
}
MOVIE = {'media_type': 'movie', 'title': 'Dune', 'movie_trakt': 7,
		 'imdb': 'tt1160419'}


class FakeTrakt(object):
	"""Routes Trakt calls by ``(METHOD, path)`` and records what was sent."""

	def __init__(self):
		self.routes = {}
		self.calls = []

	def route(self, method, path, payload, status_code=200):
		self.routes[(method.upper(), path)] = (payload, status_code)
		return self

	def __call__(self, method, url, params=None, json=None, headers=None,
				 timeout=None):
		path = url.replace(trakt.API_BASE, '')
		self.calls.append({'method': method.upper(), 'path': path,
						   'params': params or {}, 'json': json,
						   'headers': headers or {}})
		payload, status_code = self.routes.get((method.upper(), path),
											   (None, 404))
		return FakeResponse(payload, status_code)

	def paths(self):
		return [call['path'] for call in self.calls]

	def sent(self, method, path):
		for call in self.calls:
			if call['method'] == method.upper() and call['path'] == path:
				return call
		return None


class TraktBase(AddonTestCase):
	def setUp(self):
		super(TraktBase, self).setUp()
		self.set(**{'trakt.token': 'token', 'trakt.user': 'tester',
					'trakt.expires': '9999999999'})
		self.api = FakeTrakt()
		patcher = mock.patch.object(trakt.requests, 'request', self.api)
		patcher.start()
		self.addCleanup(patcher.stop)


class Credentials(AddonTestCase):
	def test_the_app_credentials_are_built_in(self):
		# A Trakt client secret identifies the application only; without it
		# baked in, every install would need its own Trakt app.
		self.assertTrue(trakt.has_credentials())

	def test_a_user_supplied_client_id_wins(self):
		self.set(**{'trakt.client_id': 'mine', 'trakt.client_secret': 'also-mine'})
		self.assertEqual(trakt.client_id(), 'mine')

	def test_the_token_is_what_makes_it_authorized(self):
		self.assertFalse(trakt.authorized())
		self.set(**{'trakt.token': 'x'})
		self.assertTrue(trakt.authorized())


class Requests(TraktBase):
	def test_every_call_carries_the_api_version_and_key(self):
		self.api.route('GET', '/sync/watched/shows', [])
		trakt._watched_shows()
		headers = self.api.calls[0]['headers']
		self.assertEqual(headers['trakt-api-version'], '2')
		self.assertTrue(headers['trakt-api-key'])
		self.assertEqual(headers['Authorization'], 'Bearer token')

	def test_a_204_counts_as_success(self):
		self.api.route('POST', '/anything', None, 204)
		self.assertTrue(trakt._request('POST', '/anything'))

	def test_an_error_status_is_reported_as_nothing(self):
		self.api.route('GET', '/anything', {'error': 'nope'}, 500)
		self.assertIsNone(trakt._request('GET', '/anything'))

	def test_a_transport_failure_is_survived(self):
		with mock.patch.object(trakt.requests, 'request',
							   side_effect=RuntimeError('offline')):
			self.assertIsNone(trakt._request('GET', '/anything'))

	def test_a_401_triggers_exactly_one_refresh_attempt(self):
		self.api.route('GET', '/sync/watched/shows', {'error': 'unauthorized'}, 401)
		with mock.patch.object(trakt, '_refresh_token', return_value=False) as refresh:
			trakt._request('GET', '/sync/watched/shows')
		refresh.assert_called_once()


class NextEpisodes(TraktBase):
	def _progress(self, next_episode):
		return {'aired': 10, 'completed': 3, 'next_episode': next_episode}

	def _setup(self):
		self.api.route('GET', '/sync/watched/shows', [
			{'last_watched_at': '2025-02-01T00:00:00.000Z',
			 'show': {'title': 'Severance', 'year': 2022,
					  'ids': {'trakt': 111, 'slug': 'severance',
							  'imdb': 'tt11280740', 'tvdb': 371980}}},
		])
		self.api.route('GET', '/shows/111/progress/watched', self._progress({
			'season': 2, 'number': 3, 'title': 'Who Is Alive?',
			'ids': {'trakt': 999, 'imdb': 'tt1'}, 'runtime': 45,
			'first_aired': '2025-01-31T13:00:00.000Z', 'overview': 'plot',
		}))

	def test_builds_an_entry_per_show(self):
		self._setup()
		entries = trakt.next_episodes(refresh=True)
		self.assertEqual(len(entries), 1)
		entry = entries[0]
		self.assertEqual(entry['show_title'], 'Severance')
		self.assertEqual((entry['season'], entry['episode']), (2, 3))
		self.assertEqual(entry['ep_trakt'], 999)
		self.assertEqual(entry['media_type'], 'episode')

	def test_a_caught_up_show_produces_no_entry(self):
		self._setup()
		self.api.route('GET', '/shows/111/progress/watched', self._progress(None))
		self.assertEqual(trakt.next_episodes(refresh=True), [])

	def test_results_are_cached(self):
		# The next-up list itself comes from cache; only the part-watched
		# lookup is repeated, because where you got to changes as you watch.
		self._setup()
		trakt.next_episodes(refresh=True)
		before = self.api.paths()
		trakt.next_episodes()
		added = self.api.paths()[len(before):]
		self.assertEqual(added, ['/sync/playback/episodes'])

	def test_marking_watched_invalidates_the_cache(self):
		self._setup()
		trakt.next_episodes(refresh=True)
		self.api.route('POST', '/sync/history', {'added': {'episodes': 1}})
		trakt.add_to_history(EPISODE)
		before = len(self.api.calls)
		trakt.next_episodes()
		self.assertGreater(len(self.api.calls), before, 'cache should have gone')

	def test_an_empty_result_is_only_cached_briefly(self):
		# Every progress call failing at once looks identical to a
		# caught-up account; a transient failure must not hide the list for
		# the whole cache window.
		from resources.lib import cache
		self._setup()
		self.api.route('GET', '/shows/111/progress/watched', None, 500)
		with mock.patch.object(cache, 'set') as setter:
			self.assertEqual(trakt.next_episodes(refresh=True), [])
		self.assertEqual(setter.call_args.args[0], 'trakt_next_tester')
		self.assertEqual(setter.call_args.kwargs['hours'], 0.25)

	def test_a_real_result_is_cached_for_the_configured_window(self):
		from resources.lib import cache
		self._setup()
		self.set(**{'cache.hours': '6'})
		with mock.patch.object(cache, 'set') as setter:
			trakt.next_episodes(refresh=True)
		self.assertEqual(setter.call_args.kwargs['hours'], 6)


class HasAired(AddonTestCase):
	"""Unaired episodes must never reach the list.

	Reported: episodes that had not aired yet were showing up. The old
	check read only ``first_aired`` and treated a missing value as "assume
	it aired" - and Trakt's progress endpoint does not reliably populate
	that field on next_episode, so those episodes went straight through.
	"""

	NOW = 1786000000.0  # a fixed "now" so the tests do not drift

	def entry(self, **fields):
		return dict({'show_title': 'X', 'aired_count': None,
					 'completed_count': None, 'first_aired': None}, **fields)

	def test_a_past_air_date_has_aired(self):
		self.assertTrue(trakt.has_aired(
			self.entry(first_aired='2025-01-31T13:00:00.000Z'), self.NOW))

	def test_a_future_air_date_has_not(self):
		self.assertFalse(trakt.has_aired(
			self.entry(first_aired='2099-01-01T00:00:00.000Z'), self.NOW))

	def test_no_date_falls_back_to_the_counts(self):
		# Everything aired has been watched, so whatever is next has not aired.
		self.assertFalse(trakt.has_aired(
			self.entry(aired_count=12, completed_count=12), self.NOW))

	def test_the_counts_also_recognise_an_aired_episode(self):
		self.assertTrue(trakt.has_aired(
			self.entry(aired_count=12, completed_count=10), self.NOW))

	def test_a_show_with_nothing_aired_yet(self):
		self.assertFalse(trakt.has_aired(
			self.entry(aired_count=0, completed_count=0), self.NOW))

	def test_watching_ahead_of_broadcast_still_counts_as_unaired(self):
		self.assertFalse(trakt.has_aired(
			self.entry(aired_count=10, completed_count=11), self.NOW))

	def test_an_air_date_wins_over_the_counts(self):
		# The date is exact; the counts are derived and can lag.
		self.assertFalse(trakt.has_aired(
			self.entry(first_aired='2099-01-01T00:00:00.000Z',
					   aired_count=12, completed_count=1), self.NOW))
		self.assertTrue(trakt.has_aired(
			self.entry(first_aired='2020-01-01T00:00:00.000Z',
					   aired_count=12, completed_count=12), self.NOW))

	def test_with_no_signal_at_all_it_is_shown(self):
		# A missing episode is much harder to notice than an extra one.
		self.assertTrue(trakt.has_aired(self.entry(), self.NOW))

	def test_unparseable_dates_and_counts_do_not_crash(self):
		trakt.has_aired(self.entry(first_aired='soon', aired_count='lots',
								   completed_count=None), self.NOW)

	def test_the_list_drops_unaired_entries(self):
		entries = [self.entry(show_title='Aired', aired_count=12,
							  completed_count=10),
				   self.entry(show_title='Unaired', aired_count=12,
							  completed_count=12)]
		self.assertEqual([e['show_title'] for e in trakt._post_filter(entries)],
						 ['Aired'])

	def test_the_setting_still_lets_them_through(self):
		self.set(**{'list.aired_only': False})
		entries = [self.entry(show_title='Unaired', aired_count=12,
							  completed_count=12)]
		self.assertEqual(len(trakt._post_filter(entries)), 1)


class AirDateBackfill(TraktBase):
	"""Trakt's progress endpoint often omits the next episode's air date."""

	def _progress_without_date(self):
		self.api.route('GET', '/sync/watched/shows', [
			{'last_watched_at': '2025-02-01T00:00:00.000Z',
			 'show': {'title': 'Severance', 'year': 2022,
					  'ids': {'trakt': 111, 'slug': 'severance'}}}])
		self.api.route('GET', '/shows/111/progress/watched', {
			'aired': 10, 'completed': 9,
			# exactly what the endpoint returns in practice: no first_aired,
			# no runtime, no overview
			'next_episode': {'season': 2, 'number': 3, 'title': 'Who Is Alive?',
							 'ids': {'trakt': 999}}})

	def test_the_air_date_is_fetched_from_the_episode(self):
		self._progress_without_date()
		self.api.route('GET', '/shows/111/seasons/2/episodes/3', {
			'first_aired': '2025-01-31T13:00:00.000Z', 'runtime': 45,
			'overview': 'plot'})
		entry = trakt.next_episodes(refresh=True)[0]
		self.assertEqual(entry['first_aired'], '2025-01-31T13:00:00.000Z')

	def test_the_runtime_comes_back_too(self):
		# Without a runtime the resume prompt can never work out a position.
		self._progress_without_date()
		self.api.route('GET', '/shows/111/seasons/2/episodes/3', {
			'first_aired': '2025-01-31T13:00:00.000Z', 'runtime': 45,
			'overview': 'plot'})
		entry = trakt.next_episodes(refresh=True)[0]
		self.assertEqual(entry['runtime'], 45)
		self.assertEqual(entry['plot'], 'plot')

	def test_an_unaired_episode_fetched_this_way_is_then_hidden(self):
		self._progress_without_date()
		self.api.route('GET', '/shows/111/seasons/2/episodes/3',
					   {'first_aired': '2099-01-01T00:00:00.000Z'})
		self.assertEqual(trakt.next_episodes(refresh=True), [])

	def test_no_extra_request_when_the_date_is_already_there(self):
		self.api.route('GET', '/sync/watched/shows', [
			{'last_watched_at': '2025-02-01T00:00:00.000Z',
			 'show': {'title': 'Severance', 'ids': {'trakt': 111}}}])
		self.api.route('GET', '/shows/111/progress/watched', {
			'aired': 10, 'completed': 9,
			'next_episode': {'season': 2, 'number': 3, 'title': 'T',
							 'ids': {'trakt': 999}, 'runtime': 45,
							 'first_aired': '2025-01-31T13:00:00.000Z'}})
		trakt.next_episodes(refresh=True)
		self.assertIsNone(self.api.sent('GET', '/shows/111/seasons/2/episodes/3'))

	def test_a_failed_lookup_leaves_the_counts_to_decide(self):
		self._progress_without_date()  # aired 10, completed 9 -> has aired
		self.api.route('GET', '/shows/111/seasons/2/episodes/3', None, 500)
		entries = trakt.next_episodes(refresh=True)
		self.assertEqual(len(entries), 1)
		self.assertIsNone(entries[0]['first_aired'])

	def test_counts_are_kept_as_none_when_trakt_omits_them(self):
		self.api.route('GET', '/sync/watched/shows', [
			{'show': {'title': 'X', 'ids': {'trakt': 111}}}])
		self.api.route('GET', '/shows/111/progress/watched', {
			'next_episode': {'season': 1, 'number': 1, 'ids': {'trakt': 9},
							 'first_aired': '2020-01-01T00:00:00.000Z'}})
		entry = trakt.next_episodes(refresh=True)[0]
		self.assertIsNone(entry['aired_count'])
		self.assertIsNone(entry['completed_count'])


class PostFilter(AddonTestCase):
	def _entries(self):
		return [
			{'show_title': 'Bravo', 'first_aired': '2020-01-01T00:00:00.000Z',
			 'last_watched': '2025-01-01T00:00:00.000Z'},
			{'show_title': 'Alpha', 'first_aired': '2099-01-01T00:00:00.000Z',
			 'last_watched': '2025-06-01T00:00:00.000Z'},
		]

	def test_unaired_episodes_are_hidden_by_default(self):
		self.assertEqual([e['show_title'] for e in trakt._post_filter(self._entries())],
						 ['Bravo'])

	def test_unaired_episodes_can_be_shown(self):
		self.set(**{'list.aired_only': False})
		self.assertEqual(len(trakt._post_filter(self._entries())), 2)

	def test_sorts_by_last_watched_by_default(self):
		self.set(**{'list.aired_only': False})
		self.assertEqual([e['show_title'] for e in trakt._post_filter(self._entries())],
						 ['Alpha', 'Bravo'])

	def test_alphabetical_sorting(self):
		self.set(**{'list.aired_only': False, 'list.sort': '2'})
		self.assertEqual([e['show_title'] for e in trakt._post_filter(self._entries())],
						 ['Alpha', 'Bravo'])

	def test_an_entry_with_no_air_date_is_kept(self):
		entries = [{'show_title': 'X', 'first_aired': None}]
		self.assertEqual(len(trakt._post_filter(entries)), 1)


class MediaReferences(AddonTestCase):
	def test_episodes_reference_episode_ids(self):
		self.assertEqual(trakt._media_ref(EPISODE)['ids'],
						 {'trakt': 999, 'imdb': 'tt1'})

	def test_movies_reference_movie_ids(self):
		self.assertEqual(trakt._media_ref(MOVIE)['ids'],
						 {'trakt': 7, 'imdb': 'tt1160419'})

	def test_an_entry_with_no_ids_is_empty_not_wrong(self):
		self.assertEqual(trakt._media_ref({'media_type': 'episode'})['ids'], {})


class Scrobble(TraktBase):
	def test_sends_the_episode_and_progress(self):
		self.api.route('POST', '/scrobble/start', {'action': 'start'})
		trakt.scrobble(EPISODE, 'start', 42.5)
		call = self.api.sent('POST', '/scrobble/start')
		self.assertEqual(call['json']['progress'], 42.5)
		self.assertEqual(call['json']['episode']['ids']['trakt'], 999)

	def test_movies_are_sent_under_the_movie_key(self):
		self.api.route('POST', '/scrobble/stop', {'action': 'scrobble'})
		trakt.scrobble(MOVIE, 'stop', 100.0)
		self.assertIn('movie', self.api.sent('POST', '/scrobble/stop')['json'])

	def test_an_entry_with_no_ids_is_not_sent(self):
		trakt.scrobble({'media_type': 'episode'}, 'start', 10.0)
		self.assertEqual(self.api.calls, [])

	def test_scrobbling_can_be_turned_off(self):
		self.set(**{'scrobble.enabled': False})
		trakt.scrobble(EPISODE, 'start', 10.0)
		self.assertEqual(self.api.calls, [])


class PlaybackPosition(TraktBase):
	def _playback(self, items):
		self.api.route('GET', '/sync/playback/episodes', items)

	def test_finds_this_episodes_position(self):
		self._playback([
			{'id': 1, 'progress': 12.0, 'episode': {'ids': {'trakt': 111}}},
			{'id': 2, 'progress': 42.5, 'episode': {'ids': {'trakt': 999}}},
		])
		self.assertEqual(trakt.playback_progress(EPISODE), 42.5)

	def test_matches_on_any_shared_id(self):
		self._playback([{'id': 2, 'progress': 30.0,
						 'episode': {'ids': {'imdb': 'tt1'}}}])
		self.assertEqual(trakt.playback_progress(EPISODE), 30.0)

	def test_a_different_episode_is_not_a_match(self):
		self._playback([{'id': 2, 'progress': 30.0,
						 'episode': {'ids': {'trakt': 1000}}}])
		self.assertEqual(trakt.playback_progress(EPISODE), 0.0)

	def test_movies_use_the_movie_endpoint(self):
		self.api.route('GET', '/sync/playback/movies',
					   [{'id': 3, 'progress': 55.0,
						 'movie': {'ids': {'trakt': 7}}}])
		self.assertEqual(trakt.playback_progress(MOVIE), 55.0)

	def test_no_position_is_zero_not_an_error(self):
		self._playback([])
		self.assertEqual(trakt.playback_progress(EPISODE), 0.0)

	def test_clearing_deletes_the_right_entry(self):
		self._playback([
			{'id': 1, 'progress': 12.0, 'episode': {'ids': {'trakt': 111}}},
			{'id': 2, 'progress': 42.5, 'episode': {'ids': {'trakt': 999}}},
		])
		self.api.route('DELETE', '/sync/playback/2', None, 204)
		trakt.clear_playback(EPISODE)
		self.assertIsNotNone(self.api.sent('DELETE', '/sync/playback/2'))

	def test_clearing_what_is_not_there_does_nothing(self):
		self._playback([])
		trakt.clear_playback(EPISODE)
		self.assertEqual([c['method'] for c in self.api.calls], ['GET'])

	def test_unauthorized_never_calls_out(self):
		self.set(**{'trakt.token': ''})
		self.assertEqual(trakt.playback_progress(EPISODE), 0.0)
		self.assertEqual(self.api.calls, [])


class HiddenShows(TraktBase):
	def test_hiding_posts_the_show_id(self):
		self.api.route('POST', '/users/hidden/progress_watched',
					   {'added': {'shows': 1}})
		self.assertTrue(trakt.hide_show(111))
		call = self.api.sent('POST', '/users/hidden/progress_watched')
		self.assertEqual(call['json']['shows'][0]['ids']['trakt'], 111)

	def test_unhiding_uses_the_remove_endpoint(self):
		self.api.route('POST', '/users/hidden/progress_watched/remove',
					   {'deleted': {'shows': 1}})
		self.assertTrue(trakt.unhide_show(111))

	def test_hiding_invalidates_the_next_up_cache(self):
		from resources.lib import cache
		cache.set('trakt_next_tester', [{'show_title': 'X'}])
		self.api.route('POST', '/users/hidden/progress_watched',
					   {'added': {'shows': 1}})
		trakt.hide_show(111)
		self.assertIsNone(cache.get('trakt_next_tester'))

	def test_listing_returns_usable_entries(self):
		self.api.route('GET', '/users/hidden/progress_watched', [
			{'type': 'show', 'show': {'title': 'Severance', 'year': 2022,
									  'ids': {'trakt': 111, 'slug': 'severance'}}},
			{'type': 'show', 'show': {'title': 'No Ids', 'ids': {}}},
		])
		shows = trakt.hidden_shows()
		self.assertEqual(len(shows), 1)
		self.assertEqual(shows[0]['show_trakt'], 111)
		self.assertEqual(self.api.calls[0]['params']['type'], 'show')

	def test_a_failed_listing_is_empty_not_broken(self):
		self.api.route('GET', '/users/hidden/progress_watched', None, 500)
		self.assertEqual(trakt.hidden_shows(), [])

	def test_no_show_id_means_no_call(self):
		self.assertFalse(trakt.hide_show(None))
		self.assertEqual(self.api.calls, [])


class NextEpisodeAfter(TraktBase):
	def test_asks_trakt_for_the_shows_current_progress(self):
		self.api.route('GET', '/shows/111/progress/watched', {
			'aired': 10, 'completed': 4,
			'next_episode': {'season': 2, 'number': 4, 'title': 'Woe\'s Hollow',
							 'ids': {'trakt': 1000}, 'runtime': 45},
		})
		following = trakt.next_episode_after(EPISODE)
		self.assertEqual(following['episode'], 4)
		self.assertEqual(following['show_title'], 'Severance')
		self.assertEqual(following['show_trakt'], 111)

	def test_a_caught_up_show_returns_nothing(self):
		self.api.route('GET', '/shows/111/progress/watched',
					   {'aired': 10, 'completed': 10, 'next_episode': None})
		self.assertIsNone(trakt.next_episode_after(EPISODE))

	def test_movies_have_no_next_episode(self):
		self.assertIsNone(trakt.next_episode_after(MOVIE))
		self.assertEqual(self.api.calls, [])


class History(TraktBase):
	def test_episodes_are_posted_under_episodes(self):
		self.api.route('POST', '/sync/history', {'added': {'episodes': 1}})
		self.assertTrue(trakt.add_to_history(EPISODE))
		self.assertIn('episodes', self.api.sent('POST', '/sync/history')['json'])

	def test_movies_are_posted_under_movies(self):
		self.api.route('POST', '/sync/history', {'added': {'movies': 1}})
		self.assertTrue(trakt.add_to_history(MOVIE))
		self.assertIn('movies', self.api.sent('POST', '/sync/history')['json'])

	def test_an_entry_with_no_ids_is_refused(self):
		self.assertFalse(trakt.add_to_history({'media_type': 'episode'}))
		self.assertEqual(self.api.calls, [])


class Artwork(AddonTestCase):
	"""Trakt serves its own images now, so artwork needs no second provider."""

	SHOW = {'title': 'Severance', 'ids': {'trakt': 111}, 'images': {
		'poster': ['media.trakt.tv/images/shows/000/154/997/posters/medium/a.jpg.webp'],
		'fanart': ['media.trakt.tv/images/shows/000/154/997/fanarts/medium/b.jpg.webp'],
		'logo': ['media.trakt.tv/images/shows/000/154/997/logos/medium/c.png.webp'],
		'thumb': ['media.trakt.tv/images/shows/000/154/997/thumbs/medium/d.jpg.webp'],
	}}

	def test_a_scheme_is_added(self):
		# Trakt returns bare paths; Kodi needs a URL.
		art = trakt.show_art(self.SHOW)
		self.assertTrue(art['poster'].startswith('https://media.trakt.tv/'))

	def test_an_absolute_url_is_left_alone(self):
		art = trakt.show_art({'images': {'poster': ['https://elsewhere/x.jpg']}})
		self.assertEqual(art['poster'], 'https://elsewhere/x.jpg')

	def test_the_kodi_keys_are_filled_in(self):
		art = trakt.show_art(self.SHOW)
		self.assertEqual(set(art), {'poster', 'fanart', 'clearlogo', 'thumb'})

	def test_missing_images_are_omitted_not_blank(self):
		art = trakt.show_art({'images': {'poster': ['x/y.jpg']}})
		self.assertEqual(set(art), {'poster', 'thumb'})
		self.assertNotIn('', art.values())

	def test_a_show_with_no_images_yields_nothing(self):
		self.assertEqual(trakt.show_art({}), {})

	def test_an_episode_screenshot_becomes_the_thumb(self):
		episode = {'images': {'screenshot': ['media.trakt.tv/e/s.jpg.webp']}}
		art = trakt.episode_art(episode, self.SHOW)
		self.assertTrue(art['thumb'].endswith('/e/s.jpg.webp'))
		self.assertIn('posters', art['poster'])

	def test_an_episode_without_a_screenshot_keeps_the_shows_thumb(self):
		art = trakt.episode_art({}, self.SHOW)
		self.assertIn('thumbs', art['thumb'])

	def test_an_empty_url_list_is_not_used(self):
		self.assertEqual(trakt.show_art({'images': {'poster': [], 'fanart': ['']}}),
						 {})


class ArtworkInEntries(TraktBase):
	def test_next_episodes_carry_artwork(self):
		self.api.route('GET', '/sync/watched/shows', [
			{'show': {'title': 'Severance', 'ids': {'trakt': 111},
					  'images': {'poster': ['m/p.jpg'], 'fanart': ['m/f.jpg']}}}])
		self.api.route('GET', '/shows/111/progress/watched', {
			'aired': 10, 'completed': 9,
			'next_episode': {'season': 2, 'number': 3, 'ids': {'trakt': 999},
							 'first_aired': '2025-01-31T13:00:00.000Z',
							 'runtime': 45,
							 'images': {'screenshot': ['m/s.jpg']}}})
		entry = trakt.next_episodes(refresh=True)[0]
		self.assertEqual(entry['art']['poster'], 'https://m/p.jpg')
		self.assertEqual(entry['art']['thumb'], 'https://m/s.jpg')

	def test_the_watched_list_is_fetched_with_images(self):
		self.api.route('GET', '/sync/watched/shows', [])
		trakt.next_episodes(refresh=True)
		self.assertEqual(self.api.calls[0]['params']['extended'], 'full')


class InProgress(TraktBase):
	"""Continue Watching, from the same records that drive resume."""

	EPISODE_ITEM = {
		'progress': 42.5, 'paused_at': '2026-08-01T00:00:00.000Z',
		'episode': {'season': 2, 'number': 3, 'title': 'Who Is Alive?',
					'ids': {'trakt': 999}, 'runtime': 45,
					'images': {'screenshot': ['m/s.jpg']}},
		'show': {'title': 'Severance', 'year': 2022, 'ids': {'trakt': 111},
				 'images': {'poster': ['m/p.jpg']}},
	}
	MOVIE_ITEM = {
		'progress': 12.0, 'paused_at': '2026-08-05T00:00:00.000Z',
		'movie': {'title': 'Dune', 'year': 2021, 'ids': {'trakt': 7},
				  'runtime': 155, 'images': {'poster': ['m/d.jpg']}},
	}

	def _routes(self, episodes=(), movies=()):
		self.api.route('GET', '/sync/playback/episodes', list(episodes))
		self.api.route('GET', '/sync/playback/movies', list(movies))

	def test_episodes_and_movies_both_appear(self):
		self._routes([self.EPISODE_ITEM], [self.MOVIE_ITEM])
		entries = trakt.in_progress()
		self.assertEqual({e['media_type'] for e in entries}, {'episode', 'movie'})

	def test_most_recently_paused_first(self):
		self._routes([self.EPISODE_ITEM], [self.MOVIE_ITEM])
		self.assertEqual(trakt.in_progress()[0]['media_type'], 'movie')

	def test_the_entry_is_playable(self):
		self._routes([self.EPISODE_ITEM])
		entry = trakt.in_progress()[0]
		self.assertEqual(entry['show_title'], 'Severance')
		self.assertEqual((entry['season'], entry['episode']), (2, 3))
		self.assertEqual(entry['ep_trakt'], 999)
		self.assertEqual(entry['runtime'], 45)
		self.assertEqual(trakt._media_ref(entry)['ids'], {'trakt': 999})

	def test_progress_is_carried_through(self):
		self._routes([self.EPISODE_ITEM])
		self.assertEqual(trakt.in_progress()[0]['progress'], 42.5)

	def test_artwork_comes_along(self):
		self._routes([self.EPISODE_ITEM])
		art = trakt.in_progress()[0]['art']
		self.assertEqual(art['thumb'], 'https://m/s.jpg')
		self.assertEqual(art['poster'], 'https://m/p.jpg')

	def test_a_malformed_record_is_skipped(self):
		self._routes([{'progress': 10.0}, self.EPISODE_ITEM])
		self.assertEqual(len(trakt.in_progress()), 1)

	def test_junk_progress_does_not_crash(self):
		self._routes([dict(self.EPISODE_ITEM, progress='lots')])
		self.assertEqual(trakt.in_progress()[0]['progress'], 0.0)

	def test_a_failed_call_is_empty_not_broken(self):
		self.api.route('GET', '/sync/playback/episodes', None, 500)
		self.api.route('GET', '/sync/playback/movies', None, 500)
		self.assertEqual(trakt.in_progress(), [])

	def test_unauthorized_never_calls_out(self):
		self.set(**{'trakt.token': ''})
		self.assertEqual(trakt.in_progress(), [])
		self.assertEqual(self.api.calls, [])


class EpisodesThrough(AddonTestCase):
	"""Building the "everything up to here" history payload."""

	SEASONS = [
		{'number': 1, 'episode_count': 9, 'aired_episodes': 9},
		{'number': 2, 'episode_count': 10, 'aired_episodes': 10},
		{'number': 3, 'episode_count': 0, 'aired_episodes': 0},
	]

	def numbers(self, payload):
		return {s['number']: [e['number'] for e in s['episodes']] for s in payload}

	def test_earlier_seasons_are_covered_in_full(self):
		payload = trakt.episodes_through(self.SEASONS, 2, 3)
		self.assertEqual(self.numbers(payload)[1], list(range(1, 10)))

	def test_the_target_season_stops_at_the_episode(self):
		payload = trakt.episodes_through(self.SEASONS, 2, 3)
		self.assertEqual(self.numbers(payload)[2], [1, 2, 3])

	def test_later_seasons_are_not_touched(self):
		payload = trakt.episodes_through(self.SEASONS, 2, 3)
		self.assertNotIn(3, self.numbers(payload))

	def test_specials_are_never_included(self):
		seasons = [{'number': 0, 'aired_episodes': 5}] + self.SEASONS
		self.assertNotIn(0, self.numbers(trakt.episodes_through(seasons, 2, 3)))

	def test_unaired_episodes_of_an_earlier_season_are_excluded(self):
		seasons = [{'number': 1, 'episode_count': 10, 'aired_episodes': 4},
				   {'number': 2, 'episode_count': 8, 'aired_episodes': 8}]
		self.assertEqual(self.numbers(trakt.episodes_through(seasons, 2, 1))[1],
						 [1, 2, 3, 4])

	def test_already_watched_episodes_are_left_out(self):
		# Trakt's history is a list of plays, so re-adding one records a
		# second viewing rather than confirming the first.
		watched = {(1, 1): True, (1, 2): True, (2, 1): True}
		payload = trakt.episodes_through(self.SEASONS, 2, 3, watched)
		self.assertEqual(self.numbers(payload)[1], list(range(3, 10)))
		self.assertEqual(self.numbers(payload)[2], [2, 3])

	def test_a_fully_watched_season_disappears_entirely(self):
		watched = {(1, n): True for n in range(1, 10)}
		self.assertNotIn(1, self.numbers(trakt.episodes_through(
			self.SEASONS, 2, 3, watched)))

	def test_nothing_left_to_mark_is_an_empty_payload(self):
		watched = {(1, n): True for n in range(1, 10)}
		watched.update({(2, n): True for n in (1, 2, 3)})
		self.assertEqual(trakt.episodes_through(self.SEASONS, 2, 3, watched), [])

	def test_the_first_episode_of_the_first_season_marks_only_itself(self):
		self.assertEqual(self.numbers(trakt.episodes_through(self.SEASONS, 1, 1)),
						 {1: [1]})

	def test_junk_targets_produce_nothing(self):
		self.assertEqual(trakt.episodes_through(self.SEASONS, None, 3), [])
		self.assertEqual(trakt.episodes_through(self.SEASONS, 'x', 'y'), [])

	def test_junk_season_data_is_skipped(self):
		seasons = [{'number': 'x'}, {'number': 1, 'aired_episodes': 'lots'},
				   {'number': 2, 'aired_episodes': 4}]
		self.assertEqual(self.numbers(trakt.episodes_through(seasons, 2, 2)),
						 {2: [1, 2]})


class MarkWatchedThrough(TraktBase):
	EPISODE = dict(EPISODE, show_slug='severance', season=2, episode=3)

	def _routes(self, watched=None):
		self.api.route('GET', '/shows/111/seasons', [
			{'number': 1, 'episode_count': 9, 'aired_episodes': 9},
			{'number': 2, 'episode_count': 10, 'aired_episodes': 10}])
		self.api.route('GET', '/shows/111/progress/watched',
					   {'seasons': watched or []})
		self.api.route('POST', '/sync/history', {'added': {'episodes': 12}})

	def test_it_counts_what_would_be_added(self):
		self._routes()
		count, _seasons = trakt.count_unwatched_through(self.EPISODE)
		self.assertEqual(count, 12)  # 9 + 3

	def test_the_count_excludes_what_is_already_watched(self):
		self._routes(watched=[
			{'number': 1, 'episodes': [{'number': n, 'completed': True}
									   for n in range(1, 10)]}])
		count, _seasons = trakt.count_unwatched_through(self.EPISODE)
		self.assertEqual(count, 3)

	def test_it_posts_one_request_for_the_whole_back_catalogue(self):
		self._routes()
		self.assertEqual(trakt.mark_watched_through(self.EPISODE), 12)
		posts = [c for c in self.api.calls if c['method'] == 'POST']
		self.assertEqual(len(posts), 1)

	def test_the_payload_is_a_show_with_nested_seasons(self):
		self._routes()
		trakt.mark_watched_through(self.EPISODE)
		body = self.api.sent('POST', '/sync/history')['json']
		self.assertEqual(body['shows'][0]['ids']['trakt'], 111)
		self.assertEqual([s['number'] for s in body['shows'][0]['seasons']], [1, 2])

	def test_the_next_up_cache_is_invalidated(self):
		from resources.lib import cache
		cache.set('trakt_next_tester', [{'show_title': 'X'}])
		self._routes()
		trakt.mark_watched_through(self.EPISODE)
		self.assertIsNone(cache.get('trakt_next_tester'))

	def test_nothing_to_do_posts_nothing(self):
		self._routes(watched=[
			{'number': 1, 'episodes': [{'number': n, 'completed': True}
									   for n in range(1, 10)]},
			{'number': 2, 'episodes': [{'number': n, 'completed': True}
									   for n in (1, 2, 3)]}])
		self.assertEqual(trakt.mark_watched_through(self.EPISODE), 0)
		self.assertIsNone(self.api.sent('POST', '/sync/history'))

	def test_a_movie_is_refused(self):
		self.assertEqual(trakt.mark_watched_through(MOVIE), 0)
		self.assertEqual(self.api.calls, [])

	def test_unauthorized_never_calls_out(self):
		self.set(**{'trakt.token': ''})
		self.assertEqual(trakt.mark_watched_through(self.EPISODE), 0)
		self.assertEqual(self.api.calls, [])

	def test_a_failed_post_reports_nothing_marked(self):
		self._routes()
		self.api.route('POST', '/sync/history', None, 500)
		self.assertEqual(trakt.mark_watched_through(self.EPISODE), 0)


class WatchedState(TraktBase):
	def test_browsing_a_season_shows_what_is_watched(self):
		self.api.route('GET', '/shows/111/seasons/1/episodes', [
			{'season': 1, 'number': 1, 'title': 'One', 'ids': {'trakt': 1}},
			{'season': 1, 'number': 2, 'title': 'Two', 'ids': {'trakt': 2}}])
		self.api.route('GET', '/shows/111/progress/watched', {'seasons': [
			{'number': 1, 'episodes': [{'number': 1, 'completed': True},
									   {'number': 2, 'completed': False}]}]})
		entries = trakt.season_episodes({'show_trakt': 111}, 1)
		self.assertEqual([e['watched'] for e in entries], [True, False])

	def test_the_watched_map_covers_every_season(self):
		self.api.route('GET', '/shows/111/progress/watched', {'seasons': [
			{'number': 1, 'episodes': [{'number': 1, 'completed': True}]},
			{'number': 2, 'episodes': [{'number': 5, 'completed': True}]}]})
		self.assertEqual(trakt.watched_map(111), {(1, 1): True, (2, 5): True})

	def test_a_failed_progress_call_is_empty_not_broken(self):
		self.api.route('GET', '/shows/111/progress/watched', None, 500)
		self.assertEqual(trakt.watched_map(111), {})


class PartWatchedInNextEpisodes(TraktBase):
	"""Half-finished episodes surface on the main list, at the top.

	Asked for: "I watch half a show one night and finish it another. It
	would be nice to see on the main page how long was left and resume
	right there at the top of the list."
	"""

	def _setup(self, playback=()):
		self.api.route('GET', '/sync/watched/shows', [
			{'last_watched_at': '2020-01-01T00:00:00.000Z',
			'show': {'title': 'Alpha', 'ids': {'trakt': 1}}},
			{'last_watched_at': '2026-01-01T00:00:00.000Z',
			'show': {'title': 'Bravo', 'ids': {'trakt': 2}}}])
		for trakt_id, ep_id in ((1, 11), (2, 22)):
			self.api.route('GET', '/shows/%d/progress/watched' % trakt_id, {
				'aired': 10, 'completed': 3,
				'next_episode': {'season': 1, 'number': 1, 'title': 'One',
								'ids': {'trakt': ep_id}, 'runtime': 45,
								'first_aired': '2020-01-01T00:00:00.000Z'}})
		self.api.route('GET', '/sync/playback/episodes', list(playback))

	def _part_watched(self, ep_id, progress):
		return {'progress': progress, 'episode': {'ids': {'trakt': ep_id}}}

	def test_the_position_reaches_the_entry(self):
		self._setup([self._part_watched(11, 42.5)])
		by_show = {e['show_title']: e for e in trakt.next_episodes(refresh=True)}
		self.assertEqual(by_show['Alpha']['progress'], 42.5)
		self.assertNotIn('progress', by_show['Bravo'])

	def test_a_part_watched_episode_goes_to_the_top(self):
		# Alpha would otherwise be last: it was watched six years earlier.
		self._setup([self._part_watched(11, 42.5)])
		entries = trakt.next_episodes(refresh=True)
		self.assertEqual([e['show_title'] for e in entries], ['Alpha', 'Bravo'])

	def test_without_it_the_chosen_order_stands(self):
		self._setup()
		entries = trakt.next_episodes(refresh=True)
		self.assertEqual([e['show_title'] for e in entries], ['Bravo', 'Alpha'])

	def test_the_setting_turns_the_pinning_off(self):
		self.set(**{'list.inprogress_first': False})
		self._setup([self._part_watched(11, 42.5)])
		entries = trakt.next_episodes(refresh=True)
		self.assertEqual([e['show_title'] for e in entries], ['Bravo', 'Alpha'])

	def test_the_chosen_order_still_decides_within_each_group(self):
		self._setup([self._part_watched(11, 42.5),
					self._part_watched(22, 10.0)])
		entries = trakt.next_episodes(refresh=True)
		self.assertEqual([e['show_title'] for e in entries], ['Bravo', 'Alpha'])

	def test_the_position_is_looked_up_fresh_not_cached_with_the_list(self):
		# Where you got to changes every time you stop watching; the
		# next-up list does not.
		self._setup()
		trakt.next_episodes(refresh=True)
		self.api.route('GET', '/sync/playback/episodes',
					[self._part_watched(11, 42.5)])
		entries = trakt.next_episodes()
		self.assertEqual(entries[0]['progress'], 42.5)

	def test_finishing_it_removes_the_position(self):
		self._setup([self._part_watched(11, 42.5)])
		trakt.next_episodes(refresh=True)
		self.api.route('GET', '/sync/playback/episodes', [])
		self.assertNotIn('progress', trakt.next_episodes()[0])


class PlaybackIndex(TraktBase):
	def test_every_id_is_a_way_in(self):
		self.api.route('GET', '/sync/playback/episodes', [
			{'progress': 42.5,
			'episode': {'ids': {'trakt': 999, 'imdb': 'tt1', 'tvdb': 5}}}])
		index = trakt.playback_index()
		self.assertEqual(index[('trakt', 999)], 42.5)
		self.assertEqual(index[('imdb', 'tt1')], 42.5)
		self.assertEqual(index[('tvdb', 5)], 42.5)

	def test_an_entry_matches_on_whichever_id_it_has(self):
		self.api.route('GET', '/sync/playback/episodes', [
			{'progress': 42.5, 'episode': {'ids': {'imdb': 'tt1'}}}])
		entry = {'media_type': 'episode', 'ep_imdb': 'tt1'}
		trakt.apply_playback([entry])
		self.assertEqual(entry['progress'], 42.5)

	def test_zero_progress_is_not_part_watched(self):
		self.api.route('GET', '/sync/playback/episodes', [
			{'progress': 0, 'episode': {'ids': {'trakt': 999}}}])
		self.assertEqual(trakt.playback_index(), {})

	def test_junk_progress_is_skipped(self):
		self.api.route('GET', '/sync/playback/episodes', [
			{'progress': 'lots', 'episode': {'ids': {'trakt': 999}}}])
		self.assertEqual(trakt.playback_index(), {})

	def test_a_failed_call_is_empty_not_broken(self):
		self.api.route('GET', '/sync/playback/episodes', None, 500)
		self.assertEqual(trakt.playback_index(), {})

	def test_unauthorized_never_calls_out(self):
		self.set(**{'trakt.token': ''})
		self.assertEqual(trakt.playback_index(), {})
		self.assertEqual(self.api.calls, [])

	def test_applying_nothing_leaves_entries_alone(self):
		entries = [{'media_type': 'episode', 'ep_trakt': 999}]
		self.assertEqual(trakt.apply_playback(entries, {}), entries)


class AuthorizationThatDoesNotStick(AddonTestCase):
	"""Linking Trakt on a device that cannot save the result.

	Reported: the add-on showed "Authorize Trakt" in the menu, authorizing
	appeared to work, and the next screen asked again. Announcing success
	and then silently forgetting is the most confusing thing it can do, so
	a token that does not reach the disk is now said out loud.
	"""

	class Response(FakeResponse):
		def raise_for_status(self):
			pass

	def _authenticate(self, persists):
		posts = {'code': self.Response({'device_code': 'D', 'user_code': 'U',
										'interval': 0, 'expires_in': 60}),
				 'token': self.Response({'access_token': 'T',
										 'refresh_token': 'R',
										 'created_at': 1, 'expires_in': 99})}

		def post(url, **kwargs):
			return posts['token' if url.endswith('/token') else 'code']

		real = control.set_setting

		def write(key, value):
			real(key, value)
			return persists

		with mock.patch.object(trakt.requests, 'post', side_effect=post), \
				mock.patch.object(control, 'set_setting', side_effect=write), \
				mock.patch.object(trakt, '_fetch_username'), \
				mock.patch.object(control, 'sleep'):
			return trakt.authenticate()

	def test_a_token_that_persists_reports_success(self):
		self.assertTrue(self._authenticate(persists=True))

	def test_a_token_that_does_not_persist_is_not_reported_as_success(self):
		self.assertFalse(self._authenticate(persists=False))

	def test_the_user_is_told_why_rather_than_just_failing(self):
		self._authenticate(persists=False)
		shown = ' '.join(str(part) for entry in xbmcgui.DIALOGS for part in entry)
		self.assertIn('cannot save its settings', shown)


class OddIdsDoNotBlankTheList(AddonTestCase):
	"""Reported on screen: "Something went wrong: unhashable type: 'dict'".

	Next Episodes came up empty with a red error and nothing else. Both
	sides of the playback lookup build a tuple key straight out of Trakt's
	ids, so one non-scalar value in there raised on the dict lookup and
	took the whole list down - the episodes were fine, the decoration was
	not.
	"""

	def setUp(self):
		super(OddIdsDoNotBlankTheList, self).setUp()
		self.set(**{'trakt.token': 'token', 'list.aired_only': False})

	def test_a_nested_object_in_ids_is_skipped_not_fatal(self):
		self.assertEqual(list(trakt._id_pairs({'trakt': 5, 'imdb': 'tt1',
											   'images': {'poster': 'x'}})),
						 [('trakt', 5), ('imdb', 'tt1')])

	def test_a_list_in_ids_is_skipped_too(self):
		self.assertEqual(list(trakt._id_pairs({'trakt': 5, 'aka': ['a', 'b']})),
						 [('trakt', 5)])

	def test_booleans_are_not_ids(self):
		# True hashes, but it is not an id and it collides with 1.
		self.assertEqual(list(trakt._id_pairs({'trakt': 1, 'private': True})),
						 [('trakt', 1)])

	def test_playback_index_survives_a_nested_id(self):
		items = [{'progress': 40.0,
				  'episode': {'ids': {'trakt': 99, 'images': {'x': 'y'}}}}]
		with mock.patch.object(trakt, '_request', return_value=items):
			index = trakt.playback_index('episodes')
		self.assertEqual(index[('trakt', 99)], 40.0)

	def test_an_entry_carrying_a_nested_id_still_gets_its_position(self):
		entry = dict(EPISODE, ep_tmdb={'nested': 'object'})
		index = {('trakt', 999): 40.0}
		self.assertEqual(trakt.apply_playback([entry], index)[0]['progress'],
						 40.0)

	def test_the_list_survives_even_if_the_lookup_itself_raises(self):
		# Defence in depth: a resume position is worth far less than the
		# episodes it was decorating.
		with mock.patch.object(trakt, 'apply_playback',
							   side_effect=TypeError("unhashable type: 'dict'")):
			entries = trakt._post_filter([dict(EPISODE)])
		self.assertEqual(len(entries), 1)
		self.assertEqual(entries[0]['show_title'], 'Severance')
