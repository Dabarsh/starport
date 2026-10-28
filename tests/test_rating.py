from conftest import make_jpeg, make_raw, needs_exiftool, xmp

import rating


def test_parse_rating_values():
    assert rating.parse_rating(None) is None
    assert rating.parse_rating("") is None
    assert rating.parse_rating(True) is None
    for n in range(0, 6):
        assert rating.parse_rating(n) == n
        assert rating.parse_rating(str(n)) == n
    assert rating.parse_rating(-1) == -1
    assert rating.parse_rating("4.0") == 4
    assert rating.parse_rating(6) is None
    assert rating.parse_rating(-2) is None
    assert rating.parse_rating("high") is None


def test_parse_xmp_attribute_and_element():
    assert rating.parse_xmp(xmp(3)) == 3
    assert rating.parse_xmp("<xmp:Rating>5</xmp:Rating>") == 5
    assert rating.parse_xmp("<xmp:Rating> -1 </xmp:Rating>") == -1
    assert rating.parse_xmp("<x:xmpmeta/>") is None
    # MicrosoftPhoto:Rating is a percentage, never a star count
    assert rating.parse_xmp('MicrosoftPhoto:Rating="75"') is None


def test_sidecar_names(tmp_path):
    raw = make_raw(tmp_path / "IMG_1.CR3", sidecar_stars=4)
    assert [p.name for p in rating.sidecars_for(raw)] == ["IMG_1.xmp"]
    (tmp_path / "IMG_1.CR3.xmp").write_text(xmp(2))
    assert {p.name for p in rating.sidecars_for(raw)} == {"IMG_1.xmp", "IMG_1.CR3.xmp"}
    assert {p.name for p in rating.images_for_sidecar(tmp_path / "IMG_1.xmp")} == {"IMG_1.CR3"}
    assert rating.read_sidecar(raw) == 4


def test_missing_sidecar_and_unreadable_raw_is_unrated(tmp_path):
    raw = make_raw(tmp_path / "IMG_2.CR3")
    assert rating.read_sidecar(raw) is None


@needs_exiftool
def test_embedded_ratings(tmp_path):
    files = {n: make_jpeg(tmp_path / f"r{n}.jpg", n) for n in (0, 1, 3, 5)}
    files[-1] = make_jpeg(tmp_path / "rejected.jpg", -1)
    files[None] = make_jpeg(tmp_path / "none.jpg")
    raw = make_raw(tmp_path / "IMG_3.CR3")
    got = rating.read_ratings([str(p) for p in files.values()] + [str(raw)])
    for n, p in files.items():
        assert got[str(p)] == n, p.name
    assert got[str(raw)] is None


@needs_exiftool
def test_sidecar_vs_embedded_by_format(tmp_path):
    raw = make_raw(tmp_path / "IMG_4.CR3", sidecar_stars=5)
    rated_jpg = make_jpeg(tmp_path / "IMG_4.jpg", 1)
    unrated_jpg = make_jpeg(tmp_path / "IMG_5.jpg")
    (tmp_path / "IMG_5.xmp").write_text(xmp(3))
    got = rating.read_ratings([str(raw), str(rated_jpg), str(unrated_jpg)])
    assert got[str(raw)] == 5  # RAW: sidecar
    assert got[str(rated_jpg)] == 1  # JPEG: embedded beats the shared sidecar
    assert got[str(unrated_jpg)] == 3  # JPEG with nothing embedded falls back to sidecar
