# -*- coding: utf-8 -*-
"""Minimal xbmcaddon replacement.

CocoScrapers reads and writes its configuration through xbmcaddon.Addon,
above all `provider.<name>` flags that decide which scrapers run. Settings
are persisted to a JSON file so a device keeps its provider choices between
launches, exactly as Kodi's settings.xml would.
"""

import json
import os
import threading

_lock = threading.Lock()
_state = {
	'data_dir': os.path.join(os.path.expanduser('~'), '.episodetracker'),
	'settings': {},
	'loaded': False,
}


def configure(data_dir):
	"""Point the shim at a writable directory (the app's filesDir on Android)."""
	with _lock:
		_state['data_dir'] = data_dir
		_state['loaded'] = False
		_state['settings'] = {}
	os.makedirs(data_dir, exist_ok=True)
	_load()


def _settings_path():
	return os.path.join(_state['data_dir'], 'settings.json')


def _load():
	with _lock:
		if _state['loaded']:
			return
		try:
			with open(_settings_path(), 'r', encoding='utf-8') as handle:
				_state['settings'] = json.load(handle)
		except Exception:
			_state['settings'] = {}
		_state['loaded'] = True


def _save():
	try:
		os.makedirs(_state['data_dir'], exist_ok=True)
		tmp = _settings_path() + '.tmp'
		with open(tmp, 'w', encoding='utf-8') as handle:
			json.dump(_state['settings'], handle)
		os.replace(tmp, _settings_path())
	except Exception:
		pass


def all_settings():
	_load()
	return dict(_state['settings'])


class Addon(object):
	def __init__(self, id=None):
		self._id = id or 'script.module.cocoscrapers'
		_load()

	def getAddonInfo(self, key):
		base = os.path.join(_state['data_dir'], self._id)
		try:
			os.makedirs(base, exist_ok=True)
		except Exception:
			pass
		return {
			'id': self._id,
			'name': self._id,
			'version': '1.0.0',
			'path': base,
			'profile': base,
			'icon': '',
			'fanart': '',
			'author': '',
		}.get(key, '')

	def getLocalizedString(self, string_id):
		return ''

	def getSetting(self, key):
		_load()
		return str(_state['settings'].get(key, ''))

	def getSettingBool(self, key):
		return self.getSetting(key) == 'true'

	def getSettingInt(self, key):
		try:
			return int(float(self.getSetting(key) or 0))
		except ValueError:
			return 0

	def getSettingString(self, key):
		return self.getSetting(key)

	def setSetting(self, key, value):
		with _lock:
			_state['settings'][key] = '' if value is None else str(value)
		_save()

	setSettingBool = setSetting
	setSettingInt = setSetting
	setSettingString = setSetting

	def openSettings(self):
		return None
