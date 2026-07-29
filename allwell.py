"""
allwell.py
-----------
Composition root for the All-Well application (PySide6).

Tabs:
  * Review  - WellViewerApp (from well_viewer package runtime)
  * Analyze - AnalyzeTab (from analyze_tab.py)

Run:
    python allwell.py [--data_dir /path/to/results]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Startup diagnostics FIRST — before PySide6 — so a silent launch failure (a
# native crash in the Qt DLL import, a Qt platform-plugin abort, or a cold-cache
# matplotlib font-scan hang) leaves a trail on disk instead of an empty terminal.
# See boot_diag: it truncates a per-launch log and arms faulthandler onto it.
import boot_diag as _boot_diag
_boot_diag.arm_faulthandler()
_boot_diag.install_excepthook()
_boot_diag.boot(f"start pid={__import__('os').getpid()} python={sys.version.split()[0]} platform={sys.platform}")

_boot_diag.boot("importing PySide6 (Qt DLLs) …")
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QTextBrowser, QVBoxLayout, QWidget,
)
_boot_diag.boot("PySide6 imported OK")

import theme as theme_v2
from version import __version__ as _APP_VERSION
from widgets.brand_tile import BrandTile
from widgets.icon_button import IconButton
from well_viewer.core.safe import swallowed

# Global tab-scoped debug toggles.
REVIEW_TAB_DEBUG = False
ANALYZE_TAB_DEBUG = False
REVIEW_BAR_DEBUG = False
REVIEW_SCATTER_DEBUG = False


def _stop_analyze_runner(analyze) -> None:
    """Stop an in-flight pipeline run on the Analyze pane at app close.

    ``AnalyzeTab`` is mounted as a *child* widget, so in Qt its own
    ``closeEvent`` never fires — quitting mid-run therefore orphaned the local
    pipeline subprocess (and its process group) and, for a remote run, killed
    the worker thread before its cleanup ``finally`` could ``scancel`` the SLURM
    job / remove the remote staging + local scratch (audit L1). The app root's
    ``closeEvent`` calls this so the runner is stopped from a live top-level
    window instead.
    """
    if analyze is None:
        return
    # Stop the self-rescheduling log poller re-arming its QTimer (audit L2).
    try:
        analyze._alive = False
    except Exception:
        swallowed()
    runner = getattr(analyze, "_runner", None)
    if runner is None:
        return
    try:
        if getattr(runner, "is_running", False):
            runner.stop()
    except Exception:
        swallowed()


def _gh_slug(text: str) -> str:
    """GitHub-style heading slug — matches the anchors the README's TOC links to.

    Lower-case, drop everything except word chars / spaces / hyphens (so an
    em-dash in a heading collapses to the double hyphen GitHub produces), then
    spaces → hyphens.
    """
    import html as _htmllib
    import re
    s = _htmllib.unescape(text).strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return s.replace(" ", "-")


def _render_manual_html(md_text: str) -> str:
    """Render the README markdown to HTML with GitHub-slug heading anchors.

    Qt's ``QTextBrowser.setMarkdown`` keeps the table-of-contents' ``#slug`` link
    *targets* but emits no matching heading anchors, so every TOC / cross-ref link
    lands nowhere (the reported "links don't work"). Round-trip through a
    QTextDocument and inject an ``<a name="slug">`` at each heading so anchor
    navigation resolves.
    """
    import re
    from PySide6.QtGui import QTextDocument
    doc = QTextDocument()
    doc.setMarkdown(md_text)
    html_text = doc.toHtml()

    def _inject(m):
        heading_text = re.sub(r"<[^>]+>", "", m.group(2))
        return f'{m.group(1)}<a name="{_gh_slug(heading_text)}"></a>{m.group(2)}{m.group(3)}'

    return re.sub(r"(<h[1-6][^>]*>)(.*?)(</h[1-6]>)", _inject, html_text, flags=re.S)


#: ``## <title>`` sections dropped from the *in-app* help manual — a running app
#: is already installed, so install/build instructions are noise in the drawer;
#: "Where to read next" points at repo docs that aren't shipped to the drawer.
_HELP_DROP_H2 = ("Install", "Building the macOS bundle", "Where to read next")
#: Contents-index link texts that belong to those dropped sections.
_HELP_DROP_TOC = set(_HELP_DROP_H2) | {
    "macOS pre-built app", "Running from source", "Windows (from source)"}


def _strip_install_help(md_text: str) -> str:
    """Remove the installation / build sections (and their Contents entries) from
    the in-app help manual. If the app launched it's already installed, so those
    instructions don't belong in the running app's help drawer."""
    import re
    h1_2 = re.compile(r"^#{1,2} ")
    h2 = re.compile(r"^## (.+?)\s*$")
    toc_head = re.compile(r"^## Contents\s*$", re.IGNORECASE)
    link = re.compile(r"\[([^\]]+)\]\(#[^)]*\)")
    lines = md_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    in_contents = False
    while i < len(lines):
        line = lines[i]
        if toc_head.match(line):
            in_contents = True
        elif in_contents and h2.match(line):
            in_contents = False
        m = h2.match(line)
        if m and m.group(1).strip() in _HELP_DROP_H2:
            i += 1
            while i < len(lines) and not h1_2.match(lines[i]):
                i += 1
            continue
        # Drop Contents entries that link only to the dropped sections.
        if in_contents:
            texts = [t.strip() for t in link.findall(line)]
            if texts and all(t in _HELP_DROP_TOC for t in texts):
                i += 1
                continue
        out.append(line)
        i += 1
    return "".join(out)


class AllWellApp(QMainWindow):
    """Root window containing the Review and Analyze notebook tabs."""

    def __init__(self, data_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("All-Well")
        self.resize(1640, 980)
        self.setMinimumSize(1100, 800)

        self._review: QWidget | None = None
        self._analyze: QWidget | None = None

        self._build_ui()
        self._install_shortcuts()
        self._apply_stylesheet()
        self._restore_window_state()
        # Defer the icon render — it builds 8 QPainter pixmaps and that
        # work was blocking first paint. Queue it after the event loop
        # picks up; the window briefly shows the default Qt icon, which
        # is fine.
        QTimer.singleShot(0, self._install_app_icon)

        if data_path is not None and self._review is not None:
            QTimer.singleShot(150, lambda: self._review._load_path(data_path))

    # ── UI construction ──────────────────────────────────────────────────
    def _apply_stylesheet(self) -> None:
        # Single source of truth for the app stylesheet — see theme.py. The
        # active theme's tokens are already applied by ``main`` (via
        # ``theme.select_theme_from_env``) before this runs, so ``qss()`` here
        # reflects the selected theme (ALLWELL_THEME); default is blue.
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme_v2.qss())

    def _build_ui(self) -> None:
        from analyze_tab import AnalyzeTab
        from datasets_tab import DatasetsTab
        from well_viewer import WellViewerApp

        root = QWidget(objectName="AppRoot")
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.setCentralWidget(root)

        # Header bar — v2: BrandTile + wordmark + dataset chip + status dot +
        # action IconButtons (Open / Help). Per DECISIONS_NEEDED #4 we keep
        # the native frame, so this is the app-shell strip.
        header = QWidget(objectName="Sidebar")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 7, 14, 7)
        hl.setSpacing(theme_v2.Spacing.sm)
        hl.addWidget(BrandTile(side=24))
        title = QLabel("All-Well")
        title.setObjectName("Title")
        hl.addWidget(title)

        # Phase 10 (B2): version pill next to wordmark.
        self._version_pill = QLabel(f"v{_APP_VERSION}")
        self._version_pill.setObjectName("VersionPill")
        # Brand pill. Reads the theme-swappable ``brand`` / ``brand_fg`` tokens:
        # the default (blue) theme is a transparent pill with white text; the
        # grey theme keeps it transparent with yellow text.
        self._version_pill.setStyleSheet(
            f"background-color: {theme_v2.Colors.brand}; "
            f"color: {theme_v2.Colors.brand_fg}; "
            f"font-family: {theme_v2.Typography.family_mono}; "
            f"font-size: {theme_v2.Typography.caption_size}px; "
            f"font-weight: 600; padding: 1px 6px; margin-left: 4px; "
            f"border-radius: {theme_v2.Radii.xs}px;"
        )
        hl.addWidget(self._version_pill)

        hl.addSpacing(theme_v2.Spacing.md)
        # Mode segmented control (Review / Analyze) lives in the titlebar
        # strip where the redundant dataset chip used to sit. The dataset
        # name is set on the OS window title instead.
        from widgets.segmented_control import SegmentedControl as _SegmentedControl
        from widgets import icons as _icons
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        _eye_icon = _icons.make_icon("eye", 14, dpr=dpr or 1.0)
        _slider_icon = _icons.make_icon("sliders-horizontal", 14, dpr=dpr or 1.0)
        _boxes_icon = _icons.make_icon("boxes", 14, dpr=dpr or 1.0)
        self._mode_seg = _SegmentedControl()
        self._mode_seg.addSegment("Review", icon=_eye_icon, data="review")
        self._mode_seg.addSegment("Analyze", icon=_slider_icon, data="analyze")
        self._mode_seg.addSegment("Datasets", icon=_boxes_icon, data="datasets")
        self._mode_seg.currentChanged.connect(self._on_titlebar_mode_seg)
        self._mode_seg.setFixedWidth(330)
        hl.addWidget(self._mode_seg)

        hl.addStretch(1)

        # Phase 10 (B3): refresh action.
        self._refresh_btn = IconButton("refresh-cw")
        self._refresh_btn.setToolTip("Reload the active dataset")
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        hl.addWidget(self._refresh_btn)

        # Global "presentation mode" toggle: flips every PlotCard in the app
        # between Screen (dark live-preview) and Publication (canonical
        # white-bg export). Per-card toggles still work; this is the master.
        # v2 default: Screen mode (dark live-preview). The Presentation
        # toggle still flips into Publication (canonical white-bg export).
        self._present_mode = "screen"
        self._present_btn = IconButton("image")
        self._present_btn.setCheckable(True)
        # Presentation mode is opt-in: unchecked (inactive) is the default Screen
        # (dark) look; checking it flips all plots to the Publication export look.
        self._present_btn.setChecked(False)
        self._present_btn.setToolTip("Presentation mode: toggle all plots screen ↔ publication")
        self._present_btn.toggled.connect(self._on_present_toggled)
        hl.addWidget(self._present_btn)

        # Reset ALL persisted state for the dataset (more aggressive than the
        # rotate-ccw "reset view"): deletes persistence.json and reloads, for
        # when saved state gets into a bad state. Confirmed; data left intact.
        self._reset_saves_btn = IconButton("trash-2")
        self._reset_saves_btn.setToolTip(
            "Reset all saved state for this dataset (selections, LUTs, crops, "
            "plot groups, heatmap layouts, cell overrides, ratios, QC params) "
            "and reload. Your data files and sample labels are left intact."
        )
        self._reset_saves_btn.clicked.connect(self._on_reset_saves_clicked)
        hl.addWidget(self._reset_saves_btn)

        self._open_btn = IconButton("home")
        self._open_btn.setToolTip(
            "Reveal the current dataset's folder in your file manager "
            "(use ⌘O to load a different dataset)."
        )
        self._open_btn.clicked.connect(self._reveal_dataset_in_file_manager)
        hl.addWidget(self._open_btn)

        # Reset selections / view state. Asks before clearing — the action
        # wipes wells, fold-change scopes, and per-tab combo state but
        # leaves the dataset itself intact. ``rotate-ccw`` reads as "undo
        # / reset"; the plain ``x`` icon previously sitting here was too
        # close to a window-close glyph.
        self._reset_view_btn = IconButton("rotate-ccw")
        self._reset_view_btn.setToolTip(
            "Reset selections and view (clears wells, fold-change, and "
            "per-tab combos)"
        )
        self._reset_view_btn.clicked.connect(self._on_reset_view_clicked)
        hl.addWidget(self._reset_view_btn)

        self._help_btn = IconButton("info")
        self._help_btn.setToolTip("Open the help drawer")
        self._help_btn.clicked.connect(self._toggle_help_drawer)
        hl.addWidget(self._help_btn)
        self._help_drawer = None  # built lazily on first open
        self._help_content = None
        self._help_manual_view = None  # None in trimmed builds without the manual

        rl.addWidget(header)

        sep = QFrame()
        sep.setObjectName("Separator")
        sep.setFixedHeight(1)
        rl.addWidget(sep)

        # Phase 11: the outer Review/Analyze QStackedWidget is gone — Analyze
        # now lives inside WellViewerApp's central pane stack so that the
        # left rail (mode-seg + section nav + plate + saved) stays visible
        # in both modes. The mode-seg itself moved into WellViewerApp's
        # sidebar above the SECTION header.
        self._central_host = QWidget()
        self._central_host.setObjectName("CentralHost")
        ch_layout = QVBoxLayout(self._central_host)
        ch_layout.setContentsMargins(0, 0, 0, 0)
        ch_layout.setSpacing(0)
        rl.addWidget(self._central_host, 1)

        self._review = WellViewerApp(parent=None)
        ch_layout.addWidget(self._review, 1)
        # Back-compat for code paths that read self._nb (legacy after the
        # outer-stack retirement) — point it at WellViewerApp's central
        # pane stack so e.g. ``self._nb.setCurrentIndex(0)`` still means
        # "show Review".
        self._nb = self._review._central_pane_stack
        self._nb.currentChanged.connect(self._on_tab_change)
        self._review.modeChanged.connect(self._on_review_mode_changed)
        self._wrap_review_load_path()

        # The directory-name label that used to live in WellViewerApp's
        # topbar is hidden; its slot now hosts the Review / Analyze
        # SegmentedControl (re-parented out of the outer header).
        try:
            self._review._dir_label.hide()
        except Exception:
            swallowed()  # was: pass (silent except)
        review_topbar = getattr(self._review, "_top_bar", None)
        if review_topbar is not None and review_topbar.layout() is not None:
            review_topbar.layout().insertWidget(0, self._mode_seg)

        self._analyze = AnalyzeTab(
            parent=None,
            on_pipeline_complete=self._on_analyze_pipeline_complete,
        )
        self._review.mountAnalyzePane(self._analyze)

        # Datasets pane — third top-level mode. Its own left rail lists the
        # dataset-level features; it has no well picker. A completed
        # concatenation hands its output to Review via _open_dataset_in_review;
        # the OME-TIFF converter hands its produced input folder to Analyze via
        # _open_dataset_in_analyze.
        self._datasets = DatasetsTab(
            parent=None,
            on_open_in_review=self._open_dataset_in_review,
            on_open_in_analyze=self._open_dataset_in_analyze,
        )
        self._review.mountDatasetsPane(self._datasets)

        # Status bar v2: status / kbd hints / Log tray IconButton.
        from widgets.kbd_hint import KbdHint as _KbdHint
        statusbar = QWidget(objectName="StatusBar")
        sb_layout = QHBoxLayout(statusbar)
        sb_layout.setContentsMargins(theme_v2.Spacing.md, 4,
                                     theme_v2.Spacing.md, 4)
        sb_layout.setSpacing(theme_v2.Spacing.sm)
        # The embedded Review widget builds its own status strip
        # (status_view.build_bottom) with a "Ready." label + progress bar. That
        # duplicates this shell's bar — two stacked "Ready." lines. Adopt the
        # Review's live label + progress bar into this single bottom bar and hide
        # the inner strip, so status/progress show here and appear only once.
        self._status_lbl_app = getattr(self._review, "_status_lbl", None) \
            or QLabel("Ready.")
        self._status_lbl_app.setStyleSheet(
            f"color: {theme_v2.Colors.text_secondary}; "
            f"font-size: {theme_v2.Typography.caption_size}px;"
        )
        sb_layout.addWidget(self._status_lbl_app)
        sb_layout.addStretch(1)
        _review_progress = getattr(self._review, "_progress_bar", None)
        if _review_progress is not None:
            sb_layout.addWidget(_review_progress)
        _inner_footer = getattr(self._review, "_status_footer", None)
        if _inner_footer is not None:
            _inner_footer.hide()
        # Kbd hints — each one is a real shortcut wired in _install_shortcuts.
        # ("Search ⌘K" used to sit here but no global search exists; replaced
        #  with the help-drawer toggle, which is a real binding.)
        for label, hint in (("Open", "⌘O"), ("Help", "⌘/"), ("Export", "⌘E")):
            l = QLabel(label)
            l.setStyleSheet(
                f"color: {theme_v2.Colors.text_muted}; "
                f"font-size: {theme_v2.Typography.caption_size}px;"
            )
            sb_layout.addWidget(l)
            sb_layout.addWidget(_KbdHint(hint))
            sb_layout.addSpacing(theme_v2.Spacing.xs)
        # Application-log tray button (far-right of the status bar). Opens the
        # shell log drawer (_toggle_log_drawer); also bound to ⌘/Ctrl+L. Mirrors
        # the title-bar help-drawer button (self._help_btn).
        self._log_btn = IconButton("terminal-square")
        self._log_btn.setToolTip("Open the application log (⌘L)")
        self._log_btn.clicked.connect(self._toggle_log_drawer)
        sb_layout.addWidget(self._log_btn)
        statusbar.setStyleSheet(
            f"#StatusBar {{ background-color: {theme_v2.Colors.titlebar}; "
            f"border-top: 1px solid {theme_v2.Colors.border_subtle}; }}"
        )
        rl.addWidget(statusbar)
        self._log_drawer = None  # lazy
        self._log_ring: list[str] = []
        # Capture logs from startup (not lazily on first drawer-open) so the
        # drawer shows history the moment it opens — and onto the right logger.
        self._attach_log_ring_buffer()

    def _install_shortcuts(self) -> None:
        """Global ⌘/Ctrl shortcuts the statusbar + help drawer advertise.

        - ⌘O / Ctrl+O — open dataset (matches the titlebar Open button).
        - ⌘E / Ctrl+E — export figure (drives the active plot card's
          ``save_figure`` toolbar action).
        - ⌘← / Ctrl+← — back through the section tab history.
        - ⌘→ / Ctrl+→ — forward through the section tab history.
        - ⌘/ / Ctrl+/ — toggle the help drawer (matches the header ? button).
        - ⌘L / Ctrl+L — toggle the application log drawer (matches the
          status-bar log button).
        - ⌘B / Ctrl+B — collapse / expand the left sidebar (matches the
          top-bar panel button; drives whichever sidebar belongs to the
          active mode).
        - ⌘W / Ctrl+W — close the window.

        Every shortcut here is surfaced somewhere in the chrome (statusbar
        key-hints or the help drawer's Keyboard-shortcuts list); keep the two
        in sync so the UI never advertises a binding that isn't wired.
        """
        from PySide6.QtGui import QKeySequence, QShortcut

        QShortcut(QKeySequence("Ctrl+O"), self, activated=self._open_dataset)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self._export_active_figure)
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=self._tab_history_back)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=self._tab_history_forward)
        QShortcut(QKeySequence("Ctrl+/"), self, activated=self._toggle_help_drawer)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._toggle_log_drawer)
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self._toggle_sidebar)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self.close)

    def _toggle_sidebar(self) -> None:
        """Collapse / expand the left sidebar (⌘B), same path as the top-bar button.

        Routed through the Review pane's proxy because
        ``tab_controller.toggle_sidebar`` already drives whichever sidebar
        belongs to the active mode — the Review rail in Review, the Analyze
        tab's form sidebar in Analyze — so the shortcut works in both.
        """
        review = getattr(self, "_review", None)
        if review is not None and hasattr(review, "_toggle_sidebar"):
            review._toggle_sidebar()

    def _tab_history_back(self) -> None:
        review = getattr(self, "_review", None)
        if review is not None and hasattr(review, "_tab_history_back"):
            review._tab_history_back()

    def _tab_history_forward(self) -> None:
        review = getattr(self, "_review", None)
        if review is not None and hasattr(review, "_tab_history_forward"):
            review._tab_history_forward()

    def _export_active_figure(self) -> None:
        review = getattr(self, "_review", None)
        if review is None:
            return
        for attr in ("_line_card", "_bar_card", "_scatter_card",
                     "_scatter_agg_card", "_distribution_card", "_heatmap_card"):
            card = getattr(review, attr, None)
            if card is None or not card.isVisible():
                continue
            nav = getattr(card, "_nav", None)
            if nav is not None and hasattr(nav, "save_figure"):
                try:
                    nav.save_figure()
                except Exception:
                    swallowed()  # was: pass (silent except)
            return

    def _install_app_icon(self) -> None:
        """96-well plate icon whose lit wells spell A W across the fluorescence spectrum."""
        # Dark-field fluorescence aesthetic: dark plate body so glowing wells pop.
        C_BG_TOP    = QColor("#0c1728")
        C_BG_BOT    = QColor("#050c18")
        C_BG_STROKE = QColor("#1c2e50")
        C_PLATE     = QColor("#16223a")   # dark slate plate body
        C_PLATE_EDGE = QColor("#253550")
        C_NOTCH     = QColor("#0f1a2c")
        C_EMPTY     = QColor("#0a1220")   # near-black empty wells
        # Vivid, saturated fluorescence colours for lit wells.
        C_BLUE   = QColor("#4d9cff")
        C_CYAN   = QColor("#00e0e0")
        C_GREEN  = QColor("#3de870")
        C_YELLOW = QColor("#ffe033")
        C_RED    = QColor("#ff4444")
        C_WHITE  = QColor("#c8e4ff")   # cool blue-white crossbar

        # 1-indexed (row, col). Same design as _Docs/icons/icon_1_plate_grid.svg.
        pattern = {
            (1, 3): C_BLUE,   (1, 7): C_BLUE,    (1, 12): C_BLUE,
            (2, 2): C_BLUE,   (2, 4): C_BLUE,    (2, 7): C_BLUE,   (2, 12): C_BLUE,
            (3, 2): C_CYAN,   (3, 4): C_CYAN,    (3, 7): C_CYAN,   (3, 12): C_CYAN,
            (4, 1): C_GREEN,  (4, 5): C_GREEN,   (4, 7): C_GREEN,  (4, 12): C_GREEN,
            (5, 1): C_GREEN,  (5, 2): C_GREEN,   (5, 3): C_WHITE,
            (5, 4): C_GREEN,  (5, 5): C_GREEN,   (5, 7): C_GREEN,  (5, 12): C_GREEN,
            (6, 1): C_YELLOW, (6, 5): C_YELLOW,  (6, 7): C_YELLOW,
            (6, 9): C_YELLOW, (6, 10): C_YELLOW, (6, 12): C_YELLOW,
            (7, 1): C_RED,    (7, 5): C_RED,     (7, 7): C_RED,
            (7, 8): C_RED,    (7, 11): C_RED,    (7, 12): C_RED,
            (8, 1): C_RED,    (8, 5): C_RED,     (8, 8): C_RED,    (8, 11): C_RED,
        }

        def _render(size: int) -> QPixmap:
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing, True)

            s = size / 1024.0  # authored on a 1024×1024 grid

            # Rounded squircle background.
            bg_rect = QRectF(72 * s, 72 * s, 880 * s, 880 * s)
            bg_grad = QRadialGradient(bg_rect.center(), bg_rect.height() * 0.65)
            bg_grad.setColorAt(0.0, C_BG_TOP)
            bg_grad.setColorAt(1.0, C_BG_BOT)
            p.setBrush(QBrush(bg_grad))
            p.setPen(QPen(C_BG_STROKE, max(1.0, 3 * s)))
            p.drawRoundedRect(bg_rect, 200 * s, 200 * s)

            # Dark plate body with notched corner.
            plate_rect = QRectF(140 * s, 220 * s, 744 * s, 584 * s)
            p.setBrush(QBrush(C_PLATE))
            p.setPen(QPen(C_PLATE_EDGE, max(1.0, 4 * s)))
            p.drawRoundedRect(plate_rect, 36 * s, 36 * s)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(C_NOTCH))
            p.drawRect(QRectF(140 * s, 220 * s, 100 * s, 36 * s))

            # 96 wells — 8 rows × 12 cols.
            xs = [200 + i * 56 for i in range(12)]
            ys = [276 + i * 64 for i in range(8)]
            r_well = 22 * s
            for row in range(1, 9):
                for col in range(1, 13):
                    cx = xs[col - 1] * s
                    cy = ys[row - 1] * s
                    colour = pattern.get((row, col))
                    if colour is None:
                        # Empty well: dark pit with a faint top-left specular hint.
                        grad = QRadialGradient(
                            cx - 0.3 * r_well, cy - 0.3 * r_well, r_well * 1.3
                        )
                        grad.setColorAt(0.0, C_EMPTY.lighter(160))
                        grad.setColorAt(0.4, C_EMPTY)
                        grad.setColorAt(1.0, C_EMPTY.darker(120))
                        p.setBrush(QBrush(grad))
                        p.setPen(Qt.NoPen)
                        p.drawEllipse(QRectF(cx - r_well, cy - r_well,
                                             r_well * 2, r_well * 2))
                    else:
                        # Lit well: white-hot centre radiating to vivid fluorescent colour.
                        grad = QRadialGradient(cx, cy - 0.25 * r_well, r_well * 1.05)
                        grad.setColorAt(0.0, Qt.white)
                        grad.setColorAt(0.30, colour.lighter(160))
                        grad.setColorAt(1.0, colour)
                        p.setBrush(QBrush(grad))
                        p.setPen(Qt.NoPen)
                        p.drawEllipse(QRectF(cx - r_well, cy - r_well,
                                             r_well * 2, r_well * 2))

            p.end()
            return pm

        icon = QIcon()
        for sz in (16, 24, 32, 48, 64, 128, 256, 512):
            icon.addPixmap(_render(sz))
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)

    def _wrap_review_load_path(self) -> None:
        """Hook the review tab's _load_path so the header chip updates whenever
        a dataset is loaded (from either tab or CLI). We monkey-patch instead
        of adding a Qt signal to avoid touching runtime_app's __init__."""
        review = self._review
        if review is None or not hasattr(review, "_load_path"):
            return
        original = review._load_path

        def _patched(path, *a, **kw):
            result = original(path, *a, **kw)
            try:
                self._update_dataset_chip(path)
            except Exception:
                swallowed()  # was: pass (silent except)
            return result

        review._load_path = _patched  # type: ignore[assignment]

    def _update_dataset_chip(self, path) -> None:
        """The titlebar's dataset chip + stats labels were retired (the OS
        window title now carries that information). This setter survives as a
        back-compat shim — every call sets the QMainWindow title (which the host
        OS renders at the top of the window) and refreshes the in-window status
        bar string.
        """
        if path is None:
            self.setWindowTitle("All-Well")
            if hasattr(self, "_status_lbl_app"):
                self._status_lbl_app.setText("Ready.")
            return
        try:
            p = Path(path)
            name = p.name or str(p)
        except Exception:
            name = str(path)
            p = None
        # Best-effort dataset stats from the Review widget's loaded state.
        wells = 0
        tps = 0
        try:
            wells = len(getattr(self._review, "_well_paths", {}) or {})
            tps_attr = getattr(self._review, "_timepoints", None)
            tps = len(tps_attr) if tps_attr is not None else 0
        except Exception:
            swallowed()  # was: pass (silent except)
        tail_bits = []
        if wells:
            tail_bits.append(f"{wells} wells")
        if tps:
            tail_bits.append(f"{tps} timepoints")
        tail = (" · " + " · ".join(tail_bits)) if tail_bits else ""
        # OS window title — visible at the top of the window, which is
        # exactly where the in-titlebar chip used to be redundant with.
        self.setWindowTitle(f"All-Well — {name}{tail}")
        if hasattr(self, "_status_lbl_app"):
            self._status_lbl_app.setText(f"Loaded: {name}{tail}")

    def _on_present_toggled(self, on: bool) -> None:
        """Flip every known PlotCard's plotTheme together."""
        # Checked (active) = Publication (presentation); unchecked = Screen (dark).
        self._present_mode = "publication" if on else "screen"
        self._present_btn.setToolTip(
            f"Presentation mode: currently {self._present_mode} — click to toggle"
        )
        if self._review is None:
            return
        card_attrs = (
            "_line_card", "_bar_card", "_scatter_card",
            "_scatter_agg_card", "_distribution_card", "_heatmap_card",
            "_sp_card", "_mv_card",
        )
        for attr in card_attrs:
            card = getattr(self._review, attr, None)
            if card is None or not hasattr(card, "setPlotTheme"):
                continue
            try:
                card.setPlotTheme(self._present_mode)
            except Exception:
                swallowed()  # was: pass (silent except)

    def _toggle_help_drawer(self) -> None:
        """Show / hide a v2 Drawer with quick help. Lazy-built on first call."""
        if self._help_drawer is None:
            from widgets.drawer import Drawer
            drawer = Drawer(self, width_hint=700)
            # The drawer hosts a single content widget: a short fixed quick-help
            # section at the top, and (when bundled) the reference manual in a
            # self-scrolling QTextBrowser that fills the rest. The manual owns its
            # own scrollbar so its links can scroll to anchors and its height
            # tracks the viewport exactly — no outer scroll area pinning the
            # browser to its document height (the old source of the trailing gap).
            import re

            content = QWidget(drawer)
            cl = QVBoxLayout(content)
            cl.setContentsMargins(theme_v2.Spacing.md, theme_v2.Spacing.md,
                                  theme_v2.Spacing.md, theme_v2.Spacing.md)
            cl.setSpacing(0)

            # Quick help + the reference manual render into ONE self-scrolling
            # QTextBrowser, so the whole drawer is a single scrollable pane rather
            # than a fixed header band above a separately-scrolling document.
            _credit_style = (
                f"color:{theme_v2.Colors.text_muted};font-style:italic;"
                f"font-size:{theme_v2.Typography.caption_size}px;"
            )
            quick_help_html = (
                "<h1>All-Well — quick help</h1>"
                f'<p style="{_credit_style}">Constructed by Zak Singer, 2026</p>'
                "<p><b>Typical workflow</b><br>"
                "1. Put every raw image in a folder called <tt>in/</tt> at "
                "the root of the dataset directory.<br>"
                "2. Open the <b>Analyze</b> tab, point it at that "
                "directory, set the filename schema + channel tokens, and "
                "click <i>Run Pipeline</i>. The pipeline writes results to "
                "a sibling <tt>out/</tt> folder and computes ThreshFracOn "
                "defaults as its last step.<br>"
                "3. When the run finishes the app jumps to <b>Review</b>. "
                "Open the <b>Cell Gating</b> sub-tab (under Sample "
                "Definitions) to inspect the per-channel CDFs and tune "
                "the Cell Area / FluorGating / ThreshFracOn thresholds. "
                "Hit <i>Auto-threshold all channels</i> any time to "
                "re-derive ThreshFracOn defaults.<br>"
                "4. Define replicate groups (Sample Definitions → Groups) "
                "and explore the data on the plotting tabs (Line / Bar / "
                "Scatter / Distribution / Heat Map).</p>"
                "<p><b>Review tab</b> — explore an already-analyzed dataset: "
                "Line / Bar / Scatter / Distribution / Heat Map plots, "
                "per-cell Segmentation review, and the rendered Image Table.</p>"
                "<p><b>Analyze tab</b> — run the segmentation + measurement "
                "pipeline on a fresh dataset.</p>"
                "<p><b>Header buttons</b> — Open a results directory, toggle "
                "presentation mode (all plots Screen ↔ Publication), or this "
                "help drawer.</p>"
                "<p><b>Style panel</b> — the per-card sliders button on every "
                "plot opens the export-style sidebar; click again to hide.</p>"
                "<p><b>Keyboard shortcuts</b><br>"
                "<tt>⌘O</tt> / <tt>Ctrl+O</tt> — Open results directory<br>"
                "<tt>⌘E</tt> / <tt>Ctrl+E</tt> — Export the active figure "
                "(drives the visible plot card's save-figure action)<br>"
                "<tt>⌘←</tt> / <tt>Ctrl+←</tt> — Back to the previously "
                "viewed section tab<br>"
                "<tt>⌘→</tt> / <tt>Ctrl+→</tt> — Forward to the next section "
                "tab in history<br>"
                "<tt>⌘/</tt> / <tt>Ctrl+/</tt> — Toggle this help drawer<br>"
                "<tt>⌘L</tt> / <tt>Ctrl+L</tt> — Toggle the application log "
                "drawer<br>"
                "<tt>⌘B</tt> / <tt>Ctrl+B</tt> — Collapse / expand the left "
                "sidebar<br>"
                "<tt>⌘W</tt> / <tt>Ctrl+W</tt> — Close window</p>"
            )

            # README manual — rendered from the bundled Markdown file so the full
            # reference is reachable without leaving the app. Source tree keeps it
            # under _Docs/Markdowns/; PyInstaller bundles it to Markdowns/ at the
            # app root — try both. Missing file → quick help only (trimmed builds).
            base = Path(__file__).resolve().parent
            readme_text = ""
            readme_path = base / "Markdowns" / "README_WellViewer.md"
            for _candidate in (base / "_Docs" / "Markdowns" / "README_WellViewer.md",
                               base / "Markdowns" / "README_WellViewer.md"):
                try:
                    readme_text = _candidate.read_text(encoding="utf-8")
                    readme_path = _candidate
                    break
                except OSError:
                    continue
            # Under an active (thin) profile, trim the manual to the profile's
            # features so running from source (ALLWELL_PROFILE=thin) shows the
            # same feature help a thin *binary* ships. Idempotent — a binary's
            # README is already trimmed at build time. Best-effort: never let a
            # missing build-tooling import break the help drawer.
            if readme_text:
                try:
                    from scripts.profile_build import trim_readme_for_profile
                    from well_viewer.core.profile import active_profile
                    _prof = active_profile()
                    readme_text = trim_readme_for_profile(
                        readme_text, _prof.features, _prof.subtabs)
                except Exception:
                    pass
                # A running app is already installed — drop install/build help.
                readme_text = _strip_install_help(readme_text)

            manual_view = QTextBrowser(content)
            # Render code / ASCII-diagram blocks at a smaller monospace size so
            # the wide box-art (the ~80-col window + pipeline diagrams) fits the
            # drawer width without wrapping — wrapping shears the box-drawing
            # characters into unreadable garbage. Prose keeps its default size.
            manual_view.document().setDefaultStyleSheet("pre, code { font-size: 9pt; }")
            if readme_text:
                manual_view.setSearchPaths([str(readme_path.parent)])
                # Render markdown -> HTML with injected heading anchors so the
                # README's TOC / cross-reference #slug links resolve, then splice
                # the quick help + a rule in at the top of the same document.
                manual_html = _render_manual_html(readme_text)
                prefix = quick_help_html + "<hr/>"
                combined, n = re.subn(r"(<body[^>]*>)", lambda m: m.group(1) + prefix,
                                      manual_html, count=1)
                manual_view.setHtml(combined if n else prefix + manual_html)
            else:
                manual_view.setHtml(quick_help_html)
            manual_view.setStyleSheet(
                f"QTextBrowser {{ background: transparent; border: none; "
                f"color: {theme_v2.Colors.text_primary}; }}"
            )
            # One browser owns the scrolling and fills the drawer (stretch=1), so
            # anchor links scroll and there's no pinned-height trailing gap.
            manual_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            manual_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            manual_view.setFrameShape(QFrame.NoFrame)

            # Route links ourselves: external URLs open in the system browser,
            # in-document ``#slug`` anchors scroll the manual. (QTextBrowser's
            # default openLinks would try to setSource() to relative targets and
            # either fail or blank the document.)
            manual_view.setOpenLinks(False)

            def _help_anchor_clicked(url, _mv=manual_view) -> None:
                from PySide6.QtGui import QDesktopServices
                frag = url.fragment()
                if url.scheme() in ("http", "https", "mailto"):
                    QDesktopServices.openUrl(url)
                elif frag or url.toString().startswith("#"):
                    _mv.scrollToAnchor(frag or url.toString().lstrip("#"))
                # relative file links (e.g. ARCHITECTURE.md) aren't bundled —
                # ignore rather than blank the manual.

            manual_view.anchorClicked.connect(_help_anchor_clicked)
            cl.addWidget(manual_view, 1)
            self._help_manual_view = manual_view

            drawer.setContentWidget(content)
            self._help_drawer = drawer
            self._help_content = content
        if self._help_drawer.isVisible():
            self._help_drawer.close()
        else:
            self._help_drawer.open()
            # Once the drawer is laid out, jump to the help section that
            # matches the page the user is currently looking at. Deferred a short
            # beat so the manual browser is laid out (its scrollbar range valid)
            # before scrollToAnchor runs.
            QTimer.singleShot(60, self._scroll_help_to_current_section)

    # Map the app's current leaf page → the reference-manual heading that
    # documents it. Keys are the leaf-tab names returned by
    # ``well_viewer.core.tab_controller.current_centre_tab`` (plus the special
    # "Analyze" pane). Values are exact heading texts in
    # ``README_WellViewer.md``. Every Review leaf now has its own dedicated
    # per-feature heading; unknown leaves fall back to the Review-mode overview.
    _HELP_SECTION_BY_PAGE = {
        "Analyze": "Analyze mode — running the pipeline",
        "Bar Plots": "Bar Plots",
        "Line Graphs": "Line Graphs",
        "Scatter Plot": "Scatter Plot",
        "Distribution": "Distribution",
        "Heat Map": "Heat Map",
        "Statistics": "Statistics",
        "Multivariate": "Multivariate analysis",
        "Spatial": "Spatial analysis",
        "Spatial Map": "Spatial analysis",
        "Image Table": "Image Table",
        "Segmentation": "Segmentation",
        "Review Image": "Segmentation",
        "Cell Browser": "Cell Browser",
        "Playground": "Playground",
        "smFISH": "smFISH",
        "Review CSV": "Review CSV",
        "Cell Gating": "Cell Gating",
        "Sample Definitions": "Sample Definitions",
        "Saved Plots": "Saved Plots",
        "Batch Export": "Batch export",
    }

    def _help_section_for_current_page(self) -> str:
        """Heading text in the reference manual that best matches the page the
        user is currently viewing, or ``""`` when it can't be determined."""
        review = getattr(self, "_review", None)
        if review is None:
            return ""
        # Analyze pane wins — its leaf tab isn't a WellViewer centre tab.
        stack = getattr(review, "_central_pane_stack", None)
        if stack is not None and stack.currentIndex() == 1:
            return self._HELP_SECTION_BY_PAGE.get("Analyze", "")
        ctx = getattr(review, "_ctx", None)
        if ctx is None:
            return ""
        try:
            from well_viewer.core.tab_controller import current_centre_tab
            leaf = current_centre_tab(ctx) or ""
        except Exception:
            return ""
        if leaf in self._HELP_SECTION_BY_PAGE:
            return self._HELP_SECTION_BY_PAGE[leaf]
        # Fall back to a loose match so renamed/prefixed leaves ("Spatial Map",
        # "Bar Plots (split)") still resolve to a section.
        for key, section in self._HELP_SECTION_BY_PAGE.items():
            if key.lower() in leaf.lower():
                return section
        # Unknown leaf → land on the Review-mode overview heading.
        return "Review mode — exploring results"

    def _scroll_help_to_current_section(self) -> None:
        """Scroll the open help drawer so the heading for the current page sits
        at the top of the manual. No-op if the drawer/manual isn't built or the
        page can't be resolved. The manual is a self-scrolling browser, so this
        just jumps to the heading's injected anchor (same GitHub slugs the TOC
        links use)."""
        mv = getattr(self, "_help_manual_view", None)
        if mv is None:
            return
        target = self._help_section_for_current_page()
        if not target:
            return
        mv.scrollToAnchor(_gh_slug(target))

    def _current_data_dir(self):
        """Return the directory of the dataset currently loaded in the
        embedded WellViewer, or ``None`` when nothing is loaded.

        The canonical source is ``review._app_state.dataset.data_dir``
        (set by ``well_viewer/core/load_controller.py``). An older ``_data_dir``
        attribute was never actually assigned, so callers that consulted it
        always behaved as if no dataset were loaded."""
        review = getattr(self, "_review", None)
        if review is None:
            return None
        state = getattr(review, "_app_state", None)
        dataset = getattr(state, "dataset", None) if state is not None else None
        cur = getattr(dataset, "data_dir", None) if dataset is not None else None
        # Fall back to the legacy attribute in case a future refactor restores
        # it, so this helper never regresses below the old behaviour.
        if not cur:
            cur = getattr(review, "_data_dir", None)
        return cur

    def _current_dataset_root(self):
        """The path to feed back to ``_load_path`` to reopen the current
        dataset. For an ``in/`` + ``out/`` layout the loaded ``data_dir`` is the
        ``out/`` folder, but ``_load_path`` wants the *root* (the folder holding
        both), so it can re-detect the layout and re-bind the raw ``in/`` images.
        Mirrors the reconstruction in ``_on_analyze_pipeline_complete``. Returns
        ``None`` when nothing is loaded."""
        cur = self._current_data_dir()
        if not cur:
            return None
        cur = Path(cur)
        if cur.name.lower() == "out" and (cur.parent / "in").is_dir():
            return cur.parent
        return cur

    # ── Phase 10 / 11 handlers ───────────────────────────────────────────
    def _on_titlebar_mode_seg(self, idx: int) -> None:
        """Titlebar mode-seg → WellViewerApp's central pane stack.

        Segment order (Review, Analyze, Datasets) matches the central-pane stack
        index order, so the segment index maps straight through.
        """
        review = getattr(self, "_review", None)
        if review is None:
            return
        stack = getattr(review, "_central_pane_stack", None)
        if stack is not None and 0 <= idx < stack.count() and stack.currentIndex() != idx:
            stack.setCurrentIndex(idx)

    def _on_review_mode_changed(self, mode: str) -> None:
        """Mirror WellViewerApp's mode change into the titlebar seg + status."""
        from well_viewer.core.tab_controller import PANE_MODES
        idx = PANE_MODES.index(mode) if mode in PANE_MODES else 0
        seg = getattr(self, "_mode_seg", None)
        if seg is not None and seg.currentIndex() != idx:
            blocked = seg.blockSignals(True)
            try:
                seg.setCurrentIndex(idx)
            finally:
                seg.blockSignals(blocked)
        # Status dot now reflects dataset state (set by _update_dataset_chip),
        # not the Review/Analyze mode toggle — that's already shown by the
        # SegmentedControl itself.

    def _on_refresh_clicked(self) -> None:
        # Re-load the active dataset (Review's _load_path is the canonical
        # entry point).
        review = self._review
        if review is None or not hasattr(review, "_load_path"):
            return
        cur = self._current_dataset_root()
        if cur:
            try:
                review._load_path(Path(cur))
            except Exception:
                swallowed()  # was: pass (silent except)

    def _on_reset_view_clicked(self) -> None:
        """Confirm + wipe the view-state (selections, fold-change, combos)."""
        review = self._review
        if review is None or not hasattr(review, "_view_state_reset"):
            return
        resp = QMessageBox.question(
            self,
            "Reset selections and view?",
            "This will clear selected wells, fold-change scopes, and the "
            "per-tab combo state for every plotting tab. The loaded "
            "dataset itself is left alone.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        try:
            review._view_state_reset()
        except Exception:
            swallowed()  # was: pass (silent except)

    def _on_reset_saves_clicked(self) -> None:
        """Confirm, delete persistence.json, and reload the dataset fresh."""
        review = self._review
        if review is None:
            return
        cur = self._current_data_dir()
        if not cur:
            QMessageBox.information(
                self, "No dataset", "Load a dataset before resetting saved state.")
            return
        resp = QMessageBox.question(
            self,
            "Reset all saved state?",
            "This deletes ALL saved view/analysis state for this dataset — "
            "selections, per-row LUTs, crop regions, plot groups, heatmap "
            "layouts, cell overrides, ratios, QC params, the cell-gating "
            "thresholds and drawn scatter gates, and the saved smFISH "
            "thresholds — then reloads it fresh.\n\nYour data files and "
            "sample-definition labels are left untouched. This cannot be "
            "undone.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        # Cell gating (fluor gates, ThreshFracOn, drawn scatter gates) and the
        # smFISH thresholds live in pipeline_info.json, not persistence.json, so
        # they travel with the dataset. Deleting persistence.json alone left them
        # in place and the reload restored them from the sidecar — the reset
        # looked like it had skipped the gates. Clear those blocks first, so the
        # reload below can't write the old values back.
        try:
            from well_viewer.persistence import cell_gating as _persist_gating
            _persist_gating.clear_from_pipeline_info(review._ctx)
        except Exception:
            swallowed()  # was: pass (silent except)
        try:
            from well_viewer.persistence import smfish as _persist_smfish
            _persist_smfish.clear_thresholds(review._ctx)
        except Exception:
            swallowed()  # was: pass (silent except)
        try:
            from well_viewer.persistence import _doc as _persist_doc
            _persist_doc.delete(review)
        except Exception:
            swallowed()  # was: pass (silent except)
        try:
            root = self._current_dataset_root() or cur
            if hasattr(review, "_load_path"):
                review._load_path(Path(root))
        except Exception:
            swallowed()  # was: pass (silent except)

    def _toggle_log_drawer(self) -> None:
        if self._log_drawer is None:
            from widgets.drawer import Drawer
            # A wide, horizontal log panel that rises from the bottom edge (log
            # lines are long — a narrow side panel wrapped/clipped them).
            drawer = Drawer(self, edge="bottom", height_fraction=0.4)
            body = QWidget(drawer)
            bl = QVBoxLayout(body)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(theme_v2.Spacing.sm)
            head = QLabel("Application log")
            head.setObjectName("Heading")
            bl.addWidget(head)
            from PySide6.QtWidgets import QTextEdit as _QTextEdit
            self._log_view = _QTextEdit(body)
            self._log_view.setReadOnly(True)
            self._log_view.setStyleSheet(
                f"QTextEdit {{ background-color: {theme_v2.Colors.panel}; "
                f"color: {theme_v2.Colors.text_secondary}; "
                f"font-family: {theme_v2.Typography.family_mono}; "
                f"font-size: {theme_v2.Typography.caption_size}px; "
                f"border: 1px solid {theme_v2.Colors.border_subtle}; }}"
            )
            bl.addWidget(self._log_view, 1)
            drawer.setContentWidget(body)
            self._log_drawer = drawer
            self._attach_log_ring_buffer()
        # Re-fill from ring buffer each open in case the drawer was rebuilt.
        try:
            self._log_view.setPlainText("\n".join(self._log_ring[-500:]))
        except Exception:
            swallowed()  # was: pass (silent except)
        if self._log_drawer.isVisible():
            self._log_drawer.close()
        else:
            self._log_drawer.open()

    def _append_log_line(self, text: str) -> None:
        """Append one formatted record to the open drawer view (main-thread only)."""
        try:
            view = getattr(self, "_log_view", None)
            if view is not None:
                view.append(text)
        except Exception:
            swallowed()

    def _attach_log_ring_buffer(self) -> None:
        """Install a logging.Handler that keeps the last N records in
        ``self._log_ring`` (so the Log drawer has history to show) and appends
        new records live while the drawer is open.

        The whole app logs under the ``"well_viewer"`` logger, which sets
        ``propagate = False`` (see ``core/status_view.build_bottom``) — so a
        handler on the *root* logger, as this used to be, never receives those
        records and the drawer stays empty. Attach directly to ``"well_viewer"``
        (plus root, for the few ``__name__`` loggers under ``services/`` that
        still propagate). Called once at startup so records emitted before the
        drawer is first opened are captured. Idempotent."""
        if getattr(self, "_log_ring_attached", False):
            return
        import logging as _logging
        from PySide6.QtCore import QTimer as _QTimer

        ring = self._log_ring
        app = self

        class _RingHandler(_logging.Handler):
            def emit(self, record):
                try:
                    line = self.format(record)
                    ring.append(line)
                    if len(ring) > 1000:
                        del ring[:500]
                    drawer = getattr(app, "_log_drawer", None)
                    if drawer is not None and drawer.isVisible():
                        # Marshal to the Qt main thread (records may come from
                        # worker threads).
                        _QTimer.singleShot(0, lambda t=line: app._append_log_line(t))
                except Exception:
                    swallowed()  # was: pass (silent except)

        h = _RingHandler()
        h.setFormatter(_logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
                                          datefmt="%H:%M:%S"))
        # Default level is WARNING — that hides every ``logger.info`` call
        # (progress streams, status updates, …). Lower the handler and each
        # target logger to INFO so the user sees what the app is doing.
        h.setLevel(_logging.INFO)
        for _name in ("well_viewer", ""):
            lg = _logging.getLogger(_name)
            if lg.level == _logging.NOTSET or lg.level > _logging.INFO:
                lg.setLevel(_logging.INFO)
            lg.addHandler(h)
        self._log_ring_attached = True

    def _open_dataset(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Open results directory")
        if not d:
            return
        self._nb.setCurrentIndex(0)
        if self._review is not None:
            QTimer.singleShot(50, lambda: self._review._load_path(Path(d)))

    def _reveal_dataset_in_file_manager(self) -> None:
        """Reveal the currently-loaded dataset's folder in the host file
        manager (Finder on macOS, Explorer on Windows, the default file
        manager via ``xdg-open`` on Linux). Falls back to the standard
        Open-directory dialog when no dataset is loaded yet."""
        # The previously-loaded directory lives on
        # ``review._app_state.dataset.data_dir`` (set by
        # ``well_viewer/core/load_controller.py``). Earlier code consulted a
        # never-set ``_data_dir``/``_loaded_path`` attribute and therefore
        # always fell into the "open a dataset" dialog branch.
        cur = self._current_data_dir()
        if cur is None or not Path(cur).exists():
            # Nothing loaded — fall through to the "load a dataset" path so
            # the button still does something useful on a cold start.
            self._open_dataset()
            return
        path = str(Path(cur))
        try:
            if sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", "-R", path])
            elif sys.platform.startswith("win"):
                import subprocess
                subprocess.Popen(["explorer", "/select,", path])
            else:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                # ``xdg-open`` doesn't have a generic "reveal/select" mode;
                # open the containing folder so the user can spot the item.
                target = path if Path(path).is_dir() else str(Path(path).parent)
                QDesktopServices.openUrl(QUrl.fromLocalFile(target))
        except Exception:
            # Last-resort fallback — open the folder in Qt's default way.
            try:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            except Exception:
                swallowed()  # was: pass (silent except)

    def _on_tab_change(self, idx: int) -> None:
        if self._review is None:
            return
        # QStackedWidget has no tabText — index 0 is Review, 1 is Analyze,
        # 2 is Datasets (matches the order they were added to self._nb and to
        # the mode-seg).
        if idx == 0:
            QTimer.singleShot(50, self._nudge_review)
        # WellViewerApp's own mode-seg syncs via its _on_central_pane_changed;
        # nothing to do at this layer.

    def _on_analyze_pipeline_complete(self, output_dir: Path) -> None:
        if self._review is None:
            return
        dataset_path = output_dir
        if output_dir.name.lower() == "out" and (output_dir.parent / "in").is_dir():
            dataset_path = output_dir.parent
        self._nb.setCurrentIndex(0)
        QTimer.singleShot(50, lambda: self._review._load_path(dataset_path))
        self._update_dataset_chip(dataset_path)

    def _open_dataset_in_review(self, dataset_path: Path) -> None:
        """Load a dataset root into Review and switch to it (from Datasets)."""
        if self._review is None:
            return
        self._nb.setCurrentIndex(0)
        QTimer.singleShot(50, lambda: self._review._load_path(dataset_path))
        self._update_dataset_chip(dataset_path)

    def _open_dataset_in_analyze(self, dataset_path: Path) -> None:
        """Point the Analyze pane at a dataset folder and switch to it.

        Used by the Datasets OME-TIFF converter: the folder it produced is an
        Analyze *input* (a raw ``in/`` dataset), so it opens in Analyze — where
        the user configures channels and runs the pipeline — not in Review.
        """
        if self._analyze is None:
            return
        self._nb.setCurrentIndex(1)
        edit = getattr(self._analyze, "_input_edit", None)
        if edit is not None:
            edit.setEnabled(True)
            edit.setText(str(dataset_path))

    def _nudge_review(self) -> None:
        if self._review is None:
            return
        if hasattr(self._review, "_redraw"):
            try:
                self._review._redraw()
            except Exception:
                swallowed()  # was: pass (silent except)
        try:
            from well_viewer.features.barplot.renderer import redraw_bars
            redraw_bars(self._review)
        except Exception:
            swallowed()  # was: pass (silent except)

    def _qsettings(self):
        from PySide6.QtCore import QSettings
        return QSettings("AllWell", "AllWellApp")

    def _restore_window_state(self) -> None:
        """Restore the main window geometry + splitter sizes saved from the
        previous session. Silent no-op the first time a user runs the app."""
        try:
            s = self._qsettings()
            geom = s.value("window/geometry")
            if geom is not None:
                self.restoreGeometry(geom)
            state = s.value("window/state")
            if state is not None:
                self.restoreState(state)
            # Splitter geometry lives inside the Review widget.
            if self._review is not None:
                h_pane = getattr(self._review, "_h_pane", None)
                if h_pane is not None:
                    sizes = s.value("review/h_pane_sizes")
                    if sizes is not None:
                        # QSettings serialises lists of int as list[str] on some
                        # backends; normalise.
                        try:
                            h_pane.setSizes([int(x) for x in sizes])
                        except Exception:
                            swallowed()  # was: pass (silent except)
        except Exception:
            swallowed()  # was: pass (silent except)

    def _save_window_state(self) -> None:
        try:
            s = self._qsettings()
            s.setValue("window/geometry", self.saveGeometry())
            s.setValue("window/state", self.saveState())
            if self._review is not None:
                h_pane = getattr(self._review, "_h_pane", None)
                if h_pane is not None:
                    s.setValue("review/h_pane_sizes", h_pane.sizes())
        except Exception:
            swallowed()  # was: pass (silent except)

    def closeEvent(self, event) -> None:
        self._save_window_state()
        if self._review is not None and hasattr(self._review, "_persistence"):
            try:
                self._review._persistence.save("view_state")
            except Exception:
                swallowed()  # was: pass (silent except)
        if self._review is not None and hasattr(self._review, "_cleanup_tmp"):
            try:
                self._review._cleanup_tmp()
            except Exception:
                swallowed()  # was: pass (silent except)
        # Stop any in-flight Analyze run — AnalyzeTab's own closeEvent never
        # fires (it's a child widget), so without this a mid-run quit leaks the
        # local subprocess / remote SLURM allocation + scratch (audit L1). Also
        # stops the log poller re-arming (L2).
        _stop_analyze_runner(self._analyze)
        # Datasets pane is also a child widget: stop any in-flight concatenate
        # worker before teardown so a mid-run quit doesn't leave a live thread.
        datasets = getattr(self, "_datasets", None)
        if datasets is not None and hasattr(datasets, "shutdown"):
            try:
                datasets.shutdown()
            except Exception:
                swallowed()
        # Review pane is also a child widget: signal its smFISH worker to abort
        # and disconnect the app-global focus watcher on close (audit L3/L4).
        if self._review is not None:
            try:
                from well_viewer.runtime_app import teardown_review_pane
                teardown_review_pane(self._review)
            except Exception:
                swallowed()
        super().closeEvent(event)

def _install_combobox_popup_widener() -> None:
    """Patch QComboBox.showPopup so the popup view sizes itself to the
    widest item text instead of inheriting the field's width — dropdown
    items elsewhere in the app were being clipped at the field width."""
    from PySide6.QtWidgets import QComboBox, QStyleOptionComboBox
    if getattr(QComboBox, "_aw_popup_widened", False):
        return
    QComboBox._aw_popup_widened = True
    _original_show = QComboBox.showPopup

    def _wide_show(self) -> None:  # Qt API name
        view = self.view()
        if view is not None:
            fm = view.fontMetrics()
            max_w = 0
            for i in range(self.count()):
                w = fm.horizontalAdvance(self.itemText(i))
                if w > max_w:
                    max_w = w
            opt = QStyleOptionComboBox()
            self.initStyleOption(opt)
            scrollbar_w = view.verticalScrollBar().sizeHint().width() if view.verticalScrollBar() else 0
            extra = 28 + scrollbar_w   # padding + scrollbar room
            target = max(self.width(), max_w + extra)
            view.setMinimumWidth(target)
        _original_show(self)

    QComboBox.showPopup = _wide_show


def main() -> None:
    # Restrict matplotlib to the bundled fonts BEFORE anything imports it (the
    # first plot pulls matplotlib.figure -> font_manager). Skips matplotlib's
    # full system-font enumeration, which can cost minutes on first launch on a
    # Windows 365 Cloud PC. Defensive: never blocks startup. (This is the #1
    # suspect for a silent "hangs on launch" — the boot log brackets it so a
    # stall here is visible as the last-reached milestone.)
    _boot_diag.boot("mpl_bootstrap.configure() — limiting matplotlib font scan …")
    try:
        import mpl_bootstrap
        mpl_bootstrap.configure()
    except Exception:
        swallowed()
    _boot_diag.boot("mpl_bootstrap done")

    # faulthandler is already armed onto the boot log by boot_diag at import
    # time; nothing more to do here.

    # Surface any misconfigured ALLWELL_* overrides once at startup (audit
    # X12) instead of letting a mistyped scratch/remotes path fall back
    # silently and only misbehave much later, mid-run.
    try:
        from services.env import log_env_validation
        log_env_validation()
    except Exception:
        swallowed()  # diagnostics-only; never block startup

    from well_viewer.core import debug_flags as _debug_flags

    _debug_flags.REVIEW_TAB_DEBUG = REVIEW_TAB_DEBUG
    _debug_flags.ANALYZE_TAB_DEBUG = ANALYZE_TAB_DEBUG
    _debug_flags.REVIEW_BAR_DEBUG = REVIEW_BAR_DEBUG
    _debug_flags.REVIEW_SCATTER_DEBUG = REVIEW_SCATTER_DEBUG

    ap = argparse.ArgumentParser(
        description="All-Well: pipeline runner + well viewer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--data_dir", type=Path, default=None,
                    help="Pre-load a results directory into the Review tab on startup.")
    ap.add_argument("--theme", default=None, choices=theme_v2.available_themes(),
                    help="UI theme (overrides the ALLWELL_THEME env var).")
    args = ap.parse_args()

    _boot_diag.boot("creating QApplication (loads Qt platform plugin) …")
    app = QApplication.instance() or QApplication(sys.argv)
    _boot_diag.boot("QApplication created OK")
    _install_combobox_popup_widener()
    # Collapse both font-family tokens to single resolvable names so Qt's
    # QSS parser doesn't walk alias tables and warn at startup
    # ("qt.qpa.fonts: Populating font family aliases took NN ms. Replace
    # uses of missing font family ...").
    #
    # ``Typography.family`` is the proportional UI face, ``family_mono`` the
    # fixed-pitch face. Prefer the bundled Inter / JetBrains Mono (consistent
    # across platforms) and fall back to the OS default-app / fixed-pitch
    # families when the bundled files are unavailable. The CSS-style generics
    # ``sans-serif`` / ``monospace`` are NOT in Qt's font registry, so leaving
    # them there triggers the alias walk on every widget that uses QSS.
    from well_viewer.core.app_fonts import register_qt_fonts as _register_qt_fonts
    _ui_family, _mono_family = _register_qt_fonts()
    try:
        theme_v2.Typography.family = _ui_family or app.font().family()
    except Exception:
        theme_v2.Typography.family = _ui_family or ""
    # Widgets that read the app default font directly (not just via QSS) should
    # use the bundled face too — re-family the existing app font (keeps size).
    if _ui_family:
        _appfont = app.font()
        _appfont.setFamily(_ui_family)
        app.setFont(_appfont)
    try:
        from PySide6.QtGui import QFontDatabase as _QFontDatabase
        theme_v2.Typography.family_mono = _mono_family or _QFontDatabase.systemFont(
            _QFontDatabase.SystemFont.FixedFont
        ).family()
    except Exception:
        theme_v2.Typography.family_mono = _mono_family or ""
    # Select the UI theme (dev-facing ALLWELL_THEME env var) BEFORE building any
    # widgets, so qss() and every widget's cached inline QSS pick up its tokens.
    # Importing ui.theme.styles here guarantees its theme-refresh hook is
    # registered before the theme is applied, so get_color() tracks the theme.
    import ui.theme.styles as _ui_styles  # noqa: F401  (registers a theme hook)
    _active_theme = (theme_v2.apply_theme(args.theme) if args.theme
                     else theme_v2.select_theme_from_env())
    _boot_diag.boot(f"theme applied: {_active_theme}")
    app.setStyleSheet(theme_v2.qss())
    _boot_diag.boot("building main window (AllWellApp) …")
    win = AllWellApp(data_path=args.data_dir)
    _boot_diag.boot("main window built; showing")
    win.show()
    _boot_diag.boot("entering Qt event loop (startup complete)")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
