"""What Home's plate actually paints, sampled from the rendered widget.

``test_home_plate_colors.py`` measures the tokens; this measures pixels, because
the two can disagree. The tokens were never the whole story: the plate dimmed
three states through ``QColor.darker`` on the way to the screen, so a table of
readable-looking greys arrived as three near-black wells and a legend whose
swatches showed colours no well on the plate carried.

Sampling the widget is the only way to assert the thing the reader sees.
"""
from __future__ import annotations

import pytest

from PySide6.QtGui import QColor, QImage

import theme
from well_viewer.features.home import collect as hc
from well_viewer.features.home.plate_panel import (
    NEUTRAL_STATES,
    STATE_COLORS,
    build_home_plate,
    paint_plate,
)

pytestmark = pytest.mark.qt

#: Derived from the renderer's own two rosters rather than restated. A
#: hand-written list here is a third place a new cell state has to be
#: remembered, and the one that fails as a `KeyError` deep inside a fixture
#: rather than as a message naming what was forgotten — which is what happened
#: when `csv_only` was added.
ALL_STATES = tuple(dict.fromkeys((*STATE_COLORS, *NEUTRAL_STATES)))


def _relative_luminance(c: QColor) -> float:
    def channel(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return (0.2126 * channel(c.red()) + 0.7152 * channel(c.green())
            + 0.0722 * channel(c.blue()))


def _contrast(a: QColor, b: QColor) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.fixture
def painted(qapp):
    """One well per state, rendered, sampled at each well's centre pixel."""
    wells = [f"A{i + 1:02d}" for i in range(len(ALL_STATES))]
    cells = dict(zip(wells, ALL_STATES))

    plate = build_home_plate(None)
    plate.show()
    plate.resize(900, 600)
    for _ in range(3):
        qapp.processEvents()
    plate.setEnabledWells(sorted(cells))
    paint_plate(plate, cells, {})
    for _ in range(3):
        qapp.processEvents()

    grid = plate._grid
    image = QImage(grid.size(), QImage.Format_ARGB32)
    image.fill(QColor(theme.Colors.panel))
    grid.render(image)
    # The harness must not be able to pass by rendering nothing: a plate that
    # never painted returns the fill colour for every well, which would make
    # every "these two differ" assertion below trivially true in the wrong
    # direction and every "this is not the background" assertion fail loudly.
    assert image.size().width() > 0 and image.size().height() > 0

    out = {}
    for index, well in enumerate(wells):
        centre = grid._well_rect(0, index).center()
        out[cells[well]] = QColor(image.pixel(int(centre.x()), int(centre.y())))
    plate.deleteLater()
    return out


def test_the_plate_actually_painted(painted):
    """Guards every other assertion in this file: at least the four unmistakable
    status states must have landed as their own hues."""
    assert painted[hc.CELL_COMPLETE].green() > painted[hc.CELL_COMPLETE].red()
    assert painted[hc.CELL_FAILED].red() > painted[hc.CELL_FAILED].blue()
    assert painted[hc.CELL_NO_MEASUREMENTS].blue() > painted[hc.CELL_NO_MEASUREMENTS].red()
    assert painted[hc.CELL_PARTIAL].red() > painted[hc.CELL_PARTIAL].blue()


def test_no_coloured_well_is_painted_black(painted):
    """The report that started this: wells arriving as near-black cells against
    a near-black panel. Every state that carries a colour must be visible on the
    card it sits on."""
    panel = QColor(theme.Colors.panel)
    dark = {
        state: (painted[state].name(), round(_contrast(painted[state], panel), 2))
        for state in STATE_COLORS
        if _contrast(painted[state], panel) < 2.5
    }
    assert not dark, f"states painted into the background: {dark}"


def test_every_coloured_state_is_distinguishable_from_every_other(painted):
    """Pixel-level version of the token check: after the widget's own gradient
    and highlight, the states must still be told apart."""
    states = sorted(STATE_COLORS)
    clashes = []
    for i, a in enumerate(states):
        for b in states[i + 1:]:
            ca, cb = painted[a], painted[b]
            steps = sum(abs(x - y) for x, y in
                        zip(ca.getRgb()[:3], cb.getRgb()[:3]))
            if steps < 60:
                clashes.append((a, b, ca.name(), cb.name(), steps))
    assert not clashes, f"indistinguishable once painted: {clashes}"


def test_the_absence_states_are_painted_as_plain_uncoloured_wells(painted):
    """Both leave the cell as the plate's own neutral well — and they cannot be
    confused with each other, because a plate holds one or the other, never
    both (``collect.plate_cells``)."""
    tones = {painted[state].name() for state in NEUTRAL_STATES}
    assert len(tones) == 1, f"absence states drew different cells: {tones}"
    for state in STATE_COLORS:
        assert painted[state].name() not in tones
