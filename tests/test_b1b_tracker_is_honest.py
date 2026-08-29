"""The B1b tracker's counts come from its own rows, and match the ratchet.

``_Docs/Markdowns/GOD_OBJECT_REPOSITORY_ROADMAP.md`` carries a row per remaining
function-local ``core/`` → ``features/`` import (roadmap terminal condition 3).
A tracker whose totals are typed rather than derived drifts from the tree within
a day and is then the most confidently wrong line in the file — the failure this
repo has already paid for once, at the cost of 42 unfinished items reported as
done.

So the tracker is not trusted here: its open rows are counted, and the count must
equal ``CORE_FEATURE_IMPORT_BUDGET``, which is measured from the AST. Tick a row
without removing the import and this fails; remove an import without ticking its
row and this fails too. Neither half can quietly get ahead of the other.
"""

from __future__ import annotations

import re

from tests._repo_scan import REPO_ROOT
from tests.test_guardrails import CORE_FEATURE_IMPORT_BUDGET

_TRACKER = REPO_ROOT / "_Docs" / "Markdowns" / "GOD_OBJECT_REPOSITORY_ROADMAP.md"

#: A tracker row: ``| 7 | `mod.py` | `fn` | `sym` | category | status | note |``
_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|.*?\|\s*([☐◐☑⊘])\s*\|", re.M)


def _rows() -> list[tuple[int, str, str]]:
    text = _TRACKER.read_text(encoding="utf-8")
    return [(int(n), mod, status) for n, mod, status in _ROW.findall(text)]


def test_the_tracker_has_a_row_per_site_numbered_without_gaps():
    rows = _rows()
    assert rows, "the B1b tracker table is missing from the roadmap"
    assert [n for n, _, _ in rows] == list(range(1, len(rows) + 1)), (
        "the tracker's row numbers have gaps or duplicates — a renumbering "
        "that lost a row is exactly how an item goes unnoticed"
    )


def test_open_rows_equal_the_measured_budget():
    """The tracker and the AST must agree on how much is left."""
    open_rows = [(n, mod) for n, mod, status in _rows() if status in "☐◐"]

    assert len(open_rows) == CORE_FEATURE_IMPORT_BUDGET, (
        f"the tracker lists {len(open_rows)} open row(s) but "
        f"CORE_FEATURE_IMPORT_BUDGET is {CORE_FEATURE_IMPORT_BUDGET}. Either a "
        f"row was ticked without the import being removed, or an import was "
        f"removed and the budget lowered without ticking its row. Open rows: "
        f"{sorted(open_rows)}"
    )
