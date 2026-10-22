"""A nanowell well whose timepoints ran and could not be addressed is partial on Home, not "never processed".

Reported: "If no fiducial was found, the home tab thinks they were skipped entirely." The completeness record
counts a nanowell timepoint as covered only when its grid record says ``ok`` (``completeness.nanowell_coverage``),
so a well whose every timepoint was located, registered and measured but left unaddressed covered nothing, and
Home painted it **Staged, never processed** — "input is present and nothing was written" — with a next step
saying the wells were never processed. The record now names those timepoints (``not_addressed``), and Home paints
the well partial and says why. The record is built by the real writer (``completeness.well_record``) here, not by
hand, so a field the writer stops writing fails these tests.
"""

from __future__ import annotations

from pipeline import completeness as C
from review.features.home import collect as hc
from tests.test_home_collect import _block, _ctx
from tests.test_nanowell_completeness import _coverage, _well


def _record(tmp_path, statuses):
    return C.well_record(_coverage(_well(tmp_path, statuses)))


def test_the_record_names_the_timepoints_that_ran_and_were_not_addressed(tmp_path):
    record = _record(tmp_path, {"0h": "unaddressed", "24h": "unaddressed", "48h": "no_lattice"})
    assert record["health"] == "partial" and record["covered_any"] == 0 and record["missing_count"] == 3
    assert record["not_addressed_count"] == 3
    assert record["not_addressed"] == [["0h", "unaddressed"], ["24h", "unaddressed"], ["48h", "no_lattice"]]


def test_home_paints_it_partial_says_why_and_does_not_call_it_never_processed(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    unaddressed = _record(tmp_path / "a", {"0h": "unaddressed", "24h": "unaddressed", "48h": "unaddressed"})
    never = C.well_record(_coverage(_well(tmp_path / "b", {})))          # no timepoint reached at all
    ctx = _ctx(_block({"A01": unaddressed, "A02": never}), wells=("A01", "A02"))
    cells = hc.plate_cells(ctx)
    assert cells == {"A01": hc.CELL_PARTIAL, "A02": hc.CELL_INPUT_ONLY}
    rows = {r["label"]: r for r in hc.health_rows(ctx)}
    assert rows["Nanowell timepoints not addressed"]["wells"] == ["A01"]
    assert rows["Nanowell timepoints not addressed"]["value"].startswith("3 timepoint(s) were processed")
    assert rows["Staged, never processed"]["wells"] == ["A02"]
