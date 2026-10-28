"""Folder watching (FSEvents via watchdog) plus settle logic.

A file is handed on only after its size and mtime have not changed for
`settle` seconds, so half-copied files from a card are never read.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

import rating as ratings


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "Watcher"):
        self.watcher = watcher

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if event.event_type in ("created", "modified", "closed"):
            self.watcher.touch(event.src_path)
        elif event.event_type == "moved":
            self.watcher.touch(event.dest_path)


class Watcher:
    """Calls on_ready(images, sidecar_images) from a background thread with settled paths.

    on_mount(present: bool) fires when the source folder disappears or comes back
    (an external drive or card being unplugged / plugged in).
    """

    def __init__(self, root: str, settle: float,
                 on_ready: Callable[[list[str], list[str]], None],
                 on_mount: Callable[[bool], None] | None = None,
                 poll: float = 1.0):
        self.root = root
        self.settle = settle
        self.on_ready = on_ready
        self.on_mount = on_mount
        self.poll = poll
        self._pending: dict[str, tuple[int, float, float, bool]] = {}  # path -> size, mtime, stable_since, is_sidecar
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._observer: Observer | None = None
        self._thread: threading.Thread | None = None
        self.present = False

    # lifecycle

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="starport-settle", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._stop_observer()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _start_observer(self) -> bool:
        try:
            obs = Observer()
            obs.schedule(_Handler(self), self.root, recursive=True)
            obs.start()
        except OSError:
            return False
        self._observer = obs
        return True

    def _stop_observer(self) -> None:
        obs, self._observer = self._observer, None
        if obs:
            try:
                obs.stop()
                obs.join(timeout=3)
            except Exception:
                pass

    # events

    def touch(self, path: str) -> None:
        name = os.path.basename(path)
        if name.startswith("."):
            return  # ._ AppleDouble files on exFAT cards, .DS_Store, temp files
        sidecar = ratings.is_sidecar(path)
        if not sidecar and not ratings.is_image(path):
            return
        with self._lock:
            self._pending[path] = (-1, -1.0, time.monotonic(), sidecar)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def _check_settled(self) -> tuple[list[str], list[str]]:
        now = time.monotonic()
        images, sidecar_images = [], []
        with self._lock:
            for path, (size, mtime, since, sidecar) in list(self._pending.items()):
                try:
                    st = os.stat(path)
                except OSError:
                    del self._pending[path]  # deleted or renamed away
                    continue
                if st.st_size != size or st.st_mtime != mtime:
                    self._pending[path] = (st.st_size, st.st_mtime, now, sidecar)
                    continue
                if now - since < self.settle:
                    continue
                del self._pending[path]
                if sidecar:
                    sidecar_images += [str(p) for p in ratings.images_for_sidecar(path)]
                else:
                    images.append(path)
        return images, sidecar_images

    def _loop(self) -> None:
        last_mount_check = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_mount_check >= 3 or not self.present:
                last_mount_check = now
                present = os.path.isdir(self.root)
                if present and self._observer is None:
                    present = self._start_observer()
                elif not present and self._observer is not None:
                    self._stop_observer()
                    with self._lock:
                        self._pending.clear()
                if present != self.present:
                    self.present = present
                    if self.on_mount:
                        self.on_mount(present)
            if self.present:
                images, sidecar_images = self._check_settled()
                if images or sidecar_images:
                    self.on_ready(images, sidecar_images)
            self._stop.wait(self.poll)


def is_inside(child: str, parent: str) -> bool:
    try:
        return Path(child).resolve().is_relative_to(Path(parent).resolve())
    except OSError:
        return False
