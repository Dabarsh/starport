"""Render resources/Starport.icns from the design's app icon: a star sinking into a dock.

    .venv/bin/python scripts/make_icon.py
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSCalibratedRGBColorSpace,
    NSColor,
    NSGraphicsContext,
    NSPNGFileType,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "resources" / "Starport.icns"
STAR = [(12, 3), (14.6, 8.4), (20.5, 9.2), (16.2, 13.3), (17.2, 19.1),
        (12, 16.4), (6.8, 19.1), (7.8, 13.3), (3.5, 9.2), (9.4, 8.4)]


def hex_color(h: str, alpha: float = 1.0) -> NSColor:
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, alpha)


def render(px: int, path: Path) -> None:
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    s = px / 1024.0  # macOS icon grid: 824 px body centred on a 1024 canvas

    body = ((100 * s, 100 * s), (824 * s, 824 * s))
    shape = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(body, 185 * s, 185 * s)
    hex_color("#1473E6").setFill()
    shape.fill()

    NSGraphicsContext.saveGraphicsState()
    shape.addClip()
    hex_color("#0F5CC0").setFill()
    NSBezierPath.fillRect_(((100 * s, 100 * s), (824 * s, 256 * s)))  # the dock band (y grows upward)
    NSGraphicsContext.restoreGraphicsState()

    # Star: 104/180 of the body, centred like the design.
    k = 824 * s * (104 / 180) / 24
    ox = 512 * s - 12 * k
    oy = 512 * s + 12 * k
    star = NSBezierPath.bezierPath()
    for i, (x, y) in enumerate(STAR):
        pt = (ox + x * k, oy - y * k)
        star.moveToPoint_(pt) if i == 0 else star.lineToPoint_(pt)
    star.closePath()
    star.setLineJoinStyle_(1)
    star.setLineWidth_(1.5 * k)
    NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.15).setFill()
    star.fill()
    NSColor.whiteColor().setStroke()
    star.stroke()

    NSGraphicsContext.restoreGraphicsState()
    path.write_bytes(bytes(rep.representationUsingType_properties_(NSPNGFileType, {})))


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    iconset = tmp / "Starport.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        render(size, iconset / f"icon_{size}x{size}.png")
        render(size * 2, iconset / f"icon_{size}x{size}@2x.png")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT)], check=True)
    shutil.copy(iconset / "icon_512x512@2x.png", tmp / "preview.png")
    print(f"{OUT}\npreview: {tmp / 'preview.png'}")


if __name__ == "__main__":
    main()
