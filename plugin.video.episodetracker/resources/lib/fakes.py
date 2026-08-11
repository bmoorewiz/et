# -*- coding: utf-8 -*-
"""Remembering torrents that turned out to be fake releases.

Some torrents contain a single large file named exactly like a real
release but ending in .exe. mediafiles refuses those, so playback moves on
- but nothing stopped the same torrent being offered again on the next
scrape, or the fallback queue burning another attempt on it.

This is deliberately evidence-based rather than a heuristic: a hash gets
recorded only after a debrid provider has shown us the file list and it
turned out to be a decoy. There are no false positives to trade off, so
the entries can be trusted and kept for a long time.
"""

from resources.lib import cache
from resources.lib import control

# Long enough that a fake stays gone, short enough that a mistake ages out.
_TTL_HOURS = 24 * 60
_PREFIX = 'fake_'


def _key(info_hash):
	return _PREFIX + (info_hash or '').lower()


def remember(info_hash, filename=''):
	"""Record that this torrent is a fake release."""
	if not info_hash:
		return False
	cache.set(_key(info_hash), {'file': filename or ''}, hours=_TTL_HOURS)
	control.log('remembering fake release %s (%s)'
				% (info_hash.lower(), filename or 'unnamed'))
	return True


def is_known(info_hash):
	if not info_hash:
		return False
	return cache.get(_key(info_hash)) is not None


def drop(sources):
	"""Remove sources already proven to be fakes. Returns ``(kept, dropped)``."""
	kept, dropped = [], 0
	for item in sources:
		if is_known(item.get('hash')):
			dropped += 1
			continue
		kept.append(item)
	return kept, dropped
