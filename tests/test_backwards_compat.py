"""Backwards-compatibility gate.

Datasets analyzed *before* the segmentation / QC / shape-features / tracking
features must keep loading and behaving identically. This harness is introduced
in PR1 (foundation) and is extended by each feature PR (e.g. PR2 adds the
"legacy CSV has no shape columns / no Shape Features dropdown section" check
once ``detect_shape_features`` exists).

See _Docs/Markdowns/TODO_segmentation_qc_shape_tracking.md §2.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.shape_features as sfc
from well_viewer.domain.viewer_state import read_pipeline_info

REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY = REPO_ROOT / "tests" / "fixtures" / "legacy_dataset"


# ── pipeline_info.json read path ─────────────────────────────────────────────

def test_legacy_pipeline_info_defaults_new_keys():
    """A pre-feature sidecar (no new keys) reads through with empty defaults and
    unchanged existing behaviour."""
    extractor, fluor, smfish, info = read_pipeline_info(LEGACY)

    # Existing behaviour is preserved exactly.
    assert fluor == ["nir", "gfp"]
    assert smfish == set()
    assert callable(extractor)
    assert extractor("exp_GFP_A01_F001_24h") == ("F001", "24h")
    assert info["nuclear_token"] == "nir"
    assert info["available_timepoints"] == ["0", "24h", "48h"]

    # New feature keys default to empty / legacy on a pre-feature sidecar.
    assert info["shape_features"] == []
    assert info["qc_options"] == {}
    assert info["phase_token"] == ""
    assert info["schema_version"] == 1
    # segmentation_method is present in legacy sidecars and parses through.
    assert info["segmentation_method"] == "stardist_nuclei"


def test_pipeline_info_new_keys_parsed(tmp_path):
    """A sidecar written by the new pipeline carries the feature keys."""
    (tmp_path / "pipeline_info.json").write_text(json.dumps({
        "schema": "experiment:channel:well:fov:timepoint",
        "separator": "_",
        "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
        "well_index": 2, "channel_index": 1, "fov_index": 3, "tp_index": 4,
        "nuclear_token": "Phase",
        "fluor_tokens": ["GFP"],
        "smfish_tokens": [],
        "schema_version": 2,
        "segmentation_method": "cellpose",
        "phase_token": "Phase",
        "shape_features": ["Eccentricity", "solidity"],
        "qc_options": {"flatfield": "gaussian", "qc_remove_debris": True},
    }))
    _, _, _, info = read_pipeline_info(tmp_path)
    assert info["schema_version"] == 2
    assert info["segmentation_method"] == "cellpose"
    assert info["phase_token"] == "phase"
    assert info["shape_features"] == ["eccentricity", "solidity"]
    assert info["qc_options"] == {"flatfield": "gaussian", "qc_remove_debris": True}


def test_missing_pipeline_info_returns_empty(tmp_path):
    extractor, fluor, smfish, info = read_pipeline_info(tmp_path, check_parent=False)
    assert extractor is None
    assert fluor == []
    assert smfish == set()
    assert info == {}


# ── shape_features_core catalog ──────────────────────────────────────────────

def test_shape_features_core_catalog():
    assert sfc.is_shape_column("shape_eccentricity")
    assert not sfc.is_shape_column("gfp_mean_intensity")

    # Centroids are always-on (not user-selectable) — needed by tracking.
    assert "centroid_row" not in sfc.SELECTABLE_KEYS
    assert "eccentricity" in sfc.SELECTABLE_KEYS

    cols = sfc.emitted_columns(["eccentricity", "bogus"])
    assert "shape_eccentricity" in cols
    assert "shape_centroid_row" in cols and "shape_centroid_col" in cols
    assert "shape_bogus" not in cols  # unknown keys are dropped

    # Derived feature expands to its inputs; centroid always included.
    props = sfc.regionprops_properties(["circularity"])
    assert "area" in props and "perimeter" in props and "centroid" in props

    # Shape metrics carry no cell-gating threshold; intensity metrics do.
    assert sfc.has_threshold("shape_area") is False
    assert sfc.has_threshold("gfp_mean_intensity") is True


# ── Review-side detection (backwards-compatible) ─────────────────────────────

def test_detect_shape_features_legacy_vs_new():
    pd = pytest.importorskip("pandas")
    from well_viewer.data.data_loading import detect_shape_features

    # Legacy CSV (intensity only) → no shape columns → no dropdown section.
    legacy = pd.DataFrame({
        "well": ["A01"], "fov": ["F001"], "timepoint": ["24h"],
        "nucleus_id": [1], "area_px": [150], "gfp_mean_intensity": [80.0],
    })
    assert detect_shape_features(legacy) == []

    # New CSV with shape columns → returned in catalog order.
    new = legacy.copy()
    new["shape_solidity"] = 0.97
    new["shape_eccentricity"] = 0.4
    new["shape_centroid_row"] = 19.5
    new["shape_centroid_col"] = 19.5
    out = detect_shape_features(new)
    assert out == [c for c in sfc.ALL_COLUMNS if c in set(new.columns)]
    # eccentricity precedes solidity in the catalog; centroids come last.
    assert out.index("shape_eccentricity") < out.index("shape_solidity")
    assert "shape_centroid_row" in out and "shape_centroid_col" in out
