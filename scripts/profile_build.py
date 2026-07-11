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
