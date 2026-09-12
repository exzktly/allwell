"""Pure-logic tests for the Cell Browser sort builder (no Qt)."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from review.features.image_browser.cell_browser.collect import (
    SortKey,
    collect_cells,
    sort_field_options,
)


def _df_for(well, gfp, tps):
    n = len(gfp)
    return pd.DataFrame({
        "Included": [1] * n,
        "timepoint_hours": [float(t) for t in tps],
        "timepoint": [str(int(t)) for t in tps],
        "fov": ["1"] * n,
        "filename": [f"{well}_{i}.tif" for i in range(n)],
        "nucleus_id": [f"{well}{i}" for i in range(n)],
        "gfp_mean_intensity": list(gfp),
    })


def _multi_app(frames: dict):
    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(well_paths={k: None for k in frames},
                                cache=dict(frames)),
        selection=SimpleNamespace(selected_wells=list(frames)),
        channel=SimpleNamespace(ratio_index={}, fluor_channels=["gfp"]),
    )
    from review.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state,
                               runtime=AppRuntime(app))
    return app


def _gfp_order(cells):
    return [c.nucleus_id for c in cells]


# ── sort_field_options ───────────────────────────────────────────────────────

def test_sort_field_options_has_intrinsics_then_metrics():
    app = _multi_app({"A01": _df_for("A01", [3.0, 1.0, 2.0], [0, 0, 24])})
    fields = dict(sort_field_options(app._ctx))
    assert "well" in fields and "timepoint" in fields and "fov" in fields
    assert "gfp_mean_intensity" in fields
    # Intrinsics come first.
    keys = [k for k, _ in sort_field_options(app._ctx)]
    assert keys[0] == "well"


# ── single-key sort ──────────────────────────────────────────────────────────

def test_sort_metric_ascending():
    app = _multi_app({"A01": _df_for("A01", [3.0, 1.0, 2.0], [0, 0, 0])})
    cells = collect_cells(app._ctx, [], None, None,
                          [SortKey("gfp_mean_intensity", ascending=True)])
    # nuclei A010=3, A011=1, A012=2 → ascending by value: A011(1),A012(2),A010(3)
    assert _gfp_order(cells) == ["A011", "A012", "A010"]


def test_sort_metric_descending():
    app = _multi_app({"A01": _df_for("A01", [3.0, 1.0, 2.0], [0, 0, 0])})
    cells = collect_cells(app._ctx, [], None, None,
                          [SortKey("gfp_mean_intensity", ascending=False)])
    assert _gfp_order(cells) == ["A010", "A012", "A011"]


def test_sort_by_timepoint():
    app = _multi_app({"A01": _df_for("A01", [1.0, 2.0, 3.0], [24, 0, 24])})
    cells = collect_cells(app._ctx, [], None, None, [SortKey("timepoint", ascending=True)])
    # tps: A010=24, A011=0, A012=24 → ascending: A011(0) first, then the two 24s
    # keep their relative (stable) order: A010, A012.
    assert _gfp_order(cells) == ["A011", "A010", "A012"]


def test_sort_by_well():
    app = _multi_app({
        "A01": _df_for("A01", [9.0], [0]),
        "B01": _df_for("B01", [1.0], [0]),
    })
    asc = collect_cells(app._ctx, [], None, None, [SortKey("well", ascending=True)])
    assert _gfp_order(asc) == ["A010", "B010"]
    desc = collect_cells(app._ctx, [], None, None, [SortKey("well", ascending=False)])
    assert _gfp_order(desc) == ["B010", "A010"]


# ── multi-key precedence ─────────────────────────────────────────────────────

def test_multi_key_primary_then_secondary():
    # Primary: timepoint asc; secondary: gfp desc within a timepoint.
    app = _multi_app({"A01": _df_for(
        "A01", gfp=[5.0, 8.0, 2.0, 9.0], tps=[24, 0, 24, 0])})
    cells = collect_cells(app._ctx, [], None, None, [
        SortKey("timepoint", ascending=True),
        SortKey("gfp_mean_intensity", ascending=False),
    ])
    # tp 0 group: A011(8), A013(9) → gfp desc: A013(9), A011(8)
    # tp 24 group: A010(5), A012(2) → gfp desc: A010(5), A012(2)
    assert _gfp_order(cells) == ["A013", "A011", "A010", "A012"]


# ── NaN / missing always last ────────────────────────────────────────────────

def test_missing_metric_sorts_last_both_directions():
    app = _multi_app({"A01": _df_for("A01", [3.0, float("nan"), 1.0], [0, 0, 0])})
    asc = collect_cells(app._ctx, [], None, None,
                        [SortKey("gfp_mean_intensity", ascending=True)])
    assert _gfp_order(asc) == ["A012", "A010", "A011"]  # NaN (A011) last
    desc = collect_cells(app._ctx, [], None, None,
                         [SortKey("gfp_mean_intensity", ascending=False)])
    assert _gfp_order(desc) == ["A010", "A012", "A011"]  # NaN (A011) still last


def test_no_sort_keys_keeps_natural_order():
    app = _multi_app({"A01": _df_for("A01", [3.0, 1.0, 2.0], [0, 0, 0])})
    cells = collect_cells(app._ctx, [], None, None, [])
    assert _gfp_order(cells) == ["A010", "A011", "A012"]
