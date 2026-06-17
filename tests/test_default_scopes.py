"""Regression: Distribution and Heat Map are registered redraw scopes.

Only line + bar used to be registered, so a channel change with the
Distribution (or Heat Map) tab visible left its data stale — the redraw
coalescer only redraws *registered* scopes. These assert the wiring that
makes a channel switch redraw those tabs too.
"""
from __future__ import annotations


def test_distribution_and_heatmap_are_registered_scopes():
    from well_viewer.core.scope_registry import register_default_scopes
    from well_viewer.domain.fold_change_scopes import get_scope
    from well_viewer.core.plot_orchestrator import redraw_app

    register_default_scopes()

    # Both use the shared default redraw (redraw_app fans out across line /
    # distribution / heat map), so a channel change redraws them when visible.
    dist = get_scope("distribution")
    assert dist is not None
    assert dist.redraw is redraw_app
    assert "Distribution" in dist.tab_names

    hm = get_scope("heatmap")
    assert hm is not None
    assert hm.redraw is redraw_app
    assert "Heat Map" in hm.tab_names
