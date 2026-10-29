"""The Identity view's controls on the Nanowells page's band: the *Compare* mode switch, the Distance map's *Colour*
switch, and the Blink timer.

Every control writes through the controller, which redraws; the timer belongs to the band, so it is deleted with the
page, and the controller starts it only while Blink frames are on screen and stops it on the first tick after they
are not (``_identity_controller.blink_tick``).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QTimer

from review.features.nanowell import _identity_controller as idc
from review.features.nanowell import controller as nwc
from review.features.nanowell.state import IDENTITY_COLOURS, IDENTITY_MODES

#: The modes as the switch lists them.
MODES = (("distance", "Distance map"), ("blink", "Blink"), ("sides", "Side by side"), ("diagnostics", "Diagnostics"))
#: What the Distance map colours by, as its switch lists them (``state.IDENTITY_COLOURS``).
COLOURS = (("auto", "Auto"), ("status", "Status"), ("distance", "Distance"))
_MODE_TIP = ("What the Identity view shows of the chosen timepoint against the well's reference timepoint:\n"
             "Distance map: each nanowell at its reference position, coloured by its status or by how far its address\n"
             "lands from itself; the compared timepoint's tiles mapped onto the reference.\n"
             "Blink: the two montages alternating in the reference's frame — a correct mapping keeps the lattice still,\n"
             "and each frame's own address labels jump where the addresses name other nanowells.\n"
             "Side by side: the two montages; hover a nanowell to light its address on both, with the registration's\n"
             "prediction crossed.\n"
             "Diagnostics: the central square looked for, where it was found and how clearly, and every check the\n"
             "verdict rests on with its numbers and thresholds.\n"
             "Every mode zooms: the wheel about the pointer, drag to pan, double-click for the whole view.")
_COLOUR_TIP = ("What the Distance map colours each nanowell by:\n"
               "Status: lands on itself, imprecise, wrong name, in a tile placed a lattice step off, or not compared —\n"
               "with a count of each, so the nanowells left out are seen at once.\n"
               "Distance: how far its address lands from itself, in nanowell spacings.\n"
               "Auto: Status unless the timepoint is confirmed.")


@dataclass
class IdentityControls:
    """The Identity view's band controls and the scope groups that show or hide them."""
    mode: object
    colour: object
    mode_group: object
    colour_group: object

    @property
    def signalling(self) -> tuple:
        return self.mode, self.colour


def _segments(items, tip: str, on_choice):
    from widgets.segmented_control import SegmentedControl

    seg = SegmentedControl()
    for key, label in items:
        seg.addSegment(text=label, data=key)
    seg.setToolTip(tip)
    seg.currentChanged.connect(lambda idx: on_choice(items[idx][0]))
    return seg


def build_identity_controls(ctx, band) -> IdentityControls:
    """The *Compare* and *Colour* switches on *band* (shown only on the Identity view by the page's refresh, the
    colouring only on the Distance map) and the Blink timer."""
    seg = _segments(MODES, _MODE_TIP, lambda key: nwc.set_identity_mode(ctx, key))
    ctx.widgets.register("_nw_identity_mode_seg", seg)
    colour = _segments(COLOURS, _COLOUR_TIP, lambda key: nwc.set_identity_colour(ctx, key))
    ctx.widgets.register("_nw_identity_colour_seg", colour)
    timer = QTimer(band)
    timer.setInterval(idc.BLINK_MS)
    timer.timeout.connect(lambda: idc.blink_tick(ctx))
    ctx.widgets.register("_nw_blink_timer", timer)
    return IdentityControls(seg, colour, band.add_scope("Compare", seg), band.add_scope("Colour", colour))


def refresh_identity_controls(st, controls: IdentityControls, on_view: bool) -> None:
    """The switches on the state's choices, shown only on the Identity view (the colouring on its Distance map)."""
    mode = st.identity_mode if st.identity_mode in IDENTITY_MODES else IDENTITY_MODES[0]
    controls.mode.setCurrentByData(mode)
    controls.colour.setCurrentByData(st.identity_colour if st.identity_colour in IDENTITY_COLOURS
                                     else IDENTITY_COLOURS[0])
    controls.mode_group.setVisible(on_view)
    controls.colour_group.setVisible(on_view and mode == "distance")
