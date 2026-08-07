# -*- coding: utf-8 -*-
"""Stand-in Kodi modules, so the add-on can be tested without Kodi.

These are deliberately *faithful* rather than convenient. Every shortcut
taken here shows up as a test that passes against the stub and fails on a
Fire Stick, so where Kodi's real behaviour is surprising the stub copies the
surprise and says so in a comment:

* an unset setting reads back as its ``settings.xml`` default, not as ''
* ``xbmcaddon.Addon('not.installed')`` raises, which is how the add-on
  detects that CocoScrapers is missing
* window properties belong to the window id, not to the Python object, which
  is the whole basis of the plugin -> service handover
* ``getVideoInfoTag()`` exists (Kodi 20+), so the InfoTagVideo path is the
  one under test rather than the legacy ``setInfo`` fallback
* localized strings come from the real strings.po, keeping format
  placeholders intact

Import order matters: ``configure()`` must run before the add-on imports
``control``, because control reads the profile path at import time.
"""

import os
import tempfile

ADDON_ROOT = os.path.join(
	os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
	'plugin.video.episodetracker')

# Filled in by configure(); every stub reads them at call time.
PROFILE = os.path.join(tempfile.gettempdir(), 'et-test-profile')


def configure(profile=None):
	"""Point the stubs at a scratch profile directory."""
	global PROFILE
	if profile:
		PROFILE = profile
	os.makedirs(PROFILE, exist_ok=True)
	return PROFILE


def reset():
	"""Return every stub to its just-imported state, between tests."""
	import xbmc
	import xbmcaddon
	import xbmcgui
	import xbmcplugin
	xbmc.reset()
	xbmcaddon.reset()
	xbmcgui.reset()
	xbmcplugin.reset()
