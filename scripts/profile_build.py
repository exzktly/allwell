"""Pure, stdlib-only build-profile math shared by ``all_well.spec`` and tests.

The PyInstaller spec cannot be imported (it needs PyInstaller's build-time
globals), so the logic that decides *what a profiled binary excludes* lived
inline and untestable. This module lifts the pure part out: given the full-app
catalog (``profiles/full.toml``) and a target profile, it computes the excluded
feature names, source paths, and — the point of the exercise — the heavy
third-party deps that get gated OUT of the binary.

The contract this makes verifiable: **a feature's heavy deps are dropped from
the binary automatically from its catalog ``deps``** — no per-feature edit in the
spec. Both the module-graph ``excludes`` and the ``collect_all`` skip derive from
the same computed set, because ``excludes`` alone does not drop a ``collect_all``'d
package (see :func:`plan_ml`).

Stdlib-only (``tomllib``); safe to import from the spec at build time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

try:  # tomllib is stdlib on 3.11+; fall back to tomli on 3.10 (build env pin).
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter version
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class Exclusions:
    features: list = field(default_factory=list)   # excluded feature names
    repo_paths: list = field(default_factory=list)  # repo-relative source paths
    deps: list = field(default_factory=list)        # import-name heavy deps


def load_catalog(path) -> list:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return list(data.get("feature") or [])


def admits(entry: dict, features, subtabs) -> bool:
    """Whether a *profile* (``features`` set + ``subtabs`` map) admits a catalog
    *entry*. ``features is None`` = the full app (admit everything). Mirrors
    ``well_viewer.core.profile.Profile.includes`` so the binary, the stripped
    tree, and the runtime subset always agree."""
    if features is None:
        return True
    parent = entry.get("parent")
    if parent:
        if parent not in features:
            return False
        allowed = subtabs.get(parent)
        return allowed is None or entry.get("name") in allowed
    return entry.get("name") in features


def compute_exclusions(catalog: list, features, subtabs=None) -> Exclusions:
    """The feature names / source paths / heavy deps a profile excludes."""
    subtabs = subtabs or {}
    ex = Exclusions()
    for entry in catalog:
        if admits(entry, features, subtabs):
            continue
        ex.features.append(entry.get("name"))
        ex.repo_paths += list(entry.get("paths") or [])
        ex.deps += list(entry.get("deps") or [])
    return ex


# The Cellpose feature's Torch stack — the only ``collect_all``'d ML packages.
# Bundled unless Cellpose is profiled out (its deps in ``excluded_deps``).
_CELLPOSE_ML = ["torch", "torchvision", "cellpose"]


def plan_ml(excluded_deps) -> list:
    """Torch-stack packages to ``collect_all`` into the binary.

    Cellpose's Torch stack ships unless its deps were profiled out. This is
    separate from the module-graph ``excludes`` on purpose: adding a package to
    ``excludes`` prunes the import graph but does NOT drop the ``datas`` /
    ``binaries`` that ``collect_all`` emits, so *skipping the collect* is what
    keeps the .so/.dylib payload out of the bundle."""
    if any(d in excluded_deps for d in _CELLPOSE_ML):
        return []
    return list(_CELLPOSE_ML)


# README_WellViewer.md ``###`` section heading → the feature it documents. A thin
# build ships a README trimmed to the profile's features (the in-app help drawer
# then documents only what the binary contains). Headings NOT listed here are
# always kept — the safe default is to keep a section we can't classify, so a new
# doc section never silently vanishes from a thin build.
README_SECTION_FEATURES = {
    "Bar Plots": "Plotting",
    "Line Graphs": "Plotting",
    "Scatter Plot": "Plotting",
    "Distribution": "Plotting",
    "Heat Map": "Plotting",
    "Statistics": "Statistics",
    "Multivariate analysis": "Multivariate",
    "Spatial analysis": "Spatial",
    "Image Browser": "Image Browser",
    "smFISH": "smFISH",
    "Review CSV": "Review CSV",
    "Sample Definitions": "Sample Definitions",
    "Saved Plots": "Saved Plots",
    "Batch export": "Batch Export",
}

_H1_3 = re.compile(r"^#{1,3} ")     # an h1/h2/h3 heading ends a ``###`` section
_H3 = re.compile(r"^### (.+?)\s*$")  # a ``### <title>`` feature section heading


def trim_readme_for_profile(readme_text: str, features) -> str:
    """Return *readme_text* with the ``###`` feature sections a profile excludes
    removed, so a thin binary ships help for only the features it contains.

    *features* is the profile's kept-feature-name set (``None`` = full app → the
    text is returned unchanged). A ``### <title>`` section whose title maps (via
    :data:`README_SECTION_FEATURES`) to a feature not in *features* is dropped
    from its heading up to the next h1/h2/h3 heading; deeper ``####`` sub-sections
    inside it go with it. Unmapped headings are always kept."""
    if features is None:
        return readme_text
    kept = set(features)
    lines = readme_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _H3.match(lines[i])
        feat = README_SECTION_FEATURES.get(m.group(1).strip()) if m else None
        if feat is not None and feat not in kept:
            i += 1
            while i < len(lines) and not _H1_3.match(lines[i]):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out)
