"""Top-level window assembly for :class:`WellViewerApp`.

``build_ui`` lays out the application shell — topbar, the sidebar│centre
splitter, and the status bar — delegating each region to its own builder
(``_build_sidebar`` / ``_build_centre`` / ``_build_bottom``). Relocated
from ``runtime_app.py`` (teardown Phase 8).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
import theme
from well_viewer.core.sidebar_view import build_sidebar
from well_viewer.core.status_view import build_bottom


#: The left panel's width, shared by all three modes — Review's own rail,
#: Datasets' sidebar, and Analyze's form panel (``analyze_tab.py``) all pin to
#: this one value, so switching modes never visibly resizes the window. 438,
#: not Review's narrower original width, because it's the value Analyze's own
#: per-channel rows need to avoid clipping (widened there twice, 280→350→438,
#: before landing on it) — Review's plate and Datasets' nav list have no
#: trouble being wider, so the shared width is set by whichever mode actually
#: needs the room. The app header's mode bar spans exactly this too, so it and
#: the rail beneath it share a left column.
RAIL_W = 438


def _dataset_button(win, parent):
    """The rail head: the loaded dataset's name, and the actions on it.

    Full rail width, so it reads as the rail's own heading rather than a control
    dropped into it. ``Open…`` lives here rather than at the far end of the
    topbar, next to the name of the folder it would replace.

    Long folder names elide in the *middle*: dataset folders differ in their
    tail as often as their head (``plate3_run2_2024-06-11`` against
    ``plate3_run2_2024-06-12``), so a right-elided name can leave two datasets
    indistinguishable. The full path is on the tooltip either way.
    """
    from PySide6.QtWidgets import QMenu, QToolButton
    btn = QToolButton(parent)
    btn.setObjectName("DatasetButton")
    btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    btn.setPopupMode(QToolButton.InstantPopup)
    btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    from widgets.icons import make_icon
    btn.setIcon(make_icon("folder-open", 14))
    menu = QMenu(btn)
    menu.addAction("Open…").triggered.connect(lambda _=False: win._browse())
    btn.setMenu(menu)
    win._dataset_menu = menu
    # Assign before refreshing: ``refresh_dataset_button`` reads the button off
    # ``win``, so refreshing here would silently no-op and leave it text-less
    # until the first dataset load.
    win._dataset_btn = btn
    refresh_dataset_button(win)
    return btn


def refresh_dataset_button(win) -> None:
    """Point the rail head at whatever dataset is currently loaded."""
    btn = getattr(win, "_dataset_btn", None)
    if btn is None:
        return
    state = getattr(win, "_app_state", None)
    dataset = getattr(state, "dataset", None) if state is not None else None
    path = str(getattr(dataset, "data_dir", "") or "")
    if not path:
        btn.setText("No dataset — Open…")
        btn.setToolTip("No dataset loaded. Choose a folder to review.")
        return
    from pathlib import Path
    name = Path(path).name or path
    metrics = btn.fontMetrics()
    # Reserve the icon, the caret and the button's own padding before eliding.
    room = max(btn.width() - 64, 80)
    btn.setText(metrics.elidedText(name, Qt.ElideMiddle, room))
    btn.setToolTip(path)


class _RailTogglePlacement(QObject):
    """Re-park the floating rail toggle whenever the window changes size."""

    def __init__(self, win) -> None:
        super().__init__(win)
        self._win = win

    def eventFilter(self, obj, event) -> bool:  # Qt naming
        if event.type() in (QEvent.Resize, QEvent.Show):
            position_rail_toggle(self._win)
        return False


def _build_rail_toggle(win) -> None:
    """The rail's collapse toggle, floating on the rail's right margin.

    Parented to the **window**, not to the rail, and positioned by hand. That is
    the whole trick: a toggle inside the rail would be hidden along with it, so
    the control that collapses the rail would be the one control the collapse
    takes away. Floating it over the boundary keeps it reachable in both states
    — it simply slides to the window's left edge once the rail is gone.

    It straddles the divider rather than sitting inside the rail's content, so
    it never collides with the dataset head or the plate picker, and it reads as
    belonging to the edge it moves.
    """
    from widgets.icon_button import IconButton
    btn = IconButton("panel-right-close", win, tooltip="Hide sidebar",
                     checkable=True)
    btn.setObjectName("RailToggle")
    btn.setCursor(Qt.PointingHandCursor)
    btn.clicked.connect(win._toggle_sidebar)
    win._sidebar_toggle_btn = btn
    btn.raise_()
    return btn


def visible_left_panel(win):
    """The left panel currently on screen, whichever mode owns it.

    All three modes put a panel down the left edge and the one collapse toggle
    hides whichever is showing, so the toggle has to find the current one rather
    than assume Review's rail. Returns ``None`` when every panel is hidden.
    """
    candidates = [getattr(win, "_sidebar_root", None)]
    candidates += [getattr(getattr(win, attr, None), "_left_sidebar", None)
                   for attr in ("_analyze_pane", "_datasets_pane")]
    for panel in candidates:
        if panel is not None and panel.isVisible():
            return panel
    return None


def position_rail_toggle(win) -> None:
    """Park the collapse toggle on the left panel's right edge (or the window's,
    when every panel is hidden). Called on every resize and on every collapse."""
    btn = getattr(win, "_sidebar_toggle_btn", None)
    pane = getattr(win, "_h_pane", None)
    if btn is None or pane is None:
        return
    panel = visible_left_panel(win)
    # Watch whatever panel is current. A mode switch re-parks the toggle before
    # the incoming pane's splitter has laid out, so the width read here is the
    # outgoing one's; the panel's own resize is what corrects it. Re-installing
    # an already-installed filter only moves it to the front, so this is safe to
    # run on every reposition and saves tracking which panels exist yet.
    placement = getattr(win, "_rail_toggle_filter", None)
    if placement is not None and panel is not None:
        panel.installEventFilter(placement)
    # Mapped, not read off ``geometry()``: the button is an overlay on the
    # window while the panels sit at different depths of the tree, and Analyze's
    # and Datasets' are inside their own pane's splitter rather than the shell's.
    edge = (panel.mapTo(win, QPoint(panel.width(), 0)).x()
            if panel is not None else 0)
    # An overlay has no layout to size it, so it would otherwise keep Qt's
    # default 100x30 and paint its chrome around an off-centre glyph.
    btn.adjustSize()
    size = btn.size()
    # Vertically centred on whichever panel is showing — the toggle straddles
    # the panel's own right edge (see below), not any one row inside it, so a
    # user scanning down that edge finds it in the same predictable spot
    # regardless of mode or what the panel currently contains.
    if panel is not None:
        origin = panel.mapTo(win, QPoint(0, 0)).y()
        top = origin + max((panel.height() - size.height()) // 2, 0)
        # Review's sidebar carries an 8x12 well-picker plate near the top of
        # its content. True centre lands on it for common window heights,
        # turning a click-and-drag well selection into an accidental sidebar
        # collapse (reported: dragging across the plate's middle columns hit
        # the toggle instead). Nudge below the plate rather than covering it
        # whenever centring would otherwise land on it.
        plate = getattr(win, "_sidebar_plate", None)
        if plate is not None and plate.isVisible():
            plate_top = plate.mapTo(win, QPoint(0, 0)).y()
            plate_bottom = plate_top + plate.height()
            if top < plate_bottom and top + size.height() > plate_top:
                top = plate_bottom + theme.Spacing.sm
    else:
        top = pane.geometry().top() + max((pane.height() - size.height()) // 2, 0)
    # Straddle the rail's right boundary.
    btn.move(max(edge - size.width() // 2, 0), top)
    btn.raise_()
    btn.show()


def build_page_tabs_host(win, parent):
    """The band's right-hand slot: whichever page's tab bar is current.

    The page tab bars used to sit inside each tab's own body, one row below the
    mode band, so the top of the window was two stacked strips of chrome. They
    are the same *kind* of thing as the mode selector — one level down — so they
    belong beside it on one row, sharing its bottom edge.

    A ``QStackedWidget`` keyed by top-level page name. Page 0 is blank and is
    what the six pages with no sub-tabs (Statistics, Spatial, smFISH, Review
    CSV, Batch Export, Saved Plots) show; the band keeps its height there rather
    than collapsing, so switching pages never shifts the content below it.
    """
    from PySide6.QtWidgets import QStackedWidget
    host = QStackedWidget(parent)
    host.setObjectName("PageTabsHost")
    host.addWidget(QWidget(host))          # index 0 — the blank page
    win._page_tabs_host = host
    win._page_tabs_by_name = {}
    return host


def register_page_tabs(win, name: str, bar) -> None:
    """Mount *bar* as the band's tab strip for the top-level page *name*.

    The bar is bottom-aligned inside its page rather than filling it. A
    ``QTabBar`` sizes its tabs from the font and its padding, and lays them from
    the top of whatever height it is given — so stretched to the band's height it
    drew its selected-tab underline part-way up, eight pixels clear of the band's
    bottom edge while the mode bar's fill ran all the way down to it. Pinning the
    bar to its own height and pushing it to the bottom puts the underline on the
    band's bottom edge, where it lines up with the active mode segment beside it,
    at any font size or band height.
    """
    host = getattr(win, "_page_tabs_host", None)
    if host is None or bar is None:
        return
    page = QWidget(host)
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.setSpacing(0)
    page_layout.addStretch(1)
    page_layout.addWidget(bar, 0)
    host.addWidget(page)
    win._page_tabs_by_name[name] = page
    # Pages build lazily, so a bar can register *after* its page is already
    # current — show it now rather than waiting for the next page change.
    nb = getattr(win, "_notebook", None)
    if nb is not None and nb.currentName() == name:
        show_page_tabs(win, name)


def show_page_tabs(win, name: str) -> None:
    """Swap the band to *name*'s tab bar, or to the blank page if it has none.

    The strip belongs to Review's pages, and the band is shared by all three
    modes — so Analyze and Datasets show the blank page. Left carrying whatever
    Review was on, the row offered Bar Plots / Line Graphs / Heat Map beside the
    Analyze form, naming views that mode cannot show.

    Split View shows two top-level pages at once and detaches both from the
    notebook, so ``currentName()`` names neither. The band can only carry one
    strip, so it carries the **focused** pane's — the same pane the sidebar and
    the tab dispatch already follow. Without this the band would fall through to
    its blank page and both panes' sub-tabs would be unreachable while split.
    """
    host = getattr(win, "_page_tabs_host", None)
    if host is None:
        return
    if hasattr(win, "currentMode") and win.currentMode() != "review":
        host.setCurrentWidget(host.widget(0))
        return
    split = getattr(getattr(win, "_ctx", None), "runtime", None)
    split = getattr(split, "split", None)
    if split is not None and getattr(split, "active", False):
        name = getattr(split, "focused", None) or name
    bar = (getattr(win, "_page_tabs_by_name", None) or {}).get(name)
    host.setCurrentWidget(bar if bar is not None else host.widget(0))


def mirror_tab_widget(win, name: str, tab_widget):
    """Drive a ``QTabWidget`` from a ``QTabBar`` in the band, two ways.

    The two nested ``QTabWidget``s (Image Browser, Sample Definitions) are read
    through their own API by controllers, persistence and eight test modules —
    ``tabText`` / ``currentIndex`` / ``count`` / ``addTab`` / ``currentChanged``.
    Converting them to a bar plus a page stack would break every one of those.
    Hiding their own bar and mirroring it instead keeps the widget and its API
    exactly as they are, and costs one signal each way.
    """
    from PySide6.QtWidgets import QTabBar
    bar = QTabBar()
    # Carry the source widget's name so theme.qss's one nested-sub-tab rule
    # styles the mirror exactly as it styled the bar it replaces.
    bar.setObjectName(tab_widget.objectName() or "PlottingSubTabs")
    bar.setDrawBase(False)
    bar.setExpanding(False)
    bar.setElideMode(Qt.ElideNone)
    bar.setUsesScrollButtons(True)
    tab_widget.tabBar().hide()

    def _sync_bar_from_widget() -> None:
        """Rebuild the mirror when tabs are added, renamed or removed."""
        blocked = bar.blockSignals(True)
        try:
            while bar.count():
                bar.removeTab(0)
            for i in range(tab_widget.count()):
                bar.addTab(tab_widget.tabText(i))
            bar.setCurrentIndex(tab_widget.currentIndex())
        finally:
            bar.blockSignals(blocked)

    def _on_widget_changed(idx: int) -> None:
        if bar.currentIndex() != idx:
            blocked = bar.blockSignals(True)
            try:
                bar.setCurrentIndex(idx)
            finally:
                bar.blockSignals(blocked)

    bar.currentChanged.connect(tab_widget.setCurrentIndex)
    tab_widget.currentChanged.connect(_on_widget_changed)
    _sync_bar_from_widget()
    tab_widget._aw_sync_mirror = _sync_bar_from_widget  # type: ignore[attr-defined]
    register_page_tabs(win, name, bar)
    return bar


def build_ui(win) -> None:
    outer = QVBoxLayout(win)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    # The mode band. The app header's mode bar is inserted at index 0 and spans
    # the rail's width flush against the window edge, so it and the rail below
    # it read as one column rather than a chip row floating over one.
    #
    # It is pinned to the page tab bar's height and carries no vertical padding
    # of its own, because everything in this strip sits *above* the centre
    # pane's tab row and pushes it down. Padded to the collapse button's natural
    # height it cost 43px on every Review tab to show one icon; at the tab bar's
    # height the band lines up with the tab row beside it and costs 10px less.
    from widgets.mode_bar import tab_bar_height as _tab_bar_height
    top = QWidget()
    top.setFixedHeight(_tab_bar_height())
    top_layout = QHBoxLayout(top)
    top_layout.setContentsMargins(0, 0, theme.Spacing.md, 0)
    top_layout.setSpacing(theme.Spacing.sm)
    win._top_bar = top
    top_layout.addWidget(build_page_tabs_host(win, top), 1)
    win._dir_label = QLabel("No data loaded")
    win._dir_label.setObjectName("Muted")
    win._dir_label.hide()
    top_layout.addWidget(win._dir_label)
    # The rail's collapse toggle is not in this band — it floats on the rail's
    # own right margin (see ``_build_rail_toggle``).
    #
    # "Open…" is not here any more either. It moved into the rail's dataset control
    # (below), where the folder it opens is also *named* — the button and the
    # thing it acts on in one place, instead of a button at one end of the
    # window and a caption at the other.
    outer.addWidget(top)

    # 1px separator under the topbar. objectName "Separator" so it fills with
    # ``theme.Colors.border_subtle`` (matching the app-header separator) — a bare
    # QFrame.HLine would instead paint Qt's default light etched line, ignoring
    # the theme (it read too light on the grey theme's dark surfaces).
    win._top_sep = QFrame()
    win._top_sep.setObjectName("Separator")
    win._top_sep.setFixedHeight(1)
    outer.addWidget(win._top_sep)

    # ── Horizontal splitter: sidebar | plots ───────────────────────────
    win._h_pane = QSplitter(Qt.Horizontal)
    outer.addWidget(win._h_pane, 1)

    sidebar = QWidget()
    # The sidebar rail is a FIXED width — it must never change with the active
    # tab or its content (a wide row wraps/scales to fit instead of stretching
    # the rail). A single fixed value (min == max) keeps every tab's rail
    # identical.
    sidebar.setFixedWidth(RAIL_W)
    # A tab can stack more content than the rail is tall — the Cell Browser, for
    # instance, shows the well plate AND its filter frame together, which used to
    # overlap ("crushed sidebar"). Host the content in a VERTICAL scroll area so
    # the overflow scrolls instead of the widgets crushing into each other. The
    # horizontal axis never scrolls: at the fixed rail width, content must fit
    # (section headers word-wrap, the plate scales to the rail). ``sidebar_layout``
    # stays the content's layout, so every ``sidebar_layout.addWidget(...)`` below
    # is unchanged.
    _sidebar_scroll = QScrollArea(sidebar)
    _sidebar_scroll.setWidgetResizable(True)
    _sidebar_scroll.setFrameShape(QFrame.NoFrame)
    _sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    _sidebar_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    _sidebar_content = QWidget()
    sidebar_layout = QVBoxLayout(_sidebar_content)
    sidebar_layout.setContentsMargins(0, 0, 0, 0)
    sidebar_layout.setSpacing(0)
    _sidebar_scroll.setWidget(_sidebar_content)
    _sidebar_outer = QVBoxLayout(sidebar)
    _sidebar_outer.setContentsMargins(0, 0, 0, 0)
    _sidebar_outer.setSpacing(0)
    _sidebar_outer.addWidget(_sidebar_scroll)

    # Phase 10 (A1): vertical section nav above the per-section frames.
    # Replaces the horizontal QTabWidget tab bar in centre_view.py; the
    # QTabWidget itself stays as page host (tab bar hidden) so existing
    # callers using tabText / setCurrentIndex / currentChanged still work.
    import theme as _theme_v2
    from widgets.rail_nav import RailNav as _RailNav

    # Mode-seg moved to the titlebar (the dataset chip slot — redundant
    # with the OS window title). The sidebar no longer hosts a mode
    # toggle; section nav starts immediately below the rail's top edge.
    win._section_nav = _RailNav(sidebar)
    win._section_nav_keys = []
    win._section_nav_building = False
    # The rail head is the dataset control, not a "SECTION" caption. The rail's
    # items are self-evidently sections; what the window had nowhere was a
    # durable statement of *which dataset* is loaded — the folder name lived in
    # a hidden label and on the OS window title. This names it, and carries the
    # Open action that used to sit alone at the far end of the topbar.
    win._dataset_btn = _dataset_button(win, sidebar)
    sidebar_layout.addWidget(win._dataset_btn)
    sidebar_layout.addWidget(win._section_nav)
    sep_nav = QFrame()
    sep_nav.setObjectName("Separator")
    sep_nav.setFixedHeight(1)
    sep_nav.setStyleSheet(
        f"background-color: {_theme_v2.Colors.border_subtle};"
    )
    sidebar_layout.addWidget(sep_nav)

    win._sidebar_main_frame = win._ctx.widgets.register("_sidebar_main_frame", QWidget())
    QVBoxLayout(win._sidebar_main_frame).setContentsMargins(0, 0, 0, 0)
    sidebar_layout.addWidget(win._sidebar_main_frame, 1)

    win._sidebar_line_frame = win._sidebar_main_frame
    # Companion frames stacked in the sidebar and toggled by
    # _on_tab_change.  All are hidden initially; the tab handler shows
    # the relevant one.
    win._sidebar_preview_frame = win._ctx.widgets.register("_sidebar_preview_frame", QWidget())
    # The Image Table / Cell Browser sidebar frames are read back by the
    # ctx-native Image Browser sub-tab controller, so register them (the registry
    # stamps ``win._<name>`` too, keeping the win-native tab_controller /
    # registry visibility readers resolving unchanged).
    win._sidebar_image_table_frame = win._ctx.widgets.register(
        "_sidebar_image_table_frame", QWidget())
    win._sidebar_sample_frame = win._ctx.widgets.register("_sidebar_sample_frame", QWidget())
    win._sidebar_stats_frame = win._ctx.widgets.register("_sidebar_stats_frame", QWidget())
    win._sidebar_cell_browser_frame = win._ctx.widgets.register(
        "_sidebar_cell_browser_frame", QWidget())
    for w in (win._sidebar_preview_frame, win._sidebar_image_table_frame,
              win._sidebar_sample_frame,
              win._sidebar_stats_frame):
        QVBoxLayout(w).setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(w, 1)
        w.hide()
    # Cell Browser is the one sub-tab that shows ALONGSIDE the main well-plate
    # frame (it has no plate of its own — it filters cells by criteria). Give it
    # stretch 0 so the plate (stretch 1) owns the vertical slack and the Cell
    # filters sit compact beneath it, instead of the rail splitting 50/50.
    QVBoxLayout(win._sidebar_cell_browser_frame).setContentsMargins(0, 0, 0, 0)
    sidebar_layout.addWidget(win._sidebar_cell_browser_frame, 0)
    win._sidebar_cell_browser_frame.hide()

    win._h_pane.addWidget(sidebar)
    win._sidebar_root = sidebar  # so mode toggles can hide/show the whole rail
    win._sidebar_collapsed = False  # user-driven collapse state (Ctrl+B / rail btn)
    _build_rail_toggle(win)
    # The toggle is positioned by hand over the splitter, so it has to be
    # re-parked whenever the window resizes. A filter rather than a
    # ``resizeEvent`` override: ``win`` is the app's own QWidget subclass and
    # this module builds into it rather than subclassing it.
    win._rail_toggle_filter = _RailTogglePlacement(win)
    win.installEventFilter(win._rail_toggle_filter)
    build_sidebar(win, win._sidebar_main_frame)

    # Centre stack — wraps the existing plots widget so allwell can
    # mount the Analyze pane as a peer page. The mode-seg in the sidebar
    # (built below) drives the stack, keeping the sidebar visible in
    # both modes (mockup parity: mode-seg sits at the top of the left
    # rail and the user can always switch back from Analyze).
    from PySide6.QtWidgets import QStackedWidget as _QStackedWidget
    win._central_pane_stack = _QStackedWidget()
    centre = QWidget()
    QVBoxLayout(centre).setContentsMargins(0, 0, 0, 0)
    win._central_pane_stack.addWidget(centre)
    win._central_pane_stack.currentChanged.connect(win._on_central_pane_changed)
    win._h_pane.addWidget(win._central_pane_stack)
    win._h_pane.setStretchFactor(0, 0)
    win._h_pane.setStretchFactor(1, 3)
    win._h_pane.setChildrenCollapsible(False)
    win._h_pane.setSizes([340, 1200])
    win._build_centre(centre)

    # ── Split View ────────────────────────────────────────────────────────
    # Self-contained feature: this single call wires up the side-by-side
    # double-pane view (toggle button + dropdown + reparenting). Comment it out
    # to remove the feature entirely — the win falls back to single-tab with no
    # other changes needed.
    from well_viewer.core.split_view import install_split_view
    install_split_view(win._ctx)

    # Phase 11 (A6 / B23): Properties rail as an OVERLAY on the centre
    # column. The rail floats over the right edge of the centre pane;
    # it does NOT push the canvas smaller (mockup parity) and it does
    # NOT extend across the sidebar (user reported it was spanning the
    # whole window). Mounted here — inside WellViewerApp — so toggling
    # Properties rail retired — figure styling now lives in the
    # per-plot Export Style sidebar (opened from each plot card's
    # sliders IconButton). Nothing to install here.

    # Status + log — packed last so it sits below the splitter.
    build_bottom(win)
