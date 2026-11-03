"""The *Resolve* walk's guide card: a strip above the Nanowells figure, shown while a walk runs on the view its
timepoint is shown on, saying in three lines what is wrong with the timepoint, exactly what to do and where, and what
happens next (``resolve_compute.guide_for``, through ``_anchor_queue.current_guide``).

It carries the one action the class takes beside a click on the montage — *Looks right — next* for a partial
timepoint, *Exclude well* for one not located, *Re-run anchored wells…* for an anchor waiting — so the button the
*Do this* line names sits beside it. *Back*, *Skip* and *Stop*, with their keys (← → Esc), stay on the band's *Queue*
group directly above the card (``anchor_queue_view``).
"""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from review.features.nanowell import _anchor_queue as aq
from review.features.nanowell import controller as nwc

#: The card's class button, by ``resolve_compute.Guide.button``: its text and tooltip.
BUTTONS = {
    "next": ("Looks right — next", "Nothing needs fixing here: go on to the next timepoint of this class."),
    "exclude": ("Exclude well", "Leave this well out of every figure on this page (bring it back from Excluded on "
                                "the band), and go on to the next timepoint."),
    "rerun": ("Re-run anchored wells…", "Open Analyze on this dataset, limited to the wells with an anchor, each run "
                                        "again in full so its timepoints are located with the anchor."),
}
#: The three rows, by the ``Guide`` field each shows.
ROWS = (("problem", "Problem"), ("action", "Do this"), ("then", "Then"))


def _class_action(ctx, kind: str) -> None:
    if kind == "next":
        aq.step(ctx, +1)
    elif kind == "exclude":
        aq.exclude_current(ctx)
    elif kind == "rerun":
        nwc.rerun_anchored(ctx)


def build_resolve_guide(ctx, parent) -> SimpleNamespace:
    """The card, hidden until a walk runs; the page puts it between the band and the figure."""
    from ui import theme

    frame = QFrame(parent)
    frame.setObjectName("NanowellResolveGuide")
    frame.setAttribute(Qt.WA_StyledBackground, True)
    col = QVBoxLayout(frame)
    col.setContentsMargins(theme.Spacing.md, theme.Spacing.sm, theme.Spacing.md, theme.Spacing.sm)
    col.setSpacing(theme.Spacing.xs)

    head = QHBoxLayout()
    head.setSpacing(theme.Spacing.sm)
    kicker = QLabel("")
    kicker.setObjectName("NanowellResolveKicker")
    title = QLabel("")
    title.setObjectName("NanowellResolveTitle")
    action = QPushButton("")
    action.setProperty("variant", "primary")
    action.clicked.connect(lambda _=False: _class_action(ctx, action.property("kind") or ""))
    head.addWidget(kicker)
    head.addWidget(title)
    head.addStretch(1)
    head.addWidget(action)
    col.addLayout(head)

    grid = QGridLayout()
    grid.setHorizontalSpacing(theme.Spacing.md)
    grid.setVerticalSpacing(theme.Spacing.xs)
    bodies = {}
    for row, (key, term) in enumerate(ROWS):
        label = QLabel(term)
        label.setObjectName("NanowellResolveTerm")
        body = QLabel("")
        body.setObjectName("NanowellResolveBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid.addWidget(label, row, 0, Qt.AlignTop)
        grid.addWidget(body, row, 1)
        bodies[key] = body
    grid.setColumnStretch(1, 1)
    col.addLayout(grid)
    frame.hide()
    reg = ctx.widgets
    for name, widget in (("_nw_resolve_guide", frame), ("_nw_resolve_kicker", kicker), ("_nw_resolve_title", title),
                         ("_nw_resolve_action_btn", action)):
        reg.register(name, widget)
    for key, body in bodies.items():
        reg.register(f"_nw_resolve_{key}", body)
    return SimpleNamespace(frame=frame, kicker=kicker, title=title, bodies=bodies, action=action)


def refresh_resolve_guide(ctx, view: str, c: SimpleNamespace) -> None:
    """Shown while a walk runs and the page is on the view its timepoint is shown on, with that timepoint's text."""
    guide = aq.current_guide(ctx) if aq.current_view(ctx) == view else None
    c.frame.setVisible(guide is not None)
    if guide is None:
        return
    c.kicker.setText(guide.kicker)
    c.title.setText(guide.title)
    for key, body in c.bodies.items():
        body.setText(getattr(guide, key))
    text, tip = BUTTONS.get(guide.button, ("", ""))
    c.action.setVisible(bool(text))
    c.action.setText(text)
    c.action.setToolTip(tip)
    c.action.setProperty("kind", guide.button)
