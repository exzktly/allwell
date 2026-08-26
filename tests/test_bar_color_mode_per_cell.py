"""The colour mode reaches the artists Violin / Beeswarm / Events actually draw.

``tests/test_bar_render_callers_pass_color_mode.py`` checks these three
renderers by reading their syntax tree. That is worth having and it is not
enough: an adversarial review dropped the ``colors = `` binding from all three
— turning the resolve into a dead expression statement, which ruff does not
flag because a call is not an unused value — and the entire suite stayed green.
The guard has since been tightened to require the binding, but a syntax check
can only ever pin the shape of the fix that was written, not the behaviour.

So this file drives the three renderers and reads the colour off the artists
they produce, which is the assertion CLAUDE.md §7.4 #1 asks for: at the
boundary, not on the value a helper returned. The row-fetch is stubbed rather
than mocked away wholesale, so the renderers run their real geometry, real
threshold split and real fraction arithmetic — only the dataframe is synthetic.

Fast lane on purpose: no ``viewer_app`` fixture, no ``WellViewerApp``. The
renderers take a ``ctx``, so a small stub is all they need, which is the whole
reason the collect / renderer split exists.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

import theme
from well_viewer.core.cell_gating_state import CellGatingState
from well_viewer.core.channel_state import ChannelState
from well_viewer.core.plot_state import PlotState

_WELLS = ["A01", "A02"]
_PLATE = ["#4488CC", "#CC4488"]
_VAL_COL = "gfp_mean_intensity"


@pytest.fixture
def stub_ctx(monkeypatch):
    """A ctx the three renderers can run against, with a real cell frame.

    ``get_rows`` is patched on **every module that bound it at import time**,
    not on ``data_loading`` alone: each of these did ``from ... import
    get_rows`` at module scope, so patching only the source would leave their
    bound references untouched and the stub unused — a harness that quietly
    measures nothing (§7.3 #1). ``draw_per_well_n_bars`` is the one that makes
    this matter: it reaches the rows through ``stats_compute.aggregate_group``
    rather than through the renderer's own fetch.
    """
    from well_viewer.data import data_loading as dl
    from well_viewer.domain import stats_compute as sc
    from well_viewer.features.barplot import renderer as rnd

    rng = np.random.default_rng(0)

    def _frame(_ctx, _well):
        n = 60
        return pd.DataFrame({
            "timepoint_hours": np.zeros(n),
            _VAL_COL: rng.normal(50.0, 8.0, n),
            # Lower-case ``fov`` — the column ``fov_integrity.check_fov_labels``
            # and the per-FOV aggregation read. Spelled ``FOV`` the whole
            # per-field spread comes back NaN ("could not be computed") and no
            # error bar is drawn, which is not the same thing as a spread of
            # zero and would have made the assertion below vacuous.
            "fov": np.repeat(["f1", "f2", "f3"], n // 3),
            # Not decoration. ``aggregate_with_threshold_df`` gates on area and
            # returns nothing at all without this column — it says so on stderr,
            # which is how the omission was caught here. A fixture missing it
            # sends every column down the placeholder branch, and a test written
            # against that would assert on the "no data" path while reading as
            # though it covered the drawn one (§7.1 #4).
            "area_px": rng.uniform(80.0, 400.0, n),
        })

    for module in (rnd, sc, dl):
        monkeypatch.setattr(module, "get_rows", _frame, raising=False)
    monkeypatch.setattr(rnd, "get_all_fluor_gates", lambda _c: {})

    channel = ChannelState()
    channel.active_channel = "gfp"
    channel.active_val_col = _VAL_COL
    return SimpleNamespace(
        state=SimpleNamespace(
            plot=PlotState(), channel=channel,
            dataset=SimpleNamespace(well_paths={w: w for w in _WELLS}, cache={}),
            # Real state objects, not stand-ins: the events panel routes
            # through ``stats_compute``, which reads the gating flags to
            # decide which column counts as "on". A SimpleNamespace here would
            # accept whatever attribute it happened to be asked for.
            cell_gating=CellGatingState(),
        ),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
        runtime=SimpleNamespace(
            barplot=SimpleNamespace(canvas=SimpleNamespace(draw_idle=lambda: None)),
            # ``aggregate.well`` is the optional delegate ``aggregate_well``
            # resolves through; ``None`` selects the real DataFrame path, which
            # is the one production takes and the one worth exercising here.
            aggregate=SimpleNamespace(well=None, group=None)),
        notify=SimpleNamespace(set_status=lambda _m: None),
    )


def _axes():
    fig = Figure(figsize=(6, 8))
    return (fig.add_subplot(3, 1, 1), fig.add_subplot(3, 1, 2), fig.add_subplot(3, 1, 3))


def _inks(*axes) -> set[str]:
    """Every colour any artist on *axes* was drawn with, as upper-case hex.

    Deliberately indiscriminate — collections, lines and patches all count.
    The question this file answers is "did anything on this panel keep a plate
    colour?", and a sweep that only looked at the artist type the renderer
    happens to use today would stop answering it the moment one changes.
    """
    out: set[str] = set()

    def _add(value) -> None:
        arr = np.atleast_2d(np.asarray(value, dtype=float))
        for row in arr:
            if len(row) >= 3 and (len(row) < 4 or row[3] > 0):
                out.add(to_hex(row[:3]).upper())

    for ax in axes:
        for coll in ax.collections:
            for getter in ("get_facecolor", "get_edgecolor"):
                try:
                    _add(getattr(coll, getter)())
                except (ValueError, TypeError):
                    pass
        for line in ax.lines:
            try:
                _add(line.get_color())
            except (ValueError, TypeError):
                pass
        for patch in ax.patches:
            try:
                _add(patch.get_facecolor())
            except (ValueError, TypeError):
                pass
    return out


def _draw(ctx, name, axm, axf, axn, threshold: float = 10.0):
    from well_viewer.features.barplot import renderer as rnd

    if name == "violin":
        rnd.draw_violin(ctx, axm, axf, _WELLS, list(_PLATE), list(_WELLS),
                        [0.0, 0.0], "0", threshold)
        return (axm, axf)
    if name == "beeswarm":
        rnd.draw_beeswarm(ctx, axm, axf, _WELLS, list(_PLATE), list(_WELLS),
                          [0.0, 0.0], "0", threshold)
        return (axm, axf)
    rnd.draw_per_well_n_bars(ctx, axn, _WELLS, list(_PLATE), list(_WELLS),
                             [0.0, 0.0], threshold)
    return (axn,)


_RENDERERS = ["violin", "beeswarm", "n_bars"]


@pytest.mark.parametrize("name", _RENDERERS)
def test_plate_mode_draws_each_column_in_its_own_colour(stub_ctx, name):
    axm, axf, axn = _axes()
    drawn = _inks(*_draw(stub_ctx, name, axm, axf, axn))
    assert {c.upper() for c in _PLATE} <= drawn, (
        f"{name}: plate colours missing from what was drawn — {sorted(drawn)}")


@pytest.mark.parametrize("name", _RENDERERS)
def test_grey_mode_leaves_no_plate_colour_on_the_panel(stub_ctx, name):
    """The assertion that bites. Dropping the ``colors = `` binding — a dead
    call the old syntax guard and ruff both waved through — leaves both plate
    colours on these artists, and this is what notices."""
    stub_ctx.state.plot.bar_color_mode = "grey"
    axm, axf, axn = _axes()
    drawn = _inks(*_draw(stub_ctx, name, axm, axf, axn))
    leaked = {c.upper() for c in _PLATE} & drawn
    assert not leaked, f"{name}: still drawing plate colour(s) {sorted(leaked)} in grey mode"
    assert theme.Colors.plot_mono.upper() in drawn, (
        f"{name}: the mono ink never reached an artist — drew {sorted(drawn)}")


def test_the_events_panel_keeps_its_error_bar_ink_in_grey_mode(stub_ctx):
    """Grey restyles data ink. The error bar is a different statement and keeps
    its own colour, the way the placeholder and the "n/a" mark do."""
    stub_ctx.state.plot.bar_color_mode = "grey"
    stub_ctx.state.plot.use_fov_replicates = True
    axm, axf, axn = _axes()
    # A threshold near the fixture's median, so the three FOVs disagree about
    # how many cells are above it and the spread is non-zero. At the default
    # threshold every FOV counts the same number, the spread is exactly 0, and
    # no error bar is drawn at all — a version of this test written that way
    # would assert nothing and pass for the wrong reason.
    drawn = _inks(*_draw(stub_ctx, "n_bars", axm, axf, axn, threshold=50.0))
    assert theme.Colors.err_bar.upper() in drawn, (
        f"the events panel lost its error-bar ink — drew {sorted(drawn)}")
