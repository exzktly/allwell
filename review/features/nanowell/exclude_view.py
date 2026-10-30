"""The Nanowells page's well exclusion on its band: **Exclude** beside the Well combo, and *Excluded (n) ▾*, which
brings wells back.

Every control writes through the controller (``set_well_excluded``, ``restore_every_well``), which keeps the list on
``NanowellState.excluded_wells`` and redraws; ``controller.page_groups`` is what leaves the wells out.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QMenu, QPushButton, QToolButton

from review.features.nanowell import controller as nwc

_EXCLUDE_TIP = ("Leave this well out of every figure on this page — Distribution, Trajectories, Position, Extremes, "
                "Array and Identity — and out of Save nanowell data. Each figure then says which wells are left out.\n"
                "Only this page: the well stays in the rest of Review. Bring it back from Excluded on the band.")
_EXCLUDED_TIP = "The wells left out of this page's figures. Choose one to bring it back."


@dataclass
class ExcludeControls:
    """**Exclude** (placed in the Well scope by the page) and the *Excluded* menu with its own scope."""
    ctx: object
    exclude: object
    excluded: object
    excluded_group: object = None

    def refresh(self, st, well_cb) -> None:
        """**Exclude** usable while a well is chosen; *Excluded* listing the excluded wells, shown only when any is."""
        self.exclude.setEnabled(bool(well_cb.currentData()))
        fill_excluded(self.excluded, st.excluded_wells, self.ctx)
        if self.excluded_group is not None:
            self.excluded_group.setVisible(bool(st.excluded_wells))


def build_exclude_controls(ctx, well_cb) -> ExcludeControls:
    """**Exclude**, acting on *well_cb*'s well, and the *Excluded* menu; the page places both on its band."""
    exclude = QPushButton("Exclude")
    exclude.setProperty("variant", "secondary")
    exclude.setToolTip(_EXCLUDE_TIP)
    exclude.clicked.connect(lambda _=False: nwc.set_well_excluded(ctx, well_cb.currentData() or "", True))
    ctx.widgets.register("_nw_exclude_well_btn", exclude)
    excluded = QToolButton()
    excluded.setPopupMode(QToolButton.InstantPopup)
    excluded.setToolTip(_EXCLUDED_TIP)
    excluded.setMenu(QMenu(excluded))
    ctx.widgets.register("_nw_excluded_wells_btn", excluded)
    return ExcludeControls(ctx, exclude, excluded)


def fill_excluded(button, wells, ctx) -> None:
    """*Excluded (n)*: one entry per excluded well that brings it back, and one that brings back all of them."""
    button.setText(f"Excluded ({len(wells)}) ▾")
    menu = button.menu()
    menu.clear()
    for well in wells:
        menu.addAction(f"Restore {well}", lambda w=well: nwc.set_well_excluded(ctx, w, False))
    if len(wells) > 1:
        menu.addSeparator()
        menu.addAction("Restore every well", lambda: nwc.restore_every_well(ctx))
