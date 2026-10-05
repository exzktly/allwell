"""The headless report's manifest, exit codes, and the write suspension that
keeps it from changing the dataset it reports on.

The run through the real command is ``tests/test_headless_report_command.py``. The module under test is the
root entry point ``allwell_report.py``.
"""

from __future__ import annotations

import json

import pytest

from review.persistence import _io
import allwell_report as M
from util import provenance_log as P


def _manifest(*statuses):
    m = M.Manifest(dataset="d", output="o", generated_at="t", app_version="v")
    m.plots = [M.PlotEntry(id=str(i), title="", tab="", status=s)
               for i, s in enumerate(statuses)]
    return m


@pytest.mark.parametrize("statuses, code, status", [
    (("drawn", "drawn"), M.EXIT_COMPLETE, "complete"),
    (("drawn", "not_drawn"), M.EXIT_PARTIAL, "partial"),
    (("not_drawn",), M.EXIT_FAILED, "failed"),
    ((), M.EXIT_NOTHING_TO_DO, "nothing_to_report"),
])
def test_the_exit_code_follows_the_entries(statuses, code, status):
    m = _manifest(*statuses)
    assert m.finish() == code
    out = m.to_json()
    assert out["status"] == status
    assert (out["drawn"], out["not_drawn"]) == (statuses.count("drawn"),
                                                len(statuses) - statuses.count("drawn"))


def test_a_decided_exit_code_is_not_overwritten_by_the_entries():
    m = _manifest()
    m.exit_code = M.EXIT_INVALID_INPUT
    assert m.finish() == M.EXIT_INVALID_INPUT


def test_every_outcome_has_its_own_exit_code():
    codes = [M.EXIT_COMPLETE, M.EXIT_FAILED, M.EXIT_INVALID_INPUT,
             M.EXIT_PARTIAL, M.EXIT_NOTHING_TO_DO]
    assert len(set(codes)) == len(codes) == len(M.STATUS_FOR_EXIT)


def test_writes_under_a_suspended_root_are_dropped_and_counted(tmp_path):
    inside = tmp_path / "plate" / "out" / "persistence.json"
    outside = tmp_path / "elsewhere" / "persistence.json"
    before = _io.suppressed_writes()
    with _io.writes_suspended(tmp_path / "plate"):
        _io.atomic_write_json(inside, {"ratios": [1]})
        _io.atomic_write_json(outside, {"ratios": [1]})
    assert not inside.exists()
    assert json.loads(outside.read_text()) == {"ratios": [1]}
    assert _io.suppressed_writes() - before == 1
    # A suppressed write is not history either.
    assert P.read_events(inside.parent).missing
    # And the suspension ends with the block.
    _io.atomic_write_json(inside, {"ratios": [2]})
    assert json.loads(inside.read_text()) == {"ratios": [2]}


def test_a_suppressed_deferred_write_is_dropped_when_the_writer_runs_it(tmp_path):
    target = tmp_path / "plate" / "persistence.json"
    queued = []
    with _io.writes_suspended(tmp_path / "plate"):
        with _io.background_writes(queued.append):
            _io.atomic_write_json(target, {"x": 1}, deferrable=True)
        ok, _ = queued[0]()
    assert ok and not target.exists()


def test_a_missing_dataset_is_invalid_input_and_still_writes_a_manifest(tmp_path, capsys):
    out = tmp_path / "out"
    code = M.main(["--dataset", str(tmp_path / "nope"), "--output", str(out)])
    assert code == M.EXIT_INVALID_INPUT
    data = json.loads((out / M.MANIFEST_FILENAME).read_text())
    assert data["status"] == "invalid_input"
    assert "is not a folder" in data["message"]
    assert data["schema_version"] == M.SCHEMA_VERSION
    assert "invalid_input" in capsys.readouterr().out
