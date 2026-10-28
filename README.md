# Starport

A macOS menu bar app that watches a folder, picks out photos at or above a star rating, copies them (with their `.xmp` sidecars) into a staging folder, and opens Lightroom Classic so Auto Import can bring them in. Originals are never moved, renamed, changed or deleted.

## Install

1. Download `Starport-<version>.dmg` from [Releases](https://github.com/Dabarsh/starport/releases), open it and drag Starport to Applications. The app is universal (Apple Silicon and Intel) and needs macOS 13 or later. ExifTool is built in.
2. The app is ad-hoc signed, not notarized, so macOS blocks it on first launch. Open it once, then go to System Settings › Privacy & Security and click **Open Anyway**. Or run:

   ```bash
   xattr -dr com.apple.quarantine /Applications/Starport.app
   ```
3. Starport lives in the menu bar (no Dock icon). The first launch opens setup.

## Run from source

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
scripts/fetch_exiftool.sh      # bundles ExifTool (or: brew install exiftool)
.venv/bin/python app.py
```

The first launch opens setup. Choose the source folder (card, drive or folder), confirm Starport can read it, then point Lightroom Classic's Auto Import at the staging folder (File › Auto Import › Auto Import Settings…).

Development helpers:

- `STARPORT_HOME=/some/folder` keeps settings and the database somewhere other than `~/Library/Application Support/Starport`.
- `STARPORT_OPEN=popover,settings,log` opens those views at launch.
- `python cli.py --source DIR --staging DIR [--once]` runs the engine without the menu bar, using a throwaway data folder.
- `python -m pytest tests` runs the tests (rating parsing, filtering, dedupe, pairs, sidecars, rename on clash, re-checks, settle timing).

## Build the app

```bash
scripts/build.sh
```

The script:
1. Downloads the latest ExifTool into `resources/exiftool` and checks it against the published SHA-256.
2. Swaps in watchdog's universal2 wheel so the bundle runs on both architectures, and runs the tests.
3. Builds with py2app, checks every native binary is universal, and signs the app.
4. Writes `dist/Starport-<version>.dmg`, `.zip` and `SHA256SUMS.txt`.

The build runs in `~/Library/Caches/Starport-build`, because iCloud-synced folders such as the Desktop add file attributes that `codesign` rejects. The version lives in `version.py`, and `scripts/make_icon.py` redraws `resources/Starport.icns`.

The default build is ad-hoc signed. For a Developer ID build, set `SIGN_IDENTITY="Developer ID Application: Name (TEAMID)"`. Add `NOTARY_PROFILE=<keychain profile>` to also notarize and staple the DMG.

From a bundle, Launch at login uses `SMAppService`. Run from source, it writes `~/Library/LaunchAgents/com.starport.app.plist`.

## How it works

1. **Watch** the source folder with FSEvents (`watchdog`). Hidden files such as `._IMG_1.CR3` from exFAT cards are ignored.
2. **Settle**: a file is read only after its size and mtime have held still for the settle time (default 5 s).
3. **Read rating**. RAW files: sidecar `.xmp` first, then embedded XMP via ExifTool. JPEG/HEIC/TIFF: embedded first, then sidecar. A shared `IMG_1.xmp` next to a RAW + JPEG pair therefore rates the RAW.
4. **Filter**: at or above, or exactly, the minimum rating. Unrated, 0★ and rejected (−1) files never qualify.
5. **Copy** the file and its sidecar into staging. If a name is taken, every member of the group gets the same `YYYYMMDD-HHMMSS_` prefix, so Lightroom still pairs RAW and sidecar. Files are created in exclusive mode, so nothing in staging is ever overwritten.
6. **Record** every file in SQLite (path, size, mtime, rating, status), so nothing is copied twice. That includes the same card mounted again at another path.
7. **Batch**: once activity has been quiet for 30 s, the batch closes. Starport saves a summary, sends a notification, and opens Lightroom Classic. If Lightroom is already running it is only brought forward.

Skipped files are re-checked every 5 min for 24 h, and immediately when their sidecar changes. A file rated after it arrived is therefore still picked up.

Edge cases: if the source drive is unplugged, Starport pauses quietly and catches up when it returns. A deleted staging folder is recreated, with a warning in the log. If Lightroom is missing, the error is logged and files are still copied. Only one copy of Starport runs at a time.

## Layout

```
app.py          menu bar shell: status item, popover, windows (PyObjC + WKWebView)
icons.py        menu bar template icons: idle, working, paused, error
engine.py       ties watcher, ingest, re-checks, batches and Lightroom together
watcher.py      folder watching + settle logic + mount detection
rating.py       ExifTool wrapper, sidecar parsing
ingest.py       filter, copy, dedupe, DB records
lrc.py          find/open Lightroom Classic, debounce
settings.py     defaults, load/save JSON
db.py           SQLite helpers
cli.py          headless runner
version.py      app version
ui/             popover, settings, log and first-run pages
resources/      app icon; exiftool/ holds the bundled ExifTool (fetched, not committed)
scripts/        build.sh, fetch_exiftool.sh, make_icon.py, entitlements.plist
tests/
setup.py        py2app config
```

## Departures from the spec

- **UI toolkit**: the spec named `rumps`. The design's popover (status, rule, last-batch tiles, re-check line) can't be drawn with `rumps`, which only builds plain NSMenus. The shell therefore uses PyObjC directly: `NSStatusItem` + `NSPopover`, with WKWebView windows built from the design's HTML.
- **Fonts**: the design uses Source Sans 3 / Source Code Pro. The app uses them when installed and otherwise falls back to the system fonts. Nothing is loaded from the network.
- **Settings › Advanced** (not in the design) holds the batch quiet time, where ExifTool, Lightroom and the database were found, and "Clear ingest history".
