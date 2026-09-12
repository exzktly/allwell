"""Reusable rubber-band rectangle for the review image.

The Segmentation review image supports two box-drag interactions that share the
exact same mechanics — arm a ``QRubberBand`` on press, resize it on drag, and on
release convert the label-local rectangle into mask-pixel coordinates. Box-delete
(bulk-exclude cells inside) and crop-region (keep cells whose centroid is inside)
differ only in what they do with the final rectangle, so the drag itself lives
here once and both call it.

Anchor / active state lives on ``ReviewImageState`` (``box_anchor`` / ``box_active``)
and the band handle on ``app._review_image_rubber_band``, so the helpers are
stateless apart from those.
"""

from __future__ import annotations


# A drag shorter than this many label-local pixels in either axis is a tap, not a
# box — the caller decides what a tap means (single-cell click in delete mode,
# no-op in crop mode).
_CLICK_PX_THRESH = 3


def box_select_begin(ctx, event) -> None:
    """Arm a rubber-band drag anchored at the event's label-local position."""
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QRubberBand

    ri = ctx.state.review_image
    pos = event.position()
    ax, ay = float(pos.x()), float(pos.y())
    ri.box_anchor = (ax, ay)
    ri.box_active = True
    label = ctx.runtime.review_image.label
    if label is not None:
        rb = ctx.runtime.review_image.rubber_band
        if rb is None:
            rb = QRubberBand(QRubberBand.Rectangle, label)
            ctx.runtime.review_image.rubber_band = rb
        rb.setGeometry(QRect(int(ax), int(ay), 1, 1))
        rb.show()


def box_select_update(ctx, event) -> None:
    """Resize the active rubber band to the current cursor position."""
    from PySide6.QtCore import QRect

    ri = ctx.state.review_image
    anchor = ri.box_anchor
    rb = ctx.runtime.review_image.rubber_band
    if anchor is None or rb is None:
        return
    ax, ay = anchor
    pos = event.position()
    cx, cy = float(pos.x()), float(pos.y())
    x0 = int(min(ax, cx))
    y0 = int(min(ay, cy))
    w = max(1, int(abs(cx - ax)))
    h = max(1, int(abs(cy - ay)))
    rb.setGeometry(QRect(x0, y0, w, h))


def box_select_finish(ctx, event) -> tuple[int, int, int, int] | None:
    """Finalize the drag and return ``(mx0, my0, mx1, my1)`` in mask-array pixel
    space, or ``None`` for a tap / too-small drag.

    Always clears the box state and hides the band. The conversion from
    label-local pixels accounts for the current ``_review_image_scale`` and the
    pixmap-centering offset, so window position, scroll, and zoom are handled.
    """
    ri = ctx.state.review_image
    anchor = ri.box_anchor
    rb = ctx.runtime.review_image.rubber_band
    ri.box_active = False
    ri.box_anchor = None
    if rb is not None:
        rb.hide()
    if anchor is None:
        return None
    ax, ay = anchor
    pos = event.position()
    rx, ry = float(pos.x()), float(pos.y())
    if abs(rx - ax) < _CLICK_PX_THRESH and abs(ry - ay) < _CLICK_PX_THRESH:
        return None
    scale = float(ctx.runtime.review_image.scale or 1.0)
    label = ctx.runtime.review_image.label
    off_x, off_y = 0, 0
    if label is not None:
        pm = label.pixmap()
        if pm is not None and not pm.isNull():
            off_x = max(0, (label.width() - pm.width()) // 2)
            off_y = max(0, (label.height() - pm.height()) // 2)
    mx0, mx1 = int((ax - off_x) / scale), int((rx - off_x) / scale)
    my0, my1 = int((ay - off_y) / scale), int((ry - off_y) / scale)
    return (mx0, my0, mx1, my1)
