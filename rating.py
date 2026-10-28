"""Read star ratings: sidecar .xmp first, then embedded metadata via ExifTool.

Returns -1 (rejected), 0 to 5, or None (unrated / unreadable).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

RAW_EXTS = {
    ".cr2", ".cr3", ".crw", ".arw", ".srf", ".sr2", ".nef", ".nrw", ".dng", ".raf", ".orf",
    ".rw2", ".pef", ".srw", ".x3f", ".3fr", ".iiq", ".erf", ".mef", ".mos", ".kdc", ".rwl",
}
RENDERED_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff", ".png"}
IMAGE_EXTS = RAW_EXTS | RENDERED_EXTS
SIDECAR_EXT = ".xmp"

_XMP_RATING = re.compile(
    r"""xmp:Rating\s*=\s*["']\s*(-?\d+(?:\.\d+)?)\s*["']|<xmp:Rating>\s*(-?\d+(?:\.\d+)?)\s*</xmp:Rating>"""
)


class ExifToolMissing(RuntimeError):
    pass


class RatingReadError(RuntimeError):
    pass


def is_image(path: str | Path) -> bool:
    p = Path(path)
    return p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")


def is_sidecar(path: str | Path) -> bool:
    p = Path(path)
    return p.suffix.lower() == SIDECAR_EXT and not p.name.startswith(".")


def parse_rating(value) -> int | None:
    """Normalize a raw rating value to -1..5, or None when absent or out of range."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        n = round(float(value))
    except (TypeError, ValueError):
        return None
    return n if -1 <= n <= 5 else None


def parse_xmp(text: str) -> int | None:
    m = _XMP_RATING.search(text)
    if not m:
        return None
    return parse_rating(m.group(1) or m.group(2))


def sidecars_for(image: str | Path) -> list[Path]:
    """Existing sidecars for an image: IMG_1.xmp (Lightroom, Bridge, Photo Mechanic) and IMG_1.CR3.xmp."""
    p = Path(image)
    try:
        siblings = {e.name.lower(): e.name for e in os.scandir(p.parent) if e.is_file()}
    except OSError:
        return []
    out = []
    for candidate in (p.stem + SIDECAR_EXT, p.name + SIDECAR_EXT):
        real = siblings.get(candidate.lower())
        if real and not real.startswith("."):
            out.append(p.parent / real)
    return out


def images_for_sidecar(sidecar: str | Path) -> list[Path]:
    """Images a sidecar belongs to (same stem, or IMG_1.CR3.xmp -> IMG_1.CR3)."""
    s = Path(sidecar)
    stem = s.stem.lower()
    try:
        entries = [e for e in os.scandir(s.parent) if e.is_file()]
    except OSError:
        return []
    out = []
    for e in entries:
        if not is_image(e.name):
            continue
        if Path(e.name).stem.lower() == stem or e.name.lower() == stem:
            out.append(s.parent / e.name)
    return out


def read_sidecar(image: str | Path) -> int | None:
    for sc in sidecars_for(image):
        try:
            rating = parse_xmp(sc.read_text(errors="replace"))
        except OSError:
            continue
        if rating is not None:
            return rating
    return None


def find_exiftool() -> str:
    """Bundled copy first (resources/exiftool, or inside the .app), then PATH and Homebrew."""
    candidates = [HERE / "resources" / "exiftool" / "exiftool"]
    if getattr(sys, "frozen", False):
        res = os.environ.get("RESOURCEPATH")
        if res:
            candidates.insert(0, Path(res) / "exiftool" / "exiftool")
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    found = shutil.which("exiftool")
    if found:
        return found
    for c in ("/opt/homebrew/bin/exiftool", "/usr/local/bin/exiftool"):
        if os.access(c, os.X_OK):
            return c
    raise ExifToolMissing("ExifTool not found")


def read_embedded(paths: list[str], exiftool: str | None = None, chunk: int = 200) -> dict[str, int | None]:
    """Embedded XMP/EXIF Rating for many files in few ExifTool calls."""
    if not paths:
        return {}
    exe = exiftool or find_exiftool()
    out: dict[str, int | None] = {p: None for p in paths}
    for i in range(0, len(paths), chunk):
        part = paths[i:i + chunk]
        cmd = [exe, "-j", "-n", "-q", "-q", "-fast2", "-charset", "filename=utf8", "-Rating", "-@", "-"]
        try:
            proc = subprocess.run(cmd, input="\n".join(part), capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RatingReadError(str(e)) from e
        if not proc.stdout.strip():
            if proc.returncode not in (0, 1):
                raise RatingReadError(proc.stderr.strip() or f"exiftool exited {proc.returncode}")
            continue
        try:
            items = json.loads(proc.stdout)
        except ValueError as e:
            raise RatingReadError("unreadable ExifTool output") from e
        for item in items:
            src = item.get("SourceFile")
            if src in out:
                out[src] = parse_rating(item.get("Rating"))
    return out


def read_ratings(paths: list[str], exiftool: str | None = None) -> dict[str, int | None]:
    """RAW: sidecar wins (where Lightroom, Bridge and Photo Mechanic write RAW ratings), then embedded.
    JPEG/HEIC/TIFF: embedded wins (those apps write into the file), then sidecar. A shared IMG_1.xmp
    next to a RAW + JPEG pair therefore rates the RAW, not the JPEG.
    """
    result: dict[str, int | None] = {}
    need_embedded = []
    for p in paths:
        r = read_sidecar(p) if Path(p).suffix.lower() in RAW_EXTS else None
        if r is None:
            need_embedded.append(p)
        else:
            result[p] = r
    result.update(read_embedded(need_embedded, exiftool))
    for p in need_embedded:
        if result.get(p) is None and Path(p).suffix.lower() not in RAW_EXTS:
            result[p] = read_sidecar(p)
    return result
