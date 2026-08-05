# -*- coding: utf-8 -*-
"""Episode Tracker - Kodi plugin entry point.

Tracks and plays your Trakt.tv "next up" episodes using the CocoScrapers
module for sources and Real-Debrid for playback.
"""

from resources.lib import router

if __name__ == '__main__':
	router.dispatch()
