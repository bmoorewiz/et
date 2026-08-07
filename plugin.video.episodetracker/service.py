# -*- coding: utf-8 -*-
"""Episode Tracker background service.

Runs for as long as Kodi does and watches playback, because the plugin
process is destroyed moments after it hands a URL to Kodi and cannot
follow an episode through to the end.
"""

from resources.lib import scrobbler

if __name__ == '__main__':
    scrobbler.run()
