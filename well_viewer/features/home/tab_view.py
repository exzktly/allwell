"""The Home page's widget tree.

Structure only — every number on screen comes from :mod:`.collect`, and every
behaviour from :mod:`.controller`. What lives here is the arrangement and the
one rendering rule the page is built around: a figure that could not be obtained
draws an em-dash and says why, rather than a zero that would read as a
measurement.

Block order follows the questions a returning user asks, in order: what is this
plate, what should I do about it, did the run finish, what did I say about it,
what is in it, what went wrong, what was it run with, what have I already done.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

import theme
from well_viewer.core.ui_helpers import notes_schedule_save

from . import collect as hc
from .plate_panel import STATE_COLORS, STATE_LABELS, build_home_plate

#: The em-dash a figure the page could not obtain renders as.
DASH = "—"

_NOTES_KEY = "_home_notes_edit"


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
    """Build the page. Called once, lazily, the first time Home is shown."""
    reg = ctx.widgets
    outer = parent.layout() or QVBoxLayout(parent)
    outer.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setObjectName("HomeScroll")
    # Before a dataset is open there is nothing here to report, and a page of
    # em-dashes under a masthead reading "No dataset open" describes its own
    # emptiness at length rather than saying what to do about it. Home takes the
    # same placeholder every other Review page shows, through the same registry,
    # so the wording and the moment of the swap are shared rather than
    # re-decided here. No ``reason``: Home is gated on the dataset alone.
    from well_viewer.core.ui_helpers import wrap_with_empty_state

    stack = wrap_with_empty_state(ctx.window, scroll, icon="home")
    reg.register("_home_empty_stack", stack)
    outer.addWidget(stack)
    # ...but Home is the page the app opens on, so pointing at a control in the
    # rail and stopping there is weaker here than anywhere else. The button runs
    # the same action the masthead and the next-step row use, so "open a
    # dataset" cannot come to mean two things.
    placeholder = getattr(stack, "_empty_placeholder", None)
    if placeholder is not None:
        placeholder.setAction("Open a dataset…", _action_handler(ctx, "open"))

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

    from .controller import hide_rail_body, refresh_home
    # Home is the landing page, and the landing page's activation hook never
    # fires — ``build_centre`` selects it before connecting the change handler.
    # So the rail has to be put into Home's state here, or the page looks one
    # way on launch and another way every time after.
    hide_rail_body(ctx)
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
            else:
                _set_notes_status(ctx, "saved")

    edit = reg.register(_NOTES_KEY, _HomeNotes(frame))
    edit.setObjectName("HomeNotes")
    edit.setPlaceholderText(
        "What happened on this plate? Anything the numbers won't tell you later.")
    edit.setPlainText(ctx.state.selection.notes_text or "")
    layout.addWidget(edit, 1)
    # The commit is on focus-out and therefore invisible: a user types, clicks
    # away, and nothing on screen distinguishes "written" from "lost". The line
    # below says which, and is the only part of this page that changes without
    # the record changing.
    status = reg.register("_home_notes_status", _muted(frame, NOTES_IDLE))
    layout.addWidget(status)
    edit.textChanged.connect(lambda: _set_notes_status(ctx, "dirty"))
    return frame


#: What the line under the notes box says in each state. Written out rather than
#: assembled so the three readings can be compared side by side — the failure
#: wording is the one that matters and it must not look like the others.
NOTES_IDLE = "Saves when you click away."
NOTES_DIRTY = "Unsaved — saves when you click away."
NOTES_SAVED = "Saved."
_NOTES_STATUS = {"idle": NOTES_IDLE, "dirty": NOTES_DIRTY, "saved": NOTES_SAVED}


def _set_notes_status(ctx, state: str) -> None:
    label = ctx.widgets.get_or_none("_home_notes_status")
    if label is not None:
        label.setText(_NOTES_STATUS.get(state, NOTES_IDLE))


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
