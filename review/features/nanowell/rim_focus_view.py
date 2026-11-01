"""The Nanowells page's rim focus control on its band: **Rim focus** (on / off) and the fraction of the well's sharp
rims a nanowell must reach, as a percentage.

A mirror of the Cell Gating panel's nanowell focus switch, never a setting of its own: both write the dataset's QC
parameters (``QCParams.exclude_soft_nanowells`` / ``nanowell_focus_fraction``) through the controller's
``set_rim_focus``, so the cells of an out-of-focus nanowell leave every tab (``NW_Focus_Pass``) when the nanowell
leaves this page. ``review.domain.nanowell_rim_focus`` decides which nanowells are out of focus.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QCheckBox

from review.features.nanowell import controller as nwc
from review.domain import nanowell_rim_focus as rfc
from widgets.spin_box import SpinBox

RIM_FOCUS_TIP = (
    "Leave out a nanowell at any timepoint where its own rim was out of focus — its tile was soft while others in "
    "the well were sharp. Each nanowell's rim sharpness is measured by every nanowell run (the focus column of "
    "nanowells/<well>.csv), so this needs no re-run.\n\n"
    "A nanowell is out of focus when its rim is less sharp than this percentage of the sharpest tenth of its "
    "well's rims at that timepoint. There it has no count, confluence or fluorescence, no fold change from or to "
    "it and no growth rate through it; the Array view hatches it with dots, the nanowell's detail names it, and the "
    "figure's note counts what was left out.\n\n"
    "Not seen: a timepoint blurred evenly everywhere (nothing sharp to compare with), and a nanowell whose rim "
    "could not be fitted (no rim focus; kept, and counted in the note).\n\n"
    "The same switch as Cell Gating's \"Exclude out-of-focus nanowells\": ticking either ticks both, and the cells of "
    "an out-of-focus nanowell leave every tab in Review — Plotting, Scatter, histograms and exports — not only this "
    "page.")


@dataclass
class RimFocusControls:
    box: object
    spin: object

    @property
    def signalling(self) -> tuple:
        return (self.box, self.spin)

    def refresh(self, ctx) -> None:
        rule = nwc.focus_rule(ctx)
        self.box.setChecked(bool(rule.enabled))
        self.spin.setValue(int(round(100 * rule.fraction)))
        self.spin.setEnabled(bool(rule.enabled))


def build_rim_focus_controls(ctx) -> RimFocusControls:
    """**Rim focus** and its percentage; the page places both on its band."""
    box = QCheckBox("Rim focus")
    box.setToolTip(RIM_FOCUS_TIP)
    spin = SpinBox()
    spin.setRange(int(round(100 * rfc.MIN_FRACTION)), int(round(100 * rfc.MAX_FRACTION)))
    # A bare number, no suffix: Qt's spin box refuses typing and Backspace while the cursor sits in a suffix,
    # and a long one (" % of sharp") filled most of the field, so a click there ignored every digit typed.
    spin.setToolTip(RIM_FOCUS_TIP)
    spin.setKeyboardTracking(False)          # recompute once on Enter or focus-out, not on every digit typed
    box.toggled.connect(lambda on: nwc.set_rim_focus(ctx, on=on))
    spin.valueChanged.connect(lambda value: nwc.set_rim_focus(ctx, fraction=value / 100.0))
    reg = ctx.widgets
    reg.register("_nw_rim_focus_box", box)
    reg.register("_nw_rim_focus_spin", spin)
    return RimFocusControls(box, spin)
