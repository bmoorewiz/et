# -*- coding: utf-8 -*-
"""CocoScrapers integration: the data handed to providers, and ranking.

Regression home for "nothing is found when it scrapes". Trakt's JSON gives
year/tvdb/tmdb as integers; CocoScrapers' source_utils.check_title() does
``title.replace(year, '')``, which throws on an int, so every candidate
release was discarded and it looked exactly like "no sources".
"""

import sys
import types

import xbmcaddon

from support import AddonTestCase

from resources.lib import scrapers

EPISODE = {
	'media_type': 'episode',
	'show_title': 'Severance', 'show_year': 2022,
	'show_imdb': 'tt11280740', 'show_tvdb': 371980, 'show_tmdb': 95396,
	'season': 2, 'episode': 3, 'ep_title': 'Who Is Alive?',
	'first_aired': '2025-01-31T13:00:00.000Z',
}
MOVIE = {
	'media_type': 'movie', 'title': 'Dune', 'year': 2021,
	'imdb': 'tt1160419', 'tmdb': 438631, 'released': '2021-10-22',
}


def source(quality='1080p', seeders=10, size=5.0, name='release', **extra):
	return dict({'quality': quality, 'seeders': seeders, 'size': size,
				 'name': name, 'hash': '%s%s%s' % (quality, seeders, size)},
				**extra)


class ScraperData(AddonTestCase):
	def test_every_episode_value_is_a_string(self):
		data = scrapers._build_data(EPISODE)
		for key, value in data.items():
			if key in ('aliases',):
				continue
			self.assertIsInstance(value, str, '%s came out as %r' % (key, value))

	def test_every_movie_value_is_a_string(self):
		data = scrapers._build_data(MOVIE)
		for key, value in data.items():
			if key in ('aliases',):
				continue
			self.assertIsInstance(value, str, '%s came out as %r' % (key, value))

	def test_the_year_survives_a_string_replace(self):
		# This is the exact operation CocoScrapers performs on the title.
		data = scrapers._build_data(EPISODE)
		'Severance 2022'.replace(data['year'], '')  # must not raise

	def test_episode_data_carries_what_providers_key_off(self):
		data = scrapers._build_data(EPISODE)
		self.assertEqual(data['tvshowtitle'], 'Severance')
		self.assertEqual(data['title'], 'Who Is Alive?')
		self.assertEqual(data['season'], '2')
		self.assertEqual(data['episode'], '3')
		self.assertEqual(data['year'], '2022')
		self.assertEqual(data['premiered'], '2025-01-31')

	def test_movie_data_has_no_tvshowtitle(self):
		# Its mere presence is what flips a provider into episode mode.
		self.assertNotIn('tvshowtitle', scrapers._build_data(MOVIE))

	def test_missing_values_become_empty_strings_not_none(self):
		data = scrapers._build_data({'media_type': 'episode'})
		self.assertEqual(data['tvshowtitle'], '')
		self.assertEqual(data['year'], '')
		self.assertNotIn(None, data.values())

	def test_debrid_hint_is_added_when_a_provider_is_set_up(self):
		self.set(**{'rd.token': 'rdtoken'})
		data = scrapers._build_data(EPISODE)
		self.assertEqual(data['debrid_service'], 'Real-Debrid')
		self.assertEqual(data['debrid_token'], 'rdtoken')

	def test_debrid_hint_follows_the_configured_priority(self):
		self.set(**{'rd.token': 'rdtoken', 'torbox.api_key': 'tbkey',
					'torbox.enabled': True, 'debrid.priority': '1'})
		data = scrapers._build_data(EPISODE)
		self.assertEqual(data['debrid_service'], 'TorBox')
		self.assertEqual(data['debrid_token'], 'tbkey')

	def test_no_debrid_hint_when_nothing_is_set_up(self):
		self.assertNotIn('debrid_service', scrapers._build_data(EPISODE))


class ModuleDetection(AddonTestCase):
	def tearDown(self):
		sys.modules.pop('cocoscrapers', None)

	def test_absent_module_is_reported_not_raised(self):
		self.assertFalse(scrapers.available())
		self.assertIs(scrapers.scrape(EPISODE), scrapers.MODULE_MISSING)

	def test_present_module_is_found(self):
		xbmcaddon.install(scrapers.COCO_ID)
		sys.modules['cocoscrapers'] = types.ModuleType('cocoscrapers')
		self.assertTrue(scrapers.available())

	def test_no_enabled_providers_is_its_own_answer(self):
		# CocoScrapers only returns providers whose setting is on, so an
		# empty list means "none enabled", not "scraped and found nothing".
		xbmcaddon.install(scrapers.COCO_ID)
		module = types.ModuleType('cocoscrapers')
		module.sources = lambda: []
		sys.modules['cocoscrapers'] = module
		self.assertEqual(scrapers.scrape(EPISODE), scrapers.NO_PROVIDERS)


class Scrape(AddonTestCase):
	def tearDown(self):
		sys.modules.pop('cocoscrapers', None)

	def _install(self, provider_results, record=None):
		xbmcaddon.install(scrapers.COCO_ID)
		module = types.ModuleType('cocoscrapers')

		def make(name, results):
			class Source(object):
				hasEpisodes = True
				hasMovies = True

				def sources(self, data, host_dict):
					if record is not None:
						record.append(data)
					if isinstance(results, Exception):
						raise results
					return list(results)
			Source.__name__ = name
			return Source

		module.sources = lambda: [(name, make(name, results))
								  for name, results in provider_results.items()]
		sys.modules['cocoscrapers'] = module

	def test_results_are_tagged_with_their_provider(self):
		self._install({'alpha': [source(name='a')]})
		found = scrapers.scrape(EPISODE)
		self.assertEqual([s['provider'] for s in found], ['alpha'])

	def test_a_provider_that_names_itself_keeps_its_own_name(self):
		self._install({'alpha': [source(name='a', provider='TorrentSite')]})
		found = scrapers.scrape(EPISODE)
		self.assertEqual([s['provider'] for s in found], ['TorrentSite'])

	def test_a_raising_provider_does_not_lose_the_others(self):
		self._install({'good': [source(name='a')],
					   'bad': RuntimeError('provider exploded')})
		self.assertEqual(len(scrapers.scrape(EPISODE)), 1)

	def test_duplicate_hashes_are_collapsed(self):
		same = source(name='a', hash='ABC')
		self._install({'alpha': [dict(same)], 'beta': [dict(same)]})
		self.assertEqual(len(scrapers.scrape(EPISODE)), 1)

	def test_providers_receive_the_stringified_data(self):
		seen = []
		self._install({'alpha': [source()]}, record=seen)
		scrapers.scrape(EPISODE)
		self.assertTrue(seen)
		self.assertEqual(seen[0]['year'], '2022')

	def test_an_empty_scrape_is_not_cached(self):
		# Caching nothing would hide sources for the whole cache window
		# after one bad scrape and look like a permanent failure.
		self._install({'alpha': []})
		scrapers.scrape(EPISODE)
		self.assertEqual(scrapers.cached_sources(EPISODE), [])
		self._install({'alpha': [source(name='later')]})
		self.assertEqual(len(scrapers.scrape(EPISODE)), 1)

	def test_a_real_scrape_is_cached(self):
		self._install({'alpha': [source(name='a')]})
		scrapers.scrape(EPISODE)
		self._install({'alpha': [source(name='b'), source(name='c', hash='z')]})
		self.assertEqual(len(scrapers.scrape(EPISODE)), 1, 'should have come from cache')

	def test_movies_and_episodes_use_different_cache_keys(self):
		self.assertNotEqual(scrapers._cache_key(EPISODE), scrapers._cache_key(MOVIE))

	def test_episode_cache_key_is_per_episode(self):
		other = dict(EPISODE, episode=4)
		self.assertNotEqual(scrapers._cache_key(EPISODE), scrapers._cache_key(other))


class FilterAndRank(AddonTestCase):
	def test_orders_by_quality_then_seeders(self):
		items = [source('720p', 100), source('4K', 1), source('1080p', 50)]
		ranked = scrapers._filter_and_rank(items)
		self.assertEqual([s['quality'] for s in ranked], ['4K', '1080p', '720p'])

	def test_disabled_qualities_are_dropped(self):
		self.set(**{'quality.4k': False})
		items = [source('4K'), source('1080p')]
		self.assertEqual([s['quality'] for s in scrapers._filter_and_rank(items)],
						 ['1080p'])

	def test_sd_toggle_also_covers_cam_and_screener(self):
		self.set(**{'quality.sd': False})
		items = [source('SD'), source('CAM'), source('SCR'), source('720p')]
		self.assertEqual([s['quality'] for s in scrapers._filter_and_rank(items)],
						 ['720p'])

	def test_non_torrent_sources_are_dropped(self):
		items = [{'quality': '1080p', 'url': 'https://host/file.mkv'},
				 source('1080p')]
		self.assertEqual(len(scrapers._filter_and_rank(items)), 1)

	def test_magnet_without_a_hash_is_kept(self):
		items = [{'quality': '1080p', 'url': 'magnet:?xt=urn:btih:abc'}]
		self.assertEqual(len(scrapers._filter_and_rank(items)), 1)

	def test_minimum_seeders_is_applied(self):
		self.set(**{'filter.min_seeders': '20'})
		items = [source('1080p', 5), source('1080p', 50, size=6.0)]
		self.assertEqual(len(scrapers._filter_and_rank(items)), 1)

	def test_unknown_seeder_counts_are_not_filtered_out(self):
		# A provider that reports no seeder count should not be punished.
		self.set(**{'filter.min_seeders': '20'})
		items = [source('1080p', seeders=0)]
		self.assertEqual(len(scrapers._filter_and_rank(items)), 1)

	def test_result_limit_is_honoured(self):
		self.set(**{'results.limit': '3'})
		items = [source('1080p', seeders=n, size=n) for n in range(10)]
		self.assertEqual(len(scrapers._filter_and_rank(items)), 3)

	def test_junk_seeder_values_do_not_crash_ranking(self):
		items = [source('1080p', seeders='lots'), source('1080p', seeders=None,
														 size=None)]
		scrapers._filter_and_rank(items)  # must not raise


class AnnotateCached(AddonTestCase):
	def test_unusable_answers_leave_every_source_unflagged(self):
		# Real-Debrid's cache endpoint is deprecated and answers with
		# nothing useful; flagging everything "uncached" would be a lie.
		self.set(**{'sources.check_cache': True})
		items = [source('1080p')]
		result, known = scrapers.annotate_cached(items)
		self.assertFalse(known)
		self.assertNotIn('cached_by', result[0])

	def test_cached_sources_sort_first(self):
		from unittest import mock
		items = [source('1080p', name='uncached', hash='AAA'),
				 source('1080p', name='cached', hash='BBB')]
		with mock.patch('resources.lib.debrid.cached_hashes',
						return_value=({'bbb': ['TorBox']}, True)):
			result, known = scrapers.annotate_cached(items)
		self.assertTrue(known)
		self.assertEqual(result[0]['name'], 'cached')
		self.assertEqual(result[0]['cached_by'], ['TorBox'])

	def test_only_cached_setting_drops_the_rest(self):
		from unittest import mock
		self.set(**{'sources.only_cached': True})
		items = [source('1080p', hash='AAA'), source('1080p', hash='BBB')]
		with mock.patch('resources.lib.debrid.cached_hashes',
						return_value=({'bbb': ['TorBox']}, True)):
			result, _known = scrapers.annotate_cached(items)
		self.assertEqual(len(result), 1)

	def test_check_can_be_turned_off(self):
		self.set(**{'sources.check_cache': False})
		items = [source('1080p')]
		result, known = scrapers.annotate_cached(items)
		self.assertFalse(known)
		self.assertEqual(result, items)


class SeasonPacks(AddonTestCase):
	"""A season pack is often the only cached source an older show has.

	Verified against the real CocoScrapers module: 9 of 9 enabled providers
	advertise pack_capable, and a Severance S01E03 scrape returned 78
	sources of which 42 were packs.
	"""

	def tearDown(self):
		sys.modules.pop('cocoscrapers', None)

	def _install(self, singles=(), packs=(), pack_capable=True, raises=False):
		xbmcaddon.install(scrapers.COCO_ID)
		module = types.ModuleType('cocoscrapers')
		seen = {'pack_data': None}

		class Source(object):
			hasEpisodes = True
			hasMovies = True

			def sources(self, data, host_dict):
				return [dict(s) for s in singles]

			def sources_packs(self, data, host_dict, search_series=False,
							  total_seasons=None, bypass_filter=False):
				seen['pack_data'] = data
				if raises:
					raise RuntimeError('pack search exploded')
				return [dict(p) for p in packs]

		if pack_capable:
			Source.pack_capable = True
		module.sources = lambda: [('alpha', Source)]
		sys.modules['cocoscrapers'] = module
		return seen

	def test_packs_are_searched_alongside_single_episodes(self):
		self._install(singles=[source(name='single')],
					  packs=[source(name='pack', hash='P1', package='season')])
		found = scrapers.scrape(EPISODE)
		self.assertEqual(sorted(s['name'] for s in found), ['pack', 'single'])

	def test_packs_are_tagged_as_packs(self):
		self._install(packs=[source(name='pack', hash='P1', package='season')])
		found = scrapers.scrape(EPISODE)
		self.assertTrue(scrapers.is_packed(found[0]))

	def test_a_pack_with_no_package_flag_still_gets_one(self):
		self._install(packs=[source(name='pack', hash='P1')])
		self.assertTrue(scrapers.is_packed(scrapers.scrape(EPISODE)[0]))

	def test_providers_that_cannot_do_packs_are_not_asked(self):
		seen = self._install(singles=[source(name='single')], pack_capable=False)
		scrapers.scrape(EPISODE)
		self.assertIsNone(seen['pack_data'])

	def test_movies_never_search_for_packs(self):
		seen = self._install(singles=[source(name='single')])
		scrapers.scrape(MOVIE)
		self.assertIsNone(seen['pack_data'])

	def test_the_setting_turns_pack_search_off(self):
		self.set(**{'sources.packs': False})
		seen = self._install(singles=[source(name='single')])
		scrapers.scrape(EPISODE)
		self.assertIsNone(seen['pack_data'])

	def test_a_failing_pack_search_does_not_lose_the_singles(self):
		self._install(singles=[source(name='single')], raises=True)
		self.assertEqual([s['name'] for s in scrapers.scrape(EPISODE)], ['single'])

	def test_packs_receive_a_string_season(self):
		# eztv and friends do data['season'].zfill(2), which throws on an int.
		seen = self._install(packs=[source(name='pack', hash='P1')])
		scrapers.scrape(EPISODE)
		self.assertIsInstance(seen['pack_data']['season'], str)
		self.assertEqual(seen['pack_data']['season'].zfill(2), '02')

	def test_a_partial_pack_that_misses_the_episode_is_dropped(self):
		self._install(packs=[
			source(name='early', hash='P1', episode_start=1, episode_end=2),
			source(name='covering', hash='P2', episode_start=1, episode_end=6)])
		found = scrapers.scrape(EPISODE)  # episode 3
		self.assertEqual([s['name'] for s in found], ['covering'])

	def test_a_whole_season_pack_is_always_kept(self):
		self._install(packs=[source(name='whole', hash='P1')])
		self.assertEqual(len(scrapers.scrape(EPISODE)), 1)

	def test_pack_range_checks_survive_junk(self):
		self.assertTrue(scrapers._pack_covers({'episode_start': 'x',
											   'episode_end': 'y'}, 3))
		self.assertTrue(scrapers._pack_covers({}, 3))


class PlausibleSize(AddonTestCase):
	"""A 173-minute film claiming 4K in 1.17 GB is 0.4 GB/hour."""

	MOVIE = {'media_type': 'movie', 'title': 'The Odyssey', 'runtime': 173}
	EPISODE = {'media_type': 'episode', 'runtime': 45}

	def test_the_reported_fake_is_dropped(self):
		fake = source('4K', size=1.17, name='The-Odyssey-.2026.-2160p-FULL-HD')
		self.assertEqual(scrapers._filter_and_rank([fake], self.MOVIE), [])

	def test_the_real_cam_survives(self):
		cam = source('CAM', size=5.56, name='The.Odyssey.2026.HDTS')
		self.assertEqual(len(scrapers._filter_and_rank([cam], self.MOVIE)), 1)

	def test_a_small_but_real_x265_episode_survives(self):
		# 300 MB for a 45-minute 1080p episode is a normal HEVC encode.
		small = source('1080p', size=0.30, name='Show.S01E01.1080p.x265')
		self.assertEqual(len(scrapers._filter_and_rank([small], self.EPISODE)), 1)

	def test_season_packs_are_exempt(self):
		# A pack's size covers a whole run, not the episode being watched.
		pack = source('4K', size=16.82, name='Severance.S01.2160p', package='season')
		self.assertEqual(len(scrapers._filter_and_rank([pack], self.EPISODE)), 1)

	def test_a_source_with_no_size_is_not_judged(self):
		unknown = source('4K', size=0, name='no size reported')
		self.assertEqual(len(scrapers._filter_and_rank([unknown], self.MOVIE)), 1)

	def test_nothing_is_judged_without_a_runtime(self):
		fake = source('4K', size=1.17)
		self.assertEqual(len(scrapers._filter_and_rank([fake], {'runtime': None})), 1)

	def test_sd_and_cam_have_no_floor(self):
		for quality in ('SD', 'CAM', 'SCR'):
			tiny = source(quality, size=0.05, name='tiny')
			self.assertEqual(len(scrapers._filter_and_rank([tiny], self.MOVIE)), 1,
							 quality)

	def test_the_setting_turns_it_off(self):
		self.set(**{'sources.plausible_size': False})
		fake = source('4K', size=1.17)
		self.assertEqual(len(scrapers._filter_and_rank([fake], self.MOVIE)), 1)


class KnownFakes(AddonTestCase):
	def test_a_remembered_hash_never_appears_again(self):
		from resources.lib import fakes
		fakes.remember('ABC', 'Movie.2026.2160p.exe')
		items = [source('1080p', hash='ABC'), source('1080p', hash='DEF')]
		kept = scrapers._filter_and_rank(items, {'runtime': 120})
		self.assertEqual([s['hash'] for s in kept], ['DEF'])

	def test_the_match_is_case_insensitive(self):
		from resources.lib import fakes
		fakes.remember('abcdef')
		self.assertTrue(fakes.is_known('ABCDEF'))

	def test_an_unknown_hash_is_untouched(self):
		from resources.lib import fakes
		self.assertFalse(fakes.is_known('NOTHINGHERE'))
		self.assertFalse(fakes.is_known(''))
		self.assertFalse(fakes.is_known(None))

	def test_the_file_list_is_what_records_it(self):
		# Recorded only from a debrid provider's actual file list, so there
		# is no heuristic and no false positive to trade off.
		from resources.lib import fakes, mediafiles
		mediafiles.pick([{'path': '/Movie.2026.2160p.exe', 'bytes': 1200 * 1048576}],
						None, None, None, info_hash='FEED')
		self.assertTrue(fakes.is_known('feed'))

	def test_a_genuine_torrent_is_not_recorded(self):
		from resources.lib import fakes, mediafiles
		mediafiles.pick([{'path': '/Movie.2026.2160p.mkv', 'bytes': 8000 * 1048576}],
						None, None, None, info_hash='GOOD')
		self.assertFalse(fakes.is_known('GOOD'))

	def test_a_missing_episode_is_not_a_fake(self):
		# No file matched S01E09, but the torrent is a real video torrent.
		from resources.lib import fakes, mediafiles
		mediafiles.pick([{'path': '/Show.S01E01.mkv', 'bytes': 2000 * 1048576}],
						1, 9, lambda s, e, p: False, info_hash='REAL')
		self.assertFalse(fakes.is_known('REAL'))


class Forget(AddonTestCase):
	def tearDown(self):
		sys.modules.pop('cocoscrapers', None)

	def test_forgetting_forces_a_re_scrape(self):
		from resources.lib import cache
		cache.set(scrapers._cache_key(EPISODE), [source(name='old')])
		self.assertEqual(len(scrapers.cached_sources(EPISODE)), 1)
		scrapers.forget(EPISODE)
		self.assertEqual(scrapers.cached_sources(EPISODE), [])

	def test_forgetting_one_episode_leaves_the_others(self):
		from resources.lib import cache
		other = dict(EPISODE, episode=9)
		cache.set(scrapers._cache_key(EPISODE), [source()])
		cache.set(scrapers._cache_key(other), [source()])
		scrapers.forget(EPISODE)
		self.assertEqual(len(scrapers.cached_sources(other)), 1)
