"""One answer to "how many wells does this plate have?".

``run_check["totals"]["wells"]`` is ``len(records)`` — how many wells the scan
managed to *record*. A well that raised never enters ``records``, so using it
as the denominator of "N of M well(s) complete" shrinks both sides of the
fraction together: a plate of 96 with two unreadable wells reads "94 of 94",
and the shortfall is hidden by the sentence written to surface it (CLAUDE.md
§7).

Four surfaces print that fraction — the Analyze pane's pre-run readout and its
idle last-run card, Review Home's Wells tile, the pipeline's own end-of-run
report, and the Datasets Validate log. The first was fixed alone, which left
one pane holding two plate sizes; §7.5 #5 names half-sweeping as worse than
not sweeping. ``pipeline.completeness.plate_total`` is now the single answer
and this guard is what keeps it that way.
"""
from __future__ import annotations

import ast

import pytest

from pipeline import completeness
from tests._repo_scan import python_sources, rel
from tests._repo_scan import parse_source

#: The functions entitled to read the raw recorded count — the one that turns
#: it into the plate's size, and the one that assembles the totals in the first
#: place. Named per *function* rather than per file (CLAUDE.md §7.5 #10): a
#: file-level exemption for `completeness.py` would have covered
#: `report_completeness` too, which is one of the four surfaces this guard
#: exists to hold.
_ALLOWED_FUNCTIONS = {"plate_total", "build_block"}


def _reads_recorded_count(node: ast.AST) -> bool:
    """``totals["wells"]`` / ``totals.get("wells")`` in any spelling."""
    if isinstance(node, ast.Subscript):
        sl = node.slice
        return isinstance(sl, ast.Constant) and sl.value == "wells"
    if isinstance(node, ast.Call):
        fn = node.func
        return (isinstance(fn, ast.Attribute) and fn.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "wells")
    return False


def _totals_named(node: ast.AST) -> bool:
    """Whether the thing being indexed is a totals mapping."""
    target = node.value if isinstance(node, ast.Subscript) else node.func.value
    name = (getattr(target, "id", "") or getattr(target, "attr", ""))
    return "total" in name.lower()


def test_the_scan_can_see_the_definition():
    """Fail closed: a guard reading nothing passes for ever."""
    assert callable(completeness.plate_total)
    assert completeness.plate_total({"wells": 94, "unreadable": 2}) == 96
    assert any(rel(p) == "pipeline/completeness.py" for p in python_sources())


def test_the_exemption_still_describes_a_real_reader():
    """The allowlist's liveness half: an entry that exempts nothing is stale."""
    import inspect

    for name in _ALLOWED_FUNCTIONS:
        src = inspect.getsource(getattr(completeness, name))
        assert '"wells"' in src, (
            f"{name} no longer reads the recorded count, so its exemption "
            f"describes nothing and should be removed")


def test_no_surface_builds_a_denominator_from_the_recorded_count():
    offenders: list[str] = []
    for path in python_sources():
        where = rel(path)
        if where.startswith("tests/"):
            continue
        try:
            tree = parse_source(path)
        except SyntaxError:                     # pragma: no cover - not our file
            continue
        exempt: set[int] = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name in _ALLOWED_FUNCTIONS):
                exempt.update(range(node.lineno,
                                    (node.end_lineno or node.lineno) + 1))
        for node in ast.walk(tree):
            if (_reads_recorded_count(node) and _totals_named(node)
                    and node.lineno not in exempt):
                offenders.append(f"{where}:{node.lineno}")

    assert not offenders, (
        "these read totals['wells'] — how many wells the scan recorded — where "
        "the plate's size is wanted, so an unreadable well shrinks the "
        "denominator along with the numerator and the fraction reads full "
        "marks. Ask pipeline.completeness.plate_total instead. Sites: "
        f"{offenders}")


@pytest.mark.parametrize("unreadable,expected", [(0, 96), (2, 96), (96, 96)])
def test_the_plate_size_is_the_same_however_many_wells_were_readable(
        unreadable, expected):
    """The property the helper exists for, stated directly."""
    recorded = 96 - unreadable
    totals = {"wells": recorded, "unreadable": unreadable}
    assert completeness.plate_total(totals) == expected


def test_the_note_names_the_count_rather_than_hedging():
    assert completeness.unreadable_note({"wells": 94, "unreadable": 2}).startswith("2 well(s)")
    assert completeness.unreadable_note({"wells": 96, "unreadable": 0}) == ""


def test_the_scan_counts_the_wells_it_could_not_read(tmp_path, monkeypatch):
    """The producer, not just the readers.

    Deleting ``unreadable += 1`` from ``scan_run`` left the whole suite green:
    every test of the new denominator hands ``unreadable=`` straight to
    ``build_block``, so the counter could have silently stayed at zero and the
    fix above would have been a no-op nothing noticed (CLAUDE.md §7.7 #2 —
    name the edit that would break it, make it, watch a test fail).
    """
    from pipeline import completeness as C

    wells = ["A01", "A02", "A03", "A04"]
    raising = {"A02", "A03"}

    real = C.coverage_for_well

    def _flaky(*a, **kw):
        well = kw.get("well") or (a[0] if a else "")
        if str(well) in raising:
            raise OSError(f"cannot read {well}")
        return real(*a, **kw)

    monkeypatch.setattr(C, "coverage_for_well", _flaky)

    in_dir = tmp_path / "in"
    in_dir.mkdir()
    containers = {}
    for w in wells:
        d = in_dir / w
        d.mkdir()
        (d / f"Exp_DAPI_{w}_F001_0h.tif").write_bytes(b"")
        containers[w] = d

    block = C.scan_run(
        output_dir=tmp_path, in_containers=containers,
        schema=["experiment", "channel", "well", "fov", "timepoint"],
        sep="_", csv_prefix="measurements_", fov_field_present=True,
    )
    totals = block.get("totals") or {}
    assert totals.get("unreadable") == len(raising), (
        f"the scan did not count the wells it could not read: {totals}")
    assert C.plate_total(totals) == len(wells), (
        f"the plate lost the unreadable wells from its size: {totals}")
    assert block.get("partial_scan") is True
