"""Settings: defaults, load/save as JSON in the app support folder."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_NAME = "Starport"


def support_dir() -> Path:
    """~/Library/Application Support/Starport, or $STARPORT_HOME when set (tests, dev)."""
    override = os.environ.get("STARPORT_HOME")
    base = Path(override).expanduser() if override else Path.home() / "Library" / "Application Support" / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def expand(path: str) -> str:
    return str(Path(path).expanduser()) if path else ""


@dataclass
class Settings:
    source_folder: str = ""
    staging_folder: str = "~/Pictures/Starport_Staging"
    min_rating: int = 4
    match_mode: str = ">="  # ">=" or "=="
    include_sidecars: bool = True
    copy_pairs: bool = True  # RAW + JPEG: copy both if either matches
    settle_seconds: int = 5
    recheck_minutes: int = 5
    recheck_window_hours: int = 24
    open_lrc: bool = True
    lrc_debounce_seconds: int = 30
    launch_at_login: bool = True
    notifications: bool = True
    paused: bool = False
    onboarded: bool = False

    @property
    def source(self) -> str:
        return expand(self.source_folder)

    @property
    def staging(self) -> str:
        return expand(self.staging_folder)

    def validate(self) -> None:
        """Clamp values into range so a hand-edited file can't break the app."""
        self.min_rating = min(5, max(1, int(self.min_rating)))
        if self.match_mode not in (">=", "=="):
            self.match_mode = ">="
        self.settle_seconds = min(600, max(1, int(self.settle_seconds)))
        self.recheck_minutes = min(1440, max(1, int(self.recheck_minutes)))
        self.recheck_window_hours = min(24 * 30, max(1, int(self.recheck_window_hours)))
        self.lrc_debounce_seconds = min(3600, max(1, int(self.lrc_debounce_seconds)))
        if not self.staging_folder:
            self.staging_folder = Settings.staging_folder

    def problems(self) -> list[str]:
        """Configuration errors that stop watching."""
        out = []
        if not self.source:
            out.append("Choose a source folder")
            return out
        src, stg = Path(self.source).resolve(), Path(self.staging).resolve()
        if src == stg:
            out.append("Source and staging must be different folders")
        elif stg.is_relative_to(src):
            out.append("Staging folder can't be inside the source folder")
        elif src.is_relative_to(stg):
            out.append("Source folder can't be inside the staging folder")
        return out


_lock = threading.Lock()


def settings_path() -> Path:
    return support_dir() / "settings.json"


def load() -> Settings:
    path = settings_path()
    s = Settings()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    known = {f.name for f in fields(Settings)}
    for key, value in data.items():
        if key in known:
            setattr(s, key, value)
    try:
        s.validate()
    except (TypeError, ValueError):
        s = Settings()
    return s


def save(s: Settings) -> None:
    s.validate()
    path = settings_path()
    tmp = path.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(asdict(s), indent=2))
        os.replace(tmp, path)
