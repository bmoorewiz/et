# -*- coding: utf-8 -*-
"""Trakt: next-up episodes, scrobbles, playback position and hidden shows."""

from unittest import mock

from support import AddonTestCase, FakeResponse

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
		self._setup()
		trakt.next_episodes(refresh=True)
		before = len(self.api.calls)
		trakt.next_episodes()
		self.assertEqual(len(self.api.calls), before)

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
