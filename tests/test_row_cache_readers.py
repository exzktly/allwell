"""Every read of the bounded row cache, enumerated from the syntax tree (tracker M3).

The row cache (``review/data/row_cache.py``) holds the wells that fit its memory
budget, not necessarily the plate. Code that iterates ``dataset.cache`` — or asks
it for a well without loading one — therefore sees the held wells only, and on a
plate larger than the budget a reader that means "every well" undercounts with no
error at all: the Statistics tab's median turned NaN that way for any well evicted
between two reads.

So the readers are a roster, keyed by the **function** that reads (the site, not
the file — CLAUDE.md §7.5 #10), each with why reading only the held frames is
right there. A new reader fails this test until it is listed with its reason; a
listed reader that no longer exists fails it too, so the roster cannot outlive
its sites. A reader that means the plate goes through ``get_rows``, one well at a
time, or through the per-well summaries — and needs no entry.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

#: ``path::function`` → why that function may read only the held frames.
_HELD_ONLY_READERS: dict[str, str] = {
    "review/data/data_loading.py::get_rows": "The lazy read itself: a held frame is returned, a missing one loaded.",
    "review/data/data_loading.py::load_and_project": "Stores the frame it has just read, pins and re-measures it.",
    "review/data/data_loading.py::held_wells_note": "Counts the held wells, to say when a readout covers only them.",
    "review/data/cache_projectors.py::drop_verdict_column": (
        "A projector walk: a verdict switched off is dropped from the held frames, and an evicted frame is "
        "re-projected from the same switch when it is read again."),
    "review/data/cache_projectors.py::project_verdict_column": (
        "A projector walk over the held frames; every frame read later is projected on load."),
    "review/data/field_exclusions.py::apply_field_exclusions_to_cache": "A projector walk, as above.",
    "review/data/field_quality_cache.py::apply_field_quality_to_cache": "A projector walk, as above.",
    "review/data/focus_gate.py::apply_focus_gate_to_cache": "A projector walk, as above.",
    "review/data/well_repository.py::rows_or_none": (
        "The named cache-only lookup, for a projector writing onto the frame load_and_project has just stored "
        "and pinned (the gating projector). Statistics used it to read rows and now reads through `rows`, "
        "because a cache-only read of an evicted well answered None."),
    "review/data/focus_gate.py::dataset_has_focus_column": (
        "Whether the dataset carries the focus column: any held frame answers it, since the column is written "
        "for every row of a run or for none, and an empty cache answers None (unknown), never 'no'."),
    "review/features/image_browser/playground/controller.py::_focus_caveats": (
        "The same question as dataset_has_focus_column, asked of any held frame; nothing held says nothing."),
    "review/features/spatial/collect.py::_sample_loaded_df": (
        "Picker discovery from any well's columns: a held frame first, else the first well read through "
        "get_rows, so an empty cache does not read as an empty dataset."),
    "review/features/qc/controller.py::qc_excluded_count": (
        "The QC row's live 'flagged' readout, recomputed on every debounced keystroke: it counts the held "
        "frames, and the readout appends held_wells_note when they are not the plate."),
    "review/features/crop/controller.py::_crop_effect_counts": (
        "The crop's status line, counted over the held frames; both lines that print it append held_wells_note."),
    "review/features/home/cell_gating/controller.py::cell_gating_refresh_included_count": (
        "'Included for analysis', counted over the held frames; the line appends held_wells_note."),
    "review/features/smfish/tab_view.py::_smfish_refresh_app_cache": (
        "Replaces a HELD well's frame after the smFISH write-back rewrote its CSV. An evicted well needs "
        "nothing: its next read sees the new file, whose fingerprint invalidates the Parquet cache."),
    "review/core/dataset_state.py::begin_load": "Clears the cache on a dataset swap.",
    "review/core/channel_state_controller.py::set_active_metric": (
        "Checks the first held frame for the new metric's column and clears the cache when it is missing (a "
        "stale cache). Any held well answers that; with none held there is nothing stale to clear."),
    "review/persistence/reset_scopes.py::clear_curation": "Clears the cache so frames reload without overrides.",
    "review/persistence/reset_scopes.py::_clear_gating": "Clears the cache so frames reload ungated.",
    # Owed by tracker M4, which moves each of these to the per-well summaries. Listed so this guard can land
    # with M3; M4 removes them, and the liveness test below fails if it does not.
    "review/core/dataset_state.py::rebuild_timepoints": "OWED (tracker M4): the plate's timepoints, from the held frames.",
    "review/core/dataset_state.py::rebuild_fovs": "OWED (tracker M4): the plate's FOVs, from the held frames.",
    "review/data/dataset_dims.py::cache_column_tokens": "OWED (tracker M4): timepoint/FOV options, from the held frames.",
    "review/runtime_app.py::_invalidate_dataset_cache": "Clears the cache.",
}


def _is_row_cache(node: ast.AST, in_dataset_state: bool) -> bool:
    """``dataset.cache`` in any spelling: the attribute, or ``getattr(dataset, "cache", …)``."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value == "cache"):
        owner = node.args[0]
    elif isinstance(node, ast.Attribute) and node.attr == "cache":
        owner = node.value
    else:
        return False
    if isinstance(owner, ast.Attribute):
        return owner.attr == "dataset"
    if isinstance(owner, ast.Name):
        return owner.id in ("dataset", "ds") or (in_dataset_state and owner.id == "self")
    return False


def _readers() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in python_sources():
        name = rel(path)
        if name.startswith("tests/") or name == "review/data/row_cache.py":
            continue
        tree = parse_source(path)
        in_state = name == "review/core/dataset_state.py"
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            hits = sum(1 for node in ast.walk(fn) if _is_row_cache(node, in_state))
            # Count each site once, at its innermost function: an outer function's
            # walk also visits its nested functions' nodes.
            nested = sum(1 for inner in ast.walk(fn) if inner is not fn
                         and isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                         for node in ast.walk(inner) if _is_row_cache(node, in_state))
            if hits - nested:
                found[f"{name}::{fn.name}"] = hits - nested
    return found


def test_every_reader_of_the_row_cache_is_on_the_roster():
    unlisted = sorted(set(_readers()) - set(_HELD_ONLY_READERS))
    assert unlisted == [], (
        "These functions read the bounded row cache directly. It holds the wells that fit its budget, not the "
        "plate: read every well through get_rows (or the per-well summaries), or add the function to "
        "_HELD_ONLY_READERS with why the held frames are what it means:\n  " + "\n  ".join(unlisted))


def test_the_roster_describes_real_sites():
    stale = sorted(set(_HELD_ONLY_READERS) - set(_readers()))
    assert stale == [], f"These roster entries no longer read the row cache; remove them: {stale}"


def test_the_scan_sees_each_spelling_of_the_cache():
    def hits(source, in_state=False):
        return sum(1 for n in ast.walk(ast.parse(source)) if _is_row_cache(n, in_state))

    assert hits("ctx.state.dataset.cache.items()") == 1
    assert hits("dataset.cache.get(label)") == 1
    assert hits("self.cache.values()", in_state=True) == 1
    assert hits("self.cache.values()") == 0
    assert hits("ctx.state.stats_cache.cache[key]") == 0
    assert hits('getattr(ctx.state.dataset, "cache", None) or {}') == 1
    assert hits('getattr(ctx.state.stats_cache, "cache", None)') == 0
