# Starport: Spec

**One line:** A small standalone app that watches a folder, picks out photos at or above a chosen star rating, copies them into a staging folder, and opens that folder in Lightroom Classic (LRC) for import.

## 1. Goals

- Only photos that meet a star rating threshold reach LRC.
- Fully automatic after one-time setup. No manual copying.
- Never modify or delete originals.
- Runs as a standalone app (no terminal needed), starts at login.

## 2. Non-goals (v1)

- No culling or rating UI. Ratings are set elsewhere (camera, Photo Mechanic, Bridge, etc.).
- No editing, keywording, or presets.
- No cloud sync.
- No Windows build in v1 (design keeps it possible).

## 3. Assumptions (confirm or change)

- OS: macOS 13+.
- Files: RAW (CR3, ARW, NEF, DNG, etc.) plus JPEG/HEIC, with optional sidecar `.xmp`.
- Ratings stored as `xmp:Rating` in embedded XMP or sidecar.
- LRC Auto Import is used for the actual catalog ingest.

## 4. How it works

1. **Watch** the source folder for new files (FSEvents via `watchdog`).
2. **Settle**: wait until a file stops changing for N seconds (default 5) so partial card copies are not read.
3. **Read rating** with ExifTool (`-Rating`, also checks sidecar `.xmp`).
4. **Filter**: if rating >= threshold (or == threshold, user setting), continue. Otherwise skip and remember it for re-check.
5. **Copy** the file plus its sidecar into the staging folder. Prefix with `YYYYMMDD-HHMMSS_` if the name already exists.
6. **Log** the file in a local SQLite DB (path, size, mtime, rating, status) so nothing is copied twice.
7. **Open LRC** once per batch (debounced, default 30 s). LRC Auto Import, pointed at the staging folder, does the rest.

### Re-check unrated files

Files rated after arrival must still get picked up. Skipped files are re-scanned every X minutes (default 5) for a configurable window (default 24 h), or whenever a sidecar `.xmp` changes.

## 5. Settings

| Setting | Default | Notes |
|---|---|---|
| Source folder | none | Required |
| Staging folder | `~/Pictures/Starport_Staging` | Must match LRC Auto Import watch folder |
| Min rating | 4 | 1 to 5 |
| Match mode | `>=` | `>=` or `==` |
| Include sidecars | on | Needed for RAW ratings to carry into LRC |
| Settle time | 5 s | |
| Re-check interval | 5 min | |
| Re-check window | 24 h | |
| Open LRC after ingest | on | |
| Launch at login | on | |
| Notifications | on | Summary per batch |

## 6. UI

Menu bar app, no dock icon.

- **Menu**: Pause/Resume, "Ingest now", last batch summary, Open staging folder, Settings, Quit.
- **Settings window**: the table above, plus folder pickers and a rating star picker.
- **Status icon states**: idle, working, paused, error.
- **Log view**: last 200 events (copied, skipped, error).

## 7. Tech stack

- **Language**: Python 3.12.
- **Menu bar**: `rumps`.
- **Folder watching**: `watchdog`.
- **Metadata**: bundled ExifTool binary (no user install).
- **Storage**: SQLite (`~/Library/Application Support/Starport/`), JSON for settings.
- **Packaging**: `py2app` into a signed `.app`, optional DMG.
- **Alternative if a native feel matters**: SwiftUI menu bar app calling ExifTool via `Process`.

## 8. Project layout

```
starport/
  app.py            # menu bar entry point
  watcher.py        # folder watching + settle logic
  rating.py         # ExifTool wrapper
  ingest.py         # copy, dedupe, DB
  lrc.py            # open LRC, debounce
  settings.py       # load/save, defaults
  db.py             # SQLite helpers
  resources/exiftool/
  tests/
  setup.py          # py2app config
```

## 9. Edge cases

- Card still copying: handled by settle time.
- Same filename from two cards: timestamp prefix.
- RAW + JPEG pairs: copy both if either matches (setting).
- Missing sidecar: fall back to embedded XMP; if neither, treat as unrated.
- LRC not installed or not found: show error, still copy files.
- LRC already open: just bring forward, do not relaunch.
- Source folder unmounted (external drive): pause quietly, resume on mount.
- Staging folder deleted: recreate and warn.
- Permissions: request Full Disk Access / folder access on first run.

## 10. Safety rules

- Copy only. Never move, rename, or delete source files.
- Never write ratings or metadata back to originals.
- Never overwrite an existing file in staging.

## 11. Milestones

1. **CLI core**: watch, read rating, copy, DB. Tested on a sample folder.
2. **Menu bar shell**: pause/resume, ingest now, settings file.
3. **Settings UI** and launch at login.
4. **LRC open** logic and notifications.
5. **Packaging**: signed `.app`, first-run permissions flow.
6. **Polish**: log view, error states, README.

## 12. Testing

- Unit tests for rating parsing (embedded, sidecar, missing, 0 to 5).
- Fixture folder with mixed ratings and RAW/JPEG/sidecar combos.
- Simulated slow copy to verify settle logic.
- Manual test with real LRC Auto Import end to end.

## 13. Open questions

- Camera/file formats and where ratings are applied?
- Should 0-star and rejected (-1) files ever be copied?
- Want a Windows version later?
- Should the app also tag files (e.g., keyword or color label) on the way in?

## 14. Name

**Starport.** Stars (ratings) plus port (import). Short, easy to say, and does not use Adobe's trademarks.

Alternates: **Keepers**, **Sieve**, **Fivestar**, **Starfall**.
