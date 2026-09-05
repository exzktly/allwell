"""Export-style sidebar view (extracted from ``figure_export_editor``).

Hosts the ``ExportStyleSidebar`` widget that lives inside each plot tab's
right-side dock. Pure view: prefs persistence, profile lookups, and the
matplotlib export pipeline live in ``well_viewer.figure_export_editor``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from collections.abc import Callable

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
    _get_all_profile_names,
    _resolve_export_dock,
    apply_export_style_to_current,
)
from well_viewer.core.plot_style import (
    AXIS_LIMIT_PREF_KEYS, FIGURE_SCOPED_PREF_KEYS, LOG_BASE_CHOICES,
    drop_bucket_limits, figure_pref_is_set, figure_prefs,
)

# v2 chrome: this panel is composed from the custom widgets in ``widgets/``
# (CollapsibleSection, ToggleSwitch) plus theme-styled stock widgets. The
# binding layer (_bind_getter_setter / _getters / _setters / _persist /
# apply_export_style_to_current) is unchanged — only the layout was re-skinned.
import theme
from widgets.collapsible_section import CollapsibleSection
from widgets.toggle_switch import ToggleSwitch
from well_viewer.core.lazy import lazy as _lazy
# Lazy so the Export Style sidebar imports without barplot / scatter present
# (keeps those features deletable); resolves on first call.
redraw_bars = _lazy("well_viewer.features.barplot.renderer", "redraw_bars")
redraw_scatter_agg_orchestrate = _lazy("well_viewer.features.scatter.controller", "redraw_scatter_agg_orchestrate")
redraw_scatter_orchestrate = _lazy("well_viewer.features.scatter.controller", "redraw_scatter_orchestrate")
redraw_distribution = _lazy("well_viewer.features.distribution.controller", "redraw_distribution")
redraw_heatmap = _lazy("well_viewer.features.heatmap.controller", "redraw_heatmap")
clear_gates = _lazy("well_viewer.features.scatter.gating", "clear_gates")
rename_gate = _lazy("well_viewer.features.scatter.gating", "rename_gate")
set_gate_draw_mode = _lazy("well_viewer.features.scatter.gating", "set_gate_draw_mode")
from well_viewer.data.metric_labels import gate_axes_label
from well_viewer.core.safe import swallow, swallowed
from well_viewer.domain.time_rescale import (
    displayed_interval as _displayed_interval,
    factor_for_interval as _factor_for_interval,
    rescale_for as _rescale_for,
)

_logger = logging.getLogger("well_viewer")

#: Plot tabs whose figure this panel can open but whose redraw is not one of
#: the callables imported above: they are reached by name through the feature
#: registry, so this module imports none of them and a profiled-out feature
#: simply falls through.
#:
#: **This is a roster, and a roster is where a control silently stops working.**
#: A figure absent here fell to ``app._redraw()``, which redraws the *Line* tab
#: — so a Properties edit on the Response tab repainted a different figure and
#: reported success. ``tests/test_properties_panel_redraws.py`` holds it against
#: the Properties roster in ``features/export/style.py``, which is the list of
#: figures this panel can be opened on in the first place.
_FIGURE_OWNING_FEATURES = (
    ("_sp_fig", "Spatial"),
    ("_mv_fig", "Multivariate"),
    ("_response_fig", "Response Curves"),
)

#: Style facets a figure does not have, keyed by the ``app`` attribute that
#: names that figure. A figure with no row here supports everything, and a facet
#: absent from a figure's row is supported by it — so the panel goes on showing
#: every section to every plot except where somebody has *measured* that it does
#: nothing, and the cost of forgetting a figure is a dead control rather than a
#: working one taken away.
#:
#: Each entry carries its reason, because a bare facet name in a roster is
#: indistinguishable six months later from one added to make something go away.
#: The reasons here were established by drawing the figure, changing the pref,
#: re-applying ``apply_export_style_prefs`` and comparing the rendered PNG —
#: ``tests/test_properties_hides_what_it_cannot_do.py`` re-measures them, so a
#: row that stops being true fails rather than quietly misleading.
_STYLE_FACETS_A_FIGURE_LACKS: dict[str, dict[str, str]] = {
    "_heatmap_fig": {
        "legend": (
            "A heat map draws no legend. Its colour bar is the key, and that is "
            "the mappable's, not an ``ax.legend()`` this section can reach."
        ),
        "marks": (
            "There are no lines or markers in the grid — ``ax.lines`` is empty "
            "and no collection is tagged ``_aw_marker_collection``, so every "
            "control in Lines & Markers styles artists that do not exist."
        ),
        "plot_overlays": (
            "The renderer reads neither ``show_threshold_line`` nor "
            "``show_value_labels``. The number in a cell is drawn by the heat "
            "map itself and formatted by Cell values, one section above."
        ),
        "bar_show_mode": (
            "``bar_show_mode`` is read by the bar renderer alone. A cell is one "
            "value; there is no Mean / Mean+spread / All points to choose "
            "between. The Error bars and Across rows beside it *are* live — "
            "they pick the interval the per-cell ``±`` prints."
        ),
        "group_labels": (
            "``group_label_size`` sizes the bar plot's group labels. A heat map "
            "names its rows and columns with ordinary ticks, which Tick label "
            "size already governs."
        ),
        "log_axes": (
            "Both axes are marked ``_categorical_xaxis`` / "
            "``_categorical_yaxis``: they carry row and column positions, not "
            "values, so the log pass skips them and always did. Colouring on a "
            "log scale is Display ▾ → Log scale, which acts on the norm."
        ),
    },
}


#: The same features, named by *tab* rather than by figure, for the reset path
#: (``_redraw_active_plot``), which dispatches on the tab in view.
_FEATURES_THAT_REDRAW_THEMSELVES = tuple(
    feature for _attr, feature in _FIGURE_OWNING_FEATURES)

# Pixel geometry for this module's controls. Named rather than written
# inline so the number is stated once and a reader can see what it sizes
# (ARCHITECTURE §8: never hardcode a pixel size at a call site).
_HI_EDIT_MAX_W = 76
_LIST_WIDGET_MAX_H = 140
_LO_EDIT_MAX_W = 76


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
#: One number for every right-hand dock in the Review pane (theme.Metrics).
EXPORT_STYLE_PANEL_WIDTH = theme.Metrics.dock_w


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

# Font-size keys scoped per-axis by the "Axis #" selector. They live in the same
# per-axis ``axis_buckets`` as the axis limits, but (unlike limits) fall back to
# the shared global size when a panel has no override, and an "All" edit resets
# them. See ``well_viewer.core.plot_style.apply_export_style_prefs``.
_PER_AXIS_FONT_KEYS = ("tick_label_size", "axis_label_size", "title_size")

# Every OTHER control under the "Axis #" selector in the Axes section — tick
# rotation, tick length / direction / visibility, and the bar group-label size.
# These share the font keys' semantics (per-axis bucket overriding a shared
# global baseline, "All" resets), so the whole Axes section is scoped by "Axis #"
# instead of a subset silently applying to every panel. ``apply_export_style_prefs``
# reads each of these per-axis (via ``_axis_style``); the bar renderer reads
# ``group_label_size`` per-axis via ``axis_scoped_value``.
_PER_AXIS_STYLE_KEYS = (
    *_PER_AXIS_FONT_KEYS,
    "x_tick_angle", "group_label_size",
    "tick_major", "tick_minor", "tick_length", "tick_direction",
)


def _base_suffix(text: str) -> str:
    """What the Limits & Scale summary adds after "X log" for a non-default base.

    Empty for base 10, so the common case reads as it always has and only a
    figure actually drawn in another base carries the extra words.
    """
    cleaned = str(text or "").strip()
    return "" if cleaned in ("", "10") else f" base {cleaned}"


#: Human-readable text for the segment values that are re-seeded from
#: ``PlotState`` on every panel open. Stated once so the initial label, the
#: click handler and the re-seed cannot spell the same mode three ways.
_SEGMENT_LABELS = {
    "plate": "Plate",
    "series": "Series",
    "grey": "Grey",
    "mean": "Mean",
    "mean_spread": "Mean+spread",
    "all_pts": "All points",
}


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
        # Per-figure view: global publication style (fonts, line/marker, grid,
        # legend …) is shared, but axis limits / log scaling are scoped to this
        # figure so they no longer leak across tabs.
        self._prefs = figure_prefs(app._ctx, fig)
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
            show_tog.setChecked(not bool(self._app._app_state.scatter.gates_hidden))
            show_tog.toggled.connect(lambda on: self._on_show_gates_toggled(on))
            add_row(s_gates, "", show_tog)
            # One-shot "Draw gate" button (not a toggle): clicking arms a
            # single drag-to-draw on the plot; gating.py disarms after the gate
            # is drawn and calls _scatter_gate_draw_sync(False) to restore the
            # button label.
            draw_btn = QPushButton("Draw gate")

            def _arm_draw(_=False, _b=draw_btn) -> None:
                set_gate_draw_mode(self._app._ctx, True)
                _b.setText("Drag on the plot…")
            draw_btn.clicked.connect(_arm_draw)
            add_full(s_gates, draw_btn)
            self._gate_draw_btn = draw_btn

            def _sync_draw_toggle(on: bool, _b=draw_btn) -> None:
                # Restore the button once the one-shot draw completes.
                if not on:
                    _b.setText("Draw gate")
            self._app._ctx.runtime.scatter.gate_draw_sync = _sync_draw_toggle
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
            self._app._ctx.runtime.scatter.gate_count_cb = self._refresh_gate_count

        # ── Heat Map: the number drawn in each cell ─────────────────────────
        if self._is_heatmap_fig():
            s_cells = section("Cell values", expanded=True)
            fmt_edit = QLineEdit(str(self._prefs.get("heatmap_value_format", "")))
            fmt_edit.setPlaceholderText("%.2f")
            fmt_edit.setToolTip(
                "printf-style format for the number in each cell — %.2f for two "
                "decimals, %.1e for scientific notation, %d for a whole number. "
                "Leave blank to pick a format from the metric and the magnitude."
            )
            # Manual getter/setter (no key= auto-wire): this changes the text of
            # the cell annotations, which only the heat-map renderer creates, and
            # the auto-wire path restyles the existing figure without redrawing.
            self._getters["heatmap_value_format"] = fmt_edit.text
            self._setters["heatmap_value_format"] = (
                lambda v, w=fmt_edit: w.setText(str(v or ""))
            )
            fmt_edit.editingFinished.connect(self._on_cell_format_changed)
            add_row(s_cells, "Value format", fmt_edit)

        # ── What the plot's data ink is coloured by ─────────────────────────
        # "Plate" is the historical behaviour on both tabs — each column or
        # trace takes the colour its well or replicate group carries on the
        # plate picker, so the plate doubles as the legend. "Grey" is for a
        # figure whose columns are already named on the axis, or whose traces
        # are named in the legend, and whose colours would only compete with
        # the data.
        #
        # What grey *means* differs between the three, and the difference is
        # the reason each tab has its own state field rather than sharing one.
        # A bar is named by the tick label under it, so every bar can take the
        # same ink and lose nothing. A trace is not, so the Line tab's grey
        # mode gives each trace its own shade **and** its own marker — see
        # ``domain/line_colors.py``. A response curve is not either, and its
        # dash is already spent on the timepoint, so its grey mode is the Line
        # tab's minus the dash — ``domain/response_colors.py``. One control,
        # three rules, and no tab shows another's: the setting changes nothing
        # on any other plot, like the Density / Gates / Cell values sections
        # above.
        #
        # The default half of the segment is named per tab too. "Plate" is
        # literal on Bar and Line — the column or trace takes the colour its
        # well or replicate group carries on the plate picker, so the plate
        # doubles as the legend. A response curve is a *series* pooled across
        # however many wells hold that value, so it has no plate colour to
        # take; calling its mode "Plate" would name a thing the figure does
        # not do.
        _color_target = None
        if self._is_bar_fig():
            from well_viewer.domain.bar_colors import bar_color_mode as _mode_of
            _color_target = ("bar_color_mode", "Bar colours", _mode_of, "plate")
        elif self._is_line_fig():
            from well_viewer.domain.line_colors import line_color_mode as _mode_of
            _color_target = ("line_color_mode", "Line colours", _mode_of, "plate")
        elif self._is_response_fig():
            from well_viewer.domain.response_colors import (
                response_color_mode as _mode_of,
            )
            _color_target = ("response_color_mode", "Curve colours", _mode_of,
                             "series")
        if _color_target is not None:
            from widgets.plot_card import _make_segmented as _seg_color
            (_color_field, _color_label, _color_resolver,
             _color_default) = _color_target
            s_colors = section("Colours", expanded=True)
            _color_init = _color_resolver(self._app._app_state.plot)
            color_sc = _seg_color(
                [(_SEGMENT_LABELS[_color_default], _color_default),
                 ("Grey", "grey")], current=_color_init,
            )
            if color_sc is not None:
                self._color_seg = color_sc
                # Which field this segment writes. The re-seed below reads it
                # rather than assuming the bar one: one attribute now holds
                # whichever of the two tabs' segments this panel was built for,
                # and re-seeding a Line segment from ``bar_color_mode`` would
                # move it with signals blocked — the widget then shows a mode
                # the figure is not drawn in, and clicking the mode the user
                # actually wants emits nothing because the control already
                # believes it is current. That is the exact failure this
                # function's own docstring was written about.
                self._color_seg_field = _color_field
                # …and what that field's absence means. Three tabs now, and
                # only two of them default to "plate"; re-seeding a Response
                # segment to "plate" would move it to a value its own segment
                # does not offer, leaving ``setCurrentByData`` with nothing to
                # select and the control showing whatever it showed before.
                self._color_seg_default = _color_default
                _color_preview = QLabel(_SEGMENT_LABELS[_color_init])
                _color_preview.setObjectName("Muted")
                self._color_seg_preview = _color_preview
                s_colors.setValueWidget(_color_preview)

                def _on_color_mode_change(*_a) -> None:
                    v = str(color_sc.currentData() or _color_default)
                    setattr(self._app._app_state.plot, _color_field, v)
                    _color_preview.setText(_SEGMENT_LABELS[v])
                    # A full redraw, not a restyle: the colour and the marker
                    # are chosen per column / per trace while the artists are
                    # built, and the auto-wire restyle path never revisits an
                    # existing artist's facecolor.
                    #
                    # The redraw is this control's *only* visible effect, so a
                    # swallowed failure would leave the segment and its summary
                    # both reading "Grey" over a figure still drawn in plate
                    # colours, with nothing on screen saying so. Say so.
                    if not self._redraw_bound_figure():
                        with swallow():
                            self._app._set_status(
                                f"{_color_label} set to {_SEGMENT_LABELS[v]}, but "
                                "the plot could not be redrawn — switch tabs and "
                                "back to refresh it."
                            )

                color_sc.currentChanged.connect(_on_color_mode_change)
                add_stacked(s_colors, _color_label, color_sc)

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
            # Everything else under the "Axis #" selector is per-axis too: an
            # "Axis #" selection scopes the tick sizes, tick rotation, tick
            # length / direction / visibility, and group-label size to that one
            # panel; "All" applies to every panel. Stored in the same per-axis
            # buckets as the limits.
            *_PER_AXIS_STYLE_KEYS,
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
        # **The colour bar is not a panel to target.** ``fig.axes`` includes it,
        # so the Heat Map offered an "Axis 2" that selected the bar — and every
        # per-axis pass in ``apply_export_style_prefs`` skips a ``_aw_colorbar``
        # axis by design, so choosing it moved nothing at all. Its numbering is
        # still the figure's, not a renumbering of what is left: axis 1 is the
        # first axes of the figure whether or not a colour bar sits at 2, which
        # is what every stored ``axis_buckets`` key already means.
        axis_cb.addItems(["All", *self._targetable_axis_numbers()])
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
        if self._canvas is not None:
            try:
                self._axis_pick_cid = self._canvas.mpl_connect(
                    "button_press_event", self._on_canvas_axis_pick
                )
            except Exception:
                self._axis_pick_cid = None
        from widgets.stepper import Stepper as _Stepper
        _axis_size_rows = [
            ("axis_label_size", "Axis label size", 1, 96),
            ("tick_label_size", "Tick label size", 1, 96),
            ("title_size", "Title size", 1, 128),
            ("x_tick_angle", "X tick angle°", 0, 90),
        ]
        # Group labels are the bar plot's second tier of x ticks. A figure that
        # draws none has nothing for this to size — see the facet table.
        if self._supports("group_labels"):
            _axis_size_rows.append(("group_label_size", "Group label size", 1, 96))
        for key, label, lo, hi in _axis_size_rows:
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

        # Error bars: None / SEM / SD / 95% CI all drive the bar error bars via
        # PlotState.bar_err_kind; SEM/SD also keep ``use_sem`` (the per-card band
        # toggle + line plots) in sync. 95% CI = 1.96 × SEM (normal approx),
        # computed at draw time.
        _err_init = str(getattr(self._app._app_state.plot, "bar_err_kind", "")
                        or ("sem" if bool(self._app._app_state.plot.use_sem) else "sd"))
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
                self._app._app_state.plot.bar_err_kind = v
                # SEM/SD keep use_sem in sync (ci95 is SEM-based; none hides).
                want_sem = (v in ("sem", "ci95"))
                if v in ("sem", "sd", "ci95") and bool(self._app._app_state.plot.use_sem) != want_sem:
                    try:
                        self._app._toggle_sem()
                    except Exception:
                        swallowed()  # was: pass (silent except)
                try:
                    self._redraw_active_plot()
                except Exception:
                    swallowed()

            err_sc.currentChanged.connect(_on_err_change)
            add_stacked(s_stats, "Error bars", err_sc)

            # Auto-sync: when _use_sem flips elsewhere (the band-controls row on
            # any plot card), update this segment too.
            def _sync_err_from_app(use_sem: bool, _sc=err_sc) -> None:
                try:
                    target = "sem" if use_sem else "sd"
                    # "None" is a choice about whether to draw error bars at
                    # all, not about which one — a SEM/SD flip made from a plot
                    # card must not turn bars back on that the user switched
                    # off here.
                    if _sc.currentData() == "none":
                        return
                    if _sc.currentData() != target:
                        _sc.blockSignals(True)
                        try:
                            _sc.setCurrentByData(target)
                        finally:
                            _sc.blockSignals(False)
                        _stats_state["err"] = target
                        # The renderer reads ``bar_err_kind``, not the combo.
                        # Moving the widget and leaving the state behind meant
                        # a "95% CI" selection survived a SEM→SD flip made from
                        # a plot card: the bars kept drawing 1.96 × SD under a
                        # label that read SD. The widget and the state say the
                        # same thing or the figure lies about itself.
                        self._app._app_state.plot.bar_err_kind = target
                        _update_stats_preview()
                        # No redraw here: this observer runs *because* something
                        # else flipped ``use_sem``, and that caller redraws. A
                        # second one would double every such toggle.
                except Exception:
                    swallowed()  # was: pass (silent except)

            if not self._app._ctx.runtime.sem.observers:
                self._app._ctx.runtime.sem.observers = []
            self._app._ctx.runtime.sem.observers.append(_sync_err_from_app)

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

            if not self._app._ctx.runtime.fov.observers:
                self._app._ctx.runtime.fov.observers = []
            self._app._ctx.runtime.fov.observers.append(_sync_across_from_app)

        # Show: Mean (bars only) / Mean+spread (bars + error bars) / All points
        # (overlay per-cell dots via the beeswarm path). Drives
        # PlotState.bar_show_mode, consumed by the bar renderer.
        _show_init = str(getattr(self._app._app_state.plot, "bar_show_mode", "mean_spread")
                         or "mean_spread")
        # Only where there is a bar to draw one of the three ways — see the
        # facet table. Skipping the build rather than hiding the widget is what
        # keeps ``_show_seg`` unset, so the re-seed in
        # ``_resync_plot_state_segments`` steps over it too.
        show_sc = _seg(
            [("Mean", "mean"), ("Mean+spread", "mean_spread"), ("All points", "all_pts")],
            current=_show_init,
        ) if self._supports("bar_show_mode") else None
        if show_sc is not None:
            _stats_state["show"] = show_sc.currentData() or "mean_spread"

            def _on_show_change(*_a) -> None:
                v = show_sc.currentData() or "mean_spread"
                _stats_state["show"] = v
                self._app._app_state.plot.bar_show_mode = v
                try:
                    self._redraw_active_plot()
                except Exception:
                    swallowed()
            show_sc.currentChanged.connect(_on_show_change)
            add_stacked(s_stats, "Show", show_sc)
            self._show_seg = show_sc

        _update_stats_preview()

        # Axes preview: shows the current axis target on the section header.
        _axes_preview = QLabel(self._current_axis)
        _axes_preview.setObjectName("Muted")
        s_axes.setValueWidget(_axes_preview)
        axis_cb.currentTextChanged.connect(lambda t, _l=_axes_preview: _l.setText(t))

        # ── Legend ──────────────────────────────────────────────────────────
        # Only where the figure draws a legend this section can reach —
        # see the facet table.
        if self._supports("legend"):
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

        # ── Overlays ────────────────────────────────────────────────────────
        # Cross-plot threshold marker (off by default). Drives
        # PlotState.show_threshold_line, read by the Distribution / line-CDF /
        # smFISH / bar-Fraction renderers.
        # Both overlays are drawn by renderers that read them off
        # ``PlotState``; a figure whose renderer reads neither shows
        # neither — see the facet table.
        if self._supports("plot_overlays"):
            s_over = section("Overlays", expanded=False)
            thr_tog = ToggleSwitch("Show threshold line")
            thr_tog.setChecked(bool(getattr(
                self._app._app_state.plot, "show_threshold_line", False)))
            thr_tog.setToolTip(
                "Overlay the ON/OFF threshold marker on plots — the line/band at "
                "ThreshFracOn on the Distribution, line-plot CDF and smFISH plots, "
                "and the 0.5 reference + 'threshold=' label on the bar Fraction "
                "panel.")

            def _on_threshold_line_toggle(on: bool) -> None:
                self._app._app_state.plot.show_threshold_line = bool(on)
                try:
                    self._redraw_active_plot()
                except Exception:
                    swallowed()
            thr_tog.toggled.connect(_on_threshold_line_toggle)
            add_row(s_over, "", thr_tog)

            # Value labels: the "12.34 ± 1.20" text above each bar / marker. Drives
            # PlotState.show_value_labels + .value_label_format, read by the Bar,
            # Line and Scatter-aggregate renderers (and their Batch Export twins)
            # through ``core.plot_annotations``. Global rather than per-figure, like
            # the threshold marker beside it: it states the plotted statistic, which
            # is the same statistic on whichever tab is showing it. The per-cell
            # Scatter view has no statistic under a point and says so on the figure
            # rather than drawing nothing.
            val_tog = ToggleSwitch("Show value labels")
            val_tog.setChecked(bool(getattr(
                self._app._app_state.plot, "show_value_labels", False)))
            val_tog.setToolTip(
                "Print each point's value beside it on the Bar, Line and "
                "Scatter → Aggregate plots — the mean, and ± the spread whenever "
                "an error bar is drawn for it; an aggregate scatter point states "
                "both of its coordinates. A bar whose spread could not be computed "
                "is labelled with its mean alone (the 'n/a' mark already says the "
                "spread is missing). The per-cell scatter takes none: one point "
                "there is one cell, not a statistic.")

            def _on_value_labels_toggle(on: bool) -> None:
                self._app._app_state.plot.show_value_labels = bool(on)
                try:
                    self._redraw_active_plot()
                except Exception:
                    swallowed()
            val_tog.toggled.connect(_on_value_labels_toggle)
            add_row(s_over, "", val_tog)

            val_fmt = QLineEdit(str(getattr(
                self._app._app_state.plot, "value_label_format", "") or ""))
            val_fmt.setPlaceholderText("auto")
            val_fmt.setToolTip(
                "printf-style format for the value labels — %.2f for two decimals, "
                "%.1e for scientific notation, %d for a whole number. Leave blank "
                "to pick a format from each value's magnitude. Both halves of a "
                "label share the format, except a spread it would round to zero — "
                "that keeps its own precision rather than reading as a measured "
                "zero.")

            def _on_value_label_format() -> None:
                self._app._app_state.plot.value_label_format = val_fmt.text().strip()
                if not bool(getattr(self._app._app_state.plot,
                                    "show_value_labels", False)):
                    return
                try:
                    self._redraw_active_plot()
                except Exception:
                    swallowed()
            val_fmt.editingFinished.connect(_on_value_label_format)
            add_row(s_over, "Value format", val_fmt)

        # ── Time ─────────────────────────────────────────────────────
        # The time-axis label correction: an interval multiplier and a fixed
        # offset, together the map ``filename hours × scale + offset``. Drives
        # PlotState.time_scale / .time_offset, read by the Line, Bar, Spatial
        # and batch-export renderers through ``time_rescale.rescale_for``. Its
        # own section rather than a row under Overlays: it draws nothing on the
        # figure, it renames the numbers an existing axis already prints.
        # Label-only: the plotted values, the timepoint menus, the Statistics tab
        # and every export keep the hours the pipeline parsed out of the
        # filenames, and any axis it changes says so in its label.
        #
        # Two controls rather than one because they answer two independent
        # questions about the plate — how far apart the images are, and where
        # the first one sits — and a plate can need either without the other.
        s_time = section("Time", expanded=False)
        _time_preview = QLabel("")
        _time_preview.setObjectName("Muted")
        s_time.setValueWidget(_time_preview)

        def _time_preview_text() -> str:
            """The collapsed section's one-glance summary of both controls.

            Built from the live state rather than from the value that just
            changed, so editing either control redraws a summary that describes
            both — a preview showing only the half last touched would say "Off"
            on an axis that is still being shifted.

            The interval half reads as an interval when the plate has one, so
            the summary agrees with the row beneath it. On a plate whose
            spacing is uneven there is no interval to state and the row is a
            multiplier again, so the summary says "uneven" rather than printing
            a number that is true of no pair of ticks.
            """
            plot = self._app._app_state.plot
            factor = float(getattr(plot, "time_scale", 1.0))
            offset = float(getattr(plot, "time_offset", 0.0))
            timepoints = self._dataset_timepoints()
            shown = _displayed_interval(timepoints, _rescale_for(plot))
            bits = []
            if shown is not None:
                bits.append(f"{shown:g}h/tp")
            elif factor != 1.0:
                bits.append(f"×{factor:g}")
            if offset != 0.0:
                bits.append(f"{'−' if offset < 0 else '+'}{abs(offset):g}")
            # "uneven" is said only when it is *true*: gaps that disagree. A
            # plate with one timepoint has no gap at all, and calling that
            # uneven names a defect in a dataset that has none — the first
            # version did exactly that, and only driving the control on a
            # one-timepoint plate showed it.
            if shown is None and len(timepoints) >= 2:
                bits.append("(uneven)")
            text = " ".join(b for b in bits if b)
            return text or "Off"

        # Opens at the plate's **own** interval, not at the multiplier's neutral
        # 1. The field is labelled "Interval per timepoint" and it now holds
        # one: the measured spacing between this dataset's timepoints, put
        # through whatever correction is in force. On a plate imaged at 0, 4, 8,
        # 12 it reads 4, where it used to read 1 — a number that described the
        # identity map rather than the data, which is what was reported.
        #
        # ``PlotState.time_scale`` goes on storing the *multiplier*, and
        # ``factor_for_interval`` is the conversion. Nothing downstream learns a
        # second spelling of the correction, and a settings file written by an
        # older build still means what it said.
        #
        # The consequence worth stating: typing 4 on a plate that already
        # carries real hours now leaves the axis alone, where before it drew
        # 0, 16, 32. On the T01…T12 plates this feature was built for the
        # measured interval is 1, so a typed number is its own multiplier and
        # the behaviour is unchanged.
        _tp_interval = _displayed_interval(
            self._dataset_timepoints(),
            _rescale_for(self._app._app_state.plot))
        time_scale = _Stepper(
            minimum=0.001, maximum=100000.0, single_step=0.5,
            value=float(_tp_interval if _tp_interval is not None
                        else getattr(self._app._app_state.plot, "time_scale", 1.0)),
            decimals=3)
        self._time_scale_stepper = time_scale
        time_scale.setToolTip(
            "How far apart the timepoints really are, in hours.\n\n"
            "Opens at this dataset's own spacing, measured from the hours the "
            "pipeline read out of the filenames — so a plate imaged every four "
            "hours reads 4 before you touch anything. Type a different number "
            "and the time axes are relabelled to that interval: a run numbered "
            "T01…T12 but imaged every 4 hours reads 4, 8, 12 … once you set 4.\n\n"
            "When a plate's timepoints are unevenly spaced there is no single "
            "interval, so the row falls back to a plain multiplier on the "
            "filename hours (1 = off) and the section summary says so.\n\n"
            "Labels only — the timepoint menus, the Statistics tab and every "
            "exported CSV keep the hours read from the filenames, so a rescaled "
            "axis names the correction to say why the numbers differ.")

        def _on_time_scale(value: float) -> None:
            # A typed interval becomes the stored multiplier here, and only
            # here. With no measurable interval ``factor_for_interval`` returns
            # the value unchanged, which is the old multiplier behaviour — so
            # the uneven-spacing plate keeps working rather than dividing by a
            # number that does not exist.
            self._app._app_state.plot.time_scale = _factor_for_interval(
                self._dataset_timepoints(), value,
                fallback=float(getattr(self._app._app_state.plot,
                                       "time_scale", 1.0)))
            _time_preview.setText(_time_preview_text())
            try:
                self._redraw_active_plot()
            except Exception:
                swallowed()
        time_scale.valueChanged.connect(_on_time_scale)
        add_row(s_time, "Interval per timepoint", time_scale)

        # Applied *after* the multiplier, and stated that way in the tooltip:
        # the interval turns a filename index into hours, so a shift typed in
        # hours has to land on the result rather than on the index.
        time_offset = _Stepper(minimum=-100000.0, maximum=100000.0,
                               single_step=1.0,
                               value=float(getattr(
                                   self._app._app_state.plot, "time_offset", 0.0)),
                               decimals=3)
        time_offset.setToolTip(
            "A fixed amount added to every time label, applied after the "
            "interval above — the axis reads (filename value × interval) + "
            "offset.\n\n"
            "For a plate imaged every 4 hours whose first image was taken 2 "
            "hours after treatment rather than at it, set an interval of 4 and "
            "an offset of −2, and the axis reads 2, 6, 10 … Negative values are "
            "ordinary; 0 = off.\n\n"
            "Labels only, on the same terms as the interval — the timepoint "
            "menus, the Statistics tab and every exported CSV keep the hours "
            "read from the filenames, and every axis it shifts names the "
            "correction in its label.")

        def _on_time_offset(value: float) -> None:
            self._app._app_state.plot.time_offset = float(value)
            _time_preview.setText(_time_preview_text())
            try:
                self._redraw_active_plot()
            except Exception:
                swallowed()
        time_offset.valueChanged.connect(_on_time_offset)
        add_row(s_time, "Offset", time_offset)

        _time_preview.setText(_time_preview_text())

        # ── Lines & Markers ─────────────────────────────────────────────────
        # Everything here styles ``ax.lines`` and the marker collections
        # tagged for it; a figure holding neither shows none of it —
        # see the facet table.
        if self._supports("marks"):
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
            lo_edit.setMaximumWidth(_LO_EDIT_MAX_W)
            hi_edit = QLineEdit(str(self._prefs[f"{lim_key}_max"]))
            hi_edit.setMaximumWidth(_HI_EDIT_MAX_W)
            self._bind_getter_setter(f"{lim_key}_min", lo_edit)
            self._bind_getter_setter(f"{lim_key}_max", hi_edit)
            add_row(s_lim, label, hrow(lo_edit, QLabel("…"), hi_edit, spacing=sp.xs))
        # Give the limits back to the plot. Typing a limit is easy and undoing
        # it was not: blanking the four fields by hand leaves the axes exactly
        # where they were, because the style pass only ever *sets* limits and
        # has no way to re-enable autoscale (the same reason the Reset button
        # redraws). ``_autoscale_axes`` does the three things that together
        # amount to "forget them".
        auto_btn = QPushButton("Auto scale")
        auto_btn.setToolTip(
            "Clear the typed limits on every panel of this figure and let the "
            "plot scale its own axes again.")
        auto_btn.clicked.connect(lambda _=False: self._autoscale_axes())
        add_full(s_lim, hrow(auto_btn))
        # The log toggles, their bases, and the section summary that
        # reports them. A figure whose axes carry positions rather than
        # values shows none of the three — see the facet table. The
        # summary goes with them because it exists to say which of these
        # toggles is on; left behind it would read "linear" for ever on a
        # figure with no scale to state.
        if self._supports("log_axes"):
            # The per-cell scatter pins its log state to scatter-specific prefs
            # (default on) so the shared x_log/y_log — which other plot types reset
            # to linear — can't silently turn it off. Toggling triggers a redraw so
            # the axis re-tags itself; other figures keep the shared keys.
            _is_scatter = self._is_scatter_cells_fig()
            _xkey = "scatter_x_log" if _is_scatter else "x_log"
            _ykey = "scatter_y_log" if _is_scatter else "y_log"
            _xdef = True if _is_scatter else False
            xlog_cb = ToggleSwitch("X log")
            xlog_cb.setChecked(self._log_toggle_seed(_xkey, "x", _xdef))
            ylog_cb = ToggleSwitch("Y log")
            ylog_cb.setChecked(self._log_toggle_seed(_ykey, "y", _xdef))
            self._bind_getter_setter(_xkey, xlog_cb)
            self._bind_getter_setter(_ykey, ylog_cb)
            # **A redraw, not a restyle.** The restyle path can only set the scale
            # of an axis that is already drawn; it cannot re-decide anything the
            # renderer chose from the data, and on a tab that pins its own log state
            # (``_aw_force_xlog`` — Response Curves, the scatter) it is overruled
            # outright, which is how the Response tab's X log toggle came to do
            # nothing at all. Every figure redraws here for the same reason, and
            # each redraw ends in a restyle anyway, so the lightweight path is not
            # lost — it is merely no longer the only thing that runs.
            xlog_cb.toggled.connect(lambda _=False: self._redraw_for_a_user_edit())
            ylog_cb.toggled.connect(lambda _=False: self._redraw_for_a_user_edit())
            add_stacked(s_lim, "Log scale", hrow(xlog_cb, ylog_cb))

            # The base each log axis is drawn in. Editable rather than a fixed list:
            # ``LOG_BASE_CHOICES`` are what almost everyone wants and are one click
            # away, but a base this panel does not offer is still a base, and
            # refusing to type it would be a control that answers a narrower
            # question than the one asked. Anything at or below 1 is not a base at
            # all and falls back to 10 (``plot_style.log_base_value``) rather than
            # taking the axis down.
            _xbase_cb = QComboBox()
            _xbase_cb.setEditable(True)
            _xbase_cb.addItems(list(LOG_BASE_CHOICES))
            _xbase_cb.setCurrentText(str(self._prefs.get("x_log_base", "10")))
            _xbase_cb.setToolTip(
                "Base of the X log axis — 10, 2, 3, e, or any number above 1.")
            _ybase_cb = QComboBox()
            _ybase_cb.setEditable(True)
            _ybase_cb.addItems(list(LOG_BASE_CHOICES))
            _ybase_cb.setCurrentText(str(self._prefs.get("y_log_base", "10")))
            _ybase_cb.setToolTip(
                "Base of the Y log axis — 10, 2, 3, e, or any number above 1.")
            self._bind_getter_setter("x_log_base", _xbase_cb)
            self._bind_getter_setter("y_log_base", _ybase_cb)
            # Same argument as the toggles: a tab that positions anything against
            # the axis's steps (the Response tab puts its zero-dose column one step
            # below the lowest real dose) can only re-place it by drawing again.
            _xbase_cb.currentTextChanged.connect(
                lambda _t: self._redraw_for_a_user_edit())
            _ybase_cb.currentTextChanged.connect(
                lambda _t: self._redraw_for_a_user_edit())
            add_stacked(s_lim, "Log base", hrow(QLabel("X"), _xbase_cb,
                                                QLabel("Y"), _ybase_cb,
                                                spacing=sp.xs))

            def _lim_preview_text() -> str:
                tags = []
                if xlog_cb.isChecked():
                    tags.append(f"X log{_base_suffix(_xbase_cb.currentText())}")
                if ylog_cb.isChecked():
                    tags.append(f"Y log{_base_suffix(_ybase_cb.currentText())}")
                return ", ".join(tags) if tags else "linear"

            _lim_preview = QLabel(_lim_preview_text())
            _lim_preview.setObjectName("Muted")
            s_lim.setValueWidget(_lim_preview)
            for _w in (xlog_cb, ylog_cb):
                _w.toggled.connect(
                    lambda *_a, _l=_lim_preview: _l.setText(_lim_preview_text()))
            for _w in (_xbase_cb, _ybase_cb):
                _w.currentTextChanged.connect(
                    lambda *_a, _l=_lim_preview: _l.setText(_lim_preview_text()))

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
                list_widget.setMaximumHeight(_LIST_WIDGET_MAX_H)
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
        # cleanly inside the panel's width; without this the QPushButton
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
        _t_getter = self._getters.get("axis_target")
        target = str(_t_getter()) if _t_getter is not None else "All"
        for k, getter in self._getters.items():
            # Per-axis Axes-section controls (font sizes, tick rotation / length /
            # direction / visibility, group-label size) must not leak into the
            # shared global style when a single axis is targeted — they live only
            # in that axis's bucket. (When "All" is selected they *do* write the
            # global baseline.)
            if k in _PER_AXIS_STYLE_KEYS and target != "All":
                continue
            self._prefs[k] = getter()
        # Mirror the axis-specific keys into the bucket for the currently
        # selected axis so each axis retains its own xlim/ylim/scale + font sizes.
        self._current_axis = target
        bucket = self._axis_buckets.setdefault(self._current_axis, {})
        for k in self._axis_keys:
            g = self._getters.get(k)
            bucket[k] = g() if g is not None else self._prefs.get(k)
        # An "All" edit resets every panel: drop the per-axis overrides for the
        # Axes-section style keys so they fall back to the freshly-set global value.
        if target == "All":
            for i in range(1, len(self._fig.axes) + 1):
                b = self._axis_buckets.get(str(i))
                if b:
                    for k in _PER_AXIS_STYLE_KEYS:
                        b.pop(k, None)
        self._prefs["axis_buckets"] = self._axis_buckets

    def _targetable_axis_numbers(self) -> list[str]:
        """The 1-based numbers of the axes an "Axis #" edit can actually reach.

        Every axes of the figure except the colour bars, which the style pass
        deliberately leaves alone (their scale, ticks and limits belong to the
        mappable that made them). Numbering stays positional so a number means
        the same axes here, in ``axis_buckets`` and in the style pass.
        """
        return [
            str(i)
            for i, ax in enumerate(getattr(self._fig, "axes", ()) or (), start=1)
            if not bool(getattr(ax, "_aw_colorbar", False))
        ]

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
                elif k in _PER_AXIS_STYLE_KEYS:
                    # A panel with no override for an Axes-section style key shows
                    # the current global value (not the neutral default), so
                    # switching axes doesn't make the field misreport what's
                    # actually rendered.
                    setter(self._prefs.get(k, DEFAULT_EXPORT_STYLE_PREFS[k]))
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
            apply_export_style_to_current(self._app._ctx, self._fig, self._canvas)
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

    def _autoscale_axes(self) -> None:
        """Forget every typed axis limit and let the plots scale themselves.

        Three steps, and leaving any one of them out leaves the figure looking
        exactly as it did:

        * blank the four limit fields — the half the user can see;
        * clear the limit keys out of *every* per-axis bucket, because
          ``apply_export_style_prefs`` reads the bucket first and only falls
          back to the flat key, so on a multi-panel figure the fields are not
          where the drawn limit came from;
        * redraw. The style pass only ever *sets* limits; nothing in it can
          re-enable autoscale, so an axis keeps the last bounds it was handed
          until its own renderer builds it again. ``_reset_defaults`` carries
          the same note for the same reason.

        The log toggles and bases are deliberately untouched: an axis drawn in
        log stays in log, it just picks its own bounds again.
        """
        self._updating = True
        try:
            for key in AXIS_LIMIT_PREF_KEYS:
                setter = self._setters.get(key)
                if setter is not None:
                    setter(DEFAULT_EXPORT_STYLE_PREFS[key])
            drop_bucket_limits(self._axis_buckets)
        finally:
            self._updating = False
        self._persist()
        # The redraw is this button's only visible effect, so a failure nobody
        # reported would read as a button that does nothing at all — which is
        # indistinguishable from the defect this button exists to fix.
        if not self._redraw_bound_figure():
            with swallow("report that the auto-scale redraw did not run"):
                self._app._set_status(
                    "Axis limits cleared, but the plot could not be redrawn — "
                    "switch tabs and back to refresh it.")
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
                redraw_bars(app._ctx)
            elif tab == "Scatter Plot":
                from well_viewer.features.scatter.tab_view import scatter_redraw_active
                scatter_redraw_active(app._ctx)
            elif (tab in _FEATURES_THAT_REDRAW_THEMSELVES
                    and app._ctx.features.redraw(app._ctx, tab)):
                # Spatial, Multivariate and Response Curves each render from
                # their own state and are not part of ``redraw_app``'s fan-out,
                # so without this a Properties edit made while looking at one of
                # them changed nothing until the tab was left and re-entered.
                # Resolved by name through the registry rather than imported, so
                # this sidebar names no other feature's module and a profiled-out
                # feature just falls through.
                #
                # Named rather than "any feature with a redraw hook": the
                # registry calls the hook as ``redraw()(ctx)`` and Line Graphs'
                # takes required keyword arguments, so a blanket dispatch would
                # raise on that tab.
                pass
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

    def _dataset_timepoints(self) -> list:
        """This dataset's timepoint hours, or an empty list.

        Read defensively and afresh on every call rather than captured when the
        panel is built: the sidebar is built once per figure and cached for the
        life of the process, so a captured list would describe whichever plate
        happened to be open first. That is the same staleness the segment
        re-seed exists for, one control over.
        """
        state = getattr(self._app, "_app_state", None)
        dataset = getattr(state, "dataset", None)
        return list(getattr(dataset, "all_timepoints_cache", None) or [])

    def _resync_time_interval(self) -> None:
        """Re-seed the interval row from the current dataset.

        The row shows a number measured from the plate, and the panel outlives
        the plate. Without this, opening Properties on a plate imaged every four
        hours after having opened it on an hourly one shows the hourly figure —
        and worse than a stale label, the number is then wrong about the data it
        claims to describe, which is the whole defect this row was changed to
        fix.

        Signals are blocked around the write: this is the widget catching up
        with the dataset, not the user setting an interval, and letting it fire
        would convert the *old* plate's number into a factor against the new
        plate's spacing.
        """
        stepper = getattr(self, "_time_scale_stepper", None)
        if stepper is None:
            return
        plot = getattr(self._app._app_state, "plot", None)
        if plot is None:
            return
        shown = _displayed_interval(self._dataset_timepoints(),
                                    _rescale_for(plot))
        want = float(shown if shown is not None
                     else getattr(plot, "time_scale", 1.0))
        if abs(float(stepper.value()) - want) < 1e-9:
            return
        stepper.blockSignals(True)
        try:
            stepper.setValue(want)
        finally:
            stepper.blockSignals(False)

    def _is_line_fig(self) -> bool:
        return getattr(self._app, "_line_fig", None) is self._fig

    def _is_response_fig(self) -> bool:
        """True only for the Response Curves figure.

        The Colours section is gated per figure for the same reason the Density
        and Cell values sections are: the mode changes nothing on a scatter or
        heat-map figure, and a control that does nothing is the defect §7.6 of
        ``CLAUDE.md`` is about wearing the opposite face.
        """
        return getattr(self._app, "_response_fig", None) is self._fig

    def _redraw_for_a_user_edit(self) -> None:
        """Redraw the bound figure, unless the panel is writing to itself.

        ``_updating`` is raised while the panel pushes values *into* its own
        widgets — restoring defaults, applying a preset, switching the targeted
        axis — and every one of those moves several fields at once. Without this
        the log controls would each ask for a full redraw of the figure on the
        way past, and the caller redraws once at the end anyway. It is the same
        guard ``_on_fields_changed`` opens with, for the same reason.
        """
        if self._updating:
            return
        self._redraw_bound_figure()

    def _log_toggle_seed(self, key: str, which: str, default: bool) -> bool:
        """What the X/Y log toggle should show when the panel opens.

        The stored preference, except where this figure has **no** stored answer
        — then the axis on screen, because that is what the user is looking at.

        The Response tab is why. It picks a log axis from the doses when the
        preference is unset (``figure_pref_is_set`` is what keeps "never
        touched" distinct from "turned off"), so a toggle seeded from the unset
        preference read *off* over an axis that was plainly logarithmic. Worse
        than merely wrong: ``_persist`` writes every field on any edit, so
        changing the title size wrote that ``False`` down and the next redraw
        obeyed it — an unrelated control silently un-logging the axis.

        Only the two shared keys behave this way. The scatter's own
        ``scatter_x_log`` / ``scatter_y_log`` default to on and are always
        stored, so there is nothing to infer there.
        """
        stored = bool(self._prefs.get(key, default))
        if key not in ("x_log", "y_log"):
            return stored
        with swallow("read whether this figure's log preference is set"):
            if figure_pref_is_set(self._app._ctx, self._fig, key):
                return stored
        axes = list(getattr(self._fig, "axes", ()) or ())
        if not axes:
            return stored
        scale = (axes[0].get_xscale if which == "x" else axes[0].get_yscale)()
        return scale != "linear"

    def _is_scatter_cells_fig(self) -> bool:
        """True only for the per-cell scatter figure (density + gates apply there)."""
        return getattr(self._app, "_scatter_fig", None) is self._fig

    def _is_heatmap_fig(self) -> bool:
        """True only for the Heat Map figure (the cell-value format applies there)."""
        return getattr(self._app, "_heatmap_fig", None) is self._fig

    def _supports(self, facet: str) -> bool:
        """Whether this figure has the thing *facet* names.

        Reads ``_STYLE_FACETS_A_FIGURE_LACKS``, which lists only the exceptions,
        so an unlisted figure supports everything. A section built behind this
        registers no getters and no setters, which is what the rest of the panel
        needs: ``_persist`` writes the keys it holds, ``_resync_pref_fields``
        re-seeds the setters it holds, and both iterate rather than assume.
        """
        for attr, missing in _STYLE_FACETS_A_FIGURE_LACKS.items():
            if getattr(self._app, attr, None) is self._fig:
                return facet not in missing
        return True

    def _is_bar_fig(self) -> bool:
        """True only for the Bar plot figure (the colour mode applies there)."""
        return getattr(self._app, "_bar_fig", None) is self._fig

    def _on_cell_format_changed(self) -> None:
        """Persist the Heat Map cell format, then redraw.

        The format decides the *text* of the per-cell annotations, which only the
        heat-map renderer writes, so a restyle of the existing figure would leave
        the old numbers on screen.
        """
        if self._updating:
            return
        try:
            self._persist()
            self._redraw_bound_figure()
        except Exception:
            swallowed()  # a formatting edit must never raise out of the handler

    def _gate_count_text(self) -> str:
        n = len(self._app._app_state.cell_gating.scatter_gates or [])
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
        self._app._app_state.scatter.gates_hidden = not bool(on)
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
        """Rebuild the editable per-gate name rows from the typed scatter gates."""
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
        gates = self._app._app_state.cell_gating.scatter_gates or []
        for idx, gate in enumerate(gates):
            edit = QLineEdit(str(gate.get("name") or ""))
            edit.setToolTip("Rename this gate (used by the Cell Gating filter).")
            # editingFinished can fire twice (focus-out + Enter); commit once and
            # revert the field on a rejected (blank/duplicate) name.
            def _commit(_i=idx, _e=edit) -> None:
                self._gate_rename_in_progress = True
                try:
                    ok = rename_gate(self._app._ctx, _i, _e.text())
                finally:
                    self._gate_rename_in_progress = False
                if not ok:
                    cur = (self._app._app_state.cell_gating.scatter_gates or [])
                    if 0 <= _i < len(cur):
                        _e.setText(str(cur[_i].get("name") or ""))
            edit.editingFinished.connect(_commit)
            layout.addWidget(edit)
            # Caption the axes the gate was drawn against. A gate is a rectangle
            # in one specific (x, y) metric pair — the same box means something
            # different once the axis combos move — so the list has to say which
            # pair, not just the gate's name.
            axes_text = gate_axes_label(gate.get("x_col"), gate.get("y_col"))
            if axes_text:
                axes_lbl = QLabel(axes_text)
                axes_lbl.setObjectName("Muted")
                axes_lbl.setWordWrap(True)
                axes_lbl.setToolTip(
                    "The x and y metrics this gate was drawn on. It filters "
                    "cells by those columns whatever the plot currently shows."
                )
                layout.addWidget(axes_lbl)

    def _on_clear_gates(self) -> None:
        clear_gates(self._app._ctx)
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

    def _redraw_bound_figure(self) -> bool:
        """Trigger the redraw entry point that matches the bound figure.

        Dispatch is by figure identity — the imported redraw function is
        the source of truth, not a string method name on app.

        Returns whether the redraw ran. It has to: a caller that reports the
        failure to the user reads this value, and while every branch returned
        ``None`` that report fired on every *success*. The Bar colour control
        set the mode, redrew the figure correctly, and then told the user
        "the plot could not be redrawn — switch tabs and back to refresh it."
        A message that is wrong whenever things work is worse than no message,
        because the one time it is right reads the same as all the others.
        """
        app = self._app
        try:
            if getattr(app, "_line_fig", None) is self._fig:
                app._redraw()
                return True
            if getattr(app, "_bar_fig", None) is self._fig:
                redraw_bars(app._ctx)
                return True
            if getattr(app, "_scatter_fig", None) is self._fig:
                redraw_scatter_orchestrate(app._ctx)
                return True
            if getattr(app, "_scatter_agg_fig", None) is self._fig:
                redraw_scatter_agg_orchestrate(app._ctx)
                return True
            if getattr(app, "_distribution_fig", None) is self._fig:
                redraw_distribution(app._ctx)
                return True
            if getattr(app, "_heatmap_fig", None) is self._fig:
                redraw_heatmap(app._ctx)
                return True
            for fig_attr, feature in _FIGURE_OWNING_FEATURES:
                if getattr(app, fig_attr, None) is self._fig:
                    return bool(app._ctx.features.redraw(app._ctx, feature))
            _logger.warning(
                "Properties: no redraw is registered for this figure, so the "
                "Line tab was redrawn instead. Add its figure to "
                "_FIGURE_OWNING_FEATURES.")
            app._redraw()
            return True
        except Exception:
            swallowed()  # was: pass (silent except)
            return False

    def _resync_pref_fields(self) -> None:
        """Re-seed the fields backed by this figure's stored preferences.

        The sibling of :meth:`_resync_plot_state_segments`, one layer over, and
        it exists for the same reason: this panel is built once per figure and
        cached for the life of the process, while the store underneath it is
        rewritten by things the panel never hears about. Loading a dataset
        restores ``export_axis_prefs`` from the saved state, and a dataset
        *switch* drops every axis limit (``ExportPrefsState.
        reset_for_new_dataset``) because a limit is in the units of the plate
        that was open.

        Without this the fields keep the previous plate's answers, and — worse
        than a stale label — ``_persist`` writes **every** field on any edit, so
        changing the title size would put the dropped limits straight back onto
        the new plate. That is exactly the defect the drop exists to prevent,
        arriving through the panel instead.

        Only the figure-scoped keys: those are the ones a load rewrites out from
        under the panel. The two shared log toggles go back through
        :meth:`_log_toggle_seed`, so a figure with no stored answer still reads
        its answer off the axis rather than off an unset preference.
        """
        self._updating = True
        try:
            self._axis_buckets = dict(self._prefs.get("axis_buckets") or {})
            self._current_axis = self._default_axis_target()
            self._axis_buckets.setdefault(
                self._current_axis,
                {k: self._prefs.get(k) for k in self._axis_keys},
            )
            bucket = self._axis_buckets.get(self._current_axis) or {}
            for key, setter in self._setters.items():
                if key not in FIGURE_SCOPED_PREF_KEYS or key == "axis_buckets":
                    continue
                if key == "axis_target":
                    value = self._current_axis
                elif key in ("x_log", "y_log"):
                    value = self._log_toggle_seed(
                        key, key[0], bool(DEFAULT_EXPORT_STYLE_PREFS.get(key)))
                elif key in bucket and bucket[key] is not None:
                    value = bucket[key]
                else:
                    value = self._prefs.get(
                        key, DEFAULT_EXPORT_STYLE_PREFS.get(key))
                if value is None:
                    continue
                with swallow(f"re-seed the Properties field for {key}"):
                    setter(value)
        finally:
            self._updating = False

    def _resync_plot_state_segments(self) -> None:
        """Re-seed the segments that write straight to ``PlotState``.

        The *Error bars* and *Across* segments do not need this — they carry
        observers on ``ctx.runtime.sem`` / ``ctx.runtime.fov`` because the plot
        cards write those same flags and the two were seen drifting. *Show* and
        *Bar colours* have no such observer: nothing else in the UI writes them,
        so the only other writer is ``persistence.view_state``, which sets them
        on a dataset load (``_apply_bar`` for the bar mode, the top-level plot
        block for the line one) and on a reset (``restore_defaults``).

        ``_color_seg`` is whichever of the three Colours segments this panel was
        built for, so the field it re-seeds from — and that field's default,
        which is not "plate" on all three — is recorded when it is built rather
        than assumed here.

        This panel is built once per figure and cached for the life of the
        process, so without a re-seed here the widget keeps showing the previous
        dataset's choice. That is worse than a stale label: re-selecting the
        segment a ``SegmentedControl`` already believes is current emits no
        signal, so clicking the mode the user wants does nothing until they
        click the other one first.

        Signals are blocked around the write — this is the widget catching up
        with the state, not a user changing it, and letting it fire would run
        the handler and redraw on every panel open.
        """
        self._resync_time_interval()
        plot = getattr(self._app._app_state, "plot", None)
        if plot is None:
            return
        for attr, field, default in (
            ("_color_seg", getattr(self, "_color_seg_field", "bar_color_mode"),
             getattr(self, "_color_seg_default", "plate")),
            ("_show_seg", "bar_show_mode", "mean_spread"),
        ):
            seg = getattr(self, attr, None)
            if seg is None:
                continue
            want = str(getattr(plot, field, default) or default)
            if seg.currentData() == want:
                continue
            seg.blockSignals(True)
            try:
                seg.setCurrentByData(want)
            finally:
                seg.blockSignals(False)
            preview = getattr(self, f"{attr}_preview", None)
            if preview is not None:
                # Moving the widget and leaving its collapsed-section summary
                # behind is the failure the *Error bars* observer already
                # documents: the header reads one mode while the figure draws
                # another, and nothing on screen says which is live.
                preview.setText(_SEGMENT_LABELS.get(want, want))

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
