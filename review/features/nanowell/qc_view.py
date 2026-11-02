"""The QC view's controls on the band: *Wells* (a row for each well needing a look, or for every well) and the
*Anchor* group — **Anchor queue**, **One per well** and **Re-run anchored wells…** — which is where the anchor walk
starts and where it comes back to (``_anchor_queue``). The walk's own controls (where it is, Back, Skip, Stop) are
``anchor_queue_view``'s, shown on the Array view while it runs."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QCheckBox, QPushButton

from review.features.nanowell import _anchor_queue as aq
from review.features.nanowell import _qc_controller as qcc
from review.features.nanowell import controller as nwc
from review.features.nanowell import full_montage_compute as fmc
from review.features.nanowell import qc_compute as qc
from review.features.nanowell.anchor_queue_view import PER_WELL_TIP, QUEUE_TIP

_WELLS = (("needing", "Needing a look"), ("all", "All"))
WELLS_TIP = ("Which wells get a row: only those with a timepoint that is not confirmed, or every well with a grid "
             "record. The legend counts every well either way.")
RERUN_TIP = ("Open Analyze on this dataset, limited to the wells with an anchor, each run again in full so its "
             "timepoints are located with the anchor.")
_RERUN_NONE = "No well has an anchor set: there is nothing for a re-run to use."


def _button(text: str, tip: str, on_click, *, variant: str = "secondary") -> QPushButton:
    button = QPushButton(text)
    button.setProperty("variant", variant)
    button.setToolTip(tip)
    if on_click is not None:
        button.clicked.connect(lambda _=False: on_click())
    return button


def build_qc_controls(ctx, band) -> SimpleNamespace:
    """The QC view's two groups, hidden on every other view."""
    from widgets.segmented_control import SegmentedControl

    reg = ctx.widgets
    wells_seg = SegmentedControl()
    for key, label in _WELLS:
        wells_seg.addSegment(text=label, data=key)
    wells_seg.setToolTip(WELLS_TIP)
    wells_seg.currentChanged.connect(lambda idx: qcc.set_wells(ctx, _WELLS[idx][0]))
    reg.register("_nw_qc_wells_seg", wells_seg)
    grp_wells = band.add_scope("Wells", wells_seg)

    # The page's one action: primary, and saying what it walks, so the option beside it visibly changes something.
    queue_btn = _button("Anchor queue", QUEUE_TIP, lambda: aq.start(ctx), variant="primary")
    # An option of the walk, not an action: a checkbox, which no one presses expecting a walk to start.
    per_well = QCheckBox("One per well")
    per_well.setToolTip(PER_WELL_TIP)
    per_well.toggled.connect(lambda on: _set_per_well(ctx, on))
    walk_btn, walk_actions = _walk_button(ctx, band)
    rerun_btn = _button("Re-run anchored wells…", RERUN_TIP, lambda: nwc.rerun_anchored(ctx))
    for name, widget in (("_nw_queue_btn", queue_btn), ("_nw_qc_per_well_btn", per_well),
                         ("_nw_qc_rerun_btn", rerun_btn), ("_nw_qc_walk_btn", walk_btn)):
        reg.register(name, widget)
    grp_anchor = band.add_scope("Anchor", queue_btn, walk_btn, per_well, rerun_btn)
    return SimpleNamespace(wells_seg=wells_seg, queue_btn=queue_btn, per_well=per_well, rerun_btn=rerun_btn,
                           walk_btn=walk_btn, walk_actions=walk_actions, groups=(grp_wells, grp_anchor),
                           signalling=(wells_seg, per_well, *walk_actions.values()))


def _walk_button(ctx, parent):
    """*Walk ▾*: which states *Anchor queue* steps through, each with its count on the plate. Not addressed,
    contradicted and inconclusive open with *Set anchor* on (``qc_compute.ARMED``); partial and not located open to
    be looked at."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMenu, QToolButton

    from widgets.owned_action import owned_action

    button = QToolButton(parent)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.setPopupMode(QToolButton.InstantPopup)
    button.setProperty("variant", "secondary")
    button.setProperty("hasMenu", True)
    button.setToolTip(WALK_TIP)
    menu = QMenu(button)
    button.setMenu(menu)
    actions = {}
    for state in qc.WALKABLE:
        action = owned_action(menu, qc.WORDS[state])
        action.setCheckable(True)
        action.toggled.connect(lambda on, state=state: _set_walk(ctx, state, on))
        ctx.widgets.register(f"_nw_qc_walk_{state}", action)
        actions[state] = action
    return button, actions


def _set_walk(ctx, state: str, on: bool) -> None:
    st = ctx.state.feature_slice("nanowell")
    chosen = (set(st.anchor_queue_states) | {state}) if on else (set(st.anchor_queue_states) - {state})
    st.anchor_queue_states = tuple(s for s in qc.WALKABLE if s in chosen)
    nwc._call(ctx, "_nw_refresh_controls")             # the button's count and the figure's sentence follow it
    nwc.redraw(ctx)


WALK_TIP = ("Which timepoints Anchor queue steps through, by their state on the plate. Not addressed, contradicted "
            "and inconclusive open with Set anchor on: click the central square and the next one opens. Partial and "
            "not located open to be looked at, with Set anchor off: → goes on to the next one.")


def _set_per_well(ctx, on: bool) -> None:
    aq.set_per_well(ctx, on)
    nwc._call(ctx, "_nw_refresh_controls")             # the button's count follows the choice


def refresh_qc_controls(ctx, st, on_view: bool, c: SimpleNamespace) -> None:
    """Shown only on the QC view; *Anchor queue* greyed, saying why, when nothing waits for an anchor."""
    for group in c.groups:
        group.setVisible(on_view)
    if not on_view:
        return
    c.wells_seg.setCurrentByData(st.qc_wells)
    c.per_well.setChecked(st.anchor_queue_per_well)
    matrix = qcc.matrix(ctx)
    states = st.anchor_queue_states
    for state, action in c.walk_actions.items():
        action.setChecked(state in states)
        action.setText(f"{qc.WORDS[state]} ({matrix.counts.get(state, 0) if matrix is not None else 0:,})")
    c.walk_btn.setText(f"Walk: {len(states)} state{'s' if len(states) != 1 else ''}")
    c.walk_btn.setToolTip(f"{WALK_TIP}\n\nNow: {qc.walk_words(states)}.")
    why = qc.queue_tip(matrix, states)
    c.queue_btn.setText(qc.queue_label(matrix, st.anchor_queue_per_well, states))
    c.queue_btn.setEnabled(not why and aq.active_queue(ctx) is None)
    c.queue_btn.setToolTip(QUEUE_TIP + (f"\n\n{why}" if why else ""))
    anchored = bool(fmc.anchored_wells(st.data)) if st.data is not None else False
    c.rerun_btn.setEnabled(anchored)
    c.rerun_btn.setToolTip(RERUN_TIP if anchored else f"{RERUN_TIP}\n\n{_RERUN_NONE}")
