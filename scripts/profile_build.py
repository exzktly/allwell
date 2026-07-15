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


def path_is_excluded(repo_rel: str, exclude_repo_dirs) -> bool:
    """Whether a repo-relative path is dropped by a profile's exclusion list.

    A path is excluded if it *is* an excluded entry or lives under one (so an
    excluded feature dir like ``well_viewer/features/stats`` drops every file
    beneath it). This is the single match rule shared by ``all_well.spec``'s
    per-file bundler (``_pkg_datas``) and the guard tests, so the binary and the
    tests can never disagree about what a profile ships."""
    for d in exclude_repo_dirs:
        d = d.rstrip("/")
        if repo_rel == d or repo_rel.startswith(d + "/"):
            return True
    return False


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

#: The ``#### <title>`` sub-headings under a feature's ``### `` section that are
#: sub-TABS (so trimming a profile's excluded sub-tabs never touches an ordinary
#: prose sub-section). Titles match both the README heading and the profile's
#: sub-tab names.
README_SUBTAB_TITLES = {
    "Image Browser": {"Segmentation", "Image Table", "Cell Browser", "Playground"},
}

_H1_3 = re.compile(r"^#{1,3} ")     # an h1/h2/h3 heading ends a ``###`` section
_H1_4 = re.compile(r"^#{1,4} ")     # an h1..h4 heading ends a ``####`` sub-section
_H1 = re.compile(r"^# ")
_H3 = re.compile(r"^### (.+?)\s*$")  # a ``### <title>`` feature section heading
_H4 = re.compile(r"^#### (.+?)\s*$")  # a ``#### <title>`` sub-tab heading
_H2 = re.compile(r"^## ")            # an h2 heading ends the Contents section
_TOC_HEAD = re.compile(r"^## Contents\s*$", re.IGNORECASE)
_TOC_SUBBULLET = re.compile(r"^\s+- ")           # a Contents sub-bullet line
_MD_LINK = re.compile(r"\[([^\]]+)\]\(#[^)]*\)")  # a [Title](#anchor) link
# A ``(a · b · c)`` sub-tab list — REQUIRES a ``·`` so it never matches a link's
# ``(#anchor)`` URL (which would break the ``](#`` and drop the whole link).
_PARENS = re.compile(r"\(([^)]*·[^)]*)\)")


def _trim_toc_line(line: str, excluded_titles: set,
                   excluded_subtabs: set = frozenset()) -> str | None:
    """Strip ``[Title](#anchor)`` links to excluded features from one Contents
    sub-bullet — and excluded sub-tab names from a ``(a · b · c)`` parenthetical
    — tidying the ``·`` separators left behind. Returns the cleaned line, or
    ``None`` if it no longer lists any section (drop it)."""
    def _drop(m):
        return "" if m.group(1).strip() in excluded_titles else m.group(0)
    body = _MD_LINK.sub(_drop, line.rstrip("\n"))

    def _filter_parens(m):
        parts = [p.strip() for p in m.group(1).split("·")]
        keep = [p for p in parts if p and p not in excluded_subtabs]
        return f" ({' · '.join(keep)})" if keep else ""
    if excluded_subtabs:
        # ``(Segmentation · Image Table · …)`` → ``(Segmentation)`` (or gone).
        body = re.sub(r"\s*" + _PARENS.pattern, _filter_parens, body)

    body = re.sub(r"·(?:\s*·)+", "·", body)   # collapse doubled separators
    body = re.sub(r"·\s*$", "", body)          # trailing separator
    body = re.sub(r"(-\s+)·\s*", r"\1", body)  # leading separator after the bullet
    body = body.rstrip()
    if "](#" not in body:                       # no sections left → drop the bullet
        return None
    return body + ("\n" if line.endswith("\n") else "")


def trim_readme_for_profile(readme_text: str, features, subtabs=None) -> str:
    """Return *readme_text* with the ``###`` feature sections a profile excludes
    removed, so a thin binary ships help for only the features it contains.

    *features* is the profile's kept-feature-name set (``None`` = full app → the
    text is returned unchanged). A ``### <title>`` section whose title maps (via
    :data:`README_SECTION_FEATURES`) to a feature not in *features* is dropped
    from its heading up to the next h1/h2/h3 heading; deeper ``####`` sub-sections
    inside it go with it. Unmapped headings are always kept.

    *subtabs* is the profile's ``{feature: allowed-sub-tab-set}`` map: for a KEPT
    feature that restricts its sub-tabs (e.g. thin's ``Image Browser`` = only
    ``Segmentation``), the ``#### <sub-tab>`` sections it does NOT include are
    dropped too — so the help gets tab-level, not just feature-level, accuracy.
    Only ``####`` headings that are known sub-tabs (:data:`README_SUBTAB_TITLES`)
    are eligible; ordinary prose sub-sections are always kept.

    The ``## Contents`` table of contents is pruned in step: link entries pointing
    at a dropped section are removed (and a sub-bullet that lists only dropped
    sections is deleted), and a feature's ``(sub · tab · list)`` parenthetical is
    filtered to its included sub-tabs. The pruning is scoped to the Contents
    section so the same ``[Title](#anchor)`` links used inline in prose are left
    intact."""
    if features is None:
        return readme_text
    kept = set(features)
    subtabs = subtabs or {}
    excluded_titles = {t for t, f in README_SECTION_FEATURES.items() if f not in kept}
    # Sub-tabs to drop: a kept feature's known sub-tab titles minus the ones its
    # profile actually includes. A feature with no ``subtabs`` entry keeps all.
    allowed_by_feat = {feat: set(subtabs[feat])
                       for feat in README_SUBTAB_TITLES if feat in kept and feat in subtabs}
    excluded_subtabs = {t for feat, allowed in allowed_by_feat.items()
                        for t in README_SUBTAB_TITLES[feat] if t not in allowed}
    lines = readme_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    in_contents = False
    cur_feat = None
    while i < len(lines):
        line = lines[i]
        if _TOC_HEAD.match(line):
            in_contents = True
        elif in_contents and _H2.match(line):
            in_contents = False
        # Track the current ``### <feature>`` scope; drop excluded features whole.
        m3 = _H3.match(line)
        if m3:
            feat = README_SECTION_FEATURES.get(m3.group(1).strip())
            if feat is not None and feat not in kept:
                i += 1
                while i < len(lines) and not _H1_3.match(lines[i]):
                    i += 1
                continue
            cur_feat = feat
        elif _H2.match(line) or _H1.match(line):
            cur_feat = None
        # Drop an excluded ``#### <sub-tab>`` within a sub-tab-restricted feature.
        m4 = _H4.match(line)
        if m4 and cur_feat in allowed_by_feat:
            title = m4.group(1).strip()
            if (title in README_SUBTAB_TITLES[cur_feat]
                    and title not in allowed_by_feat[cur_feat]):
                i += 1
                while i < len(lines) and not _H1_4.match(lines[i]):
                    i += 1
                continue
        # Prune excluded links / sub-tabs from a Contents sub-bullet.
        if in_contents and _TOC_SUBBULLET.match(line) and "](#" in line:
            cleaned = _trim_toc_line(line, excluded_titles, excluded_subtabs)
            if cleaned is None:
                i += 1
                continue
            out.append(cleaned)
            i += 1
            continue
        out.append(line)
        i += 1
    return "".join(out)
