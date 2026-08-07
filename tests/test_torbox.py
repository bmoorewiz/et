# -*- coding: utf-8 -*-
"""TorBox: cache checking and magnet -> playable link.

Regression home for "TorBox torrent contains no playable video file", and
for the cache check being the thing TorBox does that Real-Debrid no longer
does at all.
"""

from support import AddonTestCase

from resources.lib import torbox

MB = 1048576
API = '/v1/api'


class TorBoxBase(AddonTestCase):
	def setUp(self):
		super(TorBoxBase, self).setUp()
		self.set(**{'torbox.api_key': 'tb-key', 'torbox.enabled': True})
		self.http = self.use_http(torbox)
		self.clock = self.use_clock(torbox)
		self.http.json_route('GET', API + '/user/me',
							 {'success': True,
							  'data': {'plan': 2, 'email': 'a@b.c',
									   'premium_expires_at': '2030-01-01T00:00:00Z'}})


class Account(TorBoxBase):
	def test_a_working_key_is_reported_healthy(self):
		ok, message = torbox.account_status()
		self.assertTrue(ok)
		self.assertIn('plan 2', message)

	def test_the_free_plan_is_rejected(self):
		self.http.json_route('GET', API + '/user/me',
							 {'success': True, 'data': {'plan': 0}})
		ok, message = torbox.account_status()
		self.assertFalse(ok)
		self.assertIn('free plan', message)

	def test_a_transient_failure_is_never_cached(self):
		self.http.json_route('GET', API + '/user/me', {'success': False}, 500)
		ok, _message = torbox.account_status()
		self.assertFalse(ok)
		self.http.json_route('GET', API + '/user/me',
							 {'success': True, 'data': {'plan': 2}})
		ok, _message = torbox.account_status()
		self.assertTrue(ok, 'a failed check must not stick')

	def test_no_key_means_no_requests(self):
		self.set(**{'torbox.api_key': ''})
		self.assertFalse(torbox.authorized())
		self.assertIsNone(torbox._get('/anything'))
		self.assertEqual(self.http.calls, [])

	def test_enabled_needs_both_the_key_and_the_toggle(self):
		self.set(**{'torbox.enabled': False})
		self.assertTrue(torbox.authorized())
		self.assertFalse(torbox.enabled())

	def test_the_key_travels_as_a_bearer_header(self):
		torbox.account_info()
		self.assertEqual(self.http.calls[0]['headers']['Authorization'],
						 'Bearer tb-key')


class CacheCheck(TorBoxBase):
	def test_reports_which_hashes_are_cached(self):
		self.http.json_route('POST', API + '/torrents/checkcached',
							 {'success': True, 'data': [{'hash': 'AAA'}]})
		cached, usable = torbox.cached_hashes(['AAA', 'BBB'])
		self.assertTrue(usable)
		self.assertEqual(cached, {'aaa'})

	def test_a_failed_check_is_not_a_no(self):
		# Treating "we could not ask" as "not cached" would hide every
		# source when TorBox has a wobble.
		self.http.json_route('POST', API + '/torrents/checkcached',
							 {'success': False})
		cached, usable = torbox.cached_hashes(['AAA'])
		self.assertFalse(usable)
		self.assertEqual(cached, set())

	def test_answers_are_cached_between_calls(self):
		self.http.json_route('POST', API + '/torrents/checkcached',
							 {'success': True, 'data': [{'hash': 'AAA'}]})
		torbox.cached_hashes(['AAA', 'BBB'])
		before = len(self.http.paths('POST'))
		cached, usable = torbox.cached_hashes(['AAA', 'BBB'])
		self.assertEqual(len(self.http.paths('POST')), before,
						 'should not have asked twice')
		self.assertTrue(usable)
		self.assertEqual(cached, {'aaa'})

	def test_a_plain_string_list_is_understood(self):
		# TorBox has answered with both shapes.
		self.http.json_route('POST', API + '/torrents/checkcached',
							 {'success': True, 'data': ['AAA']})
		cached, _usable = torbox.cached_hashes(['AAA'])
		self.assertEqual(cached, {'aaa'})

	def test_single_hash_check_distinguishes_no_from_unknown(self):
		self.http.json_route('GET', API + '/torrents/checkcached',
							 {'success': True, 'data': []})
		self.assertIs(torbox.is_cached('AAA'), False)
		self.http.json_route('GET', API + '/torrents/checkcached',
							 {'success': False})
		self.assertIsNone(torbox.is_cached('AAA'))


class Resolve(TorBoxBase):
	def _transfer(self, file_list, state='completed', torrent_id=7):
		self.http.json_route('GET', API + '/torrents/checkcached',
							 {'success': True, 'data': [{'hash': 'abc'}]})
		self.http.json_route('POST', API + '/torrents/createtorrent',
							 {'success': True, 'data': {'torrent_id': torrent_id}})
		self.http.json_route('GET', API + '/torrents/mylist',
							 {'success': True,
							  'data': {'id': torrent_id, 'files': file_list,
									   'download_present': True,
									   'download_state': state}})
		self.deleted = []
		self.http.route('POST', API + '/torrents/controltorrent',
						lambda r: self.deleted.append(r['json']) or {'success': True})
		self.requested = []
		self.http.route('GET', API + '/torrents/requestdl',
						lambda r: self.requested.append(r['params']) or
						{'success': True, 'data': 'https://tb/direct/file.mkv'})

	def test_resolves_the_matching_episode(self):
		self._transfer([
			{'id': 0, 'name': 'Show.S01E01.mkv', 'size': 2000 * MB},
			{'id': 1, 'name': 'Show.S01E02.mkv', 'size': 1500 * MB},
		])
		url, error = torbox.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(error)
		self.assertEqual(url, 'https://tb/direct/file.mkv')
		self.assertEqual(self.requested[0]['file_id'], 1)

	def test_accepts_the_containers_that_used_to_be_rejected(self):
		# The bug report was a perfectly playable torrent turned away.
		self._transfer([{'id': 0, 'name': 'Show.S01E02.divx', 'size': 2000 * MB}])
		url, error = torbox.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(error)
		self.assertTrue(url)

	def test_accepts_a_provider_that_names_files_differently(self):
		self._transfer([{'id': 0, 'short_name': 'Show.S01E02.mkv',
						 'size': 2000 * MB}])
		url, error = torbox.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(error)
		self.assertTrue(url)

	def test_an_uncached_source_is_refused_before_a_transfer_exists(self):
		self.http.json_route('GET', API + '/torrents/checkcached',
							 {'success': True, 'data': []})
		url, error = torbox.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(url)
		self.assertIn('Not cached on TorBox', error)
		self.assertFalse(self.http.called('POST', API + '/torrents/createtorrent'),
						 'should not have created a transfer')

	def test_uncached_sources_are_allowed_when_the_setting_is_off(self):
		self.set(**{'torbox.cached_only': False})
		self._transfer([{'id': 0, 'name': 'Show.S01E02.mkv', 'size': 2000 * MB}])
		self.http.json_route('GET', API + '/torrents/checkcached',
							 {'success': True, 'data': []})
		url, error = torbox.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(error)
		self.assertTrue(url)

	def test_a_stalled_transfer_fails_fast_and_is_cleaned_up(self):
		self._transfer([], state='stalled (no seeds)')
		self.http.json_route('GET', API + '/torrents/mylist',
							 {'success': True,
							  'data': {'id': 7, 'files': [],
									   'download_state': 'stalled (no seeds)'}})
		url, error = torbox.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(url)
		self.assertIn('stalled', error)
		self.assertTrue(self.deleted)

	def test_the_transfer_is_deleted_after_a_successful_resolve(self):
		self._transfer([{'id': 0, 'name': 'Show.S01E02.mkv', 'size': 2000 * MB}])
		torbox.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertEqual(self.deleted[0]['operation'], 'delete')

	def test_keep_cloud_leaves_the_transfer_alone(self):
		self.set(**{'torbox.keep_cloud': True})
		self._transfer([{'id': 0, 'name': 'Show.S01E02.mkv', 'size': 2000 * MB}])
		torbox.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertEqual(self.deleted, [])

	def test_a_rejected_magnet_reports_torboxs_own_wording(self):
		self.http.json_route('GET', API + '/torrents/checkcached',
							 {'success': True, 'data': [{'hash': 'abc'}]})
		self.http.json_route('POST', API + '/torrents/createtorrent',
							 {'success': False, 'detail': 'active limit reached'})
		url, error = torbox.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(url)
		self.assertIn('active limit reached', error)

	def test_movies_take_the_biggest_file(self):
		self._transfer([
			{'id': 0, 'name': 'featurette.mkv', 'size': 200 * MB},
			{'id': 1, 'name': 'Dune.2021.2160p.mkv', 'size': 30000 * MB},
		])
		url, error = torbox.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc')
		self.assertIsNone(error)
		self.assertEqual(self.requested[0]['file_id'], 1)


class ErrorText(AddonTestCase):
	def test_no_response_is_said_plainly(self):
		self.assertIn('No response', torbox.error_text(None))

	def test_success_is_not_an_error(self):
		self.assertEqual(torbox.error_text({'success': True}), '')

	def test_detail_is_preferred_over_error(self):
		self.assertIn('specific reason',
					  torbox.error_text({'success': False,
										 'detail': 'specific reason',
										 'error': 'generic'}))
