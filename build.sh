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
# Usage: ./build.sh
set -euo pipefail

cd "$(dirname "$0")"
find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
python3 tools/build_repo.py
