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
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QLabel, QStackedWidget, QVBoxLayout, QWidget,
)
from well_viewer.core.tab_controller import on_tab_change
from well_viewer.core.safe import swallowed
import theme
from well_viewer.core.plot_card_actions import PLOT_CARD_ATTRS


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
        # ``_names`` is rebuilt to track the live stack order; ``_by_name`` holds
        # only currently-attached pages; ``_page_order`` is the canonical add
        # order (kept across detach/reattach so Split View can restore it).
        self._names: list[str] = []
        self._by_name: dict[str, QWidget] = {}
        self._page_order: list[str] = []
        super().currentChanged.connect(self._emit_name)

    def _rebuild_names(self) -> None:
        """Resync ``_names`` to the live stack order (after add/detach/reattach)."""
        names = []
        for i in range(self.count()):
            w = self.widget(i)
            names.append(next((n for n, wid in self._by_name.items() if wid is w), ""))
        self._names = names

    def addPage(self, name: str, widget: QWidget) -> int:
        idx = self.addWidget(widget)
        self._by_name[name] = widget
        if name not in self._page_order:
            self._page_order.append(name)
        self._rebuild_names()
        return idx

    def detachPage(self, name: str) -> QWidget | None:
        """Remove a page from the stack and return its widget (kept alive).

        Used by Split View to move a page into the side-by-side splitter. The
        canonical add order is preserved in ``_page_order`` so ``reattachPage``
        can restore the original position.
        """
        w = self._by_name.pop(name, None)
        if w is None:
            return None
        self.removeWidget(w)
        w.setParent(None)
        self._rebuild_names()
        return w

    def reattachPage(self, name: str, widget: QWidget) -> None:
        """Re-insert a previously detached page at its original relative slot."""
        insert_at = 0
        for nm in self._page_order:
            if nm == name:
                break
            if nm in self._by_name:
                insert_at += 1
        self.insertWidget(insert_at, widget)
        self._by_name[name] = widget
        if name not in self._page_order:
            self._page_order.append(name)
        self._rebuild_names()

    def setCurrentByName(self, name: str) -> bool:
        w = self._by_name.get(name)
        if w is None:
            return False
        self.setCurrentWidget(w)
        return True

    def currentName(self) -> str:
        idx = self.currentIndex()
        if 0 <= idx < len(self._names):
            return self._names[idx]
        return ""

    def pageNames(self) -> list[str]:
        return list(self._names)

    def nameOf(self, w: QWidget) -> str | None:
        idx = self.indexOf(w)
        if 0 <= idx < len(self._names):
            return self._names[idx]
        return None

    def _emit_name(self, idx: int) -> None:
        if 0 <= idx < len(self._names):
            self.currentNameChanged.emit(self._names[idx])


def build_centre(win, parent: QWidget) -> None:
    layout = parent.layout()
    if layout is None:
        layout = QVBoxLayout(parent)
        parent.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)

    # Phase 15: the centre is a NamedPageStack (QStackedWidget subclass) —
    # the rail nav on the left drives the current page externally, and
    # callers reach pages via currentName() / setCurrentByName() / pageNames().
    win._notebook = NamedPageStack(parent)
    win._notebook.setObjectName("CentreTabs")
    layout.addWidget(win._notebook, 1)

    # Map tab title -> deferred builder. Populated below; drained after the
    # window paints. The tab-change handler also calls into this map so a
    # user who clicks an un-built tab forces its builder to run inline.
    pending: dict[str, Callable[[], None]] = {}
    win._centre_pending_builders = pending

    # Defined early so builder functions (including _build_plotting, which is
    # called eagerly as the first tab) can call it before the groups loop ends.
    def _build_pending(title: str) -> None:
        builder = pending.pop(title, None)
        if builder is None:
            return
        try:
            builder()
        except Exception:
            # The log alone is not enough. A tab whose builder raises renders as
            # an empty page with no explanation — indistinguishable from a tab
            # with nothing to show — and the traceback lands in a drawer the
            # user has no reason to open. Say so on the status bar too, which is
            # where every other failure in the app reports itself.
            _logger.exception("Deferred build for %r failed", title)
            try:
                win._ctx.notify.set_status(
                    f"{title} failed to build — see the log drawer for details."
                )
            except Exception:
                swallowed()

    win._ctx.runtime.centre.build_pending = _build_pending

    # Tabs whose construction we never want to run from the background
    # drain — they only build on first user access (tab click). The tabs
    # listed here pull in the heaviest dependencies (matplotlib QtAgg,
    # skimage, tifffile) that aren't worth amortising at startup. "Image
    # Browser" is the unified image-review rail entry (Segmentation / Image
    # Table / Cell Browser / Playground sub-tabs) — its sub-tab bodies pull the
    # image-table loaders, so it (and each sub-tab inside it) builds on demand.
    lazy_only: set[str] = {d.name for d in win._features.top_level() if d.lazy_only}
    win._centre_lazy_only_titles = frozenset(lazy_only)

    # Pre-create stable widget handles so deferred builder closures can
    # capture them. Sample Definitions in particular needs the QWidget to
    # be allocated up-front so the deferred ``_build_sample_definitions``
    # closure can see it; otherwise the variable would be shadowed by the
    # ``addTab`` call further down.
    tab_groups = QWidget(win._notebook)
    QVBoxLayout(tab_groups).setContentsMargins(0, 0, 0, 0)

    # "Plotting" is a top-level tab whose body is a nested QTabWidget
    # containing the five plot sub-tabs.  We pre-allocate both the outer
    # container and the five sub-tab frames so builder closures (and the
    # drain) can reference them before the nested QTabWidget exists.
    plotting_container = QWidget(win._notebook)
    QVBoxLayout(plotting_container).setContentsMargins(0, 0, 0, 0)

    # The plot sub-tabs come from the registry (descriptors whose parent is
    # "Plotting"), so a build profile that drops one drops its sub-tab with no
    # edit here. Falls back to the canonical order if none are declared.
    _PLOT_SUBTABS = win._features.sub_leaves("Plotting") or [
        "Bar Plots", "Line Graphs", "Scatter Plot", "Distribution", "Heat Map",
    ]

    # Sidebars referenced by data-load + tab-switch logic must exist
    # immediately even though the centre tab bodies that depend on them
    # are deferred.
    from well_viewer.features.grouping.replicate_panel_view import build_replicate_panel
    from well_viewer.features.preview.preview_view import build_preview_picker
    build_replicate_panel(win._ctx, win._sidebar_sample_frame)
    build_preview_picker(win, win._sidebar_preview_frame)

    # Stable name -> tab QWidget map for builder closures to reach into.
    # Populated in two phases: plot sub-tab frames are inserted here before
    # the groups loop so the builder closures can close over them, even
    # though those frames end up inside the nested "Plotting" QTabWidget
    # rather than directly in win._notebook.
    tab_frames: dict[str, QWidget] = {}
    for _t in _PLOT_SUBTABS:
        _f = QWidget()
        QVBoxLayout(_f).setContentsMargins(12, 0, 12, 12)
        tab_frames[_t] = _f

    # Group definitions: ordered list of (title, builder, eager_flag).
    # ``eager_flag`` means build the tab body immediately; otherwise the
    # builder is registered in ``pending`` and drained later. The first
    # tab in the first group is the initial active tab and is built eagerly.
    # Plot sub-tab + top-level builders are sourced from the feature registry:
    # each descriptor's ``build(ctx, frame)`` wraps the feature's entry point,
    # so centre_view no longer imports any feature tab module directly. The
    # frame is resolved at call time from ``tab_frames`` (populated by the plot
    # sub-tab pre-allocation above / ``_new_tab`` for top-level tabs).
    def _registry_builder(title: str) -> Callable[[], None]:
        return lambda: win._features.get(title).build(win._ctx, tab_frames[title])

    # Register plot sub-tab builders in pending now so the background drain can
    # populate their frames while the win is starting up. _build_plotting
    # (below) builds the first sub-tab eagerly and the drain handles the rest.
    for _sub in _PLOT_SUBTABS:
        pending[_sub] = _registry_builder(_sub)

    def _build_plotting() -> None:
        """Build the Plotting section: the shared control band above a
        ``NamedPageStack`` hosting the five renderers (line / bar / scatter
        cells + agg / distribution / heatmap).

        The plot-type selector is a ``QTabBar`` that does not live here — it is
        registered into the shell's mode band, beside the Review / Analyze /
        Datasets selector, so the two page selectors share one row (§5.2). The
        stack still owns the content; the bar is purely a selector.

        Each per-renderer page keeps its own rendering contract.
        """
        from PySide6.QtWidgets import QTabBar

        from theme import Colors as _C, Radii as _R, Typography as _T

        # A standalone QTabBar carrying the nested-sub-tab object name, which
        # theme.qss styles alongside Sample Definitions' bar — one rule, so the
        # two nested tab strips cannot drift apart.
        sub_seg = QTabBar()
        sub_seg.setObjectName("PlottingSubTabs")
        sub_seg.setExpanding(False)
        sub_seg.setElideMode(Qt.ElideNone)
        sub_seg.setUsesScrollButtons(True)
        sub_seg.setDrawBase(False)
        for title in _PLOT_SUBTABS:
            sub_seg.addTab(title)
        # The bar goes in the shell's mode band, beside the Review / Analyze /
        # Datasets selector, not in this tab's body. Both are page selectors one
        # level apart, so they belong on one row sharing a bottom edge — stacked,
        # they were two strips of chrome above every plot.
        from well_viewer.core.app_shell_view import register_page_tabs
        register_page_tabs(win, "Plotting", sub_seg)

        # ── The shared control band, above the canvas ────────────────────
        # Every Review tab uses the same band, so the plotting row is built
        # through the shared widget rather than as a bespoke QHBoxLayout: the
        # scope controls left in a flow that wraps instead of squeezing labels
        # over the control beside them, actions flush right. See
        # ``widgets/control_band.py``.
        from widgets.control_band import ControlBand
        band = ControlBand(plotting_container)
        win._plotting_band = win._ctx.widgets.register("_plotting_band", band)

        # The Scatter sub-tab's two modes. It lives in the band's scope zone
        # rather than as a tab bar inside the Scatter page: the page then holds
        # nothing but its canvas, and the mode reads as one more thing scoping
        # what is plotted, which is what it is. Driven through
        # ``ctx.runtime.scatter.set_mode`` so the core never imports the
        # feature; the group is shown only while a Scatter sub-tab is current.
        from widgets.segmented_control import SegmentedControl as _Segmented
        scatter_view = _Segmented()
        scatter_view.addSegment("Cells", data="cells")
        scatter_view.addSegment("Wells", data="aggregate")

        def _on_scatter_view(_idx: int) -> None:
            set_mode = getattr(getattr(win._ctx.runtime, "scatter", None),
                               "set_mode", None)
            if set_mode is None:
                return          # the Scatter page has not been built yet
            try:
                set_mode(scatter_view.currentData())
            except Exception:
                swallowed()

        scatter_view.currentChanged.connect(_on_scatter_view)
        win._plotting_scatter_view = win._ctx.widgets.register(
            "_plotting_scatter_view", scatter_view)
        win._plotting_scatter_grp = band.add_scope("View", scatter_view)
        win._plotting_scatter_grp.setVisible(False)

        # Phase 11b (A4): the channel selector is now a single global combo
        # in the band. The per-renderer combos still exist for back-compat
        # but ``win._on_plot_channel_selected`` mirrors every change back to
        # all of them, so editing here re-renders every renderer's view
        # against the new channel.
        from widgets.labeled_combo import LabeledComboBox as _QComboBox
        win._plotting_channel_cb = win._ctx.widgets.register(
            "_plotting_channel_cb", _QComboBox("Channel"))
        # No min-width / setMinimumContentsLength — LabeledComboBox sizes
        # itself to exactly the painted "Channel: …" text, and a floor here
        # would just re-introduce the wasted space that sizing exists to cut.
        win._plotting_channel_cb.setStyleSheet(
            f"QComboBox {{ background-color: {_C.panel_elevated}; "
            f"color: {_C.text_secondary}; "
            f"border: 1px solid {_C.border_subtle}; "
            f"border-radius: {_R.pill}px; padding: 2px 22px 2px 10px; "
            f"font-size: {_T.caption_size}px; font-weight: 500; }}"
            f"QComboBox:hover {{ color: {_C.text_primary}; }}"
        )

        def _on_global_channel(_idx: int) -> None:
            on_select = getattr(win, "_on_plot_channel_selected", None)
            if on_select is None:
                return
            try:
                on_select(win._plotting_channel_cb)
            except Exception:
                swallowed()  # was: pass (silent except)

        win._plotting_channel_cb.currentIndexChanged.connect(_on_global_channel)
        # The combo paints its own "Channel: " prefix (LabeledComboBox), so
        # it needs no separate ControlBand label — pass "" so add_scope adds
        # the combo alone rather than pairing it with a QLabel.
        win._plotting_channel_grp = band.add_scope("", win._plotting_channel_cb)
        # Legacy display-only chip remains as an attribute for code that
        # still pokes at ``_plotting_channel_chip`` — point it at the new
        # combo so callers that read its text still work.
        win._plotting_channel_chip = win._plotting_channel_cb

        # Global Property (intensity-column) combo — mirrors the channel
        # combo styling. Drives ``win._app_state.channel.active_metric`` so every plot, stat,
        # and CSV export tracks the user's chosen intensity property.
        from well_viewer.data.metric_labels import METRIC_ORDER as _METRIC_ORDER
        win._plotting_metric_cb = win._ctx.widgets.register(
            "_plotting_metric_cb", _QComboBox("Property"))
        # No min-width / setMinimumContentsLength — see the Channel combo above.
        win._plotting_metric_cb.setStyleSheet(
            f"QComboBox {{ background-color: {_C.panel_elevated}; "
            f"color: {_C.text_secondary}; "
            f"border: 1px solid {_C.border_subtle}; "
            f"border-radius: {_R.pill}px; padding: 2px 22px 2px 10px; "
            f"font-size: {_T.caption_size}px; font-weight: 500; }}"
            f"QComboBox:hover {{ color: {_C.text_primary}; }}"
        )
        win._plotting_metric_cb.addItems(_METRIC_ORDER)

        def _on_global_metric(_idx: int) -> None:
            handler = getattr(win, "_on_metric_selected_global", None)
            if handler is None:
                return
            try:
                handler()
            except Exception:
                swallowed()  # was: pass (silent except)

        win._plotting_metric_cb.currentIndexChanged.connect(_on_global_metric)
        win._plotting_metric_grp = band.add_scope("", win._plotting_metric_cb)

        # Quick-edit for the active channel's ThreshFracOn (the per-channel
        # ON/OFF intensity threshold that drives the fraction-above- and
        # mean-above-threshold series). Editing here writes through the Cell
        # Gating tab's field (the single source of truth) and re-renders, so a
        # user can tune it without leaving the plot. Hidden on scatter sub-tabs
        # (per-axis channels) by ``_refresh_channel_chip``.
        from widgets.stepper import Stepper as _Stepper
        from well_viewer.core.channel_state_controller import (
            set_on_cutoff as _set_on_cutoff,
        )
        win._plotting_thresh_frac_stepper = win._ctx.widgets.register(
            "_plotting_thresh_frac_stepper",
            _Stepper(value=50.0, minimum=0.0, maximum=1e9, single_step=1.0, decimals=1))
        win._plotting_thresh_frac_stepper.setFixedWidth(theme.Metrics.stepper_w)
        # Don't let the threshold box grab keyboard focus when the window first
        # shows (it auto-focused on load) — only take focus on click.
        win._plotting_thresh_frac_stepper.setFocusPolicy(Qt.ClickFocus)
        _thr_edit = getattr(win._plotting_thresh_frac_stepper, "_edit", None)
        if _thr_edit is not None:
            _thr_edit.setFocusPolicy(Qt.ClickFocus)
        win._plotting_thresh_frac_stepper.setToolTip(
            "ON/OFF intensity threshold for the active channel — cells above it "
            "count toward the fraction-above- and mean-above-threshold series. "
            "Edits the same value as the Cell Gating tab's ThreshFracOn."
        )
        win._plotting_thresh_frac_stepper.valueChanged.connect(
            lambda v: _set_on_cutoff(win._ctx, v)
        )
        # The box edits whichever cutoff the selected Property is measured
        # against, so its caption switches with the target like the other two
        # quick-edits'. The band builds the caption inside the group it returns
        # rather than handing it back, so it is fished out and registered here —
        # two of three captions switching and the third not would be worse than
        # either consistent choice.
        win._plotting_thresh_frac_grp = band.add_scope(
            "ON threshold", win._plotting_thresh_frac_stepper, trailing=True)
        _thr_cap = win._plotting_thresh_frac_grp.findChild(QLabel)
        if _thr_cap is not None:
            win._ctx.widgets.register("_plotting_thresh_frac_lbl", _thr_cap)

        # "+ Add panel" button retired — the underlying per-renderer
        # multi-subplot story was never wired (the _add_panel handler
        # just toasted a "coming soon" message), so the button only
        # added clutter.
        # Configure-subplots and edit-axes buttons moved to the bottom of
        # the Properties rail (slide-out drawer); the centre action-row now
        # only carries the canvas-wide actions (channel selector + copy).
        # The Copy-SVG button has moved next to each tab's "Export CSV"
        # primary action so the figure-output controls sit together.
        # The handler is exposed on win below so per-tab buttons can
        # invoke it without re-importing centre_view.

        plotting_container.layout().addWidget(band, 0)

        # ── renderer pages (per-tab views in a NamedPageStack — Phase 15) ─
        plotting_nb = NamedPageStack(plotting_container)
        plotting_nb.setObjectName("PlottingSubTabs")
        plotting_container.layout().addWidget(plotting_nb, 1)
        win._plotting_notebook = plotting_nb

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
            on_tab_change(win._ctx, None)
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
            """Re-populate Plotting's band channel combo on sub-tab switch.

            Reads from the canonical channel list (``win._app_state.channel.fluor_channels`` +
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

            ``title`` selects which scope controls the band carries. The
            scatter tabs own per-axis (channel, property) selectors, so the
            global Channel / Property / Threshold groups do not drive the plot
            there and are swapped out for the scatter View toggle. They used to
            stay in place greyed, beside a note reading "Scatter uses per-axis
            channels below" — three dead controls and an apology for them,
            occupying the row every other tab uses for live controls.
            """
            global_cb = getattr(win, "_plotting_channel_cb", None)
            is_scatter = title in ("Scatter Plot", "Scatter Cells", "Scatter Agg")
            for grp_attr in ("_plotting_channel_grp", "_plotting_metric_grp",
                             "_plotting_thresh_frac_grp"):
                grp = getattr(win, grp_attr, None)
                if grp is not None:
                    grp.setVisible(not is_scatter)
            scatter_grp = getattr(win, "_plotting_scatter_grp", None)
            if scatter_grp is not None:
                scatter_grp.setVisible(is_scatter)
            # The Scatter page is built lazily, so the toggle can outlive a
            # window in which the feature's runtime hook does not exist yet.
            # Mirror whichever mode the page settled on rather than assuming
            # the toggle's own index is authoritative.
            toggle = getattr(win, "_plotting_scatter_view", None)
            mode = getattr(getattr(win._ctx.state, "scatter", None), "mode", None)
            if is_scatter and toggle is not None and mode:
                blocked = toggle.blockSignals(True)
                try:
                    toggle.setCurrentByData(mode)
                finally:
                    toggle.blockSignals(blocked)
            # Swap in the sub-tab's own controls. Every Plotting leaf adds its
            # scope groups and actions to this one band through a slot of its
            # own (see ``ui_helpers.plotting_band_slot``), so switching sub-tab
            # is a matter of showing one slot and hiding the rest — a leaf with
            # its own band under this one would stack two control rows above
            # every plot, which is what the band replaced.
            from well_viewer.core.ui_helpers import plotting_slot_name
            band.show_slot(plotting_slot_name(
                "Scatter Plot" if is_scatter else title,
                mode if is_scatter else None,
            ))
            # Keep the quick-edit showing the active channel's value on every
            # sub-tab switch (guarded; no-op before the stepper is built).
            win._refresh_thresh_frac_quick_edit()
            if global_cb is None:
                return
            real_labels = [str(ch).upper() for ch in
                           (win._app_state.channel.fluor_channels or [])]
            ratio_labels = list(getattr(win, "_ratio_dropdown_labels",
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
            shape_cols = list(win._app_state.channel.shape_features or [])
            shape_labels = [_shape_label_for(c) or c for c in shape_cols]
            plot_sections = [("", real_labels + ratio_labels)]
            if shape_labels:
                plot_sections.append(("Shape Features", shape_labels))
            inline_labels = real_labels + ratio_labels + shape_labels
            try:
                active_label = win._active_channel_label()
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

        # Configure-subplots / Edit-axes handlers live on ``win`` (kept here
        # as helpers for tab toolbars and the per-plot Export Style sidebar's
        # footer matplotlib helpers).
        def _config_subplots() -> None:
            for attr in PLOT_CARD_ATTRS:
                card = getattr(win, attr, None)
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
            for attr in PLOT_CARD_ATTRS:
                card = getattr(win, attr, None)
                if card is None or not card.isVisible():
                    continue
                nav = getattr(card, "_nav", None)
                if nav is not None and hasattr(nav, "edit_parameters"):
                    try:
                        nav.edit_parameters()
                    except Exception:
                        swallowed()  # was: pass (silent except)
                return

        win._plot_config_subplots = _config_subplots
        win._plot_edit_axes = _edit_axes

        # The active-card Copy / Save actions now live in
        # ``core.plot_card_actions`` as ctx-based functions (A8 P3.3); the plot
        # builders wire their Copy/Save buttons to those directly, so no
        # closures are stamped on the win here any more.

        _refresh_channel_chip(_PLOT_SUBTABS[0])

    # Top-level rail entries are enumerated from the feature registry. Each
    # group's tabs come from ``win._features.in_group(...)`` in declaration
    # order; the builder is the descriptor's registry builder, except the
    # bespoke "Plotting" composite (built by ``_build_plotting`` above, which
    # owns the nested plot sub-tab notebook + control band). Commenting a descriptor
    # out of ``features.registry.FEATURES`` removes its rail entry here — and
    # its dispatch / state / persistence — with no other edit.
    _bespoke_builders = {"Plotting": _build_plotting}

    def _top_level_builder(title: str) -> Callable[[], None]:
        return _bespoke_builders.get(title) or _registry_builder(title)

    groups: List[Tuple[str, List[Tuple[str, Callable[[], None]]]]] = [
        (group, [(d.name, _top_level_builder(d.name))
                 for d in win._features.in_group(group)])
        for group in ("Analysis", "Images", "Data")
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
            frame = QWidget(win._notebook)
            QVBoxLayout(frame).setContentsMargins(0, 0, 0, 0)
        win._notebook.addPage(title, frame)
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

    # Special pre-build hooks for tabs that wire win-level state at build
    # time even though their bodies are deferred (matches the original
    # eager attribute set so external references keep resolving). Guarded so
    # the tab being absent under a build profile is a no-op, not a KeyError.
    if "Batch Export" in tab_frames:
        win._batch_export_tab_frame = tab_frames["Batch Export"]

    win._notebook.setCurrentIndex(0)

    def _on_tab_change(_i: int = 0) -> None:
        # Force-build the tab the user just switched to if it hasn't been
        # built yet, so click-before-build never shows a blank tab body.
        title = win._notebook.currentName()
        if title in pending:
            _build_pending(title)
        on_tab_change(win._ctx, None)

    win._notebook.currentChanged.connect(_on_tab_change)

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


def drain_pending_centre_builders(ctx) -> None:
    """Force-build any centre tab whose body was deferred at startup.

    Tabs marked lazy-only (Cell Gating, smFISH) are intentionally
    skipped — they construct only when the user actually clicks the
    tab. The tab-switch handler in ``centre_view`` builds them inline
    on first access.
    """
    win = ctx.window  # widget host of the pending-builder bookkeeping
    pending = getattr(win, "_centre_pending_builders", None)
    build = ctx.runtime.centre.build_pending
    if not pending or build is None:
        return
    lazy_only = getattr(win, "_centre_lazy_only_titles", frozenset())
    # Snapshot keys — _centre_build_pending mutates the dict.
    for title in list(pending.keys()):
        if title in lazy_only:
            continue
        build(title)

