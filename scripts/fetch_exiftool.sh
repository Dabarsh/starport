#!/bin/sh
# Download Phil Harvey's ExifTool (Perl; macOS ships Perl) into resources/exiftool for bundling.
# Usage: scripts/fetch_exiftool.sh [version]   e.g. 13.10 — see https://exiftool.org for the current one.
set -eu
VERSION="${1:?pass the ExifTool version, e.g. 13.10}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/resources/exiftool"
TMP="$(mktemp -d)"
curl -fL "https://exiftool.org/Image-ExifTool-${VERSION}.tar.gz" -o "$TMP/et.tar.gz"
tar -xzf "$TMP/et.tar.gz" -C "$TMP"
rm -rf "$DEST/exiftool" "$DEST/lib"
cp "$TMP/Image-ExifTool-${VERSION}/exiftool" "$DEST/exiftool"
cp -R "$TMP/Image-ExifTool-${VERSION}/lib" "$DEST/lib"
chmod +x "$DEST/exiftool"
rm -rf "$TMP"
"$DEST/exiftool" -ver
