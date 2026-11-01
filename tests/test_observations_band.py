"""The Plotting band's Observations toggle: when it is offered, what a leaf draws, and its words
(ARCHITECTURE §6.2l).

When the toggle shows is derived from three facts — the dataset is a nanowell run, the build has the Nanowells
feature (its ``observations.nanowell`` service), and the leaf's registry descriptor declares nanowells — never
from a list of tab titles (CLAUDE.md §3). Every Plotting leaf states what it can draw; one left cell-only carries
its reason in ``CELLS_ONLY`` below, so a leaf nobody considered and a leaf deliberately left out cannot look alike.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.core import observation_band as ob  # noqa: E402
from review.domain.observation import CELLS, NANOWELLS, OBSERVATION_LABELS, footer_line, pooled_cells_line  # noqa: E402

#: Plotting leaves that draw cells only, each with why. A leaf that gains a nanowell view leaves this roster.
CELLS_ONLY = {
    "Response Curves": "a dose axis is a sample variable; nanowell responses not yet drawn (tracker #13)",
}


def test_every_plotting_leaf_declares_what_it_draws_and_a_cells_only_leaf_says_why():
    from review.features.registry import build_feature_registry

    registry = build_feature_registry()
    leaves = {name: registry.get(name) for name in registry.sub_leaves("Plotting")}
    assert leaves, "no Plotting leaves registered"
    for name, descriptor in leaves.items():
        declared = tuple(descriptor.observations)
        assert CELLS in declared, name
        if NANOWELLS not in declared:
            assert name in CELLS_ONLY, f"{name} draws cells only and says nothing about why (CELLS_ONLY)"
        else:
            assert name not in CELLS_ONLY, f"{name} draws nanowells; take it out of CELLS_ONLY"
    assert set(CELLS_ONLY) <= set(leaves), f"stale CELLS_ONLY entries: {set(CELLS_ONLY) - set(leaves)}"


def test_the_word_unit_appears_in_no_user_facing_text_about_the_toggle():
    texts = [ob.CELLS_ONLY_TEXT, *OBSERVATION_LABELS.values(), footer_line(NANOWELLS, 1, 2, {"occluded": 1}),
             footer_line(CELLS, 1, 1), pooled_cells_line({"A01": 3}, nanowell_views=False)]
    assert not [t for t in texts if "unit" in t.lower()]
    assert footer_line(CELLS, 1, 1).startswith("Observations: cells")
    assert footer_line(NANOWELLS, 1, 2).startswith("Observations: nanowells")


@pytest.fixture
def shell(qtbot, tmp_path):
    from tests._minimal_shell import load_dataset, minimal_shell

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    return ctx, load_dataset, tmp_path


def _nanowell(shell):
    from tests.fixtures.nanowell_dataset import build_nanowell_dataset

    ctx, load_dataset, tmp_path = shell
    root = build_nanowell_dataset(tmp_path / "nw")
    load_dataset(ctx, root / "out")
    ctx.state.dataset.data_dir = root / "out"
    return ctx


def test_a_standard_plate_shows_nothing(shell, mini_dataset):
    ctx, load_dataset, _ = shell
    load_dataset(ctx, mini_dataset)
    assert ob.band_mode(ctx, "Scatter Plot") == "hidden"
    ctx.state.plot.observations = NANOWELLS
    assert ob.effective_observations(ctx, "Scatter Plot") == CELLS


def test_a_nanowell_plate_offers_the_toggle_on_a_leaf_that_draws_nanowells_and_says_why_elsewhere(shell):
    ctx = _nanowell(shell)
    assert ob.band_mode(ctx, "Scatter Plot") == "toggle"
    assert ob.band_mode(ctx, "Response Curves") == "cells_only"
    ctx.state.plot.observations = NANOWELLS
    assert ob.effective_observations(ctx, "Scatter Plot") == NANOWELLS
    assert ob.effective_observations(ctx, "Response Curves") == CELLS


def test_without_the_nanowells_feature_a_nanowell_plate_shows_nothing(shell, monkeypatch):
    ctx = _nanowell(shell)
    monkeypatch.setattr(ctx.features, "service", lambda key: None)
    assert ob.band_mode(ctx, "Scatter Plot") == "hidden"
    ctx.state.plot.observations = NANOWELLS
    assert ob.effective_observations(ctx, "Scatter Plot") == CELLS


def test_the_band_shows_the_toggle_or_its_text_and_the_toggle_records_the_choice(shell, monkeypatch):
    from widgets.control_band import ControlBand

    ctx = _nanowell(shell)
    band = ControlBand()
    ob.build_observation_controls(ctx, band)
    redrawn = []
    monkeypatch.setattr("review.core.plot_orchestrator.redraw_every_scope", lambda c: redrawn.append(c))
    ob.refresh(ctx, "Scatter Plot")
    group = ctx.widgets.get("_plotting_observations_grp")
    static = ctx.widgets.get("_plotting_observations_static")
    assert not group.isHidden() and static.isHidden()
    ob.refresh(ctx, "Response Curves")
    assert group.isHidden() and not static.isHidden()
    toggle = ctx.widgets.get("_plotting_observations_toggle")
    toggle.setCurrentByData(NANOWELLS)
    assert ctx.state.plot.observations == NANOWELLS and redrawn == [ctx]


def test_the_toggle_swaps_the_cell_controls_for_the_metric_picker_and_back(shell, monkeypatch):
    """Both directions, driven through the toggle a user clicks (review finding: the band once hid the cell
    controls under Nanowells and never showed them again)."""
    from PySide6.QtWidgets import QLabel

    from widgets.control_band import ControlBand
    from widgets.page_stack import NamedPageStack

    ctx = _nanowell(shell)
    band = ControlBand()
    ob.build_observation_controls(ctx, band)
    cell_groups = {n: ctx.widgets.register(n, band.add_scope("", QLabel(n)))
                   for n in ("_plotting_channel_grp", "_plotting_metric_grp", "_plotting_thresh_frac_grp")}
    stack = ctx.widgets.register("_plotting_notebook", NamedPageStack())
    stack.addPage("Bar Plots", QLabel())
    stack.setCurrentByName("Bar Plots")
    monkeypatch.setattr("review.core.plot_orchestrator.redraw_every_scope", lambda c: None)
    ob.refresh(ctx, "Bar Plots")
    metric = ctx.widgets.get("_plotting_nw_metric_grp")
    assert metric.isHidden() and not any(g.isHidden() for g in cell_groups.values())
    toggle = ctx.widgets.get("_plotting_observations_toggle")
    toggle.setCurrentByData(NANOWELLS)
    assert not metric.isHidden() and all(g.isHidden() for g in cell_groups.values())
    toggle.setCurrentByData(CELLS)
    assert metric.isHidden() and not any(g.isHidden() for g in cell_groups.values())
