"""Characterization guard for the ctx-native Review Image draw web (A8 D4).

The Review Image redraw (``refresh_review_image`` → ``draw_review_image``) and
the Image Table binary-threshold thumbnails both bottom out in the same pure
data helpers: resolve the fluor/mask refs for the selected FOV/timepoint, build
the per-cell include map, and build the per-cell above-threshold map. D4 moved
those helpers from an ``app``-typed first argument to a ``ctx``-typed one.

A mis-mapped attribute in that migration paints the *wrong* pixels — the wrong
cells outlined, an all-black binary mask (this is exactly the latent bug the
Image Table hit: ``image_compose`` already passed ``ctx`` where the helper still
expected ``app``, so every call fell into ``except`` → all-off mask). These
tests drive the data path on a hand-built fake ``ctx`` so any such mis-map fails
here, headlessly, instead of silently mis-rendering in the GUI.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from review.features.image_browser.review_image.collect import (
    review_build_include_map,
    review_build_threshold_map,
    review_resolve_image_refs,
)


def _ctx(
    *,
    df=None,
    well="A01",
    fluor_channels=("gfp",),
    fluor_gates=None,
    thresh_frac_on=None,
    active_image_channel="gfp",
    active_metric="mean_intensity",
    active_val_col="gfp_mean_intensity",
    image_source="corrected",
    fluor_map=None,
    tophat_map=None,
    mask_map=None,
    overrides=None,
):
    """Minimal ctx exposing only what the three data leaves read."""
    state = SimpleNamespace(
        dataset=SimpleNamespace(
            cache={well: df} if df is not None else {},
            well_paths={well: "x.csv"},
            pipeline_info={},
        ),
        channel=SimpleNamespace(
            fluor_channels=list(fluor_channels),
            active_image_channel=active_image_channel,
            active_metric=active_metric,
            active_val_col=active_val_col,
            active_channel="gfp",
            ratio_index={},
            threshold=-1.0,
        ),
        cell_gating=SimpleNamespace(
            fluor_gates=dict(fluor_gates or {}),
            thresh_frac_on=dict(thresh_frac_on or {}),
        ),
        review_image=SimpleNamespace(image_source=image_source),
        preview=SimpleNamespace(
            fluor=dict(fluor_map or {}),
            tophat_fluor=dict(tophat_map or {}),
            mask=dict(mask_map or {}),
        ),
    )
    runtime = SimpleNamespace(
        review=SimpleNamespace(included_overrides=dict(overrides or {})),
    )
    return SimpleNamespace(state=state, runtime=runtime,
                           notify=SimpleNamespace(set_status=lambda *_a: None))


def _df(rows):
    return pd.DataFrame(rows)


# ── review_resolve_image_refs ────────────────────────────────────────────────


def test_resolve_refs_corrected_prefers_tophat_then_falls_back_to_raw():
    ctx = _ctx(
        tophat_map={("1", "0"): "tophat_ref"},
        fluor_map={("1", "0"): "raw_ref"},
        mask_map={("1", "0"): "mask_ref"},
        image_source="corrected",
    )
    fluor_ref, mask_ref = review_resolve_image_refs(ctx, fov_raw="1", tp_raw="0")
    assert fluor_ref == "tophat_ref"          # corrected → top-hat first
    assert mask_ref == "mask_ref"


def test_resolve_refs_raw_source_prefers_raw_frame():
    ctx = _ctx(
        tophat_map={("1", "0"): "tophat_ref"},
        fluor_map={("1", "0"): "raw_ref"},
        mask_map={("1", "0"): "mask_ref"},
        image_source="raw",
    )
    fluor_ref, _ = review_resolve_image_refs(ctx, fov_raw="1", tp_raw="0")
    assert fluor_ref == "raw_ref"             # raw/flatfield → raw frame first


def test_resolve_refs_falls_back_when_primary_missing():
    # corrected wants top-hat first; only raw exists → fall back to raw.
    ctx = _ctx(
        tophat_map={},
        fluor_map={("1", "0"): "raw_ref"},
        mask_map={("1", "0"): "mask_ref"},
        image_source="corrected",
    )
    fluor_ref, mask_ref = review_resolve_image_refs(ctx, fov_raw="1", tp_raw="0")
    assert fluor_ref == "raw_ref"
    assert mask_ref == "mask_ref"


# ── review_build_include_map ─────────────────────────────────────────────────


def test_include_map_reflects_csv_included_column():
    df = _df([
        {"fov": "1", "timepoint": "0", "nucleus_id": "1", "Included": "1"},
        {"fov": "1", "timepoint": "0", "nucleus_id": "2", "Included": "0"},
    ])
    ctx = _ctx(df=df)
    mask = np.array([[0, 1, 2]], dtype=np.int32)
    out = review_build_include_map(ctx, mask, "A01", "1", "0")
    assert out == {1: True, 2: False}


def test_include_map_manual_override_wins_over_csv():
    df = _df([
        {"fov": "1", "timepoint": "0", "nucleus_id": "1", "Included": "1"},
    ])
    # User toggled nucleus 1 to excluded via the Segmentation tab.
    ctx = _ctx(df=df, overrides={("A01", "1", "0", "1"): "0"})
    mask = np.array([[0, 1]], dtype=np.int32)
    out = review_build_include_map(ctx, mask, "A01", "1", "0")
    assert out == {1: False}


def test_include_map_qc_fail_excludes_regardless_of_included():
    df = _df([
        {"fov": "1", "timepoint": "0", "nucleus_id": "1", "Included": "1",
         "QC_Pass": "0"},
    ])
    ctx = _ctx(df=df)
    mask = np.array([[0, 1]], dtype=np.int32)
    out = review_build_include_map(ctx, mask, "A01", "1", "0")
    assert out == {1: False}


# ── review_build_threshold_map (the latent-bug path) ─────────────────────────


def test_threshold_map_marks_cells_above_on_threshold():
    # ON threshold on gfp = 50; cell 1 (intensity 80) is above, cell 2 (10) below.
    df = _df([
        {"fov": "1", "timepoint": "0", "nucleus_id": "1", "Included": "1",
         "gfp_mean_intensity": 80.0},
        {"fov": "1", "timepoint": "0", "nucleus_id": "2", "Included": "1",
         "gfp_mean_intensity": 10.0},
    ])
    ctx = _ctx(df=df, thresh_frac_on={"gfp": 50.0},
               active_val_col="gfp_mean_intensity")
    mask = np.array([[0, 1, 2]], dtype=np.int32)
    out = review_build_threshold_map(ctx, mask, "A01", "1", "0")
    assert out == {1: True, 2: False}


def test_threshold_map_excludes_non_included_cells():
    # Cell 2 is above the value threshold but manually Included=0 → not "above".
    df = _df([
        {"fov": "1", "timepoint": "0", "nucleus_id": "1", "Included": "1",
         "gfp_mean_intensity": 80.0},
        {"fov": "1", "timepoint": "0", "nucleus_id": "2", "Included": "0",
         "gfp_mean_intensity": 90.0},
    ])
    ctx = _ctx(df=df, thresh_frac_on={"gfp": 50.0},
               active_val_col="gfp_mean_intensity")
    mask = np.array([[0, 1, 2]], dtype=np.int32)
    out = review_build_threshold_map(ctx, mask, "A01", "1", "0")
    assert out == {1: True, 2: False}


def test_threshold_map_gates_on_displayed_channel_via_channel_arg():
    # image_compose passes the cell's OWN channel; the value column + ON
    # threshold must come from ``channel`` (dapi), not active_image_channel (gfp).
    df = _df([
        {"fov": "1", "timepoint": "0", "nucleus_id": "1", "Included": "1",
         "dapi_mean_intensity": 30.0},
    ])
    ctx = _ctx(df=df, fluor_channels=("dapi",), active_image_channel="gfp",
               thresh_frac_on={"dapi": 20.0, "gfp": 100.0})
    mask = np.array([[0, 1]], dtype=np.int32)
    out = review_build_threshold_map(ctx, mask, "A01", "1", "0", channel="dapi")
    assert out == {1: True}   # 30 > dapi ON-threshold 20 (gfp's 100 ignored)
