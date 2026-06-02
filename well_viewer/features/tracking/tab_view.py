"""Review "Tracking" tab — Setup & Run + Track Viewer (§D / PR5b).

A top-level rail tab (sibling of Segmentation / Plotting) with two nested
sub-pages:

* **Setup & Run** — engine picker + linking parameters + Run/Cancel. Run launches
  the engine-agnostic post-pass (:mod:`features.tracking.worker`) over the
  selected wells, marshalling status/done/after-csv back to the GUI thread via a
  ``_WorkerBridge`` (the smFISH worker contract). After the write-back the
  per-well cache is refreshed, stats cache dropped, plots + Review CSV redrawn.
* **Track Viewer** — centroid trajectories for the active well, coloured by track.

Backwards-compatible: with no tracks (or a pre-§C dataset) the viewer shows an
empty-state and nothing else changes.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabBar, QVBoxLayout, QWidget,
)

from well_viewer.core.centre_view import NamedPageStack
from well_viewer.features.tracking.engines import (
    DEFAULT_ENGINE, EngineParams, all_engines, get_engine,
)
from well_viewer.features.tracking.worker import track_wells_async

_logger = logging.getLogger("well_viewer")

_SUBTABS = ("Setup & Run", "Track Viewer")


class _WorkerBridge(QObject):
    """Marshal tracking-worker thread callbacks onto the GUI thread."""
    status = Signal(str)
    done = Signal(str)
    after_csv = Signal(object)


def _set_status(app, text: str) -> None:
    lbl = getattr(app, "_tracking_status_lbl", None)
    if lbl is not None:
        lbl.setText(text)


def build_tracking_tab(app, parent: QWidget) -> None:
    """Build the Tracking tab inside *parent* (state under ``_tracking_*``)."""
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    app._tracking_frame = parent
    app._tracking_Figure = Figure
    app._tracking_FigureCanvas = FigureCanvas

    bridge = _WorkerBridge(parent)
    bridge.status.connect(lambda t: _set_status(app, t))
    bridge.done.connect(lambda t: _set_status(app, t))
    bridge.after_csv.connect(lambda cb: cb())
    app._tracking_bridge = bridge

    if not hasattr(app, "_tracking_cancel_event") or app._tracking_cancel_event is None:
        app._tracking_cancel_event = threading.Event()

    root = parent.layout() or QVBoxLayout(parent)
    root.setContentsMargins(0, 0, 0, 0)

    bar = QTabBar()
    bar.setObjectName("SampleDefinitionsSubTabs")
    bar.setExpanding(False)
    bar.setDrawBase(False)
    for t in _SUBTABS:
        bar.addTab(t)
    root.addWidget(bar, 0)

    stack = NamedPageStack(parent)
    stack.setObjectName("TrackingSubTabs")
    root.addWidget(stack, 1)
    app._tracking_notebook = stack

    setup_page = QWidget()
    QVBoxLayout(setup_page).setContentsMargins(0, 0, 0, 0)
    _build_setup(app, setup_page)
    stack.addPage("Setup & Run", setup_page)

    viewer_page = QWidget()
    QVBoxLayout(viewer_page).setContentsMargins(0, 0, 0, 0)
    _build_viewer(app, viewer_page)
    stack.addPage("Track Viewer", viewer_page)

    def _on_bar(idx: int = 0) -> None:
        target = bar.tabText(idx)
        if target:
            stack.setCurrentByName(target)
    bar.currentChanged.connect(_on_bar)

    def _on_stack(_i: int = 0) -> None:
        name = stack.currentName()
        if name in _SUBTABS and bar.tabText(bar.currentIndex()) != name:
            blocked = bar.blockSignals(True)
            try:
                bar.setCurrentIndex(_SUBTABS.index(name))
            finally:
                bar.blockSignals(blocked)
        if name == "Track Viewer":
            _refresh_viewer(app)
    stack.currentChanged.connect(_on_stack)


# ── Setup & Run ──────────────────────────────────────────────────────────────

def _build_setup(app, parent: QWidget) -> None:
    lay = QVBoxLayout(parent) if parent.layout() is None else parent.layout()
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(8)

    def _row(label: str) -> QHBoxLayout:
        r = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setObjectName("Muted")
        lbl.setFixedWidth(150)
        r.addWidget(lbl)
        lay.addLayout(r)
        return r

    er = _row("Engine")
    app._tracking_engine_cb = QComboBox(parent)
    for info in all_engines():
        text = info.display_name if info.available else f"{info.display_name} (needs install)"
        app._tracking_engine_cb.addItem(text, userData=info.name)
    er.addWidget(app._tracking_engine_cb)
    er.addStretch(1)

    dr = _row("Max link distance (px)")
    app._tracking_max_dist_edit = QLineEdit("30", parent)
    app._tracking_max_dist_edit.setFixedWidth(80)
    dr.addWidget(app._tracking_max_dist_edit)
    dr.addStretch(1)

    gr = _row("Max gap frames")
    app._tracking_max_gap_edit = QLineEdit("1", parent)
    app._tracking_max_gap_edit.setFixedWidth(80)
    gr.addWidget(app._tracking_max_gap_edit)
    gr.addStretch(1)

    app._tracking_division_cb = QCheckBox("Allow division / lineage branching", parent)
    lay.addWidget(app._tracking_division_cb)

    btns = QHBoxLayout()
    app._tracking_run_btn = QPushButton("Run Tracking", parent)
    app._tracking_run_btn.clicked.connect(lambda: _run_tracking(app))
    btns.addWidget(app._tracking_run_btn)
    app._tracking_cancel_btn = QPushButton("Cancel", parent)
    app._tracking_cancel_btn.setEnabled(False)
    app._tracking_cancel_btn.clicked.connect(lambda: _cancel_tracking(app))
    btns.addWidget(app._tracking_cancel_btn)
    btns.addStretch(1)
    lay.addLayout(btns)

    app._tracking_status_lbl = QLabel("Select wells on the left, then Run Tracking.", parent)
    app._tracking_status_lbl.setObjectName("Muted")
    app._tracking_status_lbl.setWordWrap(True)
    lay.addWidget(app._tracking_status_lbl)
    lay.addStretch(1)


def _current_engine_name(app) -> str:
    cb = getattr(app, "_tracking_engine_cb", None)
    if cb is None:
        return DEFAULT_ENGINE
    return cb.currentData() or DEFAULT_ENGINE


def _params_from_ui(app) -> EngineParams:
    def _f(edit, default):
        try:
            return float(edit.text().strip() or default)
        except (TypeError, ValueError):
            return default

    def _i(edit, default):
        try:
            return int(float(edit.text().strip() or default))
        except (TypeError, ValueError):
            return default

    return EngineParams(
        max_distance=_f(app._tracking_max_dist_edit, 30.0),
        max_gap_frames=_i(app._tracking_max_gap_edit, 1),
        allow_division=app._tracking_division_cb.isChecked(),
    )


def _selected_well_to_csv(app) -> "dict[str, Path]":
    well_paths = dict(getattr(app._app_state.dataset, "well_paths", {}) or {})
    selected = set(app._app_state.selection.selected_wells or [])
    if selected:
        well_paths = {w: p for w, p in well_paths.items() if w in selected}
    return {w: Path(p) for w, p in well_paths.items()}


def _run_tracking(app) -> None:
    out_dir = getattr(app._app_state.dataset, "data_dir", None)
    if not out_dir:
        _set_status(app, "Load a processed dataset first.")
        return
    engine = _current_engine_name(app)
    try:
        if not get_engine(engine).available:
            _set_status(app, f"Engine '{engine}' is not installed. Pick another engine.")
            return
    except KeyError:
        _set_status(app, f"Unknown engine '{engine}'.")
        return
    well_to_csv = _selected_well_to_csv(app)
    if not well_to_csv:
        _set_status(app, "No wells available — select wells on the left.")
        return

    app._tracking_cancel_event.clear()
    app._tracking_run_btn.setEnabled(False)
    app._tracking_cancel_btn.setEnabled(True)
    out = Path(out_dir)

    def _after_csv() -> None:
        _refresh_app_cache(app)
        _refresh_viewer(app)
        app._tracking_run_btn.setEnabled(True)
        app._tracking_cancel_btn.setEnabled(False)

    def _done(msg: str) -> None:
        _set_status(app, msg)
        app._tracking_run_btn.setEnabled(True)
        app._tracking_cancel_btn.setEnabled(False)

    bridge = app._tracking_bridge
    track_wells_async(
        out_dir=out,
        well_to_csv=well_to_csv,
        engine=engine,
        params=_params_from_ui(app),
        status_cb=bridge.status.emit,
        done_cb=lambda m: bridge.done.emit(m) or _done(m),
        after_csv_cb=lambda: bridge.after_csv.emit(_after_csv),
        cancel_event=app._tracking_cancel_event,
        expected_data_dir=out,
    )


def _cancel_tracking(app) -> None:
    ev = getattr(app, "_tracking_cancel_event", None)
    if ev is not None:
        ev.set()
    _set_status(app, "Cancelling…")


def _refresh_app_cache(app) -> None:
    """Re-read tracked CSVs into the cache and redraw (smFISH refresh chain)."""
    for label, path in dict(app._app_state.dataset.well_paths).items():
        if label in app._app_state.dataset.cache:
            try:
                app._app_state.dataset.cache[label] = app._load_well_csv(path)
            except Exception:
                _logger.exception("Tracking: failed to reload %s", path)
    if hasattr(app, "_invalidate_stats_cache"):
        app._invalidate_stats_cache()
    try:
        app._redraw()
    except Exception:
        _logger.exception("Tracking: redraw after tracking failed")
    refresh_csv = getattr(app, "_refresh_review_csv", None)
    if callable(refresh_csv):
        try:
            refresh_csv()
        except Exception:
            _logger.exception("Tracking: Review CSV refresh failed")


# ── Track Viewer ─────────────────────────────────────────────────────────────

def _build_viewer(app, parent: QWidget) -> None:
    lay = QVBoxLayout(parent) if parent.layout() is None else parent.layout()
    lay.setContentsMargins(0, 0, 0, 0)

    top = QHBoxLayout()
    top.setContentsMargins(12, 6, 12, 6)
    app._tracking_viewer_summary = QLabel("No tracks yet.", parent)
    app._tracking_viewer_summary.setObjectName("Muted")
    top.addWidget(app._tracking_viewer_summary)
    top.addStretch(1)
    refresh_btn = QPushButton("Refresh", parent)
    refresh_btn.clicked.connect(lambda: _refresh_viewer(app))
    top.addWidget(refresh_btn)
    lay.addLayout(top)

    fig = app._tracking_Figure(figsize=(5, 5))
    app._tracking_viewer_fig = fig
    app._tracking_viewer_ax = fig.add_subplot(111)
    canvas = app._tracking_FigureCanvas(fig)
    app._tracking_viewer_canvas = canvas
    lay.addWidget(canvas, 1)


def _active_well(app) -> "str | None":
    sel = list(app._app_state.selection.selected_wells or [])
    if sel:
        return sel[0]
    paths = dict(getattr(app._app_state.dataset, "well_paths", {}) or {})
    return next(iter(paths), None)


def _refresh_viewer(app) -> None:
    ax = getattr(app, "_tracking_viewer_ax", None)
    canvas = getattr(app, "_tracking_viewer_canvas", None)
    summary = getattr(app, "_tracking_viewer_summary", None)
    if ax is None or canvas is None:
        return
    ax.clear()
    well = _active_well(app)
    df = None
    if well is not None:
        try:
            df = app._get_rows(well)
        except Exception:
            df = None

    from well_viewer.features.tracking.model import (
        TRACK_ID_COL, CENTROID_ROW_COL, CENTROID_COL_COL,
    )
    have = (df is not None and not df.empty and TRACK_ID_COL in df.columns
            and CENTROID_ROW_COL in df.columns and CENTROID_COL_COL in df.columns)
    if not have:
        ax.text(0.5, 0.5, "No tracks for this well.\nRun tracking on the Setup & Run tab.",
                ha="center", va="center", transform=ax.transAxes, color="#94A3B8")
        ax.set_axis_off()
        if summary is not None:
            summary.setText("No tracks yet.")
        canvas.draw_idle()
        return

    import numpy as np
    import pandas as pd
    tid = pd.to_numeric(df[TRACK_ID_COL], errors="coerce")
    rr = pd.to_numeric(df[CENTROID_ROW_COL], errors="coerce")
    cc = pd.to_numeric(df[CENTROID_COL_COL], errors="coerce")
    valid = tid.notna() & (tid >= 0) & rr.notna() & cc.notna()
    n_tracks = 0
    for track_id, grp in df[valid].groupby(tid[valid]):
        r = pd.to_numeric(grp[CENTROID_ROW_COL], errors="coerce").to_numpy()
        c = pd.to_numeric(grp[CENTROID_COL_COL], errors="coerce").to_numpy()
        ax.plot(c, r, "-", linewidth=0.8, alpha=0.8)
        ax.plot(c[:1], r[:1], "o", markersize=2)
        n_tracks += 1
    ax.set_title(f"{well} — {n_tracks} track(s)")
    ax.set_xlabel("centroid col (px)")
    ax.set_ylabel("centroid row (px)")
    ax.invert_yaxis()  # image coordinates
    ax.set_aspect("equal", adjustable="datalim")
    if summary is not None:
        summary.setText(f"{well}: {n_tracks} track(s).")
    canvas.draw_idle()


# ── Tab-switch hook ──────────────────────────────────────────────────────────

def tracking_sync_from_app(app) -> None:
    """Refresh the Tracking tab when it becomes active or the well changes."""
    if getattr(app, "_tracking_notebook", None) is None:
        return
    if app._tracking_notebook.currentName() == "Track Viewer":
        _refresh_viewer(app)
