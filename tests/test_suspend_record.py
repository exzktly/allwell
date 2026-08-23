"""What survives a suspend, and what deliberately does not.

The record holds one thing: the ``RunSpec`` that was running, plus enough to
describe it back to the user. It holds nothing about where the run had got to,
because the output directory already is that — every finished well wrote its CSV
and its ``<well>_out.zip``, and ``--incremental`` reads those as its done-set.
A record that tried to track progress separately would be a second source of
truth to drift from the first.

It lives in the user's home because at startup the app does not yet know which
output folder to look in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import services.suspend_record as sr


@pytest.fixture(autouse=True)
def _fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _run(out: str, *, when: float = 1000.0, done: int = 3, total: int = 10):
    return sr.SuspendedRun(
        spec={"opts": {"raw": "/in"}, "remote_name": None},
        input_dir="/in", output_dir=out, suspended_at=when,
        wells_done=done, wells_total=total,
    )


def test_nothing_recorded_reads_as_nothing_to_resume():
    assert sr.load_all() == []


def test_a_saved_run_round_trips(_fake_home):
    assert sr.save(_run("/data/out")) is True
    runs = sr.load_all()
    assert len(runs) == 1
    got = runs[0]
    assert got.output_dir == "/data/out"
    assert got.spec == {"opts": {"raw": "/in"}, "remote_name": None}
    assert got.wells_done == 3 and got.wells_total == 10


def test_the_record_lives_beside_the_scratch_dir(_fake_home):
    sr.save(_run("/data/out"))
    assert sr.record_path() == _fake_home / ".allwell" / "suspended_runs.json"
    assert sr.record_path().is_file()


def test_saving_the_same_output_dir_twice_replaces_rather_than_stacks():
    sr.save(_run("/data/out", when=1000.0, done=3))
    sr.save(_run("/data/out", when=2000.0, done=7))
    runs = sr.load_all()
    assert len(runs) == 1
    assert runs[0].wells_done == 7


def test_several_plates_can_be_suspended_at_once():
    sr.save(_run("/data/a", when=1000.0))
    sr.save(_run("/data/b", when=2000.0))
    # Newest first: every caller takes the head of this list.
    assert [r.output_dir for r in sr.load_all()] == ["/data/b", "/data/a"]


def test_clearing_one_leaves_the_others():
    sr.save(_run("/data/a", when=1000.0))
    sr.save(_run("/data/b", when=2000.0))
    sr.clear("/data/a")
    assert [r.output_dir for r in sr.load_all()] == ["/data/b"]


def test_clearing_matches_a_differently_spelled_path(tmp_path):
    real = tmp_path / "out"
    real.mkdir()
    sr.save(_run(str(real)))
    sr.clear(str(real) + "/")                       # trailing slash
    assert sr.load_all() == []


def test_a_corrupt_file_reads_as_nothing_rather_than_raising(_fake_home):
    """A truncated record must not stop the app from starting."""
    sr.record_path().parent.mkdir(parents=True, exist_ok=True)
    sr.record_path().write_text("{not json at all")
    assert sr.load_all() == []


def test_a_record_from_a_future_build_is_ignored(_fake_home):
    sr.save(_run("/data/out"))
    raw = json.loads(sr.record_path().read_text())
    raw[0]["version"] = sr.RECORD_VERSION + 1
    sr.record_path().write_text(json.dumps(raw))
    assert sr.load_all() == []


def test_a_record_with_no_spec_is_ignored(_fake_home):
    sr.save(_run("/data/out"))
    raw = json.loads(sr.record_path().read_text())
    raw[0].pop("spec")
    sr.record_path().write_text(json.dumps(raw))
    assert sr.load_all() == []


def test_describe_names_the_plate_and_how_far_it_got():
    line = _run("/data/PlateX", done=17, total=40).describe()
    assert "PlateX" in line
    assert "17 of 40 well(s) done" in line


def test_describe_survives_an_unknown_total():
    line = _run("/data/PlateX", done=0, total=0).describe()
    assert "progress unknown" in line


def test_an_unwritable_home_reports_failure_rather_than_raising(monkeypatch):
    """A run that cannot record its suspension still suspends."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/proc/x/y")))
    assert sr.save(_run("/data/out")) is False
    assert sr.load_all() == []
