"""smFISH tab builder (Qt port).

View-only: classification, zip scanning, image decoding, and the
apply-to-all worker live in ``smfish_controller.py`` and ``smfish_worker.py``.

Public surface (matches the convention used by every other ``tabs/*_tab_view.py``):

* :func:`build_smfish_tab` — construct the UI inside *parent*; stash widget
  refs and tab state on ``ctx.runtime.smfish.*``.
* :func:`smfish_sync_from_app` — repopulate combo boxes from the loaded
  dataset's ``pipeline_info.json``; called when a new dataset opens and
  when the user returns to the smFISH tab.

The original implementation lived in ``well_viewer/smfish_tab.py`` as a
``SmfishTab`` QWidget subclass. Phase 15.1 flattened it into this
module to match the architecture of every other tab.
"""

from __future__ import annotations

import json
import logging
import re

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from ui.theme import get_color
from well_viewer.data.image_resolver import resolve_ref_by_fov_tp
from well_viewer.features.smfish.controller import (
    SmfishImgRef as _ImgRef,  # noqa: F401 - re-exported for callers
    make_classifier,
    read_image_arrays,
    scan_well_zip,
)
from well_viewer.features.smfish.worker import apply_global_threshold_async
from well_viewer.domain.viewer_state import make_schema_extractor, extract_well_token, parse_rc
from well_viewer.core.channel_state_controller import recalculate_threshold
from well_viewer.data.data_loading import load_well_csv
from well_viewer.core.safe import swallowed

logger = logging.getLogger("smfish_tab")


class _WorkerBridge(QObject):
    """Marshal worker-thread status messages back to the GUI thread.

    The worker's daemon thread has no Qt event loop, so anything that
    touches widgets — including ``QTimer.singleShot(0, …)`` — must
    cross threads via a queued ``Signal``. ``status`` / ``done`` update
    the status label; ``after_csv`` carries the post-write callback
    that refreshes the dataset cache, recalculates the threshold, and
    shows the completion message. Without this signal the after-csv
    work silently dropped on the floor (timer created in a thread with
    no event loop never fires) and the UI showed stale counts until
    the app was restarted.
    """
    status = Signal(str)
    done = Signal(str)
    after_csv = Signal(object)


_NO_SMFISH_MESSAGE = (
    "No smFISH data was generated during the processing pipeline.\n"
    "Re-run the analysis with smFISH channels enabled to populate this tab."
)


def build_smfish_tab(ctx, parent: QWidget) -> None:
    """Build the smFISH tab inside *parent*.

    State and widget handles are attached to ``ctx.runtime.smfish.*`` (which
    resolves the legacy ``_smfish_*`` prefixed app attributes).
    """
    # Defer matplotlib imports until the tab actually builds. Loading the
    # QtAgg backend at module import time was a measurable chunk of startup.
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    # A8 burn-down: Qt widget handles go through the WidgetRegistry
    # (``ctx.widgets``), which stamps ``ctx.runtime.smfish.*`` so legacy readers resolve.
    # The (many) non-widget state fields below stay on the app — a later layer.
    reg = ctx.widgets

    # ── state init ────────────────────────────────────────────────────────
    reg.register("_smfish_frame", parent)
    # Scalar smFISH flags (separator / show_overlays / fit_on_next_redraw) are
    # typed state on ``ctx.state.smfish`` — the slice defaults cover their inits.
    ctx.runtime.smfish.out_dir = None
    ctx.runtime.smfish.fov_tp_extractor = None
    ctx.runtime.smfish.tokens = []
    ctx.runtime.smfish.well_to_zip = {}
    ctx.runtime.smfish.classifier = make_classifier(ctx.state.smfish.separator)
    ctx.runtime.smfish.current_log_img = None
    ctx.runtime.smfish.current_labels = None
    ctx.runtime.smfish.current_sorted_vals = None
    ctx.runtime.smfish.hover_annot = None
    ctx.runtime.smfish.pan_anchor = None
    ctx.runtime.smfish.cdf_popup = None
    ctx.runtime.smfish.cdf_canvas = None
    ctx.runtime.smfish.cdf_ax = None
    ctx.runtime.smfish.cdf_fig = None
    ctx.runtime.smfish.Figure = Figure
    ctx.runtime.smfish.FigureCanvas = FigureCanvas

    bridge = _WorkerBridge(parent)
    bridge.status.connect(lambda text: _smfish_set_status(ctx, text))
    bridge.done.connect(lambda text: _smfish_set_status(ctx, text))
    # The bridge lives on the GUI thread, so any callable emitted from
    # the worker thread runs here when the signal is delivered. This is
    # the only safe way to schedule widget-touching work from the
    # apply-global worker daemon.
    bridge.after_csv.connect(lambda cb: cb())
    ctx.runtime.smfish.bridge = bridge

    # ── layout ────────────────────────────────────────────────────────────
    root = parent.layout()
    if root is None:
        root = QVBoxLayout(parent)
        parent.setLayout(root)
    root.setContentsMargins(12, 12, 12, 12)

    # Row 1: channel/fov/tp/threshold/LUT
    ctrl = QWidget(parent)
    ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(0, 0, 0, 6)

    cl.addWidget(QLabel("Channel:", ctrl))
    channel_cb = reg.register("_smfish_channel_cb", QComboBox(ctrl))
    channel_cb.currentIndexChanged.connect(
        lambda _i: _smfish_on_channel_changed(ctx)
    )
    cl.addWidget(channel_cb)

    cl.addWidget(QLabel("FOV:", ctrl))
    fov_cb = reg.register("_smfish_fov_cb", QComboBox(ctrl))
    fov_cb.currentIndexChanged.connect(
        lambda _i: _smfish_load_selected_images(ctx)
    )
    cl.addWidget(fov_cb)

    cl.addWidget(QLabel("Timepoint:", ctrl))
    tp_cb = reg.register("_smfish_tp_cb", QComboBox(ctrl))
    tp_cb.currentIndexChanged.connect(
        lambda _i: _smfish_load_selected_images(ctx)
    )
    cl.addWidget(tp_cb)

    cl.addWidget(QLabel("smFISH_Thresh:", ctrl))
    threshold_edit = reg.register("_smfish_threshold_edit", QLineEdit("1500", ctrl))
    threshold_edit.setFixedWidth(90)
    threshold_edit.editingFinished.connect(lambda: _smfish_redraw(ctx))
    cl.addWidget(threshold_edit)

    cl.addWidget(QLabel("LUT Min:", ctrl))
    lut_min_edit = reg.register("_smfish_lut_min_edit", QLineEdit("", ctrl))
    lut_min_edit.setFixedWidth(70)
    lut_min_edit.editingFinished.connect(lambda: _smfish_redraw(ctx))
    cl.addWidget(lut_min_edit)

    cl.addWidget(QLabel("LUT Max:", ctrl))
    lut_max_edit = reg.register("_smfish_lut_max_edit", QLineEdit("", ctrl))
    lut_max_edit.setFixedWidth(70)
    lut_max_edit.editingFinished.connect(lambda: _smfish_redraw(ctx))
    cl.addWidget(lut_max_edit)
    cl.addStretch(1)
    root.addWidget(ctrl)

    # Row 2: action buttons
    btn_row = QWidget(parent)
    btn_row.setObjectName("TabCtrl")
    bl = QHBoxLayout(btn_row)
    bl.setContentsMargins(0, 2, 0, 2)

    apply_btn = QPushButton("Apply Global Threshold", btn_row)
    apply_btn.setProperty("variant", "secondary")
    apply_btn.clicked.connect(lambda _=False: _smfish_apply_to_all(ctx))
    bl.addWidget(apply_btn)

    refresh_btn = QPushButton("Refresh", btn_row)
    refresh_btn.setProperty("variant", "secondary")
    refresh_btn.clicked.connect(lambda _=False: _smfish_redraw(ctx))
    bl.addWidget(refresh_btn)

    overlay_btn = reg.register("_smfish_overlay_btn", QPushButton("Hide Overlays", btn_row))
    overlay_btn.setProperty("variant", "secondary")
    overlay_btn.clicked.connect(lambda _=False: _smfish_toggle_overlays(ctx))
    bl.addWidget(overlay_btn)

    cdf_btn = QPushButton("This Frame CDF", btn_row)
    cdf_btn.setProperty("variant", "secondary")
    cdf_btn.clicked.connect(lambda _=False: _smfish_open_cdf_popup(ctx))
    bl.addWidget(cdf_btn)
    bl.addStretch(1)
    root.addWidget(btn_row)

    # Status row
    status_label = reg.register("_smfish_status_label", QLabel(
        "Select a single well from the global picker.", parent
    ))
    status_label.setObjectName("Muted")
    root.addWidget(status_label)

    # Matplotlib image
    fig_img = reg.register("_smfish_fig_img", Figure(figsize=(6, 5), dpi=100))
    reg.register("_smfish_ax_img", fig_img.add_subplot(111))
    canvas_img = reg.register("_smfish_canvas_img", FigureCanvas(fig_img))
    root.addWidget(canvas_img, 1)
    # Theme the canvas immediately so it's never a bare white figure before the
    # first refresh paints data or an empty-state message.
    _smfish_paint_empty(ctx, "Select a well to view smFISH images.")

    from widgets.mpl_toolbar import MplToolbar
    toolbar = reg.register("_smfish_toolbar", MplToolbar(canvas_img, parent))
    root.addWidget(toolbar)

    canvas_img.mpl_connect(
        "motion_notify_event", lambda ev: _smfish_on_img_hover(ctx, ev)
    )
    canvas_img.mpl_connect(
        "scroll_event", lambda ev: _smfish_on_img_scroll(ctx, ev)
    )
    canvas_img.mpl_connect(
        "button_press_event", lambda ev: _smfish_on_img_press(ctx, ev)
    )
    canvas_img.mpl_connect(
        "button_release_event", lambda ev: _smfish_on_img_release(ctx, ev)
    )
    canvas_img.mpl_connect(
        "motion_notify_event", lambda ev: _smfish_on_img_drag(ctx, ev)
    )


# ── public API ────────────────────────────────────────────────────────────


def smfish_sync_from_app(ctx) -> None:
    """Repopulate combo boxes from the loaded dataset's ``pipeline_info.json``.

    Called when a new dataset opens and when the user navigates to the
    smFISH tab; a no-op if the tab hasn't been built yet.
    """
    if ctx.runtime.smfish.channel_cb is None:
        return  # tab not built yet

    out_dir = ctx.state.dataset.data_dir or ctx.runtime.smfish.out_dir
    if out_dir is None:
        _smfish_set_status(ctx, "No output loaded.")
        _smfish_paint_empty(
            ctx, "No dataset loaded.\nOpen a results folder to view smFISH images."
        )
        return
    info_path = out_dir / "pipeline_info.json"
    if not info_path.exists():
        ctx.runtime.smfish.out_dir = out_dir
        ctx.runtime.smfish.tokens = []
        ctx.runtime.smfish.well_to_zip = {}
        ctx.runtime.smfish.fov_tp_extractor = None
        _smfish_show_no_smfish_state(ctx)
        return
    try:
        info = json.loads(info_path.read_text())
        ctx.runtime.smfish.tokens = [
            str(t).strip() for t in info.get("smfish_tokens", []) if str(t).strip()
        ]
        ctx.state.smfish.separator = str(info.get("separator", "_"))
        ctx.runtime.smfish.classifier = make_classifier(ctx.state.smfish.separator)
        fov_idx = int(info.get("fov_index", -1))
        tp_idx = int(info.get("tp_index", -1))
        if tp_idx >= 0 and fov_idx >= 0:
            ctx.runtime.smfish.fov_tp_extractor = make_schema_extractor(
                ctx.state.smfish.separator, fov_idx, tp_idx
            )
        elif tp_idx >= 0:
            # Single-FOV pipeline (schema has `timepoint` but no `fov`).
            # Without this branch the smFISH tab silently falls back to
            # a legacy "last two parts of the stem" regex — that pulls
            # in the channel suffix (DAPI for the mask vs GFP for the
            # smFISH image), so the (fov, tp) keys for the two image
            # kinds never intersect and the tab reports "No smFISH/mask
            # pair found". Mirror the single-FOV extractor in
            # viewer_state.read_pipeline_info: synthesise a constant FOV
            # ("1") and read the timepoint from the schema index.
            _sep = ctx.state.smfish.separator
            _tp_idx = tp_idx

            def _single_fov_extractor(stem: str):
                parts = stem.split(_sep)
                tp = parts[_tp_idx] if 0 <= _tp_idx < len(parts) else "unknown"
                return "1", tp

            ctx.runtime.smfish.fov_tp_extractor = _single_fov_extractor
        else:
            ctx.runtime.smfish.fov_tp_extractor = None
    except Exception as exc:
        logger.warning("Could not parse %s: %s", info_path, exc)
        ctx.runtime.smfish.out_dir = out_dir
        ctx.runtime.smfish.tokens = []
        ctx.runtime.smfish.well_to_zip = {}
        ctx.runtime.smfish.fov_tp_extractor = None
        _smfish_show_no_smfish_state(ctx)
        return

    ctx.runtime.smfish.out_dir = out_dir

    if not ctx.runtime.smfish.tokens:
        ctx.runtime.smfish.well_to_zip = {}
        _smfish_show_no_smfish_state(ctx)
        return

    _smfish_set_controls_enabled(ctx, True)

    zips = sorted(out_dir.glob("*_out.zip"))
    ctx.runtime.smfish.well_to_zip = {}
    for z in zips:
        m = re.match(r"([A-Ha-h])(\d{1,2})_out\.zip$", z.name)
        if m:
            ctx.runtime.smfish.well_to_zip[
                f"{m.group(1).upper()}{int(m.group(2)):02d}"
            ] = z

    channels = ctx.runtime.smfish.tokens
    ctx.runtime.smfish.channel_cb.blockSignals(True)
    ctx.runtime.smfish.channel_cb.clear()
    ctx.runtime.smfish.channel_cb.addItems(channels)
    ctx.runtime.smfish.channel_cb.blockSignals(False)
    if channels:
        # Prefer a channel that has a saved threshold so the spinbox
        # shows the previously-applied value the moment the tab opens
        # — without this, the combo defaulted to channels[0], and if
        # the user had applied a threshold to a different channel the
        # spinbox showed the hardcoded "1500" default until they
        # manually switched the combo. The saved value still wins
        # later (via apply_saved_to_spinbox below), but the *visible*
        # selection now matches the applied configuration on reopen.
        saved = ctx.runtime.smfish.thresholds_saved or {}
        preferred_idx = 0
        if saved:
            for i, ch in enumerate(channels):
                if str(ch).strip().lower() in saved:
                    preferred_idx = i
                    break
        ctx.runtime.smfish.channel_cb.setCurrentIndex(preferred_idx)
    # Restore the saved threshold for the now-current channel before
    # the FOV / TP combos finish populating; otherwise the first
    # _smfish_redraw uses the stale hardcoded "1500" default.
    try:
        from well_viewer.persistence import smfish as _smfish_persist
        _smfish_persist.apply_saved_to_spinbox(ctx)
    except Exception:
        logging.getLogger("well_viewer").exception(
            "smFISH threshold restore on channel populate failed"
        )
    _smfish_refresh_fov_tp_values(ctx)


def _smfish_on_channel_changed(ctx) -> None:
    """Channel combo handler: restore the saved threshold for the
    new channel (so the spinbox matches the spot counts already in
    the CSVs), then run the standard FOV/TP refresh."""
    try:
        from well_viewer.persistence import smfish as _smfish_persist
        _smfish_persist.apply_saved_to_spinbox(ctx)
    except Exception:
        logging.getLogger("well_viewer").exception(
            "smFISH threshold restore on channel change failed"
        )
    _smfish_refresh_fov_tp_values(ctx)


# ── internal helpers ──────────────────────────────────────────────────────


def _smfish_set_status(ctx, text: str) -> None:
    ctx.runtime.smfish.status_label.setText(text)


def _smfish_toggle_overlays(ctx) -> None:
    sm = ctx.state.smfish
    sm.show_overlays = not sm.show_overlays
    ctx.runtime.smfish.overlay_btn.setText(
        "Hide Overlays" if sm.show_overlays else "Show Overlays"
    )
    _smfish_redraw(ctx)


def _smfish_open_cdf_popup(ctx) -> None:
    if ctx.runtime.smfish.cdf_popup is not None and ctx.runtime.smfish.cdf_popup.isVisible():
        ctx.runtime.smfish.cdf_popup.raise_()
        ctx.runtime.smfish.cdf_popup.activateWindow()
        _smfish_update_cdf_plot(ctx)
        return

    popup = QDialog(ctx.runtime.smfish.frame)
    popup.setWindowTitle("smFISH CDF")
    popup.resize(760, 380)
    vl = QVBoxLayout(popup)
    ctx.runtime.smfish.cdf_fig = ctx.runtime.smfish.Figure(figsize=(7.2, 3.4), dpi=100)
    ctx.runtime.smfish.cdf_ax = ctx.runtime.smfish.cdf_fig.add_subplot(111)
    ctx.runtime.smfish.cdf_canvas = ctx.runtime.smfish.FigureCanvas(ctx.runtime.smfish.cdf_fig)
    vl.addWidget(ctx.runtime.smfish.cdf_canvas)
    popup.finished.connect(lambda _r: _smfish_close_cdf_popup(ctx))
    ctx.runtime.smfish.cdf_popup = popup
    popup.show()
    _smfish_update_cdf_plot(ctx)


def _smfish_close_cdf_popup(ctx) -> None:
    ctx.runtime.smfish.cdf_popup = None
    ctx.runtime.smfish.cdf_canvas = None
    ctx.runtime.smfish.cdf_ax = None
    ctx.runtime.smfish.cdf_fig = None


def _smfish_update_cdf_plot(ctx) -> None:
    if ctx.runtime.smfish.cdf_ax is None:
        return

    bg_panel = get_color("BG_PANEL")
    txt_pri = get_color("TXT_PRI")
    txt_mut = get_color("TXT_MUT")
    accent = get_color("ACCENT")

    log_img = ctx.runtime.smfish.current_log_img
    labels = ctx.runtime.smfish.current_labels
    thr = _smfish_get_threshold(ctx)
    ax = ctx.runtime.smfish.cdf_ax
    ax.clear()
    ax.set_facecolor(bg_panel)
    if ctx.runtime.smfish.cdf_fig is not None:
        ctx.runtime.smfish.cdf_fig.patch.set_facecolor(bg_panel)

    if log_img is None or labels is None:
        ax.text(0.5, 0.5, "No smFISH image loaded.", transform=ax.transAxes,
                ha="center", va="center", color=txt_mut, fontsize=10)
        if ctx.runtime.smfish.cdf_canvas is not None:
            ctx.runtime.smfish.cdf_canvas.draw_idle()
        return

    candidate_mask = (log_img > 0) & (labels > 0)
    candidate_ys, candidate_xs = np.where(candidate_mask)
    candidate_vals = log_img[candidate_ys, candidate_xs]
    if candidate_vals.size == 0:
        candidate_mask = labels > 0
        candidate_ys, candidate_xs = np.where(candidate_mask)
        candidate_vals = np.abs(log_img[candidate_ys, candidate_xs])

    vals = (
        np.sort(candidate_vals)
        if candidate_vals.size
        else np.array([], dtype=np.float32)
    )
    if vals.size:
        y = np.arange(1, vals.size + 1, dtype=np.float32) / vals.size
        ax.plot(vals, y, color=accent, linewidth=2.0, alpha=0.85)
        ax.fill_between(vals, y, alpha=0.2, color=accent)
    else:
        ax.text(0.5, 0.5, "No candidate values found.", transform=ax.transAxes,
                ha="center", va="center", color=txt_mut, fontsize=10)

    if getattr(ctx.state.plot, "show_threshold_line", False):
        ax.axvline(thr, color="red", linestyle="--", linewidth=2.0, alpha=0.7)
    ax.set_title("CDF of LoG values inside labels", color=txt_pri,
                 fontsize=10, fontweight="bold")
    ax.set_xlabel("LoG value", color=txt_pri, fontsize=9)
    ax.set_ylabel("Cumulative Probability", color=txt_pri, fontsize=9)
    ax.grid(True, alpha=0.2, color=txt_mut)
    ax.tick_params(axis="x", colors=txt_mut, labelsize=8)
    ax.tick_params(axis="y", colors=txt_mut, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(txt_mut)
    if ctx.runtime.smfish.cdf_canvas is not None:
        ctx.runtime.smfish.cdf_canvas.draw_idle()


def _smfish_set_controls_enabled(ctx, enabled: bool) -> None:
    for w in (ctx.runtime.smfish.channel_cb, ctx.runtime.smfish.fov_cb, ctx.runtime.smfish.tp_cb,
              ctx.runtime.smfish.threshold_edit, ctx.runtime.smfish.lut_min_edit,
              ctx.runtime.smfish.lut_max_edit):
        try:
            w.setEnabled(enabled)
        except Exception:
            swallowed()  # was: pass (silent except)


def _smfish_paint_empty(ctx, message: str) -> None:
    """Paint *message* centred on the image axes over the theme background.

    Matches every other plot's empty/no-selection warning so the smFISH tab
    never shows a bare white matplotlib canvas (the default figure colour) when
    there's no dataset, no smFISH channel, or no image for the selection.
    """
    if ctx.runtime.smfish.ax_img is None:
        return
    try:
        bg_panel = get_color("BG_PANEL")
        txt_mut = get_color("TXT_MUT")
        ctx.runtime.smfish.ax_img.clear()
        ctx.runtime.smfish.ax_img.set_facecolor(bg_panel)
        if ctx.runtime.smfish.fig_img is not None:
            ctx.runtime.smfish.fig_img.patch.set_facecolor(bg_panel)
        ctx.runtime.smfish.ax_img.set_axis_off()
        ctx.runtime.smfish.ax_img.text(
            0.5, 0.5, message,
            transform=ctx.runtime.smfish.ax_img.transAxes,
            ha="center", va="center",
            color=txt_mut,
            fontsize=11, wrap=True,
        )
        if ctx.runtime.smfish.canvas_img is not None:
            ctx.runtime.smfish.canvas_img.draw_idle()
    except Exception:
        swallowed()


def _smfish_show_no_smfish_state(ctx) -> None:
    # The full guidance is drawn once, centered on the canvas below; keep the
    # status line concise so the same paragraph is not shown twice.
    _smfish_set_status(ctx, "smFISH not enabled for this dataset.")
    for cb in (ctx.runtime.smfish.channel_cb, ctx.runtime.smfish.fov_cb, ctx.runtime.smfish.tp_cb):
        cb.blockSignals(True)
        cb.clear()
        cb.blockSignals(False)
    _smfish_set_controls_enabled(ctx, False)
    ctx.runtime.smfish.well_to_zip = {}
    ctx.runtime.smfish.current_log_img = None
    ctx.runtime.smfish.current_labels = None
    ctx.runtime.smfish.current_sorted_vals = None
    _smfish_paint_empty(ctx, _NO_SMFISH_MESSAGE)


def _smfish_selected_well_token(ctx) -> str | None:
    sels = sorted(ctx.state.selection.selected_wells, key=parse_rc)
    if len(sels) != 1:
        return None
    tok = extract_well_token(sels[0]) or sels[0]
    m = re.match(r"([A-Ha-h])(\d{1,2})$", tok.strip())
    if not m:
        return None
    return f"{m.group(1).upper()}{int(m.group(2)):02d}"


def _smfish_scan_selected_zip(ctx):
    if ctx.runtime.smfish.out_dir is None:
        return {}, {}
    well = _smfish_selected_well_token(ctx)
    channel = ctx.runtime.smfish.channel_cb.currentText().strip().lower()
    zip_path = ctx.runtime.smfish.well_to_zip.get(well)
    if not well or not channel or zip_path is None:
        return {}, {}
    return scan_well_zip(
        zip_path=zip_path,
        channel=channel,
        classifier=ctx.runtime.smfish.classifier,
        fov_tp_extractor=ctx.runtime.smfish.fov_tp_extractor,
    )


def _smfish_refresh_fov_tp_values(ctx) -> None:
    smfish, mask = _smfish_scan_selected_zip(ctx)
    keys = sorted(set(smfish).intersection(mask))
    fovs = sorted({k[0] for k in keys})
    tps = sorted({k[1] for k in keys})

    for cb, values in ((ctx.runtime.smfish.fov_cb, fovs), (ctx.runtime.smfish.tp_cb, tps)):
        cb.blockSignals(True)
        cb.clear()
        cb.addItems(values)
        cb.blockSignals(False)
    _smfish_load_selected_images(ctx)


def _smfish_load_selected_images(ctx) -> None:
    smfish, mask = _smfish_scan_selected_zip(ctx)
    fov_raw = ctx.runtime.smfish.fov_cb.currentText().strip()
    tp_raw = ctx.runtime.smfish.tp_cb.currentText().strip()
    sm_ref = resolve_ref_by_fov_tp(
        smfish,
        fov_raw=fov_raw,
        tp_raw=tp_raw,
        norm_timepoint=lambda value: str(value or "").strip(),
    )
    mk_ref = resolve_ref_by_fov_tp(
        mask,
        fov_raw=fov_raw,
        tp_raw=tp_raw,
        norm_timepoint=lambda value: str(value or "").strip(),
    )
    if sm_ref is None or mk_ref is None:
        _smfish_set_status(ctx, "No smFISH/mask pair found for current selection.")
        _smfish_paint_empty(
            ctx, "No smFISH/mask pair found for the current well / FOV / timepoint."
        )
        return
    result = read_image_arrays(sm_ref, mk_ref)
    if result is None:
        _smfish_set_status(ctx, "Failed to load selected image data.")
        _smfish_paint_empty(ctx, "Failed to load the selected image data.")
        return
    (ctx.runtime.smfish.current_log_img, ctx.runtime.smfish.current_labels,
     ctx.runtime.smfish.current_sorted_vals) = result
    well = _smfish_selected_well_token(ctx) or "N/A"
    _smfish_set_status(ctx, f"Loaded {well} fov={fov_raw} tp={tp_raw}.")
    ctx.state.smfish.fit_on_next_redraw = True
    _smfish_redraw(ctx)


def _smfish_get_threshold(ctx) -> float:
    try:
        return float(ctx.runtime.smfish.threshold_edit.text().strip())
    except ValueError:
        return 0.0


def _smfish_redraw(ctx) -> None:
    if ctx.runtime.smfish.current_log_img is None or ctx.runtime.smfish.current_labels is None:
        return
    txt_pri = get_color("TXT_PRI")
    thr = _smfish_get_threshold(ctx)
    log_img = ctx.runtime.smfish.current_log_img
    labels = ctx.runtime.smfish.current_labels
    prev_xlim = ctx.runtime.smfish.ax_img.get_xlim()
    prev_ylim = ctx.runtime.smfish.ax_img.get_ylim()
    lut_min_txt = ctx.runtime.smfish.lut_min_edit.text().strip()
    lut_max_txt = ctx.runtime.smfish.lut_max_edit.text().strip()
    try:
        lut_min = float(lut_min_txt) if lut_min_txt else None
    except ValueError:
        lut_min = None
    try:
        lut_max = float(lut_max_txt) if lut_max_txt else None
    except ValueError:
        lut_max = None
    spot_mask = (log_img > thr) & (labels > 0)
    ys, xs = np.where(spot_mask)

    ctx.runtime.smfish.ax_img.clear()
    ctx.runtime.smfish.ax_img.imshow(log_img, cmap="gray", vmin=lut_min, vmax=lut_max)
    # skimage is multi-second to import on cold caches; defer until the
    # first redraw so opening the smFISH tab without analysing data
    # doesn't pay the cost.
    from skimage.segmentation import find_boundaries
    bnd = find_boundaries(labels, mode="outer")
    if ctx.state.smfish.show_overlays:
        ctx.runtime.smfish.ax_img.contour(
            bnd.astype(np.uint8), levels=[0.5], colors="red", linewidths=0.5
        )
        if xs.size:
            ctx.runtime.smfish.ax_img.scatter(
                xs, ys, s=20, facecolors="none", edgecolors="cyan", linewidths=0.6
            )
    ctx.runtime.smfish.ax_img.set_title(
        f"Spots above threshold: {int(xs.size)}", color=txt_pri, fontsize=10
    )
    ctx.runtime.smfish.ax_img.set_xticks([])
    ctx.runtime.smfish.ax_img.set_yticks([])
    if ctx.state.smfish.fit_on_next_redraw:
        h, w = log_img.shape[:2]
        ctx.runtime.smfish.ax_img.set_xlim(-0.5, w - 0.5)
        ctx.runtime.smfish.ax_img.set_ylim(h - 0.5, -0.5)
        ctx.state.smfish.fit_on_next_redraw = False
    else:
        ctx.runtime.smfish.ax_img.set_xlim(prev_xlim)
        ctx.runtime.smfish.ax_img.set_ylim(prev_ylim)
    if ctx.runtime.smfish.hover_annot is None:
        ctx.runtime.smfish.hover_annot = ctx.runtime.smfish.ax_img.annotate(
            "",
            xy=(0, 0),
            xytext=(8, 8),
            textcoords="offset points",
            color="white",
            fontsize=8,
            bbox=dict(boxstyle="round,pad=0.2", fc="black", ec="white",
                      lw=0.6, alpha=0.8),
        )
        ctx.runtime.smfish.hover_annot.set_visible(False)

    ctx.runtime.smfish.canvas_img.draw_idle()
    _smfish_update_cdf_plot(ctx)


def _smfish_on_img_hover(ctx, event) -> None:
    if ctx.runtime.smfish.current_log_img is None or event.inaxes != ctx.runtime.smfish.ax_img:
        if ctx.runtime.smfish.hover_annot is not None and ctx.runtime.smfish.hover_annot.get_visible():
            ctx.runtime.smfish.hover_annot.set_visible(False)
            ctx.runtime.smfish.canvas_img.draw_idle()
        return
    if event.xdata is None or event.ydata is None or ctx.runtime.smfish.hover_annot is None:
        return
    x = int(round(event.xdata))
    y = int(round(event.ydata))
    h, w = ctx.runtime.smfish.current_log_img.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        ctx.runtime.smfish.hover_annot.set_visible(False)
        ctx.runtime.smfish.canvas_img.draw_idle()
        return
    if ctx.runtime.smfish.current_labels is None or int(ctx.runtime.smfish.current_labels[y, x]) <= 0:
        ctx.runtime.smfish.hover_annot.set_visible(False)
        ctx.runtime.smfish.canvas_img.draw_idle()
        return
    px = float(ctx.runtime.smfish.current_log_img[y, x])
    ctx.runtime.smfish.hover_annot.xy = (x, y)
    ctx.runtime.smfish.hover_annot.set_text(f"({x}, {y}) = {px:.3f}")
    ctx.runtime.smfish.hover_annot.set_visible(True)
    ctx.runtime.smfish.canvas_img.draw_idle()


def _smfish_on_img_scroll(ctx, event) -> None:
    if event.inaxes != ctx.runtime.smfish.ax_img or event.xdata is None or event.ydata is None:
        return
    scale = 1 / 1.2 if event.button == "up" else 1.2
    xlim = ctx.runtime.smfish.ax_img.get_xlim()
    ylim = ctx.runtime.smfish.ax_img.get_ylim()
    new_w = (xlim[1] - xlim[0]) * scale
    new_h = (ylim[1] - ylim[0]) * scale
    relx = (event.xdata - xlim[0]) / (xlim[1] - xlim[0]) if xlim[1] != xlim[0] else 0.5
    rely = (event.ydata - ylim[0]) / (ylim[1] - ylim[0]) if ylim[1] != ylim[0] else 0.5
    ctx.runtime.smfish.ax_img.set_xlim(
        event.xdata - new_w * relx, event.xdata + new_w * (1 - relx)
    )
    ctx.runtime.smfish.ax_img.set_ylim(
        event.ydata - new_h * rely, event.ydata + new_h * (1 - rely)
    )
    ctx.runtime.smfish.canvas_img.draw_idle()


def _smfish_on_img_press(ctx, event) -> None:
    if (event.inaxes != ctx.runtime.smfish.ax_img or event.button != 1
            or event.xdata is None or event.ydata is None):
        return
    ctx.runtime.smfish.pan_anchor = (
        event.xdata, event.ydata,
        ctx.runtime.smfish.ax_img.get_xlim(), ctx.runtime.smfish.ax_img.get_ylim(),
    )


def _smfish_on_img_release(ctx, _event) -> None:
    ctx.runtime.smfish.pan_anchor = None


def _smfish_on_img_drag(ctx, event) -> None:
    if (ctx.runtime.smfish.pan_anchor is None or event.inaxes != ctx.runtime.smfish.ax_img
            or event.xdata is None or event.ydata is None):
        return
    x0, y0, xlim0, ylim0 = ctx.runtime.smfish.pan_anchor
    dx = event.xdata - x0
    dy = event.ydata - y0
    ctx.runtime.smfish.ax_img.set_xlim(xlim0[0] - dx, xlim0[1] - dx)
    ctx.runtime.smfish.ax_img.set_ylim(ylim0[0] - dy, ylim0[1] - dy)
    ctx.runtime.smfish.canvas_img.draw_idle()


def _smfish_apply_to_all(ctx) -> None:
    if ctx.runtime.smfish.out_dir is None:
        _smfish_set_status(ctx, "No output loaded.")
        return
    # Re-entrancy guard (audit E18): a double-click otherwise launched two
    # workers writing the same per-well CSVs, and the second launch's
    # ``cancel_event.clear()`` discarded a cancel meant for the first. Reject a
    # second concurrent run; the flag is cleared in ``_done`` on any exit.
    if ctx.runtime.smfish.apply_running:
        _smfish_set_status(ctx, "Apply to All is already running.")
        return
    channel = ctx.runtime.smfish.channel_cb.currentText().strip().lower()
    thr = _smfish_get_threshold(ctx)
    frame = ctx.runtime.smfish.frame

    def _after_csv() -> None:
        # Runs on the GUI thread via _WorkerBridge.after_csv — see the
        # bridge docstring for why this can't be invoked directly from
        # the worker. Safe to touch widgets and call the refresh chain
        # synchronously.
        try:
            from well_viewer.persistence import smfish as _smfish_persist
            _smfish_persist.save_threshold(ctx, channel, thr)
        except Exception:
            logging.getLogger("well_viewer").exception(
                "smFISH threshold persistence failed"
            )
        _smfish_refresh_app_cache(ctx)
        QMessageBox.information(frame, "smFISH", "Apply to All finished.")

    # Cancel event used by load_controller / app shutdown to abort
    # the in-flight smFISH run before it overwrites CSVs from a
    # different dataset.
    import threading as _threading
    if ctx.runtime.smfish.cancel_event is None:
        ctx.runtime.smfish.cancel_event = _threading.Event()
    else:
        ctx.runtime.smfish.cancel_event.clear()

    def _done(msg: str) -> None:
        # Clear the busy flag on every terminal path (success / cancel / error)
        # then hand off to the GUI-thread bridge slot (audit E18).
        ctx.runtime.smfish.apply_running = False
        ctx.runtime.smfish.bridge.done.emit(msg)

    ctx.runtime.smfish.apply_running = True
    # well_to_csv is the same map the app's loader populated, so the
    # writer hits the exact files the rest of the app reads from — no
    # silent glob mismatches on datasets whose CSVs are named e.g.
    # "<well>.csv" with no underscore prefix.
    apply_global_threshold_async(
        out_dir=ctx.runtime.smfish.out_dir,
        well_to_zip=ctx.runtime.smfish.well_to_zip,
        channel=channel,
        threshold=thr,
        classifier=ctx.runtime.smfish.classifier,
        fov_tp_extractor=ctx.runtime.smfish.fov_tp_extractor,
        status_cb=ctx.runtime.smfish.bridge.status.emit,
        done_cb=_done,
        # Route the after-csv callback through the bridge so it runs on
        # the GUI thread, not the worker daemon thread. Calling _after_csv
        # directly here would leave QTimer.singleShot + widget access in
        # the worker thread where they silently no-op.
        after_csv_cb=lambda: ctx.runtime.smfish.bridge.after_csv.emit(_after_csv),
        cancel_event=ctx.runtime.smfish.cancel_event,
        expected_data_dir=ctx.runtime.smfish.out_dir,
        well_to_csv=dict(ctx.state.dataset.well_paths),
    )


def _smfish_refresh_app_cache(ctx) -> None:
    for label, path in ctx.state.dataset.well_paths.items():
        if label in ctx.state.dataset.cache:
            ctx.state.dataset.cache[label] = load_well_csv(path)
    recalculate_threshold(ctx.app)
    # The per-well DataFrames now carry new smfish_count values; the
    # stats cache keys do not include CSV content, so without an
    # explicit invalidation every line / bar / scatter aggregate keeps
    # returning the pre-apply numbers.
    from well_viewer.core.selection_controller import invalidate_stats_cache
    from well_viewer.core.plot_orchestrator import redraw_app
    invalidate_stats_cache(ctx)
    redraw_app(ctx)
    # Refresh the Review CSV table so the smfish_count column on the
    # active well reflects the new counts. The plot redraw above does
    # not touch the Review CSV widget — it lives outside the plot fan-out.
    # A8 D4: invoke the ctx-native ``refresh_review_csv`` via the live callback
    # the Review CSV tab builder stamps on ``ctx.runtime.review`` — avoids both
    # the context's app-escape-hatch field and a cross-feature deferred import
    # into image_browser (the deletability guardrail bans those). ``None`` until
    # the Review CSV tab has been built, in which case there is nothing to refresh.
    refresh_review_csv = ctx.runtime.review.refresh_csv
    if callable(refresh_review_csv):
        try:
            refresh_review_csv()
        except Exception:
            logging.getLogger("well_viewer").exception(
                "Review CSV refresh after Apply Global Threshold failed"
            )
