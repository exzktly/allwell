"""A plate whose FOV labels never include ``"1"`` loads and aggregates.

The Review pane crashed on dataset load with

    TypeError: Cannot setitem on a Categorical with a new category (1),
    set the categories first

raised from ``gating_compute._cell_gating_first_frame_rows`` while the loader
was still opening the dataset. The loader stores the six text columns as
``pd.Categorical`` with the observed values as categories, and
``Series.fillna("1")`` validates ``"1"`` against that list whether or not any
cell needs filling. Any plate whose field labels are numbered from zero, or
zero-padded, or simply do not start at one, has no ``"1"`` category and every
consumer that normalises the FOV column that way raises.

These are the consumers, driven against a frame built the way the loader builds
it. The fixture is the point: a plate whose FOV labels are ``2`` and ``3``, and
whose timepoint labels are likewise not the values anything fills with.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from review.data.data_loading import (
    aggregate_with_threshold_df,
    load_well_csv,
)
from domain.image_identity import cell_keys_for_dataframe
from review.domain.fov_integrity import check_fov_labels
from review.domain.stats_compute import _norm_fov
from domain.text_columns import as_text, fov_text, text_column
from review.features.home.cell_gating.gating_compute import (
    _cell_gating_first_frame_rows,
)

_ROWS = 12


def _write_plate(tmp_path, fovs=("2", "3"), timepoints=("4", "8")):
    """A well CSV whose FOV and timepoint labels are the ones given."""
    rows = []
    for i in range(_ROWS):
        fov = fovs[i % len(fovs)]
        tp = timepoints[(i // len(fovs)) % len(timepoints)]
        rows.append({
            "well": "A1",
            "fov": fov,
            "timepoint": tp,
            "timepoint_hours": float(tp),
            "filename": f"A1_f{fov}_t{tp}.tif",
            "experiment": "exp",
            "channel": "w1",
            "nucleus_id": i,
            "area_px": 100.0,
            "w1_mean_intensity": float(i + 1),
            "Included": 1,
        })
    path = tmp_path / "A1.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


@pytest.fixture
def plate(tmp_path) -> pd.DataFrame:
    return load_well_csv(_write_plate(tmp_path))


def test_the_loader_types_the_plate_the_way_the_bug_needs(plate):
    """The fixture reproduces the reported condition, not a near miss.

    Without this the rest of the file could pass on an object-dtype column and
    prove nothing at all.
    """
    assert isinstance(plate["fov"].dtype, pd.CategoricalDtype)
    assert "1" not in set(plate["fov"].cat.categories)
    assert set(plate["fov"].cat.categories) == {"", "2", "3"}


def test_cell_gating_reads_the_first_frame(plate):
    """The reported crash: reached from `load_path` on every dataset open."""
    frame_df, desc = _cell_gating_first_frame_rows(plate)
    assert desc == "fov=2, tp=4"
    assert len(frame_df) == _ROWS // 4


def test_per_fov_aggregation_reads_the_labels(plate):
    """`aggregate_with_threshold_df` normalises the same column."""
    points = aggregate_with_threshold_df(
        plate, threshold=0.0, use_sem=False,
        val_col="w1_mean_intensity", per_fov_spread=True,
    )
    assert points, "no timepoints aggregated"
    # Two fields per timepoint, so the per-field spread is defined rather than
    # NaN — which is what proves the labels were read, not merely not-crashed.
    assert all(np.isfinite(p[2]) for p in points)


def test_stats_normalises_the_labels(plate):
    assert sorted(set(_norm_fov(plate))) == ["2", "3"]


def test_fov_integrity_checks_the_labels(plate):
    verdict = check_fov_labels(plate)
    assert verdict.trusted
    assert verdict.n_labels == 2


def test_cell_keys_are_built(plate):
    keys = cell_keys_for_dataframe(plate)
    assert len(keys) == _ROWS
    assert {k.fov for k in keys} == {"2", "3"}


def test_dataset_state_rebuilds_its_menus(plate):
    from review.core.dataset_state import DatasetState

    state = DatasetState()
    state.cache["A1"] = plate
    state.rebuild_fovs()
    state.rebuild_timepoints()
    assert state.all_fovs_cache == ["2", "3"]
    assert state.all_timepoints_cache == [4.0, 8.0]


# ── the helper itself ───────────────────────────────────────────────────────

def test_as_text_maps_missing_and_blank_onto_the_default():
    s = pd.Series(pd.Categorical(["2", None, "  ", "3"], categories=["", "2", "3"]))
    assert list(as_text(s, default="1")) == ["2", "1", "1", "3"]


def test_as_text_keeps_blanks_when_the_default_is_blank():
    s = pd.Series(["a", None, " b "])
    assert list(as_text(s)) == ["a", "", "b"]


def test_as_text_leaves_an_object_column_alone():
    s = pd.Series(["2", "3"])
    assert list(as_text(s, default="1")) == ["2", "3"]


def test_text_column_fills_in_for_an_absent_column():
    df = pd.DataFrame({"v": [1.0, 2.0]})
    assert list(fov_text(df)) == ["1", "1"]
    assert list(text_column(df, "timepoint")) == ["", ""]


def test_text_column_of_an_empty_frame_is_empty():
    df = pd.DataFrame({"fov": pd.Series([], dtype=object)})
    assert list(fov_text(df)) == []
