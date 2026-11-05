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


def _build_with_breaks(qapp, layout_items, rect_w):
    """*layout_items*: a ``(w, h)`` per label, ``"|"`` per ``flow_break``. Returns the host, labels and breaks."""
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QLabel, QWidget
    from widgets.flow_layout import FlowLayout, flow_break

    host = QWidget()
    fl = FlowLayout(host, h_spacing=6, v_spacing=4)
    labels, breaks = [], []
    for it in layout_items:
        if it == "|":
            brk = flow_break(host)
            fl.addWidget(brk)
            breaks.append(brk)
            continue
        lbl = QLabel("x", host)
        lbl.setFixedSize(*it)
        fl.addWidget(lbl)
        labels.append(lbl)
    host.show()
    qapp.processEvents()
    fl.setGeometry(QRect(0, 0, rect_w, 200))
    return host, fl, labels, breaks


def test_a_break_starts_a_new_line_with_room_to_spare_and_adds_no_empty_line(qapp):
    """Three 40 px items fit one 1000 px line; a break after the first puts the other two on the next line, and a
    second break straight after it (or one at the start) adds no empty line between."""
    _host, fl, (a, b, c), _breaks = _build_with_breaks(qapp, ["|", (40, 20), "|", "|", (40, 20), (40, 20)], 1000)
    assert b.y() == c.y() == a.y() + 20 + 4                            # one line down, not two
    assert b.x() == a.x() == 0 and c.x() > b.x()
    assert fl.heightForWidth(1000) == 2 * 20 + 4


def test_a_hidden_break_lets_the_items_flow_together_again(qapp):
    _host, fl, (a, b), (brk,) = _build_with_breaks(qapp, [(40, 20), "|", (40, 20)], 1000)
    from PySide6.QtCore import QRect

    assert b.y() > a.y()
    brk.hide()
    qapp.processEvents()
    fl.setGeometry(QRect(0, 0, 1000, 200))
    assert b.y() == a.y() and b.x() > a.x()
