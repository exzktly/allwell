"""A narrow read applies every verdict a full read does (tracker F2).

``review/data/narrow_rows.py`` reads a well's Parquet cache for only the columns
an aggregate and the verdict layers need, and runs the same projectors on that
frame. The hazard it has to answer is D2's: every verdict fails open, so a narrow
frame missing one verdict's input would pass every cell that verdict excludes,
and nothing would say so.

So these tests are built on a fixture where that difference is visible. One well
of 32 cells, in which **each of the seven layers excludes cells no other layer
does** — ``test_each_layer_excludes_its_own_cells`` proves it, layer by layer —
and in which most of the file's columns are ones no verdict reads, so a narrow
read is actually narrow. With every layer on at once, the narrow frame's
inclusion mask and the aggregate computed from it must equal the full frame's.

A real ``AppState``, ``AppRuntime``, feature registry and Parquet cache, rather
than a namespace: the projectors read settings from half a dozen state slices,
and a namespace holds exactly the attributes the code under test happens to
read (CLAUDE.md §7.8).
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from review.data import data_loading as dl
from review.data.data_loading import df_included_mask
from review.domain.qc_params import QCParams
from review.domain.ratio_models import RatioMetric

pytest.importorskip("pyarrow.parquet")

WELL = "A01"
RATIO = RatioMetric(name="g_over_c", numerator_channel="gfp", numerator_metric="mean_intensity",
                    denominator_channel="cy5", denominator_metric="mean_intensity")

# Cells each layer, alone, excludes. FOV 1 is cells 0-3 and FOV 8 cells 28-31.
QC_CELL, CROP_CELL, FOCUS_CELL, CURATED_CELL, GATED_CELL = 5, 9, 13, 17, 21


def _frame() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    n = 32
    df = pd.DataFrame({
        "well": [WELL] * n,
        "fov": [str(1 + i // 4) for i in range(n)],
        "timepoint": ["0"] * n,
        "timepoint_hours": [0.0] * n,
        "filename": [f"{WELL}_f{1 + i // 4}.tif" for i in range(n)],
        "nucleus_id": [str(1 + i % 4) for i in range(n)],
        "area_px": np.full(n, 120.0),
        "shape_solidity": np.full(n, 0.95),
        "shape_circularity": np.full(n, 0.8),
        "qc_region_area": np.full(n, 150.0),
        "shape_centroid_row": rng.uniform(100, 400, n),
        "shape_centroid_col": rng.uniform(100, 400, n),
        "qc_edge_focus": np.full(n, 0.5),
        "gfp_mean_intensity": rng.uniform(50, 100, n),
        "cy5_mean_intensity": rng.uniform(5, 10, n),
        "mcherry_mean_intensity": rng.uniform(1, 500, n),
    })
    for i in range(10):                      # columns no verdict and no aggregate reads
        df[f"m{i}"] = rng.normal(size=n)
    df.loc[QC_CELL, "area_px"] = 10.0
    df.loc[CROP_CELL, ["shape_centroid_row", "shape_centroid_col"]] = 900.0
    df.loc[FOCUS_CELL, "qc_edge_focus"] = 0.01
    df.loc[GATED_CELL, "gfp_mean_intensity"] = 1.0
    return df


def _field_quality_block():
    """FOV 1 wildly out of line with the other seven, so it is flagged."""
    from pipeline.field_quality import FIELD_QUALITY_BLOCK

    rows = [{"well": WELL, "fov": str(f), "timepoint": "0",
             "focus": (0.0001 if f == 1 else 0.30 + 0.001 * f),
             "sat_frac": 0.0, "dyn_range": 1000.0, "nuc_count": 50,
             "nuc_area_median": 80.0, "bg_level": 100.0}
            for f in range(1, 9)]
    return {FIELD_QUALITY_BLOCK: {"wells": {WELL: {"fields": rows}}, "partial_wells": []}}


@pytest.fixture
def ctx(tmp_path):
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.data.parquet_cache import read_cached
    from review.data.well_repository import WellRepository
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext

    build_feature_registry()                  # registers the projectors and their declarations
    state = AppState()
    attach_feature_state(state)
    path = tmp_path / f"{WELL}.csv"
    _frame().to_csv(path, index=False)
    state.dataset.data_dir = tmp_path
    state.dataset.well_paths = {WELL: path}
    state.dataset.pipeline_info = _field_quality_block()
    state.dataset.fov_integrity = {}
    state.channel.ratio_index = {RATIO.key(): RATIO}
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    dl.read_well_frame(path)                  # fills the Parquet cache
    assert read_cached(path) is not None, "no Parquet cache; the narrow path would never be taken"
    return c


def _curation_key(ctx):
    from domain.image_identity import cell_keys_for_dataframe

    key = cell_keys_for_dataframe(_frame(), default_well=WELL).iloc[CURATED_CELL]
    return (WELL, key.fov, key.tp, key.nid)


def _intensity_gating():
    return {"fluor_gates": {"gfp": 10.0}, "use_scatter": False, "gating_gates": [],
            "use_thresh": False, "thresh_gates": [], "ratios": {RATIO.key(): RATIO}}


def _ratio_scatter_gating():
    """Scatter gates on a ratio axis and a channel no other layer reads; the
    thresholding gate on another pair of axes, so ``Gate_On`` reads them too."""
    gate = {"x_col": RATIO.key(), "y_col": "cy5_mean_intensity", "x0": 6.0, "x1": 100.0, "y0": 0.0, "y1": 100.0}
    thresh = {"x_col": "mcherry_mean_intensity", "y_col": "shape_solidity",
              "x0": 0.0, "x1": 250.0, "y0": 0.0, "y1": 1.0}
    return {"fluor_gates": {}, "use_scatter": True, "gating_gates": [gate],
            "use_thresh": True, "thresh_gates": [thresh], "ratios": {RATIO.key(): RATIO}}


def _switch_on(ctx, layer: str, gating=_intensity_gating) -> None:
    params = ctx.state.qc.params or QCParams()
    if layer == "gating":
        ctx.state.cell_gating.applied_gating = gating()
    elif layer == "curation":
        ctx.runtime.review.included_overrides = {
            **(ctx.runtime.review.included_overrides or {}), _curation_key(ctx): "0"}
    elif layer == "qc":
        ctx.state.qc.params = dataclasses.replace(params, enabled=True, min_area=50.0)
    elif layer == "crop":
        ctx.state.dataset.crop_region = (0, 0, 800, 800)
    elif layer == "field_quality":
        ctx.state.qc.params = dataclasses.replace(params, exclude_flagged_fields=True)
    elif layer == "focus":
        ctx.state.qc.params = dataclasses.replace(params, exclude_soft_cells=True, focus_min=0.1)
    elif layer == "field_exclusions":
        ctx.state.dataset.field_exclusions[(WELL, "8", "0")] = {"reason": "test", "at": "now"}
    elif layer == "fov_selection":
        # Every field but 7: the Cell Gating tab's FOV picker.
        ctx.state.cell_gating.fov_selection = {"1", "2", "3", "4", "5", "6", "8"}
    else:
        raise KeyError(layer)


LAYERS = ("gating", "curation", "qc", "crop", "field_quality", "focus", "field_exclusions")
#: Layers proven here but kept out of :data:`LAYERS`, which four other files
#: import with cell counts pinned against exactly that roster. The FOV
#: selection (Cell Gating's FOV picker) excludes FOV 7, cells 24-27.
LOCAL_LAYERS = LAYERS + ("fov_selection",)


def _full(ctx) -> pd.DataFrame:
    ctx.state.dataset.cache.clear()
    frame = dl.get_rows(ctx, WELL)
    ctx.state.dataset.cache.clear()
    return frame


def _narrow(ctx, want=("mcherry_mean_intensity",)) -> pd.DataFrame:
    from review.data.narrow_rows import narrow_rows

    ctx.state.dataset.cache.clear()
    return narrow_rows(ctx, WELL, set(want))


def _excluded(frame) -> set[int]:
    return set(np.flatnonzero(~df_included_mask(frame).to_numpy()))


def test_each_layer_excludes_its_own_cells(ctx):
    """The fixture is what makes the equality below mean something: a layer
    whose verdict failed open on the narrow frame must change the mask."""
    expected = {"gating": {GATED_CELL}, "curation": {CURATED_CELL}, "qc": {QC_CELL},
                "crop": {CROP_CELL}, "field_quality": {0, 1, 2, 3}, "focus": {FOCUS_CELL},
                "field_exclusions": {28, 29, 30, 31}, "fov_selection": {24, 25, 26, 27}}
    assert _excluded(_full(ctx)) == set()
    for layer in LOCAL_LAYERS:
        ctx.state.qc.params = None
        ctx.state.cell_gating.applied_gating = None
        ctx.runtime.review.included_overrides = {}
        ctx.state.dataset.crop_region = None
        ctx.state.dataset.field_exclusions.clear()
        ctx.state.cell_gating.fov_selection = None
        _switch_on(ctx, layer)
        assert _excluded(_full(ctx)) == expected[layer], layer


@pytest.mark.parametrize("gating", [_intensity_gating, _ratio_scatter_gating], ids=["intensity", "ratio_scatter"])
def test_with_every_layer_on_the_narrow_mask_is_the_full_mask(ctx, gating):
    for layer in LOCAL_LAYERS:
        _switch_on(ctx, layer, gating)
    full, narrow = _full(ctx), _narrow(ctx)
    filler = {f"m{i}" for i in range(10)}
    assert filler <= set(full.columns) and not filler & set(narrow.columns), (
        "the read was not narrow; this measures nothing")
    assert len(_excluded(full)) >= 12, "the layers did not all bite"
    verdicts = ["Included", "QC_Pass", "Crop_Pass", "Gate_Pass", "Gate_On", "Field_Pass",
                "Focus_Pass", "Excluded_Field_Pass", "FOV_Pass"]
    for column in verdicts:
        assert (column in narrow.columns) == (column in full.columns), column
        if column in full.columns:
            assert narrow[column].tolist() == full[column].tolist(), column
    assert df_included_mask(narrow).tolist() == df_included_mask(full).tolist()


@pytest.mark.parametrize("val_col", ["mcherry_mean_intensity", RATIO.key()])
@pytest.mark.parametrize("per_fov_spread", [False, True])
def test_the_aggregate_of_a_narrow_read_is_the_aggregate_of_the_full_one(ctx, val_col, per_fov_spread):
    from review.domain.stats_compute import aggregate_group, aggregate_well

    for layer in LAYERS:
        _switch_on(ctx, layer)
    threshold = 9.0 if val_col == RATIO.key() else 250.0     # about half the cells are "on"
    kw = dict(threshold=threshold, use_sem=False, val_col=val_col, fluor_gates={"cy5": 6.0},
              per_fov_spread=per_fov_spread, tp_col="timepoint_hours")
    ctx.state.dataset.cache.clear()
    narrow = aggregate_well(ctx, WELL, **kw)
    assert WELL not in ctx.state.dataset.cache, "the aggregate read the whole frame into the row cache"
    dl.get_rows(ctx, WELL)                    # now held whole: the aggregate reads the held frame
    full = aggregate_well(ctx, WELL, **kw)
    assert narrow and _same(narrow, full)
    assert not np.isnan(narrow[0][1]), "nothing was on; the means compared are NaN on both sides"
    ctx.state.dataset.cache.clear()
    pooled_narrow = aggregate_group(ctx, [WELL, WELL], **kw)
    assert WELL not in ctx.state.dataset.cache
    dl.get_rows(ctx, WELL)
    assert pooled_narrow and _same(pooled_narrow, aggregate_group(ctx, [WELL, WELL], **kw))


@pytest.mark.parametrize("val_col", ["mcherry_mean_intensity", RATIO.key()])
def test_with_no_layer_on_the_aggregate_reads_its_own_columns(ctx, val_col):
    """No verdict reads anything here, so only the aggregate's own declaration —
    the value (both halves of a ratio) and the area its debris floor reads —
    can bring those columns in."""
    from review.domain.stats_compute import aggregate_well

    threshold = 9.0 if val_col == RATIO.key() else 250.0
    kw = dict(threshold=threshold, use_sem=False, val_col=val_col, fluor_gates={},
              per_fov_spread=False, tp_col="timepoint_hours")
    ctx.state.dataset.cache.clear()
    narrow = aggregate_well(ctx, WELL, **kw)
    dl.get_rows(ctx, WELL)
    full = aggregate_well(ctx, WELL, **kw)
    assert full and not np.isnan(full[0][1])
    assert _same(narrow, full)


def test_a_thresholding_column_in_the_file_is_read(ctx, tmp_path):
    """With thresholding switched on but no gating pass applied, ``Gate_On`` is
    whatever the file carries, and the aggregate's fraction-on is read from it."""
    from review.domain.stats_compute import aggregate_well

    path = ctx.state.dataset.well_paths[WELL]
    frame = _frame()
    frame["Gate_On"] = [1 if i % 3 else 0 for i in range(len(frame))]
    frame.to_csv(path, index=False)
    dl.read_well_frame(path)
    ctx.state.cell_gating.use_scatter_thresh = True
    ctx.state.cell_gating.scatter_gates = [{"name": "g", "x_col": "m0", "y_col": "m1",
                                            "x0": 0.0, "x1": 1.0, "y0": 0.0, "y1": 1.0}]
    ctx.state.cell_gating.thresh_choices = None
    kw = dict(threshold=0.0, use_sem=False, val_col="mcherry_mean_intensity", fluor_gates={},
              per_fov_spread=False, tp_col="timepoint_hours")
    ctx.state.dataset.cache.clear()
    narrow = aggregate_well(ctx, WELL, **kw)
    dl.get_rows(ctx, WELL)
    full = aggregate_well(ctx, WELL, **kw)
    assert full[0][3] == pytest.approx(21 / 32), "the fraction on is not Gate_On's; the test measures nothing"
    assert _same(narrow, full)


def _same(a, b) -> bool:
    """Equal point for point, NaN equal to NaN (an absent spread is NaN on both sides)."""
    def same(x, y):
        return (isinstance(x, float) and isinstance(y, float) and np.isnan(x) and np.isnan(y)) or x == y
    return len(a) == len(b) and all(len(p) == len(q) and all(map(same, p, q)) for p, q in zip(a, b))


def test_the_narrow_frame_never_enters_the_row_cache(ctx):
    _switch_on(ctx, "qc")
    _narrow(ctx)
    assert WELL not in ctx.state.dataset.cache


def test_a_held_well_is_returned_as_held(ctx):
    from review.data.narrow_rows import narrow_rows

    held = dl.get_rows(ctx, WELL)
    assert narrow_rows(ctx, WELL, {"mcherry_mean_intensity"}) is held


def test_an_undeclared_projector_reads_the_whole_frame(ctx, monkeypatch):
    import review.data.cache_projectors as cp

    monkeypatch.setattr(cp, "_PROJECTORS", [*cp._PROJECTORS, (lambda c, label=None: None, None)])
    assert len(_narrow(ctx).columns) == len(_full(ctx).columns)


def test_a_declaration_that_cannot_answer_reads_the_whole_frame(ctx, monkeypatch):
    import review.data.cache_projectors as cp

    def broken(c, label=None):
        raise RuntimeError("no settings")

    monkeypatch.setattr(cp, "_PROJECTORS", [*cp._PROJECTORS, (lambda c, label=None: None, broken)])
    assert len(_narrow(ctx).columns) == len(_full(ctx).columns)


def test_an_unknown_ratio_reads_the_whole_frame(ctx):
    _switch_on(ctx, "gating", _ratio_scatter_gating)
    ctx.state.cell_gating.applied_gating["ratios"] = {}
    assert len(_narrow(ctx).columns) == len(_full(ctx).columns)


def test_every_registered_projector_declares_its_columns():
    """A projector registered without a declaration costs no correctness — the
    read goes whole — but it silently turns every narrow read off. Say so."""
    from review.data.cache_projectors import iter_projector_declarations
    from review.features.registry import build_feature_registry

    build_feature_registry()
    undeclared = [getattr(p, "__name__", p) for p, cols in iter_projector_declarations() if cols is None]
    assert undeclared == []


@pytest.mark.parametrize("spelling", ["time", "tp", "time_h", "Time"])
def test_every_timepoint_spelling_the_identity_resolver_accepts_is_read(ctx, spelling):
    """Review finding: the narrow read kept ``timepoint`` but not ``time`` / ``tp``
    / ``time_h``, which the identity resolver also reads. Dropped, the curation
    keys and the crop scope built from it matched nothing and the manually
    excluded cell counted again."""
    path = ctx.state.dataset.well_paths[WELL]
    frame = _frame().rename(columns={"timepoint": spelling})
    frame.to_csv(path, index=False)
    dl.read_well_frame(path)
    _switch_on(ctx, "curation")
    full, narrow = _full(ctx), _narrow(ctx)
    assert spelling in narrow.columns
    assert _excluded(full) == {CURATED_CELL}, "the override matched nothing in the full frame either"
    assert _excluded(narrow) == _excluded(full)
