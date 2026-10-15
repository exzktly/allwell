"""The Test detection window: one well at one timepoint, with every nanowell's address drawn on the montage.

Nobody should start a plate-wide run without seeing the addresses land. The analysis is
:func:`pipeline.nanowell_preview.preview_timepoint`, run on a worker thread because registration and detection
on a real well take a while; this module is the window around it and the marks drawn on the montage.

What is drawn comes from :func:`site_marks`, a pure function of the result, so the colours and what is
labelled can be tested without a screen. The painter only turns marks into pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ui.theme import Colors

#: A site's outline colour by what the run would record for it. Fused is drawn dashed over whichever applies.
STATE_COLOURS = {"empty": Colors.success, "measured": Colors.accent, "occluded": Colors.warn,
                 "clipped": Colors.danger, "unaddressed": Colors.text_muted}
#: The dashed outline of a fused site (on a seam, measured from a blended crop).
FUSED_COLOUR = Colors.accent2
#: Zoom steps offered; addresses are written only where a nanowell is drawn at least this many screen pixels across.
ZOOMS = (("Fit", 0.0), ("50 %", 0.5), ("100 %", 1.0), ("200 %", 2.0), ("400 %", 4.0))
MIN_LABEL_DIAMETER_PX = 34
#: The longest side the montage is drawn at; a larger zoom of a large montage would ask Qt for a pixmap it cannot make.
MAX_PIXMAP_SIDE = 12000


@dataclass(frozen=True)
class SiteMark:
    x: float                 # montage pixels
    y: float
    radius: float
    colour: str              # a key of STATE_COLOURS
    text: str                # the address, "" for an unaddressed site
    fused: bool
    count: int | None        # cells, None where there is no count


def site_marks(result) -> list[SiteMark]:
    """The marks for a preview result's sites, in montage pixels. Empty when there is no montage.

    Positions are continuous coordinates (micrometres over the pixel size): pixel i covers [i, i+1), so a site at
    the montage origin sits at 0.0, the pixmap's top-left corner, with no half-pixel offset.
    """
    if result.montage is None or not result.um_per_px:
        return []
    scale = result.um_per_px
    marks = []
    for site in result.sites:
        colour = site["state"] if site["address"] is not None else "unaddressed"
        marks.append(SiteMark((site["well_x_um"] - result.origin_um[0]) / scale,
                              (site["well_y_um"] - result.origin_um[1]) / scale,
                              site["radius_um"] / scale, colour, site["address"] or "", bool(site["fused"]),
                              site["n_cells"]))
    return marks


def summary_text(result) -> str:
    """The result as the few sentences a person checks: status, origin, overlap against what is needed, state counts."""
    if result.status != "ok" and not result.sites:
        return f"{result.status}: {result.reason}" if result.reason else result.status
    lines = [f"{result.well} at {result.timepoint}: {result.tile_count} tile(s), "
             f"pitch {result.pitch_um:.1f} µm, lattice angle {result.angle_deg:.1f}°."]
    if result.status == "ok":
        lines.append(f"Origin found from the {str(result.origin_method).replace('_', ' ')} "
                     f"({result.origin_agreement or 'single source'}).")
    else:
        lines.append(f"Not addressed: {str(result.reason).rstrip('.')}. The sites found are drawn without addresses.")
    overlap = [f"{o:.0f}" if o is not None else "-" for o in result.overlap_um]
    short = any(o is not None and o < result.overlap_needed_um for o in result.overlap_um)
    lines.append(f"Overlap between tiles {overlap[0]} × {overlap[1]} µm against the {result.overlap_needed_um:.0f} µm a "
                 f"nanowell needs" + (": too short, so nanowells on the seams are fused (measured from a blended "
                                       "crop, with no cell count)." if short else "."))
    counts = result.counts()
    lines.append("Sites: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) + ".")
    lines += result.notes
    return "\n".join(lines)


def _montage_image(result) -> QImage:
    height, width = result.montage.shape
    image = QImage(result.montage.data, width, height, width, QImage.Format_Grayscale8)
    return image.copy()                                   # own the pixels: the array may go away with the result


def render_montage(result, zoom: float, *, show_addresses: bool = True) -> QPixmap:
    """The montage at *zoom* (screen pixels per montage pixel) with every site outlined and, where room allows, labelled."""
    image = _montage_image(result)
    pixmap = QPixmap.fromImage(image.scaled(max(int(image.width() * zoom), 1), max(int(image.height() * zoom), 1),
                                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    font = QFont()
    font.setPixelSize(max(int(MIN_LABEL_DIAMETER_PX * 0.3), 8))
    painter.setFont(font)
    for mark in site_marks(result):
        pen = QPen(QColor(STATE_COLOURS[mark.colour]))
        pen.setWidthF(1.5)
        if mark.fused:
            pen.setStyle(Qt.DashLine)
            pen.setColor(QColor(FUSED_COLOUR))
        painter.setPen(pen)
        cx, cy, r = mark.x * zoom, mark.y * zoom, mark.radius * zoom
        painter.drawEllipse(int(cx - r), int(cy - r), int(2 * r), int(2 * r))
        if show_addresses and mark.text and 2 * r >= MIN_LABEL_DIAMETER_PX:
            painter.setPen(QColor(Colors.brand_fg))
            painter.drawText(int(cx - r), int(cy - r), int(2 * r), int(2 * r), Qt.AlignCenter, mark.text)
    painter.end()
    return pixmap


def render_crop(crop: dict, size: int = 160) -> QPixmap:
    """A rim-suppressed crop with its segmentation outlined in red, scaled to *size* pixels on the long side."""
    import numpy as np
    from scipy import ndimage

    rim = np.asarray(crop["rim"], dtype=float)
    low, high = np.percentile(rim, [1, 99])
    grey = np.clip((rim - low) / max(high - low, 1e-6), 0, 1)
    rgb = np.stack([grey] * 3, axis=-1)
    edge = ndimage.binary_dilation(crop["labels"] > 0) & ~ndimage.binary_erosion(crop["labels"] > 0)
    rgb[edge] = (1.0, 0.1, 0.1)
    data = np.ascontiguousarray((rgb * 255).astype("uint8"))
    h, w, _ = data.shape
    image = QImage(data.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(image.scaled(size, size, Qt.KeepAspectRatio, Qt.FastTransformation))


#: Running workers. A QThread destroyed while running aborts the process, and the window (or the whole tab) can be
#: closed mid-run, so a worker is owned here until it finishes rather than by the widget that started it.
_RUNNING: set = set()


def _hold(worker: QThread) -> None:
    if not _RUNNING:
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_wait_for_workers)    # the interpreter must not exit under a running thread
    _RUNNING.add(worker)
    worker.finished.connect(lambda: _RUNNING.discard(worker))


def _wait_for_workers() -> None:
    for worker in list(_RUNNING):
        worker.wait(5000)


class _PreviewWorker(QThread):
    """Runs the analysis off the GUI thread. A QThread subclass, not a Python thread: the result comes back by signal."""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, job: dict):
        super().__init__()                  # no parent: its lifetime is _RUNNING's, not a widget's
        self._job = job

    def run(self) -> None:
        try:
            import process_microscopy as pm
            import tempfile

            from pipeline.nanowell_preview import preview_timepoint

            job = self._job
            with tempfile.TemporaryDirectory(prefix="allwell-preview-") as scratch:
                result = preview_timepoint(
                    job["zip"], job["well"], job["timepoint"], job["settings"], grouper=pm.find_image_groups_in_zip,
                    parse=pm.parse_filename, schema=pm.parse_schema(job["schema"]), sep=job["sep"],
                    phase_token=job["phase_token"], scratch=Path(scratch))
        except Exception as exc:          # a worker has no event loop to raise into; the window says what happened
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(result)


class NanowellPreviewWindow(QWidget):
    """Non-modal: the form stays usable (and the window can sit beside it) while a preview is up."""

    def __init__(self, tab, wells: dict[str, Path], timepoints: dict[str, list[str]]):
        super().__init__(tab, Qt.Window)
        self.setWindowTitle("Test detection")
        self.resize(980, 760)
        self._tab, self._wells, self._timepoints = tab, wells, timepoints
        self._worker: _PreviewWorker | None = None
        self._result = None
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self._well_cb = QComboBox(self)
        self._well_cb.addItems(sorted(wells))
        self._tp_cb = QComboBox(self)
        self._run_btn = QPushButton("Run", self)
        self._run_btn.clicked.connect(lambda _=False: self.run())
        self._zoom_cb = QComboBox(self)
        for label, value in ZOOMS:
            self._zoom_cb.addItem(label, userData=value)
        self._zoom_cb.setCurrentIndex(0)
        top.addWidget(QLabel("Well", self))
        top.addWidget(self._well_cb)
        top.addWidget(QLabel("Timepoint", self))
        top.addWidget(self._tp_cb)
        top.addWidget(self._run_btn)
        top.addStretch(1)
        top.addWidget(QLabel("Zoom", self))
        top.addWidget(self._zoom_cb)
        layout.addLayout(top)
        self._summary = QLabel("Choose a well and a timepoint, then Run.", self)
        self._summary.setWordWrap(True)
        self._summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._summary)
        self._legend = QLabel(self._legend_html(), self)
        layout.addWidget(self._legend)
        self._montage_label = QLabel(self)
        self._montage_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll = QScrollArea(self)
        scroll.setWidget(self._montage_label)
        scroll.setWidgetResizable(False)
        self._scroll = scroll
        layout.addWidget(scroll, 1)
        self._crops = QHBoxLayout()
        layout.addLayout(self._crops)
        self._well_cb.currentTextChanged.connect(self._fill_timepoints)
        self._zoom_cb.currentIndexChanged.connect(lambda _i: self._draw())
        self._fill_timepoints(self._well_cb.currentText())

    def reload(self, wells: dict[str, Path], timepoints: dict[str, list[str]]) -> None:
        """Take the current input folder's wells and timepoints (the form may have changed since the window opened)."""
        self._wells, self._timepoints = wells, timepoints
        current = self._well_cb.currentText()
        self._well_cb.blockSignals(True)
        self._well_cb.clear()
        self._well_cb.addItems(sorted(wells))
        if current in wells:
            self._well_cb.setCurrentText(current)
        self._well_cb.blockSignals(False)
        self._fill_timepoints(self._well_cb.currentText())

    @staticmethod
    def _legend_html() -> str:
        keys = {"empty": "empty", "measured": "cells", "occluded": "occluded", "clipped": "clipped",
                "unaddressed": "no address"}
        chips = "  ".join(f'<span style="color:{STATE_COLOURS[k]}">●</span> {v}' for k, v in keys.items())
        return chips + f'  <span style="color:{FUSED_COLOUR}">┄</span> fused (on a seam)'

    def _fill_timepoints(self, well: str) -> None:
        self._tp_cb.clear()
        self._tp_cb.addItems(self._timepoints.get(well, []))

    def _job(self) -> dict:
        from analyze.nanowell_section import settings_from_opts

        opts = self._tab._collect_run_options()
        well = self._well_cb.currentText()
        return {"zip": self._wells[well], "well": well, "timepoint": self._tp_cb.currentText(),
                "settings": settings_from_opts(opts), "schema": self._tab._build_schema_arg(),
                "sep": self._tab._schema_picker.separator(), "phase_token": self._tab._phase_token()}

    def run(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        try:
            job = self._job()
        except ValueError as exc:
            self._summary.setText(f"The preview could not start: {exc}")
            return
        problem = self._tab._nanowell_validation_error(check_input=False)
        if problem:
            self._summary.setText(f"The preview could not start: {problem}")
            return
        self._run_btn.setEnabled(False)
        self._summary.setText("Running registration and detection…")
        worker = _PreviewWorker(job)
        self._worker = worker
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)      # a bound slot: dropped with the window, unlike a lambda
        _hold(worker)
        worker.start()

    def _on_finished(self) -> None:
        self._run_btn.setEnabled(True)

    def _on_failed(self, message: str) -> None:
        self._result = None
        self._summary.setText(f"The preview could not run: {message}")

    def _on_done(self, result) -> None:
        self._result = result
        self._summary.setText(summary_text(result))
        self._draw()

    def _zoom(self) -> float:
        value = self._zoom_cb.currentData()
        if value:
            return float(value)
        if self._result is None or self._result.montage is None:
            return 1.0
        height, width = self._result.montage.shape
        area = self._scroll.viewport()
        return min(area.width() / width, area.height() / height, 1.0) or 1.0

    def _capped_zoom(self) -> float:
        if self._result is None or self._result.montage is None:
            return self._zoom()
        height, width = self._result.montage.shape
        return min(self._zoom(), MAX_PIXMAP_SIDE / max(height, width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._zoom_cb.currentData() and self._result is not None:
            self._draw()                  # "Fit" follows the window

    def _draw(self) -> None:
        for i in reversed(range(self._crops.count())):
            item = self._crops.takeAt(i)
            if item.widget() is not None:
                item.widget().deleteLater()
        result = self._result
        if result is None or result.montage is None:
            self._montage_label.clear()
            return
        pixmap = render_montage(result, self._capped_zoom())
        self._montage_label.setPixmap(pixmap)
        self._montage_label.resize(pixmap.size())
        for crop in result.crops:
            cell = QLabel(self)
            cell.setPixmap(render_crop(crop))
            cell.setToolTip(f"Site {crop['label']}: {crop['n_cells']} cell(s) — rim-suppressed, segmentation outlined")
            caption = QLabel(f"{crop['label']}: {crop['n_cells']} cell(s)", self)
            holder = QWidget(self)
            column = QVBoxLayout(holder)
            column.addWidget(cell)
            column.addWidget(caption)
            self._crops.addWidget(holder)
        self._crops.addStretch(1)


def _wells_and_timepoints(tab) -> tuple[dict[str, Path], dict[str, list[str]], str]:
    """The wells with an archive in the form's input folder, each well's timepoints from the record, and a problem if any."""
    from analyze.nanowell_preflight import wells_directory
    from pipeline.nanowell_task import timepoint_order
    from util.acquisition_record import AcquisitionRecordError, read_record_file

    raw = tab._input_edit.text().strip()
    if not raw:
        return {}, {}, "No input folder selected."
    folder = wells_directory(raw)
    zips = {p.stem: p for p in sorted(folder.glob("*.zip"))} if folder.is_dir() else {}
    if not zips:
        return {}, {}, f"No well archives were found in {folder}."
    try:
        record = read_record_file(folder / "acquisition.json")
    except AcquisitionRecordError as exc:
        return {}, {}, f"The acquisition record cannot be used: {exc}"
    if record is None:
        return {}, {}, f"There is no acquisition record ({folder / 'acquisition.json'}); nanowell mode needs it."
    timepoints = {well: timepoint_order(record, well) for well in zips}
    return {w: p for w, p in zips.items() if timepoints[w]}, timepoints, ""


def open_preview(tab) -> NanowellPreviewWindow | None:
    """Open (or raise) the Test detection window for *tab*'s input; say why not on the section's status line."""
    problems = tab._nanowell_validation_error(check_input=False)
    if problems:
        tab._nanowell_status.setText(problems)
        return None
    wells, timepoints, problem = _wells_and_timepoints(tab)
    if problem:
        tab._nanowell_status.setText(problem)
        return None
    tab._nanowell_status.setText("")
    window = getattr(tab, "_nanowell_preview_window", None)
    if window is None:
        window = NanowellPreviewWindow(tab, wells, timepoints)
        tab._nanowell_preview_window = window
    else:
        window.reload(wells, timepoints)
    window.show()
    window.raise_()
    return window
