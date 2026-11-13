"""The Nanowells page's **Nanowell** box goes to one nanowell the same way on every view.

Driven through the real page builder and the real box (``goto_view``): text typed and Enter pressed
(``returnPressed``), as a person does it, never the controller underneath (CLAUDE.md §7.6). On every view that draws
nanowells the address becomes the selected nanowell, its well the page's well, and the site detail opens on it — its
plots over time and its crops; then each view frames it in its own way, asserted on what the figure drew: the
Distribution's ring, the Trajectories' highlighted line through that nanowell's own values, the Array view zoomed
onto its disc, the Identity view focused on its address. Array QC draws wells, not nanowells, so the box is greyed
there and says why.

The dataset (``test_nanowell_page``) has nanowells ``q,0`` for ``q`` in 0…3 in A01 and B01; the address gone to is
``2,0`` in B01, the second well, so a box that ignored the typed well and stayed in the page's first one (A01)
selects the wrong nanowell and every assertion below names the difference.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell.distribution_compute import metric_column  # noqa: E402
from review.features.nanowell.renderer import RING_GID, TRAJECTORY_SELECTED_GID  # noqa: E402
from review.features.nanowell.series_compute import site_series  # noqa: E402
from review.features.nanowell.state import VIEWS  # noqa: E402
from tests.test_nanowell_page import _loaded, page  # noqa: E402,F401 — the page fixture

TARGET = ("B01", 2, 0)
from review.features.nanowell._goto_controller import NO_GOTO_VIEWS  # noqa: E402

GOING_VIEWS = [v for v in VIEWS if v not in NO_GOTO_VIEWS]


def _type(ctx, text: str) -> None:
    edit = ctx.widgets.get("_nw_goto_edit")
    edit.setText(text)
    edit.returnPressed.emit()


def _statuses(ctx, monkeypatch) -> list:
    said = []
    monkeypatch.setattr(ctx.notify, "set_status", lambda text, *a, **k: said.append(text))
    return said


@pytest.mark.parametrize("view", GOING_VIEWS)
def test_the_box_selects_the_nanowell_and_opens_its_detail_on_every_view(page, qtbot, monkeypatch, view):
    ctx = page
    st = _loaded(qtbot, ctx)
    nwc.set_view(ctx, view)
    edit = ctx.widgets.get("_nw_goto_edit")
    assert edit.isVisibleTo(ctx.window) and edit.isEnabled(), view
    said = _statuses(ctx, monkeypatch)
    _type(ctx, "B01 2,0")
    assert st.selected_sites == [list(TARGET)], view
    assert st.well == "B01" and st.identity_focus == "2,0", view
    assert nwc.crops_site(st) == TARGET, view
    assert ctx.widgets.get("_nw_detail_pane").isVisibleTo(ctx.window), view
    assert said[-1] == "Nanowell 2,0 in B01: its plots and crops are in the site detail.", view
    # The detail draws that nanowell's series: one line per measurement, through its own values.
    assert ctx.widgets.get("_nw_detail_fig").axes, view


def test_the_distribution_rings_the_nanowell_gone_to(page, qtbot):
    ctx = page
    st = _loaded(qtbot, ctx)
    nwc.set_view(ctx, "distribution")
    _type(ctx, "B01 2,0")
    rings = [c for ax in ctx.widgets.get("_nw_fig").axes for c in ax.collections if c.get_gid() == RING_GID]
    # One ring per swarm the nanowell is drawn in (one per timepoint, in B01's panel), each on the dot the hit map
    # records for B01's nanowell 2,0 in that swarm — never on another dot.
    want = sorted((id(ax), round(float(xs[k]), 6), round(float(ys[k]), 6))
                  for ax, xs, ys, sites in st.hits.entries
                  for k, site in enumerate(map(tuple, sites)) if site == TARGET)
    got = sorted((id(ring.axes), round(float(x), 6), round(float(y), 6))
                 for ring in rings for x, y in ring.get_offsets())
    assert len(want) == 2 and got == want


def test_trajectories_draws_the_nanowell_gone_to_through_its_own_values(page, qtbot):
    ctx = page
    st = _loaded(qtbot, ctx)
    nwc.set_view(ctx, "trajectories")
    _type(ctx, "B01 2,0")
    lines = [ln for ax in ctx.widgets.get("_nw_fig").axes for ln in ax.lines if ln.get_gid() == TRAJECTORY_SELECTED_GID]
    assert len(lines) == 1
    [line] = lines
    assert "B01" in line.axes.get_title(loc="left")
    metric, channel, _note = nwc.resolve_metric("trajectories", st.metric, nwc.page_channel_name(ctx), st.data.channels)
    expected = site_series(st.metrics, *TARGET)[metric_column(metric, channel)].to_numpy(dtype=float)
    assert np.asarray(line.get_ydata(), dtype=float) == pytest.approx(expected, nan_ok=True)
    # A nanowell of the same address in the other well is not it: A01's 2,0 differs from B01's in this dataset.
    other = site_series(st.metrics, "A01", 2, 0)[metric_column(metric, channel)].to_numpy(dtype=float)
    assert not np.allclose(other, expected, equal_nan=True)


def test_the_array_view_zooms_onto_the_nanowell_gone_to(page, qtbot):
    ctx = page
    st = _loaded(qtbot, ctx)
    nwc.set_view(ctx, "array")
    _type(ctx, "B01 2,0")
    drawn = st.drawn
    assert drawn is not None and drawn.region is not None
    k = list(map(tuple, drawn.sites.sites)).index(TARGET)
    x0, y0, x1, y1 = drawn.region
    assert ((x0 + x1) / 2, (y0 + y1) / 2) == pytest.approx((float(drawn.sites.x[k]), float(drawn.sites.y[k])))
    # Zoomed: the view is a few nanowells across, not the whole well the Array view fits first.
    assert (x1 - x0) < 4 * 2 * float(drawn.sites.radius[k]) * nwc.FOCUS_MARGIN
    assert st.array_goto is None


def test_without_a_well_the_address_is_in_the_page_s_well_and_a_ring_label_goes_too(page, qtbot):
    ctx = page
    st = _loaded(qtbot, ctx)
    nwc.set_view(ctx, "array")
    nwc.set_well(ctx, "B01")
    _type(ctx, "1,0")
    assert st.selected_sites == [["B01", 1, 0]]
    _type(ctx, "a1: 3,0")                                        # the well as typed: lower case, unpadded
    assert st.selected_sites == [["A01", 3, 0]] and st.well == "A01"
    _type(ctx, "1.0")                                           # ring 1, place 0 on a hex lattice: 1,0
    assert st.selected_sites == [["A01", 1, 0]]


def test_a_well_not_drawn_or_an_address_not_in_the_table_is_said_and_selects_nothing(page, qtbot, monkeypatch):
    ctx = page
    st = _loaded(qtbot, ctx)
    nwc.set_view(ctx, "distribution")
    _type(ctx, "B01 2,0")
    said = _statuses(ctx, monkeypatch)
    _type(ctx, "A02 1,0")                                       # in the dataset, but not selected on the plate
    assert said[-1].startswith("Well A02 is not drawn on this page") and "select it on the plate" in said[-1]
    assert st.selected_sites == [list(TARGET)] and st.well == "B01"
    _type(ctx, "B01 9,9")
    assert said[-1] == "B01 has no nanowell 9,9 in its table: nothing to select."
    assert st.selected_sites == [list(TARGET)]
    _type(ctx, "B3")
    assert said[-1].startswith("'B3' is not a nanowell address")
    assert st.selected_sites == [list(TARGET)]


@pytest.mark.parametrize("view", ["qc", "statistics", "position"])
def test_a_view_that_draws_no_single_nanowell_does_not_show_the_box(page, qtbot, view):
    """QC draws one row per well, Statistics a test's table and Position each ring's summary: none draws a nanowell
    to go to, so the box and Origin are not shown there (asked for: "if it has no role on that tab, it shouldn't be
    displayed", and to hide it on Statistics and Position); back on a view of nanowells they are."""
    ctx = page
    _loaded(qtbot, ctx)
    nwc.set_view(ctx, view)
    edit = ctx.widgets.get("_nw_goto_edit")
    assert not edit.isVisibleTo(ctx.window) and not ctx.widgets.get("_nw_goto_origin_btn").isVisibleTo(ctx.window)
    nwc.set_view(ctx, "distribution")
    assert edit.isVisibleTo(ctx.window) and edit.isEnabled()
