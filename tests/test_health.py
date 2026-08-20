# -*- coding: utf-8 -*-
"""Benching a debrid provider that has stopped answering.

From a real log: the box lost DNS mid-session. Every source in a
twelve-deep queue then waited out TorBox's 30-second read timeout twice -
once for the cache check, once for the transfer - so a failed playback
took a minute per source. The log shows source 1 failing at 20:57:25 and
source 2 at 20:58:26.
"""

from unittest import mock

from support import AddonTestCase

from resources.lib import debrid
from resources.lib import health
from resources.lib import realdebrid
from resources.lib import torbox


class Benching(AddonTestCase):
	def test_one_failure_is_not_a_pattern(self):
		health.record_failure('TorBox')
		self.assertFalse(health.benched('TorBox'))

	def test_two_in_a_row_benches_it(self):
		health.record_failure('TorBox')
		health.record_failure('TorBox')
		self.assertTrue(health.benched('TorBox'))

	def test_an_answer_clears_the_count(self):
		health.record_failure('TorBox')
		health.record_success('TorBox')
		health.record_failure('TorBox')
		self.assertFalse(health.benched('TorBox'))

	def test_providers_are_benched_separately(self):
		health.record_failure('TorBox')
		health.record_failure('TorBox')
		self.assertFalse(health.benched('Real-Debrid'))

	def test_the_bench_expires(self):
		import time
		health.record_failure('TorBox')
		health.record_failure('TorBox')
		self.assertFalse(health.benched('TorBox',
										now=time.time() + health.BENCH_SECONDS + 1))

	def test_it_survives_into_the_next_plugin_process(self):
		# The source list and the playback attempt are separate processes;
		# a provider that timed out building the list should not have to
		# time out again to prove it.
		health.record_failure('TorBox')
		health.record_failure('TorBox')
		from resources.lib import cache
		self.assertIsNotNone(cache.get('health_torbox'))

	def test_clearing_puts_it_back(self):
		health.record_failure('TorBox')
		health.record_failure('TorBox')
		health.clear('TorBox')
		self.assertFalse(health.benched('TorBox'))


class TorBoxStopsWaiting(AddonTestCase):
	def setUp(self):
		super(TorBoxStopsWaiting, self).setUp()
		self.set(**{'torbox.api_key': 'key', 'torbox.enabled': True})

	def _offline(self):
		return mock.patch.object(torbox._session, 'request',
								 side_effect=OSError('read timed out'))

	def test_it_stops_calling_out_after_two_dead_requests(self):
		with self._offline() as request:
			for _attempt in range(12):
				torbox._get('/torrents/checkcached')
		self.assertEqual(request.call_count, health.FAILURES_BEFORE_BENCH)

	def test_the_timeout_is_not_half_a_minute(self):
		# A minute per source, twice per source, was the whole problem.
		connect, read = torbox._TIMEOUT
		self.assertLessEqual(connect + read, 30)

	def test_a_refusal_is_not_a_transport_failure(self):
		# A rejected key or an uncached torrent must never bench anything.
		response = mock.Mock(content=b'{"success": false}')
		response.json.return_value = {'success': False}
		with mock.patch.object(torbox._session, 'request', return_value=response):
			for _attempt in range(5):
				torbox._get('/torrents/checkcached')
		self.assertFalse(health.benched('TorBox'))

	def test_resolving_gives_up_immediately_while_benched(self):
		health.record_failure('TorBox')
		health.record_failure('TorBox')
		with mock.patch.object(torbox._session, 'request') as request:
			url, error = torbox.resolve_magnet('magnet:?x', 'abc', 1, 2)
		request.assert_not_called()
		self.assertIsNone(url)
		self.assertTrue(error)


class RealDebridStopsWaiting(AddonTestCase):
	def setUp(self):
		super(RealDebridStopsWaiting, self).setUp()
		self.set(**{'rd.token': 'token'})

	def test_it_stops_calling_out_after_two_dead_requests(self):
		with mock.patch.object(realdebrid._session, 'get',
							   side_effect=OSError('no address')) as get:
			for _attempt in range(12):
				realdebrid._get('torrents')
		self.assertEqual(get.call_count, health.FAILURES_BEFORE_BENCH)

	def test_the_timeout_is_not_three_quarters_of_a_minute(self):
		connect, read = realdebrid._TIMEOUT
		self.assertLessEqual(connect + read, 45)


class UnreachableIsNotABadKey(AddonTestCase):
	"""The reported message sent the user to regenerate a working key.

	The log's own header said "TorBox did not accept the stored API key.
	Re-enter it under Settings > Accounts" - while the traceback underneath
	was a DNS failure, and TorBox had served a stream twenty minutes
	earlier.
	"""

	def setUp(self):
		super(UnreachableIsNotABadKey, self).setUp()
		self.set(**{'torbox.api_key': 'key', 'torbox.enabled': True})

	def test_a_dead_network_does_not_blame_the_key(self):
		with mock.patch.object(torbox._session, 'request',
							   side_effect=OSError('no address')):
			ok, message = torbox.account_status()
		self.assertFalse(ok)
		self.assertIn('could not be reached', message)
		self.assertNotIn('Re-enter', message)

	def test_a_rejected_key_still_says_so(self):
		response = mock.Mock(content=b'{"success": false}')
		response.json.return_value = {'success': False, 'detail': 'bad token'}
		with mock.patch.object(torbox._session, 'request', return_value=response):
			ok, message = torbox.account_status()
		self.assertFalse(ok)
		self.assertIn('Re-enter', message)

	def test_neither_is_cached(self):
		# A working key must start working again the moment the network does.
		from resources.lib import cache
		with mock.patch.object(torbox._session, 'request',
							   side_effect=OSError('no address')):
			torbox.account_status()
		self.assertIsNone(cache.get('torbox_account_status'))


class OrderPrefersWhoeverAnswers(AddonTestCase):
	def setUp(self):
		super(OrderPrefersWhoeverAnswers, self).setUp()
		self.set(**{'rd.token': 'token', 'torbox.api_key': 'key',
					'torbox.enabled': True, 'debrid.priority': '1'})

	def test_torbox_first_is_honoured_while_it_answers(self):
		self.assertEqual(debrid.order_for(None), ['TorBox', 'Real-Debrid'])

	def test_a_benched_provider_goes_last(self):
		health.record_failure('TorBox')
		health.record_failure('TorBox')
		self.assertEqual(debrid.order_for(None), ['Real-Debrid', 'TorBox'])

	def test_even_when_it_holds_the_cached_copy(self):
		# The flag came from before it went quiet; it cannot serve now.
		health.record_failure('TorBox')
		health.record_failure('TorBox')
		self.assertEqual(debrid.order_for(['TorBox']), ['Real-Debrid', 'TorBox'])


class TheCacheCheckStaysOn(AddonTestCase):
	"""A wobble must not switch TorBox's cache check off for the session.

	Reported: "it seems to only resolve with real-debrid but it should use
	torbox also". The log showed TorBox answering "not cached" - which only
	a working key can do - yet also "no debrid provider returned usable
	cache information", so no source was ever flagged [TB+] and playback
	never had a reason to ask TorBox first.
	"""

	def setUp(self):
		super(TheCacheCheckStaysOn, self).setUp()
		self.set(**{'torbox.api_key': 'key', 'torbox.enabled': True})

	def _answers(self, payload):
		response = mock.Mock(content=b'{}')
		response.json.return_value = payload
		return mock.patch.object(torbox._session, 'request', return_value=response)

	def test_it_does_not_spend_a_request_on_the_account_first(self):
		with self._answers({'success': True, 'data': [{'hash': 'aaa'}]}) as request:
			cached, usable = torbox.cached_hashes(['AAA', 'BBB'])
		self.assertTrue(usable)
		self.assertEqual(cached, {'aaa'})
		paths = [call.args[1] for call in request.call_args_list]
		self.assertNotIn(torbox.BASE + torbox.USER, paths)

	def test_a_bad_key_is_still_reported_as_unusable(self):
		with self._answers({'success': False, 'detail': 'BAD_TOKEN'}):
			cached, usable = torbox.cached_hashes(['AAA'])
		self.assertFalse(usable)
		self.assertEqual(cached, set())

	def test_a_known_bad_account_is_honoured_without_asking(self):
		from resources.lib import cache
		cache.set('torbox_account_status', {'ok': False, 'message': 'free plan'})
		with mock.patch.object(torbox._session, 'request') as request:
			cached, usable = torbox.cached_hashes(['AAA'])
		request.assert_not_called()
		self.assertFalse(usable)

	def test_a_previous_transient_failure_does_not_disable_it(self):
		# account_status() does not cache failures, so nothing is on record
		# and the check must simply run.
		with mock.patch.object(torbox._session, 'request',
							   side_effect=OSError('no address')):
			torbox.account_status()
		health.clear('TorBox')
		with self._answers({'success': True, 'data': [{'hash': 'aaa'}]}):
			cached, usable = torbox.cached_hashes(['AAA'])
		self.assertTrue(usable)
		self.assertEqual(cached, {'aaa'})

	def test_a_cached_source_sends_playback_to_torbox_first(self):
		# This is the whole point of the flags.
		self.set(**{'rd.token': 'token', 'debrid.priority': '0'})
		self.assertEqual(debrid.order_for(['TorBox'])[0], 'TorBox')

	def test_each_provider_reports_its_own_result(self):
		self.set(**{'rd.token': 'token'})
		with mock.patch.object(torbox, 'cached_hashes',
							   return_value=({'aaa'}, True)), \
				mock.patch.object(realdebrid, 'cached_hashes',
								  return_value=(set(), False)):
			debrid.cached_hashes(['aaa', 'bbb'])
		self.assertIn('TorBox cache check: 1 of 2 cached', self.logged())
		self.assertIn('Real-Debrid cache check: no usable answer', self.logged())
