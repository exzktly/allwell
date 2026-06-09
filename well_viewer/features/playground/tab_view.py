"""Playground tab — Qt builder.

``build_playground_tab(ctx, parent)`` assembles the single-frame correction
testbed: a left controls column (frame selectors, correction method + params,
display options) and a right zoomable canvas. Per ARCHITECTURE.md §5.3 it reads
``ctx.app`` directly (no ``app = ctx.app`` unwrap) and wires every signal to the
controller via ``functools.partial`` — no new ``runtime_app`` proxies.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from process_microscopy import BACKGROUND_METHODS, FLATFIELD_MODES
from well_viewer.features.playground.controller import (
    playground_on_change, playground_repopulate_dropdowns,
)
from well_viewer.features.playground.renderer import render_playground_display


def _row(parent, label_text: str) -> QWidget:
    row = QWidget(parent)
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 1, 0, 1)
    rl.setSpacing(6)
    lbl = QLabel(label_text, row)
    lbl.setObjectName("Muted")
    lbl.setFixedWidth(96)
    rl.addWidget(lbl)
    cell = QWidget(row)
    QHBoxLayout(cell).setContentsMargins(0, 0, 0, 0)
    rl.addWidget(cell, 1)
    parent.layout().addWidget(row)
    return cell


def build_playground_tab(ctx, parent: QWidget) -> None:
    # ctx is a ViewerContext (carries .app); tolerate a bare app in headless
    # tests. This getattr is not the guardrailed `app = ctx.app` unwrap.
    app = getattr(ctx, "app", ctx)
    if parent.layout() is None:
        QHBoxLayout(parent).setContentsMargins(0, 0, 0, 0)
    on_change = partial(playground_on_change, app)

    # ── Left: controls column ────────────────────────────────────────────────
    controls = QWidget(parent)
    controls.setFixedWidth(320)
    cl = QVBoxLayout(controls)
    cl.setContentsMargins(10, 10, 10, 10)
    cl.setSpacing(4)

    def _section(title: str) -> None:
        lbl = QLabel(title, controls)
        lbl.setObjectName("SectionTitle")
        cl.addWidget(lbl)
        rule = QFrame(controls)
        rule.setFrameShape(QFrame.HLine)
        rule.setObjectName("HRule")
        cl.addWidget(rule)

    def _combo(cell, items, attr: str) -> QComboBox:
        cb = QComboBox(cell)
        if items:
            cb.addItems(list(items))
        cb.currentIndexChanged.connect(lambda _i: on_change())
        cell.layout().addWidget(cb)
        setattr(app, attr, cb)
        return cb

    def _edit(cell, default: str, attr: str, width: int = 70) -> QLineEdit:
        e = QLineEdit(default, cell)
        e.setFixedWidth(width)
        e.editingFinished.connect(on_change)
        cell.layout().addWidget(e)
        cell.layout().addStretch(1)
        setattr(app, attr, e)
        return e

    # Frame selection
    _section("Frame")
    _combo(_row(controls, "Well"), [], "_playground_well_cb")
    _combo(_row(controls, "Channel"), [], "_playground_chan_cb")
    _combo(_row(controls, "Timepoint"), [], "_playground_tp_cb")
    _combo(_row(controls, "FOV"), [], "_playground_fov_cb")

    # Correction method
    _section("Correction")
    ff_cb = _combo(_row(controls, "Flat-field"), FLATFIELD_MODES, "_playground_ff_cb")
    bg_cb = _combo(_row(controls, "Background"), BACKGROUND_METHODS, "_playground_bg_cb")
    bg_cb.setCurrentText("sliding_disk")

    radius_row = _row(controls, "Radius")
    _edit(radius_row, "50", "_playground_radius_edit")
    fft_row = _row(controls, "Small px")
    _edit(fft_row, "0", "_playground_fft_small_edit")
    mesh_ratio_row = _row(controls, "Mesh ratio")
    _edit(mesh_ratio_row, "3.0", "_playground_mesh_ratio_edit")
    mesh_sigma_row = _row(controls, "Mesh sigma")
    _edit(mesh_sigma_row, "3.0", "_playground_mesh_sigma_edit")
    bgwell_row = _row(controls, "Bg well")
    bgwell_edit = QLineEdit("", bgwell_row)
    bgwell_edit.setPlaceholderText("e.g. H12")
    bgwell_edit.editingFinished.connect(on_change)
    bgwell_row.layout().addWidget(bgwell_edit)
    app._playground_bgwell_edit = bgwell_edit

    # Display options
    _section("Display")
    src_row = _row(controls, "Source")
    raw_corr_cb = QComboBox(src_row)
    raw_corr_cb.addItems(["Corrected", "Raw"])

    def _on_source(_i: int) -> None:
        app._app_state.playground.show_corrected = (raw_corr_cb.currentText() == "Corrected")
        on_change()
    raw_corr_cb.currentIndexChanged.connect(_on_source)
    src_row.layout().addWidget(raw_corr_cb)
    app._playground_source_cb = raw_corr_cb

    lut_row = _row(controls, "LUT min/max")
    lmin = QLineEdit("auto", lut_row)
    lmin.setFixedWidth(64)
    lmax = QLineEdit("auto", lut_row)
    lmax.setFixedWidth(64)
    lmin.editingFinished.connect(on_change)
    lmax.editingFinished.connect(on_change)
    lut_row.layout().addWidget(lmin)
    lut_row.layout().addWidget(lmax)
    lut_row.layout().addStretch(1)
    app._playground_lmin_entry = lmin
    app._playground_lmax_entry = lmax

    cmap_row = _row(controls, "Colormap")
    from widgets.lut_selector import LutSelector
    cmap = LutSelector(cmap_row, lut="Greys", reversed=True, default="Greys")
    cmap.lutChanged.connect(lambda *_a: on_change())
    cmap_row.layout().addWidget(cmap)
    app._playground_cmap_cb = cmap

    toggles = QWidget(controls)
    tl = QHBoxLayout(toggles)
    tl.setContentsMargins(0, 2, 0, 2)
    binary_cb = QCheckBox("Binary", toggles)
    overlay_cb = QCheckBox("Mask overlay", toggles)
    overlay_cb.setChecked(True)

    def _on_binary(v: bool) -> None:
        app._app_state.playground.binary_mask = bool(v)
        on_change()

    def _on_overlay(v: bool) -> None:
        app._app_state.playground.show_overlay = bool(v)
        on_change()
    binary_cb.toggled.connect(_on_binary)
    overlay_cb.toggled.connect(_on_overlay)
    tl.addWidget(binary_cb)
    tl.addWidget(overlay_cb)
    tl.addStretch(1)
    cl.addWidget(toggles)
    app._playground_binary_cb = binary_cb
    app._playground_overlay_cb = overlay_cb

    # Zoom buttons
    zoom_row = QWidget(controls)
    zl = QHBoxLayout(zoom_row)
    zl.setContentsMargins(0, 2, 0, 2)
    zl.addWidget(QLabel("Zoom", zoom_row))

    def _zoom(delta: float) -> None:
        st = app._app_state.playground
        st.zoom = max(0.1, min(8.0, st.zoom * delta))
        render_playground_display(app)
    zout = QPushButton("−", zoom_row)
    zout.setFixedWidth(28)
    zout.clicked.connect(lambda _=False: _zoom(1 / 1.25))
    zin = QPushButton("+", zoom_row)
    zin.setFixedWidth(28)
    zin.clicked.connect(lambda _=False: _zoom(1.25))
    zfit = QPushButton("Fit", zoom_row)
    zfit.clicked.connect(lambda _=False: (setattr(app._app_state.playground, "zoom", 1.0),
                                          render_playground_display(app)))
    zl.addWidget(zout)
    zl.addWidget(zin)
    zl.addWidget(zfit)
    zl.addStretch(1)
    cl.addWidget(zoom_row)

    status = QLabel("Select a frame to begin.", controls)
    status.setObjectName("Muted")
    status.setWordWrap(True)
    cl.addWidget(status)
    app._playground_status = status
    cl.addStretch(1)
    parent.layout().addWidget(controls)

    # ── Right: zoomable canvas ───────────────────────────────────────────────
    canvas = QScrollArea(parent)
    canvas.setWidgetResizable(False)
    canvas.setAlignment(Qt.AlignCenter)
    label = QLabel(canvas)
    label.setAlignment(Qt.AlignCenter)
    canvas.setWidget(label)
    parent.layout().addWidget(canvas, 1)
    app._playground_image_canvas = canvas
    app._playground_image_label = label

    # Show/hide param rows by method (mirrors analyze_tab._sync_bg).
    def _sync_method_rows(_=None) -> None:
        bg = app._playground_bg_cb.currentText()
        ff = app._playground_ff_cb.currentText()
        radius_row.parentWidget().setVisible(
            bg in ("top_hat", "sliding_disk", "robust_mesh", "fft_bandpass"))
        fft_row.parentWidget().setVisible(bg == "fft_bandpass")
        for r in (mesh_ratio_row, mesh_sigma_row):
            r.parentWidget().setVisible(bg == "robust_mesh")
        bgwell_row.parentWidget().setVisible(
            ff in ("empty_well", "basic") or bg == "empty_well")
    bg_cb.currentIndexChanged.connect(_sync_method_rows)
    ff_cb.currentIndexChanged.connect(_sync_method_rows)
    _sync_method_rows()

    playground_repopulate_dropdowns(app)
    on_change()
