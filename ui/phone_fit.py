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
   thing holding the CZI and ND2 pages wide). A ``QLabel`` that does not wrap —
   a section heading such as "How are the filenames laid out?" — is set to
   wrap; that heading held Zip Loose Folder one pixel wider than the page on a
   machine whose font ran a little wider.

   A label is judged against both its centred room (``_room``) and the room to
   its right, less the content's right margin, since a margin wider on the
   right than the label's left offset leaves less than the centred estimate.
   A broken label's lines are measured, not estimated, and a label broken
   here is broken again if it still does not fit. A combo box sized to its
   longest entry is let shrink to a few characters, its entry elided.

The fonts matter: CI's runner has no Inter and draws in DejaVu Sans, which runs
wider, so the tests run the Datasets tools at a larger type scale too.

Qt-only and pane-free: it reads layouts and widths, never a feature.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QBoxLayout, QComboBox, QLabel, QScrollArea, QWidget,
)

#: A widget's original form, for :func:`unfit`: the box layouts stacked, and
#: the labels re-broken.
_P_STACKED = "_awPhoneStacked"
_P_TEXT = "_awPhoneText"
#: The text :func:`fit` put on a widget, beside :data:`_P_TEXT`'s original. A widget
#: whose text no longer matches was re-set by its page since — Home repaints
#: its labels from the record — so that text is the original now, and
#: :func:`unfit` must not put the stale one back.
_P_SET = "_awPhoneTextSet"
_P_OWN_FORM = "_awPhoneOwnForm"
_P_WRAPPED = "_awPhoneWrapped"
_P_COMBO = "_awPhoneCombo"


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


def _show_pending(content: QWidget) -> None:
    """Show now what Qt would show on the next event-loop turn.

    A widget added to a layout whose parent is already showing is not shown at
    once: Qt queues ``_q_showIfNotHidden`` for it, and until that runs the
    widget is hidden and every width here passes over it. A page that repaints
    and then fits in one go — Home's Overview, whose rows are rebuilt from the
    record — was therefore fitted without the rows it had just built, which
    appeared a moment later at their full width (a recent-dataset row 590 px
    wide on a 400 px phone). This does what that queued call will do, now,
    and only where it would: to a widget a layout manages, never hidden on
    purpose (``WA_WState_ExplicitShowHide``), and not a window — a menu or a
    dialog parented here is a child too, and showing one would open it. A
    child added without a layout is never shown by Qt, so it is left alone.
    """
    for w in content.findChildren(QWidget):
        if (w.isHidden() and not w.isWindow()
                and not w.testAttribute(Qt.WA_WState_ExplicitShowHide)
                and _in_a_layout(w)):
            w.show()


def _in_a_layout(w: QWidget) -> bool:
    """Is *w* an item of its parent's layout, at any depth of nesting?"""
    parent = w.parentWidget()
    lay = parent.layout() if parent is not None else None
    pending = [lay] if lay is not None else []
    while pending:
        lay = pending.pop()
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item.widget() is w:
                return True
            if item.layout() is not None:
                pending.append(item.layout())
    return False


def _activate(content: QWidget) -> None:
    for w in [content, *content.findChildren(QWidget)]:
        lay = w.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()


def _label_room(w: QWidget, content: QWidget, width: int) -> int:
    """The tighter of *w*'s centred room and the room to its right."""
    x = w.mapTo(content, w.rect().topLeft()).x()
    lay = content.layout()
    right = lay.contentsMargins().right() if lay is not None else 0
    return max(0, min(_room(w, content, width), width - max(0, x) - right))


def _wrap_label(label: QLabel) -> bool:
    if label.wordWrap() or not label.text():
        return False
    label.setWordWrap(True)
    label.setProperty(_P_WRAPPED, True)
    return True


def _widest_line(fm, text: str) -> int:
    return max(fm.horizontalAdvance(line) for line in text.split("\n"))


#: Where a label may be broken: after a space, and after the characters a
#: folder or file name is joined with instead — a path separator, an underscore,
#: a hyphen. A path has no spaces to break at, and a dataset is often named
#: ``2026-10-21_HeLa_DAPI_plate07``; Home's recent-datasets rows are one over the
#: other. Each break keeps its character at the end of the line, so the name
#: still reads as one.
_BREAK_AFTER = re.compile(r"[^ /\\_-]*(?:[ /\\_-]+|$)")

#: The break points Qt's own line breaking lacks: it already breaks after a
#: space, ``/`` and ``-``, but treats ``\`` as a prefix character and ``_`` as
#: part of a word. :func:`_soften_breaks` adds a zero-width space after each.
_UNBREAKABLE = ("\\", "_")


def _wrap_line(fm, line: str, usable: int) -> list[str]:
    """One line of text broken greedily at :data:`_BREAK_AFTER` points so each
    piece fits *usable* px, measured piece by piece — an average character width
    underestimates a line of wide letters, and a font that runs wider than this
    machine's (CI's DejaVu Sans against Inter) left a broken label still too
    wide. A single unbreakable piece wider than *usable* is kept whole."""
    pieces: list[str] = []
    current = ""
    for token in (t for t in _BREAK_AFTER.findall(line) if t):
        if current and fm.horizontalAdvance((current + token).rstrip()) > usable:
            pieces.append(current.rstrip())
            current = token
        else:
            current += token
    pieces.append(current.rstrip())
    return pieces


def _wrap_to(fm, text: str, usable: int) -> str:
    """*text* broken into lines whose widest fits *usable* px. A line break
    already in *text* is the author's and is kept; each of its lines is broken
    on its own."""
    return "\n".join(piece for line in text.split("\n")
                     for piece in _wrap_line(fm, line, usable))


def _break_label(button: QAbstractButton, room: int) -> bool:
    """Break *button*'s label to fit *room*. A label already broken here is
    broken again from its original text. A line break in the original is the
    author's and is kept — a recent-datasets row is a name over a folder — and
    each of its lines is broken on its own."""
    current = button.text()
    if not current:
        return False
    text = _original_text(button)
    fm = button.fontMetrics()
    chrome = _min_width(button) - _widest_line(fm, current)
    usable = room - max(0, chrome)
    if usable <= 0 or _widest_line(fm, current) <= usable:
        return False
    wrapped = _wrap_to(fm, text, usable)
    if wrapped == current:
        return False
    _set_fitted_text(button, text, wrapped)
    return True


def _original_text(w) -> str:
    """*w*'s text before :func:`fit` changed it — or its text now, when it was
    never changed or its page has set a new one since (:data:`_P_SET`)."""
    original = w.property(_P_TEXT)
    current = w.text()
    if original is None or w.property(_P_SET) != current:
        return current
    return original


def _set_fitted_text(w, original: str, text: str) -> None:
    w.setProperty(_P_TEXT, original)
    w.setProperty(_P_SET, text)
    w.setText(text)


def _soften_breaks(label: QLabel) -> bool:
    """Let a word-wrapped label break inside a path or a joined-up name.

    ``C:\\Users\\…\\plate07`` and ``HeLa_DAPI_plate07`` each wrap as one
    unbreakable word (:data:`_UNBREAKABLE`). A zero-width space after each of
    those characters gives them the break points ``/`` already has, and draws
    as nothing.
    """
    text = _original_text(label)
    if label.textFormat() == Qt.RichText or not any(c in text for c in _UNBREAKABLE):
        return False
    softened = text
    for c in _UNBREAKABLE:
        softened = softened.replace(c, c + "\u200b")
    if label.text() == softened:
        return False
    _set_fitted_text(label, text, softened)
    return True


def _narrow_combo(combo: QComboBox) -> bool:
    """Let a combo box sized to its longest entry be narrower: the closed box
    shows the current entry elided, and the open list is as wide as it needs.
    One held Convert ND2 three pixels wider than the page with DejaVu Sans."""
    if combo.property(_P_COMBO) is not None:
        return False
    combo.setProperty(_P_COMBO, (int(combo.sizeAdjustPolicy().value),
                                 combo.minimumContentsLength()))
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(8)
    _forget_size_hints(combo)
    return True


def _forget_size_hints(combo: QComboBox) -> None:
    """QComboBox caches its minimum size hint, and changing the adjust policy
    clears only the size hint: measured, the minimum stayed 360 px while the
    size hint fell to 180. A style change clears both."""
    QApplication.sendEvent(combo, QEvent(QEvent.StyleChange))
    combo.updateGeometry()


def fit(area: QScrollArea, *, max_passes: int = 40) -> bool:
    """Narrow *area*'s content to its viewport. ``True`` when it fits."""
    content = area.widget()
    if content is None:
        return True
    return fit_content(content, area.viewport().width(), max_passes=max_passes)


def fit_content(content: QWidget, width: int, *, max_passes: int = 40) -> bool:
    """Narrow *content* to *width* px. ``True`` when it fits.

    :func:`fit` for a widget that is not in a scroll area — Home's no-dataset
    placeholder, whose recent-dataset rows sit in the same stack as the page
    and would otherwise hold the whole stack wider than the phone.
    """
    if width <= 0:
        return False
    _show_pending(content)
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
            room = _label_room(b, content, width)
            if b.isVisibleTo(content) and _min_width(b) > room:
                broke |= _break_label(b, room)
        for label in content.findChildren(QLabel):
            if (label.isVisibleTo(content)
                    and _min_width(label) > _label_room(label, content, width)):
                broke |= _wrap_label(label) or _soften_breaks(label)
        for combo in content.findChildren(QComboBox):
            if (combo.isVisibleTo(content)
                    and _min_width(combo) > _label_room(combo, content, width)):
                broke |= _narrow_combo(combo)
        if not broke:
            break
    _activate(content)
    return _min_width(content) <= width


def unfit(area: QScrollArea) -> None:
    """Give *area*'s content back its desk form."""
    content = area.widget()
    if content is not None:
        unfit_content(content)


def unfit_content(content: QWidget) -> None:
    """Give *content* back its desk form — :func:`unfit` for :func:`fit_content`."""
    for w in [content, *content.findChildren(QWidget)]:
        if w.property(_P_OWN_FORM):
            w.setStacked(False)
            w.setProperty(_P_OWN_FORM, None)
        if w.property(_P_STACKED):
            lay = w.layout()
            if isinstance(lay, QBoxLayout):
                lay.setDirection(QBoxLayout.LeftToRight)
            w.setProperty(_P_STACKED, None)
        combo = w.property(_P_COMBO)
        if combo is not None and isinstance(w, QComboBox):
            w.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy(combo[0]))
            w.setMinimumContentsLength(combo[1])
            w.setProperty(_P_COMBO, None)
            _forget_size_hints(w)
        if w.property(_P_WRAPPED) and isinstance(w, QLabel):
            w.setWordWrap(False)
            w.setProperty(_P_WRAPPED, None)
        text = w.property(_P_TEXT)
        if text is not None and isinstance(w, (QAbstractButton, QLabel)):
            if w.text() == w.property(_P_SET):
                w.setText(text)
            w.setProperty(_P_TEXT, None)
            w.setProperty(_P_SET, None)
    _activate(content)
