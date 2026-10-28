"""Menu bar template icons: idle, working (dashed, animated), paused, error. Drawn, not bundled."""

from __future__ import annotations

from AppKit import (
    NSBezierPath,
    NSColor,
    NSCompositingOperationClear,
    NSCompositingOperationSourceOver,
    NSGraphicsContext,
    NSImage,
    NSMakeRect,
    NSRectFill,
)

SIZE = 18.0
SCALE = SIZE / 24.0
STAR = [(12, 3), (14.6, 8.4), (20.5, 9.2), (16.2, 13.3), (17.2, 19.1),
        (12, 16.4), (6.8, 19.1), (7.8, 13.3), (3.5, 9.2), (9.4, 8.4)]


def _pt(x: float, y: float) -> tuple[float, float]:
    return (x * SCALE, y * SCALE)


def status_image(state: str, phase: float = 0.0) -> NSImage:
    def draw(rect):
        NSColor.blackColor().set()
        star = NSBezierPath.bezierPath()
        star.moveToPoint_(_pt(*STAR[0]))
        for p in STAR[1:]:
            star.lineToPoint_(_pt(*p))
        star.closePath()
        star.setLineWidth_(1.8 * SCALE)
        star.setLineJoinStyle_(1)  # round
        if state == "working":
            star.setLineDash_count_phase_([3 * SCALE, 2.2 * SCALE], 2, phase)
        star.stroke()

        ctx = NSGraphicsContext.currentContext()
        if state == "paused":
            ctx.setCompositingOperation_(NSCompositingOperationClear)
            NSRectFill(NSMakeRect(*_pt(16, 13.6), 7.4 * SCALE, 9.2 * SCALE))
            ctx.setCompositingOperation_(NSCompositingOperationSourceOver)
            bars = NSBezierPath.bezierPath()
            for x in (18, 21.5):
                bars.moveToPoint_(_pt(x, 15))
                bars.lineToPoint_(_pt(x, 21.5))
            bars.setLineWidth_(2.4 * SCALE)
            bars.setLineCapStyle_(1)
            bars.stroke()
        elif state == "error":
            ctx.setCompositingOperation_(NSCompositingOperationClear)
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(*_pt(14, 13), 11 * SCALE, 11 * SCALE)).fill()
            ctx.setCompositingOperation_(NSCompositingOperationSourceOver)
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(*_pt(15.2, 14.2), 8.6 * SCALE, 8.6 * SCALE)).fill()
            ctx.setCompositingOperation_(NSCompositingOperationClear)
            mark = NSBezierPath.bezierPath()
            mark.moveToPoint_(_pt(19.5, 16.2))
            mark.lineToPoint_(_pt(19.5, 18.9))
            mark.moveToPoint_(_pt(19.5, 20.6))
            mark.lineToPoint_(_pt(19.5, 20.7))
            mark.setLineWidth_(1.7 * SCALE)
            mark.setLineCapStyle_(1)
            mark.stroke()
            ctx.setCompositingOperation_(NSCompositingOperationSourceOver)
        return True

    img = NSImage.imageWithSize_flipped_drawingHandler_((SIZE, SIZE), True, draw)
    img.setTemplate_(True)
    img.setAccessibilityDescription_(f"Starport, {state}")
    return img
