# -*- coding: utf-8 -*-
"""Minimal xbmcplugin replacement. CocoScrapers does not build directories,
but a few helper modules import this."""


def addDirectoryItem(handle=-1, url='', listitem=None, isFolder=False, totalItems=0):
    return True


def addDirectoryItems(handle=-1, items=None, totalItems=0):
    return True


def endOfDirectory(handle=-1, succeeded=True, updateListing=False, cacheToDisc=True):
    return None


def setContent(handle=-1, content=''):
    return None


def setResolvedUrl(handle=-1, succeeded=True, listitem=None):
    return None


def setPluginCategory(handle=-1, category=''):
    return None


def addSortMethod(handle=-1, sortMethod=0, label2Mask=''):
    return None
