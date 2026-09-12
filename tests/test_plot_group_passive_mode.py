"""Creating a Plot Group in group (passive-plate) mode captures what is on screen.

Once replicate sets exist, the sidebar plate on a plotting tab runs in passive
group-visibility mode — clicks toggle a set's ``hidden`` flag rather than
selecting wells. ``selected_wells`` is not part of that mode: nothing keeps it
current, so it holds whatever a per-well tab (or an earlier Plot Group load)
last wrote there. ``_wells_for_new_plot_group`` therefore reads the visible
sets, not ``selected_wells``, whenever the plate is in group-visibility mode.
"""
from __future__ import annotations

from types import SimpleNamespace

from review.core.sidebar_view import _wells_for_new_plot_group


def _ctx(*, selected=(), selections=(), solo=(), loaded=None):
    # ctx-shaped fake: ``_wells_for_new_plot_group`` reads typed state off
    # ``ctx.state`` (A8 teardown — the helper is ctx-native, no god-object).
    # It has no ``window``, so the plate-mode probe can't reach the shell and
    # the helper assumes group-visibility mode whenever replicate sets exist —
    # which is the mode every case below is about.
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


def test_passive_mode_uses_visible_group_wells():
    # Passive plate + two visible groups → union of their wells.
    ctx = _ctx(
        selections=[
            {"name": "G1", "hidden": False, "wells": ["A01", "A02"]},
            {"name": "G2", "hidden": False, "wells": ["B01"]},
        ],
    )
    assert _wells_for_new_plot_group(ctx) == ["A01", "A02", "B01"]


def test_every_visible_group_is_captured_despite_a_stale_selection():
    """Regression: a leftover ``selected_wells`` must not shadow the visible sets.

    Loading a Plot Group writes its wells to ``selected_wells`` (per-well tabs
    and the Image Table need them there). On a plotting tab the plate is passive,
    so that store then sits stale while the user clicks more sets visible. The
    capture used to prefer it, so saving a group that spanned three replicate
    sets stored only the one set the previous load had left behind — and
    reloading it brought back that one set.
    """
    ctx = _ctx(
        selected={"A01", "A02"},   # stale: what a previous "load G1" wrote
        selections=[
            {"name": "G1", "hidden": False, "wells": ["A01", "A02"]},
            {"name": "G2", "hidden": False, "wells": ["B01", "B02"]},
            {"name": "G3", "hidden": False, "wells": ["A03"]},
        ],
    )
    assert _wells_for_new_plot_group(ctx) == ["A01", "A02", "A03", "B01", "B02"]


def test_a_stale_selection_does_not_resurrect_hidden_groups():
    """Only the sets the user can see are saved, even when the stale selection
    names wells from a hidden one."""
    ctx = _ctx(
        selected={"A01", "A02"},   # G1's wells, but G1 is hidden now
        selections=[
            {"name": "G1", "hidden": True, "wells": ["A01", "A02"]},
            {"name": "G2", "hidden": False, "wells": ["B01"]},
        ],
    )
    assert _wells_for_new_plot_group(ctx) == ["B01"]


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
