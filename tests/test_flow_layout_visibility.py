"""FlowLayout must recompute when a managed widget is shown or hidden.

``FlowLayout._do_layout`` skips hidden widgets, which makes the set of visible
widgets an *input* to the layout in a way it is not for a box layout: hiding an
item does not blank a slot, it shifts every later item left and can change
whether the row wraps at all. Nothing in Qt guarantees a layout pass when that
input changes, so a control row that swaps one option group for another — the
Multivariate tab's per-mode row is the case that surfaced this — kept the
positions computed for the previous visible set and painted the newly-shown
group on top of the widgets laid out without it. On the Multivariate tab that
read as the words "Value:" and "Timepoint:" drawn over each other.

These tests use plain Qt widgets, no application fixture, so they run in the
fast lane.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QLabel, QWidget  # noqa: E402

from widgets.flow_layout import FlowLayout, flow_group  # noqa: E402


def _rects(widgets) -> list[QRect]:
    return [w.geometry() for w in widgets]


def _overlaps(widgets) -> list[tuple[int, int]]:
    """Index pairs of *widgets* whose geometries intersect."""
    rects = _rects(widgets)
    return [
        (i, j)
        for i in range(len(rects))
        for j in range(i + 1, len(rects))
        if rects[i].intersects(rects[j])
    ]


def _build_row(qtbot):
    """A flow row shaped like the Multivariate tab's row 2.

    ``wide`` stands in for the Faceting option group (wide enough that the row
    wraps at the host width), ``narrow`` for the Clustering one, and the trailing
    label + combo for the shared Timepoint filter.
    """
    host = QWidget()
    qtbot.addWidget(host)
    lay = FlowLayout(host, h_spacing=6, v_spacing=6)

    wide = QLabel("x" * 120, host)
    narrow = QLabel("x" * 10, host)
    trailing = QLabel("Timepoint:", host)
    for w in (wide, narrow, trailing):
        lay.addWidget(w)

    host.resize(320, 200)
    host.show()
    qtbot.waitExposed(host)
    return host, lay, wide, narrow, trailing


def test_showing_a_widget_relayouts_the_row(qtbot):
    """Swapping which option group is visible must not leave stale positions."""
    host, lay, wide, narrow, trailing = _build_row(qtbot)

    # Start in the "narrow group" state, as a mode switch would leave it.
    wide.setVisible(False)
    narrow.setVisible(True)
    lay.activate()
    qtbot.wait(50)

    # Now swap to the "wide group" state — hide one, show the other, exactly as
    # a per-mode ``_sync_controls`` does.
    narrow.setVisible(False)
    wide.setVisible(True)
    lay.activate()
    qtbot.wait(50)

    assert _overlaps([wide, trailing]) == [], (
        "widgets overlap after a visibility swap — the layout kept positions "
        f"computed for the previous visible set: wide={wide.geometry()} "
        f"trailing={trailing.geometry()}"
    )


def test_hiding_a_widget_reclaims_its_space(qtbot):
    """The item after a hidden one moves into the space it vacated."""
    host, lay, wide, narrow, trailing = _build_row(qtbot)

    wide.setVisible(False)
    narrow.setVisible(True)
    lay.activate()
    qtbot.wait(50)
    with_narrow = trailing.geometry().x()

    narrow.setVisible(False)
    lay.activate()
    qtbot.wait(50)
    without_narrow = trailing.geometry().x()

    assert without_narrow < with_narrow, (
        "hiding the preceding widget did not move the trailing one left "
        f"({with_narrow} -> {without_narrow})"
    )


def test_flow_group_keeps_a_label_with_its_control(qtbot):
    """A label and its control added as one group never wrap apart."""
    host = QWidget()
    qtbot.addWidget(host)
    lay = FlowLayout(host, h_spacing=6, v_spacing=6)

    lay.addWidget(QLabel("x" * 120, host))
    combo = QLabel("All timepoints", host)
    group = flow_group(host, "Timepoint:", combo)
    lay.addWidget(group)

    host.resize(320, 200)
    host.show()
    qtbot.waitExposed(host)
    lay.activate()
    qtbot.wait(50)

    label = next(child for child in group.findChildren(QLabel)
                 if child.text() == "Timepoint:")
    # Same line: the group is one flow item, so its two children share a row.
    assert label.mapTo(host, label.rect().topLeft()).y() == \
        combo.mapTo(host, combo.rect().topLeft()).y()
