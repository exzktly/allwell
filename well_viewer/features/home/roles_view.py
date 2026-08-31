"""The two-line strip at the top of each definition tab: what this tab is for.

Home's definition tabs are siblings that all point at wells, and until this
strip existed none of them said what it *was*. Groups' hint explained the
mechanism ("click a group to make it active, then click wells") and never the
purpose; Variables said the right thing but only in the empty state, so it
vanished the moment a user defined one; Well Labels said nothing at all. A user
meeting three tabs that all take a well selection had no way to tell which one
would change a number.

The strip is built here and mounted by the Sample map tab, which rebuilds it
whenever the layer changes: the sentence is about what the plate below it means
*now*, so three strips stacked and hidden would be three answers with two of them
waiting for a bug to show them.

Two lines, from :data:`well_viewer.features.home.collect.DECLARATION_ROLES`:

* **the role** — what this declaration is and what it reaches, always visible
  rather than only while the tab is empty, because the question "which of these
  do I want" is asked again every time a plate is set up;
* **the cross-link** — where to go when the answer is one of the others. It is
  a sentence rather than a link, because sending a click to a sibling tab is
  navigation and the reader is mid-decision, not lost.

The copy lives in the roster, not here: three surfaces say these things (this
strip, Overview's effect column, the manual's comparison table) and the version
that lived in three places had already drifted apart.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

import theme
from well_viewer.features.home.collect import DeclarationRole


def build_role_strip(role: DeclarationRole, parent: QWidget) -> QWidget:
    """The strip for *role*, ready to add at the top of that tab's layout."""
    host = QWidget(parent)
    host.setObjectName("HomeRoleStrip")
    column = QVBoxLayout(host)
    column.setContentsMargins(0, theme.Spacing.sm, 0, theme.Spacing.sm)
    column.setSpacing(2)

    what = QLabel(role.role, host)
    what.setObjectName("HomeRoleText")
    what.setWordWrap(True)
    column.addWidget(what)

    where = QLabel(role.cross_link, host)
    where.setObjectName("Muted")
    where.setWordWrap(True)
    column.addWidget(where)
    return host
