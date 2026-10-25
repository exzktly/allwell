"""A run limited to named wells resumes only those wells, so an unrelated unfinished run does not block it.

Reported: running a subset of a nanowell plate on a remote host was refused with "this plate has results from an
earlier run that did not finish, so this run would add to them incrementally, and a nanowell run cannot do that on a
remote host yet". ``resume_aware_spec`` turned on Incremental because *some* earlier run of the plate was unfinished,
whichever wells it had touched, and the remote nanowell check then refused the run — for every subset, until a full
run finished. A run limited to named wells can be left half-done only in those wells, so only they decide now:
:func:`pipeline.run_report.wells_left_part_way` asks which have output in the unfinished run's folder and no
completion seal, and the run is made incremental only when one does.

A remote nanowell run that is made incremental is no longer refused at all: it goes well by well
(``analyze.run_spec._nanowell_remote_resume_by_well``) — the wells finished here are skipped, every other is sent
whole and analysed in full (``tests/test_a_remote_rerun_skips_finished_wells.py``).
"""

from __future__ import annotations

import pytest

from pipeline.run_report import COMPLETE_DIRNAME, wells_left_part_way

pytest.importorskip("PySide6")

from tests.test_nanowell_form import _plate, _use_nanowell_schema, tab  # noqa: E402,F401


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_only_a_well_with_output_and_no_seal_is_part_way(tmp_path):
    out = tmp_path / "out"
    _write(out / "A01_out.zip")                                   # whole: output and its seal
    _write(out / "measurements_A01.csv.gz")
    _write(out / COMPLETE_DIRNAME / "A01.json", "{}")
    _write(out / "measurements_B02.csv")                          # output, no seal: part-way
    _write(out / "nanowells" / "D04_grid.json")                   # a nanowell well's grid record, no seal
    _write(out / "E05_out.zip", "")                               # an empty file is no output
    assert wells_left_part_way(out, ["A1", "B02", "C03", "D04", "E05"]) == ["B02", "D04"]
    assert wells_left_part_way(tmp_path / "missing", ["A01"]) == []


@pytest.fixture
def remote_nanowell_run(tab, tmp_path, monkeypatch):  # noqa: F811
    """The form set up for a remote nanowell run of a plate whose last run failed, and a ``start`` that reports
    whether the run got past every refusal."""
    import services.remote_config as remote_config
    from services import run_journal

    root, _ = _plate(tmp_path)
    out = root / "out"
    out.mkdir(exist_ok=True)
    run_journal.open_run(out, spec={"opts": {"raw": str(root)}}, mode="remote", input_dir=root / "in")
    run_journal.close_run(out, outcome="error", message="a well failed")
    _use_nanowell_schema(tab)
    tab._input_edit.setText(str(root))
    tab._nanowell_cb.setChecked(True)
    tab._remote_cb.addItem("bench-box", userData="bench-box")
    tab._remote_cb.setCurrentIndex(tab._remote_cb.findData("bench-box"))
    monkeypatch.setattr(remote_config, "get_remote", lambda name: object())
    logged = []
    monkeypatch.setattr(tab, "_log_line", lambda text, tag="INFO": logged.append(text))

    sent: list[dict] = []

    class Launched(Exception):
        """The run reached the runner: past every refusal. What it was handed is in ``sent``."""

    def launch(_pipeline, opts, **_kwargs):
        sent.append(dict(opts))
        raise Launched

    monkeypatch.setattr(tab, "_set_running_ui_state", lambda **_k: None)
    monkeypatch.setattr(tab._runner, "start", launch)

    def start():
        try:
            return tab.start_run(tab._collect_run_spec())
        except Launched:
            return "launched"

    start.sent = sent
    return tab, out, start, logged


def test_a_subset_the_unfinished_run_never_touched_runs_on_the_remote(remote_nanowell_run):
    tab, _out, start, logged = remote_nanowell_run
    tab._only_wells_edit.setText("A01")
    assert start() == "launched"
    assert any("none of the wells this run names (A01) was left part-way" in line for line in logged)


def test_a_subset_whose_well_the_unfinished_run_left_whole_runs_on_the_remote(remote_nanowell_run):
    tab, out, start, _logged = remote_nanowell_run
    _write(out / "A01_out.zip")
    _write(out / COMPLETE_DIRNAME / "A01.json", "{}")
    tab._only_wells_edit.setText("A01")
    assert start() == "launched"


def test_a_subset_with_a_part_way_well_runs_well_by_well_on_the_remote(remote_nanowell_run):
    """It was refused, naming the well and what Force does. It now runs, not incrementally: the part-way well is
    sent whole and analysed in full on the remote, and the log says so."""
    tab, out, start, logged = remote_nanowell_run
    _write(out / "nanowells" / "A01_grid.json")
    tab._only_wells_edit.setText("A01")
    assert start() == "launched"
    assert start.sent[-1]["incremental"] is False
    assert any("goes well by well" in line for line in logged)


def test_the_whole_plate_after_an_unfinished_run_runs_well_by_well(remote_nanowell_run):
    """The reported case: a stopped remote nanowell run, started again, was refused with Force as the only way on."""
    _tab, _out, start, logged = remote_nanowell_run
    assert start() == "launched"
    assert start.sent[-1]["incremental"] is False
    assert any("goes well by well" in line for line in logged)


def test_a_forced_resume_stays_forced(remote_nanowell_run):
    tab, _out, start, _logged = remote_nanowell_run
    tab._force_cb.setChecked(True)
    assert start() == "launched"
    assert start.sent[-1].get("force") is True
