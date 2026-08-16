"""A section title keeps one line for as long as the row can hold one.

Both halves of the rule are pinned here, because fixing either alone
reintroduces the other. The title is word-wrapped so its *minimum* width is its
longest word — otherwise a long title forces a narrow rail wider than its peers
and clips the buttons beside it. But wrapping also drops its *preferred* width
from the one-line width to the squarish block Qt lays the string into (193 px
against 347 px here), which is what made a title wrap inside a 1260 px row.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from well_viewer.core.ui_helpers import build_section_header

# The real Cell Gating / Statistics strings, so the numbers are the app's own.
_LONG_TITLE = "Fraction-on — which included cells count as ON"
_HINT = ("Which of the included cells count as ON. Removes no cells — it only "
         "sets what 'on' means in the fraction-on statistics.")
_WIDE = 1260   # the Cell Gating header row inside a 1640 px window
_NARROW = 240  # narrower than the left rail


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _title_label(header: QWidget) -> QLabel:
    titles = [w for w in header.findChildren(QLabel)
              if w.property("role") == "section"]
    assert len(titles) == 1, f"expected one section title, found {len(titles)}"
    return titles[0]


def _line_count(label: QLabel, width: int) -> int:
    return round(label.heightForWidth(width) / label.fontMetrics().lineSpacing())


def _laid_out(qapp, header: QWidget, width: int) -> tuple[QWidget, QWidget]:
    """Give *header* a real width and let its layout settle.

    The host is returned for the caller to hold: it owns the header once
    reparented, and a host collected here takes the widget tree with it, so
    every later read raises ``RuntimeError`` on a deleted C++ object.
    """
    host = QWidget()
    host.resize(width, 400)
    header.setParent(host)
    header.resize(width, header.sizeHint().height())
    host.show()
    qapp.processEvents()
    header.layout().activate()
    qapp.processEvents()
    return host, header


def test_long_title_keeps_one_line_in_a_wide_row(qapp):
    """The defect: a 347 px title wrapped inside a 1260 px row."""
    _host, header = _laid_out(
        qapp, build_section_header(None, _LONG_TITLE, hint=_HINT), _WIDE)
    label = _title_label(header)
    one_line = label.fontMetrics().horizontalAdvance(_LONG_TITLE)
    assert one_line < _WIDE, (
        "fixture no longer exercises the bug: this title needs more than the "
        "row's whole width even on one line")

    # The trap, checked rather than assumed: a plain wrapped QLabel prefers a
    # squarish block, so a title handed its bare preference wraps in a wide row.
    plain = QLabel(_LONG_TITLE)
    plain.setWordWrap(True)
    assert plain.sizeHint().width() < one_line, (
        "fixture no longer exercises the bug: this Qt's word-wrapped QLabel now "
        "prefers its one-line width, so the header has nothing to correct")
    assert _line_count(label, label.width()) == 1, (
        f"title wrapped at width {label.width()} px in a {_WIDE} px row, though "
        f"one line needs only {one_line} px")


def test_title_still_wraps_when_the_row_is_genuinely_narrow(qapp):
    _host, header = _laid_out(
        qapp, build_section_header(None, _LONG_TITLE, hint=_HINT), _NARROW)
    label = _title_label(header)
    assert _line_count(label, label.width()) > 1, (
        "a title far wider than the row must wrap rather than force the row wider")


def test_header_minimum_width_is_bounded_by_a_word_not_the_string(qapp):
    """The invariant the word-wrap was added for, in the other direction.

    Stated as a ratio, not as a rebuilt "longest word plus margins" budget: Qt
    derives a wrapped label's minimum from its own text layout, which sits below
    a bare ``horizontalAdvance`` of the longest word under the local faces and
    above it on CI, so two versions of that arithmetic missed by a pixel while
    the invariant held four times over.
    """
    header = build_section_header(None, _LONG_TITLE, hint=_HINT)
    labels = header.findChildren(QLabel)
    assert len(labels) == 2, "expected exactly a title and a hint"
    for lbl in labels:
        one_line = lbl.fontMetrics().horizontalAdvance(lbl.text())
        assert lbl.minimumSizeHint().width() < one_line / 2, (
            f"{lbl.text()[:30]!r}… reports a minimum of "
            f"{lbl.minimumSizeHint().width()} px against a one-line width of "
            f"{one_line} px — it is sizing itself to the string, not to a word, "
            f"so a narrow rail would be forced wider by it")

    margins = header.layout().contentsMargins()
    widest = max(lbl.minimumSizeHint().width() for lbl in labels)
    measured = header.minimumSizeHint().width()
    assert measured <= widest + margins.left() + margins.right(), (
        f"header minimum width {measured} px exceeds its widest label "
        f"({widest} px) plus margins ({margins.left()} + {margins.right()})")


def test_buttons_beside_a_title_are_never_clipped(qapp):
    """The Statistics rail case, measured at the header's *own* minimum width.

    A widget squeezed below its stated minimum clips by definition; what matters
    is that the minimum it asks for is one the buttons survive, and the test
    above bounds that minimum to a word.
    """
    clear = QPushButton("Clear All")
    add = QPushButton("+ Add")
    header = build_section_header(None, "COMPARISON GROUPS", buttons=(clear, add))
    width = max(_NARROW, header.minimumSizeHint().width())
    _host, header = _laid_out(qapp, header, width)
    for btn in (clear, add):
        assert btn.width() >= btn.minimumSizeHint().width(), (
            f"{btn.text()!r} clipped to {btn.width()} px in a {width} px header")
        assert btn.x() + btn.width() <= header.width(), (
            f"{btn.text()!r} extends past the header's right edge")
