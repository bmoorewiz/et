#!/usr/bin/env bash
# Build an installable Kodi zip for Episode Tracker.
# Usage: ./build.sh
set -euo pipefail

ADDON="plugin.video.episodetracker"
cd "$(dirname "$0")"

VERSION=$(grep -o 'version="[^"]*"' "$ADDON/addon.xml" | head -2 | tail -1 | cut -d'"' -f2)
ZIP="${ADDON}-${VERSION}.zip"

find "$ADDON" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
rm -f "$ZIP"

zip -r -q "$ZIP" "$ADDON" -x '*__pycache__*' '*.pyc' '*.DS_Store'

echo "Built $ZIP"
unzip -l "$ZIP" | tail -3
