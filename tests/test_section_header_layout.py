"""A section title keeps one line for as long as the row can hold one.

``build_section_header`` word-wraps its title deliberately: the title's *minimum*
width has to be its longest single word, or a long title reports its full
one-line width as the header's minimum, which widens a narrow rail past its peers
and clips the action buttons sitting beside the title.

The trap is that a word-wrapped ``QLabel`` also changes what it reports as its
*preferred* width. It stops being the one-line width and becomes the roughly
square block Qt lays the string into — for the Cell Gating titles, 163 px against
a one-line 246 px, and 193 px against 347 px. A title handed its preference in a
row over a thousand pixels wide therefore wrapped to two lines with hundreds of
pixels of the row left empty beside it.

So this file pins both halves of the rule at once, because fixing either one
alone reintroduces the other:

* a title in a wide row occupies one line, and
* the header's minimum width is still bounded by the longest word, not by the
  whole title.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from well_viewer.core.ui_helpers import build_section_header

# The real Cell Gating / Statistics strings, so the numbers this file reasons
# about are the ones the app actually lays out.
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
    """How many lines *label* needs at *width*, in whole line heights."""
    return round(label.heightForWidth(width) / label.fontMetrics().lineSpacing())


def _laid_out(qapp, header: QWidget, width: int) -> tuple[QWidget, QWidget]:
    """Give *header* a real width and let its layout settle.

    Returns ``(host, header)``: the host has to be handed back and held by the
    caller, because it owns the header once reparented and a host collected at
    the end of this call takes the whole widget tree down with it — every
    subsequent read then raises ``RuntimeError`` on a deleted C++ object.
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

    # The trap this file exists for, stated as a fixture check rather than
    # assumed: a plain word-wrapped QLabel prefers a squarish block, so anything
    # handing the title its bare preference gets a wrapped title in a wide row.
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


def test_header_minimum_width_is_bounded_by_the_longest_word(qapp):
    """The invariant the word-wrap was added for, in the other direction.

    A header whose minimum width tracked the whole title would push a narrow rail
    wider than its peers, so the bound is the longest word plus the layout's own
    margins — not the one-line width of the title.
    """
    header = build_section_header(None, _LONG_TITLE, hint=_HINT)
    longest_word = max(
        header.fontMetrics().horizontalAdvance(w) for w in _LONG_TITLE.split())
    margins = header.layout().contentsMargins()
    budget = longest_word + margins.left() + margins.right() + 8
    assert header.minimumSizeHint().width() <= budget, (
        f"header minimum width {header.minimumSizeHint().width()} px exceeds the "
        f"longest word ({longest_word} px) plus margins — a narrow rail would be "
        f"forced wider by this title")


def test_buttons_beside_a_title_are_never_clipped(qapp):
    """The Statistics rail case: a title plus two action buttons in a narrow rail.

    Measured at the header's *own* minimum width rather than at an arbitrary
    narrow number: a widget squeezed below its stated minimum clips by
    definition, and asserting against that measures the squeeze, not the layout.
    What matters is that the minimum the header asks for is one the buttons
    survive — and the previous test bounds that minimum to the title's longest
    word, so the two together say the rail is neither widened nor clipped.
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
