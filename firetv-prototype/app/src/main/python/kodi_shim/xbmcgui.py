# -*- coding: utf-8 -*-
"""Minimal xbmcgui replacement. Nothing here draws anything; the scrapers
only ever poke at window properties and would-be dialogs."""

import threading

NOTIFICATION_INFO = 'info'
NOTIFICATION_WARNING = 'warning'
NOTIFICATION_ERROR = 'error'

_windows = {}
_lock = threading.Lock()


class Window(object):
    def __init__(self, window_id=10000):
        with _lock:
            _windows.setdefault(window_id, {})
        self._id = window_id

    def getProperty(self, key):
        with _lock:
            return _windows.get(self._id, {}).get(key, '')

    def setProperty(self, key, value):
        with _lock:
            _windows.setdefault(self._id, {})[key] = value

    def clearProperty(self, key):
        with _lock:
            _windows.get(self._id, {}).pop(key, None)

    def clearProperties(self):
        with _lock:
            _windows[self._id] = {}


class WindowXML(Window):
    pass


class ListItem(object):
    def __init__(self, label='', label2='', path='', offscreen=False):
        self.label = label
        self.path = path

    def setArt(self, art):
        pass

    def setInfo(self, type, info):
        pass

    def setProperty(self, key, value):
        pass

    def setLabel(self, label):
        self.label = label

    def addContextMenuItems(self, items):
        pass

    def getVideoInfoTag(self):
        raise NotImplementedError('no InfoTagVideo outside Kodi')


class Dialog(object):
    def notification(self, heading, message, icon=None, time=5000, sound=True):
        return None

    def ok(self, heading, message='', line2='', line3=''):
        return True

    def yesno(self, heading, message='', *args, **kwargs):
        return False

    def select(self, heading, list, autoclose=0, preselect=-1, useDetails=False):
        return -1

    def input(self, heading, defaultt='', type=0, option=0, autoclose=0):
        return defaultt

    def textviewer(self, heading, text, usemono=False):
        return None


class DialogProgress(object):
    def create(self, heading, message=''):
        return None

    def update(self, percent, message=''):
        return None

    def close(self):
        return None

    def iscanceled(self):
        return False


class DialogProgressBG(DialogProgress):
    pass
