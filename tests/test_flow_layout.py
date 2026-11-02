"""FlowLayout: items wrap and are vertically centred within their line (not
top-aligned), and never overlap horizontally.

Regression for the Image Table action row where short controls top-aligned
against tall buttons, reading as ragged/misaligned. Drives ``setGeometry``
directly with an explicit rect (the offscreen platform ignores widget resize),
which invokes the layout and pins each child's geometry.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.qt


def _build(qapp, sizes, rect_w, rect_h):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QLabel, QWidget
    from widgets.flow_layout import FlowLayout

    host = QWidget()
    fl = FlowLayout(host, h_spacing=6, v_spacing=4)
    widgets = []
    for w, h in sizes:
        lbl = QLabel("x", host)
        lbl.setFixedSize(w, h)
        fl.addWidget(lbl)
        widgets.append(lbl)
    # Show so the children are visible (FlowLayout skips hidden widgets), then
    # force the layout at an explicit rect — the offscreen platform ignores
    # widget resize, so setGeometry is how we control the available width.
    host.show()
    qapp.processEvents()
    fl.setGeometry(QRect(0, 0, rect_w, rect_h))
    return host, widgets


def test_items_vertically_centred_and_non_overlapping(qapp):
    # Three different heights on one wide line.
    host, widgets = _build(qapp, [(40, 20), (40, 32), (40, 24)], 1000, 120)
    geoms = [w.geometry() for w in widgets]

    line_top = min(g.top() for g in geoms)
    line_h = max(g.height() for g in geoms)
    line_center = line_top + line_h / 2
    for g in geoms:
        assert abs((g.top() + g.height() / 2) - line_center) <= 1, "item not vertically centred"
    for a, b in zip(geoms, geoms[1:]):
        assert a.right() <= b.left(), "consecutive items overlap horizontally"


def test_wraps_to_second_line_when_narrow(qapp):
    # Four 120px items in a 260px-wide rect → cannot all fit on one line.
    host, widgets = _build(qapp, [(120, 24)] * 4, 260, 400)
    tops = sorted({w.geometry().top() for w in widgets})
    assert len(tops) >= 2, "row did not wrap onto multiple lines when narrow"


def test_the_visibility_watcher_keeps_no_per_instance_state(qapp):
    """The watcher must find its layout through ``parent()``, not an attribute.

    Its C++ object belongs to the layout, which belongs to the host widget, so
    it outlives the Python instance ``__init__`` ran on: once that instance is
    collected PySide builds a *fresh* wrapper the next time an event reaches the
    watcher, and that wrapper never ran ``__init__``. Anything stashed on
    ``self`` is therefore gone by the time it is read — the watcher raised
    ``AttributeError`` inside the Qt event loop on every show and hide from that
    point on, and the row silently stopped reflowing around the item that
    changed.

    Asserting the absence of instance state rather than simulating the re-wrap:
    PySide decides when to discard a wrapper and there is no supported way to
    force it, so the reachable invariant is that there is nothing to lose.
    """
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QLabel, QWidget
    from widgets.flow_layout import FlowLayout, _FlowItemVisibility

    host = QWidget()
    layout = FlowLayout(host, h_spacing=6, v_spacing=4)
    label = QLabel("x", host)
    label.setFixedSize(120, 24)
    layout.addWidget(label)

    watcher = next(child for child in layout.children()
                   if isinstance(child, _FlowItemVisibility))
    # PySide caches a SignalInstance per signal in ``__dict__``; those are
    # rebuilt with the wrapper. Anything else was put there by our own code.
    from PySide6.QtCore import SignalInstance
    ours = {name: value for name, value in vars(watcher).items()
            if not isinstance(value, SignalInstance)}
    assert ours == {}, (
        f"the visibility watcher stores {sorted(ours)} on the instance; a "
        "re-wrapped watcher will not have it. Resolve the layout from "
        "self.parent()."
    )
    # And it still does its job: consumes nothing, raises nothing.
    assert watcher.eventFilter(label, QEvent(QEvent.Hide)) is False


def test_a_break_before_item_starts_a_new_line_with_room_to_spare(qapp):
    """``BREAK_BEFORE`` wraps an item that would fit on the current line, and
    every item after it follows it onto the new line."""
    from widgets.flow_layout import BREAK_BEFORE

    host, widgets = _build(qapp, [(40, 24)] * 4, 1000, 400)
    flat = {w.geometry().top() for w in widgets}
    assert len(flat) == 1, "without the property four 40px items share one 1000px line"

    from PySide6.QtCore import QRect

    widgets[2].setProperty(BREAK_BEFORE, True)
    host.layout().setGeometry(QRect(0, 0, 1000, 400))
    tops = [w.geometry().top() for w in widgets]
    assert tops[0] == tops[1]
    assert tops[2] == tops[3]
    assert tops[2] > tops[1], f"the break item stayed on the first line: tops={tops}"
    assert widgets[2].geometry().left() == widgets[0].geometry().left(), \
        "the new line does not start at the left edge"


def test_a_break_before_first_item_adds_no_empty_line(qapp):
    from widgets.flow_layout import BREAK_BEFORE
    from PySide6.QtCore import QRect

    host, widgets = _build(qapp, [(40, 24)] * 2, 1000, 400)
    widgets[0].setProperty(BREAK_BEFORE, True)
    host.layout().setGeometry(QRect(0, 0, 1000, 400))
    assert widgets[0].geometry().top() == 0
    assert host.layout().heightForWidth(1000) == 24
