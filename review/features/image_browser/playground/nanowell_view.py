"""Playground — the nanowell-array window: what was found, drawn on the image, and the Analyze settings it implies.

A non-modal ``QDialog`` opened by the Playground controller when an array search finishes, reused across
searches like the threshold-curve window beside it. Three things, top to bottom:

* the image that was searched (a well's stitched montage, or the frame on screen) with every lattice site of the
  inferred array ringed — green where a nanowell was found, amber where the image shows the site dark — plus the
  array's outline, the central square and the address origin;
* the Analyze → Nanowells fields as a table: what to enter in each, or blank for the form's default, and why;
* the measurements behind them, and a **Copy** button that puts the whole suggestion on the clipboard.

What is drawn comes from :func:`overlay_marks`, a pure function of the suggestion, so the marks can be tested
without a screen; the painter only turns them into pixels.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui.theme import Colors

#: Mark kind -> colour token. Found and dark sites are told apart at a glance; the origin and the square are the
#: two marks addressing hangs on.
MARK_COLOURS = {"found": Colors.success, "dark": Colors.warn, "outline": Colors.text_muted,
                "square": Colors.accent2, "origin": Colors.accent}
#: The longest side the image is drawn at in the window.
DRAW_SIDE = 640


@dataclass(frozen=True)
class Mark:
    kind: str               # a key of MARK_COLOURS
    x: float                # image pixels, continuous (pixel i covers [i, i + 1))
    y: float
    radius: float


def overlay_marks(suggestion) -> list[Mark]:
    """The marks for *suggestion*, in image pixels: one ring per array site, the outline, square and origin."""
    marks: list[Mark] = []
    r = (suggestion.diameter_px or 0.0) / 2
    if suggestion.roster_px is not None:
        for (x, y), found in zip(suggestion.roster_px, suggestion.roster_found):
            marks.append(Mark("found" if found else "dark", float(x), float(y), r))
    elif suggestion.wells_px is not None:            # wells found, but no array: show what was found
        marks += [Mark("found", float(x), float(y), r) for x, y in suggestion.wells_px]
    if suggestion.radius_px and suggestion.centre_px is not None:
        marks.append(Mark("outline", *suggestion.centre_px, float(suggestion.radius_px)))
    if suggestion.square_px is not None:
        marks.append(Mark("square", *suggestion.square_px, r * 1.25))
    if suggestion.origin_px is not None:
        marks.append(Mark("origin", *suggestion.origin_px, max(r * 0.3, 2.0)))
    return marks


def _grey(image) -> np.ndarray:
    """*image* as contiguous uint8, stretched between its 1st and 99th percentiles (a montage is uint8 already)."""
    arr = np.asarray(image)
    if arr.dtype == np.uint8:
        return np.ascontiguousarray(arr)
    arr = arr.astype(np.float32)
    finite = arr[np.isfinite(arr)]
    low, high = (np.percentile(finite, [1, 99]) if finite.size else (0.0, 1.0))
    scaled = np.clip((np.nan_to_num(arr, nan=low) - low) / max(float(high - low), 1e-6), 0, 1)
    return np.ascontiguousarray((scaled * 255).astype(np.uint8))


def render_overlay(suggestion, side: int = DRAW_SIDE) -> QPixmap:
    """The searched image scaled so its longer side is *side* px, with :func:`overlay_marks` painted on it."""
    grey = _grey(suggestion.image)
    h, w = grey.shape
    zoom = side / max(h, w)
    image = QImage(grey.data, w, h, w, QImage.Format_Grayscale8).copy()
    pixmap = QPixmap.fromImage(image.scaled(max(int(w * zoom), 1), max(int(h * zoom), 1),
                                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    for mark in overlay_marks(suggestion):
        pen = QPen(QColor(MARK_COLOURS[mark.kind]))
        pen.setWidthF(2.0 if mark.kind in ("square", "origin", "outline") else 1.2)
        if mark.kind == "outline":
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        cx, cy, r = mark.x * zoom, mark.y * zoom, max(mark.radius * zoom, 1.0)
        if mark.kind == "square":
            painter.drawRect(int(cx - r), int(cy - r), int(2 * r), int(2 * r))
        else:
            if mark.kind == "origin":
                painter.setBrush(QColor(MARK_COLOURS["origin"]))
            painter.drawEllipse(int(cx - r), int(cy - r), int(2 * r), int(2 * r))
            painter.setBrush(Qt.NoBrush)
    painter.end()
    return pixmap


def _legend_html() -> str:
    words = {"found": "nanowell found", "dark": "site shown dark", "square": "central square",
             "origin": "address origin 0.0", "outline": "array outline"}
    return "  ".join(f'<span style="color:{MARK_COLOURS[k]}">●</span> {v}' for k, v in words.items())


def _fill_table(table: QTableWidget, suggestion) -> None:
    table.setRowCount(len(suggestion.fields))
    for row, f in enumerate(suggestion.fields):
        value = QTableWidgetItem(f.value or "(blank)")
        if not f.value:
            value.setForeground(QColor(Colors.text_muted))
        for col, item in enumerate((QTableWidgetItem(f.label), value, QTableWidgetItem(f.note))):
            item.setToolTip(f.note)
            table.setItem(row, col, item)
    table.resizeRowsToContents()


def _build_popup(ctx) -> QDialog:
    popup = QDialog(ctx.window)
    popup.setWindowTitle("Nanowell array")
    popup.resize(1180, 720)
    outer = QVBoxLayout(popup)
    outer.setContentsMargins(8, 8, 8, 8)
    popup._status = QLabel("", popup)
    popup._status.setWordWrap(True)
    popup._status.setTextInteractionFlags(Qt.TextSelectableByMouse)
    outer.addWidget(popup._status)
    split = QSplitter(Qt.Horizontal, popup)
    left = QWidget(split)
    ll = QVBoxLayout(left)
    ll.setContentsMargins(0, 0, 0, 0)
    popup._image = QLabel(left)
    popup._image.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    scroll = QScrollArea(left)
    scroll.setWidget(popup._image)
    scroll.setWidgetResizable(False)
    ll.addWidget(scroll, 1)
    legend = QLabel(_legend_html(), left)
    legend.setWordWrap(True)
    ll.addWidget(legend)
    right = QWidget(split)
    rl = QVBoxLayout(right)
    rl.setContentsMargins(0, 0, 0, 0)
    heading = QLabel("Analyze → Nanowells", right)
    heading.setObjectName("SectionTitle")
    rl.addWidget(heading)
    table = QTableWidget(0, 3, right)
    table.setHorizontalHeaderLabels(["Field", "Enter", "Why"])
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setWordWrap(True)
    header = table.horizontalHeader()
    header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.Stretch)
    popup._table = table
    rl.addWidget(table, 3)
    popup._facts = QLabel("", right)
    popup._facts.setWordWrap(True)
    popup._facts.setTextInteractionFlags(Qt.TextSelectableByMouse)
    popup._facts.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    facts_scroll = QScrollArea(right)
    facts_scroll.setWidgetResizable(True)
    facts_scroll.setWidget(popup._facts)
    rl.addWidget(facts_scroll, 2)
    buttons = QHBoxLayout()
    popup._copy = QPushButton("Copy settings", right)
    popup._copy.setToolTip("Put the suggested settings and the measurements behind them on the clipboard as text.")
    popup._copy.clicked.connect(lambda _=False: QApplication.clipboard().setText(popup._text))
    buttons.addWidget(popup._copy)
    buttons.addStretch(1)
    rl.addLayout(buttons)
    split.addWidget(left)
    split.addWidget(right)
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 1)
    outer.addWidget(split, 1)
    popup._text = ""
    popup.finished.connect(lambda _r: setattr(ctx.runtime.playground, "nanowell_popup", None))
    return popup


def show_nanowell_suggestion(ctx, suggestion) -> QDialog:
    """Open (or refill) the window for *suggestion*. A refusal is shown as its reason, with whatever was found."""
    popup = getattr(ctx.runtime.playground, "nanowell_popup", None)
    if popup is None or not popup.isVisible():
        popup = _build_popup(ctx)
        ctx.runtime.playground.nanowell_popup = popup
        popup.show()
    popup._text = suggestion.as_text()
    popup._status.setText(f"Searched {suggestion.source}." if suggestion.ok else
                          f"Searched {suggestion.source}: {suggestion.reason}")
    if suggestion.image is not None and np.asarray(suggestion.image).ndim == 2:
        pixmap = render_overlay(suggestion)
        popup._image.setPixmap(pixmap)
        popup._image.resize(pixmap.size())
    else:
        popup._image.clear()
    _fill_table(popup._table, suggestion)
    lines = [f"<b>{what}</b>: {value}" for what, value in suggestion.facts]
    lines += [f"<i>{note}</i>" for note in suggestion.notes]
    popup._facts.setText("<br>".join(lines))
    popup._copy.setEnabled(bool(suggestion.ok))
    popup.raise_()
    return popup


def build_nanowell_section(ctx, controls: QWidget, layout, row) -> None:
    """The dock's Nanowell array controls: the two sources, and the pixel size a lone frame needs.

    *row* is the dock's own label+cell row builder, so these rows line up with the rest of the dock.
    """
    from PySide6.QtWidgets import QFileDialog, QLineEdit

    from review.features.image_browser.playground.controller import (
        playground_nanowell_archive_dir, playground_nanowell_from_archive, playground_nanowell_from_frame,
    )

    note = QLabel("Find the nanowell array in one well and get the settings to type into Analyze → Nanowells.",
                  controls)
    note.setObjectName("Muted")
    note.setWordWrap(True)
    layout.addWidget(note)
    buttons = QWidget(controls)
    bl = QHBoxLayout(buttons)
    bl.setContentsMargins(0, 2, 0, 2)
    archive_btn = QPushButton("Well archive…", buttons)
    archive_btn.setToolTip(
        "Choose a raw well archive (<well>.zip, with the dataset's acquisition.json beside it, as in a dataset's in/ "
        "folder). Its tiles at the first timepoint are stitched as a run stitches them, the array is found in the "
        "montage, and the Nanowells settings it implies are shown. Opens on the loaded dataset's archive for the "
        "selected well, when there is one.")
    frame_btn = QPushButton("From this frame", buttons)
    frame_btn.setToolTip(
        "Search the raw frame on screen (a dataset frame, or an image opened with Open image…: one tile or an "
        "already-stitched well) at the pixel size given below.")
    bl.addWidget(archive_btn)
    bl.addWidget(frame_btn)
    bl.addStretch(1)
    layout.addWidget(buttons)
    px_tip = ("The frame's pixel size in µm, for From this frame. Every length the search reports — the pitch, the "
              "nanowell diameter — is measured in it, so a wrong one suggests the wrong plate format. A well archive "
              "reads its own from the acquisition record.")
    px_row = row(controls, "Pixel size (µm)", tooltip=px_tip)
    px_edit = QLineEdit("", px_row)
    px_edit.setPlaceholderText("µm / px")
    px_edit.setToolTip(px_tip)
    px_edit.setMaximumWidth(archive_btn.sizeHint().width())
    px_row.layout().addWidget(px_edit)
    px_row.layout().addStretch(1)
    ctx.widgets.register("_playground_nw_archive_btn", archive_btn)
    ctx.widgets.register("_playground_nw_frame_btn", frame_btn)
    ctx.widgets.register("_playground_nw_px_edit", px_edit)

    def _on_archive() -> None:
        folder, current = playground_nanowell_archive_dir(ctx)
        path, _filt = QFileDialog.getOpenFileName(controls, "Choose a nanowell well archive", current or folder,
                                                  "Well archives (*.zip);;All files (*)")
        if path:
            playground_nanowell_from_archive(ctx, path)

    archive_btn.clicked.connect(lambda _=False: _on_archive())
    frame_btn.clicked.connect(lambda _=False: playground_nanowell_from_frame(ctx))
