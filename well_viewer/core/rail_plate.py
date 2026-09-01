"""The sidebar's one well-picker slot.

Three sidebar sections offer a well picker — the shared one in
``_sidebar_main_frame``, plus Segmentation and the Image Table — and each used
to build its plate into the top of its own section. That made a picker's
position a function of which section owned it, and the sections are stacked in
a fixed order, so the rail moved every time the focused pane changed: the
unfocused section collapsed by the height of the picker it was no longer
showing, and everything below it slid up with it. Measured on a 1600×1000
window with two plate-bearing panes side by side, the plot-groups drawer sat at
y=789 under one and y=456 under the other.

Every plate the slot arbitrates between answers the same question — which wells
am I looking at. The shared one selects wells to plot, Segmentation's picks the
one well to preview, the Image Table's picks the wells to tile. Statistics is
deliberately not here: a click on its plate writes a well into a named
comparison group, which is a different verb, and a slot that shows one plate at
a time turns two verbs into an invisible mode. Its editor is part of the
Statistics page instead (``features/stats/tab_view.build_stats_group_editor``).
A fourth plate that only ever *selects* would be a fine addition to the slot; a
fourth verb would not.

Reserving the missing picker's height in the section that is not showing one
removes the movement and puts back a picker-shaped hole. The two are the same
problem seen from either side, and neither is fixable while a picker's place on
the rail depends on whose section it is.

So the picker has one place. This module owns it: every rail plate is mounted
into a single slot that sits above the section frames, and exactly one is
visible at a time. The sections below hold only their own controls, so their
heights no longer depend on which pane has the focus, and nothing on the rail
moves when the focus changes.

The plate keeps its section: :func:`section_of` reports which frame a mounted
plate belongs to, which is how the tab controller decides whose picker to show.
That association is stored on the plate rather than in a roster here — a list
of "which sections have pickers" is a second place to update when a fifth one
grows one, and the guard against that kind of drift is not to keep the list.
"""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

#: Registry key for the slot widget itself.
SLOT_KEY = "_sidebar_plate_slot"


def build_rail_plate_slot(parent) -> QWidget:
    """The container every rail plate is mounted into. Built once, by the shell."""
    slot = QWidget(parent)
    lay = QVBoxLayout(slot)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    return slot


def mount_rail_plate(ctx, section, plate, *, alignment=None, fallback_layout=None) -> None:
    """Put *plate* in the shared slot, remembering which *section* it serves.

    *fallback_layout* is where the plate goes when there is no slot — a bare
    frame built by a test, or a profile whose shell was not assembled. Without
    it such a build would show no picker at all rather than an unshared one.
    """
    slot = ctx.widgets.get_or_none(SLOT_KEY) if ctx is not None else None
    layout = slot.layout() if slot is not None else None
    if layout is None:
        if fallback_layout is not None:
            _add(fallback_layout, plate, alignment)
        return
    plate.setParent(slot)
    _add(layout, plate, alignment)
    # Only the focused section's picker shows; the tab controller decides which
    # that is on every tab change, and until then a freshly built one must not
    # stack under the picker already on the rail.
    plate.setVisible(False)
    plate._aw_rail_section = section


def _add(layout, plate, alignment) -> None:
    if alignment is None:
        layout.addWidget(plate)
    else:
        layout.addWidget(plate, 0, alignment)


def section_of(plate):
    """The sidebar section frame *plate* serves, or ``None`` if it is unmounted."""
    return getattr(plate, "_aw_rail_section", None)


def rail_plates(ctx) -> list:
    """Every plate mounted in the shared slot, in mount order."""
    from widgets.well_plate_selector import WellPlateSelector
    slot = ctx.widgets.get_or_none(SLOT_KEY)
    if slot is None:
        return []
    return list(slot.findChildren(WellPlateSelector))


def settle_slot(ctx) -> None:
    """Make a just-changed picker visibility real before the rail is laid out.

    Hiding a widget only *posts* the layout invalidation that would reclaim its
    space, and the rail is laid out again before that event is delivered. The
    slot therefore went on reserving room for the picker it had just hidden:
    measured, returning the focus to the pane it started on left the slot 336px
    tall with both plates squeezed to 168 — the one on screen two thirds of the
    height it has in every other state. Clearing the caches here makes the hide
    current, and the focus states round-trip exactly.
    """
    slot = ctx.widgets.get_or_none(SLOT_KEY)
    layout = slot.layout() if slot is not None else None
    if layout is None:
        return
    layout.invalidate()
    layout.activate()
    outer_parent = slot.parentWidget()
    outer = outer_parent.layout() if outer_parent is not None else None
    if outer is not None:
        outer.invalidate()
        outer.activate()
