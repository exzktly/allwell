"""Every site that mutates a cached measurement frame, enumerated from the AST.

``aggregate_well``'s results are memoized per well, and one component of that
memo's key is ``DatasetState.rows_version`` — a counter that must be bumped by
every path which changes what a cached frame contains. Miss one and the plots
draw a number computed from rows that no longer exist, and report it as current.
That is the failure mode the whole memo is built to avoid, so the roster of
writers cannot be a grep result someone once ran.

It is derived here instead, from the syntax tree, over the scan surface in
``_repo_scan``. Three shapes count as writing to a frame:

* ``df[col] = ...`` — a subscript assignment onto a name bound from the cache;
* ``df.loc[...] = ...`` / ``df.iloc[...] = ...`` — the indexer forms, which a
  search for ``df[`` misses entirely;
* ``...(inplace=True)`` — the keyword form, which mutates without an assignment
  statement at all. **Detected but not attributed**: the keyword says nothing
  about *which* object is being mutated, so a hit cannot be pinned to a cached
  frame and is reported here rather than failed on. The app's one in-place frame
  mutation is ``cache_projectors``' ``df.drop(columns=[column], inplace=True)``,
  which removes a verdict column on a projection failure and is covered by that
  file's roster entry. Do not read this shape as guarded.

A grep for the first shape alone is what produced the original writer list, and
it missed ``curation.apply_curation_to_cache``'s ``df.loc[mask, "Included"] = ...``
— the manual per-cell curation projection, reached from four call sites, one of
which does not invalidate afterwards.

The guard is a roster with a justification per entry rather than a blanket ban,
because mutating a cached frame is legitimate: it is how the QC, crop, gating and
smFISH layers project their verdicts onto the rows every plot reads. What is not
legitimate is doing so from a site nobody has considered. A new writer fails this
test until it is listed here *and* its author has said, in the entry, how
``rows_version`` comes to be bumped for it.
"""

from __future__ import annotations

import ast

import pytest

from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source

#: Names that, when subscript-assigned or mutated in place, are treated as a
#: cached measurement frame. These are the conventional spellings across the
#: app for "the frame ``get_rows`` handed me".
_FRAME_NAMES = frozenset({"df", "frame", "rows", "sub", "cached"})

#: Columns whose value changes what an aggregation computes. A write to any of
#: these on a cached frame changes plotted numbers; a write to some unrelated
#: scratch column does not, which is why the guard is scoped to these rather
#: than to every column assignment in the app.
_INCLUSION_COLUMNS = frozenset({
    "Included", "QC_Pass", "Crop_Pass", "Gate_Pass", "Gate_On",
})

#: The known writers, each with the reason it is allowed to exist.
#:
#: Keyed by ``"<path>::<column>"``. The value is why this site is sound — which
#: must say how the row-cache version comes to be bumped, because that is the
#: single thing standing between a mutation here and a stale plotted number.
_KNOWN_WRITERS: dict[str, str] = {
    "well_viewer/data/data_loading.py::Included": (
        "load_well_csv seeds Included=1 on a frame it has just built, before "
        "that frame is placed in the cache. Nothing can hold a memo keyed on a "
        "frame that does not yet exist, so no bump is needed."
    ),
    "well_viewer/data/cache_projectors.py::__dynamic__": (
        "project_verdict_column writes whichever column its caller names "
        "(QC_Pass, Crop_Pass) onto cached frames. It does NOT bump. Three "
        "routes reach it. The QC and crop controllers' recompute entry points "
        "call invalidate_stats_cache immediately afterwards, which empties the "
        "dict the memo lives in. get_rows projects onto a frame it has just "
        "built, which cannot have a memoized aggregate yet. And "
        "load_controller calls apply_crop_to_cache directly, without "
        "invalidating — safe only because it runs during dataset load, after "
        "begin_load has already ticked rows_version and cleared the cache, so "
        "there is nothing memoized for it to stale. That third caller is the "
        "one an earlier version of this note missed while asserting there were "
        "only two; if the load sequence ever grows an aggregation before this "
        "point, the bump has to move into project_verdict_column."
    ),
    "well_viewer/features/home/cell_gating/gating_compute.py::Included": (
        "apply_fluor_gating rewrites the inclusion columns for a well from the "
        "current gate thresholds. Its callers invalidate the stats cache; the "
        "version bump rides on the same edit."
    ),
    "well_viewer/features/home/cell_gating/gating_compute.py::Gate_Pass": (
        "Same call as Included above — one function writes both columns."
    ),
    "well_viewer/features/home/cell_gating/gating_compute.py::Gate_On": (
        "Same call again. Written when the scatter-gate thresholding layer is "
        "active; the aggregation reads it as the on-population, so it changes "
        "plotted fractions and must bump."
    ),
    "well_viewer/features/smfish/tab_view.py::__replaces_frame__": (
        "The smFISH worker writes its per-cell tallies back into the well CSV "
        "and then REPLACES the cached frame by re-reading it. A replacement "
        "invalidates a memo exactly as hard as a column write, and is a "
        "different syntactic shape, so a scan looking only for column writes "
        "walks past it."
    ),
    "well_viewer/data/data_loading.py::__replaces_frame__": (
        "load_and_project seeds the cache well by well — the single path a "
        "frame enters the row cache by, for the lazy read and the dataset "
        "loader's bulk pass alike. Bumping on a fresh insert would be wrong — "
        "it would churn the key once per well during the cold pass and "
        "invalidate each previous well's entry — so the bump belongs on "
        "replacement of a label the cache already holds, not on first insert. "
        "The loader used to hold this write itself, spelled through a local "
        "name the scan could not attribute to the row cache; writing it as "
        "``dataset.cache[label]`` in one place is what put it in view here."
    ),
    "well_viewer/data/curation.py::Included": (
        "apply_curation_to_cache projects manual per-cell curation onto the "
        "cached frame via df.loc. This is the site a grep for 'df[' missed. "
        "Reached from review_image/controller.py, review_image/callbacks.py, "
        "home/cell_gating/controller.py and core/load_controller.py, and as a "
        "registered cache projector on every fresh load — and the loader path "
        "does NOT invalidate afterwards, which is exactly why the bump must "
        "live in this function rather than in its callers."
    ),
}


def _frame_writes(tree: ast.AST) -> set[str]:
    """Column names written onto a frame-shaped name anywhere in *tree*."""
    found: set[str] = set()

    def _target_column(node: ast.AST) -> str | None:
        """The literal column of ``df[...]`` or ``df.loc[..., "col"]``."""
        if not isinstance(node, ast.Subscript):
            return None
        base = node.value
        # df.loc[mask, "col"] / df.iloc[...]: unwrap the indexer attribute.
        if isinstance(base, ast.Attribute) and base.attr in ("loc", "iloc", "at", "iat"):
            base = base.value
            key = node.slice
            if isinstance(key, ast.Tuple) and len(key.elts) == 2:
                col = key.elts[1]
                if isinstance(col, ast.Constant) and isinstance(col.value, str):
                    if _is_frame(base):
                        return col.value
            return None
        if not _is_frame(base):
            return None
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
        return None

    def _is_frame(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in _FRAME_NAMES
        # ``ctx.state.dataset.cache[label]`` and friends.
        if isinstance(node, ast.Subscript):
            return _is_frame(node.value)
        if isinstance(node, ast.Attribute):
            return _is_row_cache(node)
        return False

    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            col = _target_column(t)
            if col in _INCLUSION_COLUMNS:
                found.add(col)
        # inplace=True mutates with no assignment statement to find.
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "inplace" and isinstance(kw.value, ast.Constant) \
                        and kw.value.value is True:
                    found.add("__inplace__")
        # Whole-frame REPLACEMENT — ``cache[label] = load_well_csv(path)``.
        # Invalidates a memo exactly as hard as mutating a column does, and is
        # a different syntactic shape, so a scan looking only for column writes
        # walks straight past it. This is how a reloaded well used to be able to
        # keep serving the previous frame's aggregate.
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and _is_row_cache(t.value):
                    found.add("__replaces_frame__")
    return found


def _is_row_cache(node: ast.AST) -> bool:
    """``dataset.cache`` specifically — NOT any attribute spelled ``cache``.

    ``stats_cache.cache[key] = result`` is a different cache with a different
    lifetime, and matching it here was a predicate applied outside its domain:
    the first version of this scan reported ``stats_compute.py`` as a row-cache
    writer, which it is not. The owning attribute has to be ``dataset``.
    """
    if not (isinstance(node, ast.Attribute) and node.attr == "cache"):
        return False
    owner = node.value
    if isinstance(owner, ast.Attribute):
        return owner.attr == "dataset"
    if isinstance(owner, ast.Name):
        return owner.id in ("dataset", "ds")
    return False


def _discovered_writers() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in python_sources():
        try:
            tree = parse_source(path)
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        cols = _frame_writes(tree)
        if cols:
            out[rel(path)] = cols
    return out


def test_every_cached_frame_writer_is_a_known_one():
    """A new mutation site fails until someone says how rows_version is bumped."""
    discovered = _discovered_writers()
    unknown: list[str] = []
    for path, columns in sorted(discovered.items()):
        for col in sorted(columns):
            if col == "__inplace__":
                # Deliberately not failed on — see the module docstring. The
                # keyword names no object, so this cannot say whether a cached
                # frame was the target.
                continue
            key = f"{path}::{col}"
            dynamic = f"{path}::__dynamic__"
            if key not in _KNOWN_WRITERS and dynamic not in _KNOWN_WRITERS:
                unknown.append(key)
    assert not unknown, (
        "These sites write an inclusion column onto a cached measurement frame "
        "but are not in _KNOWN_WRITERS. Each one can make a memoized "
        "aggregation stale, so add it with a note saying how "
        "DatasetState.rows_version comes to be bumped for it:\n  "
        + "\n  ".join(unknown)
    )


def test_the_roster_describes_real_sites():
    """The liveness half: an entry that no longer matches anything is removed.

    An allowlist nobody prunes stops describing the codebase and starts
    excusing it. ``__dynamic__`` entries are exempt — they name a function that
    writes a caller-supplied column, which this scan cannot resolve.
    """
    discovered = _discovered_writers()
    stale: list[str] = []
    for key in sorted(_KNOWN_WRITERS):
        path, _, col = key.partition("::")
        if col == "__dynamic__":
            if path not in {p for p in discovered} and not (
                    _repo_has(path)):
                stale.append(key)
            continue
        if col not in discovered.get(path, set()):
            stale.append(key)
    assert not stale, (
        "These _KNOWN_WRITERS entries no longer describe a real write. Remove "
        "them — a roster that outlives its sites is not a roster:\n  "
        + "\n  ".join(stale)
    )


def _repo_has(path: str) -> bool:
    from tests._repo_scan import REPO_ROOT
    return (REPO_ROOT / path).exists()


def test_the_scan_finds_the_site_a_grep_missed():
    """The guard is only worth having if it sees the indexer form.

    ``curation.apply_curation_to_cache`` writes through ``df.loc[mask, "Included"]``.
    A search for ``df["Included"] =`` does not match it, and the original
    hand-built writer list did not contain it. If this stops being found, the
    scan has regressed to what a grep could already do.
    """
    discovered = _discovered_writers()
    key = "well_viewer/data/curation.py"
    assert key in discovered, "the df.loc writer is no longer being found"
    assert "Included" in discovered[key]


@pytest.mark.parametrize("source, should_fire", [
    ('ctx.state.dataset.cache[w] = load_well_csv(p)', True),
    ('app._app_state.dataset.cache[key] = frame', True),
    # The false positive the first version of this scan produced: a different
    # cache, a different lifetime, and not a measurement frame at all.
    ('ctx.state.stats_cache.cache[key] = result', False),
    ('self._thumb_cache[key] = img', False),
])
def test_the_scan_separates_the_row_cache_from_every_other_cache(source, should_fire):
    """``.cache`` is not a sufficient predicate; the owner has to be ``dataset``."""
    hit = "__replaces_frame__" in _frame_writes(ast.parse(source))
    assert hit is should_fire, f"{source!r} -> {hit!r}"


@pytest.mark.parametrize("source, should_fire", [
    ('df["Included"] = 1', True),
    ('df.loc[mask, "QC_Pass"] = 0', True),
    ('frame["Gate_Pass"] = mask.astype(int)', True),
    ('cached["Crop_Pass"] = verdict', True),
    ('df["some_scratch_column"] = 1', False),
    ('other["Included"] = 1', False),
    ('x = df["Included"]', False),
])
def test_the_scan_fires_on_the_shapes_it_claims_to_catch(source, should_fire):
    """The guard, handed the violation it exists to prevent, must fire — and
    handed a clean equivalent, must not. Without this the scan can silently
    stop matching and the suite stays green."""
    tree = ast.parse(source)
    hits = {c for c in _frame_writes(tree) if c in _INCLUSION_COLUMNS}
    assert bool(hits) is should_fire, f"{source!r} -> {hits!r}"
