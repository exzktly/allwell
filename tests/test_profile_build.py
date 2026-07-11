"""The binary build gates heavy deps OUT by feature, automatically from the catalog.

``scripts/profile_build`` is the pure math ``all_well.spec`` uses to decide what a
profiled binary excludes. These tests pin the contract the .spec can't self-test
(it needs PyInstaller's build-time globals): a feature's ``deps`` are dropped from
the bundle when the feature is profiled out — with no per-feature edit in the spec
— and the tracking opt-in reconciles correctly with a profiled-out Cellpose.
"""
from __future__ import annotations

from pathlib import Path

from scripts.profile_build import (
    admits,
    compute_exclusions,
    load_catalog,
    plan_ml,
)

_REPO = Path(__file__).resolve().parents[1]


def _catalog():
    return [
        {"name": "Multivariate", "paths": ["well_viewer/features/multivariate"],
         "deps": ["sklearn", "umap", "pynndescent"]},
        {"name": "Cellpose", "paths": ["pipeline/cellpose.py"],
         "deps": ["cellpose", "torch", "torchvision"]},
        {"name": "Image Table", "parent": "Image Browser",
         "paths": ["well_viewer/features/image_browser/image_table"]},
        {"name": "Spatial", "paths": ["well_viewer/features/spatial"]},
    ]


# ── admits() mirrors Profile.includes ────────────────────────────────────────

def test_admits_full_app_admits_everything():
    for e in _catalog():
        assert admits(e, None, {})


def test_admits_top_level_and_subtab():
    feats = {"Image Browser"}
    assert admits({"name": "Image Table", "parent": "Image Browser"},
                  feats, {"Image Browser": {"Segmentation"}}) is False
    assert admits({"name": "Segmentation", "parent": "Image Browser"},
                  feats, {"Image Browser": {"Segmentation"}}) is True
    assert admits({"name": "Multivariate"}, feats, {}) is False


# ── compute_exclusions: deps fall out of the catalog automatically ───────────

def test_thin_excludes_both_dep_bearing_features():
    ex = compute_exclusions(_catalog(), frozenset({"Plotting"}))
    assert set(ex.deps) == {"sklearn", "umap", "pynndescent",
                            "cellpose", "torch", "torchvision"}
    assert "pipeline/cellpose.py" in ex.repo_paths
    assert "Cellpose" in ex.features and "Multivariate" in ex.features


def test_admitting_multivariate_keeps_its_deps():
    ex = compute_exclusions(_catalog(), frozenset({"Plotting", "Multivariate"}))
    assert not ({"sklearn", "umap", "pynndescent"} & set(ex.deps))
    assert {"cellpose", "torch", "torchvision"} <= set(ex.deps)  # Cellpose still out


def test_full_profile_excludes_nothing():
    ex = compute_exclusions(_catalog(), None)
    assert ex.deps == [] and ex.features == [] and ex.repo_paths == []


# ── plan_ml: the Torch stack is collected only when Cellpose is present ───────

def test_full_build_collects_the_torch_stack():
    assert plan_ml([]) == ["torch", "torchvision", "cellpose"]


def test_cellpose_out_drops_the_torch_stack():
    assert plan_ml(["cellpose", "torch", "torchvision"]) == []


def test_any_one_cellpose_dep_excluded_drops_the_collect():
    # Defensive: even a partial exclusion means Cellpose is out, so skip collect.
    assert plan_ml(["torch"]) == []


def test_multivariate_out_still_collects_torch():
    # Multivariate's deps don't touch the Torch stack, so it still ships.
    assert plan_ml(["sklearn", "umap", "pynndescent"]) == \
        ["torch", "torchvision", "cellpose"]


# ── the real catalog agrees with the shipped thin profile ────────────────────

def test_real_thin_profile_drops_torch_and_sklearn():
    catalog = load_catalog(_REPO / "profiles" / "full.toml")
    prof = __import__("tomllib").loads(
        (_REPO / "profiles" / "thin.toml").read_text(encoding="utf-8"))
    ex = compute_exclusions(catalog, frozenset(prof["features"]),
                            prof.get("subtabs") or {})
    assert {"torch", "torchvision", "cellpose"} <= set(ex.deps)
    assert {"sklearn", "umap"} <= set(ex.deps)
    assert plan_ml(ex.deps) == []  # StarDist-only binary
