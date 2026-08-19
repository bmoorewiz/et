# -*- coding: utf-8 -*-
"""Stub for Kodi's ``xbmcgui`` module."""

INPUT_ALPHANUM = 0
INPUT_NUMERIC = 1

# Window properties are keyed on the *window id*, not on the Python object.
# Kodi's Window(10000) is the one home window however many times you
# construct it, and the plugin -> service handover depends entirely on that:
# the plugin's Window object and the service's are different objects in
# different processes writing to the same store.
_PROPERTIES = {}

# Recorded dialog interactions, for tests to assert on.
DIALOGS = []
# Queued answers. Each is popped by the matching dialog call; when the queue
# runs dry the fallback is used, so a test only has to script the answers it
# cares about.
YESNO_QUEUE = []
YESNO_DEFAULT = [True]
INPUT_QUEUE = []
SELECT_QUEUE = []


def reset():
	_PROPERTIES.clear()
	del DIALOGS[:]
	del YESNO_QUEUE[:]
	del INPUT_QUEUE[:]
	del SELECT_QUEUE[:]
	YESNO_DEFAULT[0] = True


class InfoTagVideo(object):
	"""Kodi 20+ info tag. Only the setters the add-on uses."""

	def __init__(self):
		self.values = {}

	def _set(self, key, value):
		self.values[key] = value

	def setTitle(self, v): self._set('title', v)
	def setTvShowTitle(self, v): self._set('tvshowtitle', v)
	def setPlot(self, v): self._set('plot', v)
	def setFirstAired(self, v): self._set('aired', v)
	def setPremiered(self, v): self._set('premiered', v)
	def setMediaType(self, v): self._set('mediatype', v)

	# Kodi's typed setters reject the wrong type rather than coercing, which
	# is why control._apply_info_tag() casts before calling them.
	def setSeason(self, v):
		if not isinstance(v, int):
			raise TypeError('setSeason expects an integer')
		self._set('season', v)

	def setEpisode(self, v):
		if not isinstance(v, int):
			raise TypeError('setEpisode expects an integer')
		self._set('episode', v)

	def setDuration(self, v):
		if not isinstance(v, int):
			raise TypeError('setDuration expects an integer')
		self._set('duration', v)

	def setPlaycount(self, v):
		if not isinstance(v, int):
			raise TypeError('setPlaycount expects an integer')
		self._set('playcount', v)

	def setResumePoint(self, time, totaltime=0.0):
		# Kodi takes floats here and rejects anything else.
		if not isinstance(time, float) or not isinstance(totaltime, float):
			raise TypeError('setResumePoint expects floats')
		self._set('resumepoint', (time, totaltime))


class ListItem(object):
	def __init__(self, label='', label2='', path='', offscreen=False):
		self.label = label
		self.label2 = label2
		self.path = path
		self.art = {}
		self.properties = {}
		self.context = []
		self.info = {}
		self._tag = InfoTagVideo()

	def setLabel(self, label):
		self.label = label

	def setPath(self, path):
		self.path = path

	def setArt(self, art):
		self.art.update(art)

	def setProperty(self, key, value):
		self.properties[str(key)] = str(value)

	def getProperty(self, key):
		return self.properties.get(str(key), '')

	def setContentLookup(self, enable):
		self.properties['contentlookup'] = str(enable)

	def addContextMenuItems(self, items, replaceItems=False):
		self.context = list(items)

	def setInfo(self, type, infoLabels):
		# Kodi rejects None values here; add_directory_item() strips them
		# first, and this stub enforces that so the stripping stays honest.
		for key, value in infoLabels.items():
			if value is None:
				raise TypeError('setInfo got None for "%s"' % key)
		self.info.update(infoLabels)

	def getVideoInfoTag(self):
		return self._tag


class Window(object):
	def __init__(self, existingWindowId=-1):
		self.window_id = existingWindowId

	def setProperty(self, key, value):
		_PROPERTIES[(self.window_id, key)] = str(value)

	def getProperty(self, key):
		return _PROPERTIES.get((self.window_id, key), '')

	def clearProperty(self, key):
		_PROPERTIES.pop((self.window_id, key), None)


class Dialog(object):
	def ok(self, heading, message):
		DIALOGS.append(('ok', heading, message))
		return True

	def yesno(self, heading, message, nolabel='', yeslabel='', autoclose=0):
		DIALOGS.append(('yesno', heading, message))
		if YESNO_QUEUE:
			return YESNO_QUEUE.pop(0)
		return YESNO_DEFAULT[0]

	def notification(self, heading, message, icon=None, time=5000, sound=True):
		DIALOGS.append(('notification', heading, message))

	def input(self, heading, defaultt='', type=INPUT_ALPHANUM, option=0,
			  autoclose=0):
		DIALOGS.append(('input', heading, defaultt))
		return INPUT_QUEUE.pop(0) if INPUT_QUEUE else ''

	def select(self, heading, list, autoclose=0, preselect=-1, useDetails=False):
		DIALOGS.append(('select', heading, list))
		return SELECT_QUEUE.pop(0) if SELECT_QUEUE else -1

	def textviewer(self, heading, text, usemono=False):
		DIALOGS.append(('textviewer', heading, text))


class DialogProgress(object):
	def __init__(self):
		self.messages = []
		self.closed = False

	def create(self, heading, message=''):
		self.messages.append((heading, message))

	def update(self, percent, message=''):
		self.messages.append((percent, message))

	def close(self):
		self.closed = True

	def iscanceled(self):
		return False


class DialogProgressBG(DialogProgress):
	pass
