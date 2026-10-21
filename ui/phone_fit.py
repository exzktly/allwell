"""Phone layout: fit a desk-shaped form into a phone's width.

The Datasets tools are laid out for the desk: paired fields side by side
("Dataset 1 + Dataset 2"), an option row of five controls, a dashboard of eight
counters in one line, a radio button whose label is a sentence. In phone layout
seven of the nine were wider than the phone — up to 479 px past its right edge
on a 432 px screen — and a ``QHBoxLayout`` cannot wrap, so the only way to a
control was a sideways scroll, and the description paragraphs above were cut.

:func:`fit` narrows a scroll area's content to its viewport in two moves, both
undone by :func:`unfit`:

1. **Stack the rows that overflow.** A widget with a phone form of its own
   (``setStacked``) takes it; any other horizontal box layout wider than the room
   it has is turned to top-to-bottom — the outermost such row first, then the
   widths are measured again, so a pair of fields stacks while each field keeps
   its own button beside it.
2. **Break a label that still does not fit.** A checkbox, radio button or push
   button reports its whole label as its minimum width; one longer than the room
   is given line breaks ("Choose a folder of .czi files instead…" was the last
   thing holding the CZI and ND2 pages wide).

Qt-only and pane-free: it reads layouts and widths, never a feature.
"""

from __future__ import annotations

import textwrap

from PySide6.QtWidgets import (
    QAbstractButton, QBoxLayout, QScrollArea, QWidget,
)

#: A widget's original form, for :func:`unfit`: the box layouts stacked, and
#: the labels re-broken.
_P_STACKED = "_awPhoneStacked"
_P_TEXT = "_awPhoneText"
_P_OWN_FORM = "_awPhoneOwnForm"


def _own_phone_form(content: QWidget, width: int) -> bool:
    """Give an overflowing widget that has a phone form of its own that form.

    The app's convention for one is ``setStacked`` (``ControlBand``,
    ``PlateProgress`` and the dashboards built on it): such a widget knows
    better than a generic pass what to hide and how to stack — the CZI and
    ND2 converters' dashboards, a stage rail beside a plate beside a legend,
    held their pages 16 px wider than the phone until stacked this way.
    """
    done = False
    for w in [content, *content.findChildren(QWidget)]:
        stack = getattr(w, "setStacked", None)
        if (callable(stack) and w.isVisibleTo(content) and not w.property(_P_OWN_FORM)
                and not getattr(w, "_stacked", False)
                and _min_width(w) > _room(w, content, width)):
            stack(True)
            w.setProperty(_P_OWN_FORM, True)
            done = True
    return done


def _min_width(w: QWidget) -> int:
    hint = w.minimumSizeHint()
    return max(w.minimumWidth(), hint.width() if hint.isValid() else 0)


def _room(w: QWidget, content: QWidget, width: int) -> int:
    """The width *w* can have inside *content* laid out at *width*: the content
    width less *w*'s left offset and an equal right margin."""
    x = w.mapTo(content, w.rect().topLeft()).x() if w is not content else 0
    return max(0, width - 2 * max(0, x))


def _overflowing_rows(content: QWidget, width: int) -> list[QWidget]:
    """Horizontal rows wider than their room, outermost first."""
    rows = []
    for w in [content, *content.findChildren(QWidget)]:
        lay = w.layout()
        if (isinstance(lay, QBoxLayout) and lay.direction() == QBoxLayout.LeftToRight
                and w.isVisibleTo(content) and lay.count() > 1
                and _min_width(w) > _room(w, content, width)):
            rows.append(w)
    # Outermost first: a row inside another overflowing row may fit once its
    # parent is stacked, and then needs no change of its own.
    return [r for r in rows if not any(o is not r and o.isAncestorOf(r) for o in rows)]


def _activate(content: QWidget) -> None:
    for w in [content, *content.findChildren(QWidget)]:
        lay = w.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()


def _break_label(button: QAbstractButton, room: int) -> bool:
    text = button.text()
    if not text or "\n" in text:
        return False
    fm = button.fontMetrics()
    chrome = _min_width(button) - fm.horizontalAdvance(text)
    usable = room - max(0, chrome)
    if usable <= 0 or fm.horizontalAdvance(text) <= usable:
        return False
    per_char = fm.horizontalAdvance(text) / len(text)
    wrapped = textwrap.fill(text, width=max(8, int(usable / per_char)),
                            break_long_words=False)
    if wrapped == text:
        return False
    if button.property(_P_TEXT) is None:
        button.setProperty(_P_TEXT, text)
    button.setText(wrapped)
    return True


def fit(area: QScrollArea, *, max_passes: int = 40) -> bool:
    """Narrow *area*'s content to its viewport. ``True`` when it fits."""
    content = area.widget()
    if content is None:
        return True
    width = area.viewport().width()
    if width <= 0:
        return False
    for _ in range(max_passes):
        _activate(content)
        if _min_width(content) <= width:
            return True
        if _own_phone_form(content, width):
            continue
        rows = _overflowing_rows(content, width)
        if rows:
            for row in rows:
                row.layout().setDirection(QBoxLayout.TopToBottom)
                row.setProperty(_P_STACKED, True)
            continue
        broke = False
        for b in content.findChildren(QAbstractButton):
            if b.isVisibleTo(content) and _min_width(b) > _room(b, content, width):
                broke |= _break_label(b, _room(b, content, width))
        if not broke:
            break
    _activate(content)
    return _min_width(content) <= width


def unfit(area: QScrollArea) -> None:
    """Give *area*'s content back its desk form."""
    content = area.widget()
    if content is None:
        return
    for w in [content, *content.findChildren(QWidget)]:
        if w.property(_P_OWN_FORM):
            w.setStacked(False)
            w.setProperty(_P_OWN_FORM, None)
        if w.property(_P_STACKED):
            lay = w.layout()
            if isinstance(lay, QBoxLayout):
                lay.setDirection(QBoxLayout.LeftToRight)
            w.setProperty(_P_STACKED, None)
        text = w.property(_P_TEXT)
        if text is not None and isinstance(w, QAbstractButton):
            w.setText(text)
            w.setProperty(_P_TEXT, None)
    _activate(content)
