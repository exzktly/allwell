"""Centre page-stack builder (Qt port).

``build_centre`` is the entry point. Pages are hosted by a
:class:`NamedPageStack` (a ``QStackedWidget`` subclass with name-keyed
lookup); the section RailNav in the left sidebar drives the current page
externally. Phase 15 retired the v1 ``QTabWidget`` + ``_GroupedTabBar``
chrome along with the per-instance ``select_by_text`` closure.

Sections:

* **Analysis** — Plotting (sub-pages: Line Graphs, Bar Plots, Scatter Plot,
  Distribution, Heat Map), Statistics.
* **Images** — Image Table, Segmentation, smFISH.
* **Data** — Review CSV, Sample Definitions, Batch Export.

Pages are also built lazily: only the initially active "Plotting" page
and the sidebar panels that other code touches at startup are constructed
eagerly. The remaining bodies build on a per-event-loop-tick timer so
the window paints quickly and stays responsive while heavy widget trees
(matplotlib canvases, image grids, etc.) populate in the background. If
the user navigates to a page whose body hasn't been built yet, the
builder for that page is run inline on the page-change event.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Set

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)
from well_viewer.core.tab_controller import on_tab_change
from well_viewer.features.preview.panel_view import build_review_image_panel
from well_viewer.features.grouping.replicate_panel_view import build_replicate_panel
from well_viewer.core.safe import swallowed


_logger = logging.getLogger("well_viewer.centre_view")


class NamedPageStack(QStackedWidget):
    """``QStackedWidget`` with name-keyed page lookup.

    Phase 15 replacement for the legacy ``_notebook`` / ``_plotting_notebook``
    ``QTabWidget``s. Exposes the v2 name-based API
    (``addPage`` / ``setCurrentByName`` / ``currentName`` / ``pageNames`` /
    ``nameOf``).
    """

    currentNameChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._names: list[str] = []
        self._by_name: dict[str, QWidget] = {}
        super().currentChanged.connect(self._emit_name)

    def addPage(self, name: str, widget: QWidget) -> int:  # noqa: N802
        idx = self.addWidget(widget)
        # Pad / overwrite the name list to keep indices aligned with the
        # underlying QStackedWidget order.
        while len(self._names) <= idx:
            self._names.append("")
        self._names[idx] = name
        self._by_name[name] = widget
        return idx

    def setCurrentByName(self, name: str) -> bool:  # noqa: N802
        w = self._by_name.get(name)
        if w is None:
            return False
        self.setCurrentWidget(w)
        return True

    def currentName(self) -> str:  # noqa: N802
        idx = self.currentIndex()
        if 0 <= idx < len(self._names):
            return self._names[idx]
        return ""

    def pageNames(self) -> list[str]:  # noqa: N802
        return list(self._names)

    def nameOf(self, w: QWidget) -> str | None:  # noqa: N802
        idx = self.indexOf(w)
        if 0 <= idx < len(self._names):
            return self._names[idx]
        return None

    def _emit_name(self, idx: int) -> None:
        if 0 <= idx < len(self._names):
            self.currentNameChanged.emit(self._names[idx])


def build_centre(app, parent: QWidget) -> None:
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    # Phase 15: the centre is a NamedPageStack (QStackedWidget subclass) —
    # the rail nav on the left drives the current page externally, and
    # callers reach pages via currentName() / setCurrentByName() / pageNames().
    app._notebook = NamedPageStack(parent)
    app._notebook.setObjectName("CentreTabs")
    layout.addWidget(app._notebook, 1)

    # Map tab title -> deferred builder. Populated below; drained after the
    # window paints. The tab-change handler also calls into this map so a
    # user who clicks an un-built tab forces its builder to run inline.
    pending: Dict[str, Callable[[], None]] = {}
    app._centre_pending_builders = pending

    # Defined early so builder functions (including _build_plotting, which is
    # called eagerly as the first tab) can call it before the groups loop ends.
    def _build_pending(title: str) -> None:
        builder = pending.pop(title, None)
        if builder is None:
            return
        try:
            builder()
        except Exception:
            _logger.exception("Deferred build for %r failed", title)

    app._centre_build_pending = _build_pending

    # Tabs whose construction we never want to run from the background
    # drain — they only build on first user access (tab click). The tabs
    # listed here pull in the heaviest dependencies (matplotlib QtAgg,
    # skimage, tifffile) that aren't worth amortising at startup. smFISH
    # used to live here; it is now a sub-tab inside the Segmentation
    # parent, and its builder defers itself via the segmentation sub-stack.
    lazy_only: Set[str] = {"smFISH"}
    app._centre_lazy_only_titles = frozenset(lazy_only)

    # Pre-create stable widget handles so deferred builder closures can
    # capture them. Sample Definitions in particular needs the QWidget to
    # be allocated up-front so the deferred ``_build_sample_definitions``
    # closure can see it; otherwise the variable would be shadowed by the
    # ``addTab`` call further down.
    tab_groups = QWidget(app._notebook)
    QVBoxLayout(tab_groups).setContentsMargins(0, 0, 0, 0)

    # "Plotting" is a top-level tab whose body is a nested QTabWidget
    # containing the five plot sub-tabs.  We pre-allocate both the outer
    # container and the five sub-tab frames so builder closures (and the
    # drain) can reference them before the nested QTabWidget exists.
    plotting_container = QWidget(app._notebook)
    QVBoxLayout(plotting_container).setContentsMargins(0, 0, 0, 0)

    _PLOT_SUBTABS = [
        "Bar Plots", "Line Graphs", "Scatter Plot", "Distribution", "Heat Map",
    ]

    # Sidebars referenced by data-load + tab-switch logic must exist
    # immediately even though the centre tab bodies that depend on them
    # are deferred.
    from well_viewer.features.preview.preview_view import build_preview_picker
    build_replicate_panel(app, app._sidebar_sample_frame)
    build_preview_picker(app, app._sidebar_preview_frame)

    # Stable name -> tab QWidget map for builder closures to reach into.
    # Populated in two phases: plot sub-tab frames are inserted here before
    # the groups loop so the builder closures can close over them, even
    # though those frames end up inside the nested "Plotting" QTabWidget
    # rather than directly in app._notebook.
    tab_frames: Dict[str, QWidget] = {}
    for _t in _PLOT_SUBTABS:
        _f = QWidget()
        QVBoxLayout(_f).setContentsMargins(12, 0, 12, 12)
        tab_frames[_t] = _f

    # Group definitions: ordered list of (title, builder, eager_flag).
    # ``eager_flag`` means build the tab body immediately; otherwise the
    # builder is registered in ``pending`` and drained later. The first
    # tab in the first group is the initial active tab and is built eagerly.
    def _line_graphs_eager() -> None:
        from well_viewer.features.lineplot.tab_view import build_line_graphs_tab
        build_line_graphs_tab(app._ctx, tab_frames["Line Graphs"])

    def _build_bar() -> None:
        from well_viewer.features.barplot.tab_view import build_bar_plots_tab
        build_bar_plots_tab(app._ctx, tab_frames["Bar Plots"])

    def _build_scatter() -> None:
        from well_viewer.features.scatter.tab_view import build_scatter_tab
        build_scatter_tab(app._ctx, tab_frames["Scatter Plot"])

    def _build_distribution() -> None:
        from well_viewer.features.distribution.tab_view import build_distribution_tab
        build_distribution_tab(app._ctx, tab_frames["Distribution"])

    def _build_heatmap() -> None:
        from well_viewer.features.heatmap.tab_view import build_heatmap_tab
        build_heatmap_tab(app._ctx, tab_frames["Heat Map"])

    # Register plot sub-tab builders in pending now so the background drain
    # can populate their frames while the app is starting up.  _build_plotting
    # (below) calls _build_pending("Line Graphs") eagerly and the drain
    # handles the rest.
    pending["Line Graphs"] = _line_graphs_eager
    pending["Bar Plots"] = _build_bar
    pending["Scatter Plot"] = _build_scatter
    pending["Distribution"] = _build_distribution
    pending["Heat Map"] = _build_heatmap

    def _build_plotting() -> None:
        """Build the Plotting section: mockup-style ctxbar above a tab-bar-hidden
        QTabWidget that hosts the five renderers (line / bar / scatter cells +
        agg / distribution / heatmap). Phase 11 retires the visible plot-type
        QTabBar in favour of the v2 ctxbar.subnav SegmentedControl (Q5: switch
        governs the whole canvas — implemented here by routing through the
        existing per-renderer pages); a global Channel chip lives in
        ctxbar.right (A4); Add panel / Configure subplots / Edit axes-curve
        buttons (B9, B11) sit alongside.

        The underlying per-renderer pages keep their existing rendering
        contracts so we don't regress functionality. Phase 11b will collapse
        them into a single shared PlotCanvas.
        """
        from PySide6.QtWidgets import QTabBar

        # ── ctxbar ─ Row 1 (plot-type QTabBar, matching Sample Definitions style) ──
        # Switched from SegmentedControl (pill style) to a styled QTabBar so
        # the plot-type switcher matches the Sample Definitions sub-tabs.
        # The bar is wired to ``plotting_nb`` (NamedPageStack) so the page
        # stack still owns the actual content; the bar is purely a selector.
        ctxbar = QWidget(plotting_container)
        ctxbar.setObjectName("PlottingCtxbar")
        ctxbar.setAttribute(Qt.WA_StyledBackground, True)
        from theme import Colors as _C, Spacing as _S, Typography as _T, Radii as _R
        ctxbar.setStyleSheet(
            f"#PlottingCtxbar {{ background-color: {_C.surface}; "
            f"border-bottom: 1px solid {_C.border_subtle}; }}"
        )
        cbl = QHBoxLayout(ctxbar)
        cbl.setContentsMargins(0, 0, 0, 6)
        cbl.setSpacing(_S.sm)

        # A standalone QTabBar with the SampleDefinitionsSubTabs object
        # name picks up the existing rectangular-tab QSS — keeps the look
        # consistent without duplicating styling rules.
        sub_seg = QTabBar()
        sub_seg.setObjectName("SampleDefinitionsSubTabs")
        sub_seg.setExpanding(False)
        sub_seg.setElideMode(Qt.ElideNone)
        sub_seg.setUsesScrollButtons(True)
        sub_seg.setDrawBase(False)
        for title in _PLOT_SUBTABS:
            sub_seg.addTab(title)
        cbl.addWidget(sub_seg, 1)

        plotting_container.layout().addWidget(ctxbar, 0)

        # ── ctxbar ─ Row 2 (channel + actions, sits above the canvas) ────
        action_row = QWidget(plotting_container)
        action_row.setObjectName("PlottingActionRow")
        action_row.setAttribute(Qt.WA_StyledBackground, True)
        action_row.setStyleSheet(
            f"#PlottingActionRow {{ background-color: {_C.surface}; "
            f"border-bottom: 1px solid {_C.border_subtle}; }}"
        )
        arl = QHBoxLayout(action_row)
        arl.setContentsMargins(12, 4, 12, 4)
        arl.setSpacing(_S.sm)

        # Phase 11b (A4): the channel selector is now a single global combo
        # in the ctxbar. The per-renderer combos still exist for back-compat
        # but ``app._on_plot_channel_selected`` mirrors every change back to
        # all of them, so editing here re-renders every renderer's view
        # against the new channel.
        from PySide6.QtWidgets import QComboBox as _QComboBox
        chan_lbl = QLabel("Channel")
        chan_lbl.setStyleSheet(
            f"color: {_C.text_muted}; font-size: {_T.caption_size}px; "
            f"letter-spacing: 0.08em;"
        )
        arl.addWidget(chan_lbl)
        # Expose the label so tab-switch logic can hide it on scatter tabs.
        app._plotting_channel_lbl = chan_lbl
        app._plotting_channel_cb = _QComboBox()
        app._plotting_channel_cb.setMinimumContentsLength(8)
        app._plotting_channel_cb.setStyleSheet(
            f"QComboBox {{ background-color: {_C.panel_elevated}; "
            f"color: {_C.text_secondary}; "
            f"border: 1px solid {_C.border_subtle}; "
            f"border-radius: {_R.pill}px; padding: 2px 22px 2px 10px; "
            f"font-size: {_T.caption_size}px; font-weight: 500; "
            f"min-width: 88px; }}"
            f"QComboBox:hover {{ color: {_C.text_primary}; }}"
        )

        def _on_global_channel(_idx: int) -> None:
            on_select = getattr(app, "_on_plot_channel_selected", None)
            if on_select is None:
                return
            try:
                on_select(app._plotting_channel_cb)
            except Exception:
                swallowed()  # was: pass (silent except)

        app._plotting_channel_cb.currentIndexChanged.connect(_on_global_channel)
        arl.addWidget(app._plotting_channel_cb)
        # Legacy display-only chip remains as an attribute for code that
        # still pokes at ``_plotting_channel_chip`` — point it at the new
        # combo so callers that read its text still work.
        app._plotting_channel_chip = app._plotting_channel_cb

        # Global Property (intensity-column) combo — mirrors the channel
        # combo styling. Drives ``app._app_state.channel.active_metric`` so every plot, stat,
        # and CSV export tracks the user's chosen intensity property.
        from well_viewer.data.metric_labels import METRIC_ORDER as _METRIC_ORDER
        prop_lbl = QLabel("Property")
        prop_lbl.setStyleSheet(
            f"color: {_C.text_muted}; font-size: {_T.caption_size}px; "
            f"letter-spacing: 0.08em;"
        )
        arl.addWidget(prop_lbl)
        app._plotting_metric_lbl = prop_lbl
        app._plotting_metric_cb = _QComboBox()
        app._plotting_metric_cb.setMinimumContentsLength(8)
        app._plotting_metric_cb.setStyleSheet(
            f"QComboBox {{ background-color: {_C.panel_elevated}; "
            f"color: {_C.text_secondary}; "
            f"border: 1px solid {_C.border_subtle}; "
            f"border-radius: {_R.pill}px; padding: 2px 22px 2px 10px; "
            f"font-size: {_T.caption_size}px; font-weight: 500; "
            f"min-width: 110px; }}"
            f"QComboBox:hover {{ color: {_C.text_primary}; }}"
        )
        app._plotting_metric_cb.addItems(_METRIC_ORDER)

        def _on_global_metric(_idx: int) -> None:
            handler = getattr(app, "_on_metric_selected_global", None)
            if handler is None:
                return
            try:
                handler()
            except Exception:
                swallowed()  # was: pass (silent except)

        app._plotting_metric_cb.currentIndexChanged.connect(_on_global_metric)
        arl.addWidget(app._plotting_metric_cb)

        # Quick-edit for the active channel's ThreshFracOn (the per-channel
        # ON/OFF intensity threshold that drives the fraction-above- and
        # mean-above-threshold series). Editing here writes through the Cell
        # Gating tab's field (the single source of truth) and re-renders, so a
        # user can tune it without leaving the plot. Hidden on scatter sub-tabs
        # (per-axis channels) by ``_refresh_channel_chip``.
        from widgets.stepper import Stepper as _Stepper
        thresh_lbl = QLabel("Threshold")
        thresh_lbl.setStyleSheet(
            f"color: {_C.text_muted}; font-size: {_T.caption_size}px; "
            f"letter-spacing: 0.08em;"
        )
        arl.addWidget(thresh_lbl)
        app._plotting_thresh_frac_lbl = thresh_lbl
        app._plotting_thresh_frac_stepper = _Stepper(
            value=50.0, minimum=0.0, maximum=1e9, single_step=1.0, decimals=1)
        app._plotting_thresh_frac_stepper.setFixedWidth(96)
        # Don't let the threshold box grab keyboard focus when the window first
        # shows (it auto-focused on load) — only take focus on click.
        app._plotting_thresh_frac_stepper.setFocusPolicy(Qt.ClickFocus)
        _thr_edit = getattr(app._plotting_thresh_frac_stepper, "_edit", None)
        if _thr_edit is not None:
            _thr_edit.setFocusPolicy(Qt.ClickFocus)
        app._plotting_thresh_frac_stepper.setToolTip(
            "ON/OFF intensity threshold for the active channel — cells above it "
            "count toward the fraction-above- and mean-above-threshold series. "
            "Edits the same value as the Cell Gating tab's ThreshFracOn."
        )
        app._plotting_thresh_frac_stepper.valueChanged.connect(
            lambda v: app._set_thresh_frac_on(v)
        )
        arl.addWidget(app._plotting_thresh_frac_stepper)

        arl.addStretch(1)

        # "+ Add panel" button retired — the underlying per-renderer
        # multi-subplot story was never wired (the _add_panel handler
        # just toasted a "coming soon" message), so the button only
        # added clutter.
        # Configure-subplots and edit-axes buttons moved to the bottom of
        # the Properties rail (slide-out drawer); the centre action-row now
        # only carries the canvas-wide actions (channel selector + copy).
        # The Copy-SVG button has moved next to each tab's "Export CSV"
        # primary action so the figure-output controls sit together.
        # The handler is exposed on app below so per-tab buttons can
        # invoke it without re-importing centre_view.

        plotting_container.layout().addWidget(action_row, 0)

        # ── renderer pages (per-tab views in a NamedPageStack — Phase 15) ─
        plotting_nb = NamedPageStack(plotting_container)
        plotting_nb.setObjectName("PlottingSubTabs")
        plotting_container.layout().addWidget(plotting_nb, 1)
        app._plotting_notebook = plotting_nb

        # Register all pages BEFORE wiring currentChanged so the first emit
        # always sees a valid currentName().
        for title in _PLOT_SUBTABS:
            plotting_nb.addPage(title, tab_frames[title])

        # Build the first sub-tab immediately so the user sees content.
        _build_pending(_PLOT_SUBTABS[0])

        def _on_plotting_subtab(_i: int = 0) -> None:
            sub_title = plotting_nb.currentName()
            if sub_title in pending:
                _build_pending(sub_title)
            on_tab_change(app, None)
            _refresh_channel_chip(sub_title)
            # Properties rail retired — its scope picker followed the
            # active sub-tab. Per-plot styling now happens through the
            # floating Export Style sidebar.
            # QTabBar uses tabText(i) for the title — _PLOT_SUBTABS is the
            # registry of legal page names and matches QTabBar tab indices.
            if sub_seg.tabText(sub_seg.currentIndex()) != sub_title:
                blocked = sub_seg.blockSignals(True)
                try:
                    if sub_title in _PLOT_SUBTABS:
                        sub_seg.setCurrentIndex(_PLOT_SUBTABS.index(sub_title))
                finally:
                    sub_seg.blockSignals(blocked)

        plotting_nb.currentChanged.connect(_on_plotting_subtab)

        def _on_sub_seg(idx: int) -> None:
            target = sub_seg.tabText(idx)
            if target:
                plotting_nb.setCurrentByName(target)

        sub_seg.currentChanged.connect(_on_sub_seg)
        sub_seg.setCurrentIndex(0)

        def _refresh_channel_chip(title: str) -> None:
            """Re-populate the global ctxbar channel combo on sub-tab switch.

            Reads from the canonical channel list (``app._app_state.channel.fluor_channels`` +
            ratios) rather than mirroring a per-renderer combo's items.
            The per-renderer combos can lag the canonical list — they're
            seeded with a ``["GFP"]`` placeholder and are only refreshed
            during ``_update_channel_selector``. A sub-tab built lazily
            after a dataset has loaded therefore had a stale placeholder
            its first time on screen, which used to clobber the global
            combo back to just ``["GFP"]`` (or whatever single placeholder
            it held) and broke channel tracking until the user wiggled
            the dropdown two or three times to force a re-sync via
            ``_set_active_channel``.

            ``title`` selects whether the global Channel / Property combos
            are visible — the scatter tabs (cells + aggregate) have
            per-axis selectors so the global combos are hidden there to
            avoid confusion about which one drives the plot.
            """
            global_cb = getattr(app, "_plotting_channel_cb", None)
            metric_cb = getattr(app, "_plotting_metric_cb", None)
            # Hide the global Channel/Property combos and the ThreshFracOn
            # quick-edit on scatter sub-tabs — those tabs own per-axis
            # (channel, property) selectors, so a single active-channel control
            # would be ambiguous.
            is_scatter = title in ("Scatter Plot", "Scatter Cells", "Scatter Agg")
            for w in (global_cb, metric_cb,
                      getattr(app, "_plotting_thresh_frac_stepper", None)):
                if w is not None:
                    w.setVisible(not is_scatter)
            # Also hide the "Channel" / "Property" / "Threshold" labels in the
            # action row so the row looks intentional (not just empty boxes hidden).
            for lbl_attr in ("_plotting_channel_lbl", "_plotting_metric_lbl",
                             "_plotting_thresh_frac_lbl"):
                lbl = getattr(app, lbl_attr, None)
                if lbl is not None:
                    lbl.setVisible(not is_scatter)
            # Keep the quick-edit showing the active channel's value on every
            # sub-tab switch (guarded; no-op before the stepper is built).
            app._refresh_thresh_frac_quick_edit()
            if global_cb is None:
                return
            real_labels = [str(ch).upper() for ch in
                           (app._app_state.channel.fluor_channels or [])]
            ratio_labels = list(getattr(app, "_ratio_dropdown_labels",
                                        lambda: [])() or [])
            # Shape features are channel-independent virtual channels shown
            # under their own section — mirror ``_update_channel_selector`` so a
            # lazily-built sub-tab switch doesn't clobber the Shape Features
            # group out of the global combo. Rebuilding with the plain
            # fluor+ratio list (as this did before) dropped the section every
            # time the user changed plot sub-tabs, so shape metrics only
            # reappeared after a channel "wiggle" forced ``_update_channel_selector``.
            from pipeline.shape_features import label_for as _shape_label_for
            from well_viewer.core.ui_helpers import (
                set_combo_values as _scv,
                set_combo_values_sectioned as _scv_sectioned,
            )
            shape_cols = list(app._app_state.channel.shape_features or [])
            shape_labels = [_shape_label_for(c) or c for c in shape_cols]
            plot_sections = [("", real_labels + ratio_labels)]
            if shape_labels:
                plot_sections.append(("Shape Features", shape_labels))
            inline_labels = real_labels + ratio_labels + shape_labels
            try:
                active_label = app._active_channel_label()
            except Exception:
                active_label = ""
            if inline_labels:
                _scv_sectioned(global_cb, plot_sections)
            else:
                _scv(global_cb, ["—"])
            if active_label and global_cb.findText(active_label) >= 0:
                blocked = global_cb.blockSignals(True)
                try:
                    global_cb.setCurrentIndex(global_cb.findText(active_label))
                finally:
                    global_cb.blockSignals(blocked)

        # Configure-subplots / Edit-axes handlers live on ``app`` (kept here
        # as helpers for tab toolbars and the per-plot Export Style sidebar's
        # footer matplotlib helpers).
        def _config_subplots() -> None:
            for attr in ("_line_card", "_bar_card", "_scatter_card",
                         "_scatter_agg_card", "_distribution_card", "_heatmap_card"):
                card = getattr(app, attr, None)
                if card is None or not card.isVisible():
                    continue
                nav = getattr(card, "_nav", None)
                if nav is not None and hasattr(nav, "configure_subplots"):
                    try:
                        nav.configure_subplots()
                    except Exception:
                        swallowed()  # was: pass (silent except)
                return

        def _edit_axes() -> None:
            for attr in ("_line_card", "_bar_card", "_scatter_card",
                         "_scatter_agg_card", "_distribution_card", "_heatmap_card"):
                card = getattr(app, attr, None)
                if card is None or not card.isVisible():
                    continue
                nav = getattr(card, "_nav", None)
                if nav is not None and hasattr(nav, "edit_parameters"):
                    try:
                        nav.edit_parameters()
                    except Exception:
                        swallowed()  # was: pass (silent except)
                return

        app._plot_config_subplots = _config_subplots
        app._plot_edit_axes = _edit_axes

        def _copy_svg() -> None:
            """Copy the active card's figure to the clipboard, preferring
            vector.

            On macOS the figure is written natively to ``NSPasteboard`` so
            Illustrator pastes true editable vector; elsewhere a Qt
            clipboard payload is used (see ``copy_figure_to_clipboard``).
            The figure is captured in publication mode.
            """
            from well_viewer.features.export.style import copy_figure_to_clipboard
            for attr in ("_line_card", "_bar_card", "_scatter_card",
                         "_scatter_agg_card", "_distribution_card", "_heatmap_card"):
                card = getattr(app, attr, None)
                if card is None or not card.isVisible():
                    continue
                fig = getattr(card, "_figure", None) or getattr(card, "figure", None)
                canvas = getattr(card, "_canvas", None)
                if fig is None and canvas is not None:
                    fig = getattr(canvas, "figure", None)
                if fig is None:
                    return
                prev_theme = getattr(card, "_plot_theme", "screen")
                try:
                    if hasattr(card, "setPlotTheme") and prev_theme != "publication":
                        card.setPlotTheme("publication")

                    status = copy_figure_to_clipboard(fig)
                finally:
                    if (hasattr(card, "setPlotTheme")
                            and getattr(card, "_plot_theme", "screen") != prev_theme):
                        try:
                            card.setPlotTheme(prev_theme)
                        except Exception:
                            swallowed()  # was: pass (silent except)
                if hasattr(app, "_set_status"):
                    app._set_status(status)
                return
        app._copy_active_card_as_svg = _copy_svg

        def _save_active_card_figure() -> None:
            """Open a Save-As dialog for the active visible plot card's
            figure. Uses the existing ``app._save_matplotlib_fig`` helper
            so all renderers honour the same file-type filter and the
            active dataset directory as the starting location."""
            for attr, default in (
                ("_line_card", "lines.png"),
                ("_bar_card", "bars.png"),
                ("_scatter_card", "scatter.png"),
                ("_scatter_agg_card", "scatter_agg.png"),
                ("_distribution_card", "distribution.png"),
                ("_heatmap_card", "heatmap.png"),
            ):
                card = getattr(app, attr, None)
                if card is None or not card.isVisible():
                    continue
                fig = getattr(card, "_figure", None) or getattr(card, "figure", None)
                if fig is None:
                    return
                try:
                    app._save_matplotlib_fig(fig, default)
                except Exception:
                    swallowed()  # was: pass (silent except)
                return
        app._save_active_card_figure = _save_active_card_figure

        _refresh_channel_chip(_PLOT_SUBTABS[0])

    def _build_image_table() -> None:
        from well_viewer.features.image_table.tab_view import build_image_table_tab
        from well_viewer.features.image_table.picker_view import build_image_table_picker
        build_image_table_tab(app._ctx, tab_frames["Image Table"])
        build_image_table_picker(app, app._sidebar_image_table_frame)

    def _build_cell_segmentation() -> None:
        """Build the Segmentation page — a QTabWidget hosting the Review Image
        panel and the Playground (interactive single-frame correction testbed).

        The Playground sub-tab is built lazily on first click (it pulls the
        correction estimators), mirroring the Sample Definitions sub-tab pattern.
        """
        parent = tab_frames["Segmentation"]
        if parent.layout() is None:
            QVBoxLayout(parent).setContentsMargins(0, 0, 0, 0)

        sub_tabs = QTabWidget(parent)
        # Reuse the SampleDefinitionsSubTabs QSS (rounded rectangular tabs with a
        # min-width) so the bold selected-tab text isn't clipped — same pattern
        # the plotting sub-tab bar uses to pick up that styling.
        sub_tabs.setObjectName("SampleDefinitionsSubTabs")
        sub_tabs.tabBar().setExpanding(False)
        sub_tabs.tabBar().setElideMode(Qt.ElideNone)
        sub_tabs.tabBar().setUsesScrollButtons(True)
        parent.layout().addWidget(sub_tabs)
        app._segmentation_subtabs = sub_tabs

        review_tab = QWidget(sub_tabs)
        QVBoxLayout(review_tab).setContentsMargins(12, 12, 12, 12)
        build_review_image_panel(app, review_tab)
        sub_tabs.addTab(review_tab, "Review Image")

        playground_tab = QWidget(sub_tabs)
        QHBoxLayout(playground_tab).setContentsMargins(12, 12, 12, 12)
        sub_tabs.addTab(playground_tab, "Playground")
        app._playground_subtab_frame = playground_tab
        app._playground_subtab_built = False

        def _on_seg_subtab(idx: int) -> None:
            if sub_tabs.tabText(idx) != "Playground":
                return
            from well_viewer.features.playground.controller import (
                playground_repopulate_dropdowns,
            )
            if not app._playground_subtab_built:
                from well_viewer.features.playground.tab_view import (
                    build_playground_tab,
                )
                build_playground_tab(app._ctx, app._playground_subtab_frame)
                app._playground_subtab_built = True
            else:
                # Pick up a dataset loaded after the tab was first built.
                playground_repopulate_dropdowns(app)
        sub_tabs.currentChanged.connect(_on_seg_subtab)

    def _build_smfish() -> None:
        """Build the smFISH tab (its own top-level rail entry).

        Lazy-only (see ``lazy_only``) because it pulls heavy deps
        (tifffile / skimage / matplotlib-QtAgg) — it constructs on first
        click rather than during the background drain.
        """
        parent = tab_frames["smFISH"]
        if parent.layout() is None:
            QVBoxLayout(parent).setContentsMargins(0, 0, 0, 0)
        try:
            from well_viewer.features.smfish.tab_view import build_smfish_tab
            build_smfish_tab(app._ctx, parent)
        except Exception:
            _logger.exception("Build for smFISH tab failed")

    def _build_stats() -> None:
        from well_viewer.features.stats.view import (
            build_stats_group_editor, build_stats_tab,
        )
        build_stats_tab(app, tab_frames["Statistics"])
        build_stats_group_editor(app, app._sidebar_stats_frame)

    def _build_review_csv() -> None:
        app._build_review_csv_tab(tab_frames["Review CSV"])

    def _build_sample_definitions() -> None:
        from well_viewer.features.grouping.replicate_panel_view import (
            build_sample_definitions_centre,
        )
        build_sample_definitions_centre(app, tab_groups)

    def _build_batch_export() -> None:
        from well_viewer.features.batch_export.tab_view import build_batch_export_tab
        build_batch_export_tab(app._ctx, tab_frames["Batch Export"])

    groups: List[Tuple[str, List[Tuple[str, Callable[[], None]]]]] = [
        ("Analysis", [
            # "Plotting" is a single top-level tab that contains Line Graphs,
            # Bar Plots, Scatter Plot, Distribution, and Heat Map as sub-tabs.
            ("Plotting", _build_plotting),
            # Cell Gating moved into the Sample Definitions tab (Cell Gating
            # sub-tab) — that's the new home for global per-cell config.
            ("Statistics", _build_stats),
        ]),
        ("Images", [
            # Movie Montage was folded into Image Table — pick a well, set
            # the channel to NUC+SEG (or any other), and click "Distribute
            # Timepoints" to get the same per-timepoint grid.
            ("Image Table", _build_image_table),
            ("Segmentation", _build_cell_segmentation),
            # smFISH has its own rail entry (it briefly lived as a sub-tab of
            # Segmentation). Lazy-only — heavy deps build on first click.
            ("smFISH", _build_smfish),
        ]),
        ("Data", [
            ("Review CSV", _build_review_csv),
            ("Sample Definitions", _build_sample_definitions),
            ("Batch Export", _build_batch_export),
        ]),
    ]

    def _new_tab(title: str) -> QWidget:
        if title == "Sample Definitions":
            # Sample Definitions uses the pre-allocated tab_groups widget so
            # the deferred body builder closure (_build_sample_definitions)
            # can reference it before the tab is added to the QTabWidget.
            frame = tab_groups
        elif title == "Plotting":
            # "Plotting" uses the pre-allocated container so the nested
            # QTabWidget can be inserted into it by _build_plotting.
            frame = plotting_container
        else:
            frame = QWidget(app._notebook)
            QVBoxLayout(frame).setContentsMargins(0, 0, 0, 0)
        app._notebook.addPage(title, frame)
        tab_frames[title] = frame
        return frame

    # Add tabs in group order. The tab bar / group separator chrome retired
    # in Phase 15 — RailNav (in the left sidebar) is now the only section
    # selector, so group_starts is unused at the view layer.
    for group_idx, (_group_label, tabs) in enumerate(groups):
        for tab_idx_in_group, (title, builder) in enumerate(tabs):
            _new_tab(title)
            if group_idx == 0 and tab_idx_in_group == 0:
                # Initial active tab — build eagerly so the user sees content.
                builder()
            else:
                pending[title] = builder

    # Special pre-build hooks for tabs that wire app-level state at build
    # time even though their bodies are deferred (matches the original
    # eager attribute set so external references keep resolving).
    app._batch_export_tab_frame = tab_frames["Batch Export"]

    app._notebook.setCurrentIndex(0)

    def _on_tab_change(_i: int = 0) -> None:
        # Force-build the tab the user just switched to if it hasn't been
        # built yet, so click-before-build never shows a blank tab body.
        title = app._notebook.currentName()
        if title in pending:
            _build_pending(title)
        on_tab_change(app, None)

    app._notebook.currentChanged.connect(_on_tab_change)

    # Drain pending builders one-per-event-loop-tick so the UI stays
    # responsive while heavy tabs (matplotlib canvases, image grids) build
    # in the background. Tabs marked lazy_only stay in ``pending`` so the
    # tab-switch handler can still build them on demand, but the drain
    # never touches them — they only construct when the user clicks them.
    def _next_drain_title():
        for title in pending:
            if title not in lazy_only:
                return title
        return None

    def _drain() -> None:
        title = _next_drain_title()
        if title is None:
            return
        _build_pending(title)
        if _next_drain_title() is not None:
            QTimer.singleShot(0, _drain)

    QTimer.singleShot(0, _drain)


def drain_pending_centre_builders(app) -> None:
    """Force-build any centre tab whose body was deferred at startup.

    Tabs marked lazy-only (Cell Gating, smFISH) are intentionally
    skipped — they construct only when the user actually clicks the
    tab. The tab-switch handler in ``centre_view`` builds them inline
    on first access.
    """
    pending = getattr(app, "_centre_pending_builders", None)
    build = getattr(app, "_centre_build_pending", None)
    if not pending or build is None:
        return
    lazy_only = getattr(app, "_centre_lazy_only_titles", frozenset())
    # Snapshot keys — _centre_build_pending mutates the dict.
    for title in list(pending.keys()):
        if title in lazy_only:
            continue
        build(title)

