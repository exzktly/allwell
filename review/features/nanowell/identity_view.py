"""The Identity view's controls on the Nanowells page's band: the *Compare* mode switch, and the Blink timer.

Every control writes through ``_identity_controller``, which redraws; the timer belongs to the band, so it is
deleted with the page, and the controller starts it only while Blink frames are on screen and stops it on the first
tick after they are not (``_identity_controller.blink_tick``).
"""

from __future__ import annotations

from PySide6.QtCore import QTimer

from review.features.nanowell import _identity_controller as idc
from review.features.nanowell.state import IDENTITY_MODES

#: The modes as the switch lists them.
MODES = (("distance", "Distance map"), ("blink", "Blink"), ("sides", "Side by side"), ("diagnostics", "Diagnostics"))
_MODE_TIP = ("What the Identity view shows of the chosen timepoint against the well's reference timepoint:\n"
             "Distance map: each nanowell at its reference position, filled by how far its address lands from itself,\n"
             "in pitches; failing addresses outlined in red, the compared timepoint's tiles mapped onto the reference.\n"
             "Blink: the two montages alternating in the reference's frame — a correct mapping keeps the lattice still,\n"
             "and each frame's own address labels jump where the addresses name other nanowells.\n"
             "Side by side: the two montages; hover a nanowell to light its address on both, with the registration's\n"
             "prediction crossed.\n"
             "Diagnostics: the correlation surface of the registration on the central square, peak and runner-up.")


def build_identity_controls(ctx, band):
    """The *Compare* switch on *band* (shown only on the Identity view by the page's refresh) and the Blink timer;
    returns the switch's scope group."""
    from widgets.segmented_control import SegmentedControl

    seg = SegmentedControl()
    for key, label in MODES:
        seg.addSegment(text=label, data=key)
    seg.setToolTip(_MODE_TIP)
    seg.currentChanged.connect(lambda idx: idc.set_mode(ctx, MODES[idx][0]))
    ctx.widgets.register("_nw_identity_mode_seg", seg)
    timer = QTimer(band)
    timer.setInterval(idc.BLINK_MS)
    timer.timeout.connect(lambda: idc.blink_tick(ctx))
    ctx.widgets.register("_nw_blink_timer", timer)
    return seg, band.add_scope("Compare", seg)


def refresh_identity_controls(st, seg, group, on_view: bool) -> None:
    """The switch on the state's mode, shown only on the Identity view."""
    seg.setCurrentByData(st.identity_mode if st.identity_mode in IDENTITY_MODES else IDENTITY_MODES[0])
    group.setVisible(on_view)
