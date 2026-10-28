"""SQLite helpers: file history (so nothing is copied twice), event log, batches."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    rating INTEGER,
    status TEXT NOT NULL,          -- copied | skipped | error
    first_seen REAL NOT NULL,
    last_checked REAL NOT NULL,
    dest TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS files_identity ON files (name, size, mtime);
CREATE INDEX IF NOT EXISTS files_status ON files (status, first_seen);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,            -- copied | skipped | error | recheck | info
    file TEXT NOT NULL,
    rating INTEGER,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started REAL NOT NULL,
    finished REAL NOT NULL,
    copied INTEGER NOT NULL,
    skipped INTEGER NOT NULL,
    errors INTEGER NOT NULL
);
"""

EVENT_KEEP = 5000


@dataclass
class FileRow:
    path: str
    name: str
    size: int
    mtime: float
    rating: int | None
    status: str
    first_seen: float
    last_checked: float
    dest: str | None
    detail: str | None


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # files

    def get_file(self, path: str) -> FileRow | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
        return FileRow(**dict(row)) if row else None

    def copied_elsewhere(self, name: str, size: int, mtime: float) -> bool:
        """Same file (name, size, mtime) already copied from another path, e.g. the same card on a new mount."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM files WHERE name = ? AND size = ? AND abs(mtime - ?) < 1 AND status = 'copied' LIMIT 1",
                (name, size, mtime),
            ).fetchone()
        return row is not None

    def upsert_file(self, path: str, name: str, size: int, mtime: float, rating: int | None,
                    status: str, dest: str | None = None, detail: str | None = None) -> None:
        now = time.time()
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO files (path, name, size, mtime, rating, status, first_seen, last_checked, dest, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                     name = excluded.name, size = excluded.size, mtime = excluded.mtime,
                     rating = excluded.rating, status = excluded.status,
                     last_checked = excluded.last_checked,
                     dest = COALESCE(excluded.dest, files.dest), detail = excluded.detail""",
                (path, name, size, mtime, rating, status, now, now, dest, detail),
            )

    def pending_rechecks(self, window_seconds: float) -> list[FileRow]:
        cutoff = time.time() - window_seconds
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM files WHERE status IN ('skipped', 'error') AND first_seen >= ? ORDER BY first_seen",
                (cutoff,),
            ).fetchall()
        return [FileRow(**dict(r)) for r in rows]

    def recheck_summary(self, window_seconds: float) -> tuple[int, float | None]:
        """(count of files still being re-checked, seconds until the newest one leaves the window)."""
        cutoff = time.time() - window_seconds
        with self._lock:
            row = self._conn.execute(
                "SELECT count(*), max(first_seen) FROM files WHERE status IN ('skipped', 'error') AND first_seen >= ?",
                (cutoff,),
            ).fetchone()
        count, newest = row[0], row[1]
        left = (newest + window_seconds - time.time()) if newest else None
        return count, left

    def clear_history(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM files")

    # events

    def log(self, kind: str, file: str, rating: int | None = None, detail: str = "") -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO events (ts, kind, file, rating, detail) VALUES (?, ?, ?, ?, ?)",
                (time.time(), kind, file, rating, detail),
            )
            self._conn.execute(
                "DELETE FROM events WHERE id <= (SELECT max(id) FROM events) - ?", (EVENT_KEEP,)
            )

    def recent_events(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, kind, file, rating, detail FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # batches

    def add_batch(self, started: float, finished: float, copied: int, skipped: int, errors: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO batches (started, finished, copied, skipped, errors) VALUES (?, ?, ?, ?, ?)",
                (started, finished, copied, skipped, errors),
            )

    def last_batch(self) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
