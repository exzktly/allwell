"""The remediation tracker's summary is derived from its own rows.

CLAUDE.md §6.1 #3: a tracker's counts are never typed, because a
hand-maintained total drifts from the rows within a day and is then the most
confidently wrong line in the file. This recomputes the table and fails on any
disagreement — including the one this guard was written after, where a summary
edited by hand said four Critical rows were done while the rows said three.

The tracker is deleted by the pull request that closes its last row, and this
file goes with it. Until then it is live state, and a stale count in it is
worse than no count.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TRACKER = (Path(__file__).resolve().parent.parent
           / "_Docs" / "Markdowns" / "CONVERTER_REVIEW_TRACKER.md")

_ROW = re.compile(r"^\|\s*([CHMLT]\d+)\s*\|")
_STATE = re.compile(r"\|\s*(open|done|declined)\s*\|")
_SUMMARY = re.compile(
    r"^\|\s*([A-Za-z/ ]+?)\s*\|\s*\**(\d+)\**\s*\|\s*\**(\d+)\**\s*\|"
    r"\s*\**(\d+)\**\s*\|\s*\**(\d+)\**\s*\|")

#: Heading text → the label its rows are summarised under. Written out because
#: the headings carry prose the summary does not, and a fuzzy match between the
#: two would be a third thing to keep in step.
_SECTIONS = {
    "Critical": "Critical",
    "High": "High",
    "Medium": "Medium",
    "Found while fixing": "Found while fixing",
    "Low / hygiene": "Low / hygiene",
    "Test gaps": "Test gap",
}


def _section_for(heading: str) -> str | None:
    for prefix, label in _SECTIONS.items():
        if heading.startswith(prefix):
            return label
    return None


@pytest.fixture(scope="module")
def tracker() -> str:
    if not TRACKER.exists():
        pytest.skip("the tracker has been deleted, which closes this guard too")
    return TRACKER.read_text(encoding="utf-8")


def _rows(text: str) -> dict[str, list[tuple[str, str]]]:
    """``{section label: [(id, state), …]}`` read off the tables."""
    out: dict[str, list[tuple[str, str]]] = {}
    section = None
    for line in text.split("\n"):
        if line.startswith("#"):
            section = _section_for(line.lstrip("# ").strip())
            continue
        match = _ROW.match(line)
        if not (match and section):
            continue
        states = _STATE.findall(line)
        assert states, f"row {match.group(1)} names no state"
        out.setdefault(section, []).append((match.group(1), states[0]))
    return out


def test_every_summary_number_matches_the_rows_it_counts(tracker):
    rows = _rows(tracker)
    assert rows, "found no tracker rows — the row pattern drifted"

    seen = set()
    for line in tracker.split("\n"):
        match = _SUMMARY.match(line)
        if not match:
            continue
        label, total, opened, done, declined = match.groups()
        if label == "Severity":          # the header
            continue
        if label == "Total":
            these = [r for group in rows.values() for r in group]
        elif label in rows:
            these = rows[label]
            seen.add(label)
        else:
            pytest.fail(f"summary names a section with no rows: {label!r}")
        counted = {
            "total": len(these),
            "open": sum(1 for _, s in these if s == "open"),
            "done": sum(1 for _, s in these if s == "done"),
            "declined": sum(1 for _, s in these if s == "declined"),
        }
        claimed = {"total": int(total), "open": int(opened),
                   "done": int(done), "declined": int(declined)}
        assert counted == claimed, (
            f"the summary row for {label!r} says {claimed} and its rows say "
            f"{counted}")

    assert seen == set(rows), (
        f"sections with rows but no summary line: {sorted(set(rows) - seen)}")


def test_a_done_row_carries_its_evidence(tracker):
    """`done` means the defect cannot recur, which means a test that was
    watched failing without the fix (§6.1 #4). A row claiming `done` with an
    empty Evidence cell is a row reporting from memory."""
    thin = []
    for line in tracker.split("\n"):
        match = _ROW.match(line)
        if not match or "| done |" not in line:
            continue
        evidence = line.rsplit("| done |", 1)[1].strip().strip("|").strip()
        if len(evidence) < 40:
            thin.append(f"{match.group(1)}: {evidence!r}")
    assert not thin, (
        "rows marked done without evidence naming the test that pins them:\n  "
        + "\n  ".join(thin))


def test_no_row_is_left_without_a_state(tracker):
    for line in tracker.split("\n"):
        match = _ROW.match(line)
        if match:
            assert _STATE.search(line), f"row {match.group(1)} has no state"
