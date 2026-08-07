# -*- coding: utf-8 -*-
"""Real-Debrid: magnet -> playable link.

Regression home for "could not resolve a playable link on all sources",
which came from reading a freshly-added torrent's status once instead of
polling it, and for the failure message that discarded Real-Debrid's own
error and guessed instead.
"""

from support import AddonTestCase, FakeResponse

from resources.lib import realdebrid

MB = 1048576
REST = '/rest/1.0/'


def files(*names_and_sizes):
	return [{'id': index + 1, 'path': '/%s' % name, 'bytes': mb * MB,
			 'selected': 1}
			for index, (name, mb) in enumerate(names_and_sizes)]


class Transfer(object):
	"""Scripts one torrent through a sequence of Real-Debrid statuses."""

	def __init__(self, statuses, file_list=None, links=None, torrent_id='T1'):
		self.statuses = list(statuses)
		self.files = file_list or files(('Show.S01E02.1080p.mkv', 2000))
		self.links = links if links is not None else ['https://rd/link1']
		self.torrent_id = torrent_id
		self.deleted = False
		self.selected = False

	def info(self, _request):
		status = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
		payload = {'id': self.torrent_id, 'status': status,
				   'files': self.files, 'progress': 12, 'seeders': 3}
		if status == 'downloaded':
			payload['links'] = self.links
		return payload

	def install(self, http, add_response=None):
		http.route('POST', REST + 'torrents/addMagnet',
				   lambda r: add_response if add_response is not None
				   else {'id': self.torrent_id})
		http.route('GET', REST + 'torrents/info/%s' % self.torrent_id, self.info)
		http.route('POST', REST + 'torrents/selectFiles/%s' % self.torrent_id,
				   lambda r: self._select(r))
		http.route('DELETE', REST + 'torrents/delete/%s' % self.torrent_id,
				   lambda r: self._delete(r))
		http.route('GET', REST + 'torrents/activeCount', lambda r: {'nb': 0, 'list': []})
		http.route('POST', REST + 'unrestrict/link',
				   lambda r: {'download': 'https://rd/direct/file.mkv'})
		return http

	def _select(self, _request):
		self.selected = True
		return FakeResponse(status_code=204)

	def _delete(self, _request):
		self.deleted = True
		return FakeResponse(status_code=204)


class ResolveBase(AddonTestCase):
	def setUp(self):
		super(ResolveBase, self).setUp()
		self.set(**{'rd.token': 'testtoken'})
		self.http = self.use_http(realdebrid)
		self.clock = self.use_clock(realdebrid)


class Polling(ResolveBase):
	def test_polls_until_the_torrent_is_downloaded(self):
		# A freshly added magnet is never immediately 'downloaded'; reading
		# the status once is what made every source fail.
		transfer = Transfer(['magnet_conversion', 'waiting_files_selection',
							 'queued', 'downloaded'])
		transfer.install(self.http)
		url, error = realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(error)
		self.assertEqual(url, 'https://rd/direct/file.mkv')
		self.assertTrue(transfer.selected, 'files were never selected')

	def test_selects_files_when_asked_to(self):
		transfer = Transfer(['waiting_files_selection', 'downloaded'])
		transfer.install(self.http)
		realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertTrue(self.http.called('POST', REST + 'torrents/selectFiles/T1'))

	def test_gives_up_on_an_uncached_torrent_after_the_grace_period(self):
		# Downloading means Real-Debrid does not have it; waiting out the
		# full timeout on a source we know is not instant wastes the user's
		# time when the next source might be cached.
		self.set(**{'rd.uncached_grace': '4', 'rd.resolve_timeout': '60'})
		transfer = Transfer(['downloading'])
		transfer.install(self.http)
		url, error = realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(url)
		self.assertIn('Not cached on Real-Debrid', error)
		self.assertLess(self.clock.now - 1700000000.0, 20,
						'should not have burned the whole timeout')

	def test_a_slow_but_cached_torrent_is_not_abandoned(self):
		# queued -> downloaded within the grace period is the normal path
		# for a cached torrent and must not be mistaken for uncached.
		self.set(**{'rd.uncached_grace': '4'})
		transfer = Transfer(['queued', 'queued', 'downloaded'])
		transfer.install(self.http)
		url, error = realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(error)
		self.assertTrue(url)

	def test_timeout_while_still_converting_says_so(self):
		self.set(**{'rd.resolve_timeout': '5'})
		transfer = Transfer(['magnet_conversion'])
		transfer.install(self.http)
		url, error = realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(url)
		self.assertIn('still converting', error)


class TerminalStates(ResolveBase):
	def test_each_terminal_state_is_explained(self):
		expected = {
			'magnet_error': 'could not read the magnet',
			'error': 'transfer error',
			'virus': 'virus',
			'dead': 'no seeders',
		}
		for status, fragment in expected.items():
			transfer = Transfer([status], torrent_id='T-%s' % status)
			transfer.install(self.http)
			url, error = realdebrid.resolve_magnet(
				'magnet:?xt=urn:btih:abc', 'abc', 1, 2)
			self.assertIsNone(url, status)
			self.assertIn(fragment, error, status)
			self.assertTrue(transfer.deleted,
							'%s should not leave a transfer behind' % status)


class ErrorReporting(ResolveBase):
	def test_real_debrids_own_error_is_reported(self):
		# The old message guessed "check your account is active" whatever
		# Real-Debrid actually said.
		self.assertIn('Traffic exhausted', realdebrid.error_text({'error_code': 23}))
		self.assertIn('allowance is used up', realdebrid.error_text({'error_code': 23}))
		self.assertIn('(Real-Debrid error 23)', realdebrid.error_text({'error_code': 23}))

	def test_every_documented_code_has_wording(self):
		for code in realdebrid.RD_ERRORS:
			self.assertTrue(realdebrid.error_text({'error_code': code}), code)

	def test_an_unknown_code_still_produces_something_useful(self):
		text = realdebrid.error_text({'error_code': 999, 'error': 'weird_thing'})
		self.assertIn('weird_thing', text)
		self.assertIn('999', text)

	def test_a_clean_payload_is_not_an_error(self):
		self.assertEqual(realdebrid.error_text({'id': 'T1'}), '')

	def test_rejected_magnet_carries_the_reason_through(self):
		transfer = Transfer(['downloaded'])
		transfer.install(self.http, add_response={'error_code': 35,
												  'error': 'infringing_file'})
		url, error = realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(url)
		self.assertIn('Infringing file', error)


class Recovery(ResolveBase):
	def test_a_torrent_already_in_the_account_is_reused(self):
		transfer = Transfer(['downloaded'], torrent_id='EXISTING')
		transfer.install(self.http, add_response={'error_code': 33})
		self.http.route('GET', REST + 'torrents',
						lambda r: [{'id': 'EXISTING', 'hash': 'ABC'}])
		url, error = realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(error)
		self.assertTrue(url)

	def test_too_many_transfers_prunes_and_retries_once(self):
		transfer = Transfer(['downloaded'])
		attempts = []

		def add(_request):
			attempts.append(1)
			return {'error_code': 21} if len(attempts) == 1 else {'id': 'T1'}

		transfer.install(self.http)
		self.http.route('POST', REST + 'torrents/addMagnet', add)
		# activeCount's list holds the *hashes* of the blocking transfers.
		self.http.route('GET', REST + 'torrents/activeCount',
						lambda r: {'nb': 3, 'limit': 3, 'list': ['ZZZ']})
		self.http.route('GET', REST + 'torrents',
						lambda r: [{'id': 'OLD', 'hash': 'ZZZ',
									'status': 'downloading'}])
		self.http.route('DELETE', REST + 'torrents/delete/OLD',
						lambda r: FakeResponse(status_code=204))
		url, error = realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(error)
		self.assertTrue(url)
		self.assertEqual(len(attempts), 2, 'should have retried exactly once')


class FileSelection(ResolveBase):
	def test_the_link_index_follows_the_selected_files(self):
		# links[] lines up with the *selected* files in order, so picking
		# the second selected file must take the second link.
		transfer = Transfer(
			['downloaded'],
			file_list=[
				{'id': 1, 'path': '/Show.S01E01.mkv', 'bytes': 2000 * MB, 'selected': 1},
				{'id': 2, 'path': '/Show.S01E02.mkv', 'bytes': 1000 * MB, 'selected': 1},
			],
			links=['https://rd/one', 'https://rd/two'])
		transfer.install(self.http)
		captured = {}
		self.http.route('POST', REST + 'unrestrict/link',
						lambda r: captured.update(r['data'] or {}) or
						{'download': 'https://rd/direct/file.mkv'})
		url, error = realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(error)
		self.assertEqual(captured.get('link'), 'https://rd/two')

	def test_unselected_files_are_not_counted(self):
		transfer = Transfer(
			['downloaded'],
			file_list=[
				{'id': 1, 'path': '/extras.mkv', 'bytes': 90 * MB, 'selected': 0},
				{'id': 2, 'path': '/Show.S01E02.mkv', 'bytes': 2000 * MB, 'selected': 1},
			],
			links=['https://rd/only'])
		transfer.install(self.http)
		captured = {}
		self.http.route('POST', REST + 'unrestrict/link',
						lambda r: captured.update(r['data'] or {}) or
						{'download': 'https://rd/direct/file.mkv'})
		url, error = realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(error)
		self.assertEqual(captured.get('link'), 'https://rd/only')

	def test_a_rar_result_is_refused_rather_than_played(self):
		transfer = Transfer(['downloaded'])
		transfer.install(self.http)
		self.http.route('POST', REST + 'unrestrict/link',
						lambda r: {'download': 'https://rd/direct/release.rar'})
		url, error = realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertIsNone(url)
		self.assertIn('.rar', error)


class Housekeeping(ResolveBase):
	def test_the_transfer_is_deleted_after_a_successful_resolve(self):
		transfer = Transfer(['downloaded'])
		transfer.install(self.http)
		realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertTrue(transfer.deleted)

	def test_keep_cloud_leaves_the_transfer_alone(self):
		self.set(**{'rd.keep_cloud': True})
		transfer = Transfer(['downloaded'])
		transfer.install(self.http)
		realdebrid.resolve_magnet('magnet:?xt=urn:btih:abc', 'abc', 1, 2)
		self.assertFalse(transfer.deleted)

	def test_no_token_means_no_requests_at_all(self):
		self.set(**{'rd.token': ''})
		self.assertFalse(realdebrid.authorized())
		self.assertIsNone(realdebrid._get('torrents'))
		self.assertEqual(self.http.calls, [])


class EpisodeMatching(AddonTestCase):
	def test_matches_the_common_naming_schemes(self):
		for name in ('Show.S01E02.1080p.mkv', 'Show 1x02 1080p.mkv',
					 'Show.s1e2.mkv', 'Show Season 1 Episode 2.mkv'):
			self.assertTrue(realdebrid._episode_match(1, 2, '/dir/' + name), name)

	def test_does_not_match_a_different_episode(self):
		self.assertFalse(realdebrid._episode_match(1, 2, '/Show.S01E03.mkv'))

	def test_only_looks_at_the_filename(self):
		# A season directory named S01E02 must not make every file inside
		# it look like episode 2.
		self.assertFalse(realdebrid._episode_match(1, 2, '/S01E02/Show.S01E05.mkv'))


class BadToken(AddonTestCase):
	def test_recognises_both_shapes_of_bad_token(self):
		self.assertTrue(realdebrid._is_bad_token({'error_code': 8}))
		self.assertTrue(realdebrid._is_bad_token({'error': 'bad_token'}))
		self.assertFalse(realdebrid._is_bad_token({'error_code': 23}))
		self.assertFalse(realdebrid._is_bad_token(None))
