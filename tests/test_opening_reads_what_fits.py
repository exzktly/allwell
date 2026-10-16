"""Opening a dataset reads rows while they fit, and a summary of every well (tracker M4).

A plate larger than the row-cache budget used to be read whole on open — the
crash in the report. Now the open reads rows only until the cache has had to
evict, and takes what the open needs from every well (timepoints, FOVs, tokens,
columns, the FOV-label verdict) from a per-well summary stored beside the
Parquet cache. These tests hold that the summaries say what the frames said, that
a stored summary spares the read and is replaced when the file changes, and that
the plate-wide lists are the whole plate's whatever is held.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from review.core.dataset_state import DatasetState
from review.data import data_loading
from review.data.row_cache import frame_bytes
from review.data.well_repository import WellRepository
from review.data.well_summary import read_summary, summarize_frame, summary_path, well_summary
from review.features.registry import build_feature_registry

_WELLS = {
    # label: (timepoint labels, fov labels) — each well brings a timepoint and a
    # field the others lack, so a list built from one held well is visibly short.
    "A01": (["0h", "0h", "24h"], ["1", "2", "1"]),
    "B02": (["0h", "48h", "48h"], ["1", "3", "3"]),
    "C03": (["72h", "72h", "0h"], ["4", "1", "1"]),
}


def _write(tmp_path, label, *, extra=0.0):
    tps, fovs = _WELLS[label]
    path = tmp_path / f"{label}.csv"
    pd.DataFrame({
        "well": [label] * 3,
        "fov": fovs,
        "timepoint": tps,
        "filename": [f"{label}_f{f}_t{t}.tif" for f, t in zip(fovs, tps)],
        "nucleus_id": ["1", "2", "3"],
        "gfp_mean_intensity": np.array([1.0, 2.0, 3.0]) + extra,
    }).to_csv(path, index=False)
    return path


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    import review.core.load_controller as lc

    # The progress bar is the window's; the loop under test is everything else.
    monkeypatch.setattr(lc, "_step_progress", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_opening_set_phase", lambda *a, **k: None)
    dataset = DatasetState()
    obj = SimpleNamespace(
        state=SimpleNamespace(dataset=dataset, review_image=SimpleNamespace(override_version=0),
                              qc=SimpleNamespace(params=None)),
        runtime=SimpleNamespace(review=SimpleNamespace(included_overrides={})))
    obj.state.repo = WellRepository(obj)
    build_feature_registry()
    obj.paths = {label: _write(tmp_path, label) for label in _WELLS}
    return obj


def _open(ctx, *, budget=None):
    """The loader's own loop over the wells (``load_controller._open_wells``)."""
    from review.core.load_controller import _open_wells

    if budget is not None:
        ctx.state.dataset.cache.set_budget(budget)
    app = SimpleNamespace(_extract_well_token=lambda stem: stem)
    _open_wells(ctx, app, list(ctx.paths.values()))
    ctx.state.dataset.rebuild_timepoints()
    ctx.state.dataset.rebuild_fovs()


def _one_well_budget(ctx):
    return frame_bytes(data_loading.read_well_frame(ctx.paths["A01"])) + 1


# ── a summary says what the frame says ──────────────────────────────────────


def test_a_plate_that_fits_opens_as_it_always_did(ctx):
    _open(ctx)
    assert sorted(ctx.state.dataset.cache.keys()) == ["A01", "B02", "C03"]
    assert ctx.state.dataset.all_timepoints_cache == [0.0, 24.0, 48.0, 72.0]
    assert ctx.state.dataset.all_fovs_cache == ["1", "2", "3", "4"]


def test_a_plate_that_does_not_fit_still_lists_every_timepoint_and_field(ctx):
    _open(ctx, budget=_one_well_budget(ctx))
    dataset = ctx.state.dataset
    assert len(dataset.cache) < 3, "every well was held; the test measures nothing"
    assert sorted(dataset.summaries) == ["A01", "B02", "C03"]
    assert dataset.all_timepoints_cache == [0.0, 24.0, 48.0, 72.0]
    assert dataset.all_fovs_cache == ["1", "2", "3", "4"]


def test_the_image_grid_options_come_from_every_well(ctx):
    from review.data.dataset_dims import fov_options, timepoint_options

    _open(ctx, budget=_one_well_budget(ctx))
    ctx.state.dataset.pipeline_info = {}
    assert fov_options(ctx) == ["1", "2", "3", "4"]
    assert timepoint_options(ctx) == ["0h", "24h", "48h", "72h"]


def test_opening_past_the_budget_stops_reading_rows(ctx, monkeypatch):
    _open(ctx)                                     # fills the Parquet cache and the summaries
    budget = _one_well_budget(ctx)
    reads: list = []
    real = data_loading.read_well_frame
    monkeypatch.setattr(data_loading, "read_well_frame", lambda path: reads.append(path.name) or real(path))
    fresh = DatasetState()
    ctx.state.dataset = fresh
    _open(ctx, budget=budget)
    # A01 fits; B02 evicts it, so loading stops; C03 is opened from its summary.
    assert reads == ["A01.csv", "B02.csv"]
    assert sorted(fresh.summaries) == ["A01", "B02", "C03"]


def test_a_summary_matches_the_frame_it_summarises(ctx):
    frame = data_loading.read_well_frame(ctx.paths["B02"])
    summary = summarize_frame(frame)
    assert summary.n_rows == 3
    assert summary.timepoints == (0.0, 48.0)
    assert summary.fovs == ("1", "3")
    assert summary.timepoint_tokens == ("0h", "48h")
    assert "gfp_mean_intensity" in summary.columns


def test_the_fov_verdict_is_recorded_for_a_well_opened_from_its_summary(ctx, tmp_path):
    bad = tmp_path / "D04.csv"
    pd.DataFrame({"well": ["D04"] * 4, "fov": ["1"] * 4, "timepoint": ["0h"] * 4,
                  "filename": [f"img{i}.tif" for i in range(4)], "nucleus_id": ["1", "2", "3", "4"]}
                 ).to_csv(bad, index=False)
    ctx.state.repo.open_well("D04", bad, load_rows=False)
    verdict = ctx.state.dataset.fov_integrity["D04"]
    assert verdict.trusted is False and verdict.n_images == 4


# ── the stored summary ──────────────────────────────────────────────────────


def test_a_stored_summary_spares_the_read(ctx, monkeypatch):
    well_summary(ctx.paths["A01"])
    assert summary_path(ctx.paths["A01"]).exists()
    monkeypatch.setattr(data_loading, "read_well_frame",
                        lambda path: pytest.fail("a stored summary should not read the frame"))
    import review.data.parquet_cache as pc
    monkeypatch.setattr(pc, "read_cached", lambda *a, **k: pytest.fail("nor the Parquet cache"))
    assert well_summary(ctx.paths["A01"]).fovs == ("1", "2")


def test_a_rewritten_csv_replaces_its_summary(ctx, tmp_path):
    well_summary(ctx.paths["A01"])
    _WELLS["Z09"] = (["96h"] * 3, ["7"] * 3)
    try:
        path = ctx.paths["A01"]
        new = _write(tmp_path, "Z09")
        path.write_bytes(new.read_bytes())
        assert read_summary(path) is None, "the stored summary describes the old file"
        assert well_summary(path).timepoints == (96.0,)
    finally:
        del _WELLS["Z09"]


def test_a_summary_is_not_stamped_with_a_file_that_changed_during_the_read(ctx):
    from review.data.parquet_cache import source_fingerprint
    from review.data.well_summary import write_summary

    path = ctx.paths["A01"]
    before = source_fingerprint(path)
    summary = summarize_frame(data_loading.read_well_frame(path))
    path.write_text(path.read_text() + "A01,9,0h,x.tif,4,1.0\n")
    assert write_summary(path, summary, before) is False
    assert read_summary(path) is None


def test_an_orphaned_summary_is_pruned(ctx):
    from review.data.parquet_cache import prune

    well_summary(ctx.paths["C03"])
    stored = summary_path(ctx.paths["C03"])
    ctx.paths["C03"].unlink()
    prune(ctx.paths["C03"].parent)
    assert not stored.exists()
