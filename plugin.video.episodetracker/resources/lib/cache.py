# -*- coding: utf-8 -*-
"""A tiny sqlite-backed key/value cache with per-entry expiry.

Used to cache scraped source lists and Trakt responses so the addon does
not re-scrape or re-query on every navigation.
"""

import os
import time
import json
import hashlib
from sqlite3 import dbapi2 as database

from resources.lib import control

control.make_profile()
_cache_file = os.path.join(control.profile_path, 'cache.db')


def _connect():
	# sqlite cannot create its database inside a directory that does not
	# exist. Guarantee it here rather than relying on the profile dir having
	# been made earlier - otherwise every get/set silently no-ops and callers
	# see a permanently empty cache.
	directory = os.path.dirname(_cache_file)
	if directory and not os.path.isdir(directory):
		os.makedirs(directory, exist_ok=True)
	conn = database.connect(_cache_file, timeout=30)
	conn.execute(
		'CREATE TABLE IF NOT EXISTS cache ('
		'key TEXT PRIMARY KEY, value TEXT, expires INTEGER)'
	)
	return conn


def _hash(*parts):
	raw = '|'.join([str(p) for p in parts])
	return hashlib.md5(raw.encode('utf-8')).hexdigest()


def get(key):
	"""Return the cached value for key, or None if missing/expired."""
	try:
		conn = _connect()
		row = conn.execute(
			'SELECT value, expires FROM cache WHERE key = ?', (key,)
		).fetchone()
		conn.close()
		if not row:
			return None
		value, expires = row
		if expires and expires < int(time.time()):
			return None
		return json.loads(value)
	except Exception:
		control.error('cache.get failed')
		return None


def set(key, value, hours=6):
	"""Store value under key for the given number of hours."""
	try:
		expires = int(time.time()) + int(hours * 3600)
		conn = _connect()
		conn.execute(
			'INSERT OR REPLACE INTO cache (key, value, expires) VALUES (?, ?, ?)',
			(key, json.dumps(value), expires),
		)
		conn.commit()
		conn.close()
	except Exception:
		control.error('cache.set failed')


def cached_call(func, *args, hours=6, **kwargs):
	"""Return func(*args) using a cache keyed on function name + args."""
	key = _hash(getattr(func, '__name__', 'fn'), args, sorted(kwargs.items()))
	value = get(key)
	if value is not None:
		return value
	result = func(*args, **kwargs)
	if result:
		set(key, result, hours)
	return result


def delete(key):
	"""Remove a single cache entry."""
	try:
		conn = _connect()
		conn.execute('DELETE FROM cache WHERE key = ?', (key,))
		conn.commit()
		conn.close()
	except Exception:
		control.error('cache.delete failed')


def clear():
	"""Wipe every cache entry."""
	try:
		conn = _connect()
		conn.execute('DELETE FROM cache')
		conn.commit()
		conn.close()
		return True
	except Exception:
		control.error('cache.clear failed')
		return False
