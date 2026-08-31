"""The Image Table's Plot Groups drawer saves the Image Table's own selection.

The drawer is one shared core builder used by two sidebars, but "the current
selection" is not one store. The plotting sidebar's plate writes
``selection.selected_wells`` (or expresses itself as replicate-set visibility);
the Image Table's picker is a separate selector that deliberately ignores
replicate sets and keeps its wells in ``state.image_table.active_wells``.

Add on the Image Table drawer used to read the plotting sidebar's selection, so
it saved whatever had been chosen on some other tab rather than the wells the
user had just clicked directly above the button. Each drawer now supplies its
own reader.
"""

from __future__ import annotations

from types import SimpleNamespace

from well_viewer.core import sidebar_view as SV
from well_viewer.features.image_browser.image_table.controller import (
    image_table_active_wells,
)


def _ctx(*, active_wells, well_paths, selected_wells=frozenset(), selections=()):
    statuses: list[str] = []
    ctx = SimpleNamespace(
        state=SimpleNamespace(
            selection=SimpleNamespace(
                selected_wells=set(selected_wells),
                selections=list(selections),
                active_solo_wells=set(),
                plot_groups=[],
            ),
            dataset=SimpleNamespace(well_paths=well_paths),
            image_table=SimpleNamespace(active_wells=set(active_wells)),
        ),
        widgets=SimpleNamespace(get_or_none=lambda _n: None),
        persistence=None,
        notify=SimpleNamespace(set_status=statuses.append),
    )
    return ctx, statuses


WELLS = {f"{r}{c:02d}": f"/data/{r}{c:02d}" for r in "AB" for c in range(1, 5)}


def test_active_wells_are_plate_ordered_and_loaded_only():
    ctx, _ = _ctx(active_wells={"B02", "A01", "H12"}, well_paths=WELLS)

    assert image_table_active_wells(ctx) == ["A01", "B02"]


def test_add_uses_the_drawers_own_source_not_the_plotting_selection(monkeypatch):
    ctx, _ = _ctx(
        active_wells={"A03", "A01"},
        well_paths=WELLS,
        # What the plotting sidebar happens to hold — must not be saved here.
        selected_wells={"B01", "B02"},
    )
    monkeypatch.setattr(SV, "refresh_plot_groups_sidebar", lambda _c: None)

    SV._plot_groups_add_current(
        ctx, None, wells_source=lambda: image_table_active_wells(ctx))

    assert [g["wells"] for g in ctx.state.selection.plot_groups] == [["A01", "A03"]]


def test_add_without_a_source_still_reads_the_plotting_selection(monkeypatch):
    ctx, _ = _ctx(active_wells={"A03"}, well_paths=WELLS,
                  selected_wells={"B01", "B02"})
    monkeypatch.setattr(SV, "refresh_plot_groups_sidebar", lambda _c: None)

    SV._plot_groups_add_current(ctx, None)

    assert [g["wells"] for g in ctx.state.selection.plot_groups] == [["B01", "B02"]]


def test_an_empty_image_table_selection_says_so_and_saves_nothing():
    ctx, statuses = _ctx(active_wells=set(), well_paths=WELLS,
                         selected_wells={"B01"})

    SV._plot_groups_add_current(
        ctx, None, wells_source=lambda: image_table_active_wells(ctx))

    assert ctx.state.selection.plot_groups == []
    assert statuses == [
        "Select wells on the plate above before saving a selection."
    ]
