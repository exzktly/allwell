"""The three buttons every band ends in: Display, Style, Share.

No kind of control had a fixed place: the panel of display options was a
``Display ▾`` on seven pages, a ``View ▾`` on one and a ``Layers`` menu on
another; figure styling was an unlabelled icon; saving was ``Save ▾`` here and
``Export ▾`` there. Now a band's action zone ends in the same three buttons on
every page, in the same order:

* **Display** — opens the inspector on its Display tab (the page's root panel
  of display options, ``inspector_panels.make_popover_button(..., root=True)``).
* **Style** — opens the inspector on its Style tab (the figure's export look,
  ``ui_helpers.make_style_button``).
* **Share** — a menu of commands: save the figure, copy it, save its data
  (``ui_helpers.build_plot_save_menu``).

A page builds the ones it has the way it always did, and then calls
:func:`finish_band` once. That is the one factory: it finds the page's three
by the role each builder declared, adds a **disabled** button for any the page
does not have — a slot a page cannot use is disabled, never removed, so the
row has one shape — and moves the three to the end of the row.

On a band shared by several leaves (the Plotting page's), each leaf finishes
its own slot, so each leaf has its own three and the band shows one leaf's at
a time.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton, QWidget

from widgets.control_band import ControlBand, ControlBandSlot
from widgets.control_band_form import ROLE_PROPERTY

#: The fixed buttons, in the order they end the row.
FIXED_ROLES: tuple[str, ...] = ("display", "style", "share")
TITLES = {"display": "Display", "style": "Style", "share": "Share"}

#: What a placeholder says when the pointer rests on it.
_NOTHING = {
    "display": "This page has no display options.",
    "style": "This page has no figure to style.",
    "share": "This page has nothing to save or copy.",
}

#: Property marking a button :func:`finish_band` added for a slot the page
#: does not use.
PLACEHOLDER_PROPERTY = "bandPlaceholder"


def role_of(widget: QWidget) -> str:
    role = widget.property(ROLE_PROPERTY)
    return role if isinstance(role, str) else ""


def _rank(widget: QWidget) -> int:
    role = role_of(widget)
    return FIXED_ROLES.index(role) + 1 if role in FIXED_ROLES else 0


def _owned(target) -> list[QWidget]:
    """The actions *target* — a band or one leaf's slot of a band — owns."""
    if isinstance(target, ControlBandSlot):
        mine = set(map(id, target.items()))
        return [w for w in target.band.actions() if id(w) in mine]
    claimed: set[int] = set()
    for name in target.slot_names():
        claimed |= set(map(id, target.slot(name).items()))
    return [w for w in target.actions() if id(w) not in claimed]


def _placeholder(parent: QWidget, role: str) -> QToolButton:
    btn = QToolButton(parent)
    btn.setText(TITLES[role])
    btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
    btn.setProperty("variant", "default")
    btn.setProperty(ROLE_PROPERTY, role)
    btn.setProperty(PLACEHOLDER_PROPERTY, True)
    btn.setToolTip(_NOTHING[role])
    btn.setEnabled(False)
    return btn


def finish_band(target) -> dict[str, QWidget]:
    """End *target*'s action row in Display · Style · Share.

    *target* is the page's :class:`~widgets.control_band.ControlBand`, or its
    slot of a shared one. Call it once, after the page has added its actions.
    Returns the three buttons by role."""
    band = target.band if isinstance(target, ControlBandSlot) else target
    if not isinstance(band, ControlBand):
        raise TypeError("finish_band needs a ControlBand or one of its slots")
    have = {role_of(w): w for w in _owned(target) if role_of(w) in FIXED_ROLES}
    for role in FIXED_ROLES:
        if role not in have:
            have[role] = target.add_action(_placeholder(band, role))
    band.order_actions(_rank)
    return {role: have[role] for role in FIXED_ROLES}


def fixed_actions(target) -> list[QWidget]:
    """*target*'s three fixed buttons, in the order the row holds them."""
    return [w for w in _owned(target) if role_of(w) in FIXED_ROLES]
