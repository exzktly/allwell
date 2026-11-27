"""``SettlingScrollArea`` answers a content resize once, never from inside its own answer.

Review's rail is a resizable scroll area holding the well plate, which is height-for-width. A plain
``QScrollArea`` re-sizes such content each time its vertical scroll bar comes or goes, synchronously and inside
the resize that caused it, and at sizes where neither state is stable the recursion never returns: the Review
window died of a stack overflow on its way to the Plotting page. This builds the rail's shape — a column holding
a plate under the stylesheet the app starts with — across a band of sizes and measures how deep the scroll
area's handling of a content resize nests. A probe stops answering past a depth well beyond any honest one, so
a scroll area that loops is measured as a number rather than taking the test process down.
"""

from __future__ import annotations

import pytest

SIZES = [(w, h) for w in range(240, 300, 3) for h in (200, 232, 260, 300)]
STOP_AT = 60


@pytest.fixture
def shipped_sheet(qapp):
    from ui import theme

    theme.apply_theme(theme.DEFAULT_THEME)
    qapp.setStyleSheet(theme.qss())
    yield
    theme.apply_theme("blue")
    qapp.setStyleSheet("")


def _deepest_nesting(qapp, area_cls) -> tuple[int, tuple[int, int]]:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    from widgets.well_plate_selector import WellPlateSelector

    class Probe(area_cls):
        depth = deepest = 0

        def eventFilter(self, obj, ev):  # noqa: N802 — Qt naming
            if ev.type() != QEvent.Resize or obj is not self.widget():
                return super().eventFilter(obj, ev)
            if Probe.depth >= STOP_AT:
                return True
            Probe.depth += 1
            Probe.deepest = max(Probe.deepest, Probe.depth)
            try:
                return super().eventFilter(obj, ev)
            finally:
                Probe.depth -= 1

    worst = (0, (0, 0))
    for size in SIZES:
        Probe.depth = Probe.deepest = 0
        area = Probe()
        area.setWidgetResizable(True)
        column = QWidget()
        lay = QVBoxLayout(column)
        lay.addWidget(QLabel("Plotting"))
        lay.addWidget(WellPlateSelector())
        lay.addStretch(0)
        area.setWidget(column)
        area.resize(*size)
        area.show()
        for _ in range(10):
            qapp.processEvents()
        worst = max(worst, (Probe.deepest, size))
        area.deleteLater()
        qapp.processEvents()
    return worst


def test_a_rail_of_plate_settles_at_every_size(qapp, shipped_sheet):
    from widgets.settling_scroll_area import SettlingScrollArea

    depth, size = _deepest_nesting(qapp, SettlingScrollArea)
    assert depth <= 2, f"at {size[0]}×{size[1]} a content resize nested {depth} deep"
