#!/bin/sh
# Download Phil Harvey's ExifTool (Perl; macOS ships Perl) into resources/exiftool for bundling,
# checked against the SHA-256 published at https://exiftool.org/checksums.txt.
# Usage: scripts/fetch_exiftool.sh [version]   (default: the latest, from https://exiftool.org/ver.txt)
set -eu
VERSION="${1:-$(curl -fsSL https://exiftool.org/ver.txt)}"
NAME="Image-ExifTool-${VERSION}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/resources/exiftool"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fsSL "https://sourceforge.net/projects/exiftool/files/${NAME}.tar.gz/download" -o "$TMP/et.tar.gz" \
  || curl -fsSL "https://exiftool.org/${NAME}.tar.gz" -o "$TMP/et.tar.gz"

EXPECTED="$(curl -fsSL https://exiftool.org/checksums.txt | sed -n "s/^SHA2-256(${NAME}.tar.gz)= *//p")"
ACTUAL="$(shasum -a 256 "$TMP/et.tar.gz" | cut -d' ' -f1)"
if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "checksum mismatch for ${NAME}.tar.gz (expected ${EXPECTED:-none}, got $ACTUAL)" >&2
  exit 1
fi

tar -xzf "$TMP/et.tar.gz" -C "$TMP"
rm -rf "$DEST/exiftool" "$DEST/lib"
mkdir -p "$DEST"
cp "$TMP/${NAME}/exiftool" "$DEST/exiftool"
cp -R "$TMP/${NAME}/lib" "$DEST/lib"
chmod +x "$DEST/exiftool"
echo "ExifTool $("$DEST/exiftool" -ver) in $DEST"
