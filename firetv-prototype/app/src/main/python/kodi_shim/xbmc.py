# -*- coding: utf-8 -*-
"""Minimal xbmc replacement for running CocoScrapers outside Kodi."""

import os
import sys
import time

LOGDEBUG, LOGINFO, LOGNOTICE, LOGWARNING, LOGERROR, LOGFATAL = 0, 1, 2, 3, 4, 5

_log_sink = None


def set_log_sink(fn):
    """Route scraper logging somewhere useful (e.g. Android logcat)."""
    global _log_sink
    _log_sink = fn


def log(message, level=LOGINFO):
    try:
        text = message if isinstance(message, str) else str(message)
        if _log_sink:
            _log_sink(text, level)
        else:
            sys.stderr.write('[cocoscrapers] %s\n' % text)
    except Exception:
        pass


def executebuiltin(command, wait=False):
    return None


def executeJSONRPC(request):
    # Nothing in the scraper path depends on a real answer.
    return '{"jsonrpc":"2.0","result":{},"id":1}'


def getCondVisibility(condition):
    return False


def getInfoLabel(label):
    return ''


def getLanguage(*args, **kwargs):
    return 'English'


def getRegion(*args, **kwargs):
    return ''


def sleep(milliseconds):
    time.sleep(max(0, milliseconds) / 1000.0)


def translatePath(path):
    return path


class Monitor(object):
    def waitForAbort(self, timeout=0):
        if timeout:
            time.sleep(timeout)
        return False

    def abortRequested(self):
        return False


class Player(object):
    def isPlaying(self):
        return False

    def isPlayingVideo(self):
        return False

    def getTotalTime(self):
        return 0.0

    def getTime(self):
        return 0.0

    def getPlayingFile(self):
        return ''

    def stop(self):
        return None


class Keyboard(object):
    def __init__(self, default='', heading=''):
        self._text = default

    def doModal(self):
        return None

    def isConfirmed(self):
        return False

    def getText(self):
        return self._text
