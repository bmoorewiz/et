#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the Episode Tracker test suite.

    python3 tests/run.py                    # everything
    python3 tests/run.py trakt              # only tests whose id matches
    python3 tests/run.py --exclude packaging  # everything but those
    python3 tests/run.py -v                 # per-test output

Uses nothing but the standard library, so it runs anywhere the add-on does.
"""

import os
import sys
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, 'plugin.video.episodetracker')

# The Kodi stubs must be importable as plain top-level modules, and the
# add-on's package root must come before them so `resources.lib.*` resolves.
sys.path.insert(0, os.path.join(HERE, 'kodistubs'))
sys.path.insert(0, HERE)
sys.path.insert(0, ADDON)


def main(argv):
	verbosity = 2 if ('-v' in argv or '--verbose' in argv) else 1
	excluded = []
	patterns = []
	expecting_exclude = False
	for argument in argv:
		if expecting_exclude:
			excluded.append(argument)
			expecting_exclude = False
		elif argument in ('-x', '--exclude'):
			expecting_exclude = True
		elif not argument.startswith('-'):
			patterns.append(argument)

	profile = tempfile.mkdtemp(prefix='episodetracker-tests-')
	try:
		import kodistubs
		kodistubs.configure(profile)

		# Import order matters: configure() has to have run before anything
		# reads control.profile_path, which happens at import time.
		import xbmcaddon
		xbmcaddon.reset()

		import support
		support.block_network()

		loader = unittest.TestLoader()
		suite = loader.discover(HERE, pattern='test_*.py', top_level_dir=HERE)
		if patterns or excluded:
			suite = _filter(suite, patterns, excluded)

		result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
		return 0 if result.wasSuccessful() else 1
	finally:
		shutil.rmtree(profile, ignore_errors=True)


def _filter(suite, patterns, excluded):
	kept = unittest.TestSuite()
	for test in _flatten(suite):
		name = test.id().lower()
		if patterns and not any(p.lower() in name for p in patterns):
			continue
		if any(x.lower() in name for x in excluded):
			continue
		kept.addTest(test)
	return kept


def _flatten(suite):
	for item in suite:
		if isinstance(item, unittest.TestSuite):
			for sub in _flatten(item):
				yield sub
		else:
			yield item


if __name__ == '__main__':
	sys.exit(main(sys.argv[1:]))
