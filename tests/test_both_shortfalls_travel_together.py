"""A surface that names one shortfall must name the other.

The Image Table can produce an artifact that is degraded in two independent
ways, and both are invisible in the artifact itself. A scale bar longer than
its cell is refused, so the cell looks like one nobody asked a bar for; an
inventory that was TRUNCATED still draws real images, so the grid looks
finished and is simply short of frames the dataset holds.

Row 1 of this branch gave the bar one shared clause and reached all five
surfaces with it. The truncated inventory reached one — and that asymmetry is
worse than reporting neither, because a saved-file line that names the missing
bar and stays silent about the missing frames convinces the reader it is
telling them what is wrong with the file.

**This file imports nothing from the application**, deliberately. Its sibling
``test_a_short_artifact_says_it_is_short.py`` tests what the clause *says* and
so imports it, which makes that file unprovable against a base branch where the
symbol does not exist yet — ``scripts/prove_guard.sh`` correctly reports
``NOT PROVEN (ImportError)`` and measures nothing. The rule here is decidable
from the syntax tree alone, so keeping it free of app imports is what lets it
be proven where it actually bites.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source

#: The package whose functions turn a render pass into an artifact.
_MODULES = (
    "well_viewer/features/image_browser/image_table/controller.py",
    "well_viewer/features/image_browser/image_table/export.py",
)

_BAR = "scale_bar_refusal_clause"
_PARTIAL = "partial_scan_clause"

#: Every surface that produces an artifact from a render pass, named rather
#: than counted: a new one added without a clause is then a missing name here,
#: which a count would not notice.
_SURFACES = frozenset({
    "well_viewer/features/image_browser/image_table/controller.py::"
    "generate_status_message",
    "well_viewer/features/image_browser/image_table/export.py::"
    "image_table_export",
    "well_viewer/features/image_browser/image_table/export.py::"
    "image_table_copy_png",
    "well_viewer/features/image_browser/image_table/export.py::"
    "image_table_copy_svg",
    "well_viewer/features/image_browser/image_table/export.py::"
    "image_table_save_to_gallery",
})


def _functions_reporting(name: str) -> set[str]:
    """Every function in the scanned modules that calls *name*."""
    found = set()
    for path in _MODULES:
        for node in ast.walk(parse_source(path)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == name):
                    found.add(f"{path}::{node.name}")
    return found


def test_the_two_clauses_travel_together():
    """The invariant, per function rather than per file.

    A file-level check — "the module mentions the clause somewhere" — is what
    the scale bar's own first guard did, and it passes happily while three of
    four surfaces in the same file say nothing. The unit that produces an
    artifact is the function, so the function is what is scanned.
    """
    bar = _functions_reporting(_BAR)
    partial = _functions_reporting(_PARTIAL)
    assert bar, "the scan found no reporting surfaces at all — check _MODULES"
    assert bar == partial, (
        "these surfaces report one shortfall and not the other:\n"
        + "\n".join(f"  {site}: "
                     + ("no partial-scan clause" if site not in partial
                        else "no scale-bar clause")
                     for site in sorted(bar ^ partial))
    )


def test_every_export_surface_is_covered():
    assert _functions_reporting(_PARTIAL) == set(_SURFACES), (
        "the roster and the tree disagree about which functions report a "
        "truncated inventory"
    )


def test_no_surface_spells_the_sentence_out_itself():
    """Two hand-written sentences for one fact drift — these two already had.

    The Generate line counted the wells and named the scans' reasons; the
    saved-PNG line did neither, so the artifact that outlives the session
    carried *less* than the transient line, which is backwards.
    """
    for path in _MODULES:
        source = parse_source(path)
        for node in ast.walk(source):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "is INCOMPLETE" not in node.value, (
                    f"{path} writes the shortfall sentence itself instead of "
                    f"using {_PARTIAL}"
                )
