"""Characterization: iter_plot_groups' (name, colour, df) contract under ctx.

A8-teardown T3 converts the shared plot-grouping leaf ``iter_plot_groups`` and
the colour graph it uses (``sel_color`` / ``rank_color_rset`` / ``rep_sets_active``
+ the pure ``color_for_wells``) from the god-object to ``ctx``. Colours are
visual and have no other headless coverage, so this test locks the exact yielded
tuples — group name, resolved colour, and pooled row values — captured from the
pre-conversion behaviour. If the ctx conversion shifts any colour or grouping,
these assertions fail (headless), independent of the interactive smoke pass.
"""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from well_viewer.data.data_loading import iter_plot_groups


def _ctx(selections, selected=(), solo=()):
    # A01,A02,A03,B01,B02,B03 — cache carries a distinct x per well (its index).
    wp = {f"{r}{c:02d}": f"/p/{r}{c:02d}.csv" for r in "AB" for c in range(1, 4)}
    cache = {w: pd.DataFrame({"x": [float(i)]}) for i, w in enumerate(wp)}
    state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths=wp, cache=cache),
        selection=SimpleNamespace(
            selections=list(selections),
            selected_wells=set(selected),
            active_solo_wells=set(solo),
        ),
    )
    return SimpleNamespace(state=state, app=SimpleNamespace(_app_state=state))


def _tuples(ctx, **kw):
    return [(n, c, list(df["x"])) for n, c, df in iter_plot_groups(ctx, **kw)]


def test_rep_sets_pool_and_colour_override_and_solo():
    ctx = _ctx(
        selections=[
            {"name": "G1", "wells": ["A01", "A02"], "hidden": False},
            {"name": "G2", "wells": ["B01"], "hidden": False, "color": "#123456"},
        ],
        solo=["A03"],
    )
    # G1 pools A01+A02 (rank colour of A01); G2 honours its explicit colour;
    # the solo well A03 yields last with its own rank colour.
    assert _tuples(ctx) == [
        ("G1", "#6AB2FB", [0.0, 1.0]),
        ("G2", "#123456", [3.0]),
        ("A03", "#0606F9", [2.0]),
    ]


def test_no_rep_sets_uses_selected_wells():
    ctx = _ctx(selections=[], selected=["A02", "A01"])
    assert _tuples(ctx) == [
        ("A01", "#6AB2FB", [0.0]),
        ("A02", "#06F906", [1.0]),
    ]


def test_no_selection_falls_back_to_all_wells():
    ctx = _ctx(selections=[])
    assert _tuples(ctx, fallback_to_all=True) == [
        ("A01", "#6AB2FB", [0.0]),
        ("A02", "#06F906", [1.0]),
        ("A03", "#0606F9", [2.0]),
        ("B01", "#774C22", [3.0]),
        ("B02", "#131386", [4.0]),
        ("B03", "#05C783", [5.0]),
    ]


def test_no_fallback_yields_nothing_without_selection():
    ctx = _ctx(selections=[])
    assert _tuples(ctx, fallback_to_all=False) == []
