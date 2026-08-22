# -*- coding: utf-8 -*-
"""A copy of the account credentials, kept outside Kodi's settings file.

Kodi stores add-on settings in one file that it rewrites whole. Anything
that disturbs that file - a schema that fails to load, a profile that runs
out of space mid-write, a setting id that disappears from the schema -
takes every credential with it at once, and the add-on then presents as
signed out of everything with no way back except re-entering all of them.
That has now happened on a real install.

So the credentials are also written here, in the add-on's own file, and
put back whenever the settings copy is empty and this one is not. The
TorBox API key in particular has to survive an update untouched.

Two rules make this safe rather than annoying:

  - Signing out must stick. revoke() calls forget(), so a key the user
    deliberately cleared is not resurrected on the next launch.
  - The settings copy always wins when it has a value. This file is a
    fallback, never an override, so editing a key in the settings dialog
    behaves exactly as it looks.

No new exposure: these values are already stored in plain text in Kodi's
own settings file, in this same directory.
"""

import json
import os

from resources.lib import control

# Everything that costs the user something to re-enter.
CREDENTIALS = (
	'torbox.api_key',
	'trakt.token', 'trakt.refresh', 'trakt.expires', 'trakt.user',
	'rd.token', 'rd.refresh', 'rd.expires', 'rd.user',
	'rd.client_id', 'rd.client_secret',
)

# Settings that are not credentials but are meaningless without one, keyed
# by the credential they belong to. Restoring the TorBox key while leaving
# the service switched off would look like the key had not come back.
#
# They are handled separately because they have a declared default, so they
# always read as set: treating them as credentials in their own right would
# back up "torbox.enabled = false" on an install that has never had a key,
# and then count restoring that default as having recovered something.
COMPANIONS = {'torbox.api_key': ('torbox.enabled',)}

FILENAME = 'credentials.json'


def _path():
	return os.path.join(control.profile_path, FILENAME)


def _load():
	try:
		with open(_path(), encoding='utf-8') as handle:
			data = json.load(handle)
		return data if isinstance(data, dict) else {}
	except Exception:
		return {}


def _save(data):
	try:
		control.make_profile()
		with open(_path(), 'w', encoding='utf-8') as handle:
			json.dump(data, handle)
		return True
	except Exception:
		control.error('could not write the credential backup')
		return False


def values():
	"""The backed-up values, for redaction. Never for display."""
	return [value for value in _load().values() if value]


def sync():
	"""Reconcile the settings copy with this one. Returns keys restored.

	One pass does both directions, because which copy is authoritative is
	per key: a credential that is set gets backed up, a credential that is
	missing gets put back. Nothing is overwritten in either direction.
	"""
	stored = _load()
	restored, changed = [], False

	for key in CREDENTIALS:
		live = control.setting(key, '')
		companions = COMPANIONS.get(key, ())
		if live:
			if stored.get(key) != live:
				stored[key] = live
				changed = True
			# Only worth recording alongside a credential that exists.
			for companion in companions:
				value = control.setting(companion, '')
				if value and stored.get(companion) != value:
					stored[companion] = value
					changed = True
		elif stored.get(key):
			if not control.set_setting(key, stored[key]):
				# The profile cannot be written, so putting the value back
				# will not hold either. Say it once rather than per key.
				control.log('could not restore %s: settings are not saving'
							% key)
				break
			restored.append(key)
			for companion in companions:
				if stored.get(companion):
					control.set_setting(companion, stored[companion])

	if changed:
		_save(stored)
	if restored:
		control.log('restored from backup: %s' % ', '.join(restored))
	return restored


def forget(keys):
	"""Drop these from the backup, so a deliberate sign-out is not undone."""
	stored = _load()
	if not any(key in stored for key in keys):
		return
	for key in keys:
		stored.pop(key, None)
	_save(stored)
