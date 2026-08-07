# -*- coding: utf-8 -*-
"""The sqlite cache, the debrid dispatcher, and the updater's version maths."""

import os
import shutil
from unittest import mock

import kodistubs

from support import AddonTestCase

from resources.lib import cache
from resources.lib import debrid
from resources.lib import updater


class Cache(AddonTestCase):
	def test_round_trips_a_value(self):
		cache.set('key', {'a': [1, 2, 3]})
		self.assertEqual(cache.get('key'), {'a': [1, 2, 3]})

	def test_a_missing_key_is_none(self):
		self.assertIsNone(cache.get('never-set'))

	def test_an_expired_entry_is_gone(self):
		cache.set('key', 'value', hours=-1)
		self.assertIsNone(cache.get('key'))

	def test_a_fractional_window_works(self):
		# The empty next-up result is cached for 0.25 hours.
		cache.set('key', 'value', hours=0.25)
		self.assertEqual(cache.get('key'), 'value')

	def test_writing_twice_replaces(self):
		cache.set('key', 'first')
		cache.set('key', 'second')
		self.assertEqual(cache.get('key'), 'second')

	def test_delete_removes_one_entry(self):
		cache.set('a', 1)
		cache.set('b', 2)
		cache.delete('a')
		self.assertIsNone(cache.get('a'))
		self.assertEqual(cache.get('b'), 2)

	def test_clear_empties_everything(self):
		cache.set('a', 1)
		self.assertTrue(cache.clear())
		self.assertIsNone(cache.get('a'))

	def test_a_missing_profile_directory_is_created(self):
		# Without this every get and set silently no-ops and the caller
		# sees a permanently empty cache.
		shutil.rmtree(os.path.dirname(cache._cache_file), ignore_errors=True)
		cache.set('key', 'value')
		self.assertEqual(cache.get('key'), 'value')
		self.assertTrue(os.path.isdir(os.path.dirname(cache._cache_file)))

	def test_a_false_value_is_still_a_hit(self):
		cache.set('key', False)
		self.assertIs(cache.get('key'), False)

	def test_cached_call_only_runs_once(self):
		calls = []

		def work(argument):
			calls.append(argument)
			return {'result': argument}

		self.assertEqual(cache.cached_call(work, 'x'), {'result': 'x'})
		self.assertEqual(cache.cached_call(work, 'x'), {'result': 'x'})
		self.assertEqual(len(calls), 1)

	def test_cached_call_keys_on_its_arguments(self):
		calls = []
		work = lambda argument: calls.append(argument) or {'r': argument}
		cache.cached_call(work, 'x')
		cache.cached_call(work, 'y')
		self.assertEqual(len(calls), 2)


class DebridDispatcher(AddonTestCase):
	def test_no_provider_is_reported_clearly(self):
		self.assertEqual(debrid.providers(), [])
		self.assertFalse(debrid.any_authorized())
		url, error, provider = debrid.resolve_magnet('magnet:?x', 'abc')
		self.assertIsNone(url)
		self.assertIsNone(provider)
		self.assertIn('No debrid provider', error)

	def test_a_stored_real_debrid_token_is_the_switch(self):
		self.set(**{'rd.token': 'token'})
		self.assertEqual(debrid.providers(), ['Real-Debrid'])

	def test_torbox_needs_its_toggle_as_well_as_a_key(self):
		self.set(**{'torbox.api_key': 'key'})
		self.assertEqual(debrid.providers(), [])
		self.set(**{'torbox.enabled': True})
		self.assertEqual(debrid.providers(), ['TorBox'])

	def test_priority_reverses_the_order(self):
		self.set(**{'rd.token': 'token', 'torbox.api_key': 'key',
					'torbox.enabled': True})
		self.assertEqual(debrid.providers(), ['Real-Debrid', 'TorBox'])
		self.set(**{'debrid.priority': '1'})
		self.assertEqual(debrid.providers(), ['TorBox', 'Real-Debrid'])

	def test_the_serving_provider_is_carried_back(self):
		self.set(**{'rd.token': 'token'})
		with mock.patch('resources.lib.realdebrid.resolve_magnet',
						return_value=('https://rd/file', None)):
			url, error, provider = debrid.resolve_magnet('magnet:?x', 'abc')
		self.assertEqual(provider, 'Real-Debrid')
		self.assertIsNone(error)

	def test_it_falls_through_to_the_second_provider(self):
		self.set(**{'rd.token': 'token', 'torbox.api_key': 'key',
					'torbox.enabled': True})
		with mock.patch('resources.lib.realdebrid.resolve_magnet',
						return_value=(None, 'not cached')), \
				mock.patch('resources.lib.torbox.resolve_magnet',
						   return_value=('https://tb/file', None)):
			url, error, provider = debrid.resolve_magnet('magnet:?x', 'abc')
		self.assertEqual(provider, 'TorBox')
		self.assertEqual(url, 'https://tb/file')

	def test_both_failures_are_reported_together(self):
		self.set(**{'rd.token': 'token', 'torbox.api_key': 'key',
					'torbox.enabled': True})
		with mock.patch('resources.lib.realdebrid.resolve_magnet',
						return_value=(None, 'rd said no')), \
				mock.patch('resources.lib.torbox.resolve_magnet',
						   return_value=(None, 'tb said no')):
			url, error, _provider = debrid.resolve_magnet('magnet:?x', 'abc')
		self.assertIsNone(url)
		self.assertIn('rd said no', error)
		self.assertIn('tb said no', error)

	def test_a_raising_provider_does_not_stop_the_other(self):
		self.set(**{'rd.token': 'token', 'torbox.api_key': 'key',
					'torbox.enabled': True})
		with mock.patch('resources.lib.realdebrid.resolve_magnet',
						side_effect=RuntimeError('boom')), \
				mock.patch('resources.lib.torbox.resolve_magnet',
						   return_value=('https://tb/file', None)):
			url, _error, provider = debrid.resolve_magnet('magnet:?x', 'abc')
		self.assertEqual(provider, 'TorBox')

	def test_cache_answers_are_merged_across_providers(self):
		self.set(**{'rd.token': 'token', 'torbox.api_key': 'key',
					'torbox.enabled': True})
		with mock.patch('resources.lib.realdebrid.cached_hashes',
						return_value=({'aaa'}, True)), \
				mock.patch('resources.lib.torbox.cached_hashes',
						   return_value=({'aaa', 'bbb'}, True)):
			mapping, usable = debrid.cached_hashes(['aaa', 'bbb'])
		self.assertTrue(usable)
		self.assertEqual(mapping['aaa'], ['Real-Debrid', 'TorBox'])
		self.assertEqual(mapping['bbb'], ['TorBox'])

	def test_every_provider_has_a_short_tag(self):
		for name in debrid._MODULES:
			self.assertIn(name, debrid.TAGS)


class CachedProviderGoesFirst(AddonTestCase):
	"""A source flagged [TB+] must not be resolved through Real-Debrid.

	From a real report: the source list said TorBox had it cached, but
	resolution walked the fixed priority order, so playback started and
	announced "Playing via Real-Debrid" - contradicting the flag the user
	had just chosen from.
	"""

	def setUp(self):
		super(CachedProviderGoesFirst, self).setUp()
		self.set(**{'rd.token': 'token', 'torbox.api_key': 'key',
					'torbox.enabled': True})

	def test_the_holder_is_asked_first(self):
		self.assertEqual(debrid.order_for(['TorBox']),
						 ['TorBox', 'Real-Debrid'])

	def test_the_other_provider_still_gets_a_turn(self):
		self.assertIn('Real-Debrid', debrid.order_for(['TorBox']))

	def test_no_cache_information_leaves_the_configured_order(self):
		self.assertEqual(debrid.order_for(None), ['Real-Debrid', 'TorBox'])
		self.assertEqual(debrid.order_for([]), ['Real-Debrid', 'TorBox'])

	def test_a_holder_that_is_not_enabled_is_ignored(self):
		self.set(**{'torbox.enabled': False})
		self.assertEqual(debrid.order_for(['TorBox']), ['Real-Debrid'])

	def test_both_holders_keep_the_configured_order_between_them(self):
		self.assertEqual(debrid.order_for(['TorBox', 'Real-Debrid']),
						 ['Real-Debrid', 'TorBox'])

	def test_the_named_provider_is_the_one_that_served_it(self):
		with mock.patch('resources.lib.torbox.resolve_magnet',
						return_value=('https://tb/file', None)), \
				mock.patch('resources.lib.realdebrid.resolve_magnet') as rd:
			url, error, provider = debrid.resolve_magnet(
				'magnet:?x', 'abc', cached_by=['TorBox'])
		self.assertEqual(provider, 'TorBox')
		self.assertEqual(url, 'https://tb/file')
		rd.assert_not_called()

	def test_the_player_passes_the_sources_cache_flags_through(self):
		from resources.lib import player
		source = {'hash': 'ABC', 'url': 'magnet:?xt=urn:btih:ABC',
				  'cached_by': ['TorBox']}
		entry = {'media_type': 'movie', 'title': 'The Odyssey'}
		with mock.patch('resources.lib.debrid.resolve_magnet',
						return_value=('u', None, 'TorBox')) as resolve:
			player._resolve_one(source, entry)
		self.assertEqual(resolve.call_args.kwargs['cached_by'], ['TorBox'])


class Versions(AddonTestCase):
	def test_parses_a_three_part_version(self):
		self.assertEqual(updater.parse_version('1.7.0'), (1, 7, 0))

	def test_a_newer_remote_is_an_update(self):
		self.assertTrue(updater.compare_versions('1.7.1', '1.7.0'))
		# 1.10 must beat 1.9, which a string comparison would get wrong.
		self.assertTrue(updater.compare_versions('1.10.0', '1.9.9'))
		self.assertTrue(updater.compare_versions('2.0.0', '1.99.99'))

	def test_the_same_version_is_not_an_update(self):
		self.assertFalse(updater.compare_versions('1.7.0', '1.7.0'))

	def test_an_older_remote_is_not_an_update(self):
		self.assertFalse(updater.compare_versions('1.6.0', '1.7.0'))

	def test_a_shorter_version_is_padded_not_mis_ranked(self):
		self.assertFalse(updater.compare_versions('1.7', '1.7.0'))
		self.assertTrue(updater.compare_versions('1.8', '1.7.9'))

	def test_the_installed_version_matches_the_manifest(self):
		import re
		xml = open(os.path.join(kodistubs.ADDON_ROOT, 'addon.xml'),
				   encoding='utf-8').read()
		declared = re.search(r'version="([\d.]+)" provider-name', xml).group(1)
		self.assertEqual(updater.installed_version(), declared)

	def test_junk_versions_do_not_crash_the_comparison(self):
		updater.compare_versions('not.a.version', '1.7.0')
		updater.compare_versions('', '')
