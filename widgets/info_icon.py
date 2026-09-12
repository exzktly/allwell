"""A small ``ⓘ`` marker whose hover tooltip carries a longer explanation.

The app's affordance for "there is more to say about this control than fits
beside it". Without a marker a tooltip is invisible: nothing on screen tells you
there is anything to hover, so the explanation only reaches the people who were
already going to guess right.

Lives in ``widgets/`` — no All-Well knowledge, no ``review`` import, just a
styled ``QLabel`` — so both the Analyze pane and the Review pane's tabs can use
the one implementation. Styling is by object name (``InfoHint``) rather than a
hardcoded colour, per the QSS convention.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget


def info_icon(parent: QWidget, info: str) -> QLabel:
    """An ``ⓘ`` label for *parent* whose tooltip (and WhatsThis) is *info*.

    The WhatsThis copy is not redundant: it surfaces the same note through the
    ?-cursor, which is the route that works for someone who cannot hold a hover.
    """
    lbl = QLabel("ⓘ", parent)
    lbl.setObjectName("InfoHint")
    lbl.setToolTip(info)
    lbl.setWhatsThis(info)
    return lbl
