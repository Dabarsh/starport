#!/bin/bash
# Build a release: bundle ExifTool, make watchdog universal, run tests, py2app, sign, package.
#
#   scripts/build.sh                     # ad-hoc signed (runs locally; Gatekeeper warns on other Macs)
#   SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" scripts/build.sh
#   NOTARY_PROFILE=starport scripts/build.sh   # also notarize + staple (xcrun notarytool store-credentials first)
#
# Output: dist/Starport.app, dist/Starport-<version>.dmg, dist/Starport-<version>.zip
#
# The bundle is built and signed in $BUILD_DIR (default ~/Library/Caches/Starport-build): folders synced
# by iCloud Drive (like the Desktop) keep re-adding Finder attributes that codesign refuses.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
WORK="${BUILD_DIR:-$HOME/Library/Caches/Starport-build}"
VERSION="$("$PY" -c 'from version import VERSION; print(VERSION)')"
echo "== Starport $VERSION"

if [ ! -x resources/exiftool/exiftool ]; then
  echo "== bundling ExifTool"
  scripts/fetch_exiftool.sh
fi
[ -f resources/Starport.icns ] || "$PY" scripts/make_icon.py

echo "== universal2 watchdog (so the app runs on Apple Silicon and Intel)"
WD="$("$PY" -c 'import importlib.metadata as m; print(m.version("watchdog"))')"
WHEELS="$(mktemp -d)"
"$PY" -m pip download -q "watchdog==$WD" --only-binary=:all: --no-deps \
  --platform macosx_10_13_universal2 --python-version 3.12 -d "$WHEELS"
"$PY" -m pip install -q --force-reinstall --no-deps "$WHEELS"/watchdog-*.whl
rm -rf "$WHEELS"

echo "== tests"
"$PY" -m pytest -q tests

echo "== py2app"
rm -rf dist "$WORK"
mkdir -p dist "$WORK"
"$PY" setup.py py2app --dist-dir "$WORK/dist" --bdist-base "$WORK/build" > build.log 2>&1 || { tail -40 build.log; exit 1; }
APP="$WORK/dist/Starport.app"

echo "== architecture check"
BAD=0
while IFS= read -r -d '' f; do
  archs="$(lipo -archs "$f" 2>/dev/null)" || continue
  case "$archs" in *x86_64*arm64*|*arm64*x86_64*) ;; *) echo "not universal: $archs $f"; BAD=1;; esac
done < <(find "$APP" -type f \( -name '*.so' -o -name '*.dylib' -o -perm +111 \) -print0)
[ "$BAD" = 0 ] || { echo "found single-architecture binaries" >&2; exit 1; }

echo "== signing"
xattr -cr "$APP"  # Finder info / provenance attributes make codesign refuse the bundle
if [ -n "${SIGN_IDENTITY:-}" ]; then
  codesign --force --deep --timestamp --options runtime \
    --entitlements scripts/entitlements.plist -s "$SIGN_IDENTITY" "$APP"
else
  codesign --force --deep -s - "$APP"
fi
codesign --verify --deep --strict "$APP"

echo "== packaging"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Starport $VERSION" -srcfolder "$STAGE" -ov -format UDZO "dist/Starport-$VERSION.dmg"
rm -rf "$STAGE"
ditto -c -k --sequesterRsrc --keepParent "$APP" "dist/Starport-$VERSION.zip"
ditto "$APP" dist/Starport.app

if [ -n "${SIGN_IDENTITY:-}" ]; then
  codesign --force --timestamp -s "$SIGN_IDENTITY" "dist/Starport-$VERSION.dmg"
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit "dist/Starport-$VERSION.dmg" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "dist/Starport-$VERSION.dmg"
  fi
fi

(cd dist && shasum -a 256 "Starport-$VERSION.dmg" "Starport-$VERSION.zip" | tee SHA256SUMS.txt)
echo "== done: dist/"
