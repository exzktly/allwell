"""smFISH tab builder (Qt port).

View-only: classification, zip scanning, image decoding, and the
apply-to-all worker live in ``smfish_controller.py`` and ``smfish_worker.py``.

Public surface (matches the convention used by every other ``tabs/*_tab_view.py``):

* :func:`build_smfish_tab` — construct the UI inside *parent*; stash widget
  refs and tab state on ``app._smfish_*``.
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
from pathlib import Path
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from ui.theme import get_color
from well_viewer.image_resolver import resolve_ref_by_fov_tp
from well_viewer.smfish_controller import (
    SmfishImgRef as _ImgRef,  # noqa: F401 - re-exported for callers
    make_classifier,
    read_image_arrays,
    scan_well_zip,
)
from well_viewer.smfish_worker import apply_global_threshold_async
from well_viewer.ui_helpers import attach_plot_toolbar
from well_viewer.viewer_state import make_schema_extractor
from well_viewer.channel_state_controller import recalculate_threshold

logger = logging.getLogger("smfish_tab")


class _WorkerBridge(QObject):
    """Marshal worker-thread status messages back to the GUI thread."""
    status = Signal(str)
    done = Signal(str)


_NO_SMFISH_MESSAGE = (
    "No smFISH data was generated during the processing pipeline.\n"
    "Re-run the analysis with smFISH channels enabled to populate this tab."
)


def build_smfish_tab(ctx, parent: QWidget) -> None:
    """Build the smFISH tab inside *parent*.

    State and widget handles are attached to ``ctx.app`` under the
    ``_smfish_*`` prefix.
    """
    # Defer matplotlib imports until the tab actually builds. Loading the
    # QtAgg backend at module import time was a measurable chunk of startup.
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    # ── state init ────────────────────────────────────────────────────────
    ctx.app._smfish_frame = parent
    ctx.app._smfish_out_dir = None
    ctx.app._smfish_separator = "_"
    ctx.app._smfish_fov_tp_extractor = None
    ctx.app._smfish_tokens = []
    ctx.app._smfish_well_to_zip = {}
    ctx.app._smfish_classifier = make_classifier(ctx.app._smfish_separator)
    ctx.app._smfish_current_log_img = None
    ctx.app._smfish_current_labels = None
    ctx.app._smfish_current_sorted_vals = None
    ctx.app._smfish_hover_annot = None
    ctx.app._smfish_pan_anchor = None
    ctx.app._smfish_fit_on_next_redraw = True
    ctx.app._smfish_show_overlays = True
    ctx.app._smfish_cdf_popup = None
    ctx.app._smfish_cdf_canvas = None
    ctx.app._smfish_cdf_ax = None
    ctx.app._smfish_cdf_fig = None
    ctx.app._smfish_Figure = Figure
    ctx.app._smfish_FigureCanvas = FigureCanvas

    bridge = _WorkerBridge(parent)
    bridge.status.connect(lambda text: _smfish_set_status(ctx.app, text))
    bridge.done.connect(lambda text: _smfish_set_status(ctx.app, text))
    ctx.app._smfish_bridge = bridge

    # ── layout ────────────────────────────────────────────────────────────
    root = parent.layout()
    if root is None:
        root = QVBoxLayout(parent)
        parent.setLayout(root)
    root.setContentsMargins(0, 0, 0, 0)

    # Row 1: channel/fov/tp/threshold/LUT
    ctrl = QWidget(parent)
    ctrl.setObjectName("TabCtrl")
    cl = QHBoxLayout(ctrl)
    cl.setContentsMargins(10, 6, 10, 6)

    cl.addWidget(QLabel("Channel:", ctrl))
    ctx.app._smfish_channel_cb = QComboBox(ctrl)
    ctx.app._smfish_channel_cb.currentIndexChanged.connect(
        lambda _i: _smfish_refresh_fov_tp_values(ctx.app)
    )
    cl.addWidget(ctx.app._smfish_channel_cb)

    cl.addWidget(QLabel("FOV:", ctrl))
    ctx.app._smfish_fov_cb = QComboBox(ctrl)
    ctx.app._smfish_fov_cb.currentIndexChanged.connect(
        lambda _i: _smfish_load_selected_images(ctx.app)
    )
    cl.addWidget(ctx.app._smfish_fov_cb)

    cl.addWidget(QLabel("Timepoint:", ctrl))
    ctx.app._smfish_tp_cb = QComboBox(ctrl)
    ctx.app._smfish_tp_cb.currentIndexChanged.connect(
        lambda _i: _smfish_load_selected_images(ctx.app)
    )
    cl.addWidget(ctx.app._smfish_tp_cb)

    cl.addWidget(QLabel("smFISH_Thresh:", ctrl))
    ctx.app._smfish_threshold_edit = QLineEdit("1500", ctrl)
    ctx.app._smfish_threshold_edit.setFixedWidth(90)
    ctx.app._smfish_threshold_edit.editingFinished.connect(lambda: _smfish_redraw(ctx.app))
    cl.addWidget(ctx.app._smfish_threshold_edit)

    cl.addWidget(QLabel("LUT Min:", ctrl))
    ctx.app._smfish_lut_min_edit = QLineEdit("", ctrl)
    ctx.app._smfish_lut_min_edit.setFixedWidth(70)
    ctx.app._smfish_lut_min_edit.editingFinished.connect(lambda: _smfish_redraw(ctx.app))
    cl.addWidget(ctx.app._smfish_lut_min_edit)

    cl.addWidget(QLabel("LUT Max:", ctrl))
    ctx.app._smfish_lut_max_edit = QLineEdit("", ctrl)
    ctx.app._smfish_lut_max_edit.setFixedWidth(70)
    ctx.app._smfish_lut_max_edit.editingFinished.connect(lambda: _smfish_redraw(ctx.app))
    cl.addWidget(ctx.app._smfish_lut_max_edit)
    cl.addStretch(1)
    root.addWidget(ctrl)

    # Row 2: action buttons
    btn_row = QWidget(parent)
    btn_row.setObjectName("TabCtrl")
    bl = QHBoxLayout(btn_row)
    bl.setContentsMargins(10, 2, 10, 2)

    apply_btn = QPushButton("Apply Global Threshold", btn_row)
    apply_btn.setProperty("variant", "secondary")
    apply_btn.clicked.connect(lambda _=False: _smfish_apply_to_all(ctx.app))
    bl.addWidget(apply_btn)

    refresh_btn = QPushButton("Refresh", btn_row)
    refresh_btn.setProperty("variant", "secondary")
    refresh_btn.clicked.connect(lambda _=False: _smfish_redraw(ctx.app))
    bl.addWidget(refresh_btn)

    ctx.app._smfish_overlay_btn = QPushButton("Hide Overlays", btn_row)
    ctx.app._smfish_overlay_btn.setProperty("variant", "secondary")
    ctx.app._smfish_overlay_btn.clicked.connect(lambda _=False: _smfish_toggle_overlays(ctx.app))
    bl.addWidget(ctx.app._smfish_overlay_btn)

    cdf_btn = QPushButton("This Frame CDF", btn_row)
    cdf_btn.setProperty("variant", "secondary")
    cdf_btn.clicked.connect(lambda _=False: _smfish_open_cdf_popup(ctx.app))
    bl.addWidget(cdf_btn)
    bl.addStretch(1)
    root.addWidget(btn_row)

    # Status row
    ctx.app._smfish_status_label = QLabel(
        "Select a single well from the global picker.", parent
    )
    ctx.app._smfish_status_label.setObjectName("Muted")
    root.addWidget(ctx.app._smfish_status_label)

    # Matplotlib image
    ctx.app._smfish_fig_img = Figure(figsize=(6, 5), dpi=100)
    ctx.app._smfish_ax_img = ctx.app._smfish_fig_img.add_subplot(111)
    ctx.app._smfish_canvas_img = FigureCanvas(ctx.app._smfish_fig_img)
    root.addWidget(ctx.app._smfish_canvas_img, 1)

    from widgets.mpl_toolbar import MplToolbar
    ctx.app._smfish_toolbar = MplToolbar(ctx.app._smfish_canvas_img, parent)
    root.addWidget(ctx.app._smfish_toolbar)

    ctx.app._smfish_canvas_img.mpl_connect(
        "motion_notify_event", lambda ev: _smfish_on_img_hover(ctx.app, ev)
    )
    ctx.app._smfish_canvas_img.mpl_connect(
        "scroll_event", lambda ev: _smfish_on_img_scroll(ctx.app, ev)
    )
    ctx.app._smfish_canvas_img.mpl_connect(
        "button_press_event", lambda ev: _smfish_on_img_press(ctx.app, ev)
    )
    ctx.app._smfish_canvas_img.mpl_connect(
        "button_release_event", lambda ev: _smfish_on_img_release(ctx.app, ev)
    )
    ctx.app._smfish_canvas_img.mpl_connect(
        "motion_notify_event", lambda ev: _smfish_on_img_drag(ctx.app, ev)
    )


# ── public API ────────────────────────────────────────────────────────────


def smfish_sync_from_app(app) -> None:
    """Repopulate combo boxes from the loaded dataset's ``pipeline_info.json``.

    Called when a new dataset opens and when the user navigates to the
    smFISH tab; a no-op if the tab hasn't been built yet.
    """
    if not hasattr(app, "_smfish_channel_cb"):
        return  # tab not built yet

    out_dir = app._app_state.dataset.data_dir or app._smfish_out_dir
    if out_dir is None:
        _smfish_set_status(app, "No output loaded.")
        return
    info_path = out_dir / "pipeline_info.json"
    if not info_path.exists():
        app._smfish_out_dir = out_dir
        app._smfish_tokens = []
        app._smfish_well_to_zip = {}
        app._smfish_fov_tp_extractor = None
        _smfish_show_no_smfish_state(app)
        return
    try:
        info = json.loads(info_path.read_text())
        app._smfish_tokens = [
            str(t).strip() for t in info.get("smfish_tokens", []) if str(t).strip()
        ]
        app._smfish_separator = str(info.get("separator", "_"))
        app._smfish_classifier = make_classifier(app._smfish_separator)
        fov_idx = int(info.get("fov_index", -1))
        tp_idx = int(info.get("tp_index", -1))
        if tp_idx >= 0 and fov_idx >= 0:
            app._smfish_fov_tp_extractor = make_schema_extractor(
                app._smfish_separator, fov_idx, tp_idx
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
            _sep = app._smfish_separator
            _tp_idx = tp_idx

            def _single_fov_extractor(stem: str):
                parts = stem.split(_sep)
                tp = parts[_tp_idx] if 0 <= _tp_idx < len(parts) else "unknown"
                return "1", tp

            app._smfish_fov_tp_extractor = _single_fov_extractor
        else:
            app._smfish_fov_tp_extractor = None
    except Exception as exc:
        logger.warning("Could not parse %s: %s", info_path, exc)
        app._smfish_out_dir = out_dir
        app._smfish_tokens = []
        app._smfish_well_to_zip = {}
        app._smfish_fov_tp_extractor = None
        _smfish_show_no_smfish_state(app)
        return

    app._smfish_out_dir = out_dir

    if not app._smfish_tokens:
        app._smfish_well_to_zip = {}
        _smfish_show_no_smfish_state(app)
        return

    _smfish_set_controls_enabled(app, True)

    zips = sorted(out_dir.glob("*_out.zip"))
    app._smfish_well_to_zip = {}
    for z in zips:
        m = re.match(r"([A-Ha-h])(\d{1,2})_out\.zip$", z.name)
        if m:
            app._smfish_well_to_zip[
                f"{m.group(1).upper()}{int(m.group(2)):02d}"
            ] = z

    channels = app._smfish_tokens
    app._smfish_channel_cb.blockSignals(True)
    app._smfish_channel_cb.clear()
    app._smfish_channel_cb.addItems(channels)
    app._smfish_channel_cb.blockSignals(False)
    if channels:
        app._smfish_channel_cb.setCurrentIndex(0)
    _smfish_refresh_fov_tp_values(app)


# ── internal helpers ──────────────────────────────────────────────────────


def _smfish_set_status(app, text: str) -> None:
    app._smfish_status_label.setText(text)


def _smfish_toggle_overlays(app) -> None:
    app._smfish_show_overlays = not app._smfish_show_overlays
    app._smfish_overlay_btn.setText(
        "Hide Overlays" if app._smfish_show_overlays else "Show Overlays"
    )
    _smfish_redraw(app)


def _smfish_open_cdf_popup(app) -> None:
    if app._smfish_cdf_popup is not None and app._smfish_cdf_popup.isVisible():
        app._smfish_cdf_popup.raise_()
        app._smfish_cdf_popup.activateWindow()
        _smfish_update_cdf_plot(app)
        return

    popup = QDialog(app._smfish_frame)
    popup.setWindowTitle("smFISH CDF")
    popup.resize(760, 380)
    vl = QVBoxLayout(popup)
    app._smfish_cdf_fig = app._smfish_Figure(figsize=(7.2, 3.4), dpi=100)
    app._smfish_cdf_ax = app._smfish_cdf_fig.add_subplot(111)
    app._smfish_cdf_canvas = app._smfish_FigureCanvas(app._smfish_cdf_fig)
    vl.addWidget(app._smfish_cdf_canvas)
    popup.finished.connect(lambda _r: _smfish_close_cdf_popup(app))
    app._smfish_cdf_popup = popup
    popup.show()
    _smfish_update_cdf_plot(app)


def _smfish_close_cdf_popup(app) -> None:
    app._smfish_cdf_popup = None
    app._smfish_cdf_canvas = None
    app._smfish_cdf_ax = None
    app._smfish_cdf_fig = None


def _smfish_update_cdf_plot(app) -> None:
    if app._smfish_cdf_ax is None:
        return

    bg_panel = get_color("BG_PANEL")
    txt_pri = get_color("TXT_PRI")
    txt_mut = get_color("TXT_MUT")
    accent = get_color("ACCENT")

    log_img = app._smfish_current_log_img
    labels = app._smfish_current_labels
    thr = _smfish_get_threshold(app)
    ax = app._smfish_cdf_ax
    ax.clear()
    ax.set_facecolor(bg_panel)
    if app._smfish_cdf_fig is not None:
        app._smfish_cdf_fig.patch.set_facecolor(bg_panel)

    if log_img is None or labels is None:
        ax.text(0.5, 0.5, "No smFISH image loaded.", transform=ax.transAxes,
                ha="center", va="center", color=txt_mut, fontsize=10)
        if app._smfish_cdf_canvas is not None:
            app._smfish_cdf_canvas.draw_idle()
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
    if app._smfish_cdf_canvas is not None:
        app._smfish_cdf_canvas.draw_idle()


def _smfish_set_controls_enabled(app, enabled: bool) -> None:
    for w in (app._smfish_channel_cb, app._smfish_fov_cb, app._smfish_tp_cb,
              app._smfish_threshold_edit, app._smfish_lut_min_edit,
              app._smfish_lut_max_edit):
        try:
            w.setEnabled(enabled)
        except Exception:
            pass


def _smfish_show_no_smfish_state(app) -> None:
    _smfish_set_status(app, _NO_SMFISH_MESSAGE.replace("\n", "  "))
    for cb in (app._smfish_channel_cb, app._smfish_fov_cb, app._smfish_tp_cb):
        cb.blockSignals(True)
        cb.clear()
        cb.blockSignals(False)
    _smfish_set_controls_enabled(app, False)
    app._smfish_well_to_zip = {}
    app._smfish_current_log_img = None
    app._smfish_current_labels = None
    app._smfish_current_sorted_vals = None
    try:
        app._smfish_ax_img.clear()
        app._smfish_ax_img.set_axis_off()
        app._smfish_ax_img.text(
            0.5, 0.5, _NO_SMFISH_MESSAGE,
            transform=app._smfish_ax_img.transAxes,
            ha="center", va="center",
            color="#888888",
            fontsize=11, wrap=True,
        )
        if app._smfish_canvas_img is not None:
            app._smfish_canvas_img.draw_idle()
    except Exception:
        pass


def _smfish_selected_well_token(app) -> str | None:
    sels = sorted(app._app_state.selection.selected_wells, key=lambda lbl: app._parse_rc(lbl))
    if len(sels) != 1:
        return None
    tok = app._extract_well_token(sels[0]) or sels[0]
    m = re.match(r"([A-Ha-h])(\d{1,2})$", tok.strip())
    if not m:
        return None
    return f"{m.group(1).upper()}{int(m.group(2)):02d}"


def _smfish_scan_selected_zip(app):
    if app._smfish_out_dir is None:
        return {}, {}
    well = _smfish_selected_well_token(app)
    channel = app._smfish_channel_cb.currentText().strip().lower()
    zip_path = app._smfish_well_to_zip.get(well)
    if not well or not channel or zip_path is None:
        return {}, {}
    return scan_well_zip(
        zip_path=zip_path,
        channel=channel,
        classifier=app._smfish_classifier,
        fov_tp_extractor=app._smfish_fov_tp_extractor,
    )


def _smfish_refresh_fov_tp_values(app) -> None:
    smfish, mask = _smfish_scan_selected_zip(app)
    keys = sorted(set(smfish).intersection(mask))
    fovs = sorted({k[0] for k in keys})
    tps = sorted({k[1] for k in keys})

    for cb, values in ((app._smfish_fov_cb, fovs), (app._smfish_tp_cb, tps)):
        cb.blockSignals(True)
        cb.clear()
        cb.addItems(values)
        cb.blockSignals(False)
    _smfish_load_selected_images(app)


def _smfish_load_selected_images(app) -> None:
    smfish, mask = _smfish_scan_selected_zip(app)
    fov_raw = app._smfish_fov_cb.currentText().strip()
    tp_raw = app._smfish_tp_cb.currentText().strip()
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
        _smfish_set_status(app, "No smFISH/mask pair found for current selection.")
        return
    result = read_image_arrays(sm_ref, mk_ref)
    if result is None:
        _smfish_set_status(app, "Failed to load selected image data.")
        return
    (app._smfish_current_log_img, app._smfish_current_labels,
     app._smfish_current_sorted_vals) = result
    well = _smfish_selected_well_token(app) or "N/A"
    _smfish_set_status(app, f"Loaded {well} fov={fov_raw} tp={tp_raw}.")
    app._smfish_fit_on_next_redraw = True
    _smfish_redraw(app)


def _smfish_get_threshold(app) -> float:
    try:
        return float(app._smfish_threshold_edit.text().strip())
    except ValueError:
        return 0.0


def _smfish_redraw(app) -> None:
    if app._smfish_current_log_img is None or app._smfish_current_labels is None:
        return
    txt_pri = get_color("TXT_PRI")
    thr = _smfish_get_threshold(app)
    log_img = app._smfish_current_log_img
    labels = app._smfish_current_labels
    prev_xlim = app._smfish_ax_img.get_xlim()
    prev_ylim = app._smfish_ax_img.get_ylim()
    lut_min_txt = app._smfish_lut_min_edit.text().strip()
    lut_max_txt = app._smfish_lut_max_edit.text().strip()
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

    app._smfish_ax_img.clear()
    app._smfish_ax_img.imshow(log_img, cmap="gray", vmin=lut_min, vmax=lut_max)
    # skimage is multi-second to import on cold caches; defer until the
    # first redraw so opening the smFISH tab without analysing data
    # doesn't pay the cost.
    from skimage.segmentation import find_boundaries
    bnd = find_boundaries(labels, mode="outer")
    if app._smfish_show_overlays:
        app._smfish_ax_img.contour(
            bnd.astype(np.uint8), levels=[0.5], colors="red", linewidths=0.5
        )
        if xs.size:
            app._smfish_ax_img.scatter(
                xs, ys, s=20, facecolors="none", edgecolors="cyan", linewidths=0.6
            )
    app._smfish_ax_img.set_title(
        f"Spots above threshold: {int(xs.size)}", color=txt_pri, fontsize=10
    )
    app._smfish_ax_img.set_xticks([])
    app._smfish_ax_img.set_yticks([])
    if app._smfish_fit_on_next_redraw:
        h, w = log_img.shape[:2]
        app._smfish_ax_img.set_xlim(-0.5, w - 0.5)
        app._smfish_ax_img.set_ylim(h - 0.5, -0.5)
        app._smfish_fit_on_next_redraw = False
    else:
        app._smfish_ax_img.set_xlim(prev_xlim)
        app._smfish_ax_img.set_ylim(prev_ylim)
    if app._smfish_hover_annot is None:
        app._smfish_hover_annot = app._smfish_ax_img.annotate(
            "",
            xy=(0, 0),
            xytext=(8, 8),
            textcoords="offset points",
            color="white",
            fontsize=8,
            bbox=dict(boxstyle="round,pad=0.2", fc="black", ec="white",
                      lw=0.6, alpha=0.8),
        )
        app._smfish_hover_annot.set_visible(False)

    app._smfish_canvas_img.draw_idle()
    _smfish_update_cdf_plot(app)


def _smfish_on_img_hover(app, event) -> None:
    if app._smfish_current_log_img is None or event.inaxes != app._smfish_ax_img:
        if app._smfish_hover_annot is not None and app._smfish_hover_annot.get_visible():
            app._smfish_hover_annot.set_visible(False)
            app._smfish_canvas_img.draw_idle()
        return
    if event.xdata is None or event.ydata is None or app._smfish_hover_annot is None:
        return
    x = int(round(event.xdata))
    y = int(round(event.ydata))
    h, w = app._smfish_current_log_img.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        app._smfish_hover_annot.set_visible(False)
        app._smfish_canvas_img.draw_idle()
        return
    if app._smfish_current_labels is None or int(app._smfish_current_labels[y, x]) <= 0:
        app._smfish_hover_annot.set_visible(False)
        app._smfish_canvas_img.draw_idle()
        return
    px = float(app._smfish_current_log_img[y, x])
    app._smfish_hover_annot.xy = (x, y)
    app._smfish_hover_annot.set_text(f"({x}, {y}) = {px:.3f}")
    app._smfish_hover_annot.set_visible(True)
    app._smfish_canvas_img.draw_idle()


def _smfish_on_img_scroll(app, event) -> None:
    if event.inaxes != app._smfish_ax_img or event.xdata is None or event.ydata is None:
        return
    scale = 1 / 1.2 if event.button == "up" else 1.2
    xlim = app._smfish_ax_img.get_xlim()
    ylim = app._smfish_ax_img.get_ylim()
    new_w = (xlim[1] - xlim[0]) * scale
    new_h = (ylim[1] - ylim[0]) * scale
    relx = (event.xdata - xlim[0]) / (xlim[1] - xlim[0]) if xlim[1] != xlim[0] else 0.5
    rely = (event.ydata - ylim[0]) / (ylim[1] - ylim[0]) if ylim[1] != ylim[0] else 0.5
    app._smfish_ax_img.set_xlim(
        event.xdata - new_w * relx, event.xdata + new_w * (1 - relx)
    )
    app._smfish_ax_img.set_ylim(
        event.ydata - new_h * rely, event.ydata + new_h * (1 - rely)
    )
    app._smfish_canvas_img.draw_idle()


def _smfish_on_img_press(app, event) -> None:
    if (event.inaxes != app._smfish_ax_img or event.button != 1
            or event.xdata is None or event.ydata is None):
        return
    app._smfish_pan_anchor = (
        event.xdata, event.ydata,
        app._smfish_ax_img.get_xlim(), app._smfish_ax_img.get_ylim(),
    )


def _smfish_on_img_release(app, _event) -> None:
    app._smfish_pan_anchor = None


def _smfish_on_img_drag(app, event) -> None:
    if (app._smfish_pan_anchor is None or event.inaxes != app._smfish_ax_img
            or event.xdata is None or event.ydata is None):
        return
    x0, y0, xlim0, ylim0 = app._smfish_pan_anchor
    dx = event.xdata - x0
    dy = event.ydata - y0
    app._smfish_ax_img.set_xlim(xlim0[0] - dx, xlim0[1] - dx)
    app._smfish_ax_img.set_ylim(ylim0[0] - dy, ylim0[1] - dy)
    app._smfish_canvas_img.draw_idle()


def _smfish_apply_to_all(app) -> None:
    if app._smfish_out_dir is None:
        _smfish_set_status(app, "No output loaded.")
        return
    channel = app._smfish_channel_cb.currentText().strip().lower()
    thr = _smfish_get_threshold(app)
    frame = app._smfish_frame

    def _after_csv() -> None:
        QTimer.singleShot(0, lambda: _smfish_refresh_app_cache(app))
        QTimer.singleShot(
            0,
            lambda: QMessageBox.information(frame, "smFISH", "Apply to All finished."),
        )

    # Cancel event used by load_controller / app shutdown to abort
    # the in-flight smFISH run before it overwrites CSVs from a
    # different dataset.
    import threading as _threading
    if not hasattr(app, "_smfish_cancel_event") or app._smfish_cancel_event is None:
        app._smfish_cancel_event = _threading.Event()
    else:
        app._smfish_cancel_event.clear()

    apply_global_threshold_async(
        out_dir=app._smfish_out_dir,
        well_to_zip=app._smfish_well_to_zip,
        channel=channel,
        threshold=thr,
        classifier=app._smfish_classifier,
        fov_tp_extractor=app._smfish_fov_tp_extractor,
        status_cb=app._smfish_bridge.status.emit,
        done_cb=app._smfish_bridge.done.emit,
        after_csv_cb=_after_csv,
        cancel_event=app._smfish_cancel_event,
        expected_data_dir=app._smfish_out_dir,
    )


def _smfish_refresh_app_cache(app) -> None:
    for label, path in app._app_state.dataset.well_paths.items():
        if label in app._app_state.dataset.cache:
            app._app_state.dataset.cache[label] = app._load_well_csv(path)
    recalculate_threshold(app)
    app._redraw()
