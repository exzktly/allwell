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
    bundle_name,
    compute_exclusions,
    load_catalog,
    plan_ml,
    trim_readme_for_profile,
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


# ── README trimming: a thin binary ships help for only its features ──────────

_SAMPLE_README = """\
# All-Well

## Contents

1. [Install](#install)
   - Plotting: [Bar Plots](#bar-plots) · [Distribution](#distribution)
   - [Multivariate analysis](#multivariate-analysis) · [Spatial analysis](#spatial-analysis)
   - [Home](#home) · [Batch export](#batch-export)
2. [Building](#building)

## Review mode

### Bar Plots
Bars docs. See [Multivariate analysis](#multivariate-analysis) for more.

#### A bar sub-heading
More bar docs.

### Multivariate analysis
Multivariate docs.

### Spatial analysis
Spatial docs.

### Home
Home docs.

## Building
Build docs (unmapped ## heading — always kept).
"""


# ── bundle_name — versioned + profile-tagged artifact name ───────────────────
def test_bundle_name_full_app_is_version_only():
    assert bundle_name("8.8.8") == "AllWell-8.8.8"


def test_bundle_name_appends_profile():
    assert bundle_name("8.8.8", "thin") == "AllWell-8.8.8-thin"


def test_bundle_name_full_and_empty_profiles_add_no_suffix():
    # Mirrors all_well.spec's _profile_active rule: "" / "full" = the full app.
    assert bundle_name("8.8.8", "") == "AllWell-8.8.8"
    assert bundle_name("8.8.8", "full") == "AllWell-8.8.8"
    assert bundle_name("8.8.8", "FULL") == "AllWell-8.8.8"
    assert bundle_name("8.8.8", "  full  ") == "AllWell-8.8.8"


def test_trim_readme_full_app_is_unchanged():
    # features=None → the full app → return the text verbatim.
    assert trim_readme_for_profile(_SAMPLE_README, None) == _SAMPLE_README


def test_trim_readme_drops_excluded_feature_sections():
    out = trim_readme_for_profile(_SAMPLE_README, {"Plotting", "Home"})
    # Kept: Plotting (Bar Plots + its #### sub-heading), Home.
    assert "### Bar Plots" in out
    assert "A bar sub-heading" in out       # the #### sub-section rode along
    assert "### Home" in out
    # Dropped: the Multivariate / Spatial section headings + bodies.
    assert "### Multivariate analysis" not in out
    assert "### Spatial analysis" not in out
    assert "Multivariate docs." not in out
    assert "Spatial docs." not in out
    # Unmapped headings (title h1, the ## Building section) are always kept.
    assert "## Building" in out
    assert "# All-Well" in out


def test_trim_readme_prunes_the_contents_index():
    out = trim_readme_for_profile(_SAMPLE_README, {"Plotting", "Home"})
    toc = out.split("## Review mode")[0]   # the Contents section only
    # Kept features' index links survive.
    assert "[Bar Plots](#bar-plots)" in toc
    assert "[Home](#home)" in toc
    # Excluded features' index links are pruned (no broken anchors advertised).
    assert "multivariate-analysis" not in toc
    assert "spatial-analysis" not in toc
    assert "batch-export" not in toc
    # The sub-bullet that listed ONLY excluded sections is gone entirely.
    assert "Multivariate analysis" not in toc


def test_trim_readme_leaves_inline_prose_links_intact():
    # A [Title](#anchor) cross-reference inside a KEPT section's prose is not the
    # Contents index and must survive verbatim (the pruning is TOC-scoped).
    out = trim_readme_for_profile(_SAMPLE_README, {"Plotting", "Home"})
    body = out.split("## Review mode")[1]
    assert "See [Multivariate analysis](#multivariate-analysis) for more." in body


_SUBTAB_README = """\
# All-Well

## Contents

1. [Review mode](#review-mode)
   - [Image Browser](#image-browser) (Segmentation · Image Table · Cell Browser · Playground)

## Review mode

### Image Browser
Intro to the browser.

#### Segmentation
Seg docs.

#### Image Table
Table docs.

#### Cell Browser
Cell docs.

#### Playground
Playground docs.

### Home
Sample docs.
"""


def test_trim_readme_drops_excluded_subtab_sections():
    # Image Browser kept, but only its Segmentation sub-tab is included.
    out = trim_readme_for_profile(
        _SUBTAB_README, {"Image Browser", "Home"},
        {"Image Browser": {"Segmentation"}})
    assert "### Image Browser" in out          # the feature stays
    assert "#### Segmentation" in out          # the included sub-tab stays
    assert "Seg docs." in out
    # The excluded sub-tabs (and their bodies) are gone.
    for gone in ("#### Image Table", "Table docs.", "#### Cell Browser",
                 "Cell docs.", "#### Playground", "Playground docs."):
        assert gone not in out, gone
    # The Contents parenthetical is filtered to the included sub-tab.
    assert "(Segmentation)" in out
    assert "Image Table" not in out
    # A feature with no sub-tab restriction keeps everything.
    assert "### Home" in out


def test_trim_readme_no_subtab_restriction_keeps_all_subtabs():
    # Image Browser in `features` but absent from `subtabs` → all sub-tabs kept.
    out = trim_readme_for_profile(_SUBTAB_README, {"Image Browser"}, {})
    for kept in ("#### Segmentation", "#### Image Table", "#### Cell Browser",
                 "#### Playground"):
        assert kept in out, kept


def test_trim_readme_matches_real_thin_profile():
    import tomllib
    readme = (_REPO / "_Docs" / "Markdowns" / "README_WellViewer.md").read_text(
        encoding="utf-8")
    prof = tomllib.loads(
        (_REPO / "profiles" / "thin.toml").read_text(encoding="utf-8"))
    out = trim_readme_for_profile(readme, prof["features"])
    # thin = Plotting + Home + Image Browser.
    assert "### Bar Plots" in out
    assert "### Image Browser" in out
    assert "### Home" in out
    # Everything the thin profile excludes is gone from the shipped help.
    for dropped in ("### Multivariate analysis", "### Spatial analysis",
                    "### smFISH", "### Review CSV", "### Saved Plots",
                    "### Batch export", "### Statistics"):
        assert dropped not in out, dropped
