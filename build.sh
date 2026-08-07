#!/usr/bin/env bash
# Build all distributables for Episode Tracker.
#
# Produces, from a single pass:
#   docs/                     the Kodi repository tree (addons.xml, checksum,
#                             per-addon zips, browsable index)
#   ./index.html + ./*.zip    the same listing and zips at the repo root, so
#                             the GitHub Pages site works whether its source
#                             folder is "/" or "/docs"
#
# The root plugin.video.episodetracker-<version>.zip is also the path the
# add-on's built-in updater downloads, so it must keep existing.
#
# Tests bracket the build. The source tests run first, because there is no
# point packaging code that is broken. The packaging tests run afterwards,
# because they check the built artefacts against each other - a zip whose
# name says one version and whose addon.xml says another has shipped twice
# from running this script in the wrong working directory, and Kodi's only
# symptom is an update that quietly installs the old code.
#
# Usage: ./build.sh            build, with tests either side
#        ./build.sh --no-test  build only
set -euo pipefail

cd "$(dirname "$0")"

run_tests=1
[ "${1:-}" = "--no-test" ] && run_tests=0

find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

if [ "$run_tests" = 1 ]; then
	echo "--- tests (source) ---"
	python3 tests/run.py --exclude packaging
fi

python3 tools/build_repo.py

if [ "$run_tests" = 1 ]; then
	echo "--- tests (packaging) ---"
	python3 tests/run.py packaging
fi

find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
