# -*- coding: utf-8 -*-
"""Stub for Kodi's ``xbmcplugin`` module."""

SORT_METHOD_NONE = 0
SORT_METHOD_UNSORTED = 40

# Everything the plugin handed to Kodi during a request.
ITEMS = []
ENDED = []
RESOLVED = []
CONTENT = []


def reset():
	del ITEMS[:]
	del ENDED[:]
	del RESOLVED[:]
	del CONTENT[:]


def addDirectoryItem(handle, url, listitem, isFolder=False, totalItems=0):
	ITEMS.append({'handle': handle, 'url': url, 'item': listitem,
				  'folder': isFolder})
	return True


def endOfDirectory(handle, succeeded=True, updateListing=False,
				   cacheToDisc=True):
	ENDED.append({'handle': handle, 'succeeded': succeeded,
				  'cacheToDisc': cacheToDisc})


def setContent(handle, content):
	CONTENT.append(content)


def setResolvedUrl(handle, succeeded, listitem):
	RESOLVED.append({'handle': handle, 'succeeded': succeeded,
					 'item': listitem})


def setPluginCategory(handle, category):
	pass


def addSortMethod(handle, sortMethod, label2Mask=''):
	pass
