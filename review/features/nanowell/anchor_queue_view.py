"""The *Anchor queue*'s walk controls: the group shown on the Array view while a walk runs (where it is, Back, Skip, One
per well, Stop), with keys for the three a long walk presses most (:data:`KEYS`). It is the band's first group, so it
sits at the band's left edge however the Array view's own groups wrap. The button that starts the walk is on the QC
view (``qc_view``); the walk itself is ``_anchor_queue``."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QLabel, QPushButton

from review.features.nanowell import _anchor_queue as aq


def _button(text: str, tip: str, on_click) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("variant", "secondary")
    button.setToolTip(tip)
    button.clicked.connect(lambda _=False: on_click())
    return button


QUEUE_TIP = ("Walk every timepoint that is not addressed and has no anchor yet: each opens on Array with Set anchor "
             "on — click the central square and the next one opens. Back, Skip, One per well and Stop sit at the left "
             "of the band while it runs (← → Esc), and you come back here at the end, with the selection it started "
             "with restored.")


PER_WELL_TIP = ("Only each well's first waiting timepoint. One anchor per well is usually enough: the re-run addresses "
                 "the well's other timepoints from it through the rest of the plate, and any it still cannot address "
                 "says so and waits for the next queue.")


#: The keys for Back, Skip and Stop. A walk is fifty clicks on the canvas, and reaching for a button between each is
#: most of its time. They live on the Queue group with a window context, so they act only while that group is
#: shown: during a walk, on the Array view. Arrow keys still reach a text field that has focus (Qt lets an editor
#: claim them first).
KEYS = {"back": Qt.Key_Left, "skip": Qt.Key_Right, "stop": Qt.Key_Escape}
_KEY_NAMES = {"back": "←", "skip": "→", "stop": "Esc"}


def _shortcut(group, name: str, on_key) -> QShortcut:
    shortcut = QShortcut(QKeySequence(KEYS[name]), group)
    shortcut.setContext(Qt.WindowShortcut)
    shortcut.activated.connect(on_key)
    return shortcut


def build_queue_controls(ctx, band) -> SimpleNamespace:
    """The *Anchor queue*'s own group, shown only while it runs: where it is, Back, Skip and Stop, each with a key."""
    position = QLabel("")
    position.setObjectName("NanowellQueueLabel")
    back = _button("Back", f"Show the previous timepoint in the queue; a click there replaces its anchor "
                   f"({_KEY_NAMES['back']}).", lambda: aq.step(ctx, -1))
    skip = _button("Skip", f"Leave this timepoint without an anchor and show the next one ({_KEY_NAMES['skip']}).",
                   lambda: aq.step(ctx, +1))
    stop = _button("Stop", f"End the queue here and restore the selection it started with ({_KEY_NAMES['stop']}).",
                   lambda: aq.stop(ctx))
    per_well = QPushButton("One per well")
    per_well.setProperty("variant", "toggle")       # "secondary" paints checked and unchecked the same
    per_well.setCheckable(True)
    per_well.setToolTip(PER_WELL_TIP)
    per_well.toggled.connect(lambda on: aq.set_per_well(ctx, on))
    for name, widget in (("_nw_queue_lbl", position), ("_nw_queue_back_btn", back), ("_nw_queue_skip_btn", skip),
                         ("_nw_queue_stop_btn", stop), ("_nw_queue_per_well_btn", per_well)):
        ctx.widgets.register(name, widget)
    group = band.add_scope("Queue", position, back, skip, per_well, stop)
    group.setVisible(False)
    # Back's key follows Back's button: nothing before the first item.
    keys = {"back": _shortcut(group, "back", lambda: back.isEnabled() and aq.step(ctx, -1)),
            "skip": _shortcut(group, "skip", lambda: aq.step(ctx, +1)),
            "stop": _shortcut(group, "stop", lambda: aq.stop(ctx))}
    ctx.widgets.register("_nw_queue_keys", keys)
    return SimpleNamespace(group=group, position=position, back=back, skip=skip, stop=stop, per_well=per_well,
                           keys=keys)


def refresh_queue_controls(ctx, on_array: bool, c: SimpleNamespace) -> None:
    """The walk's group: shown on the Array view while a walk runs, saying where it is."""
    queue = aq.active_queue(ctx)
    c.group.setVisible(on_array and queue is not None)
    if queue is not None:
        c.position.setText(queue.position_text())
        c.back.setEnabled(queue.index > 0)
        c.per_well.blockSignals(True)
        c.per_well.setChecked(queue.per_well)
        c.per_well.blockSignals(False)
