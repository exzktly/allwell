"""Statistics auto-populate in Split View seeds only the *visible* groups.

When the Statistics tab first loads while Split View is active, its comparison
groups should mirror what's drawn on the plots — the non-hidden rep-sets —
rather than every defined grouping. Outside Split View the behaviour is
unchanged (all groups).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


def _two_repsets(app):
    # G1 visible, G2 hidden.
    app._app_state.selection.selections = [
        {"id": "1", "name": "G1", "wells": ["A01", "A02"], "hidden": False},
        {"id": "2", "name": "G2", "wells": ["B01", "B02"], "hidden": True},
    ]


def _group_names(groups):
    return sorted(g.name for g in groups)


@pytest.mark.slow
def test_stats_seeds_all_groups_outside_split(loaded_app):
    app = loaded_app
    from well_viewer.features.stats.controller import stats_sync_from_app
    _two_repsets(app)
    app._notebook.setCurrentByName("Statistics")
    app._ctx.runtime.split.active = False
    stats_sync_from_app(app._ctx)
    assert _group_names(app._app_state.stats.groups) == ["G1", "G2"]


@pytest.mark.slow
def test_stats_seeds_only_visible_groups_in_split(loaded_app):
    app = loaded_app
    from well_viewer.features.stats.controller import stats_sync_from_app
    _two_repsets(app)
    app._notebook.setCurrentByName("Statistics")
    app._ctx.runtime.split.active = True
    stats_sync_from_app(app._ctx)
    # G2 is hidden → not drawn on the plots → excluded from the seeded groups.
    assert _group_names(app._app_state.stats.groups) == ["G1"]


@pytest.mark.slow
def test_load_from_sample_defs_loads_all_groups_even_in_split(loaded_app):
    # The explicit "Load from Sample Definitions" action must load every group
    # (visible_only_in_split=False), not just the split-visible subset — even
    # with a group hidden in Split View.
    app = loaded_app
    from well_viewer.features.stats.controller import stats_sync_from_app
    _two_repsets(app)
    app._app_state.selection.selections[1]["hidden"] = True
    app._notebook.setCurrentByName("Statistics")
    app._ctx.runtime.split.active = True
    stats_sync_from_app(app._ctx, visible_only_in_split=False)
    assert _group_names(app._app_state.stats.groups) == ["G1", "G2"]


@pytest.mark.slow
def test_stats_split_falls_back_to_all_when_none_visible(loaded_app):
    app = loaded_app
    from well_viewer.features.stats.controller import stats_sync_from_app
    _two_repsets(app)
    # Hide everything → nothing visible; fall back to all so the tab isn't blank.
    for s in app._app_state.selection.selections:
        s["hidden"] = True
    app._notebook.setCurrentByName("Statistics")
    app._ctx.runtime.split.active = True
    stats_sync_from_app(app._ctx)
    assert _group_names(app._app_state.stats.groups) == ["G1", "G2"]
