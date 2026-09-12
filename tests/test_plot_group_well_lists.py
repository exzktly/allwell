"""Which wells a plot is about is decided in exactly one place.

It is not "the selected wells". With replicate sets defined the plotted set is
every non-hidden group's wells *plus* the solo wells toggled on, and
``selected_wells`` is ignored entirely. A second implementation of that rule —
the per-cell export writing its own — would produce a plausible file of the
wrong wells: right schema, right-looking counts, no error, nothing to notice it
with. So ``iter_plot_groups`` (pooled, for the renderers) and
``iter_plot_group_wells`` (unpooled, for the export) both go through
``plot_group_well_lists``, and these hold them to it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from review.data.data_loading import (
    iter_plot_group_wells,
    iter_plot_groups,
    plot_group_well_lists,
)


class _Rset:
    def __init__(self, name, wells, hidden=False):
        self.name = name
        self.wells = list(wells)
        self.hidden = hidden
        self.color = "#123456"


def _ctx(*, wells, selected=(), rsets=(), solo=()):
    frames = {w: pd.DataFrame({"well": [w] * 3, "v": [1.0, 2.0, 3.0]}) for w in wells}
    selections = [{"name": r.name, "wells": list(r.wells), "hidden": r.hidden}
                  for r in rsets]
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(
                well_paths={w: f"/tmp/{w}.csv" for w in wells},
                cache=frames,
            ),
            selection=SimpleNamespace(
                selections=selections,
                selected_wells=set(selected),
                active_solo_wells=set(solo),
            ),
        ),
    )


@pytest.fixture(autouse=True)
def _rows_from_cache(monkeypatch):
    monkeypatch.setattr("review.data.data_loading.get_rows",
                        lambda ctx, label: ctx.state.dataset.cache[label])


def _wells_of(ctx, fallback_to_all=True):
    return sorted(w for _g, w, _df in iter_plot_group_wells(ctx, fallback_to_all))


def _lists_wells(ctx, fallback_to_all=True):
    return sorted(w for _n, wells, _r in plot_group_well_lists(ctx, fallback_to_all)
                  for w in wells)


def test_selection_branch_agrees():
    ctx = _ctx(wells=["A01", "A02", "B01"], selected=["A01", "B01"])
    assert _wells_of(ctx) == ["A01", "B01"] == _lists_wells(ctx)


def test_fallback_to_all_agrees():
    ctx = _ctx(wells=["A01", "A02"])
    assert _wells_of(ctx) == ["A01", "A02"] == _lists_wells(ctx)
    assert _wells_of(ctx, fallback_to_all=False) == []


def test_a_solo_well_beside_a_hidden_group_is_not_lost():
    """The branch a private reimplementation gets wrong.

    With groups defined, ``selected_wells`` is ignored and the plotted set is
    the visible groups plus ``active_solo_wells``. A hidden group contributes
    nothing; the solo well outside it still does.
    """
    ctx = _ctx(
        wells=["A01", "A02", "B01", "B02"],
        selected=["B02"],                      # ignored in rep-set mode
        rsets=[_Rset("visible", ["A01", "A02"]),
               _Rset("hidden", ["B01"], hidden=True)],
        solo=["B02"],
    )
    assert _wells_of(ctx) == ["A01", "A02", "B02"]
    assert _lists_wells(ctx) == ["A01", "A02", "B02"]


def test_a_solo_well_already_inside_a_group_is_not_yielded_twice():
    ctx = _ctx(
        wells=["A01", "A02"],
        rsets=[_Rset("g", ["A01", "A02"])],
        solo=["A01"],
    )
    assert _wells_of(ctx) == ["A01", "A02"]


@pytest.mark.parametrize("case", ["selection", "fallback", "repsets"])
def test_the_pooled_and_unpooled_generators_draw_the_same_wells(case):
    """The equivalence itself, across all three branches.

    ``iter_plot_groups`` cannot be asked for well tokens — its pooled frame
    carries only the filename-parsed ``well`` column — so the comparison is
    against the shared list both of them are built on, and against the row
    counts, which is what would move if the two ever disagreed.
    """
    if case == "selection":
        ctx = _ctx(wells=["A01", "A02", "B01"], selected=["A01", "B01"])
    elif case == "fallback":
        ctx = _ctx(wells=["A01", "A02"])
    else:
        ctx = _ctx(wells=["A01", "A02", "B01", "B02"],
                   rsets=[_Rset("g", ["A01", "A02"]),
                          _Rset("hidden", ["B01"], hidden=True)],
                   solo=["B02"])

    pooled_names = [name for name, _c, _df in iter_plot_groups(ctx)]
    pooled_rows = sum(len(df) for _n, _c, df in iter_plot_groups(ctx))
    list_names = [name for name, _w, _r in plot_group_well_lists(ctx)]
    unpooled_rows = sum(len(df) for _g, _w, df in iter_plot_group_wells(ctx))

    assert pooled_names == list_names
    assert pooled_rows == unpooled_rows


def test_a_well_the_dataset_no_longer_has_is_dropped_from_both():
    ctx = _ctx(wells=["A01"], rsets=[_Rset("g", ["A01", "GONE"])])
    assert _wells_of(ctx) == ["A01"]
    assert [n for n, _w, _r in plot_group_well_lists(ctx)] == ["g"]
