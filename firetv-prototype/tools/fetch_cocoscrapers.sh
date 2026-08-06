#!/usr/bin/env bash
# Download the official CocoScrapers module and place its `cocoscrapers`
# package where the app expects it. Not vendored into this repo: it is a
# third-party GPL module and is better tracked at its own version.
#
# Usage: ./tools/fetch_cocoscrapers.sh [version]
set -euo pipefail

VERSION="${1:-}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/app/src/main/python"
REPO="https://raw.githubusercontent.com/CocoJoe2411/repository.cocoscrapers/main/zips"

if [ -z "$VERSION" ]; then
  VERSION=$(curl -fsSL "$REPO/addons.xml" \
    | grep -o 'id="script.module.cocoscrapers"[^>]*version="[^"]*"' \
    | grep -o 'version="[^"]*"' | tail -1 | cut -d'"' -f2)
fi
echo "CocoScrapers version: $VERSION"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/coco.zip" \
  "$REPO/script.module.cocoscrapers/script.module.cocoscrapers-$VERSION.zip"
unzip -q "$TMP/coco.zip" -d "$TMP/x"

rm -rf "$DEST/cocoscrapers"
cp -r "$TMP/x/script.module.cocoscrapers/lib/cocoscrapers" "$DEST/cocoscrapers"
echo "Installed -> $DEST/cocoscrapers"
