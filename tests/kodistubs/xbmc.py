# -*- coding: utf-8 -*-
"""Stub for Kodi's ``xbmc`` module."""

LOGDEBUG = 0
LOGINFO = 1
LOGWARNING = 2
LOGERROR = 3
LOGFATAL = 4

# Everything the add-on emitted, for tests to assert on.
LOG = []
BUILTINS = []

INFO_LABELS = {
	'System.BuildVersion': '21.1 (21.1.0) Git:20240501-testing',
	'System.BuildDate': 'May  1 2024',
	'Skin.CurrentSkin': 'Estuary',
}

# Set by tests to make abortRequested() true.
ABORT = [False]
# Every waitForAbort() duration, so a test can prove a loop actually waited
# without any of them costing real wall-clock time.
WAITS = []


def reset():
	del LOG[:]
	del BUILTINS[:]
	del WAITS[:]
	ABORT[0] = False


def log(msg, level=LOGINFO):
	LOG.append((level, msg))


def executebuiltin(function, wait=False):
	BUILTINS.append(function)


def translatePath(path):
	return path


def getInfoLabel(label):
	return INFO_LABELS.get(label, '')


def sleep(milliseconds):
	WAITS.append(milliseconds / 1000.0)


class Monitor(object):
	def abortRequested(self):
		return ABORT[0]

	def waitForAbort(self, timeout=0):
		# Never actually sleeps: a test that waited out Real-Debrid's 15s
		# resolve timeout for real would make the suite unusable. Tests that
		# care about elapsed time drive a fake clock instead.
		WAITS.append(timeout)
		return ABORT[0]


class Player(object):
	"""Only the surface the add-on's ScrobblePlayer actually uses.

	The callbacks are not defined here on purpose - Kodi calls them on the
	subclass, and tests call them directly.
	"""

	def __init__(self, *args, **kwargs):
		pass

	def isPlayingVideo(self):
		return False

	def getTotalTime(self):
		raise RuntimeError('Kodi is not playing a file')

	def getTime(self):
		raise RuntimeError('Kodi is not playing a file')

	def seekTime(self, seconds):
		raise RuntimeError('Kodi is not playing a file')
