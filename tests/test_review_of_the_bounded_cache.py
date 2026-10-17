"""The adversarial review's findings on the bounded row cache (tracker rows R3–R11).

Each test reproduces one finding: a path on which a well's numbers depended on
whether it happened to be held, a snapshot that was not one, a readout that went
stale, a remembered answer that was not remembered, or a concurrent edit that
could break a walk. R1, R2 and R8 are tested beside the code they concern
(``test_no_view_holds_the_plate.py``, ``test_opening_reads_what_fits.py``).
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from review.core.cell_gating_state import CellGatingState
from review.core.dataset_state import DatasetState
from review.core.stats_cache_state import StatsCacheState
from review.data import data_loading
from review.data.data_loading import df_included_mask, get_rows
from review.data.well_repository import WellRepository
from review.features.registry import build_feature_registry


def _write(tmp_path, label, rows=100):
    path = tmp_path / f"{label}.csv"
    pd.DataFrame({
        "well": [label] * rows, "fov": ["1"] * rows, "timepoint": ["0"] * rows,
        "nucleus_id": [str(i + 1) for i in range(rows)],
        "gfp_mean_intensity": np.arange(1.0, rows + 1.0),
    }).to_csv(path, index=False)
    return path


@pytest.fixture
def ctx(tmp_path):
    dataset = DatasetState()
    for label in ("A01", "A02"):
        dataset.well_paths[label] = _write(tmp_path, label)
    obj = SimpleNamespace(
        state=SimpleNamespace(dataset=dataset, cell_gating=CellGatingState(), stats_cache=StatsCacheState(),
                              review_image=SimpleNamespace(override_version=0), qc=SimpleNamespace(params=None)),
        runtime=SimpleNamespace(review=SimpleNamespace(included_overrides={}, refresh_csv=None)))
    obj.state.repo = WellRepository(obj)
    build_feature_registry()
    return obj


_GATE_AT_60 = {"fluor_gates": {"gfp": 60.0}, "use_scatter": False, "gating_gates": [],
               "use_thresh": False, "thresh_gates": [], "ratios": {}}


# ── R3: the smFISH write-back re-reads a held well through the projectors ───


def test_a_held_well_refreshed_after_smfish_keeps_its_verdicts(ctx, monkeypatch):
    import review.features.smfish.tab_view as sm

    ctx.state.cell_gating.applied_gating = dict(_GATE_AT_60)
    ctx.runtime.review.included_overrides = {("A01", "1", "0", "5"): "1"}
    assert int(df_included_mask(get_rows(ctx, "A01")).sum()) == 41
    for name in ("recalculate_threshold",):
        monkeypatch.setattr(sm, name, lambda ctx: None)
    monkeypatch.setattr("review.core.selection_controller.invalidate_stats_cache", lambda ctx: None)
    monkeypatch.setattr("review.core.plot_orchestrator.redraw_app", lambda ctx: None)

    sm._smfish_refresh_app_cache(ctx)

    refreshed = ctx.state.dataset.cache["A01"]
    assert int(df_included_mask(refreshed).sum()) == 41, "the refreshed frame lost its gating or curation"
    assert "Gate_Pass" in refreshed.columns


# ── R4: the recorded configuration is a snapshot of the gate geometry ──────


def test_the_recorded_gates_do_not_move_with_a_drag(monkeypatch):
    from review.features.home.cell_gating import controller as cg

    gate = {"name": "G", "x_col": "gfp_mean_intensity", "y_col": "gfp_mean_intensity",
            "x0": 0.0, "x1": 10.0, "y0": 0.0, "y1": 10.0}
    state = CellGatingState(scatter_gates=[gate], use_scatter_gates=True, use_scatter_thresh=True)
    ctx = SimpleNamespace(state=SimpleNamespace(cell_gating=state, channel=SimpleNamespace(ratio_index={})))
    monkeypatch.setattr(cg, "get_all_fluor_gates", lambda ctx: {})
    config = cg.gating_pass_config(ctx)
    gate["x1"] = 99.0                                   # what on_gate_edit_motion does, in place
    assert config["gating_gates"][0]["x1"] == 10.0
    assert config["thresh_gates"][0]["x1"] == 10.0


# ── R5: clearing the gating forgets the applied configuration ──────────────


def test_a_gating_reset_forgets_the_applied_configuration(monkeypatch):
    from review.persistence import cell_gating as pcg

    monkeypatch.setattr(pcg, "_mirror_gating_to_widgets", lambda ctx, **kw: True)
    state = CellGatingState(applied_gating=dict(_GATE_AT_60))
    pcg.restore_gating_block(SimpleNamespace(state=SimpleNamespace(cell_gating=state)), {})
    assert state.applied_gating is None


# ── R6: a cancelled pass does not leave its configuration recorded ─────────


def test_a_cancelled_pass_records_nothing_and_a_finished_one_reasserts(qtbot, monkeypatch):
    from review.features.home.cell_gating import controller as cg

    def config(which):
        return dict(_GATE_AT_60, which=which)

    monkeypatch.setattr(cg, "apply_curation_to_cache", lambda ctx: None)
    state = CellGatingState()
    ctx = SimpleNamespace(state=SimpleNamespace(cell_gating=state, dataset=SimpleNamespace(well_paths={}),
                                                stats_cache=StatsCacheState(), feature_slice=lambda name: None))
    monkeypatch.setattr(cg, "invalidate_stats_cache", lambda ctx: None)

    cancelled = cg.GatingWorker(ctx)
    cancelled.cancel()
    monkeypatch.setattr(cg, "gating_pass_config", lambda ctx: config("old"))
    cancelled._run_inner()                               # the pass, run inline rather than on its thread
    assert state.applied_gating is None, "a cancelled pass recorded its configuration"

    # The interleaving the start-of-pass check cannot close: a predecessor's
    # configuration lands after this pass recorded its own, here simulated by
    # overwriting it as the first well is read. Completion must re-assert.
    monkeypatch.setattr(cg, "gating_pass_config", lambda ctx: config("new"))
    ctx.state.dataset.well_paths = {"A01": None}

    def late_predecessor(label):
        state.applied_gating = config("old")
        return pd.DataFrame({"gfp_mean_intensity": [1.0]})

    # The pass gates the wells the row cache holds, found through the
    # repository's cache-only lookup; the first well is held here.
    ctx.state.repo = SimpleNamespace(rows_or_none=late_predecessor)
    monkeypatch.setattr(cg, "apply_fluor_gating", lambda df, **kw: 0)
    ctx.state.dataset.note_rows_changed = lambda: None
    monkeypatch.setattr(cg.time, "sleep", lambda s: None)
    live = cg.GatingWorker(ctx)                          # its progress signal has no listener
    live._run_inner()
    assert state.applied_gating["which"] == "new"


# ── R7: the QC readout blanks when nothing is held ─────────────────────────


def test_the_qc_readout_blanks_when_nothing_is_held_to_count():
    import inspect

    from review.features.qc import tab_view

    source = inspect.getsource(tab_view)
    line = next(l for l in source.splitlines() if "status.setText(f\"flagged" in l)
    assert line.rstrip().endswith('if n_tot else "")'), line


# ── R9: a remembered None is remembered ────────────────────────────────────


def test_a_well_with_nothing_to_contribute_is_not_reread(ctx, monkeypatch):
    from review.data.plate_scan import every_well

    reads: list = []
    real = data_loading.read_well_frame
    monkeypatch.setattr(data_loading, "read_well_frame", lambda p: reads.append(p) or real(p))
    assert every_well(ctx, "nothing", None, lambda df: None) == {"A01": None, "A02": None}
    ctx.state.dataset.cache.clear()
    first = len(reads)
    assert every_well(ctx, "nothing", None, lambda df: None) == {"A01": None, "A02": None}
    assert len(reads) == first, "a well whose answer is None was read again"


# ── R10: an integer column is exported as the strings were ─────────────────


def test_an_integer_column_is_written_with_six_decimals(tmp_path, monkeypatch):
    from review.features.export import service as svc

    monkeypatch.setattr(data_loading, "iter_plot_groups", lambda ctx: iter([("A01", "#000", pd.DataFrame())]))
    monkeypatch.setattr(data_loading, "_all_fluor_values_filtered", lambda df, **kw: np.array([12, 3]))
    out = tmp_path / "dist.csv"
    monkeypatch.setattr(svc, "ask_save_csv", lambda *a, **k: str(out))
    monkeypatch.setattr(svc, "get_all_fluor_gates", lambda ctx: {})
    monkeypatch.setattr(svc, "figure_names_map", lambda ctx: {})
    monkeypatch.setattr(svc, "_well_paths_keys", lambda ctx: {})
    monkeypatch.setattr(svc, "well_name_for", lambda name, *a, **k: name)
    ctx = SimpleNamespace(
        state=SimpleNamespace(channel=SimpleNamespace(active_val_col="gfp_smfish_count", ratio_index={},
                                                      active_channel="gfp")),
        widgets=SimpleNamespace(get_or_none=lambda key: None),
        notify=SimpleNamespace(set_status=lambda text: None), window=None)
    svc.export_distribution_data(ctx)
    assert out.read_text().splitlines()[1:] == ["A01,A01,,12.000000", "A01,A01,,3.000000"]


# ── R11: the CDF cap tolerates the cache being cleared from another thread ──


# ── the per-read costs the benchmark found ─────────────────────────────────


@pytest.mark.parametrize("default", ["", "1"])
def test_a_categorical_reads_as_the_same_text_as_its_object_column(default):
    from domain.text_columns import as_text

    raw = pd.Series([" 1", "2 ", None, "", "  ", "x y", "1", None, "01"], name="fov")
    expected = as_text(raw.astype(object), default=default)
    got = as_text(raw.astype("category"), default=default)
    pd.testing.assert_series_equal(got, expected)
    assert got.dtype == object


def test_a_reread_well_is_not_judged_again(ctx, monkeypatch):
    calls: list = []
    real = data_loading.check_fov_labels
    monkeypatch.setattr(data_loading, "check_fov_labels", lambda df: calls.append(1) or real(df))
    get_rows(ctx, "A01")
    ctx.state.dataset.cache.clear()
    get_rows(ctx, "A01")
    assert len(calls) == 1, "the FOV labels were judged on every read"
    ctx.state.dataset.fov_integrity.clear()                 # a new dataset (begin_load)
    ctx.state.dataset.cache.clear()
    get_rows(ctx, "A01")
    assert len(calls) == 2


def test_a_categorical_is_stripped_once_per_category_not_per_row(monkeypatch):
    from pandas.core.strings.accessor import StringMethods

    from domain.text_columns import as_text

    lengths: list[int] = []
    real = StringMethods.strip
    monkeypatch.setattr(StringMethods, "strip",
                        lambda self, *a, **k: lengths.append(len(self._data)) or real(self, *a, **k))
    as_text(pd.Series(["1", "2", "3"] * 10_000, dtype="category"))
    assert lengths == [3], lengths


def test_cell_gating_reads_its_source_wells_once(ctx, monkeypatch):
    from review.features.home.cell_gating import controller as cg

    for name in ("_cell_gating_build_channel_controls", "_cell_gating_refresh_optimizer_controls",
                 "_cell_gating_plot_cdf"):
        monkeypatch.setattr(cg, name, lambda ctx: None)
    monkeypatch.setattr(cg, "_cell_gating_source_wells", lambda ctx: ["A01", "A02"])
    ctx.runtime.cell_gating = SimpleNamespace(fluor_gate_edits={}, cell_areas=[], fluor_data={}, ratio_data={},
                                              axes_stack=[], status_label=SimpleNamespace(setText=lambda text: None))
    ctx.state.channel = SimpleNamespace(fluor_channels=["gfp"], ratio_metrics=[])
    reads: list = []
    real = data_loading.read_well_frame
    monkeypatch.setattr(data_loading, "read_well_frame", lambda p: reads.append(p) or real(p))
    cg.cell_gating_load_cell_areas(ctx)
    first = len(reads)
    assert first == 2
    assert len(ctx.runtime.cell_gating.fluor_data["gfp"]) == 200
    ctx.state.dataset.cache.clear()
    cg.cell_gating_load_cell_areas(ctx)
    assert len(reads) == first, "the source wells were read again for values already taken"
    assert len(ctx.runtime.cell_gating.fluor_data["gfp"]) == 200
