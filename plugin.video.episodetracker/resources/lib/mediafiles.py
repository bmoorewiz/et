# -*- coding: utf-8 -*-
"""Picking the playable file out of a torrent's file list.

Shared by both debrid providers so they behave identically. Two things this
handles that a plain "endswith a video extension" check does not:

* Debrid services do not agree on which key holds a file's name, and some
  entries carry a full path where others carry only the basename.
* Plenty of real torrents do not name their video with an extension this
  code knows, or wrap it in an archive. Failing outright on those is worse
  than falling back to the obvious candidate - the biggest file - so an
  unrecognised container costs a guess rather than the whole playback.
"""

from resources.lib import control

VIDEO_EXTENSIONS = (
	'.mkv', '.mp4', '.avi', '.mov', '.m4v', '.mpg', '.mpeg', '.mpe', '.wmv',
	'.flv', '.ts', '.m2ts', '.mts', '.m2v', '.webm', '.ogv', '.ogm', '.iso',
	'.divx', '.xvid', '.vob', '.rmvb', '.rm', '.asf', '.3gp', '.f4v', '.mpv',
	'.dat', '.img',
)
ARCHIVE_EXTENSIONS = ('.rar', '.zip', '.7z', '.tar', '.gz', '.bz2', '.001')
# Anything smaller than this is a sample, subtitle, artwork or readme.
MIN_VIDEO_BYTES = 64 * 1024 * 1024

_NAME_KEYS = ('short_name', 'name', 'path', 'filename', 'file')


def name_of(entry):
	"""Best available name for a file entry, whatever the provider calls it."""
	if isinstance(entry, str):
		return entry
	for key in _NAME_KEYS:
		value = entry.get(key)
		if value:
			return str(value)
	return ''


def basename(entry):
	return name_of(entry).replace('\\', '/').rsplit('/', 1)[-1]


def size_of(entry):
	for key in ('bytes', 'size', 'filesize'):
		try:
			value = entry.get(key)
			if value:
				return int(value)
		except (TypeError, ValueError):
			continue
	return 0


def is_video(entry):
	return name_of(entry).lower().endswith(VIDEO_EXTENSIONS)


def is_archive(entry):
	return name_of(entry).lower().endswith(ARCHIVE_EXTENSIONS)


def describe(files, limit=12):
	"""A short listing for the log, so a rejection can be diagnosed."""
	parts = []
	for entry in files[:limit]:
		parts.append('%s (%.1f MB)' % (basename(entry) or '<unnamed>',
									   size_of(entry) / 1048576.0))
	if len(files) > limit:
		parts.append('... +%d more' % (len(files) - limit))
	return '; '.join(parts) or '<empty file list>'


def playable_candidates(files):
	"""Video files, best-guess first, plus why a fallback was needed.

	Returns ``(candidates, note)``. ``note`` is '' when the files named
	themselves properly, or an explanation when a fallback kicked in.
	"""
	if not files:
		return [], 'the torrent has no files'

	videos = [f for f in files if is_video(f)]
	if videos:
		videos.sort(key=size_of, reverse=True)
		return videos, ''

	# Nothing matched by extension. Rather than give up, take the biggest
	# file if it is plausibly a video by size alone.
	sizeable = [f for f in files if size_of(f) >= MIN_VIDEO_BYTES]
	sizeable.sort(key=size_of, reverse=True)
	if sizeable:
		if all(is_archive(f) for f in sizeable):
			return sizeable, ('the video is inside an archive (%s) - '
							  'playback will probably fail'
							  % basename(sizeable[0]))
		return sizeable, ('no file had a known video extension, falling back '
						  'to the largest (%s)' % basename(sizeable[0]))

	if any(is_archive(f) for f in files):
		return [], 'the torrent contains only archives, not a playable video'
	return [], 'the torrent contains no file big enough to be a video'


def pick(files, season=None, episode=None, matcher=None):
	"""Choose the file to play. Returns ``(entry, error)``.

	`matcher` is the season/episode filename test, injected so this module
	stays free of provider specifics.
	"""
	candidates, note = playable_candidates(files)
	if note:
		control.log('file selection: %s | files: %s' % (note, describe(files)))
	if not candidates:
		return None, 'Torrent contains no playable video file - %s' % note

	if season and episode and matcher:
		for entry in candidates:
			if matcher(season, episode, name_of(entry)):
				return entry, None
		if len(candidates) > 1:
			control.log('no S%02dE%02d match among: %s'
						% (int(season), int(episode), describe(candidates)))
			return None, ('No file matching S%02dE%02d in this torrent'
						  % (int(season), int(episode)))
	return candidates[0], None
