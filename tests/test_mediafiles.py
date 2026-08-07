# -*- coding: utf-8 -*-
"""Picking the playable file out of a torrent.

Regression home for "Torrent contains no playable video file", which
rejected perfectly playable torrents because the check knew 14 extensions
and looked at a single name field.
"""

from support import AddonTestCase

from resources.lib import mediafiles

MB = 1048576


def video(name, mb=1200, **extra):
	return dict({'path': '/%s' % name, 'bytes': mb * MB}, **extra)


class NameAndSize(AddonTestCase):
	def test_reads_whichever_key_the_provider_used(self):
		for key in ('short_name', 'name', 'path', 'filename', 'file'):
			self.assertEqual(mediafiles.name_of({key: 'Show.S01E01.mkv'}),
							 'Show.S01E01.mkv', key)

	def test_prefers_the_most_specific_name_key(self):
		entry = {'path': '/Season 1/Show.S01E01.mkv', 'short_name': 'Show.S01E01.mkv'}
		self.assertEqual(mediafiles.name_of(entry), 'Show.S01E01.mkv')

	def test_accepts_a_bare_string(self):
		self.assertEqual(mediafiles.name_of('Show.S01E01.mkv'), 'Show.S01E01.mkv')

	def test_basename_strips_both_separators(self):
		self.assertEqual(mediafiles.basename({'path': 'a/b/c.mkv'}), 'c.mkv')
		self.assertEqual(mediafiles.basename({'path': r'a\b\c.mkv'}), 'c.mkv')

	def test_size_reads_whichever_key_the_provider_used(self):
		for key in ('bytes', 'size', 'filesize'):
			self.assertEqual(mediafiles.size_of({key: 123}), 123, key)

	def test_size_survives_junk(self):
		self.assertEqual(mediafiles.size_of({'bytes': None, 'size': 'big'}), 0)

	def test_unnamed_entries_do_not_crash_the_listing(self):
		self.assertIn('<unnamed>', mediafiles.describe([{'bytes': 10}]))


class ExtensionRecognition(AddonTestCase):
	def test_accepts_the_containers_that_used_to_be_rejected(self):
		# The exact extensions that made the original 14-entry list fail.
		for extension in ('.divx', '.vob', '.mts', '.ogm', '.m2v', '.rmvb',
						  '.xvid', '.f4v', '.img'):
			self.assertTrue(mediafiles.is_video({'path': 'file' + extension}),
							extension)

	def test_is_case_insensitive(self):
		self.assertTrue(mediafiles.is_video({'path': 'FILE.MKV'}))

	def test_archives_are_not_videos(self):
		self.assertFalse(mediafiles.is_video({'path': 'release.rar'}))
		self.assertTrue(mediafiles.is_archive({'path': 'release.rar'}))


class Candidates(AddonTestCase):
	def test_biggest_video_first(self):
		files = [video('small.mkv', 700), video('big.mkv', 4000)]
		candidates, note = mediafiles.playable_candidates(files)
		self.assertEqual(mediafiles.basename(candidates[0]), 'big.mkv')
		self.assertEqual(note, '')

	def test_falls_back_to_the_largest_unrecognised_file(self):
		files = [video('readme.txt', 0), video('feature', 3000)]
		candidates, note = mediafiles.playable_candidates(files)
		self.assertEqual(mediafiles.basename(candidates[0]), 'feature')
		self.assertIn('no file had a known video extension', note)

	def test_says_so_when_everything_is_an_archive(self):
		files = [video('release.rar', 3000), video('release.r01', 3000)]
		candidates, note = mediafiles.playable_candidates(files)
		self.assertTrue(candidates)
		self.assertIn('inside an archive', note)

	def test_rejects_a_torrent_of_only_small_files(self):
		files = [video('sample.mkv.txt', 1), video('poster.jpg', 2)]
		candidates, note = mediafiles.playable_candidates(files)
		self.assertEqual(candidates, [])
		self.assertIn('big enough', note)

	def test_rejects_an_empty_file_list(self):
		candidates, note = mediafiles.playable_candidates([])
		self.assertEqual(candidates, [])
		self.assertIn('no files', note)

	def test_a_tiny_sample_never_beats_the_feature(self):
		files = [video('sample.mkv', 30), video('feature.mkv', 4000)]
		candidates, _note = mediafiles.playable_candidates(files)
		self.assertEqual(mediafiles.basename(candidates[0]), 'feature.mkv')


def matcher(season, episode, path):
	return 's%02de%02d' % (int(season), int(episode)) in path.lower()


class Pick(AddonTestCase):
	def test_picks_the_matching_episode_not_the_biggest(self):
		files = [video('Show.S01E01.mkv', 4000), video('Show.S01E02.mkv', 900)]
		entry, error = mediafiles.pick(files, 1, 2, matcher)
		self.assertIsNone(error)
		self.assertEqual(mediafiles.basename(entry), 'Show.S01E02.mkv')

	def test_reports_the_episode_it_could_not_find(self):
		files = [video('Show.S01E01.mkv'), video('Show.S01E02.mkv')]
		entry, error = mediafiles.pick(files, 1, 9, matcher)
		self.assertIsNone(entry)
		self.assertIn('S01E09', error)

	def test_single_file_torrent_is_accepted_unmatched(self):
		# A one-file torrent named by release group often has no SxxExx at
		# all; refusing it would fail sources that play perfectly.
		files = [video('some.release.name.mkv', 2000)]
		entry, error = mediafiles.pick(files, 1, 2, matcher)
		self.assertIsNone(error)
		self.assertEqual(mediafiles.basename(entry), 'some.release.name.mkv')

	def test_movies_skip_episode_matching(self):
		files = [video('Movie.2021.1080p.mkv', 8000), video('extra.mkv', 100)]
		entry, error = mediafiles.pick(files, None, None, matcher)
		self.assertIsNone(error)
		self.assertEqual(mediafiles.basename(entry), 'Movie.2021.1080p.mkv')

	def test_error_explains_why(self):
		entry, error = mediafiles.pick([], 1, 1, matcher)
		self.assertIsNone(entry)
		self.assertIn('no playable video file', error)
		self.assertIn('no files', error)
