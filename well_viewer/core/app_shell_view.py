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
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from well_viewer.core.sidebar_view import build_sidebar
from well_viewer.core.status_view import build_bottom


def build_ui(app) -> None:
    outer = QVBoxLayout(app)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    # Topbar
    top = QWidget()
    top_layout = QHBoxLayout(top)
    top_layout.setContentsMargins(14, 8, 14, 8)
    app._top_bar = top
    # Left-rail collapse toggle. Checked == the rail is hidden; the glyph
    # flips between panel-open / panel-close.
    from widgets.icon_button import IconButton as _IconButton
    app._sidebar_toggle_btn = _IconButton(
        "panel-right-close", tooltip="Hide sidebar", checkable=True)
    app._sidebar_toggle_btn.clicked.connect(app._toggle_sidebar)
    top_layout.addWidget(app._sidebar_toggle_btn)
    app._dir_label = QLabel("No data loaded")
    app._dir_label.setObjectName("Muted")
    top_layout.addWidget(app._dir_label)
    top_layout.addStretch(1)
    open_btn = QPushButton("Open…")
    open_btn.setProperty("variant", "primary")
    open_btn.clicked.connect(app._browse)
    top_layout.addWidget(open_btn)
    outer.addWidget(top)

    app._top_sep = QFrame()
    app._top_sep.setFrameShape(QFrame.HLine)
    outer.addWidget(app._top_sep)

    # ── Horizontal splitter: sidebar | plots ───────────────────────────
    app._h_pane = QSplitter(Qt.Horizontal)
    outer.addWidget(app._h_pane, 1)

    sidebar = QWidget()
    # Review sidebar width reduced 25% (400→300 min, 600→450 max) per
    # redesign-bug-batch-2: the plate map needs less breathing room than
    # the original 400-px reservation; freed pixels go to the canvas.
    sidebar.setMinimumWidth(300)
    sidebar.setMaximumWidth(450)
    sidebar_layout = QVBoxLayout(sidebar)
    sidebar_layout.setContentsMargins(0, 0, 0, 0)
    sidebar_layout.setSpacing(0)

    # Phase 10 (A1): vertical section nav above the per-section frames.
    # Replaces the horizontal QTabWidget tab bar in centre_view.py; the
    # QTabWidget itself stays as page host (tab bar hidden) so existing
    # callers using tabText / setCurrentIndex / currentChanged still work.
    import theme as _theme_v2
    from widgets.rail_nav import RailNav as _RailNav

    # Mode-seg moved to the titlebar (the dataset chip slot — redundant
    # with the OS window title). The sidebar no longer hosts a mode
    # toggle; section nav starts immediately below the rail's top edge.
    app._section_nav = _RailNav(sidebar)
    app._section_nav_keys = []
    app._section_nav_building = False
    nav_head = QLabel("SECTION", sidebar)
    nav_head.setObjectName("RailHead")
    nav_head.setContentsMargins(13, 10, 13, 4)
    nav_head.setStyleSheet(
        f"color: {_theme_v2.Colors.text_muted}; "
        f"font-family: {_theme_v2.Typography.family}; "
        f"font-size: 11px; "
        f"font-weight: 600; "
        f"letter-spacing: 0.08em;"
    )
    sidebar_layout.addWidget(nav_head)
    sidebar_layout.addWidget(app._section_nav)
    sep_nav = QFrame()
    sep_nav.setObjectName("Separator")
    sep_nav.setFixedHeight(1)
    sep_nav.setStyleSheet(
        f"background-color: {_theme_v2.Colors.border_subtle};"
    )
    sidebar_layout.addWidget(sep_nav)

    app._sidebar_main_frame = QWidget()
    QVBoxLayout(app._sidebar_main_frame).setContentsMargins(0, 0, 0, 0)
    sidebar_layout.addWidget(app._sidebar_main_frame, 1)

    app._sidebar_line_frame = app._sidebar_main_frame
    # Companion frames stacked in the sidebar and toggled by
    # _on_tab_change.  All are hidden initially; the tab handler shows
    # the relevant one.
    app._sidebar_preview_frame = QWidget()
    app._sidebar_image_table_frame = QWidget()
    app._sidebar_sample_frame = QWidget()
    app._sidebar_stats_frame = QWidget()
    app._sidebar_cell_browser_frame = QWidget()
    for w in (app._sidebar_preview_frame, app._sidebar_image_table_frame,
              app._sidebar_sample_frame,
              app._sidebar_stats_frame,
              app._sidebar_cell_browser_frame):
        QVBoxLayout(w).setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(w, 1)
        w.hide()

    app._h_pane.addWidget(sidebar)
    app._sidebar_root = sidebar  # so mode toggles can hide/show the whole rail
    app._sidebar_collapsed = False  # user-driven collapse state (Ctrl+B / top-bar btn)
    build_sidebar(app, app._sidebar_main_frame)

    # Centre stack — wraps the existing plots widget so allwell can
    # mount the Analyze pane as a peer page. The mode-seg in the sidebar
    # (built below) drives the stack, keeping the sidebar visible in
    # both modes (mockup parity: mode-seg sits at the top of the left
    # rail and the user can always switch back from Analyze).
    from PySide6.QtWidgets import QStackedWidget as _QStackedWidget
    app._central_pane_stack = _QStackedWidget()
    centre = QWidget()
    QVBoxLayout(centre).setContentsMargins(0, 0, 0, 0)
    app._central_pane_stack.addWidget(centre)
    app._central_pane_stack.currentChanged.connect(app._on_central_pane_changed)
    app._h_pane.addWidget(app._central_pane_stack)
    app._h_pane.setStretchFactor(0, 0)
    app._h_pane.setStretchFactor(1, 3)
    app._h_pane.setChildrenCollapsible(False)
    app._h_pane.setSizes([340, 1200])
    app._build_centre(centre)

    # Phase 11 (A6 / B23): Properties rail as an OVERLAY on the centre
    # column. The rail floats over the right edge of the centre pane;
    # it does NOT push the canvas smaller (mockup parity) and it does
    # NOT extend across the sidebar (user reported it was spanning the
    # whole window). Mounted here — inside WellViewerApp — so toggling
    # Properties rail retired — figure styling now lives in the
    # per-plot Export Style sidebar (opened from each plot card's
    # sliders IconButton). Nothing to install here.

    # Status + log — packed last so it sits below the splitter.
    build_bottom(app)
