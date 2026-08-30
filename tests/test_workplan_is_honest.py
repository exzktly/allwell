"""The feature workplan's rows are well-formed and its counts are not typed.

``_Docs/Markdowns/FEATURE_WORKPLAN.md`` tracks a body of work too large to hold
in one session's head. ``CLAUDE.md`` §6.1 records what that costs when it goes
wrong: a 177-finding review was reported complete with 42 items still owed,
because the state of the work lived in conversation while confidence in it
survived every compaction.

So the tracker is not trusted here. Three properties are checked mechanically,
each of them a way a tracker has actually gone wrong before:

* **Every table line parses, and row numbers are contiguous and unique.** Both
  halves are needed: a row the parser cannot read simply vanishes from its
  results, so without the first check a malformed row is reported as whichever
  *other* rule its absence happens to break — which is how the first version of
  this file passed its own bite case for the wrong reason.
* **Every workstream letter in the table has a section** below it, and every
  section has rows. A section with no rows is a plan nobody is tracking; a row
  with no section is a row nobody can act on.
* **No total is written in prose.** A hand-maintained count drifts from the rows
  within a day and is then the most confidently wrong line in the file, so the
  counts are derived here and nowhere else.

This file is deleted along with the tracker by the PR that closes its last row.
"""

from __future__ import annotations

import re

from tests._repo_scan import REPO_ROOT

_TRACKER = REPO_ROOT / "_Docs" / "Markdowns" / "FEATURE_WORKPLAN.md"

#: A tracker row: ``| 7 | D | item text | ☐ | note |``
_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*([A-H])\s*\|\s*(.+?)\s*\|\s*([☐◐☑⊘])\s*\|",
                  re.M)

#: ``## D — Parquet storage`` — the workstream sections.
_SECTION = re.compile(r"^## ([A-H]) — ", re.M)

_STATUSES = "☐◐☑⊘"


def _text() -> str:
    return _TRACKER.read_text(encoding="utf-8")


def _rows() -> list[tuple[int, str, str, str]]:
    return [(int(n), ws, item, status) for n, ws, item, status in _ROW.findall(_text())]


def test_the_tracker_exists_and_has_rows():
    assert _TRACKER.exists(), (
        "the workplan is gone but its test is not — either restore the tracker "
        "or delete this file with it, per its own closing rule"
    )
    assert _rows(), "the workplan's tracker table is missing or unparseable"


def test_every_table_line_parses_as_a_row():
    """A row the regex cannot read is dropped, not reported — so check the count.

    Without this, a malformed row disappears from :func:`_rows` and the failure
    surfaces as a numbering gap somewhere else in the table, naming the wrong
    line.
    """
    body = [
        line for line in _text().splitlines()
        if line.lstrip().startswith("|")
        and not line.lstrip().startswith("|---")
        and not line.lstrip().startswith("| # |")
    ]
    assert len(body) == len(_rows()), (
        f"{len(body)} table lines but only {len(_rows())} parse as rows — one is "
        "malformed (an unknown workstream letter, a missing status glyph, or a "
        "broken cell). Fix the row rather than the regex."
    )


def test_row_numbers_are_contiguous_and_unique():
    numbers = [n for n, _, _, _ in _rows()]
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"the tracker's row numbers have gaps or duplicates: {numbers}. A "
        "renumbering that lost a row is how an item goes unnoticed."
    )


def test_every_row_has_an_item_and_a_known_status():
    for n, _ws, item, status in _rows():
        assert item.strip(), f"row {n} has no item text"
        assert status in _STATUSES, f"row {n} carries an unknown status {status!r}"


def test_the_table_and_the_sections_describe_the_same_workstreams():
    row_ws = {ws for _, ws, _, _ in _rows()}
    section_ws = set(_SECTION.findall(_text()))
    assert row_ws == section_ws, (
        f"rows cover {sorted(row_ws)} but sections cover {sorted(section_ws)} — "
        "a section with no rows is untracked work; a row with no section is a "
        "row nobody can act on"
    )


def test_no_count_of_rows_is_written_in_prose():
    """Counts are derived from the rows, never typed beside them."""
    offenders = []
    for line in _text().splitlines():
        if line.lstrip().startswith("|"):
            continue  # the table itself
        for match in re.finditer(
            r"\b(\d+)\s+(?:rows?|items?)\s+(?:are\s+)?"
            r"(?:done|open|outstanding|remaining|complete|left)\b",
            line, re.I,
        ):
            offenders.append(f"{line.strip()!r} (claims {match.group(1)})")
        if re.search(r"\b(\d+)\s*/\s*(\d+)\s+(?:rows?|items?|done|complete)\b",
                     line, re.I):
            offenders.append(f"{line.strip()!r}")
    assert not offenders, (
        "the workplan types a count of its own rows:\n  " + "\n  ".join(offenders)
        + "\nDerive it from the table instead — a typed total drifts within a day."
    )
