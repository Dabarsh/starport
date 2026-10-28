import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import rating  # noqa: E402

# Smallest valid baseline JPEG (1x1 px).
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////"
    "////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
)


def xmp(rating_value) -> str:
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f'<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="{rating_value}"/>'
        "</rdf:RDF></x:xmpmeta>"
    )


try:
    EXIFTOOL = rating.find_exiftool()
except rating.ExifToolMissing:
    EXIFTOOL = None

needs_exiftool = pytest.mark.skipif(EXIFTOOL is None, reason="ExifTool not installed")


def make_jpeg(path: Path, stars=None, age: float = 60) -> Path:
    path.write_bytes(TINY_JPEG)
    if stars is not None:
        subprocess.run([EXIFTOOL, "-q", "-overwrite_original", f"-XMP:Rating={stars}", str(path)], check=True)
    old = path.stat().st_mtime - age
    os.utime(path, (old, old))
    return path


def make_raw(path: Path, sidecar_stars=None, age: float = 60) -> Path:
    path.write_bytes(b"not really a raw file")
    if sidecar_stars is not None:
        path.with_suffix(".xmp").write_text(xmp(sidecar_stars))
    old = path.stat().st_mtime - age
    os.utime(path, (old, old))
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("STARPORT_HOME", str(tmp_path / "support"))
    return tmp_path
