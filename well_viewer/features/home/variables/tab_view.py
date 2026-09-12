"""The Variables sub-tab — a plate, with the form that feeds it beside.

**A canvas with a form beside it, not a form with a picture beside it.** That
inversion is what this page was, and every complaint about it followed: the
plate — the only surface where the work happens and the only one where a user
can come to trust what is annotated — sat third down a column under a value
list that had been given all the vertical stretch, while the definition form
took the top third of the page for four fields that are typed once and never
touched again.

So the page is two columns. The **left** is the definition: which variables
exist, and what the selected one is called, typed as and made of
(``editor_view``). The **right** is the plate and everything that acts on it —
what is selected, what to set it to, how to fill the whole thing at once, and
what the saved groups roll up to (``plate_view``). Across the top of both, a
header naming what the plate is showing, because one list selection silently
decides four things and nothing used to write the answer down.

**Every rule this tab enforces lives in the domain** — ``sample_variables``
for the model and its refusals, ``variable_series`` for what a value looks
like, ``plate_layout`` for what "along columns" means. This package builds
widgets, connects them in one place, and repaints. That split is §7.6's lesson
made structural: a rule implemented in a signal handler is a rule nothing tests.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ui import theme

#: The definition column's width. A fixed rail, so the plate beside it does not
#: reflow every time a variable is renamed.
EDITOR_W = 268

#: What the tab says when a plate has no variables yet — the payoff alone. The
#: Sample Map tab's role strip sits above this and already says what a variable
#: *is*, whether or not any exist; saying it twice on one screen is how the only
#: statement of that used to disappear the moment somebody defined one.
EMPTY_HINT = (
    "Two cell lines at six doses is twelve conditions, and you do not have to "
    "paint twelve group names to plot them separately: name the two variables "
    "once, fill their values onto the plate, and every plot can group, colour "
    "and split by either."
)

#: Said once, at the top, rather than appended to a status line that is re-read
#: on every click. It is a standing fact about what an unannotated well costs,
#: not news about the plate in front of the user — and it used to arrive as the
#: tail of a sentence that changed every time a well did.
POLICY_NOTE = (
    "A plot grouped by a variable leaves out any member holding a well with no "
    "value, and says how many on the figure."
)


def build_variables_subtab(ctx, frame: QWidget) -> None:
    """Build the tab body into *frame*. Registered lazily, like every sub-tab."""
    from .controller import refresh_variables, wire_variables
    from .editor_view import build_editor_column
    from .plate_view import build_plate_column

    reg = ctx.widgets
    outer = frame.layout() or QVBoxLayout(frame)
    # No margins of its own: the frame is already inset by ``subtab_inset`` on
    # the descriptor, the way Groups and Well Labels are, and a second set here
    # would double it on one tab only.
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(theme.Spacing.md)

    outer.addWidget(_build_header(ctx, frame, reg))

    # One line for "what just happened", and nothing else ever writes to it.
    # The tab used to have four muted labels between them carrying status, the
    # fill's scope, an error, an instruction and a policy note — so a message
    # was routinely overwritten by the next repaint of whatever else that label
    # was for, and the fill's own confirmation vanished on the next keystroke.
    message = reg.register("_var_message", QLabel("", frame))
    message.setObjectName("VariablesMessage")
    message.setWordWrap(True)
    outer.addWidget(message)

    body = QWidget(frame)
    columns = QHBoxLayout(body)
    columns.setContentsMargins(0, 0, 0, 0)
    columns.setSpacing(theme.Spacing.lg)
    columns.addWidget(build_editor_column(ctx, body, reg))
    columns.addWidget(build_plate_column(ctx, body, reg), 1)
    outer.addWidget(body, 1)

    wire_variables(ctx)
    refresh_variables(ctx)


def _build_header(ctx, parent: QWidget, reg) -> QWidget:
    """``Editing Dose (µM) · colour   Also showing Cell line · letters   [Undo]``

    The plate is one widget answering to a list selection that also decides
    which variable the chips assign, which one the fill rewrites, and which
    definition is on screen. Four questions, one answer, and the answer was
    written down nowhere except the rail's highlight and a lead-in word on a
    control the user may never look at. A plate this page can hold two
    variables' worth of marks on needs to say whose marks they are.
    """
    host = reg.register("_var_header", QWidget(parent))
    host.setObjectName("VariablesHeader")
    row = QHBoxLayout(host)
    row.setContentsMargins(theme.Spacing.sm, theme.Spacing.xs,
                           theme.Spacing.sm, theme.Spacing.xs)
    row.setSpacing(theme.Spacing.sm)

    row.addWidget(reg.register("_var_showing_caption",
                               _caption(host, "SHOWING")))
    showing = reg.register("_var_showing", QLabel("", host))
    showing.setObjectName("VariablesHeaderValue")
    row.addWidget(showing)
    row.addStretch(1)

    undo = reg.register("_var_undo", QPushButton("↶  Undo", host))
    undo.setObjectName("HomeAction")
    undo.setToolTip("Put the variables back the way they were before the last "
                    "change on this tab.")
    row.addWidget(undo)
    return host


def _caption(parent: QWidget, text: str) -> QLabel:
    """A small tracked-out label naming the field beside it.

    The shape Groups' ACTIVE GROUP banner already uses, so the two definition
    tabs state "here is what you are working on" the same way.
    """
    label = QLabel(text, parent)
    label.setObjectName("VariablesCaption")
    return label


def section_label(parent: QWidget, text: str) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("HomeCardTitle")
    label.setAlignment(Qt.AlignLeft)
    return label
