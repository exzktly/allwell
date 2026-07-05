"""Regression: creating a Plot Group must work in group (passive-plate) mode.

Once replicate sets exist, the sidebar plate on a plotting tab runs in passive
group-visibility mode — clicks toggle a group's ``hidden`` flag, so
``selected_wells`` stays empty. The Plot Groups "Add" action used to read only
``selected_wells`` and fail with "Select wells before adding a Plot Group",
making the feature unusable whenever any replicate set was defined. The fix
(``_wells_for_new_plot_group``) falls back to the visible-group wells.
"""
from __future__ import annotations

from types import SimpleNamespace

from well_viewer.core.sidebar_view import _wells_for_new_plot_group


def _ctx(*, selected=(), selections=(), solo=(), loaded=None):
    # ctx-shaped fake: ``_wells_for_new_plot_group`` reads typed state off
    # ``ctx.state`` (A8 teardown — the helper is ctx-native, no god-object).
    if loaded is None:
        loaded = {f"{r}{c:02d}": None for r in "AB" for c in range(1, 4)}
    state = SimpleNamespace(
        selection=SimpleNamespace(
            selected_wells=set(selected),
            selections=list(selections),
            active_solo_wells=set(solo),
        ),
        dataset=SimpleNamespace(well_paths=loaded),
    )
    return SimpleNamespace(state=state)


def test_active_plate_uses_raw_selection():
    ctx = _ctx(selected={"A02", "A01"})
    assert _wells_for_new_plot_group(ctx) == ["A01", "A02"]


def test_passive_mode_falls_back_to_visible_group_wells():
    # No raw selection (passive plate) + two visible groups → union of their wells.
    ctx = _ctx(
        selections=[
            {"name": "G1", "hidden": False, "wells": ["A01", "A02"]},
            {"name": "G2", "hidden": False, "wells": ["B01"]},
        ],
    )
    assert _wells_for_new_plot_group(ctx) == ["A01", "A02", "B01"]


def test_hidden_groups_are_excluded():
    ctx = _ctx(
        selections=[
            {"name": "G1", "hidden": True, "wells": ["A01", "A02"]},   # hidden → skip
            {"name": "G2", "hidden": False, "wells": ["B01"]},
        ],
        solo={"A03"},
    )
    assert _wells_for_new_plot_group(ctx) == ["A03", "B01"]


def test_wells_not_loaded_are_dropped():
    ctx = _ctx(
        selections=[{"name": "G1", "hidden": False,
                     "wells": ["A01", "Z99"]}],   # Z99 not in the loaded plate
    )
    assert _wells_for_new_plot_group(ctx) == ["A01"]


def test_all_hidden_and_no_selection_yields_empty():
    # Everything hidden, no solo, no raw selection → empty → caller shows the hint.
    ctx = _ctx(selections=[{"name": "G1", "hidden": True, "wells": ["A01"]}])
    assert _wells_for_new_plot_group(ctx) == []
