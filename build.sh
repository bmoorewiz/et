#!/usr/bin/env bash
# Build all distributables for Episode Tracker.
#
#   1. plugin.video.episodetracker-<version>.zip at the repo root.
#      This is what the add-on's built-in updater downloads. Existing
#      installs have this path baked in, so it must keep working.
#   2. docs/ - the Kodi repository tree (addons.xml, checksum, per-addon
#      zips, browsable index) served via GitHub Pages / raw URLs.
#
# Usage: ./build.sh
set -euo pipefail

ADDON="plugin.video.episodetracker"
cd "$(dirname "$0")"

VERSION=$(python3 -c "import xml.etree.ElementTree as ET; print(ET.parse('$ADDON/addon.xml').getroot().get('version'))")
ZIP="${ADDON}-${VERSION}.zip"

find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# 1. root zip for the built-in updater
rm -f "${ADDON}"-*.zip
zip -r -q "$ZIP" "$ADDON" -x '*__pycache__*' '*.pyc' '*.DS_Store'
echo "Built $ZIP ($(stat -c%s "$ZIP") bytes)"

# 2. Kodi repository tree
python3 tools/build_repo.py
