"""Cell Gating CDF panel: per-channel/ratio colours shouldn't collide, and an
empty channel/ratio must not steal a later series' axes slot or colour.

Regression coverage for the fix in ``_cell_gating_plot_cdf`` (renderer.py):
``next_idx`` used to only advance when a channel/ratio had data, so an empty
one left its axes slot both unindexed *and* silently reused by whatever came
next -- shifting every later panel's colour/axes by one and leaving a blank
panel at the end instead of where the empty channel actually was.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("matplotlib")


class _FakeCanvas:
    def draw_idle(self) -> None:
        pass

    def setMinimumHeight(self, _px: int) -> None:
        pass


def _make_ctx(*, fluor_data, ratio_data=None, cell_areas=None):
    from matplotlib.figure import Figure

    return SimpleNamespace(
        state=SimpleNamespace(cell_gating=SimpleNamespace(fluor_gates={})),
        runtime=SimpleNamespace(cell_gating=SimpleNamespace(
            figure=Figure(),
            canvas=_FakeCanvas(),
            cell_areas=cell_areas or [],
            fluor_data=fluor_data,
            ratio_data=ratio_data or {},
            axes_stack=[],
            ax=None,
        )),
    )


def test_five_channels_get_five_distinct_colours():
    """The old 4-entry cycle would give channel 5 the same colour as the
    cell-area CDF (index 0) or channel 1 (index 4 % 4 == 0)."""
    from well_viewer.features.grouping.cell_gating.renderer import _cell_gating_plot_cdf

    fluor_data = {f"ch{i}": [1.0, 2.0, 3.0] for i in range(5)}
    ctx = _make_ctx(fluor_data=fluor_data, cell_areas=[10, 20, 30])
    _cell_gating_plot_cdf(ctx)

    fig = ctx.runtime.cell_gating.figure
    line_colours = [ax.lines[0].get_color() for ax in fig.axes if ax.lines]
    assert len(line_colours) == len(set(line_colours)) == 6  # cell-area + 5 channels


def test_empty_channel_does_not_shift_or_blank_later_channels():
    """A dataset where the *middle* channel has no valid values must not
    steal the next channel's axes slot -- every non-empty channel gets its
    own titled subplot, and only the truly-empty one is left blank."""
    from well_viewer.features.grouping.cell_gating.renderer import _cell_gating_plot_cdf

    import theme

    fluor_data = {
        "ch_a": [1.0, 2.0, 3.0],
        "ch_b": [],              # empty -- must not be plotted, must not be skipped over
        "ch_c": [4.0, 5.0, 6.0],
    }
    ctx = _make_ctx(fluor_data=fluor_data, cell_areas=[10, 20, 30])
    _cell_gating_plot_cdf(ctx)

    fig = ctx.runtime.cell_gating.figure
    titles = [ax.get_title() for ax in fig.axes]
    assert "CH_A Distribution" in titles
    assert "CH_C Distribution" in titles
    # ch_b's axes slot exists but was never plotted into or titled.
    blank = [ax for ax in fig.axes if not ax.get_title()]
    assert len(blank) == 1
    assert not blank[0].lines

    # The real regression check: ch_c must land on ITS OWN slot's colour
    # (index 3: cell-area=0, ch_a=1, ch_b=2 (skipped), ch_c=3), not silently
    # inherit ch_b's slot/colour (index 2) the way the pre-fix code did --
    # WELL_COLORS_TUPLE[2] and [3] are guaranteed distinct so this can't pass
    # by accident.
    ch_c_ax = next(ax for ax in fig.axes if ax.get_title() == "CH_C Distribution")
    from matplotlib.colors import to_hex
    assert to_hex(ch_c_ax.lines[0].get_color()) == theme.WELL_COLORS_TUPLE[3].lower()
