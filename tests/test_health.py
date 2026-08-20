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


class ExpiredProviderStepsAside(AddonTestCase):
	"""A lapsed subscription hands over to the other service.

	Asked for: "my real-debrid account expires in 2 days, it needs to be
	able to check that somehow and switch to torbox automatically since it
	is already setup."
	"""

	def setUp(self):
		super(ExpiredProviderStepsAside, self).setUp()
		self.set(**{'rd.token': 'token', 'torbox.api_key': 'key',
					'torbox.enabled': True})

	def _accounts(self, rd, tb):
		return (mock.patch.object(realdebrid, 'account_status', return_value=rd),
				mock.patch.object(torbox, 'account_status', return_value=tb))

	def _resolve(self, rd, tb):
		rd_patch, tb_patch = self._accounts(rd, tb)
		asked = []
		with rd_patch, tb_patch, \
				mock.patch.object(realdebrid, 'resolve_magnet',
								  side_effect=lambda *a, **k:
								  asked.append('Real-Debrid') or (None, 'no')), \
				mock.patch.object(torbox, 'resolve_magnet',
								  side_effect=lambda *a, **k:
								  asked.append('TorBox') or ('https://tb/f', None)):
			result = debrid.resolve_magnet('magnet:?x', 'abc')
		return asked, result

	OK_RD = (True, 'premium, 30 days left')
	DEAD_RD = (False, 'Real-Debrid account "x" has no active premium time.')
	OK_TB = (True, 'plan 2, expires 2030-01-01')

	def test_an_expired_account_is_not_even_tried(self):
		asked, (url, _error, provider) = self._resolve(self.DEAD_RD, self.OK_TB)
		self.assertEqual(asked, ['TorBox'])
		self.assertEqual(provider, 'TorBox')
		self.assertEqual(url, 'https://tb/f')

	def test_a_working_account_is_still_used(self):
		asked, _result = self._resolve(self.OK_RD, self.OK_TB)
		self.assertIn('Real-Debrid', asked)

	def test_the_handover_is_logged(self):
		self._resolve(self.DEAD_RD, self.OK_TB)
		self.assertIn('skipping Real-Debrid', self.logged())

	def test_the_only_provider_is_still_tried_even_when_unusable(self):
		# Its real error is far more use than silence.
		self.set(**{'torbox.enabled': False})
		with mock.patch.object(realdebrid, 'account_status',
							   return_value=self.DEAD_RD), \
				mock.patch.object(realdebrid, 'resolve_magnet',
								  return_value=(None, 'no premium')) as resolve:
			url, error, _provider = debrid.resolve_magnet('magnet:?x', 'abc')
		resolve.assert_called_once()
		self.assertIn('no premium', error)

	def test_an_unknown_account_state_is_not_treated_as_expired(self):
		with mock.patch.object(realdebrid, 'account_status',
							   side_effect=RuntimeError('offline')):
			self.assertTrue(debrid.serviceable('Real-Debrid'))

	def test_a_benched_provider_is_also_unserviceable(self):
		health.record_failure('TorBox')
		health.record_failure('TorBox')
		# And answered from the bench alone: asking the account check would
		# reach the same conclusion, but only after doing the work.
		with mock.patch.object(torbox, 'account_status') as status:
			self.assertFalse(debrid.serviceable('TorBox'))
		status.assert_not_called()


class ExpiryWarning(AddonTestCase):
	def setUp(self):
		super(ExpiryWarning, self).setUp()
		self.set(**{'rd.token': 'token'})

	def _days(self, days):
		from resources.lib import cache
		cache.set('rd_account_status',
				  {'ok': True, 'message': 'premium', 'days': days})

	def test_it_reads_the_days_without_asking_again(self):
		self._days(2)
		with mock.patch.object(realdebrid._session, 'get') as get:
			self.assertEqual(realdebrid.days_left(), 2)
		get.assert_not_called()

	def test_a_subscription_about_to_lapse_is_reported(self):
		self._days(2)
		self.assertEqual(debrid.expiring_soon(), [('Real-Debrid', 2)])

	def test_a_healthy_subscription_is_not(self):
		self._days(30)
		self.assertEqual(debrid.expiring_soon(), [])

	def test_an_already_expired_one_is_not_warned_about(self):
		# It is not a warning any more; it is handled by stepping aside.
		self._days(0)
		self.assertEqual(debrid.expiring_soon(), [])

	def test_nothing_known_says_nothing(self):
		self.assertIsNone(realdebrid.days_left())
		self.assertEqual(debrid.expiring_soon(), [])

	def test_torbox_dates_are_understood(self):
		# Half a day past the boundary, so the whole-days floor is not a
		# race against how long the test takes to run.
		import time as _time
		soon = _time.strftime('%Y-%m-%dT%H:%M:%S.000Z',
							  _time.gmtime(_time.time() + 2.5 * 86400))
		self.assertEqual(torbox._days_until(soon), 2)
		past = _time.strftime('%Y-%m-%dT%H:%M:%S.000Z',
							  _time.gmtime(_time.time() - 86400))
		self.assertEqual(torbox._days_until(past), 0)
		self.assertIsNone(torbox._days_until(''))
		self.assertIsNone(torbox._days_until('not a date'))

	def test_the_user_is_told_once_a_day_not_once_a_click(self):
		from resources.lib import router
		self._days(2)
		with mock.patch('resources.lib.debrid.account_status',
						return_value=(True, 'fine')), \
				mock.patch('resources.lib.scrapers.available', return_value=True), \
				mock.patch('resources.lib.trakt.authorized', return_value=True):
			router._preflight()
			first = len(self.dialogs('notification'))
			router._preflight()
			second = len(self.dialogs('notification'))
		self.assertEqual(first, 1)
		self.assertEqual(second, 1)
		self.assertIn('2', self.last_dialog('notification')[2])
