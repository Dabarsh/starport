"""Filter by rating, copy keepers (plus sidecars) into staging, record everything in the DB.

Safety: sources are only ever opened for reading. Staging files are created
with exclusive mode, so an existing file is never overwritten.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import rating as ratings
from db import DB
from settings import Settings


@dataclass
class Result:
    copied: int = 0
    skipped: int = 0
    errors: int = 0
    picked: int = 0  # previously skipped files copied after a re-check
    copied_paths: list[str] = field(default_factory=list)

    def add(self, other: "Result") -> None:
        self.copied += other.copied
        self.skipped += other.skipped
        self.errors += other.errors
        self.picked += other.picked
        self.copied_paths += other.copied_paths

    @property
    def empty(self) -> bool:
        return not (self.copied or self.skipped or self.errors)


def stars(n: int) -> str:
    return f"{n} star" + ("" if n == 1 else "s")


def matches(rating: int | None, s: Settings) -> bool:
    if rating is None or rating < 1:
        return False  # unrated, 0 stars and rejected never qualify
    return rating == s.min_rating if s.match_mode == "==" else rating >= s.min_rating


def skip_reason(rating: int | None, s: Settings) -> str:
    window = f"re-checking for {s.recheck_window_hours} h"
    if rating is None or rating == 0:
        return f"unrated, {window}"
    if rating < 0:
        return f"rejected, {window}"
    if s.match_mode == "==":
        return f"not exactly {stars(s.min_rating)}"
    return f"below {stars(s.min_rating)}"


def _stat(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return st


def copy_exclusive(src: Path, dest: Path) -> None:
    """Copy bytes and timestamps; raises FileExistsError instead of overwriting."""
    with open(src, "rb") as fin, open(dest, "xb") as fout:
        try:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
        except BaseException:
            fout.close()
            dest.unlink(missing_ok=True)
            raise
    shutil.copystat(src, dest)


def _plan_names(files: list[Path], sidecars: list[Path], prefix: str) -> dict[Path, str]:
    names = {f: prefix + f.name for f in files}
    stems = {f.stem.lower(): prefix + f.stem for f in files}
    for sc in sidecars:
        inner = Path(sc.stem)  # IMG_1.CR3.xmp -> IMG_1.CR3, IMG_1.xmp -> IMG_1
        if inner.suffix and inner.suffix.lower() in ratings.IMAGE_EXTS:
            names[sc] = prefix + inner.stem + inner.suffix + sc.suffix
        else:
            names[sc] = stems.get(sc.stem.lower(), prefix + sc.stem) + sc.suffix
    return names


def copy_group(files: list[Path], sidecars: list[Path], staging: Path) -> dict[Path, Path]:
    """Copy a group (pair + sidecars) under one shared name prefix so Lightroom keeps them together.

    Plain names when free; otherwise YYYYMMDD-HHMMSS_ (then -2, -3...) on every member.
    """
    attempt = 0
    while True:
        if attempt == 0:
            prefix = ""
        else:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            prefix = f"{stamp}_" if attempt == 1 else f"{stamp}-{attempt}_"
        names = _plan_names(files, sidecars, prefix)
        if any((staging / n).exists() for n in names.values()):
            attempt += 1
            continue
        done: dict[Path, Path] = {}
        try:
            for src, name in names.items():
                dest = staging / name
                copy_exclusive(src, dest)
                done[src] = dest
            return done
        except FileExistsError:
            # Lost a race with another writer: roll back only what we just created, try the next name.
            for dest in done.values():
                dest.unlink(missing_ok=True)
            attempt += 1
        except BaseException:
            for dest in done.values():
                dest.unlink(missing_ok=True)
            raise


class Ingestor:
    def __init__(self, db: DB, get_settings: Callable[[], Settings], exiftool: str | None = None):
        self.db = db
        self.get_settings = get_settings
        self.exiftool = exiftool

    # selection

    def _already_copied(self, path: Path, st) -> bool:
        row = self.db.get_file(str(path))
        if row and row.status == "copied":
            return True
        return self.db.copied_elsewhere(path.name, st.st_size, st.st_mtime)

    def _group_for(self, path: Path, settle: float) -> list[Path]:
        """The file plus same-stem image siblings (RAW + JPEG). Siblings still being written are left out."""
        try:
            entries = list(os.scandir(path.parent))
        except OSError:
            return [path]
        stem = path.stem.lower()
        now = time.time()
        group = [path]
        for e in entries:
            if e.name == path.name or not ratings.is_image(e.name) or Path(e.name).stem.lower() != stem:
                continue
            try:
                if now - e.stat().st_mtime < settle:
                    continue
            except OSError:
                continue
            group.append(Path(e.path))
        return group

    # processing

    def process(self, paths: list[str], reason: str = "new") -> Result:
        """reason: 'new' (watcher / scan), 'recheck' (timer), 'sidecar' (an .xmp changed)."""
        s = self.get_settings()
        result = Result()
        staging = Path(s.staging)

        seen: set[Path] = set()
        groups: list[list[Path]] = []
        for raw in paths:
            p = Path(raw)
            if p in seen or not ratings.is_image(p) or not p.is_file():
                continue
            group = self._group_for(p, s.settle_seconds) if s.copy_pairs else [p]
            group = [g for g in group if g not in seen]
            seen.update(group)
            if group:
                groups.append(group)
        if not groups:
            return result

        stats = {}
        to_read = []
        for group in groups:
            for f in group:
                st = _stat(f)
                if st is None:
                    continue
                stats[f] = st
                if not self._already_copied(f, st):
                    to_read.append(str(f))

        try:
            found = ratings.read_ratings(to_read, self.exiftool)
        except (ratings.ExifToolMissing, ratings.RatingReadError) as e:
            for f in to_read:
                p = Path(f)
                st = stats[p]
                prev = self.db.get_file(f)
                self.db.upsert_file(f, p.name, st.st_size, st.st_mtime, None, "error", detail=str(e))
                if not prev or prev.status != "error":
                    self.db.log("error", p.name, None, f"could not read rating: {e}")
                result.errors += 1
            return result

        for group in groups:
            members = [f for f in group if f in stats]
            pending = [f for f in members if str(f) in found]
            if not pending:
                continue
            group_ratings = {f: found[str(f)] for f in pending}
            for f in members:
                if f not in group_ratings:
                    row = self.db.get_file(str(f))
                    group_ratings[f] = row.rating if row else None

            if s.copy_pairs:
                hit = [f for f in members if matches(group_ratings[f], s)]
                keep = pending if hit else []
            else:
                hit = keep = [f for f in pending if matches(group_ratings[f], s)]

            for f in pending:
                if f not in keep:
                    self._skip(f, stats[f], group_ratings[f], s, result)

            if keep:
                partner_rating = max((group_ratings[f] for f in hit), default=None)
                self._copy(keep, members, stats, group_ratings, partner_rating, staging, s, reason, result)
        return result

    def _skip(self, f: Path, st, rating: int | None, s: Settings, result: Result) -> None:
        prev = self.db.get_file(str(f))
        detail = skip_reason(rating, s)
        self.db.upsert_file(str(f), f.name, st.st_size, st.st_mtime, rating, "skipped", detail=detail)
        if prev is None or prev.status != "skipped" or prev.rating != rating:
            self.db.log("skipped", f.name, rating, detail)
            result.skipped += 1

    def _copy(self, keep: list[Path], members: list[Path], stats, group_ratings, partner_rating,
              staging: Path, s: Settings, reason: str, result: Result) -> None:
        if not staging.is_dir():
            try:
                staging.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                for f in keep:
                    self._fail(f, stats[f], group_ratings[f], f"staging folder unavailable: {e}", result)
                return
            if self.db.last_batch() is not None:
                self.db.log("warning", staging.name, None, "staging folder was missing, recreated")

        sidecars: list[Path] = []
        if s.include_sidecars:
            for f in keep:
                for sc in ratings.sidecars_for(f):
                    row = self.db.get_file(str(sc))
                    if sc not in sidecars and not (row and row.status == "copied"):
                        sidecars.append(sc)  # a shared IMG_1.xmp goes over once, with the first of the pair

        try:
            placed = copy_group(keep, sidecars, staging)
        except OSError as e:
            for f in keep:
                self._fail(f, stats[f], group_ratings[f], f"copy failed: {e.strerror or e}", result)
            return

        for sc in sidecars:
            st = _stat(sc)
            if st:
                self.db.upsert_file(str(sc), sc.name, st.st_size, st.st_mtime, None, "copied", str(placed[sc]), "sidecar")

        for f in keep:
            prev = self.db.get_file(str(f))
            rating = group_ratings[f]
            dest = placed[f]
            if prev and prev.status in ("skipped", "error"):
                why = "rated after arrival" + (", sidecar changed" if reason == "sidecar" else "")
                self.db.log("recheck", f.name, rating, why)
                result.picked += 1
            if dest.name != f.name:
                detail = f"renamed {dest.name}"
            elif not matches(rating, s):
                detail = "RAW + JPEG pair"
                rating = rating if rating is not None else partner_rating
            else:
                raw_partner = f.suffix.lower() not in ratings.RAW_EXTS and any(
                    k.suffix.lower() in ratings.RAW_EXTS for k in keep)
                own = [] if raw_partner else [placed[sc].name for sc in ratings.sidecars_for(f) if sc in placed]
                detail = f"with sidecar {own[0]}" if own else ("RAW + JPEG pair" if raw_partner else "")
            st = stats[f]
            self.db.upsert_file(str(f), f.name, st.st_size, st.st_mtime, group_ratings[f], "copied", str(dest), detail)
            self.db.log("copied", f.name, rating, detail)
            result.copied += 1
            result.copied_paths.append(str(dest))

    def _fail(self, f: Path, st, rating, detail: str, result: Result) -> None:
        self.db.upsert_file(str(f), f.name, st.st_size, st.st_mtime, rating, "error", detail=detail)
        self.db.log("error", f.name, rating, detail)
        result.errors += 1

    # bulk

    def scan(self, root: str, settle: float) -> list[str]:
        """All settled images under root that have not been copied yet."""
        out = []
        now = time.time()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if not ratings.is_image(name):
                    continue
                p = os.path.join(dirpath, name)
                try:
                    if now - os.stat(p).st_mtime < settle:
                        continue
                except OSError:
                    continue
                row = self.db.get_file(p)
                if row and row.status == "copied":
                    continue
                out.append(p)
        return out

    def recheck(self) -> Result:
        s = self.get_settings()
        rows = self.db.pending_rechecks(s.recheck_window_hours * 3600)
        paths = [r.path for r in rows if os.path.exists(r.path)]
        result = Result()
        if paths:
            result = self.process(paths, reason="recheck")
            result.skipped = 0  # still-unrated files are not news
        return result
