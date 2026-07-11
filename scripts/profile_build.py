"""Pure, stdlib-only build-profile math shared by ``all_well.spec`` and tests.

The PyInstaller spec cannot be imported (it needs PyInstaller's build-time
globals), so the logic that decides *what a profiled binary excludes* lived
inline and untestable. This module lifts the pure part out: given the full-app
catalog (``profiles/full.toml``) and a target profile, it computes the excluded
feature names, source paths, and — the point of the exercise — the heavy
third-party deps that get gated OUT of the binary.

The contract this makes verifiable: **a feature's heavy deps are dropped from
the binary automatically from its catalog ``deps``** — no per-feature edit in the
spec. The one subtlety is that opt-in tracking (``ALLWELL_WITH_TRACKING``) needs
Torch even when Cellpose is profiled out, so a package can be both "a profiled-out
feature's dep" and "force-collected"; :func:`plan_ml` and :func:`graph_excludes`
reconcile that so we never exclude from the module graph something we bundled.

Stdlib-only (``tomllib``); safe to import from the spec at build time.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


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


# The Cellpose feature's Torch stack — bundled unless BOTH Cellpose is profiled
# out AND tracking is off. Tracking additionally pulls its own training stack.
_CELLPOSE_ML = ["torch", "torchvision", "cellpose"]
_TRACKING_ML = ["btrack", "trackastra", "kornia", "lightning",
                "pytorch_lightning", "torchmetrics"]


def plan_ml(excluded_deps, with_tracking: bool) -> list:
    """ML packages to ``collect_all`` into the binary.

    Cellpose's Torch stack ships unless its deps were profiled out; tracking
    (opt-in) forces the Torch stack back in and adds its own training packages."""
    pkgs: list = []
    if not any(d in excluded_deps for d in _CELLPOSE_ML) or with_tracking:
        pkgs += list(_CELLPOSE_ML)
    if with_tracking:
        pkgs += list(_TRACKING_ML)
    return pkgs


def graph_excludes(excluded_deps, collected_ml) -> list:
    """The deps to add to PyInstaller ``excludes`` — the profiled-out deps minus
    anything we actually collected (never prune the module graph of a package
    whose .so payload we bundled, or it ships broken)."""
    return [d for d in excluded_deps if d not in collected_ml]
