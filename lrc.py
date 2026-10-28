"""Find and open Lightroom Classic, once per batch (debounced)."""

from __future__ import annotations

import glob
import subprocess
import threading
from typing import Callable

BUNDLE_IDS = ("com.adobe.LightroomClassicCC7",)
FALLBACK_GLOBS = (
    "/Applications/Adobe Lightroom Classic/Adobe Lightroom Classic.app",
    "/Applications/Adobe Lightroom Classic*/*.app",
    "/Applications/Adobe Lightroom*/Adobe Lightroom Classic*.app",
)


class LightroomNotFound(RuntimeError):
    pass


def find_lightroom() -> str | None:
    try:
        from AppKit import NSWorkspace

        ws = NSWorkspace.sharedWorkspace()
        for bid in BUNDLE_IDS:
            url = ws.URLForApplicationWithBundleIdentifier_(bid)
            if url is not None:
                return str(url.path())
    except ImportError:
        pass
    for pattern in FALLBACK_GLOBS:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


def open_lightroom() -> None:
    """`open` brings a running Lightroom forward instead of launching a second copy."""
    app = find_lightroom()
    if not app:
        raise LightroomNotFound("Lightroom Classic not found")
    proc = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise LightroomNotFound(proc.stderr.strip() or "could not open Lightroom Classic")


class Debouncer:
    """Run fn once, `delay` seconds after the last call to trigger()."""

    def __init__(self, delay: float, fn: Callable[[], None]):
        self.delay = delay
        self.fn = fn
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def trigger(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        """Fire now if something is waiting."""
        with self._lock:
            timer, self._timer = self._timer, None
        if timer:
            timer.cancel()
            self.fn()

    def cancel(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = None

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        self.fn()
