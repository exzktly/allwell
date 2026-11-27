"""A well plate inside a resizable scroll area settles on a height.

``WellPlateSelector.resizeEvent`` raises the plate's minimum height to the aspect-locked height for its width.
Inside a resizable ``QScrollArea`` (the rail's) a taller plate brings in the vertical scroll bar, which narrows
the plate, which lowers its floor, which takes the bar away — and at some sizes neither state is stable. The
re-entry was synchronous and never ended: the Review window died with a stack overflow on its way to the
Plotting page once the plate's label size moved to 12 px, and the every-page render stopped after Home.

Each floor change is counted through a plate whose ``setMinimumHeight`` gives up after a few hundred calls, so a
plate that loops is measured as a number rather than taking the test process down with it.
"""

from __future__ import annotations

import pytest

#: Viewport sizes around the one the app hit. The loop is a property of the arithmetic
#: (label band, twelve columns, scroll bar width), so a band of widths is swept rather than one size trusted.
SIZES = [(w, h) for w in range(246, 262) for h in (232, 240, 256)]
GIVE_UP = 300


@pytest.fixture
def counting_plate(qapp):
    """A plate under the stylesheet the app starts with: its label band is measured from the font the sheet
    gives it, and the loop lives in that arithmetic."""
    from ui import theme
    from widgets.well_plate_selector import WellPlateSelector

    theme.apply_theme(theme.DEFAULT_THEME)
    qapp.setStyleSheet(theme.qss())

    class CountingPlate(WellPlateSelector):
        calls = 0

        def setMinimumHeight(self, h):  # noqa: N802 — Qt naming
            type(self).calls += 1
            if type(self).calls > GIVE_UP:
                return
            super().setMinimumHeight(h)

    yield CountingPlate
    theme.apply_theme("blue")
    qapp.setStyleSheet("")


def test_the_plate_floor_settles_at_every_size(qapp, counting_plate):
    from PySide6.QtWidgets import QScrollArea

    worst = (0, None)
    for size in SIZES:
        counting_plate.calls = 0
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(counting_plate())
        area.resize(*size)
        area.show()
        for _ in range(10):
            qapp.processEvents()
        worst = max(worst, (counting_plate.calls, size))
        area.deleteLater()
        qapp.processEvents()
    calls, size = worst
    assert calls <= 4, (
        f"a plate in a {size[0]}×{size[1]} scroll area changed its floor {calls} times "
        f"({'and was still going when it was stopped' if calls > GIVE_UP else 'before it settled'})")
