"""Top-level window assembly for :class:`WellViewerApp`.

``build_ui`` lays out the application shell — topbar, the sidebar│centre
splitter, and the status bar — delegating each region to its own builder
(``_build_sidebar`` / ``_build_centre`` / ``_build_bottom``). Relocated
from ``runtime_app.py`` (teardown Phase 8).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
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


#: The left rail's width. Fixed, and the one place it is stated: the app
#: header's mode bar spans exactly this so the two share a left column.
RAIL_W = 340


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


def build_ui(win) -> None:
    outer = QVBoxLayout(win)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    # Topbar. No left inset: the app header's mode bar is inserted at index 0
    # and must sit flush against the window edge and span the rail's full width,
    # so the bar and the rail below it read as one column rather than a chip row
    # floating over it. Everything after the bar carries its own leading pad.
    top = QWidget()
    top_layout = QHBoxLayout(top)
    top_layout.setContentsMargins(0, 8, 14, 8)
    top_layout.setSpacing(theme.Spacing.sm)
    win._top_bar = top
    _lead = QWidget(top)
    _lead.setFixedWidth(theme.Spacing.md)
    top_layout.addWidget(_lead)
    # Left-rail collapse toggle. Checked == the rail is hidden; the glyph
    # flips between panel-open / panel-close.
    from widgets.icon_button import IconButton as _IconButton
    win._sidebar_toggle_btn = _IconButton(
        "panel-right-close", tooltip="Hide sidebar", checkable=True)
    win._sidebar_toggle_btn.clicked.connect(win._toggle_sidebar)
    top_layout.addWidget(win._sidebar_toggle_btn)
    win._dir_label = QLabel("No data loaded")
    win._dir_label.setObjectName("Muted")
    top_layout.addWidget(win._dir_label)
    top_layout.addStretch(1)
    # "Open…" is not here any more. It moved into the rail's dataset control
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
    win._sidebar_collapsed = False  # user-driven collapse state (Ctrl+B / top-bar btn)
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
