import hashlib
import os
import time
from pathlib import Path

import pytest
from conftest import make_jpeg, make_raw, needs_exiftool, xmp

from db import DB
from ingest import Ingestor, copy_group, matches
from settings import Settings

pytestmark = needs_exiftool


def digest(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime


@pytest.fixture
def env(tmp_path):
    src, stg = tmp_path / "card", tmp_path / "staging"
    src.mkdir()
    s = Settings(source_folder=str(src), staging_folder=str(stg), settle_seconds=1)
    db = DB(tmp_path / "t.db")
    return src, stg, s, db, Ingestor(db, lambda: s)


def names(folder: Path):
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def test_matches_modes():
    s = Settings(min_rating=4)
    assert [matches(r, s) for r in (None, -1, 0, 3, 4, 5)] == [False, False, False, False, True, True]
    s.match_mode = "=="
    assert [matches(r, s) for r in (3, 4, 5)] == [False, True, False]


def test_threshold_filter_and_originals_untouched(env):
    src, stg, s, db, ing = env
    files = [make_jpeg(src / f"r{n}.jpg", n) for n in (2, 4, 5)] + [make_jpeg(src / "none.jpg")]
    before = {p: digest(p) for p in files}
    r = ing.process([str(p) for p in files])
    assert (r.copied, r.skipped, r.errors) == (2, 2, 0)
    assert names(stg) == ["r4.jpg", "r5.jpg"]
    assert {p: digest(p) for p in files} == before
    assert names(src) == sorted(p.name for p in files)


def test_exact_mode(env):
    src, stg, s, db, ing = env
    s.match_mode = "=="
    files = [make_jpeg(src / f"r{n}.jpg", n) for n in (3, 4, 5)]
    ing.process([str(p) for p in files])
    assert names(stg) == ["r4.jpg"]


def test_no_double_copy(env):
    src, stg, s, db, ing = env
    p = make_jpeg(src / "a.jpg", 5)
    assert ing.process([str(p)]).copied == 1
    assert ing.process([str(p)]).copied == 0
    assert ing.process(ing.scan(str(src), 1)).copied == 0
    assert names(stg) == ["a.jpg"]


def test_same_file_on_new_mount_not_copied_again(env, tmp_path):
    src, stg, s, db, ing = env
    a = make_jpeg(src / "a.jpg", 5)
    ing.process([str(a)])
    other = tmp_path / "card2"
    other.mkdir()
    b = other / "a.jpg"
    b.write_bytes(a.read_bytes())
    os.utime(b, (a.stat().st_mtime, a.stat().st_mtime))
    assert ing.process([str(b)]).copied == 0


def test_name_clash_gets_timestamp_prefix_never_overwrites(env, tmp_path):
    src, stg, s, db, ing = env
    stg.mkdir()
    (stg / "IMG_1.jpg").write_bytes(b"someone else's file")
    (src / "d1").mkdir()
    p = make_jpeg(src / "d1" / "IMG_1.jpg", 5)
    ing.process([str(p)])
    assert (stg / "IMG_1.jpg").read_bytes() == b"someone else's file"
    renamed = [n for n in names(stg) if n != "IMG_1.jpg"]
    assert len(renamed) == 1 and renamed[0].endswith("_IMG_1.jpg") and len(renamed[0]) == len("20261022-141405_IMG_1.jpg")
    (src / "d2").mkdir()
    q = make_jpeg(src / "d2" / "IMG_1.jpg", 4, age=30)
    ing.process([str(q)])
    assert len(names(stg)) == 3


def test_raw_with_sidecar_copied_together(env):
    src, stg, s, db, ing = env
    raw = make_raw(src / "IMG_5.CR3", sidecar_stars=5)
    ing.process([str(raw)])
    assert names(stg) == ["IMG_5.CR3", "IMG_5.xmp"]
    s.include_sidecars = False
    raw2 = make_raw(src / "IMG_6.CR3", sidecar_stars=5)
    ing.process([str(raw2)])
    assert "IMG_6.xmp" not in names(stg)


def test_renamed_sidecar_keeps_pairing(env):
    src, stg, s, db, ing = env
    stg.mkdir()
    (stg / "IMG_7.CR3").write_bytes(b"x")
    raw = make_raw(src / "IMG_7.CR3", sidecar_stars=5)
    ing.process([str(raw)])
    new = sorted(n for n in names(stg) if n != "IMG_7.CR3")
    assert len(new) == 2
    assert new[0].endswith("_IMG_7.CR3") and new[1] == new[0][:-4] + ".xmp"


def test_raw_jpeg_pair(env):
    src, stg, s, db, ing = env
    raw = make_raw(src / "IMG_8.CR3", sidecar_stars=5)
    jpg = make_jpeg(src / "IMG_8.JPG", 2)  # JPEG itself below threshold
    ing.process([str(jpg)])
    assert names(stg) == ["IMG_8.CR3", "IMG_8.JPG", "IMG_8.xmp"]
    s.copy_pairs = False
    raw2 = make_raw(src / "IMG_9.CR3", sidecar_stars=5)
    jpg2 = make_jpeg(src / "IMG_9.JPG", 2)
    ing.process([str(jpg2), str(raw2)])
    assert "IMG_9.JPG" not in names(stg) and "IMG_9.CR3" in names(stg)


def test_recheck_picks_up_late_rating(env):
    src, stg, s, db, ing = env
    raw = make_raw(src / "IMG_10.CR3")
    r = ing.process([str(raw)])
    assert r.skipped == 1 and names(stg) == []
    assert ing.recheck().copied == 0
    (src / "IMG_10.xmp").write_text(xmp(4))
    r = ing.recheck()
    assert (r.copied, r.picked) == (1, 1)
    assert names(stg) == ["IMG_10.CR3", "IMG_10.xmp"]
    kinds = [e["kind"] for e in db.recent_events()]
    assert kinds[:3] == ["copied", "recheck", "skipped"]


def test_recheck_window_expires(env):
    src, stg, s, db, ing = env
    raw = make_raw(src / "IMG_11.CR3")
    ing.process([str(raw)])
    db._conn.execute("UPDATE files SET first_seen = ?", (time.time() - 25 * 3600,))
    (src / "IMG_11.xmp").write_text(xmp(5))
    assert ing.recheck().copied == 0


def test_staging_recreated(env):
    src, stg, s, db, ing = env
    a = make_jpeg(src / "a.jpg", 5)
    ing.process([str(a)])
    db.add_batch(0, 0, 1, 0, 0)
    for p in stg.iterdir():
        p.unlink()
    stg.rmdir()
    b = make_jpeg(src / "b.jpg", 5)
    ing.process([str(b)])
    assert names(stg) == ["b.jpg"]
    assert any(e["kind"] == "warning" for e in db.recent_events())


def test_scan_skips_unsettled_and_hidden(env):
    src, stg, s, db, ing = env
    make_jpeg(src / "old.jpg", 5)
    make_jpeg(src / "fresh.jpg", 5, age=0)
    make_jpeg(src / "._old.jpg", 5)
    assert [Path(p).name for p in ing.scan(str(src), 5)] == ["old.jpg"]


def test_copy_group_exclusive(tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"1")
    stg = tmp_path / "s"
    stg.mkdir()
    placed = copy_group([a], [], stg)
    assert placed[a].name == "a.jpg"
    placed2 = copy_group([a], [], stg)
    assert placed2[a].name != "a.jpg" and (stg / "a.jpg").read_bytes() == b"1"
