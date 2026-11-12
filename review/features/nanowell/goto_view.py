"""The Nanowells page band's **Nanowell** box and **Origin** button: go to one nanowell on any view
(``_goto_controller``).

One control in one place on every view — the first of the viewing row — so the same address goes to the same
nanowell whichever view is open. Greyed, saying why, on the one view with no nanowell to go to (Array QC).
"""

from __future__ import annotations

from dataclasses import dataclass

from review.features.nanowell import _goto_controller as gtc
from review.features.nanowell.identity_focus_compute import FOCUS_MODES

_TIP = ("Go to one nanowell: type its address and press Enter. Write it as the addresses are drawn, q,r (for\n"
        "example -2,3), or as a ring label such as 3.7, and lead it with a well to go to another well: B04 2,-1.\n"
        "The nanowell is selected on every view: its plots over time and its crops open in the site detail, and the\n"
        "Distribution and Array views ring it. The Array view zooms onto it; the Identity view's Distance map, Blink\n"
        "and Side by side zoom onto it and keep it as you change the timepoint, the well or the mode, with a ring\n"
        "where each timepoint puts the address and a cross where the other timepoint's address of that name lands.\n"
        "Clear the box to take the Identity view back to its whole view.")
_DIAGNOSTICS_TIP = _TIP + ("\n\nDiagnostics draws the central square and its match, not the array: the nanowell is selected "
                           "but not zoomed onto there.")
_OFF_TIP = "Array QC draws one row per well, not nanowells: there is no nanowell to go to on this view."
_ORIGIN_TIP = "Go to the origin: nanowell 0,0, the centre of the array the addresses count from (ring 0)."


@dataclass
class GotoControls:
    edit: object
    origin: object
    group: object

    @property
    def signalling(self) -> tuple:
        return (self.edit,)


def build_goto_controls(ctx, band) -> GotoControls:
    """The box and **Origin** on *band*, as one scope."""
    from PySide6.QtWidgets import QLineEdit, QPushButton

    edit = QLineEdit()
    edit.setPlaceholderText("q,r")
    edit.setClearButtonEnabled(True)
    edit.setToolTip(_TIP)
    edit.setMaxLength(32)
    edit.setFixedWidth(edit.fontMetrics().horizontalAdvance("M" * 9))
    edit.returnPressed.connect(lambda: gtc.go_to_nanowell(ctx, edit.text()))
    edit.textChanged.connect(lambda text: gtc.go_to_nanowell(ctx, "") if not text.strip() else None)
    ctx.widgets.register("_nw_goto_edit", edit)
    origin = QPushButton("Origin")
    origin.setProperty("variant", "secondary")
    origin.setToolTip(_ORIGIN_TIP)
    origin.clicked.connect(lambda _=False: go_to(ctx, edit, "0,0"))
    ctx.widgets.register("_nw_goto_origin_btn", origin)
    return GotoControls(edit, origin, band.add_scope("Nanowell", edit, origin))


def go_to(ctx, edit, text: str) -> None:
    """Write *text* in the box and go to it, as typing it and pressing Enter does."""
    edit.setText(text)
    gtc.go_to_nanowell(ctx, text)


def refresh_goto_controls(st, controls: GotoControls, view: str) -> None:
    """Shown on every view; greyed on Array QC; the box shows the address last gone to, unless the reader is
    typing in it."""
    goes = view not in gtc.NO_GOTO_VIEWS
    controls.group.setVisible(True)
    controls.group.setEnabled(goes)
    zooms = view != "identity" or st.identity_mode in FOCUS_MODES
    controls.edit.setToolTip(_TIP if goes and zooms else _DIAGNOSTICS_TIP if goes else _OFF_TIP)
    if not controls.edit.hasFocus() and controls.edit.text().strip() != st.identity_focus:
        controls.edit.setText(st.identity_focus)          # the reader's typing is never overwritten
