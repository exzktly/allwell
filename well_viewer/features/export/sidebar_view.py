"""Export-style sidebar view (extracted from ``figure_export_editor``).

Hosts the ``ExportStyleSidebar`` widget that lives inside each plot tab's
right-side dock. Pure view: prefs persistence, profile lookups, and the
matplotlib export pipeline live in ``well_viewer.figure_export_editor``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from well_viewer.features.export.style import (
    DEFAULT_EXPORT_STYLE_PREFS,
    EXPORT_PROFILES,
    _ensure_custom_export_profiles,
    _ensure_export_style_prefs,
    _get_all_profile_names,
    _resolve_export_dock,
    apply_export_style_to_current,
)

# v2 chrome: this panel is composed from the custom widgets in ``widgets/``
# (CollapsibleSection, ToggleSwitch) plus theme-styled stock widgets. The
# binding layer (_bind_getter_setter / _getters / _setters / _persist /
# apply_export_style_to_current) is unchanged — only the layout was re-skinned.
import theme
from widgets.collapsible_section import CollapsibleSection
from widgets.toggle_switch import ToggleSwitch
from well_viewer.features.barplot.renderer import redraw_bars
from well_viewer.features.scatter.controller import redraw_scatter_agg_orchestrate
from well_viewer.features.scatter.controller import redraw_scatter_orchestrate
from well_viewer.features.scatter.gating import clear_gates, rename_gate, set_gate_draw_mode
from well_viewer.core.safe import swallowed


#: Single source of truth for the style-panel width — read by both the
#: sidebar itself and ``figure_export_editor.launch_export_editor`` when
#: it sizes the dock container. Bumping this value here resizes both
#: ends in lock-step (sizeHint() can lag the actual fixed width on the
#: first show, which is why launch path must NOT rely on sizeHint).
#: Width of the floating Export Style sidebar (the legacy per-card dock
#: opened by the sliders IconButton). Set so the panel fits inside the
#: centre column even on smaller screens — every internal row uses a
#: narrow fixed label column (88 px) plus an expanding control column so
#: the contents reflow within the panel rather than overflowing.
EXPORT_STYLE_PANEL_WIDTH = 440


#: Figure-export formats offered by the Export… dialog, in display order.
#: ``label`` is the Qt file-filter prefix; the savefig format is the dict key.
_EXPORT_FILTERS: dict[str, str] = {
    "png": "PNG (*.png)",
    "tiff": "TIFF (*.tiff *.tif)",
    "svg": "SVG (*.svg)",
    "eps": "EPS (*.eps)",
    "pdf": "Vector PDF (*.pdf)",
}
_EXPORT_FILTER_ORDER = ("png", "tiff", "svg", "eps", "pdf")


class ExportStyleSidebar(QWidget):
    def __init__(self, app, parent, fig, canvas, default_name: str):
        super().__init__(parent)
        self.setObjectName("PropertyPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#PropertyPanel {{ background-color: {theme.Colors.rail}; }}")
        self._app = app
        self._fig = fig
        self._canvas = canvas
        self._default_name = default_name
        self._base_dir = Path(app._app_state.dataset.data_dir) if app._app_state.dataset.data_dir else Path.cwd()
        self._prefs = _ensure_export_style_prefs(app)
        self._updating = False
        self._getters: dict[str, Callable[[], object]] = {}
        self._setters: dict[str, Callable[[object], None]] = {}

        self.setFixedWidth(EXPORT_STYLE_PANEL_WIDTH)
        self.setMinimumWidth(EXPORT_STYLE_PANEL_WIDTH)
        self._build_ui()

    def _bind_getter_setter(self, key: str, widget) -> None:
        """Register read/write hooks and wire a change signal to auto-apply.

        Custom v2 widgets opt in via a ``bindingAdapter() -> (getter, setter,
        change_signal)`` method (see ``OPEN_DECISIONS.md`` #2 / Phase 6.5.1);
        stock widgets fall through to the ``isinstance`` branches below.
        """
        adapter = getattr(widget, "bindingAdapter", None)
        if callable(adapter):
            getter, setter, change_signal = adapter()
            self._getters[key] = getter
            self._setters[key] = lambda v, _s=setter: _s(v)
            change_signal.connect(lambda *_a: self._on_fields_changed())
            return
        if isinstance(widget, QSpinBox):
            self._getters[key] = widget.value
            self._setters[key] = lambda v, w=widget: w.setValue(int(v))
            widget.valueChanged.connect(lambda _v: self._on_fields_changed())
        elif isinstance(widget, QDoubleSpinBox):
            self._getters[key] = widget.value
            self._setters[key] = lambda v, w=widget: w.setValue(float(v))
            widget.valueChanged.connect(lambda _v: self._on_fields_changed())
        elif isinstance(widget, QComboBox):
            self._getters[key] = widget.currentText
            self._setters[key] = lambda v, w=widget: w.setCurrentText(str(v))
            widget.currentTextChanged.connect(lambda _t: self._on_fields_changed())
        elif isinstance(widget, QCheckBox):
            self._getters[key] = widget.isChecked
            self._setters[key] = lambda v, w=widget: w.setChecked(bool(v))
            widget.toggled.connect(lambda _c: self._on_fields_changed())
        elif isinstance(widget, QLineEdit):
            self._getters[key] = widget.text
            self._setters[key] = lambda v, w=widget: w.setText(str(v))
            widget.textChanged.connect(lambda _t: self._on_fields_changed())

    def _build_ui(self) -> None:
        sp = theme.Spacing
        root = QVBoxLayout(self)
        root.setContentsMargins(sp.sm, sp.sm, sp.sm, sp.sm)
        root.setSpacing(sp.sm)

        # ── header ──────────────────────────────────────────────────────────
        hdr = QWidget(self)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(sp.xs, 0, 0, 0)
        title = QLabel("Properties", hdr)
        title.setObjectName("Heading")
        hl.addWidget(title)
        hl.addStretch(1)
        from widgets.icon_button import IconButton as _IconButton
        close_btn = _IconButton("x", hdr)
        close_btn.setToolTip("Hide this panel")
        close_btn.clicked.connect(lambda _=False: self._close_dock())
        hl.addWidget(close_btn)
        root.addWidget(hdr)

        # ── scrollable stack of CollapsibleSections ─────────────────────────
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(sp.sm)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Per-section grid bookkeeping: {section: [grid, next_row]}.
        grids: dict[CollapsibleSection, list] = {}

        def section(name: str, *, expanded: bool = True) -> CollapsibleSection:
            sec = CollapsibleSection(name, expanded=expanded)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(sp.sm)
            grid.setVerticalSpacing(sp.xs)
            # Fixed narrow label column + expanding control column. Without
            # this, the longest label dictates the column width and pushes
            # controls past the panel's fixed width.
            grid.setColumnMinimumWidth(0, 96)
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(1, 1)
            sec.addLayout(grid)
            grids[sec] = [grid, 0]
            body_layout.addWidget(sec)
            return sec

        def add_row(sec: CollapsibleSection, label: str, widget: QWidget,
                    key: str | None = None) -> None:
            grid, r = grids[sec]
            if label:
                grid.addWidget(QLabel(label), r, 0)
                grid.addWidget(widget, r, 1)
            else:
                grid.addWidget(widget, r, 0, 1, 2)
            if key is not None:
                self._bind_getter_setter(key, widget)
            grids[sec][1] = r + 1

        def add_full(sec: CollapsibleSection, widget: QWidget) -> None:
            grid, r = grids[sec]
            grid.addWidget(widget, r, 0, 1, 2)
            grids[sec][1] = r + 1

        def add_stacked(sec: CollapsibleSection, label: str, widget: QWidget,
                        key: str | None = None) -> None:
            """Label on its own row above the widget — for wide controls (e.g.
            three-segment SegmentedControls) that overflow the 2-column grid."""
            grid, r = grids[sec]
            lbl = QLabel(label)
            lbl.setProperty("variant", "muted")
            grid.addWidget(lbl, r, 0, 1, 2)
            grid.addWidget(widget, r + 1, 0, 1, 2)
            if key is not None:
                self._bind_getter_setter(key, widget)
            grids[sec][1] = r + 2

        def hrow(*items, spacing: int | None = None) -> QWidget:
            host = QWidget()
            hb = QHBoxLayout(host)
            hb.setContentsMargins(0, 0, 0, 0)
            hb.setSpacing(spacing if spacing is not None else sp.md)
            for w in items:
                hb.addWidget(w)
            hb.addStretch(1)
            return host

        # ── Profile & Format ────────────────────────────────────────────────
        s_profile = section("Profile & Format", expanded=True)
        self._profile_combo = QComboBox()
        self._profile_combo.addItems(_get_all_profile_names(self._app))
        self._profile_combo.setCurrentText(str(self._prefs.get("export_profile", "Custom")))
        self._profile_combo.currentTextChanged.connect(lambda _t: self._on_profile_selected())
        add_row(s_profile, "Profile", self._profile_combo, "export_profile")

        # ── Scatter: Density + Gates (per-cell scatter only) ─────────────────
        if self._is_scatter_cells_fig():
            s_density = section("Density", expanded=True)
            dens_tog = ToggleSwitch("Color by density")
            dens_tog.setChecked(bool(self._prefs.get("scatter_density", False)))
            add_row(s_density, "", dens_tog)
            # Manual getter/setter (no key= auto-wire): density changes which
            # artists exist, not just their style, so a single handler must both
            # persist and trigger a real redraw (the auto-wire only restyles).
            self._getters["scatter_density"] = dens_tog.isChecked
            self._setters["scatter_density"] = (
                lambda v, w=dens_tog: w.setChecked(bool(v))
            )
            dens_tog.toggled.connect(lambda _c: self._on_density_toggled())
            _dens_preview = QLabel("On" if dens_tog.isChecked() else "Off")
            _dens_preview.setObjectName("Muted")
            s_density.setValueWidget(_dens_preview)
            dens_tog.toggled.connect(
                lambda on, _l=_dens_preview: _l.setText("On" if on else "Off")
            )

            s_gates = section("Gates", expanded=True)
            show_tog = ToggleSwitch("Show gates")
            show_tog.setChecked(not bool(getattr(self._app, "_scatter_gates_hidden", False)))
            show_tog.toggled.connect(lambda on: self._on_show_gates_toggled(on))
            add_row(s_gates, "", show_tog)
            # One-shot "Draw gate" button (not a toggle): clicking arms a
            # single drag-to-draw on the plot; gating.py disarms after the gate
            # is drawn and calls _scatter_gate_draw_sync(False) to restore the
            # button label.
            draw_btn = QPushButton("Draw gate")

            def _arm_draw(_=False, _b=draw_btn) -> None:
                set_gate_draw_mode(self._app, True)
                _b.setText("Drag on the plot…")
            draw_btn.clicked.connect(_arm_draw)
            add_full(s_gates, draw_btn)
            self._gate_draw_btn = draw_btn

            def _sync_draw_toggle(on: bool, _b=draw_btn) -> None:
                # Restore the button once the one-shot draw completes.
                if not on:
                    _b.setText("Draw gate")
            self._app._scatter_gate_draw_sync = _sync_draw_toggle
            clear_btn = QPushButton("Clear gates")
            clear_btn.clicked.connect(lambda _=False: self._on_clear_gates())
            add_full(s_gates, clear_btn)
            self._gate_count_label = QLabel(self._gate_count_text())
            self._gate_count_label.setObjectName("Muted")
            add_full(s_gates, self._gate_count_label)
            # Editable per-gate name list (auto-named Gate N on draw; rename here).
            self._gate_list_box = QWidget()
            self._gate_list_layout = QVBoxLayout(self._gate_list_box)
            self._gate_list_layout.setContentsMargins(0, 0, 0, 0)
            self._gate_list_layout.setSpacing(2)
            add_full(s_gates, self._gate_list_box)
            self._rebuild_gate_list()
            # Keep the gate-count label + name list live when a gate is drawn on
            # the plot (gating.py calls this after appending/clearing a gate).
            self._app._scatter_gate_count_cb = self._refresh_gate_count

        # File format selector intentionally omitted — the Save As… dialog
        # offers PDF / SVG / EPS / PNG via the file filter, so picking the
        # format up front was redundant. The "format" pref (read by
        # ``_export`` when the dedicated Export… button is used) still
        # falls back to "png" if absent.

        # Per-axis xlim/ylim/log buckets so each axis keeps its own values when
        # the Axis # dropdown is flipped. Unchanged from the legacy panel — the
        # flat ``_prefs`` keys reflect whatever axis is currently selected.
        self._axis_keys = (
            "x_lim_min", "x_lim_max", "y_lim_min", "y_lim_max",
            "x_log", "y_log",
        )
        self._axis_buckets: dict[str, dict] = dict(self._prefs.get("axis_buckets") or {})
        # Default the targeted axis to the subplot showing the active metric (so
        # edits land on the plot in view) rather than "All"; a saved explicit
        # choice is respected.
        self._current_axis = self._default_axis_target()
        self._axis_buckets.setdefault(
            self._current_axis, {k: self._prefs.get(k) for k in self._axis_keys},
        )

        # ── Axes (incl. ticks) ──────────────────────────────────────────────
        s_axes = section("Axes", expanded=True)
        axis_cb = QComboBox()
        axis_cb.addItems(["All", *[str(i + 1) for i in range(len(self._fig.axes))]])
        axis_cb.setCurrentText(self._current_axis)
        # Getter/setter only (no auto-apply): switching the target axis must
        # not push the currently displayed limits onto the new one.
        add_row(s_axes, "Axis #", axis_cb)
        self._axis_cb = axis_cb
        self._getters["axis_target"] = axis_cb.currentText
        self._setters["axis_target"] = lambda v, w=axis_cb: w.setCurrentText(str(v))
        axis_cb.currentTextChanged.connect(lambda t: self._on_axis_target_changed(t))
        # Click a subplot in the figure to target it: the Axis # field tracks the
        # axis currently being viewed. Connected once (the sidebar is cached per
        # figure); the handler no-ops while the sidebar is hidden.
        self._axis_pick_cid = None
        self._axis_highlight = None
        if self._canvas is not None:
            try:
                self._axis_pick_cid = self._canvas.mpl_connect(
                    "button_press_event", self._on_canvas_axis_pick
                )
            except Exception:
                self._axis_pick_cid = None
        from widgets.stepper import Stepper as _Stepper
        for key, label, lo, hi in [
            ("axis_label_size", "Axis label size", 1, 96),
            ("tick_label_size", "Tick label size", 1, 96),
            ("title_size", "Title size", 1, 128),
            ("x_tick_angle", "X tick angle°", 0, 90),
            ("group_label_size", "Group label size", 1, 96),
        ]:
            spin = _Stepper(minimum=lo, maximum=hi, single_step=1,
                            value=float(self._prefs[key]), decimals=0)
            add_row(s_axes, label, spin, key)
        maj_cb = ToggleSwitch("Major")
        maj_cb.setChecked(bool(self._prefs["tick_major"]))
        min_cb = ToggleSwitch("Minor")
        min_cb.setChecked(bool(self._prefs["tick_minor"]))
        self._bind_getter_setter("tick_major", maj_cb)
        self._bind_getter_setter("tick_minor", min_cb)
        add_row(s_axes, "Tick visibility", hrow(maj_cb, min_cb))
        tlen = _Stepper(minimum=0.0, maximum=20.0, single_step=0.5,
                        value=float(self._prefs["tick_length"]), decimals=1)
        add_row(s_axes, "Tick length", tlen, "tick_length")
        tdir = QComboBox()
        tdir.addItems(["out", "in", "inout"])
        tdir.setCurrentText(str(self._prefs["tick_direction"]))
        add_row(s_axes, "Tick direction", tdir, "tick_direction")

        # ── Statistics (v2 §6.2) ────────────────────────────────────────────
        # Writes through to app state (_use_sem / _toggle_fov_replicates) so the
        # error band / spread on the *plots* updates immediately. NOTE: the
        # plot cards' band-controls row writes to the same state, so changes
        # there don't auto-refresh this section's segments until the sidebar
        # is reopened — sync is one-way (sidebar → app).
        from widgets.plot_card import _make_segmented as _seg
        s_stats = section("Statistics", expanded=False)
        self._stats_preview = QLabel("")
        self._stats_preview.setObjectName("Muted")
        s_stats.setValueWidget(self._stats_preview)

        _stats_state = {"err": None, "across": None, "show": None}

        def _update_stats_preview() -> None:
            err = _stats_state["err"] or "—"
            acr = _stats_state["across"] or "—"
            self._stats_preview.setText(f"{err.upper()} · {acr}")

        # Error bars: SEM / SD wire through; None / 95% CI are placeholders.
        _err_init = "sem" if bool(self._app._app_state.plot.use_sem) else "sd"
        err_sc = _seg(
            [("None", "none"), ("SEM", "sem"), ("SD", "sd"), ("95% CI", "ci95")],
            current=_err_init,
        )
        if err_sc is not None:
            _stats_state["err"] = err_sc.currentData()

            def _on_err_change(*_a) -> None:
                v = err_sc.currentData()
                _stats_state["err"] = v
                _update_stats_preview()
                want_sem = (v == "sem")
                if v in ("sem", "sd") and bool(self._app._app_state.plot.use_sem) != want_sem:
                    try:
                        self._app._toggle_sem()
                    except Exception:
                        swallowed()  # was: pass (silent except)

            err_sc.currentChanged.connect(_on_err_change)
            add_stacked(s_stats, "Error bars", err_sc)

            # Auto-sync: when _use_sem flips elsewhere (the band-controls row on
            # any plot card), update this segment too.
            def _sync_err_from_app(use_sem: bool, _sc=err_sc) -> None:
                try:
                    target = "sem" if use_sem else "sd"
                    if _sc.currentData() != target:
                        _sc.blockSignals(True)
                        try:
                            _sc.setCurrentByData(target)
                        finally:
                            _sc.blockSignals(False)
                        _stats_state["err"] = target
                        _update_stats_preview()
                except Exception:
                    swallowed()  # was: pass (silent except)

            if not hasattr(self._app, "_sem_observers"):
                self._app._sem_observers = []
            self._app._sem_observers.append(_sync_err_from_app)

        # Across: Replicates vs FOV (single-well-mode FOV-spread toggle).
        _across_init = "fov" if bool(getattr(self._app, "_use_fov_spread_active", lambda: False)()) else "rep"
        across_sc = _seg(
            [("Replicates", "rep"), ("FOV", "fov")],
            current=_across_init,
        )
        if across_sc is not None:
            _stats_state["across"] = "FOV" if _across_init == "fov" else "Replicates"

            def _on_across_change(*_a) -> None:
                v = across_sc.currentData()
                _stats_state["across"] = "FOV" if v == "fov" else "Replicates"
                _update_stats_preview()
                want_fov = (v == "fov")
                if bool(getattr(self._app, "_use_fov_spread_active", lambda: False)()) != want_fov:
                    try:
                        self._app._toggle_fov_replicates()
                    except Exception:
                        swallowed()  # was: pass (silent except)

            across_sc.currentChanged.connect(_on_across_change)
            add_stacked(s_stats, "Across", across_sc)

            def _sync_across_from_app(use_fov: bool, _sc=across_sc) -> None:
                try:
                    target = "fov" if use_fov else "rep"
                    if _sc.currentData() != target:
                        _sc.blockSignals(True)
                        try:
                            _sc.setCurrentByData(target)
                        finally:
                            _sc.blockSignals(False)
                        _stats_state["across"] = "FOV" if use_fov else "Replicates"
                        _update_stats_preview()
                except Exception:
                    swallowed()  # was: pass (silent except)

            if not hasattr(self._app, "_fov_observers"):
                self._app._fov_observers = []
            self._app._fov_observers.append(_sync_across_from_app)

        # Show: placeholder (Mean only; Mean+spread/All points are future).
        show_sc = _seg(
            [("Mean", "mean"), ("Mean+spread", "mean_spread"), ("All points", "all_pts")],
            current="mean",
        )
        if show_sc is not None:
            _stats_state["show"] = "Mean"

            def _on_show_change(*_a) -> None:
                _stats_state["show"] = show_sc.currentData() or "mean"
            show_sc.currentChanged.connect(_on_show_change)
            add_stacked(s_stats, "Show", show_sc)

        _update_stats_preview()

        # Axes preview: shows the current axis target on the section header.
        _axes_preview = QLabel(self._current_axis)
        _axes_preview.setObjectName("Muted")
        s_axes.setValueWidget(_axes_preview)
        axis_cb.currentTextChanged.connect(lambda t, _l=_axes_preview: _l.setText(t))

        # ── Legend ──────────────────────────────────────────────────────────
        s_leg = section("Legend", expanded=False)
        leg_show = ToggleSwitch("Show legend")
        leg_show.setChecked(bool(self._prefs["legend_show"]))
        add_row(s_leg, "", leg_show, "legend_show")
        _leg_preview = QLabel("On" if leg_show.isChecked() else "Off")
        _leg_preview.setObjectName("Muted")
        s_leg.setValueWidget(_leg_preview)
        leg_show.toggled.connect(lambda on, _l=_leg_preview: _l.setText("On" if on else "Off"))
        leg_sz = _Stepper(minimum=6, maximum=24, single_step=1,
                          value=float(self._prefs["legend_font_size"]), decimals=0)
        add_row(s_leg, "Font size", leg_sz, "legend_font_size")
        leg_loc = QComboBox()
        leg_loc.addItems(["best", "upper right", "upper left", "lower right", "lower left"])
        leg_loc.setCurrentText(str(self._prefs["legend_loc"]))
        add_row(s_leg, "Location", leg_loc, "legend_loc")

        # ── Lines & Markers ─────────────────────────────────────────────────
        s_lm = section("Lines & Markers", expanded=False)
        ls_cb = QComboBox()
        # Pair each matplotlib value with a glyphic preview so the combo
        # doesn't show bare punctuation. Items carry the mpl token in
        # ``Qt.UserRole`` and a human-facing label.
        _LINE_STYLES = [
            ("Solid (—)",       "-"),
            ("Dashed (––)",     "--"),
            ("Dotted (·)",      ":"),
            ("Dash-dot (-·)",   "-."),
            ("None",            "none"),
        ]
        for label, val in _LINE_STYLES:
            ls_cb.addItem(label, val)
        _ls_cur = str(self._prefs.get("line_style", "-"))
        _ls_idx = next(
            (i for i, (_, v) in enumerate(_LINE_STYLES) if v == _ls_cur), 0,
        )
        ls_cb.setCurrentIndex(_ls_idx)
        add_row(s_lm, "Line style", ls_cb)
        self._getters["line_style"] = lambda cb=ls_cb: cb.currentData() or "-"
        self._setters["line_style"] = lambda v, cb=ls_cb: cb.setCurrentIndex(
            next((i for i in range(cb.count()) if cb.itemData(i) == v), 0)
        )
        ls_cb.currentIndexChanged.connect(lambda _i: self._on_fields_changed())

        for key, label, lo, hi, step in [
            ("line_width", "Line width", 0.1, 8.0, 0.1),
        ]:
            dsp = _Stepper(minimum=lo, maximum=hi, single_step=step,
                           value=float(self._prefs[key]), decimals=1)
            add_row(s_lm, label, dsp, key)

        ms_cb = QComboBox()
        _MARKER_STYLES = [
            ("Circle (●)",        "o"),
            ("Open circle (○)",   "o_open"),
            ("Square (■)",        "s"),
            ("Triangle ▲",        "^"),
            ("Triangle ▼",        "v"),
            ("Diamond (◆)",       "D"),
            ("Plus (+)",          "+"),
            ("Cross (×)",         "x"),
            ("Star (★)",          "*"),
            ("Point (·)",         "."),
            ("Pentagon",          "p"),
            ("Hexagon",           "h"),
            ("None",              "none"),
        ]
        for label, val in _MARKER_STYLES:
            ms_cb.addItem(label, val)
        _ms_cur = str(self._prefs.get("marker_style", "o"))
        _ms_idx = next(
            (i for i, (_, v) in enumerate(_MARKER_STYLES) if v == _ms_cur), 0,
        )
        ms_cb.setCurrentIndex(_ms_idx)
        add_row(s_lm, "Marker style", ms_cb)
        self._getters["marker_style"] = lambda cb=ms_cb: cb.currentData() or "o"
        self._setters["marker_style"] = lambda v, cb=ms_cb: cb.setCurrentIndex(
            next((i for i in range(cb.count()) if cb.itemData(i) == v), 0)
        )
        ms_cb.currentIndexChanged.connect(lambda _i: self._on_fields_changed())

        for key, label, lo, hi, step in [
            ("marker_size", "Marker size", 0.0, 20.0, 0.5),
            ("marker_edge_width", "Marker edge width", 0.0, 5.0, 0.1),
        ]:
            dsp = _Stepper(minimum=lo, maximum=hi, single_step=step,
                           value=float(self._prefs[key]), decimals=1)
            add_row(s_lm, label, dsp, key)

        # ── Grid ────────────────────────────────────────────────────────────
        s_grid = section("Grid", expanded=False)
        g_show = ToggleSwitch("Show grid")
        g_show.setChecked(bool(self._prefs["grid_show"]))
        add_row(s_grid, "", g_show, "grid_show")
        g_alpha = _Stepper(minimum=0.0, maximum=1.0, single_step=0.05,
                           value=float(self._prefs["grid_alpha"]), decimals=2)
        add_row(s_grid, "Opacity", g_alpha, "grid_alpha")
        g_style = QComboBox()
        g_style.addItems(["-", "--", ":", "-."])
        g_style.setCurrentText(str(self._prefs["grid_style"]))
        add_row(s_grid, "Line style", g_style, "grid_style")
        _grid_preview = QLabel("On" if g_show.isChecked() else "Off")
        _grid_preview.setObjectName("Muted")
        s_grid.setValueWidget(_grid_preview)
        g_show.toggled.connect(lambda on, _l=_grid_preview: _l.setText("On" if on else "Off"))

        # ── Limits & Scale ──────────────────────────────────────────────────
        s_lim = section("Limits & Scale", expanded=False)
        for lim_key, label in [("x_lim", "X limits"), ("y_lim", "Y limits")]:
            lo_edit = QLineEdit(str(self._prefs[f"{lim_key}_min"]))
            lo_edit.setMaximumWidth(76)
            hi_edit = QLineEdit(str(self._prefs[f"{lim_key}_max"]))
            hi_edit.setMaximumWidth(76)
            self._bind_getter_setter(f"{lim_key}_min", lo_edit)
            self._bind_getter_setter(f"{lim_key}_max", hi_edit)
            add_row(s_lim, label, hrow(lo_edit, QLabel("…"), hi_edit, spacing=sp.xs))
        # The per-cell scatter pins its log state to scatter-specific prefs
        # (default on) so the shared x_log/y_log — which other plot types reset
        # to linear — can't silently turn it off. Toggling triggers a redraw so
        # the axis re-tags itself; other figures keep the shared keys.
        _is_scatter = self._is_scatter_cells_fig()
        _xkey = "scatter_x_log" if _is_scatter else "x_log"
        _ykey = "scatter_y_log" if _is_scatter else "y_log"
        _xdef = True if _is_scatter else False
        xlog_cb = ToggleSwitch("X log")
        xlog_cb.setChecked(bool(self._prefs.get(_xkey, _xdef)))
        ylog_cb = ToggleSwitch("Y log")
        ylog_cb.setChecked(bool(self._prefs.get(_ykey, _xdef)))
        self._bind_getter_setter(_xkey, xlog_cb)
        self._bind_getter_setter(_ykey, ylog_cb)
        if _is_scatter:
            xlog_cb.toggled.connect(lambda _=False: self._redraw_bound_figure())
            ylog_cb.toggled.connect(lambda _=False: self._redraw_bound_figure())
        add_stacked(s_lim, "Log scale", hrow(xlog_cb, ylog_cb))

        def _lim_preview_text() -> str:
            tags = []
            if xlog_cb.isChecked():
                tags.append("X log")
            if ylog_cb.isChecked():
                tags.append("Y log")
            return ", ".join(tags) if tags else "linear"

        _lim_preview = QLabel(_lim_preview_text())
        _lim_preview.setObjectName("Muted")
        s_lim.setValueWidget(_lim_preview)
        xlog_cb.toggled.connect(lambda *_a, _l=_lim_preview: _l.setText(_lim_preview_text()))
        ylog_cb.toggled.connect(lambda *_a, _l=_lim_preview: _l.setText(_lim_preview_text()))

        # ── Layout (+ draw order) ───────────────────────────────────────────
        s_layout = section("Layout", expanded=False)
        tight_cb = ToggleSwitch("Tight")
        tight_cb.setChecked(bool(self._prefs["layout_tight"]))
        cons_cb = ToggleSwitch("Constrained")
        cons_cb.setChecked(bool(self._prefs["layout_constrained"]))
        self._bind_getter_setter("layout_tight", tight_cb)
        self._bind_getter_setter("layout_constrained", cons_cb)
        # Stacked: "Figure layout" + "Tight + Constrained" toggle pair exceeded
        # the panel width as a single row, so the trailing "ed" of Constrained
        # was getting clipped. Label-above-widgets layout matches Stats rows.
        add_stacked(s_layout, "Figure layout", hrow(tight_cb, cons_cb))

        # Replicate-set OR well draw-order lists (only one shows at a time,
        # exactly as before). Behaviour lives in _move_list_item /
        # _apply_line_order / _refresh_line_order_lists — unchanged. Items
        # display sample-definition labels and carry the underlying token /
        # rep-set name in Qt.UserRole for the apply step.
        self._line_order_rsets_list: QListWidget | None = None
        self._line_order_wells_list: QListWidget | None = None
        self._line_order_rsets_section: QWidget | None = None
        self._line_order_wells_section: QWidget | None = None
        if self._supports_well_order():
            def _order_block(heading: str, list_widget: QListWidget) -> QWidget:
                block = QWidget()
                bl = QVBoxLayout(block)
                bl.setContentsMargins(0, sp.xs, 0, 0)
                bl.setSpacing(sp.xs)
                bl.addWidget(QLabel(heading))
                list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
                list_widget.setMaximumHeight(140)
                bl.addWidget(list_widget)
                btns = QWidget(block)
                bbl = QHBoxLayout(btns)
                bbl.setContentsMargins(0, 0, 0, 0)
                bbl.setSpacing(sp.xs)
                up = QPushButton("▴")
                up.setObjectName("Ghost")
                up.setToolTip("Move up")
                up.clicked.connect(lambda _=False, lw=list_widget: self._move_list_item(lw, -1))
                down = QPushButton("▾")
                down.setObjectName("Ghost")
                down.setToolTip("Move down")
                down.clicked.connect(lambda _=False, lw=list_widget: self._move_list_item(lw, +1))
                apply_b = QPushButton("Apply")
                apply_b.setObjectName("Primary")
                apply_b.clicked.connect(lambda _=False: self._apply_line_order())
                bbl.addWidget(up)
                bbl.addWidget(down)
                bbl.addWidget(apply_b)
                bbl.addStretch(1)
                bl.addWidget(btns)
                return block

            rs_list = QListWidget()
            rs_block = _order_block("Replicate set order", rs_list)
            add_full(s_layout, rs_block)
            self._line_order_rsets_list = rs_list
            self._line_order_rsets_section = rs_block

            w_list = QListWidget()
            w_block = _order_block("Well order", w_list)
            add_full(s_layout, w_block)
            self._line_order_wells_list = w_list
            self._line_order_wells_section = w_block
            self._refresh_line_order_lists()

        body_layout.addStretch(1)

        # ── footer: reset / save preset / export ────────────────────────────
        # The Copy PNG / Copy SVG / Save As… row that used to live here was
        # redundant — every plot card's controls row already exposes
        # ``Copy SVG`` and ``Save figure`` buttons via btn_secondary, and the
        # ``Export…`` action below covers the on-disk-export use case.
        footer = QWidget(self)
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(0, sp.sm, 0, 0)
        fl.setSpacing(sp.xs)
        act_row = QHBoxLayout()
        act_row.setContentsMargins(0, 0, 0, 0)
        act_row.setSpacing(sp.xs)
        reset_btn = QPushButton("Reset")
        reset_btn.clicked.connect(lambda _=False: self._reset_defaults())
        save_btn = QPushButton("Save Preset")
        save_btn.clicked.connect(lambda _=False: self._save_preset())
        exp_btn = QPushButton("Export…")
        exp_btn.setObjectName("Primary")
        exp_btn.clicked.connect(lambda _=False: self._export())
        # Equal flex on the three action buttons so they share the row
        # cleanly inside the 440-px panel; without this the QPushButton
        # default minimum-width can push Export… off the right edge.
        for btn in (reset_btn, save_btn, exp_btn):
            btn.setSizePolicy(btn.sizePolicy().horizontalPolicy(),
                              btn.sizePolicy().verticalPolicy())
            act_row.addWidget(btn, 1)
        fl.addLayout(act_row)
        root.addWidget(footer)

    def _on_profile_selected(self) -> None:
        if self._updating:
            return
        profile = self._profile_combo.currentText()
        overrides = EXPORT_PROFILES.get(profile, {})
        if not overrides:
            custom_profiles = _ensure_custom_export_profiles(self._app)
            overrides = custom_profiles.get(profile, {})
        if not overrides:
            self._on_fields_changed()
            return
        self._updating = True
        try:
            for k, v in overrides.items():
                if k in self._setters:
                    self._setters[k](v)
        finally:
            self._updating = False
        self._on_fields_changed()

    def _persist(self) -> None:
        for k, getter in self._getters.items():
            self._prefs[k] = getter()
        # Mirror the axis-specific keys into the bucket for the currently
        # selected axis so each axis retains its own xlim/ylim/scale.
        self._current_axis = str(self._prefs.get("axis_target", "All"))
        bucket = self._axis_buckets.setdefault(self._current_axis, {})
        for k in self._axis_keys:
            bucket[k] = self._prefs.get(k)
        self._prefs["axis_buckets"] = self._axis_buckets

    def _default_axis_target(self) -> str:
        """The axis the panel targets by default: the subplot showing the active
        metric, so edits land on the plot in view instead of spreading across
        "All". The global Property (active metric) always drives the FIRST
        subplot — the value axis on the Bar/Line stacks [value, fraction, count]
        and the only axis on Scatter / Distribution / Heat Map — so that's axis
        1. A saved explicit (non-"All") choice is respected.
        """
        saved = str(self._prefs.get("axis_target", "All"))
        return saved if saved != "All" else "1"

    def _on_canvas_axis_pick(self, event) -> None:
        """Clicking a subplot targets it: sync the Axis # field to the clicked
        axis and flash it. No-op while the sidebar is hidden or the click misses
        every axis, so it never interferes with normal canvas interaction."""
        if not self.isVisible():
            return
        ax = getattr(event, "inaxes", None)
        if ax is None:
            return
        try:
            idx = list(self._fig.axes).index(ax)
        except ValueError:
            return
        target = str(idx + 1)
        cb = getattr(self, "_axis_cb", None)
        if cb is not None and cb.currentText() != target:
            cb.setCurrentText(target)  # drives _on_axis_target_changed
        self._flash_axis(ax)

    def _flash_axis(self, ax) -> None:
        """Briefly outline *ax* so it's clear which subplot is now targeted."""
        try:
            spines = list(ax.spines.values())
            saved = [(sp, sp.get_edgecolor(), sp.get_linewidth(), sp.get_visible())
                     for sp in spines]
            for sp in spines:
                sp.set_visible(True)
                sp.set_edgecolor(theme.Colors.accent)
                sp.set_linewidth(2.5)
            self._canvas.draw_idle()

            def _restore():
                for sp, ec, lw, vis in saved:
                    sp.set_edgecolor(ec)
                    sp.set_linewidth(lw)
                    sp.set_visible(vis)
                self._canvas.draw_idle()

            QTimer.singleShot(500, _restore)
        except Exception:
            swallowed()  # was: pass (silent except)

    def _on_axis_target_changed(self, new_axis: str) -> None:
        if self._updating:
            return
        # Snapshot widget values into the previous axis's bucket before
        # loading the new axis's stored values.
        prev_bucket = self._axis_buckets.setdefault(self._current_axis, {})
        for k in self._axis_keys:
            getter = self._getters.get(k)
            if getter is not None:
                prev_bucket[k] = getter()
        self._current_axis = str(new_axis)
        new_bucket = self._axis_buckets.setdefault(self._current_axis, {})
        self._updating = True
        try:
            # Always set every bucketed widget — fall back to defaults for
            # keys the new axis hasn't customised yet. Without this, a fresh
            # axis inherits the previous axis's widget values, and the next
            # _persist call would silently push those stale values into the
            # new axis's bucket.
            for k in self._axis_keys:
                setter = self._setters.get(k)
                if setter is None:
                    continue
                if k in new_bucket and new_bucket[k] is not None:
                    setter(new_bucket[k])
                else:
                    setter(DEFAULT_EXPORT_STYLE_PREFS[k])
        finally:
            self._updating = False
        # Persist the new axis_target selection without re-applying the
        # previously displayed settings to the newly selected axis.
        self._prefs["axis_target"] = self._current_axis
        self._prefs["axis_buckets"] = self._axis_buckets

    def _on_fields_changed(self) -> None:
        if self._updating:
            return
        try:
            self._persist()
            apply_export_style_to_current(self._app, self._fig, self._canvas)
        except Exception:
            swallowed()  # was: pass (silent except)

    def _close_dock(self) -> None:
        self.hide()
        dock = _resolve_export_dock(self._app, self._fig)
        if dock is not None:
            dock.setVisible(False)

    def _reset_defaults(self) -> None:
        self._updating = True
        try:
            for k, v in DEFAULT_EXPORT_STYLE_PREFS.items():
                if k in self._setters:
                    self._setters[k](v)
            # Drop every per-axis override bucket so *all* axes reset, not just
            # the one currently shown in the axis picker.
            self._axis_buckets = {}
        finally:
            self._updating = False
        self._persist()
        # Clearing the axis-limit *fields* doesn't actually reset the axes:
        # apply_export_style only ever *sets* limits, it never re-enables
        # autoscale, and the plots set intentional limits (e.g. the fraction
        # subplot's [-0.05, 1.05]). Re-run the active plot's own redraw so the
        # axes return to their natural / intentional values, then re-apply the
        # (now default) style on top via _on_fields_changed.
        self._redraw_active_plot()
        self._on_fields_changed()

    def _redraw_active_plot(self) -> None:
        """Re-run the active plot tab's own draw so axes return to their natural
        limits (the style sidebar can't restore autoscale by clearing fields)."""
        app = self._app
        try:
            tab = app._current_centre_tab()
        except Exception:
            tab = ""
        try:
            if tab == "Bar Plots":
                redraw_bars(app)
            elif tab == "Scatter Plot":
                from well_viewer.features.scatter.tab_view import scatter_redraw_active
                scatter_redraw_active(app)
            else:
                app._redraw()
        except Exception:
            swallowed()  # reset must never raise out of the click handler

    def _save_preset(self) -> None:
        try:
            self._persist()
            name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
            if not ok:
                return
            preset_name = name.strip()
            if not preset_name:
                return
            if preset_name in EXPORT_PROFILES:
                QMessageBox.warning(self, "Preset exists", "Cannot overwrite built-in preset name.")
                return
            custom_profiles = _ensure_custom_export_profiles(self._app)
            custom_profiles[preset_name] = dict(self._prefs)
            self._profile_combo.blockSignals(True)
            self._profile_combo.clear()
            self._profile_combo.addItems(_get_all_profile_names(self._app))
            self._profile_combo.setCurrentText(preset_name)
            self._profile_combo.blockSignals(False)
            self._app._set_status(f"Saved export preset: {preset_name}")
        except Exception as exc:
            QMessageBox.critical(self, "Save preset failed", str(exc))

    def _write_figure(self, out_path: Path, fmt: str) -> None:
        """Save ``self._fig`` to *out_path* as *fmt* with format-appropriate
        settings: 300 dpi for raster (PNG / TIFF), text-kept-as-text for SVG,
        and Type-42 (TrueType) fonts for PDF / EPS so text stays editable in
        downstream vector tools."""
        kw: dict = {"format": fmt, "bbox_inches": "tight", "transparent": True}
        rc_overrides: dict = {}
        if fmt in ("png", "tiff"):
            kw["dpi"] = 300
        elif fmt == "svg":
            rc_overrides["svg.fonttype"] = "none"
        else:  # pdf / eps
            rc_overrides["pdf.fonttype"] = 42
            rc_overrides["ps.fonttype"] = 42
        import matplotlib as _mpl
        with _mpl.rc_context(rc_overrides):
            self._fig.savefig(str(out_path), **kw)

    def _export(self) -> None:
        """Save the figure to disk. The dialog offers every supported format —
        PNG, TIFF, SVG, EPS, PDF — with the export profile's ``format`` pref as
        the default filter; the user may pick any of them."""
        try:
            self._persist()

            default_fmt = (self._prefs.get("format") or "png").lower()
            if default_fmt not in _EXPORT_FILTERS:
                default_fmt = "png"
            stem = Path(self._default_name).stem or "figure"
            start = str(self._base_dir / f"{stem}.{default_fmt}")
            filter_str = ";;".join(
                _EXPORT_FILTERS[f] for f in _EXPORT_FILTER_ORDER
            ) + ";;All files (*.*)"

            out, picked = QFileDialog.getSaveFileName(
                self, "Save figure", start, filter_str,
                _EXPORT_FILTERS[default_fmt],
            )
            if not out:
                return

            out_path = Path(out)
            # Resolve the format from the typed extension; fall back to the
            # filter the user selected when no known extension was given.
            ext = out_path.suffix.lstrip(".").lower()
            ext = "tiff" if ext == "tif" else ext
            if ext in _EXPORT_FILTERS:
                fmt = ext
            else:
                label = picked.split(" (")[0]
                fmt = next(
                    (f for f, flt in _EXPORT_FILTERS.items()
                     if flt.startswith(label)),
                    default_fmt,
                )
                out_path = out_path.with_suffix(f".{fmt}")

            self._write_figure(out_path, fmt)
            self._app._set_status(f"Figure saved → {out_path.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    # ── Reorder ──────────────────────────────────────────────────────────────

    def _is_line_fig(self) -> bool:
        return getattr(self._app, "_line_fig", None) is self._fig

    def _is_scatter_cells_fig(self) -> bool:
        """True only for the per-cell scatter figure (density + gates apply there)."""
        return getattr(self._app, "_scatter_fig", None) is self._fig

    def _gate_count_text(self) -> str:
        n = len(getattr(self._app, "_scatter_gates", None) or [])
        return f"{n} gate(s)"

    def _on_density_toggled(self) -> None:
        """Persist the density pref then redraw (a restyle alone can't switch
        between solid Line2D markers and the per-point density PathCollection).

        Toggling density is the same data on the same axes, so preserve the
        user's current zoom/pan instead of snapping back to autoscale on redraw.
        """
        if self._updating:
            return
        try:
            ax = getattr(self._app, "_ax_scatter", None)
            xlim = ax.get_xlim() if ax is not None else None
            ylim = ax.get_ylim() if ax is not None else None
            self._persist()
            self._redraw_bound_figure()
            if ax is not None and xlim is not None and ylim is not None:
                ax.set_xlim(xlim)
                ax.set_ylim(ylim)
                canvas = getattr(self._app, "_scatter_canvas", None)
                if canvas is not None:
                    canvas.draw_idle()
        except Exception:
            swallowed()  # was: pass (silent except)

    def _on_show_gates_toggled(self, on: bool) -> None:
        """Show/hide stored gates without discarding them."""
        self._app._scatter_gates_hidden = not bool(on)
        try:
            self._redraw_bound_figure()
        except Exception:
            swallowed()

    def _refresh_gate_count(self) -> None:
        """Update the 'N gate(s)' label + name list — called when a gate is
        drawn / cleared / renamed."""
        lbl = getattr(self, "_gate_count_label", None)
        if lbl is not None:
            try:
                lbl.setText(self._gate_count_text())
            except Exception:
                swallowed()
        self._rebuild_gate_list()

    def _rebuild_gate_list(self) -> None:
        """Rebuild the editable per-gate name rows from ``app._scatter_gates``."""
        layout = getattr(self, "_gate_list_layout", None)
        if layout is None:
            return
        # A rename notifies gate-changed, which re-enters here — don't tear down
        # the very field being edited (the name is already committed to it).
        if getattr(self, "_gate_rename_in_progress", False):
            return
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        gates = getattr(self._app, "_scatter_gates", None) or []
        for idx, gate in enumerate(gates):
            edit = QLineEdit(str(gate.get("name") or ""))
            edit.setToolTip("Rename this gate (used by the Cell Gating filter).")
            # editingFinished can fire twice (focus-out + Enter); commit once and
            # revert the field on a rejected (blank/duplicate) name.
            def _commit(_i=idx, _e=edit) -> None:
                self._gate_rename_in_progress = True
                try:
                    ok = rename_gate(self._app, _i, _e.text())
                finally:
                    self._gate_rename_in_progress = False
                if not ok:
                    cur = (getattr(self._app, "_scatter_gates", None) or [])
                    if 0 <= _i < len(cur):
                        _e.setText(str(cur[_i].get("name") or ""))
            edit.editingFinished.connect(_commit)
            layout.addWidget(edit)

    def _on_clear_gates(self) -> None:
        clear_gates(self._app)
        self._refresh_gate_count()

    def _supports_well_order(self) -> bool:
        """Return True for plot figs whose color order can be reorderable."""
        app = self._app
        for attr in ("_line_fig", "_bar_fig", "_scatter_fig", "_scatter_agg_fig"):
            if getattr(app, attr, None) is self._fig:
                return True
        return False

    def _move_list_item(self, list_widget: QListWidget, delta: int) -> None:
        """Shift the selected row up (delta=-1) or down (delta=+1)."""
        if list_widget is None:
            return
        row = list_widget.currentRow()
        if row < 0:
            return
        new_row = row + int(delta)
        if not (0 <= new_row < list_widget.count()):
            return
        item = list_widget.takeItem(row)
        list_widget.insertItem(new_row, item)
        list_widget.setCurrentRow(new_row)

    def _apply_line_order(self) -> None:
        """Commit the visible list's order to app state and redraw the figure.

        Only the section that's actually visible (rep-set OR well) gets
        written so a stale list doesn't clobber the active mode's order.
        """
        app = self._app

        def _ids(list_widget: QListWidget | None) -> list[str]:
            if list_widget is None:
                return []
            out: list[str] = []
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                tok = item.data(Qt.UserRole)
                out.append(str(tok) if tok else item.text())
            return out

        if (self._line_order_rsets_section is not None
                and self._line_order_rsets_section.isVisible()):
            app._app_state.plot.line_order_rsets = _ids(self._line_order_rsets_list)
        if (self._line_order_wells_section is not None
                and self._line_order_wells_section.isVisible()):
            app._app_state.plot.line_order_wells = _ids(self._line_order_wells_list)
        try:
            app._persistence.schedule_save("line_order")
        except Exception:
            swallowed()  # was: pass (silent except)
        self._redraw_bound_figure()

    def _redraw_bound_figure(self) -> None:
        """Trigger the redraw entry point that matches the bound figure.

        Dispatch is by figure identity — the imported redraw function is
        the source of truth, not a string method name on app.
        """
        app = self._app
        try:
            if getattr(app, "_line_fig", None) is self._fig:
                app._redraw()
                return
            if getattr(app, "_bar_fig", None) is self._fig:
                redraw_bars(app)
                return
            if getattr(app, "_scatter_fig", None) is self._fig:
                redraw_scatter_orchestrate(app)
                return
            if getattr(app, "_scatter_agg_fig", None) is self._fig:
                redraw_scatter_agg_orchestrate(app)
                return
            app._redraw()
        except Exception:
            swallowed()  # was: pass (silent except)

    def _refresh_line_order_lists(self) -> None:
        """Repopulate the rep-set / well order lists from current app state.

        Only one section is shown at a time, matching the renderer's branch
        on whether replicate sets are active. Items display sample-definition
        labels (well_labels / replicate display labels) and stash the
        underlying token / rep-set name in ``Qt.UserRole`` so the apply step
        can recover the canonical value.
        """
        app = self._app
        try:
            active_rsets = list(app._rep_sets_active() or [])
        except Exception:
            active_rsets = []
        rs_active = bool(active_rsets)

        if self._line_order_rsets_section is not None:
            self._line_order_rsets_section.setVisible(rs_active)
        if self._line_order_wells_section is not None:
            self._line_order_wells_section.setVisible(not rs_active)

        if rs_active and self._line_order_rsets_list is not None:
            self._line_order_rsets_list.blockSignals(True)
            self._line_order_rsets_list.clear()
            names = [getattr(rs, "name", "") for rs in active_rsets if getattr(rs, "name", "")]
            saved = list(app._app_state.plot.line_order_rsets or [])
            ordered_names = [n for n in saved if n in names] + [n for n in names if n not in saved]
            display_fn = getattr(app, "_replicate_display_label", None)
            by_name = {getattr(rs, "name", ""): rs for rs in active_rsets}
            for n in ordered_names:
                rs = by_name.get(n)
                if rs is not None and callable(display_fn):
                    try:
                        text = str(display_fn(rs)) or n
                    except Exception:
                        text = n
                else:
                    text = n
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, n)
                self._line_order_rsets_list.addItem(item)
            self._line_order_rsets_list.blockSignals(False)

        if (not rs_active) and self._line_order_wells_list is not None:
            self._line_order_wells_list.blockSignals(True)
            self._line_order_wells_list.clear()
            # Per-well code paths in line/bar/scatter all derive from
            # ``app._app_state.selection.selected_wells`` sorted by ``_parse_rc``; mirror that
            # exactly so the order list matches the plot.
            try:
                raw = app._app_state.selection.selected_wells or set()
                parse_rc = getattr(app, "_parse_rc", None)
                if callable(parse_rc):
                    selected = sorted(raw, key=parse_rc)
                else:
                    selected = sorted(raw)
            except Exception:
                selected = []
            saved = list(app._app_state.plot.line_order_wells or [])
            ordered = [w for w in saved if w in selected] + [w for w in selected if w not in saved]
            label_fn = getattr(app, "_well_display_label", None)
            for w in ordered:
                if callable(label_fn):
                    try:
                        disp = str(label_fn(w))
                    except Exception:
                        disp = w
                else:
                    disp = w
                text = w if disp == w else f"{w} — {disp}"
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, w)
                self._line_order_wells_list.addItem(item)
            self._line_order_wells_list.blockSignals(False)
