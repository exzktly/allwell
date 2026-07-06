"""Spatial tab tracks replicate-mode (group) selection.

In replicate mode the plate is passive and ``selected_wells`` is not maintained
— the active set lives in the group ``hidden`` flags. The Spatial tab must read
that active set (non-hidden groups + solo wells) like the plotting tabs, instead
of falling back to the first / all loaded wells.

The well-source helpers are pure (a small ``ctx`` stand-in), so these run
without a QApplication. A separate membership check guards that Spatial /
Multivariate are wired into the rep-mode mirror set.
"""

from __future__ import annotations

from types import SimpleNamespace

from well_viewer.features.spatial import controller as spc

_WP = {"A01": 1, "A02": 1, "B01": 1, "B02": 1}


def _ctx(*, selections, selected=(), solo=()):
    return SimpleNamespace(state=SimpleNamespace(
        selection=SimpleNamespace(
            selections=list(selections),
            selected_wells=set(selected),
            active_solo_wells=set(solo),
        ),
        dataset=SimpleNamespace(well_paths=_WP),
    ))


def test_rep_mode_uses_visible_group_wells():
    ctx = _ctx(selections=[
        {"wells": ["A01", "A02"], "hidden": False},
        {"wells": ["B01", "B02"], "hidden": True},
    ])
    assert spc._frame_wells(ctx, None) == ["A01", "A02"]
    assert spc._summary_wells(ctx) == ["A01", "A02"]


def test_rep_mode_includes_solo_wells():
    ctx = _ctx(
        selections=[{"wells": ["A01", "A02"], "hidden": False}],
        solo=["B02"],
    )
    assert spc._frame_wells(ctx, None) == ["A01", "A02", "B02"]


def test_rep_mode_all_hidden_is_empty():
    ctx = _ctx(selections=[{"wells": ["A01", "A02"], "hidden": True}])
    assert spc._frame_wells(ctx, None) == []
    assert spc._summary_wells(ctx) == []


def test_no_groups_uses_selected_wells():
    ctx = _ctx(selections=[], selected=["B02", "A01"])
    assert spc._frame_wells(ctx, None) == ["A01", "B02"]
    assert spc._summary_wells(ctx) == ["A01", "B02"]


def test_spatial_and_multivariate_in_rep_mirror_set():
    from well_viewer.runtime_app import WellViewerApp
    assert "Spatial" in WellViewerApp._REP_MIRROR_TABS
    assert "Multivariate" in WellViewerApp._REP_MIRROR_TABS
    # plotting tabs stay in the mirror set too
    assert "Bar Plots" in WellViewerApp._REP_MIRROR_TABS
