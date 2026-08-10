"""The Home page's widget tree.

Structure only — every number on screen comes from :mod:`.collect`, and every
behaviour from :mod:`.controller`. What lives here is the arrangement and the
one rendering rule the page is built around: a figure that could not be obtained
draws an em-dash and says why, rather than a zero that would read as a
measurement.

The page is a notebook. **Overview** is what the app opens on — the reporting
column this module builds, whose block order follows the questions a returning
user asks: what is this plate, what should I do about it, did the run finish,
what did I say about it, what is in it, what went wrong, what was it run with,
what have I already done. The tabs after it are where the user *declares* what
the wells mean, and their bodies live in ``features/grouping`` and
``features/scatter``; :mod:`.controller` builds them through lazy proxies so
this feature never imports another one at module scope.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

import theme
from well_viewer.core.app_shell_view import mirror_tab_widget
from well_viewer.core.profile import active_profile
from well_viewer.core.selection_controller import import_selections_from_json
from well_viewer.core.ui_helpers import notes_schedule_save
from widgets.icon_button import IconButton
from widgets.page_body import build_page_body

from . import collect as hc
from .plate_panel import STATE_COLORS, STATE_LABELS, build_home_plate

#: The em-dash a figure the page could not obtain renders as.
DASH = "—"

_NOTES_KEY = "_home_notes_edit"

#: The Import action, registered so the sub-tab handler can hide it on Overview.
IMPORT_KEY = "_home_import_btn"

#: The page's tabs, in order. Overview leads because it is the landing page and
#: reports rather than asks. Cell Gating follows it as the definition users reach
#: for most, keeping the order the definitions had among themselves.
SUBTABS = (
    "Overview", "Cell Gating", "Groups", "Well Labels", "Ratios",
    "Pipeline Info",
)

#: Tabs whose content is a definition editor rather than the report. The Import
#: action and the sidebar's group-editing plate belong to these and to no other.
DEFINITION_SUBTABS = tuple(t for t in SUBTABS if t != "Overview")

#: Each body's own inset. The report and the two read-only panes paint to their
#: own edges; the two editable grids want a gutter. The frames themselves are
#: flush with the notebook either way, which is what ``test_page_alignment``
#: measures.
_SUBTAB_MARGINS = {
    "Groups": theme.Spacing.md,
    "Well Labels": theme.Spacing.md,
}


def active_subtabs() -> tuple[str, ...]:
    """The tabs this build ships. Overview is not removable — it is the page."""
    prof = active_profile()
    return tuple(t for t in SUBTABS
                 if t == "Overview" or prof.includes_subtab("Home", t))


def _card(parent: QWidget, title: str = "") -> tuple[QFrame, QVBoxLayout]:
    """A titled panel. Returns the frame and the layout its body goes in."""
    frame = QFrame(parent)
    frame.setObjectName("HomeCard")
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(theme.Spacing.lg, theme.Spacing.md,
                             theme.Spacing.lg, theme.Spacing.md)
    outer.setSpacing(theme.Spacing.sm)
    if title:
        label = QLabel(title, frame)
        label.setObjectName("HomeCardTitle")
        outer.addWidget(label)
    return frame, outer


def _muted(parent: QWidget, text: str) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


def build_home_tab(ctx, parent: QWidget) -> None:
    """Build the page shell: the control band, then the sub-tab notebook.

    Overview is built here because it is what the app opens on. The definition
    tabs are built one per event-loop tick by ``controller.drain_home_subtabs``
    immediately afterwards, which is where they were built before they moved
    onto this page — their old page was drained the same way, and code across
    the app (persistence hydration, the plot tabs' threshold quick-edit, the
    Playground's context bar) reads their widgets shortly after a load without
    the user having opened anything. Building them inline instead would put all
    of it on the startup path, which the landing page cannot pay.
    """
    band, body = build_page_body(parent)

    # Every definition on this page autosaves to the data folder on edit, so
    # there is no Save button to pair this with: Import merely merges an
    # external JSON in. It is an action on the page rather than on any one tab,
    # but it means nothing beside the report, so the handler hides it there.
    import_btn = ctx.widgets.register(
        IMPORT_KEY, IconButton("plus", band, text="Import"))
    import_btn.setToolTip(
        "Import selections from a JSON file (merges into the current set).")
    import_btn.clicked.connect(lambda _=False: import_selections_from_json(ctx))
    band.add_action(import_btn)

    sub_tabs = QTabWidget(parent)
    # One theme.py rule names every nested sub-tab bar in the pane — this one,
    # Plotting's and the Image Browser's — so the three cannot drift apart.
    sub_tabs.setObjectName("HomeSubTabs")
    sub_tabs.tabBar().setExpanding(False)
    sub_tabs.tabBar().setElideMode(Qt.ElideNone)
    sub_tabs.tabBar().setUsesScrollButtons(True)
    body.layout().addWidget(sub_tabs, 1)
    ctx.runtime.home.subtabs = sub_tabs
    ctx.runtime.home.built = {}

    for name in active_subtabs():
        frame = QWidget(sub_tabs)
        inset = _SUBTAB_MARGINS.get(name, 0)
        QVBoxLayout(frame).setContentsMargins(inset, inset, inset, inset)
        sub_tabs.addTab(frame, name)
        if name == "Cell Gating":
            # The Cell Gating builder finds its own frame through the runtime
            # rather than being handed one, because ``ensure_cell_gating_tab_built``
            # materializes it from persistence with no sub-tab index to hand over.
            ctx.runtime.cell_gating.subtab_frame = frame
            ctx.runtime.cell_gating.subtab_built = False

    from .controller import drain_home_subtabs, on_home_subtab_changed
    sub_tabs.currentChanged.connect(lambda idx: on_home_subtab_changed(ctx, idx))

    # The visible bar lives in the shell's mode band beside the mode selector,
    # not above this page's body. ``mirror_tab_widget`` hides this widget's own
    # bar and drives it from one up there, leaving the QTabWidget's API — which
    # the plate-commit guard and several tests read — untouched.
    mirror_tab_widget(ctx.window, "Home", sub_tabs)

    build_home_overview(ctx, sub_tabs.widget(0))
    drain_home_subtabs(ctx)


def build_home_overview(ctx, parent: QWidget) -> None:
    """Build the Overview tab — the report the page opens on."""
    reg = ctx.widgets
    outer = parent.layout() or QVBoxLayout(parent)
    outer.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setObjectName("HomeScroll")
    outer.addWidget(scroll)

    page = QWidget(scroll)
    scroll.setWidget(page)
    body = QVBoxLayout(page)
    body.setContentsMargins(theme.Spacing.xl, theme.Spacing.lg,
                            theme.Spacing.xl, theme.Spacing.xl)
    body.setSpacing(theme.Spacing.lg)

    body.addWidget(_build_masthead(ctx, page, reg))
    body.addWidget(_build_next_step(ctx, page, reg))
    body.addLayout(_build_plate_and_notes(ctx, page, reg))
    body.addWidget(reg.register("_home_tiles", _tile_strip(page)))
    body.addLayout(_build_health_and_receipt(ctx, page, reg))
    body.addWidget(_build_your_work(ctx, page, reg))
    body.addStretch(1)

    from .controller import refresh_home
    refresh_home(ctx)


# ── masthead ────────────────────────────────────────────────────────────────

def _build_masthead(ctx, page: QWidget, reg) -> QWidget:
    host = QWidget(page)
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.Spacing.lg)

    left = QVBoxLayout()
    left.setSpacing(theme.Spacing.xs)
    left.addWidget(reg.register("_home_title", QLabel("", host)))
    reg.get("_home_title").setObjectName("HomeTitle")
    left.addWidget(reg.register("_home_path", _muted(host, "")))
    left.addWidget(reg.register("_home_schema", _muted(host, "")))
    row.addLayout(left, 1)

    right = QVBoxLayout()
    right.setSpacing(theme.Spacing.xs)
    right.setAlignment(Qt.AlignRight | Qt.AlignTop)
    right.addWidget(reg.register("_home_run_meta", _muted(host, "")))
    actions = QHBoxLayout()
    actions.setSpacing(theme.Spacing.xs)
    from .controller import HOME_ACTIONS
    for key, label in HOME_ACTIONS:
        button = QPushButton(label, host)
        button.setObjectName("HomeAction")
        button.clicked.connect(_action_handler(ctx, key))
        actions.addWidget(button)
        reg.register(f"_home_action_{key}", button)
    right.addLayout(actions)
    row.addLayout(right)
    return host


def _action_handler(ctx, key: str):
    def _run(_checked=False) -> None:
        from .controller import run_home_action
        run_home_action(ctx, key)
    return _run


# ── next step ───────────────────────────────────────────────────────────────

def _build_next_step(ctx, page: QWidget, reg) -> QWidget:
    frame, layout = _card(page)
    frame.setObjectName("HomeNextStep")
    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.md)
    row.addWidget(reg.register("_home_next_text", QLabel("", frame)), 1)
    button = reg.register("_home_next_button", QPushButton("", frame))
    button.clicked.connect(lambda _c=False: _run_next_step(ctx))
    row.addWidget(button)
    layout.addLayout(row)
    return frame


def _run_next_step(ctx) -> None:
    from .controller import run_home_action
    run_home_action(ctx, hc.next_step(ctx).get("action", ""))


# ── plate + notes ───────────────────────────────────────────────────────────

def _build_plate_and_notes(ctx, page: QWidget, reg) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.lg)

    plate_card, plate_layout = _card(page, "Plate")
    plate_row = QHBoxLayout()
    plate_row.setSpacing(theme.Spacing.lg)
    plate = reg.register("_home_plate", build_home_plate(plate_card))
    from .controller import on_home_plate_selection
    plate.selectionChanged.connect(
        lambda wells: on_home_plate_selection(ctx, wells))
    plate_row.addWidget(plate, 0, Qt.AlignHCenter | Qt.AlignTop)
    plate_row.addWidget(reg.register("_home_legend", _legend(plate_card)), 0,
                        Qt.AlignTop)
    plate_layout.addLayout(plate_row)
    plate_layout.addWidget(reg.register("_home_plate_summary",
                                        _muted(plate_card, "")))
    row.addWidget(plate_card, 7)

    row.addWidget(_build_notes(ctx, page, reg), 5)
    return row


def _legend(parent: QWidget) -> QWidget:
    host = QWidget(parent)
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(theme.Spacing.sm)
    grid.setVerticalSpacing(theme.Spacing.xs)
    host.setProperty("rows", {})
    return host


def _build_notes(ctx, page: QWidget, reg) -> QWidget:
    """The one editable thing on an otherwise read-only page.

    Always editable rather than click-to-edit: the hover lift already tells the
    reader this region accepts typing, and adding a mode to a page whose whole
    premise is that it only reports would cost more than it saves. It commits on
    focus-out, the same rule the long-form editor follows, and mirrors into that
    editor so the two can never hold different text.
    """
    from PySide6.QtWidgets import QPlainTextEdit

    frame, layout = _card(page, "Notes")

    class _HomeNotes(QPlainTextEdit):
        def focusOutEvent(self, event):
            super().focusOutEvent(event)
            from well_viewer.core.safe import swallowed
            try:
                notes_schedule_save(ctx, widget_key=_NOTES_KEY)
            except Exception:
                swallowed("home notes commit")

    edit = reg.register(_NOTES_KEY, _HomeNotes(frame))
    edit.setObjectName("HomeNotes")
    edit.setPlaceholderText(
        "What happened on this plate? Anything the numbers won't tell you later.")
    edit.setPlainText(ctx.state.selection.notes_text or "")
    layout.addWidget(edit, 1)
    layout.addWidget(_muted(frame, "Saves when you click away."))
    return frame


# ── tiles ───────────────────────────────────────────────────────────────────

def _tile_strip(page: QWidget) -> QWidget:
    host = QWidget(page)
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(theme.Spacing.md)
    grid.setVerticalSpacing(theme.Spacing.md)
    return host


# ── health + receipt ────────────────────────────────────────────────────────

def _build_health_and_receipt(ctx, page: QWidget, reg) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(theme.Spacing.lg)

    health_card, health_layout = _card(page, "Health")
    health_layout.addWidget(reg.register("_home_health", _rows_host(health_card)), 1)
    validate = QPushButton("Check this dataset now", health_card)
    validate.setObjectName("HomeAction")
    validate.clicked.connect(_action_handler(ctx, "validate"))
    health_layout.addWidget(validate, 0, Qt.AlignLeft)
    reg.register("_home_validate_btn", validate)
    row.addWidget(health_card, 7)

    receipt_card, receipt_layout = _card(page, "Run parameters")
    receipt_layout.addWidget(
        reg.register("_home_receipt", _rows_host(receipt_card)), 1)
    row.addWidget(receipt_card, 5)
    return row


def _rows_host(parent: QWidget) -> QWidget:
    host = QWidget(parent)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.Spacing.xs)
    return host


# ── your work ───────────────────────────────────────────────────────────────

def _build_your_work(ctx, page: QWidget, reg) -> QWidget:
    card, layout = _card(page, "Your work here")
    layout.addWidget(reg.register("_home_work", _rows_host(card)))
    return card


# ── rendering helpers used by the controller ────────────────────────────────

def format_value(value) -> str:
    """A figure, or the em-dash that says it could not be obtained."""
    if isinstance(value, hc.Missing):
        return DASH
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def legend_swatch_style(state: str) -> str:
    colour = STATE_COLORS.get(state, theme.Colors.border)
    return (f"background:{colour}; border-radius:{theme.Radii.xs}px;")


def legend_label(state: str) -> str:
    return STATE_LABELS.get(state, state)
