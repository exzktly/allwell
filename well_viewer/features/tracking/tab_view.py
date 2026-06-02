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
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabBar, QVBoxLayout, QWidget,
)

from well_viewer.core.centre_view import NamedPageStack
from well_viewer.features.tracking.engines import (
    DEFAULT_ENGINE, EngineParams, all_engines, get_engine,
)
from well_viewer.features.tracking.worker import track_wells_async

_logger = logging.getLogger("well_viewer")

_SUBTABS = ("Setup & Run", "Track Viewer", "Track Plots", "Lineage", "Editor", "Pruning", "Analytics")


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

    track_plots_page = QWidget()
    QVBoxLayout(track_plots_page).setContentsMargins(0, 0, 0, 0)
    _build_track_plots(app, track_plots_page)
    stack.addPage("Track Plots", track_plots_page)

    lineage_page = QWidget()
    QVBoxLayout(lineage_page).setContentsMargins(0, 0, 0, 0)
    _build_lineage(app, lineage_page)
    stack.addPage("Lineage", lineage_page)

    editor_page = QWidget()
    QVBoxLayout(editor_page).setContentsMargins(0, 0, 0, 0)
    _build_editor(app, editor_page)
    stack.addPage("Editor", editor_page)

    pruning_page = QWidget()
    QVBoxLayout(pruning_page).setContentsMargins(0, 0, 0, 0)
    _build_pruning(app, pruning_page)
    stack.addPage("Pruning", pruning_page)

    analytics_page = QWidget()
    QVBoxLayout(analytics_page).setContentsMargins(0, 0, 0, 0)
    _build_analytics(app, analytics_page)
    stack.addPage("Analytics", analytics_page)

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
        elif name == "Track Plots":
            _refresh_track_plots(app)
        elif name == "Lineage":
            _refresh_lineage(app)
        elif name == "Editor":
            _refresh_editor(app)
        elif name == "Pruning":
            _refresh_pruning(app)
        elif name == "Analytics":
            _refresh_analytics(app)
    stack.currentChanged.connect(_on_stack)

    # Live update: the Track Plots view reads the well-plate selection, so refresh
    # it whenever the selection changes while that sub-tab is showing.
    bus = getattr(app, "_event_bus", None)
    if bus is not None:
        def _on_selection_changed(_state=None) -> None:
            if stack.currentName() == "Track Plots":
                _refresh_track_plots(app)
        bus.selection_changed.connect(_on_selection_changed)


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

    # trackastra linking mode (ignored by the centroid engines). 'ilp' needs motile.
    lr = _row("Linking mode (trackastra)")
    app._tracking_link_mode_cb = QComboBox(parent)
    from well_viewer.features.tracking.engines.trackastra_engine import LINKING_MODES, ilp_available
    for m in LINKING_MODES:
        app._tracking_link_mode_cb.addItem(m + ("" if m != "ilp" or ilp_available() else " (needs motile)"),
                                           userData=m)
    app._tracking_link_mode_cb.setCurrentIndex(1)  # "greedy"
    lr.addWidget(app._tracking_link_mode_cb)
    lr.addStretch(1)

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

    mode_cb = getattr(app, "_tracking_link_mode_cb", None)
    extra = {"linking_mode": mode_cb.currentData()} if mode_cb is not None else {}
    return EngineParams(
        max_distance=_f(app._tracking_max_dist_edit, 30.0),
        max_gap_frames=_i(app._tracking_max_gap_edit, 1),
        allow_division=app._tracking_division_cb.isChecked(),
        extra=extra,
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


# ── Edit / prune shared helpers ──────────────────────────────────────────────

def _editor_for(app, well: str):
    """Return a per-well :class:`TrackEditor` bound to the cached DataFrame."""
    from well_viewer.features.tracking.edit import TrackEditor
    cache = getattr(app, "_tracking_editors", None)
    if cache is None:
        cache = app._tracking_editors = {}
    df = app._get_rows(well)
    ed = cache.get(well)
    if ed is None or ed.df is not df:
        ed = TrackEditor(df)
        cache[well] = ed
    return ed


def _refresh_after_edit(app) -> None:
    if hasattr(app, "_invalidate_stats_cache"):
        app._invalidate_stats_cache()
    try:
        app._redraw()
    except Exception:
        _logger.exception("Tracking edit: redraw failed")
    rc = getattr(app, "_refresh_review_csv", None)
    if callable(rc):
        try:
            rc()
        except Exception:
            _logger.exception("Tracking edit: Review CSV refresh failed")
    _refresh_viewer(app)
    _refresh_lineage(app)
    _refresh_editor(app)
    _refresh_pruning(app)
    _refresh_analytics(app)


def _persist_well(app, well: str, df) -> None:
    """Write the edited well CSV + rebuild its tracks.json entry, then refresh."""
    from well_viewer.persistence._io import atomic_write_text
    from well_viewer.persistence import tracking as _tp
    from well_viewer.features.tracking.model import payload_from_df
    path = dict(getattr(app._app_state.dataset, "well_paths", {}) or {}).get(well)
    if path:
        try:
            atomic_write_text(Path(path), df.to_csv(index=False))
        except OSError:
            _logger.exception("Tracking edit: failed to write %s", path)
    out_dir = getattr(app._app_state.dataset, "data_dir", None)
    if out_dir:
        wells = _tp.load_tracks(out_dir)
        wells[well] = payload_from_df(well, df)
        _tp.save_tracks(out_dir, wells)
    _refresh_after_edit(app)


def _verb_row(parent, lay, label, fields):
    """Add a row: muted label + one QLineEdit per (placeholder,width) in *fields*.
    Returns the list of QLineEdits (the Apply button is added by the caller)."""
    row = QHBoxLayout()
    lbl = QLabel(label, parent)
    lbl.setObjectName("Muted")
    lbl.setFixedWidth(130)
    row.addWidget(lbl)
    edits = []
    for placeholder, width in fields:
        e = QLineEdit(parent)
        e.setPlaceholderText(placeholder)
        e.setFixedWidth(width)
        row.addWidget(e)
        edits.append(e)
    lay.addLayout(row)
    return row, edits


def _as_int(edit, default=None):
    try:
        return int(float(edit.text().strip()))
    except (TypeError, ValueError):
        return default


# ── Editor sub-tab ───────────────────────────────────────────────────────────

def _build_editor(app, parent: QWidget) -> None:
    lay = parent.layout() or QVBoxLayout(parent)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(6)

    def _well_or_status():
        well = _active_well(app)
        if well is None:
            app._tracking_editor_status.setText("Select a well on the left.")
        return well

    def _do(fn):
        well = _well_or_status()
        if well is None:
            return
        ed = _editor_for(app, well)
        try:
            msg = fn(ed)
        except Exception as exc:  # noqa: BLE001
            app._tracking_editor_status.setText(f"Edit failed: {exc}")
            return
        _persist_well(app, well, ed.df)
        app._tracking_editor_status.setText(msg)

    _, relabel = _verb_row(parent, lay, "Relabel", [("old id", 70), ("new id", 70)])
    rb = QPushButton("Apply", parent)
    rb.clicked.connect(lambda: _do(lambda ed: (
        f"Relabelled {ed.relabel(_as_int(relabel[0], -1), _as_int(relabel[1], -1))} cell(s).")))
    _attach(lay, rb)

    _, merge = _verb_row(parent, lay, "Merge (into ← from)", [("into id", 70), ("from id", 70)])
    mb = QPushButton("Apply", parent)
    mb.clicked.connect(lambda: _do(lambda ed: (
        f"Merged {ed.merge(_as_int(merge[0], -1), _as_int(merge[1], -1))} cell(s).")))
    _attach(lay, mb)

    _, split = _verb_row(parent, lay, "Split (id @ tp)", [("track id", 70), ("timepoint", 90)])
    sb = QPushButton("Apply", parent)
    sb.clicked.connect(lambda: _do(lambda ed: (
        f"Split → new track {ed.split(_as_int(split[0], -1), split[1].text().strip())}.")))
    _attach(lay, sb)

    _, delete = _verb_row(parent, lay, "Delete", [("track id", 70)])
    db = QPushButton("Apply", parent)
    db.clicked.connect(lambda: _do(lambda ed: (
        f"Deleted {ed.delete(_as_int(delete[0], -1))} cell(s).")))
    _attach(lay, db)

    _, div = _verb_row(parent, lay, "Division (parent→child)", [("parent", 70), ("child", 70)])
    vb = QPushButton("Apply", parent)
    vb.clicked.connect(lambda: _do(lambda ed: (
        "Division assigned." if ed.assign_division(_as_int(div[0], -1), _as_int(div[1], -1))
        else "Division not assigned (check ids).")))
    _attach(lay, vb)

    ub = QPushButton("Undo last edit", parent)

    def _undo():
        well = _well_or_status()
        if well is None:
            return
        ed = _editor_for(app, well)
        if ed.undo():
            _persist_well(app, well, ed.df)
            app._tracking_editor_status.setText("Undid last edit.")
        else:
            app._tracking_editor_status.setText("Nothing to undo.")
    ub.clicked.connect(_undo)
    lay.addWidget(ub)

    app._tracking_editor_status = QLabel("Edit the active well's tracks.", parent)
    app._tracking_editor_status.setObjectName("Muted")
    app._tracking_editor_status.setWordWrap(True)
    lay.addWidget(app._tracking_editor_status)
    lay.addStretch(1)


def _attach(lay, btn) -> None:
    """Append *btn* to the most-recently added row layout."""
    item = lay.itemAt(lay.count() - 1)
    if item is not None and item.layout() is not None:
        item.layout().addWidget(btn)
        item.layout().addStretch(1)
    else:
        lay.addWidget(btn)


def _refresh_editor(app) -> None:
    lbl = getattr(app, "_tracking_editor_status", None)
    if lbl is None:
        return
    well = _active_well(app)
    if well is None:
        lbl.setText("Select a well on the left.")
        return
    try:
        ids = _editor_for(app, well).track_ids()
    except Exception:
        ids = []
    lbl.setText(f"{well}: {len(ids)} track(s). Enter ids above and Apply.")


# ── Pruning sub-tab ──────────────────────────────────────────────────────────

def _build_pruning(app, parent: QWidget) -> None:
    lay = parent.layout() or QVBoxLayout(parent)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(6)

    _, mf = _verb_row(parent, lay, "Min frames", [("e.g. 3", 70)])
    _, md = _verb_row(parent, lay, "Min displacement (px)", [("e.g. 10", 70)])
    app._tracking_prune_min_frames = mf[0]
    app._tracking_prune_min_disp = md[0]

    btns = QHBoxLayout()
    pv = QPushButton("Preview", parent)
    pv.clicked.connect(lambda: _prune(app, apply_now=False))
    btns.addWidget(pv)
    ap = QPushButton("Apply", parent)
    ap.clicked.connect(lambda: _prune(app, apply_now=True))
    btns.addWidget(ap)
    btns.addStretch(1)
    lay.addLayout(btns)

    app._tracking_prune_status = QLabel(
        "Drop short / low-motion tracks. Preview first.", parent)
    app._tracking_prune_status.setObjectName("Muted")
    app._tracking_prune_status.setWordWrap(True)
    lay.addWidget(app._tracking_prune_status)
    lay.addStretch(1)


def _prune_rules(app):
    from well_viewer.features.tracking.pruning import PruneRules
    mf = _as_int(app._tracking_prune_min_frames, 0) or 0
    try:
        md = float(app._tracking_prune_min_disp.text().strip() or 0)
    except (TypeError, ValueError):
        md = 0.0
    return PruneRules(min_frames=mf, min_displacement=md)


def _prune(app, *, apply_now: bool) -> None:
    from well_viewer.features.tracking import pruning
    well = _active_well(app)
    if well is None:
        app._tracking_prune_status.setText("Select a well on the left.")
        return
    rules = _prune_rules(app)
    if not rules.active:
        app._tracking_prune_status.setText("Set a min-frames or min-displacement rule first.")
        return
    df = app._get_rows(well)
    n_tracks, n_cells = pruning.preview(df, rules)
    if not apply_now:
        app._tracking_prune_status.setText(
            f"Would drop {n_tracks} track(s) / {n_cells} cell(s). Click Apply to prune.")
        return
    dropped = pruning.evaluate(df, rules)
    pruning.apply(df, dropped)
    _persist_well(app, well, df)
    app._tracking_prune_status.setText(f"Pruned {len(dropped)} track(s).")


def _refresh_pruning(app) -> None:
    lbl = getattr(app, "_tracking_prune_status", None)
    if lbl is not None and _active_well(app) is None:
        lbl.setText("Select a well on the left.")


# ── Lineage sub-tab ──────────────────────────────────────────────────────────

def _build_lineage(app, parent: QWidget) -> None:
    lay = parent.layout() or QVBoxLayout(parent)
    lay.setContentsMargins(0, 0, 0, 0)
    top = QHBoxLayout()
    top.setContentsMargins(12, 6, 12, 6)
    app._tracking_lineage_summary = QLabel("No lineage yet.", parent)
    app._tracking_lineage_summary.setObjectName("Muted")
    top.addWidget(app._tracking_lineage_summary)
    top.addStretch(1)
    rb = QPushButton("Refresh", parent)
    rb.clicked.connect(lambda: _refresh_lineage(app))
    top.addWidget(rb)
    lay.addLayout(top)

    fig = app._tracking_Figure(figsize=(5, 5))
    app._tracking_lineage_fig = fig
    app._tracking_lineage_ax = fig.add_subplot(111)
    canvas = app._tracking_FigureCanvas(fig)
    app._tracking_lineage_canvas = canvas
    lay.addWidget(canvas, 1)


def _refresh_lineage(app) -> None:
    ax = getattr(app, "_tracking_lineage_ax", None)
    canvas = getattr(app, "_tracking_lineage_canvas", None)
    summary = getattr(app, "_tracking_lineage_summary", None)
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

    import pandas as pd
    from well_viewer.features.tracking.model import TRACK_ID_COL, TRACK_PARENT_COL
    from well_viewer.data.data_loading import parse_timepoint_hours
    have = (df is not None and not df.empty and TRACK_ID_COL in df.columns
            and "timepoint" in df.columns)
    if not have:
        ax.text(0.5, 0.5, "No lineage for this well.", ha="center", va="center",
                transform=ax.transAxes, color="#94A3B8")
        ax.set_axis_off()
        if summary is not None:
            summary.setText("No lineage yet.")
        canvas.draw_idle()
        return

    tid = pd.to_numeric(df[TRACK_ID_COL], errors="coerce")
    parent = (pd.to_numeric(df[TRACK_PARENT_COL], errors="coerce")
              if TRACK_PARENT_COL in df.columns else pd.Series([-1] * len(df)))
    valid = df[tid.notna() & (tid >= 0)]
    ids = sorted({int(v) for v in tid.dropna() if int(v) >= 0})
    lane = {t: i for i, t in enumerate(ids)}
    last_pt = {}  # track_id -> (x, y_max)
    first_pt = {}
    parent_of = {}
    for t in ids:
        grp = valid[pd.to_numeric(valid[TRACK_ID_COL], errors="coerce") == t]
        ys = sorted(parse_timepoint_hours(str(v)) or 0.0 for v in grp["timepoint"])
        x = lane[t]
        ax.plot([x, x], [min(ys), max(ys)], "-", linewidth=2, alpha=0.85)
        first_pt[t] = (x, min(ys))
        last_pt[t] = (x, max(ys))
        p = pd.to_numeric(grp[TRACK_PARENT_COL], errors="coerce")
        pv = int(p.iloc[0]) if len(p) and pd.notna(p.iloc[0]) else -1
        parent_of[t] = pv
    # division edges: parent's last point → child's first point
    n_div = 0
    for t, p in parent_of.items():
        if p in last_pt and t in first_pt:
            (px, py), (cx, cy) = last_pt[p], first_pt[t]
            ax.plot([px, cx], [py, cy], "--", linewidth=1, color="#94A3B8")
            n_div += 1
    ax.set_xlabel("track")
    ax.set_ylabel("time (h)")
    ax.set_title(f"{well} — {len(ids)} track(s), {n_div} division link(s)")
    ax.invert_yaxis()
    if summary is not None:
        summary.setText(f"{well}: {len(ids)} track(s), {n_div} division(s).")
    canvas.draw_idle()


# ── Analytics sub-tab ────────────────────────────────────────────────────────

def _build_analytics(app, parent: QWidget) -> None:
    lay = parent.layout() or QVBoxLayout(parent)
    lay.setContentsMargins(0, 0, 0, 0)

    top = QHBoxLayout()
    top.setContentsMargins(12, 6, 12, 6)
    app._tracking_analytics_summary = QLabel("No tracks yet.", parent)
    app._tracking_analytics_summary.setObjectName("Muted")
    top.addWidget(app._tracking_analytics_summary)
    top.addStretch(1)
    rb = QPushButton("Refresh", parent)
    rb.clicked.connect(lambda: _refresh_analytics(app))
    top.addWidget(rb)
    lay.addLayout(top)

    fig = app._tracking_Figure(figsize=(5, 4))
    app._tracking_analytics_fig = fig
    app._tracking_analytics_ax = fig.add_subplot(111)
    app._tracking_analytics_canvas = app._tracking_FigureCanvas(fig)
    lay.addWidget(app._tracking_analytics_canvas, 1)

    exp = QHBoxLayout()
    exp.setContentsMargins(12, 6, 12, 6)
    exp.addWidget(QLabel("Export:", parent))
    for label, kind in (("Motility CSV", "motility"), ("CTC man_track", "ctc"),
                        ("Lineage JSON", "lineage")):
        b = QPushButton(label, parent)
        b.clicked.connect(lambda _=False, k=kind: _export(app, k))
        exp.addWidget(b)
    exp.addStretch(1)
    lay.addLayout(exp)


def _refresh_analytics(app) -> None:
    ax = getattr(app, "_tracking_analytics_ax", None)
    canvas = getattr(app, "_tracking_analytics_canvas", None)
    summary = getattr(app, "_tracking_analytics_summary", None)
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
    from well_viewer.features.tracking.motility import compute_track_motility
    m = compute_track_motility(df) if df is not None else None
    if m is None or m.empty:
        ax.text(0.5, 0.5, "No tracks for this well.", ha="center", va="center",
                transform=ax.transAxes, color="#94A3B8")
        ax.set_axis_off()
        if summary is not None:
            summary.setText("No tracks yet.")
        canvas.draw_idle()
        return
    ax.hist(m["directionality"].to_numpy(), bins=10, range=(0, 1),
            color="#3B82F6", alpha=0.85)
    ax.set_xlabel("directionality (net / total)")
    ax.set_ylabel("tracks")
    ax.set_title(f"{well} — {len(m)} track(s)")
    if summary is not None:
        summary.setText(
            f"{well}: {len(m)} track(s) · mean speed {m['mean_speed'].mean():.2f} px/h · "
            f"mean directionality {m['directionality'].mean():.2f}")
    canvas.draw_idle()


def _export(app, kind: str) -> None:
    well = _active_well(app)
    if well is None:
        return
    try:
        df = app._get_rows(well)
    except Exception:
        df = None
    if df is None or df.empty:
        return
    from well_viewer.features.tracking import export as _exp
    defaults = {"motility": (f"{well}_motility.csv", "CSV (*.csv)"),
                "ctc": (f"{well}_man_track.txt", "Text (*.txt)"),
                "lineage": (f"{well}_lineage.json", "JSON (*.json)")}
    fname, filt = defaults[kind]
    path, _ = QFileDialog.getSaveFileName(
        app._tracking_frame, f"Export {kind}", fname, filt)
    if not path:
        return
    try:
        if kind == "motility":
            _exp.export_motility_csv(df, path)
        elif kind == "ctc":
            _exp.export_ctc_man_track(df, path)
        else:
            _exp.export_lineage_json(df, path, well=well)
    except Exception:
        _logger.exception("Tracking export (%s) failed", kind)
        return
    summary = getattr(app, "_tracking_analytics_summary", None)
    if summary is not None:
        summary.setText(f"Exported {kind} → {Path(path).name}")


# ── Track Plots sub-tab ──────────────────────────────────────────────────────

def _selected_wells(app) -> "list[str]":
    sel = sorted(app._app_state.selection.selected_wells or [])
    if sel:
        return sel
    paths = dict(getattr(app._app_state.dataset, "well_paths", {}) or {})
    active = _active_well(app)
    return [active] if active in paths else list(paths)[:1]


def _build_track_plots(app, parent: QWidget) -> None:
    lay = parent.layout() or QVBoxLayout(parent)
    lay.setContentsMargins(0, 0, 0, 0)

    top = QHBoxLayout()
    top.setContentsMargins(12, 6, 12, 6)
    top.addWidget(QLabel("Metric", parent))
    app._tracking_tp_metric_cb = QComboBox(parent)
    app._tracking_tp_metric_cb.setMinimumWidth(180)
    app._tracking_tp_metric_cb.currentIndexChanged.connect(lambda _i: _refresh_track_plots(app))
    top.addWidget(app._tracking_tp_metric_cb)
    app._tracking_tp_avg_cb = QCheckBox("Show average track (per well)", parent)
    app._tracking_tp_avg_cb.setChecked(True)
    app._tracking_tp_avg_cb.toggled.connect(lambda _b: _refresh_track_plots(app))
    top.addWidget(app._tracking_tp_avg_cb)
    top.addStretch(1)
    rb = QPushButton("Refresh", parent)
    rb.clicked.connect(lambda: _refresh_track_plots(app))
    top.addWidget(rb)
    lay.addLayout(top)

    fig = app._tracking_Figure(figsize=(6, 4))
    app._tracking_tp_fig = fig
    app._tracking_tp_ax = fig.add_subplot(111)
    app._tracking_tp_canvas = app._tracking_FigureCanvas(fig)
    lay.addWidget(app._tracking_tp_canvas, 1)

    app._tracking_tp_summary = QLabel("Select wells on the left to plot tracks.", parent)
    app._tracking_tp_summary.setObjectName("Muted")
    app._tracking_tp_summary.setContentsMargins(12, 2, 12, 6)
    lay.addWidget(app._tracking_tp_summary)


def _populate_tp_metric_combo(app, wells) -> str:
    """Fill the metric combo with the union of plottable metrics across *wells*,
    preserving the current selection. Returns the active metric ('' if none)."""
    from well_viewer.features.tracking.track_plots import plottable_metrics
    cb = app._tracking_tp_metric_cb
    metrics: "list[str]" = []
    seen = set()
    for well in wells:
        try:
            df = app._get_rows(well)
        except Exception:
            continue
        for m in plottable_metrics(df):
            if m not in seen:
                seen.add(m)
                metrics.append(m)
    prev = cb.currentText()
    current = [cb.itemText(i) for i in range(cb.count())]
    if metrics != current:
        blocked = cb.blockSignals(True)
        try:
            cb.clear()
            cb.addItems(metrics)
            if prev in metrics:
                cb.setCurrentIndex(metrics.index(prev))
        finally:
            cb.blockSignals(blocked)
    return cb.currentText()


def _refresh_track_plots(app) -> None:
    ax = getattr(app, "_tracking_tp_ax", None)
    canvas = getattr(app, "_tracking_tp_canvas", None)
    summary = getattr(app, "_tracking_tp_summary", None)
    if ax is None or canvas is None:
        return
    ax.clear()
    wells = _selected_wells(app)
    metric = _populate_tp_metric_combo(app, wells) if wells else ""

    if not wells or not metric:
        ax.text(0.5, 0.5, "Select wells on the left and a metric to plot tracks.",
                ha="center", va="center", transform=ax.transAxes, color="#94A3B8")
        ax.set_axis_off()
        if summary is not None:
            summary.setText("Select wells on the left to plot tracks.")
        canvas.draw_idle()
        return

    import matplotlib as mpl
    from well_viewer.features.tracking.track_plots import compute_track_series
    palette = mpl.rcParams["axes.prop_cycle"].by_key().get("color", ["#3B82F6"])
    show_avg = app._tracking_tp_avg_cb.isChecked()

    total_tracks = 0
    plotted_wells = 0
    for i, well in enumerate(wells):
        try:
            df = app._get_rows(well)
        except Exception:
            continue
        ts = compute_track_series(df, metric)
        if ts.n_tracks == 0:
            continue
        color = palette[i % len(palette)]
        for _tid, t, v in ts.tracks:
            ax.plot(t, v, "-", color=color, linewidth=0.7, alpha=0.35)
        if show_avg and ts.avg_times.size:
            ax.plot(ts.avg_times, ts.avg_values, "-", color=color, linewidth=2.2,
                    label=f"{well} (avg, n={ts.n_tracks})")
        else:
            # still give the well a legend handle even without the average line
            ax.plot([], [], "-", color=color, linewidth=2, label=f"{well} (n={ts.n_tracks})")
        total_tracks += ts.n_tracks
        plotted_wells += 1

    if total_tracks == 0:
        ax.text(0.5, 0.5, "No tracks in the selected well(s).\nRun tracking first.",
                ha="center", va="center", transform=ax.transAxes, color="#94A3B8")
        ax.set_axis_off()
        if summary is not None:
            summary.setText("No tracks in the selected well(s).")
        canvas.draw_idle()
        return

    ax.set_xlabel("time (h)")
    ax.set_ylabel(metric)
    ax.set_title(f"{metric} per track — {plotted_wells} well(s), {total_tracks} track(s)")
    if plotted_wells <= 12:
        ax.legend(fontsize=7, loc="best")
    if summary is not None:
        avg_note = "average track per well shown" if show_avg else "individual tracks only"
        summary.setText(f"{plotted_wells} well(s) · {total_tracks} track(s) · {avg_note}.")
    canvas.draw_idle()


# ── Tab-switch hook ──────────────────────────────────────────────────────────

def tracking_sync_from_app(app) -> None:
    """Refresh the Tracking tab when it becomes active or the well changes."""
    nb = getattr(app, "_tracking_notebook", None)
    if nb is None:
        return
    name = nb.currentName()
    refresher = {
        "Track Viewer": _refresh_viewer,
        "Track Plots": _refresh_track_plots,
        "Lineage": _refresh_lineage,
        "Analytics": _refresh_analytics,
    }.get(name)
    if refresher is not None:
        refresher(app)
