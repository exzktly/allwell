"""Ratchet: single-well row reads must go through ``WellRepository`` (roadmap B2/B3).

The read path is already ~80% funneled through ``data_loading.get_rows`` /
``ctx.state.repo``. This guard locks that in: it counts *single-well cache
reads* — ``<x>.dataset.cache.get(label)`` and ``<x>.dataset.cache[label]`` in a
load context — outside the sanctioned owner modules and the documented
allowlist, and only ratchets down. A new direct cache read (instead of
``repo.rows`` / ``repo.rows_or_none`` / ``get_rows``) trips it.

Not counted (legitimately not read-facade ops, so out of scope for this ratchet):
cache *writes* (the bulk eager load, the smFISH reload, the QC/Crop projectors),
``.clear()`` invalidations, and whole-dict grabs / iteration.
"""

from __future__ import annotations

import ast
from pathlib import Path
from tests._repo_scan import parse_source

ROOT = Path(__file__).resolve().parent.parent / "well_viewer"

# Modules that legitimately own direct dataset.cache access.
_OWNERS = {
    ROOT / "data" / "well_repository.py",
    ROOT / "data" / "data_loading.py",
    ROOT / "core" / "dataset_state.py",
}
# Deliberate cache-only readers, documented as exceptions. Spatial reads each
# well's frame straight from the cache (it is downstream of whatever plotting /
# image tab first loaded the well, so a cache-only lookup is correct by design).
_ALLOWLIST = {
    ROOT / "features" / "spatial" / "collect.py",
    ROOT / "features" / "spatial" / "tab_view.py",
}

# B3 routed the last single-well cache read (stats_compute._safe_rows) through
# WellRepository.rows_or_none, so the budget is now 0; ratchet DOWN only.
REPO_BYPASS_BUDGET = 0


def _is_dataset_cache(node: ast.AST) -> bool:
    """True if *node* is an ``X.dataset.cache`` attribute access."""
    return (isinstance(node, ast.Attribute) and node.attr == "cache"
            and isinstance(node.value, ast.Attribute) and node.value.attr == "dataset")


def _single_well_cache_reads():
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path in _OWNERS or path in _ALLOWLIST:
            continue
        tree = parse_source(path)
        for node in ast.walk(tree):
            # dataset.cache.get(label)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and _is_dataset_cache(node.func.value)):
                offenders.append(f"{path.relative_to(ROOT.parent)}:{node.lineno}")
            # dataset.cache[label] in a load (read) context
            elif (isinstance(node, ast.Subscript) and _is_dataset_cache(node.value)
                    and isinstance(node.ctx, ast.Load)):
                offenders.append(f"{path.relative_to(ROOT.parent)}:{node.lineno}")
    return offenders


def test_repo_bypass_budget():
    offenders = _single_well_cache_reads()
    assert len(offenders) <= REPO_BYPASS_BUDGET, (
        f"{len(offenders)} single-well dataset.cache reads, over the "
        f"{REPO_BYPASS_BUDGET} budget. Read rows via ctx.state.repo.rows / "
        f"rows_or_none (or data_loading.get_rows):\n  " + "\n  ".join(offenders)
    )


def test_repo_bypass_budget_not_slack():
    offenders = _single_well_cache_reads()
    assert len(offenders) >= REPO_BYPASS_BUDGET, (
        f"only {len(offenders)} direct cache reads remain but the budget is "
        f"{REPO_BYPASS_BUDGET}: lower REPO_BYPASS_BUDGET to {len(offenders)}."
    )
