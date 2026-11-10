"""The nanowell scatter offers the Nanowells page's Subset and Baseline, and a click shows the nanowell there.

Asked for: "Why is it that I still can't see subsets defined in scatter->nanowells, or define how fold change
chooses its timepoints. Why can't I click on a nanowell in scatter and have it show that nanowell in the nanowell
viewer." The two pickers are the page's own settings — choosing one here sets the page's, through the page's own
calls — so these assert the page's state after the choice as well as what the scatter then draws. They drive the
real Scatter builder, its band pickers and its canvas click handler (CLAUDE.md §7.6 #1, §7.8 #3).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from review.domain.observation import NANOWELLS  # noqa: E402
from tests.test_scatter_draws_nanowell_observations import _footer, _points, scatter_ctx  # noqa: E402,F401


def _page(ctx):
    return ctx.state.feature_slice("nanowell")


def _draw(ctx):
    from review.features.scatter.controller import redraw_scatter_orchestrate

    ctx.state.plot.observations = NANOWELLS
    redraw_scatter_orchestrate(ctx)
    return sorted(map(tuple, _points(ctx.widgets.get("_ax_scatter"))))


def _items(combo):
    return [(combo.itemData(i), combo.itemText(i)) for i in range(combo.count())]


def _choose(combo, key):
    index = combo.findData(key)
    assert index >= 0, (key, _items(combo))
    combo.setCurrentIndex(index)


def _save(ctx, name, n):
    from review.features.nanowell import controller as nwc
    from review.features.nanowell import subset_compute as ss

    _page(ctx).subset = ss.Subset((ss.Condition("n0", "ge", n),)).validated()
    assert nwc.save_subset(ctx, name) == ""


def test_the_subset_picker_lists_the_page_s_subsets_and_choosing_one_sets_the_page_s(scatter_ctx):
    from review.features.nanowell import subset_compute as ss

    ctx = scatter_ctx
    _save(ctx, "Two+", 2)
    _page(ctx).subset = ss.Subset()                    # the page draws every nanowell
    every = _draw(ctx)
    combo = ctx.widgets.get("_scatter_cells_nw_subset_cb")
    assert combo.parentWidget().isVisibleTo(ctx.window)
    assert _items(combo) == [("", "All nanowells"), ("Two+", "Two+")]
    assert combo.currentData() == ""
    _choose(combo, "Two+")
    assert _page(ctx).subset == ss.Subset((ss.Condition("n0", "ge", 2),)).validated()
    kept = sorted(map(tuple, _points(ctx.widgets.get("_ax_scatter"))))
    # Starting cells ≥ 2: A01's (1,0) and (-1,1), B01's (0,0), at both timepoints.
    assert kept == sorted([(2.0, 20.0), (2.0, 20.0), (4.0, 40.0), (4.0, 40.0), (2.0, 5.0), (2.0, 5.0)])
    _choose(ctx.widgets.get("_scatter_cells_nw_subset_cb"), "")
    assert not _page(ctx).subset.is_active()
    assert sorted(map(tuple, _points(ctx.widgets.get("_ax_scatter")))) == every


def test_an_unsaved_subset_on_the_page_is_named_rather_than_shown_as_all(scatter_ctx):
    from review.features.nanowell import subset_compute as ss

    ctx = scatter_ctx
    _page(ctx).subset = ss.Subset((ss.Condition("n0", "ge", 3),)).validated()
    _draw(ctx)
    combo = ctx.widgets.get("_scatter_cells_nw_subset_cb")
    assert combo.currentText() == f"{_page(ctx).subset.label()} (not saved)"


def test_the_baseline_picker_sets_the_page_s_baseline_and_the_fold_changes_follow(scatter_ctx):
    ctx = scatter_ctx
    _draw(ctx)
    y = ctx.widgets.get("_scatter_cells_nw_y_cb")
    _choose(y, "count_fc")
    x = ctx.widgets.get("_scatter_cells_nw_x_cb")
    _choose(x, "n")
    base = ctx.widgets.get("_scatter_cells_nw_baseline_cb")
    assert [k for k, _t in _items(base)] == ["earliest", "first_measurable", "tp:0h", "tp:24h"]
    assert base.currentData() == "earliest"
    _choose(base, "tp:24h")
    assert _page(ctx).baseline == "tp:24h"
    points = sorted(map(tuple, _points(ctx.widgets.get("_ax_scatter"))))
    # Measured from 24h, every nanowell's 24h count fold change is 1 — not true of the earliest baseline, where
    # A01's (0,0) is 3 / 1.
    at_24h = {pt for pt in points if pt[1] == 1.0}
    assert (3.0, 1.0) in at_24h and (3.0, 3.0) not in points
    assert "Fold changes are over the Nanowells page's Baseline: 24h." in _footer(ctx.widgets.get("_scatter_fig"))


def _event_at(ctx, x, y):
    ax = ctx.widgets.get("_ax_scatter")
    ctx.widgets.get("_scatter_canvas").draw()
    px, py = ax.transData.transform((x, y))
    return SimpleNamespace(inaxes=ax, x=px, y=py, xdata=x, ydata=y, button=1)


def test_a_click_on_a_nanowell_shows_it_on_the_nanowells_page(scatter_ctx, monkeypatch):
    from review.features.scatter.callbacks import on_scatter_click, on_scatter_motion

    ctx = scatter_ctx
    shown = []
    notebook = type("Nb", (), {"setCurrentByName": lambda self, name: shown.append(name) or True})()
    monkeypatch.setattr(ctx.window, "_notebook", notebook, raising=False)
    said = []
    monkeypatch.setattr(ctx.notify, "set_status", said.append)
    ctx.state.selection.selected_wells = {"A01", "B01"}
    _draw(ctx)
    # B01's (-1,1) nanowell holds no cell at either timepoint, so it has no brightness; B01's (0,1) holds 1 cell
    # at both, with brightness 25 — the only point at (1, 25).
    on_scatter_motion(ctx, _event_at(ctx, 1.0, 25.0))
    assert said[-1].startswith("B01 · nanowell (0, 1)"), said
    on_scatter_click(ctx, _event_at(ctx, 1.0, 25.0))
    assert shown == ["Nanowells"]
    assert _page(ctx).selected_sites == [["B01", 0, 1]]
    assert _page(ctx).timepoint in ("0h", "24h")


def test_a_click_adds_the_well_to_the_plate_selection_when_it_is_not_in_it(scatter_ctx, monkeypatch):
    from review.features.nanowell._plot_links import show_nanowell

    ctx = scatter_ctx
    notebook = type("Nb", (), {"setCurrentByName": lambda self, name: True,
                               "currentName": lambda self: "Scatter Plot"})()
    monkeypatch.setattr(ctx.window, "_notebook", notebook, raising=False)
    ctx.state.selection.selected_wells = {"A01"}
    _page(ctx).popout_source = "window"            # the pop-out left on the window: it turns to the nanowell clicked
    assert show_nanowell(ctx, "B01", 1, 0, "24h")
    assert _page(ctx).popout_source == "selected"
    assert set(ctx.state.selection.selected_wells) == {"A01", "B01"}
    assert _page(ctx).selected_sites == [["B01", 1, 0]] and _page(ctx).timepoint == "24h"


def test_the_scatter_names_the_page_s_saved_subset_on_the_figure(scatter_ctx):
    """The nanowell scatter is of the page's Subset, so — like the page's own figures — it says which, by name, and
    the line is replaced rather than piled up on each redraw."""
    from review.features.nanowell import subset_compute as ss

    ctx = scatter_ctx
    _save(ctx, "Two+", 2)

    def stamps():
        fig = ctx.widgets.get("_scatter_canvas").figure
        return [t.get_text() for t in fig.texts if t.get_gid() == "nw-subset-label"]

    _draw(ctx)
    _draw(ctx)
    assert stamps() == ["Subset: “Two+”"]
    _page(ctx).subset = ss.Subset()
    _draw(ctx)
    assert stamps() == []
