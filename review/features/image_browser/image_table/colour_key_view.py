"""The colour key under the live Image Table grid, as a label (``_colour_key`` draws it; ARCHITECTURE §6.2l)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel

from review.features.image_browser.image_table._colour_key import key_rgb
from review.features.image_browser.image_table.image_compose import _load_chrome_font, colour_keys


def add_colour_key(ctx, cells, grid, sz: int) -> None:
    """Put the colour key (``image_compose.colour_keys``) under the grid when it holds a nanowell metric map or
    fluorescence montage: a metric drawn as colours is unreadable without one."""
    cols = max((len(row) for row in cells), default=1)
    rgb = key_rgb(colour_keys(ctx, cells), width_px=max(_KEY_MIN_PX, sz * cols), font=_load_chrome_font)
    if rgb is None:
        return
    h, w = rgb.shape[:2]
    image = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format_RGB888).copy()
    label = QLabel()
    label.setObjectName("ImageTableColourKey")
    label.setPixmap(QPixmap.fromImage(image))
    label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
    grid.addWidget(label, grid.rowCount(), 0, 1, max(1, grid.columnCount()))


#: The colour key is at least this wide, so a one-column grid's key is not squeezed.
_KEY_MIN_PX = 520
