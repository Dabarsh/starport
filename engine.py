"""The core: ties watcher, ingest, re-checks, batches and Lightroom together. No UI here.

All ingest work runs on one worker thread, so files are never processed twice at once.
Listeners are called from background threads; the UI hops to the main thread itself.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from dataclasses import asdict
from typing import Callable

import lrc
import rating as ratings
import settings as settings_mod
from db import DB
from ingest import Ingestor, Result, stars
from settings import Settings
from version import VERSION
from watcher import Watcher


def osascript_notify(title: str, body: str) -> None:
    script = f'display notification {_as_str(body)} with title {_as_str(title)}'
    subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _as_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Engine:
    def __init__(self, notify: Callable[[str, str], None] = osascript_notify,
                 open_lightroom: Callable[[], None] = lrc.open_lightroom):
        self.settings: Settings = settings_mod.load()
        self.db = DB(settings_mod.support_dir() / "starport.db")
        self.ingestor = Ingestor(self.db, lambda: self.settings)
        self.notify = notify
        self.open_lightroom = open_lightroom
        self.listeners: list[Callable[[], None]] = []

        self._jobs: queue.Queue = queue.Queue()
        self._busy = False
        self._watcher: Watcher | None = None
        self._mounted: bool | None = None
        self._error: str | None = None
        self._batch: Result | None = None
        self._batch_started = 0.0
        self._batch_lock = threading.Lock()
        self._batch_debounce = lrc.Debouncer(self.settings.lrc_debounce_seconds, self._close_batch)
        self._last_recheck = time.monotonic()
        self._stop = threading.Event()
        self._signature = None

        try:
            self.exiftool = ratings.find_exiftool()
        except ratings.ExifToolMissing:
            self.exiftool = None
        self.ingestor.exiftool = self.exiftool
        self.lightroom = lrc.find_lightroom()

    # lifecycle

    def start(self) -> None:
        threading.Thread(target=self._work_loop, name="starport-worker", daemon=True).start()
        threading.Thread(target=self._tick_loop, name="starport-tick", daemon=True).start()
        self._restart_watcher()

    def shutdown(self) -> None:
        self._stop.set()
        self._stop_watcher()
        self._batch_debounce.flush()
        self._jobs.put(None)

    def can_run(self) -> bool:
        return self.settings.onboarded and not self.settings.problems() and not self.settings.paused

    def _restart_watcher(self) -> None:
        self._stop_watcher()
        if not self.can_run():
            self._changed()
            return
        self._watcher = Watcher(self.settings.source, self.settings.settle_seconds, self._on_ready, self._on_mount)
        self._watcher.start()
        self._changed()

    def _stop_watcher(self) -> None:
        w, self._watcher = self._watcher, None
        self._mounted = None
        if w:
            w.stop()

    # commands (safe from any thread)

    def pause(self) -> None:
        self.update_settings(paused=True)

    def resume(self) -> None:
        self.update_settings(paused=False)

    def ingest_now(self) -> None:
        """Scan the whole source now, even while paused, and open Lightroom as soon as it is done."""
        if self.settings.source and not self.settings.problems():
            self._jobs.put(("scan", True))
            self._changed()

    def update_settings(self, **changes) -> None:
        old = self.settings
        data = asdict(old)
        data.update(changes)
        new = Settings(**data)
        new.validate()
        self.settings = new
        settings_mod.save(new)
        self._batch_debounce.delay = new.lrc_debounce_seconds
        restart_keys = ("source_folder", "settle_seconds", "paused", "onboarded")
        if any(getattr(old, k) != getattr(new, k) for k in restart_keys) or old.problems() != new.problems():
            self._restart_watcher()
        else:
            self._changed()

    def clear_history(self) -> None:
        self.db.clear_history()
        self.db.log("info", "History", None, "ingest history cleared")
        self._changed()

    # watcher callbacks

    def _on_ready(self, images: list[str], sidecar_images: list[str]) -> None:
        if images:
            self._jobs.put(("process", images, "new"))
        if sidecar_images:
            self._jobs.put(("process", sidecar_images, "sidecar"))
        self._changed()

    def _on_mount(self, present: bool) -> None:
        was = self._mounted
        self._mounted = present
        if present:
            self._jobs.put(("scan", False))  # catch up on anything that arrived while away
        elif was:
            self.db.log("info", os.path.basename(self.settings.source) or "Source", None,
                        "source folder disconnected, waiting for it to return")
        self._changed()

    # worker

    def _work_loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            self._busy = True
            self._changed()
            try:
                self._run(job)
            except Exception as e:  # keep the worker alive whatever happens
                self.db.log("error", "Starport", None, f"internal error: {e}")
                self._error = str(e)
            finally:
                self._busy = False
                self._changed()

    def _run(self, job) -> None:
        kind = job[0]
        s = self.settings
        if kind == "process":
            if not self.can_run():
                return
            self._add_to_batch(self.ingestor.process(job[1], reason=job[2]))
        elif kind == "scan":
            forced = job[1]
            if not os.path.isdir(s.source):
                if forced:
                    self.db.log("error", os.path.basename(s.source) or "Source", None, "source folder not found")
                    self._error = "Source folder not found"
                return
            if not forced and not self.can_run():
                return
            paths = self.ingestor.scan(s.source, s.settle_seconds)
            self._add_to_batch(self.ingestor.process(paths))
            if forced:
                self._add_to_batch(self.ingestor.recheck())
                self._batch_debounce.flush()
        elif kind == "recheck":
            if self.can_run() and os.path.isdir(s.source):
                self._add_to_batch(self.ingestor.recheck())

    def _tick_loop(self) -> None:
        while not self._stop.wait(1.0):
            if time.monotonic() - self._last_recheck >= self.settings.recheck_minutes * 60:
                self._last_recheck = time.monotonic()
                self._jobs.put(("recheck",))
            self._changed(only_if_different=True)

    # batches

    def _add_to_batch(self, r: Result) -> None:
        if r.empty:
            return
        with self._batch_lock:
            if self._batch is None:
                self._batch = Result()
                self._batch_started = time.time()
            self._batch.add(r)
        self._batch_debounce.trigger()

    def _close_batch(self) -> None:
        with self._batch_lock:
            batch, self._batch = self._batch, None
            started = self._batch_started
        if batch is None or batch.empty:
            return
        self.db.add_batch(started, time.time(), batch.copied, batch.skipped, batch.errors)
        s = self.settings
        error = None
        if batch.copied and s.open_lrc:
            self.lightroom = lrc.find_lightroom()
            try:
                self.open_lightroom()
            except lrc.LightroomNotFound as e:
                self.db.log("error", "Lightroom Classic", None, "not found, files still copied")
                error = str(e)
                batch.errors += 1
        if batch.errors and error is None:
            error = f"{batch.errors} error{'s' if batch.errors != 1 else ''} in last batch"
        self._error = error
        if s.notifications:
            parts = [f"{batch.copied} copied", f"{batch.skipped} skipped"]
            if batch.errors:
                parts.append(f"{batch.errors} error{'s' if batch.errors != 1 else ''}")
            self.notify("Starport", " · ".join(parts))
        self._changed()

    # state for the UI

    def add_listener(self, fn: Callable[[], None]) -> None:
        self.listeners.append(fn)

    def _changed(self, only_if_different: bool = False) -> None:
        if only_if_different:
            sig = self._status()
            if sig == self._signature:
                return
            self._signature = sig
        else:
            self._signature = self._status()
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass

    def _status(self) -> tuple[str, str]:
        s = self.settings
        problems = s.problems()
        if not s.onboarded or (problems and not s.source):
            return "error", "Not set up"
        if problems:
            return "error", problems[0]
        if not self.exiftool:
            return "error", "ExifTool not found"
        if s.paused:
            return "paused", "Paused"
        if self._mounted is False:
            return "paused", "Source not connected"
        pending = self._watcher.pending_count() if self._watcher else 0
        if self._busy or pending or not self._jobs.empty():
            return "working", "Working…" if not pending else f"Waiting for {pending} file{'s' if pending != 1 else ''} to settle"
        if self._error:
            return "error", self._error
        return "idle", "Watching · 1 folder"

    def rule_text(self) -> str:
        s = self.settings
        if s.match_mode == "==":
            return f"Exactly {stars(s.min_rating)}"
        return "Only 5 stars" if s.min_rating == 5 else f"{stars(s.min_rating)} and up"

    def state(self) -> dict:
        s = self.settings
        status, label = self._status()
        last = self.db.last_batch()
        count, left = self.db.recheck_summary(s.recheck_window_hours * 3600)
        home = os.path.expanduser("~")

        def short(p: str) -> str:
            return "~" + p[len(home):] if p.startswith(home + os.sep) else p

        return {
            "status": status,
            "label": label,
            "settings": asdict(s),
            "rule": self.rule_text(),
            "source": short(s.source) if s.source else "",
            "staging": short(s.staging),
            "lastBatch": last and {
                "finished": last["finished"],
                "copied": last["copied"],
                "skipped": last["skipped"],
                "errors": last["errors"],
            },
            "recheck": {
                "count": count,
                "every": s.recheck_minutes,
                "hoursLeft": max(1, round(left / 3600)) if left and left > 0 else 0,
            },
            "exiftool": self.exiftool,
            "lightroom": self.lightroom,
            "db": self.db.path,
            "version": VERSION,
        }

    def events(self, limit: int = 200) -> list[dict]:
        return self.db.recent_events(limit)
