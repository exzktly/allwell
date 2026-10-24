"""How a nanowell is drawn over a montage: the colour of each state, and its outline with its address.

Two views draw the nanowells of one well over its montage, at two moments of the same work. Analyze's **Test
detection** window (``analyze/nanowell_preview.py``) draws them before a run, from one timepoint analysed on the
spot, painting each with :func:`paint_site`; the **Array view** of Review's Nanowells page
(``review/features/nanowell/renderer.render_array``) draws them after one, from what the run recorded, in
matplotlib with the same colours, outline width, fused dash, label colour and label threshold. A person checks
the first against the second, so a state has to read the same in both. Each view used to carry its own palette,
label threshold and outline painter, and they had already drifted: Review's full-resolution window had no colour
for a fused site at all, drew outlines a third thinner, and wrote addresses in the state's colour rather than the
one Test detection had chosen to stay legible over a dark montage.

This module is the one copy. :data:`STATE_COLOURS` is keyed on the ``state`` the pipeline records for a site
(``pipeline/nanowell_run.py``), plus the two the Test detection window derives for itself — ``unaddressed`` (a
site found where no address could be given) and ``uncounted`` (seen, but the segmenter could not be loaded, so
"holding cells" would be a claim nobody measured). Any state not named — ``absent``, ``not_located`` — is drawn
in the muted text colour by :func:`state_colour`, never left uncoloured.

Part of the removable Nanowells feature (``profiles/full.toml``): its only importers are that feature's modules.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPen

from ui.theme import Colors

#: A site's outline colour by the state recorded (or, in Test detection, about to be recorded) for it.
STATE_COLOURS = {"empty": Colors.success, "measured": Colors.accent, "occluded": Colors.warn,
                 "clipped": Colors.danger, "unaddressed": Colors.text_muted, "uncounted": Colors.text_secondary}
#: The outline of a fused site (on a tile seam, measured from a blended crop), drawn dashed in place of its state's.
FUSED_COLOUR = Colors.accent2
#: An address is written only inside a nanowell drawn at least this many screen pixels across.
MIN_LABEL_DIAMETER_PX = 34
#: The width of a site's outline, in screen pixels.
OUTLINE_WIDTH_PX = 1.5
#: The colour of an address written inside its outline: legible over any part of a greyscale montage.
LABEL_COLOUR = Colors.brand_fg
#: A counted cell's dot on the Array view: light with a dark rim, so it reads over a greyscale image and over any
#: colour a nanowell is filled with, and is not mistaken for a state's colour.
CELL_COLOUR, CELL_EDGE_COLOUR = Colors.ink_light, Colors.ink_dark


def state_colour(state: str) -> str:
    """The outline colour of *state*; the muted text colour for a state with none of its own."""
    return STATE_COLOURS.get(state, Colors.text_muted)


def paint_site(painter, centre: QPointF, radius: float, state: str, label: str = "", *,
               fused: bool = False, show_label: bool = True) -> None:
    """Outline one nanowell at *centre* (screen pixels) and write *label* inside it where there is room.

    A fused site is dashed in :data:`FUSED_COLOUR` whatever its state, as the Test detection legend says. The label
    is drawn only when *show_label* is on and the outline is at least :data:`MIN_LABEL_DIAMETER_PX` across, so a
    zoomed-out view is not a field of overlapping text.
    """
    pen = QPen(QColor(FUSED_COLOUR if fused else state_colour(state)))
    pen.setWidthF(OUTLINE_WIDTH_PX)
    if fused:
        pen.setStyle(Qt.DashLine)
    painter.setPen(pen)
    painter.drawEllipse(centre, radius, radius)
    if show_label and label and 2 * radius >= MIN_LABEL_DIAMETER_PX:
        painter.setPen(QColor(LABEL_COLOUR))
        painter.drawText(QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius),
                         Qt.AlignCenter, label)


def gray_qimage(pixels) -> QImage:
    """An 8-bit greyscale array (rows × columns) as a ``QImage`` that owns its pixels.

    A ``QImage`` built over an array's buffer reads that buffer for as long as the image lives, and the array can be
    gone by the time it is drawn; the copy makes the image independent of it.
    """
    data = np.ascontiguousarray(pixels, dtype=np.uint8)
    height, width = data.shape[:2]
    return QImage(data.data, width, height, width, QImage.Format_Grayscale8).copy()
