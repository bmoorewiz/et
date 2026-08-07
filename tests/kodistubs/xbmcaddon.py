# -*- coding: utf-8 -*-
"""Stub for Kodi's ``xbmcaddon`` module."""

import os
import re

import kodistubs

_ADDON_ID = 'plugin.video.episodetracker'

# addon id -> {setting key: value as stored}. Values are always strings, the
# way Kodi's settings file stores them.
_STORE = {}
# addon id -> {setting key: default from settings.xml}. Kodi hands back the
# declared default for a setting the user has never touched, so a test that
# does not set 'updates.repo' must still read 'bmoorewiz/et'.
_DEFAULTS = {}
_STRINGS = {}
# Addon ids that "exist". Anything else raises, which is exactly how
# scrapers._load() discovers CocoScrapers is not installed.
_INSTALLED = set()
OPENED_SETTINGS = []


def _addon_xml_info():
	path = os.path.join(kodistubs.ADDON_ROOT, 'addon.xml')
	text = open(path, encoding='utf-8').read()
	head = re.search(r'<addon\b[^>]*>', text).group(0)

	def attr(name):
		found = re.search(r'\b%s="([^"]*)"' % name, head)
		return found.group(1) if found else ''

	return {
		'id': attr('id'),
		'name': attr('name'),
		'version': attr('version'),
		'author': attr('provider-name'),
		'path': kodistubs.ADDON_ROOT,
		'icon': os.path.join(kodistubs.ADDON_ROOT, 'resources/icon.png'),
		'fanart': os.path.join(kodistubs.ADDON_ROOT, 'resources/fanart.png'),
	}


def _load_defaults():
	path = os.path.join(kodistubs.ADDON_ROOT, 'resources/settings.xml')
	text = open(path, encoding='utf-8').read()
	defaults = {}
	for element in re.findall(r'<setting\b[^>]*/?>', text):
		key = re.search(r'\bid="([^"]*)"', element)
		if not key:
			continue
		value = re.search(r'\bdefault="([^"]*)"', element)
		defaults[key.group(1)] = value.group(1) if value else ''
	return defaults


def _load_strings():
	path = os.path.join(kodistubs.ADDON_ROOT,
						'resources/language/resource.language.en_gb/strings.po')
	text = open(path, encoding='utf-8').read()
	strings = {}
	for match in re.finditer(
			r'msgctxt\s+"#(\d+)"\s*\nmsgid\s+"((?:[^"\\]|\\.)*)"\s*\n'
			r'msgstr\s+"((?:[^"\\]|\\.)*)"', text):
		number = int(match.group(1))
		# An empty msgstr means "use the msgid", which is how every en_gb
		# catalogue in Kodi is written. Getting this wrong would hand tests
		# empty strings and hide missing format placeholders.
		raw = match.group(3) or match.group(2)
		strings[number] = raw.replace('\\"', '"').replace('\\n', '\n')
	return strings


_INFO = {_ADDON_ID: _addon_xml_info()}


def reset():
	_STORE.clear()
	_DEFAULTS.clear()
	_DEFAULTS[_ADDON_ID] = _load_defaults()
	_STRINGS.clear()
	_STRINGS.update(_load_strings())
	_INSTALLED.clear()
	_INSTALLED.add(_ADDON_ID)
	del OPENED_SETTINGS[:]
	_INFO[_ADDON_ID] = _addon_xml_info()
	_INFO[_ADDON_ID]['profile'] = kodistubs.PROFILE


def install(addon_id, info=None):
	"""Pretend another add-on (CocoScrapers) is installed."""
	_INSTALLED.add(addon_id)
	_INFO[addon_id] = dict({'id': addon_id, 'name': addon_id, 'version': '1.0.0',
							'path': os.path.join(kodistubs.PROFILE, addon_id),
							'profile': os.path.join(kodistubs.PROFILE, addon_id),
							'icon': '', 'fanart': '', 'author': ''}, **(info or {}))


class Addon(object):
	def __init__(self, id=None):
		self.id = id or _ADDON_ID
		if self.id not in _INSTALLED:
			# Kodi raises for an add-on that is not installed.
			raise RuntimeError('Unknown addon id "%s"' % self.id)

	def getAddonInfo(self, key):
		return _INFO.get(self.id, {}).get(key, '')

	def getSetting(self, key):
		store = _STORE.get(self.id, {})
		if key in store:
			return store[key]
		return _DEFAULTS.get(self.id, {}).get(key, '')

	def setSetting(self, key, value):
		_STORE.setdefault(self.id, {})[key] = str(value)

	def getSettingBool(self, key):
		return self.getSetting(key) == 'true'

	def getSettingInt(self, key):
		return int(self.getSetting(key) or 0)

	def getLocalizedString(self, string_id):
		return _STRINGS.get(string_id, '')

	def openSettings(self):
		OPENED_SETTINGS.append(self.id)


reset()
