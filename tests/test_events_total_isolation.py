"""The Events **total** is a reporting figure, never a plotted one.

Two helpers answer "how many events did this replicate set have", and they
answer differently on purpose:

* ``compute_rep_n_above_stats`` — the mean of the per-well counts with the
  cross-well spread. This is what every panel draws, because it is the
  statistic the Mean and Fraction panels beside it use, and because a set
  normalised against itself has to land on 1.0.
* ``compute_rep_n_above_total`` — the cross-well sum. The CSV's
  ``n_above_threshold`` column has always carried this, and downstream scripts
  read it as an integer count.

Picking the wrong one is not hypothetical. A predecessor named
``compute_rep_n_above`` — a near-synonym differing only by an absent ``_stats``
suffix — was read by the Line tab's numerator and by both fold-change
denominators, so the two plotting tabs drew different numbers for the same
replicate set (5 per well against 10 pooled) and a set divided by itself came
out at 1/n. It was deleted for that reason; the total came back only because
the CSV genuinely needs it, under a name that says what it is for.

This guard is what keeps the distinction from eroding again: the total may be
imported by code that writes reports, and by nothing that draws.
"""
from __future__ import annotations

import ast

from tests._repo_scan import python_sources, rel

#: Modules allowed to import the total. Every entry writes a CSV; none of them
#: draws. Allowlisting the *site* rather than the file is the point — a broad
#: "this whole package is fine" entry is how these rot (CLAUDE.md §7.5 #10).
ALLOWED = frozenset({
    # Note who is NOT here: ``features/export/service.py``, which writes the
    # column. It never imports the total — it is handed one as a parameter, so
    # the collectors decide and the writer only formats. That is the shape to
    # keep; a writer that started computing it would need an entry, and would
    # be worth a second look.
    "well_viewer/features/barplot/collect.py",
    "well_viewer/features/lineplot/controller.py",
    "well_viewer/features/batch_export/_line_series_runner.py",
})

_TOTAL = "compute_rep_n_above_total"


def _imports_the_total(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if any(a.name == _TOTAL for a in node.names):
                return True
        elif isinstance(node, ast.Attribute) and node.attr == _TOTAL:
            return True
        elif isinstance(node, ast.Name) and node.id == _TOTAL:
            return True
    return False


def _offenders() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        name = rel(path)
        if name in ALLOWED or name.endswith("stats_compute.py"):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a file we cannot parse
            continue
        if _imports_the_total(tree):
            out.append(name)
    return out


def test_the_events_total_stays_out_of_drawing_code():
    offenders = _offenders()
    assert not offenders, (
        f"{_TOTAL} is the CSV's count, not a plotted value — every panel draws "
        "compute_rep_n_above_stats. These modules reach for it:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_no_renderer_is_on_the_allowlist():
    """A renderer must never be allowed, whatever a future edit claims."""
    bad = [a for a in ALLOWED if a.endswith("renderer.py")]
    assert not bad, f"a renderer cannot need the Events total: {bad}"


def test_the_allowlist_has_no_stale_entries():
    """Every entry must name a module that really imports it.

    Without this the allowlist silently grows into a list of files nobody has
    checked, which is how an exemption outlives the reason for it.
    """
    stale = []
    for name in ALLOWED:
        path = next((p for p in python_sources() if rel(p) == name), None)
        if path is None:
            stale.append(f"{name} (no such file)")
            continue
        if not _imports_the_total(ast.parse(path.read_text())):
            stale.append(f"{name} (does not import it)")
    assert not stale, (
        "these allowlist entries no longer describe a real import:\n"
        + "\n".join(f"  {s}" for s in stale)
    )
