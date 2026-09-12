"""The Segmentation tab's frame-scope popover — one builder, two controls.

A drag in Delete mode normally acts on the one frame on screen, and a crop
normally acts on every frame in the dataset. Plate-scale artifacts — a dust
speck at a fixed stage position, a bright corner, a scratch on the plate bottom
— sit in the same image region across many wells, fields and timepoints, and
handling those one frame at a time means repeating an identical gesture hundreds
of times. Equally, a crop that should only clip the two wells whose edge was
imaged badly must not be forced onto the rest of the plate.

Both needs are the same control: three checkable dropdowns (wells, FOVs,
timepoints), a shortcut that arms the current plate selection, and a reset. What
differs between them is carried by the :class:`~review.core.frame_scope.ScopeSpec`
— which typed-state fields to write, what an un-armed field means, and what to
re-derive after a change. The Qt-free half (options, readout, state writes) is
:mod:`review.core.frame_scope`; this module is the widget.
"""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from review.core.frame_scope import (
    ScopeSpec, apply_scope_change, current_frame, read_scope,
    refresh_scope_button, reset_scope, scope_options, write_scope_field,
)
from core.safe import swallowed
from review.core.ui_helpers import make_popover_button
from widgets.checkable_dropdown import CheckableDropdown

# Width the three dropdowns share, so they line up as a column. This is what a
# caption label used to supply as a side effect of its own minimum width; the
# alignment was worth keeping, the caption was not — see below.
_FIELD_W = 190

# How long a burst of ticks is allowed to run together before the spec's
# ``on_change`` fires. A ``CheckableDropdown`` emits ``changed`` once per item,
# and the crop's on_change re-projects ``Crop_Pass`` over every cached well,
# redraws the active plot and re-renders the canvas — so ticking eleven wells
# one at a time ran eleven full recomputes on the UI thread. The state and the
# button still update on every tick; only the expensive half waits. Long enough
# to swallow a run of clicks, short enough that letting go of the mouse and
# looking at the plot behind the popover shows the new numbers.
_COMMIT_DEBOUNCE_MS = 400


def build_frame_scope_control(ctx, ctrl: QWidget, spec: ScopeSpec):
    """Build *spec*'s scope popover button for the Segmentation control band."""
    btn, panel, lay = make_popover_button(
        f"{spec.title}: {spec.unarmed_text}", ctrl)
    btn.setToolTip(spec.tooltip)
    ctx.widgets.register(spec.button_key, btn)

    hint = QLabel(spec.hint, panel)
    hint.setObjectName("Muted")
    lay.addWidget(hint)

    # Each dropdown names its own field — "2 of 3 timepoints", "No wells" — so a
    # caption beside it printed the same word twice. That is the pattern every
    # other Timepoint control in Review folded away, and
    # ``test_timepoint_control_folding`` fails on a stranded caption label. The
    # hint above says what the three fields are *for*; the buttons say which is
    # which, in every state including un-armed (``_seed`` ticks either the
    # displayed frame's value or every option, so a button reads "1 of 3
    # timepoints" rather than going blank).
    wells_dd = CheckableDropdown(panel, noun="wells", empty_text="No wells")
    fovs_dd = CheckableDropdown(panel, noun="FOVs", empty_text="No FOVs")
    tps_dd = CheckableDropdown(panel, noun="timepoints", empty_text="No timepoints")
    dropdowns = (wells_dd, fovs_dd, tps_dd)
    for _dd in dropdowns:
        _dd.setMinimumWidth(_FIELD_W)
        lay.addWidget(_dd)

    def _seed(dropdown: CheckableDropdown, options, armed, current) -> None:
        """Refill *dropdown* and restore its ticks without emitting ``changed``.

        When the field is un-armed (``None``) the popover shows what that field
        currently means — the displayed frame's value for a "this frame" spec,
        every option for an "all frames" one — so it never reads as an empty
        list. The state stays ``None``, so nothing is armed until the user
        actually changes something.
        """
        if armed is None:
            ticks = [str(current)] if spec.seed_current_frame else [
                str(o) for o in options]
        else:
            ticks = [str(v) for v in armed]
        blocked = dropdown.blockSignals(True)
        try:
            dropdown.clear()
            for opt in options:
                dropdown.addItem(str(opt), str(opt))
            dropdown.setCheckedData(ticks)
        finally:
            dropdown.blockSignals(blocked)

    def _repopulate() -> None:
        frame = current_frame(ctx)
        options = scope_options(ctx)
        armed = read_scope(ctx, spec)
        for dropdown, opts, arm, cur in zip(dropdowns, options, armed, frame):
            # An un-armed "this frame" field seeds from the displayed frame; make
            # sure that value is offered even when the caches have not been
            # rebuilt for it yet.
            _seed(dropdown, opts or ([cur] if cur else []), arm, cur)
        refresh_scope_button(ctx, spec)

    # The expensive half of a commit, coalesced across a burst of ticks. Parented
    # to the popover so it dies with it — a timer outliving the panel would fire
    # a redraw into a torn-down tab.
    settle = QTimer(panel)
    settle.setSingleShot(True)
    settle.setInterval(_COMMIT_DEBOUNCE_MS)
    # Tracked here rather than by asking the timer, because the one moment this
    # has to be right — the panel being destroyed — is the moment the timer is
    # being destroyed too.
    pending: list[bool] = [False]

    def _apply_now() -> None:
        pending[0] = False
        apply_scope_change(ctx, spec)

    settle.timeout.connect(_apply_now)

    def _apply_on_teardown() -> None:
        """Do not let a pending change die with the widget that scheduled it.

        The state is already written and the button already relabelled, so
        dropping this would leave the row cache projected against a scope the
        user can see armed — silently, for the rest of the session. The teardown
        is why ``crop.controller`` guards its redraw: the widgets it would
        refresh may be gone, and its own ``swallowed()`` handling covers that.
        """
        if not pending[0]:
            return
        pending[0] = False
        try:
            apply_scope_change(ctx, spec)
        except Exception:
            swallowed()

    panel.destroyed.connect(lambda *_a: _apply_on_teardown())

    def _commit(index: int, dropdown: CheckableDropdown) -> None:
        """Write one field now; let the re-derivation settle.

        ``write_scope_field`` is given no ``on_change`` here — the state write
        and the button relabel are cheap and must land on every tick, while the
        spec's re-derivation (for the crop: re-project every cached well,
        redraw, re-render) waits for the burst to finish.
        """
        write_scope_field(ctx, spec, index, dropdown.checkedData(), apply=False)
        refresh_scope_button(ctx, spec)
        if spec.on_change is not None:
            pending[0] = True
            settle.start()

    def _flush() -> None:
        """Apply a pending change immediately (the popover is closing)."""
        if pending[0]:
            settle.stop()
            _apply_now()

    for _i, _dd in enumerate(dropdowns):
        _dd.changed.connect(partial(_commit, _i, _dd))

    plate_btn = QPushButton("Use plate selection", panel)
    plate_btn.setProperty("variant", "secondary")
    plate_btn.setToolTip(
        "Arm the wells currently selected on the plate. The plate selection "
        "itself is not changed."
    )

    def _use_plate_selection() -> None:
        # Read-only on the app-wide selection: writing it here would leak this
        # tab's scope into every other tab (see the plate-selection note in
        # ARCHITECTURE §15).
        chosen = [w for w in ctx.state.selection.selected_wells
                  if w in ctx.state.dataset.well_paths]
        if not chosen:
            ctx.notify.set_status(spec.plate_status_empty)
            return
        write_scope_field(ctx, spec, 0, chosen, apply=False)
        _repopulate()
        # This message first, then the re-derivation, so the line the user is
        # left with is the one carrying the numbers ("now excluding N of M").
        # The button already reads back how many wells were armed.
        ctx.notify.set_status(spec.plate_status_ok.format(n=len(chosen)))
        settle.stop()
        _apply_now()

    plate_btn.clicked.connect(lambda _=False: _use_plate_selection())
    lay.addWidget(plate_btn)

    reset_btn = QPushButton(spec.reset_label, panel)
    reset_btn.setProperty("variant", "secondary")
    reset_btn.setToolTip(spec.reset_tooltip)

    def _reset() -> None:
        settle.stop()
        pending[0] = False
        reset_scope(ctx, spec)
        _repopulate()
        ctx.notify.set_status(spec.reset_status)

    reset_btn.clicked.connect(lambda _=False: _reset())
    lay.addWidget(reset_btn)

    menu = btn.menu()
    if menu is not None:
        menu.aboutToShow.connect(_repopulate)
        # Closing the popover is the user saying they are done ticking, so the
        # pending re-derivation runs then rather than waiting out its timer.
        menu.aboutToHide.connect(_flush)
    return btn
