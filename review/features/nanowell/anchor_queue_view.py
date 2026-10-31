"""The Array view's *Anchor queue* controls: the button that starts it, and the group shown while it runs (where
it is, Back, Skip, Stop). The walk itself is ``_anchor_queue``."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QLabel, QPushButton

from review.features.nanowell import _anchor_queue as aq


def _button(text: str, tip: str, on_click) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("variant", "secondary")
    button.setToolTip(tip)
    button.clicked.connect(lambda _=False: on_click())
    return button


QUEUE_TIP = ("Walk every timepoint on the plate the run left unaddressed and that has no anchor yet, one montage "
             "after another: click the central square and the next one opens. Skip, Back and Stop sit beside it "
             "while it runs; the selection it started with is restored at the end.")


_PER_WELL_TIP = ("Only each well's first waiting timepoint. One anchor per well is usually enough: the re-run addresses "
                 "the well's other timepoints from it through the rest of the plate, and any it still cannot address "
                 "says so and waits for the next queue.")


def build_queue_controls(ctx, band) -> SimpleNamespace:
    """The *Anchor queue*'s own group, shown only while it runs: where it is, Back, Skip and Stop."""
    position = QLabel("")
    position.setObjectName("NanowellQueueLabel")
    back = _button("Back", "Show the previous timepoint in the queue; a click there replaces its anchor.",
                   lambda: aq.step(ctx, -1))
    skip = _button("Skip", "Leave this timepoint without an anchor and show the next one.", lambda: aq.step(ctx, +1))
    stop = _button("Stop", "End the queue here and restore the selection it started with.", lambda: aq.stop(ctx))
    per_well = QPushButton("One per well")
    per_well.setProperty("variant", "secondary")
    per_well.setCheckable(True)
    per_well.setToolTip(_PER_WELL_TIP)
    per_well.toggled.connect(lambda on: aq.set_per_well(ctx, on))
    for name, widget in (("_nw_queue_lbl", position), ("_nw_queue_back_btn", back), ("_nw_queue_skip_btn", skip),
                         ("_nw_queue_stop_btn", stop), ("_nw_queue_per_well_btn", per_well)):
        ctx.widgets.register(name, widget)
    group = band.add_scope("Queue", position, back, skip, per_well, stop)
    group.setVisible(False)
    return SimpleNamespace(group=group, position=position, back=back, skip=skip, stop=stop, per_well=per_well)


def refresh_queue_controls(ctx, st, on_view: bool, c: SimpleNamespace) -> None:
    queue = aq.active_queue(ctx)
    c.queue_btn.setEnabled(st.data is not None and queue is None)
    c.queue.group.setVisible(on_view and queue is not None)
    if queue is not None:
        c.queue.position.setText(queue.position_text())
        c.queue.back.setEnabled(queue.index > 0)
        c.queue.per_well.blockSignals(True)
        c.queue.per_well.setChecked(queue.per_well)
        c.queue.per_well.blockSignals(False)
