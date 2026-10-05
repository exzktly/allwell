"""*Only these wells* — from the Analyze form, and from the two handoffs that
fill it — reaches the pipeline, and nothing restores it behind the user's back.

The pipeline half (what ``--only_wells`` does to a run) is
``tests/test_only_wells_run.py``. This file is the chain in front of it:

* Validate & Repair's "Complete this run in Analyze" names exactly the wells a
  completion run can do something for, and none that are already whole;
* the shell writes that list into the form — and clears a stale one when a
  handoff names none — and says so on the dashboard;
* the form's field reaches argv canonical and sorted, so the remote run
  address (a hash of the argv) does not depend on how the list was typed;
* loading a previous run's settings never re-applies its subset;
* a run started with a subset says so where the user is looking.

The Analyze side is a real ``AnalyzeTab`` throughout (CLAUDE.md §7.8 rule 3).
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from tests.fixtures.dataset_builder import build_dataset  # noqa: E402
from pipeline import cli_args


def _plate_needing_work(root):
    """B02 whole; C08 processed with its CSV gone (needs force); D03 missing
    its 24h rows (partial)."""
    build_dataset(root, wells=["B02", "C08", "D03"], timepoints=["0h", "24h"],
                  fovs=("F001", "F002"))
    (root / "out" / "measurements_C08.csv").unlink()
    # Partial means missing from the CSV *and* the archive: the done-set is
    # the union of the two, so a CSV short of rows alone reads as whole.
    import zipfile
    out_zip = root / "out" / "D03_out.zip"
    with zipfile.ZipFile(out_zip) as zf:
        keep = [m for m in zf.namelist() if "_24h_" not in m]
    with zipfile.ZipFile(out_zip, "w") as zf:
        for m in keep:
            zf.writestr(m, b"x")
    path = root / "out" / "measurements_D03.csv"
    with path.open() as f:
        rows = list(csv.DictReader(f))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(r for r in rows if r["timepoint"] == "0h")


class _NotebookStub:
    index = None

    def setCurrentIndex(self, index) -> None:
        self.index = index


def _shell_with(analyze):
    from allwell import AllWellApp

    shell = type("_Shell", (), {})()
    shell._analyze = analyze
    shell._nb = _NotebookStub()
    shell._announce_analyze_handoff = AllWellApp._announce_analyze_handoff.__get__(shell)
    return shell


@pytest.fixture
def analyze(qtbot, monkeypatch):
    import analyze.form
    from analyze.tab import AnalyzeTab

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    tab = AnalyzeTab()
    qtbot.addWidget(tab)
    return tab


def _argv(tab, tmp_path):
    from services.pipeline_service import build_pipeline_args

    return build_pipeline_args(Path("process_microscopy.py"), tmp_path / "in",
                               tmp_path / "out", tab._collect_run_options())


def _only(argv):
    """What the pipeline's own parser reads back out of *argv*."""

    rest = [a for a in argv[2:]]
    return cli_args.build_parser().parse_args(rest).only_wells


# ── Validate: which wells it names ───────────────────────────────────────────

def test_validate_names_the_partial_and_the_csv_less_wells_and_not_the_whole_one(
        tmp_path):
    from datasets.validate import validate_compute as vc

    _plate_needing_work(tmp_path)
    (tmp_path / "out" / "B02_out.zip").unlink()   # B02 now csv_only
    report = vc.build_report(tmp_path)
    assert vc.well_health(next(w for w in report.wells if w.well == "B02")) == "csv_only"
    # csv_only is left out: its rows count as done, so a completion run would
    # skip it. The wrong implementation this excludes is ``health != "complete"``.
    assert vc.wells_needing_work(report) == ("C08", "D03")
    # Force stays the narrower list: D03 is finished by the incremental run,
    # and re-segmenting it whole would throw away the 0h work it already has.
    assert vc.wells_needing_force(report) == ("C08",)


def test_validate_leaves_out_the_exempt_wells(tmp_path):
    """Zero cells re-runs to the same empty result; never-fetched needs a
    fetch. The wrong implementation is one that forgets the exemptions."""
    from dataclasses import replace

    from datasets.validate import validate_compute as vc

    _plate_needing_work(tmp_path)
    report = vc.build_report(tmp_path)
    wells = tuple(replace(w, zero_cell_recorded=True) if w.well == "C08"
                  else replace(w, not_fetched_recorded=True) if w.well == "D03"
                  else w for w in report.wells)
    assert vc.wells_needing_work(replace(report, wells=wells)) == ()


def test_complete_this_run_hands_over_the_subset(qtbot, tmp_path):
    from datasets.validate import validate_compute as vc
    from datasets.validate.controller import ValidateController

    _plate_needing_work(tmp_path)
    calls: dict = {}

    def _open(path, info=None, *, incremental=False, force_wells=None,
              only_wells=None):
        calls.update(incremental=incremental, force=force_wells, only=only_wells)

    ctrl = ValidateController(on_open_in_analyze=_open)
    qtbot.addWidget(ctrl.page)
    ctrl._on_succeeded(vc.build_report(tmp_path), None)
    ctrl._on_complete_run(str(tmp_path))
    assert calls == {"incremental": True, "force": ["C08"], "only": ["C08", "D03"]}


def test_the_datasets_tab_forwards_the_subset(qtbot, tmp_path):
    from datasets.tab import DatasetsTab

    calls: dict = {}

    def _open(path, info=None, *, incremental=False, force_wells=None,
              only_wells=None):
        calls["only"] = only_wells

    tab = DatasetsTab(on_open_in_analyze=_open)
    qtbot.addWidget(tab)
    tab._open_in_analyze(str(tmp_path), {}, incremental=True, only_wells=["D03"])
    assert calls["only"] == ["D03"]


# ── the shell and the form ───────────────────────────────────────────────────

def test_the_handoff_fills_the_field_says_so_and_reaches_argv(analyze, tmp_path):
    from allwell import AllWellApp

    _plate_needing_work(tmp_path)
    info = json.loads((tmp_path / "out" / "pipeline_info.json").read_text())
    AllWellApp._open_dataset_in_analyze(
        _shell_with(analyze), tmp_path, info, incremental=True,
        force_wells=["C08"], only_wells=["C08", "D03"])

    assert analyze._only_wells_edit.text() == "C08, D03"
    status = analyze._dashboard._status.text()
    assert "only the 2 well(s) that need work will run (C08, D03)" in status
    assert _only(_argv(analyze, tmp_path)) == ["C08", "D03"]


def test_a_handoff_naming_no_subset_clears_a_stale_one(analyze, tmp_path):
    """The wrong implementation writes the field only when it has wells to
    write, leaving whatever an earlier run typed there to narrow this one."""
    from allwell import AllWellApp

    analyze._only_wells_edit.setText("H12")
    AllWellApp._open_dataset_in_analyze(_shell_with(analyze), tmp_path, {})
    assert analyze._only_wells_edit.text() == ""
    assert "--only_wells" not in _argv(analyze, tmp_path)


def test_the_typed_list_reaches_argv_canonical_and_sorted(analyze, tmp_path):
    """The remote run address hashes the argv: two spellings of one subset
    must be one argv, or the same run would never find its own directory."""
    analyze._only_wells_edit.setText("c8; A1  b02,")
    argv = _argv(analyze, tmp_path)
    assert argv[argv.index("--only_wells"):argv.index("--only_wells") + 4] == [
        "--only_wells", "A01", "B02", "C08"]
    analyze._only_wells_edit.setText("A01, C08, b2")
    assert _argv(analyze, tmp_path) == argv


def test_an_empty_field_means_the_whole_plate(analyze, tmp_path):
    assert analyze._only_wells_edit.text() == ""
    assert "--only_wells" not in _argv(analyze, tmp_path)


# ── never restored ───────────────────────────────────────────────────────────

def test_loading_a_sidecar_does_not_restore_a_previous_subset(analyze):
    analyze._apply_pipeline_info({"execution_options": {"only_wells": ["A01"]}})
    assert analyze._only_wells_edit.text() == ""
    analyze._only_wells_edit.setText("B02")
    analyze._apply_pipeline_info({"execution_options": {"only_wells": ["A01"]}})
    assert analyze._only_wells_edit.text() == "B02", (
        "loading settings overwrote the subset the user typed")


def test_preflight_names_it_among_the_run_scope_knobs():
    from analyze.preflight import _SCOPE_KEYS

    assert "only_wells" in _SCOPE_KEYS


# ── the run says so ──────────────────────────────────────────────────────────

def test_a_subset_run_announces_itself_when_it_starts(
        analyze, monkeypatch, tmp_path):
    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    monkeypatch.setattr(type(analyze._runner), "start", lambda self, *a, **k: None)
    analyze._only_wells_edit.setText("C08, D03")
    spec = analyze._collect_run_spec()
    spec.opts["raw"] = str(in_dir)
    assert analyze.start_run(spec, pipeline=Path("pm.py")) is None
    status = analyze._dashboard._status.text()
    assert "Only 2 well(s) will run (C08, D03)" in status, status
