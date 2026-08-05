# -*- coding: utf-8 -*-
"""Central helpers: addon handles, settings, paths, dialogs and logging."""

import sys
from urllib.parse import urlencode, parse_qsl

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

addon = xbmcaddon.Addon()
addon_id = addon.getAddonInfo('id')
addon_name = addon.getAddonInfo('name')
addon_version = addon.getAddonInfo('version')
addon_path = xbmcvfs.translatePath(addon.getAddonInfo('path'))
addon_icon = addon.getAddonInfo('icon')
addon_fanart = addon.getAddonInfo('fanart')

# profile (user data) directory - created on first use
profile_path = xbmcvfs.translatePath(addon.getAddonInfo('profile'))

# plugin invocation context (only valid when launched as a plugin)
try:
	handle = int(sys.argv[1])
	base_url = sys.argv[0]
except (IndexError, ValueError):
	handle = -1
	base_url = 'plugin://%s/' % addon_id

dialog = xbmcgui.Dialog()
progress = xbmcgui.DialogProgress()
progress_bg = xbmcgui.DialogProgressBG()
monitor = xbmc.Monitor()


def lang(string_id):
	"""Return a localized string for the given id."""
	try:
		return addon.getLocalizedString(string_id)
	except Exception:
		return ''


def langf(string_id, *args):
	"""Localized string with % formatting applied.

	Falls back to the unformatted text if the string is missing or its
	placeholders do not match the supplied arguments, so a translation gap can
	never raise mid-operation.
	"""
	text = lang(string_id)
	if not args:
		return text
	try:
		return text % args
	except Exception:
		return text


def setting(key, default=''):
	try:
		value = addon.getSetting(key)
		return value if value != '' else default
	except Exception:
		return default


def set_setting(key, value):
	try:
		addon.setSetting(key, '' if value is None else str(value))
	except Exception:
		pass


def get_bool(key, default=False):
	value = setting(key, None)
	if value is None:
		return default
	return value == 'true'


def get_int(key, default=0):
	try:
		return int(float(setting(key, default)))
	except Exception:
		return default


def open_settings():
	addon.openSettings()


def make_profile():
	if not xbmcvfs.exists(profile_path):
		try:
			xbmcvfs.mkdirs(profile_path)
		except Exception:
			pass


def build_url(query):
	"""Build a plugin:// callback URL from a dict of query params."""
	return '%s?%s' % (base_url, urlencode(query))


def parse_params(query_string):
	"""Parse the sys.argv[2] query string ('?a=b&c=d') into a dict."""
	if query_string.startswith('?'):
		query_string = query_string[1:]
	return dict(parse_qsl(query_string))


def notify(message, heading=None, icon=None, time=4000, sound=True):
	if heading is None:
		heading = addon_name
	if isinstance(message, int):
		message = lang(message)
	if isinstance(heading, int):
		heading = lang(heading)
	dialog.notification(heading, message, icon or addon_icon, time, sound)


def ok_dialog(message, heading=None):
	if heading is None:
		heading = addon_name
	if isinstance(message, int):
		message = lang(message)
	if isinstance(heading, int):
		heading = lang(heading)
	return dialog.ok(heading, message)


def yesno_dialog(message, heading=None):
	if heading is None:
		heading = addon_name
	if isinstance(message, int):
		message = lang(message)
	if isinstance(heading, int):
		heading = lang(heading)
	return dialog.yesno(heading, message)


def select_dialog(options, heading=None):
	if heading is None:
		heading = addon_name
	if isinstance(heading, int):
		heading = lang(heading)
	return dialog.select(heading, options)


def sleep(ms):
	monitor.waitForAbort(ms / 1000.0)


def aborted():
	return monitor.abortRequested()


def add_directory_item(label, params, is_folder=True, art=None, info=None,
					   context=None, is_playable=False):
	"""Add a single ListItem to the current plugin directory."""
	item = xbmcgui.ListItem(label=label)
	art = art or {}
	if 'icon' not in art:
		art['icon'] = addon_icon
	if 'fanart' not in art:
		art['fanart'] = addon_fanart
	item.setArt(art)
	if info:
		try:
			# Kodi 20+ (InfoTagVideo) with graceful fallback to setInfo
			tag = item.getVideoInfoTag()
			_apply_info_tag(tag, info)
		except Exception:
			item.setInfo('video', info)
	if is_playable:
		item.setProperty('IsPlayable', 'true')
	if context:
		item.addContextMenuItems(context)
	url = build_url(params)
	xbmcplugin.addDirectoryItem(handle=handle, url=url, listitem=item,
								isFolder=is_folder)
	return item


def _apply_info_tag(tag, info):
	setters = {
		'title': tag.setTitle,
		'tvshowtitle': tag.setTvShowTitle,
		'plot': tag.setPlot,
		'season': tag.setSeason,
		'episode': tag.setEpisode,
		'aired': tag.setFirstAired,
		'premiered': tag.setPremiered,
		'mediatype': tag.setMediaType,
		'duration': tag.setDuration,
	}
	for key, value in info.items():
		if value in (None, ''):
			continue
		fn = setters.get(key)
		if not fn:
			continue
		try:
			if key in ('season', 'episode', 'duration'):
				fn(int(value))
			else:
				fn(value)
		except Exception:
			pass


def end_directory(cache_to_disc=True, content='episodes'):
	if content:
		xbmcplugin.setContent(handle, content)
	xbmcplugin.endOfDirectory(handle, cacheToDisc=cache_to_disc)


def resolve(list_item):
	xbmcplugin.setResolvedUrl(handle, True, list_item)


def resolve_failed():
	xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())


def log(message, level=xbmc.LOGINFO):
	try:
		xbmc.log('[%s] %s' % (addon_id, message), level)
	except Exception:
		pass


def debug(message):
	if get_bool('debug.enabled'):
		log(message, xbmc.LOGINFO)


def error(message=''):
	import traceback
	log('%s\n%s' % (message, traceback.format_exc()), xbmc.LOGERROR)
