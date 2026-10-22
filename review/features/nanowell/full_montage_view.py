"""The Full montage window: one well's registered montage at full resolution, with the anchor set by hand.

Opened from the Nanowells page's Array QC (**Full montage…**). It draws the well the page shows, at the timepoint
it shows, from the raw tiles where the run's registration placed them (:mod:`.full_montage_compute`), with the
tile outlines, every nanowell at its measured position and its address, and the anchor set for this timepoint.

**Moving around.** Drag to pan; the wheel zooms about the pointer; *Fit* shows the whole well, *1:1* one image
pixel per screen pixel. Tiles are read on a worker: the whole well at a quarter resolution when the window opens,
and the finer levels of the tiles in view when a zoom asks for them. Until they arrive the view is drawn from what
is held, so it never waits on a read.

**Setting the anchor.** With *Set anchor* on, a click (not a drag) sets this well's anchor at this timepoint to
the point clicked, in ``in/nanowell_anchors.json`` (``util.nanowell_anchors``): the run snaps it to the nearest
nanowell, so it need only land on the central square. *Clear anchor* removes it. The anchor is used when the well
is run again; *Re-run anchored wells…* opens Analyze on this dataset limited to the wells that have one
(``review.core.analyze_handoff.open_in_analyze``), each forced so all its timepoints are located again with it.

The window is not modal: it never blocks the app, and closing it changes nothing but the view.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from review.features.nanowell import full_montage_compute as fmc
from ui.theme import Colors

log = logging.getLogger("review")

#: The canvas's smallest size, so a window shrunk to nothing still has room to draw a message.
CANVAS_MIN_W, CANVAS_MIN_H = 320, 240
#: A press that moves less than this many pixels before release is a click, not a drag.
CLICK_SLOP_PX = 4
#: One wheel notch zooms by this factor.
WHEEL_STEP = 0.8
#: Addresses are written only where a nanowell is drawn at least this many pixels across.
MIN_LABEL_DIAMETER_PX = 34
#: The anchor's crosshair arm, in screen pixels.
ANCHOR_ARM_PX = 14
#: A nanowell's outline colour by the state the run recorded for it.
STATE_COLOURS = {"empty": Colors.success, "measured": Colors.accent, "occluded": Colors.warn,
                 "clipped": Colors.danger}


class MontageCanvas(QWidget):
    """The pixels and what is drawn over them. Knows nothing of files: the window hands it a store."""

    anchorClicked = Signal(float, float)         # well-frame µm
    tilesWanted = Signal(object)                 # {(tile id, level or None for its coarse levels only)}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(CANVAS_MIN_W, CANVAS_MIN_H)
        self.setMouseTracking(True)
        self.store: fmc.TileStore | None = None
        self.region: tuple | None = None
        self.sites: list[fmc.SiteOverlay] = []
        self.anchor: tuple[float, float] | None = None
        self.anchor_mode = False
        self.message = ""
        self._press = None
        self._last = None
        self._moved = False          # the user panned or zoomed: a resize keeps their view instead of refitting

    # ── what is shown ────────────────────────────────────────────────────────
    def show_store(self, store, sites, anchor, *, keep_region: bool = False) -> None:
        self.store, self.sites, self.anchor, self.message = store, sites, anchor, ""
        if not keep_region or self.region is None:
            self._moved = False
            self.fit()
        self.update()

    def show_message(self, text: str) -> None:
        self.store, self.message = None, text
        self.update()

    def fit(self) -> None:
        if self.store is not None:
            self.region = fmc.fit_region(self.store.source.extent_um(), max(self.width(), 1), max(self.height(), 1))
            self.update()

    def one_to_one(self) -> None:
        """One image pixel per screen pixel, about the centre of the view."""
        if self.store is None or self.region is None:
            return
        left, top, right, bottom = self.region
        px = self.store.source.pixel_size_um
        cx, cy = (left + right) / 2, (top + bottom) / 2
        half_w, half_h = self.width() * px / 2, self.height() * px / 2
        self.region = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
        self._moved = True
        self.update()

    def zoom(self, factor: float, at_view: tuple[float, float] | None = None) -> None:
        if self.region is None:
            return
        x, y = at_view if at_view is not None else (self.width() / 2, self.height() / 2)
        self.region = fmc.zoom_region(self.region, factor, self._well(x, y))
        self._moved = True
        self.update()

    def _well(self, x: float, y: float) -> tuple[float, float]:
        return fmc.to_well(self.region, self.width(), self.height(), x, y)

    def _view(self, x_um: float, y_um: float) -> QPointF:
        return QPointF(*fmc.to_view(self.region, self.width(), self.height(), x_um, y_um))

    # ── painting ─────────────────────────────────────────────────────────────
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(Colors.plot_bg))
        if self.store is None or self.region is None:
            painter.setPen(QColor(Colors.text_muted))
            painter.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.message)
            painter.end()
            return
        w, h = self.width(), self.height()
        rendered = self.store.render(self.region, w, h)
        pixels = rendered.image.copy(order="C")
        image = QImage(pixels.data, w, h, w, QImage.Format_Grayscale8)
        painter.drawImage(0, 0, image)
        self._draw_tiles(painter)
        self._draw_sites(painter)
        self._draw_anchor(painter)
        painter.end()
        wanted = {(tile, None) for tile in rendered.unread} | set(rendered.wants)
        if wanted:
            self.tilesWanted.emit(wanted)

    def _draw_tiles(self, painter) -> None:
        painter.setPen(QPen(QColor(Colors.text_muted), 1, Qt.DotLine))
        tw, th = self.store.source.tile_size_um
        for tile in self.store.source.tiles:
            corner = self._view(tile.left_um, tile.top_um)
            far = self._view(tile.left_um + tw, tile.top_um + th)
            painter.drawRect(QRectF(corner, far))

    def _draw_sites(self, painter) -> None:
        if not self.sites:
            return
        scale = self.width() / (self.region[2] - self.region[0])          # screen px per µm
        font = QFont(painter.font())
        painter.setFont(font)
        for site in self.sites:
            centre = self._view(site.x_um, site.y_um)
            radius = site.radius_um * scale
            painter.setPen(QPen(QColor(STATE_COLOURS.get(site.state, Colors.text_muted)), 1))
            painter.drawEllipse(centre, radius, radius)
            if site.label and 2 * radius >= MIN_LABEL_DIAMETER_PX:
                painter.drawText(QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius),
                                 Qt.AlignCenter, site.label)

    def _draw_anchor(self, painter) -> None:
        if self.anchor is None:
            return
        at = self._view(*self.anchor)
        painter.setPen(QPen(QColor(Colors.accent2), 2))
        painter.drawLine(QPointF(at.x() - ANCHOR_ARM_PX, at.y()), QPointF(at.x() + ANCHOR_ARM_PX, at.y()))
        painter.drawLine(QPointF(at.x(), at.y() - ANCHOR_ARM_PX), QPointF(at.x(), at.y() + ANCHOR_ARM_PX))
        painter.drawEllipse(at, ANCHOR_ARM_PX / 2, ANCHOR_ARM_PX / 2)

    # ── the mouse ────────────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press = self._last = event.position()

    def mouseMoveEvent(self, event) -> None:
        if self._press is None or self.region is None:
            return
        pos = event.position()
        dx, dy = pos.x() - self._last.x(), pos.y() - self._last.y()
        self._last = pos
        left, top, right, bottom = self.region
        sx, sy = (right - left) / self.width(), (bottom - top) / self.height()
        self.region = (left - dx * sx, top - dy * sy, right - dx * sx, bottom - dy * sy)
        self._moved = True
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        press, self._press = self._press, None
        if press is None or event.button() != Qt.LeftButton or self.region is None:
            return
        pos = event.position()
        moved = abs(pos.x() - press.x()) + abs(pos.y() - press.y())
        if self.anchor_mode and moved <= CLICK_SLOP_PX:
            self.anchorClicked.emit(*self._well(pos.x(), pos.y()))

    def wheelEvent(self, event) -> None:
        steps = event.angleDelta().y() / 120
        if steps:
            pos = event.position()
            self.zoom(WHEEL_STEP ** steps, (pos.x(), pos.y()))

    def resizeEvent(self, event) -> None:
        """Keep one µm per screen pixel on both axes: refit while the user has not moved the view, else keep its
        top-left corner and scale and show more or less of the well."""
        super().resizeEvent(event)
        if self.store is None:
            return
        old = event.oldSize()
        if self.region is None or not self._moved or old.width() <= 0:
            self.fit()
            return
        left, top, right, _bottom = self.region
        scale = (right - left) / old.width()
        self.region = (left, top, left + self.width() * scale, top + self.height() * scale)


class FullMontageWindow(QWidget):
    """The window around the canvas: the timepoint, the view buttons, the anchor and what the run said."""

    def __init__(self, ctx, data, metrics, well: str, timepoints, timepoint: str, parent=None):
        super().__init__(parent, Qt.Window)
        self.setObjectName("FullMontageWindow")
        self.setWindowTitle(f"Full montage — {well}")
        self.ctx, self.data, self.metrics, self.well = ctx, data, metrics, well
        self.store: fmc.TileStore | None = None
        self.source: fmc.MontageSource | None = None
        self._bridges: list = []
        self._pending: set = set()

        self.tp_cb = QComboBox()
        for tp in timepoints:
            self.tp_cb.addItem(tp, tp)
        self.tp_cb.setToolTip("The timepoint drawn.")
        fit_btn = QPushButton("Fit")
        fit_btn.setToolTip("Show the whole well.")
        one_btn = QPushButton("1:1")
        one_btn.setToolTip("One image pixel per screen pixel.")
        self.anchor_btn = QPushButton("Set anchor")
        self.anchor_btn.setCheckable(True)
        self.anchor_btn.setToolTip("On: click the central square to anchor this well at this timepoint. "
                                   "Drag still pans.")
        self.clear_btn = QPushButton("Clear anchor")
        self.clear_btn.setToolTip("Remove this well's anchor at this timepoint.")
        self.rerun_btn = QPushButton("Re-run anchored wells…")
        self.rerun_btn.setToolTip("Open Analyze on this dataset, limited to the wells with an anchor, each run "
                                  "again in full so its timepoints are located with the anchor.")
        bar = QHBoxLayout()
        for widget in (QLabel(f"Well {well}"), self.tp_cb, fit_btn, one_btn, self.anchor_btn, self.clear_btn):
            bar.addWidget(widget)
        bar.addStretch(1)
        bar.addWidget(self.rerun_btn)
        self.status = QLabel("")
        self.status.setObjectName("FullMontageStatus")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.canvas = MontageCanvas(self)
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.status)
        layout.addWidget(self.canvas, 1)

        fit_btn.clicked.connect(lambda _=False: self.canvas.fit())
        one_btn.clicked.connect(lambda _=False: self.canvas.one_to_one())
        self.anchor_btn.toggled.connect(self._anchor_mode)
        self.clear_btn.clicked.connect(lambda _=False: self.clear_anchor())
        self.rerun_btn.clicked.connect(lambda _=False: self.rerun())
        self.tp_cb.currentIndexChanged.connect(lambda _i: self.show_timepoint(self.tp_cb.currentData()))
        self.canvas.anchorClicked.connect(self.set_anchor)
        self.canvas.tilesWanted.connect(self._want)
        index = self.tp_cb.findData(timepoint)
        self.tp_cb.setCurrentIndex(max(index, 0))
        if index <= 0:                      # index 0 is already current, so no signal fires: show it here
            self.show_timepoint(self.tp_cb.currentData())

    # ── the timepoint ────────────────────────────────────────────────────────
    def show_timepoint(self, timepoint) -> None:
        if not timepoint:
            return
        self.timepoint = timepoint
        self._release()
        self.source, why = fmc.montage_source(self.data, self.well, timepoint)
        if self.source is None:
            self.store = None
            self.canvas.show_message(why)
            self.status.setText(why)
            self._refresh_anchor_controls()
            return
        self.store = fmc.TileStore(self.source)
        anchor, _problem = fmc.read_well_anchor(self.data, self.well, timepoint)
        self.canvas.show_store(self.store, fmc.site_overlays(self.metrics, self.well, timepoint), anchor)
        self._refresh_status()
        self._load([t.tile for t in self.source.tiles], level=None, first=True)

    def _release(self) -> None:
        """Stop the reads of the store shown so far and let its pixels go: another timepoint, or the window closing."""
        if self.store is not None:
            self.store.cancelled = True
        self.store = None
        self.canvas.store = None
        self._pending.clear()

    def closeEvent(self, event) -> None:
        self._release()
        super().closeEvent(event)

    def _refresh_status(self, extra: str = "") -> None:
        if self.source is None:
            return
        anchor, problem = fmc.read_well_anchor(self.data, self.well, self.timepoint)
        self.canvas.anchor = anchor
        text = fmc.status_text(self.source, anchor, problem)
        failed = self.store.failed if self.store is not None else {}
        if failed:
            text += (f"\n{len(failed)} tile(s) could not be read and are not drawn: "
                     + "; ".join(f"{tile} ({why})" for tile, why in sorted(failed.items())))
        self.status.setText(text + (f"\n{extra}" if extra else ""))
        self._refresh_anchor_controls(anchor)
        self.canvas.update()

    def _refresh_anchor_controls(self, anchor=None) -> None:
        can = (self.source is not None and fmc.anchors_file(self.data) is not None
               and self.source.status in fmc.ANCHORABLE)
        self.anchor_btn.setEnabled(can)
        self.clear_btn.setEnabled(can and anchor is not None)
        self.rerun_btn.setEnabled(bool(fmc.anchored_wells(self.data)))
        if not can:
            self.anchor_btn.setChecked(False)

    # ── reading tiles ────────────────────────────────────────────────────────
    def _want(self, wanted) -> None:
        """Read what the canvas asked for, once: a request already on its way is not sent again."""
        by_level: dict = {}
        for tile, level in set(wanted) - self._pending:
            by_level.setdefault(level, []).append(tile)
        for level, names in by_level.items():
            self._load(sorted(names), level=level)

    def _load(self, names, *, level, first: bool = False) -> None:
        from core.worker_bridge import run_off_thread

        store = self.store
        if store is None:
            return
        keys = {(name, level) for name in names}
        self._pending |= keys
        total = len(names)

        def job(reporter):
            for k, name in enumerate(names, 1):
                store.load([name], level=level)
                reporter.progress(k, total)
            return names

        def done(_loaded) -> None:
            if not self._alive() or store is not self.store:       # a timepoint since moved away from
                return
            self._pending -= keys
            if store is self.store:
                self._refresh_status()          # names any tile this read found unreadable
                self.canvas.update()

        def failed(message) -> None:
            if not self._alive() or store is not self.store:
                return
            self._pending -= keys
            log.warning("Full montage: tiles of %s could not be read: %s", self.well, message)
            if store is self.store:
                self._refresh_status(f"Some tiles could not be read: {message}")

        def progress(done_count, of) -> None:
            if first and self._alive() and store is self.store and done_count % max(of // 10, 1) == 0:
                self._refresh_status(f"Reading tiles: {done_count} of {of}…" if done_count < of else "")

        self._bridges = [b for b in self._bridges if b is not None][-8:]
        self._bridges.append(run_off_thread(job, on_done=done, on_failed=failed, on_progress=progress))

    def _alive(self) -> bool:
        """Whether this window and its canvas still exist: a tile read finishing after the window was closed and
        deleted delivers to nothing."""
        from shiboken6 import isValid

        return isValid(self) and isValid(self.canvas)

    # ── the anchor ───────────────────────────────────────────────────────────
    def _anchor_mode(self, on: bool) -> None:
        self.canvas.anchor_mode = bool(on)
        self.canvas.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)

    def set_anchor(self, x_um: float, y_um: float) -> None:
        from util.nanowell_anchors import AnchorsFileError, set_anchor

        path = fmc.anchors_file(self.data)
        if path is None or self.source is None:
            return
        try:
            set_anchor(path, self.well, self.timepoint, (x_um, y_um))
        except (AnchorsFileError, OSError) as exc:
            self._refresh_status(f"The anchor could not be saved: {exc}")
            return
        self._refresh_status()

    def clear_anchor(self) -> None:
        from util.nanowell_anchors import AnchorsFileError, clear_anchor

        path = fmc.anchors_file(self.data)
        if path is None:
            return
        try:
            clear_anchor(path, self.well, self.timepoint)
        except (AnchorsFileError, OSError) as exc:
            self._refresh_status(f"The anchor could not be removed: {exc}")
            return
        self._refresh_status()

    def rerun(self) -> bool:
        from review.core.analyze_handoff import open_in_analyze

        wells = fmc.anchored_wells(self.data)
        # Every well is forced, so Incremental adds nothing, and a remote nanowell run refuses it.
        if not wells or not open_in_analyze(self.ctx, force_wells=wells, only_wells=wells, incremental=False):
            self._refresh_status("Analyze could not be opened on this dataset from here.")
            return False
        return True


def open_full_montage(ctx, data, metrics, well: str, timepoints, timepoint: str) -> FullMontageWindow:
    """Open the Full montage window for *well*, replacing one already open; parented to Review's window so it
    closes with it."""
    previous = ctx.widgets.get_or_none("_nw_full_montage")
    if previous is not None:
        try:
            previous.close()
            previous.deleteLater()
        except RuntimeError:          # its C++ object went with an earlier window
            pass
    window = FullMontageWindow(ctx, data, metrics, well, timepoints, timepoint, parent=ctx.window)
    ctx.widgets.register("_nw_full_montage", window)
    window.resize(ctx.window.width() * 3 // 4, ctx.window.height() * 3 // 4)
    window.show()
    window.raise_()
    return window
