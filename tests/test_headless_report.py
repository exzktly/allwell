"""The headless report's manifest, exit codes, and the write suspension that
keeps it from changing the dataset it reports on.

The run through the real command is ``tests/test_headless_report_command.py``. The module under test is the
root entry point ``allwell_report.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

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


def test_writes_under_a_suspended_root_are_refused_and_counted(tmp_path):
    """Refused with ``WritesSuspended`` — an ``OSError`` — not dropped: a
    dropped write that returns normally lets its caller act on a change that
    never landed (the legacy migration then deleted the user's only copy)."""
    inside = tmp_path / "plate" / "out" / "persistence.json"
    outside = tmp_path / "elsewhere" / "persistence.json"
    before = _io.suppressed_writes()
    with _io.writes_suspended(tmp_path / "plate"):
        with pytest.raises(_io.WritesSuspended):
            _io.atomic_write_json(inside, {"ratios": [1]})
        _io.atomic_write_json(outside, {"ratios": [1]})
    assert isinstance(_io.WritesSuspended("x"), OSError)
    assert not inside.exists()
    assert json.loads(outside.read_text()) == {"ratios": [1]}
    assert _io.suppressed_writes() - before == 1
    # A refused write is not history either.
    assert P.read_events(inside.parent).missing
    # And the suspension ends with the block.
    _io.atomic_write_json(inside, {"ratios": [2]})
    assert json.loads(inside.read_text()) == {"ratios": [2]}


def test_a_refused_deferred_write_reports_failure_when_the_writer_runs_it(tmp_path):
    target = tmp_path / "plate" / "persistence.json"
    queued = []
    with _io.writes_suspended(tmp_path / "plate"):
        with _io.background_writes(queued.append):
            _io.atomic_write_json(target, {"x": 1}, deferrable=True)
        ok, detail = queued[0]()
    assert not ok and "suspended" in detail and not target.exists()


def test_a_missing_dataset_is_invalid_input_and_still_writes_a_manifest(tmp_path, capsys):
    out = tmp_path / "out"
    code = M.main(["--dataset", str(tmp_path / "nope"), "--output", str(out)])
    assert code == M.EXIT_INVALID_INPUT
    data = json.loads((out / M.MANIFEST_FILENAME).read_text())
    assert data["status"] == "invalid_input"
    assert "is not a folder" in data["message"]
    assert data["schema_version"] == M.SCHEMA_VERSION
    assert "invalid_input" in capsys.readouterr().out


# ── the suspension must not let a caller act on a write that did not land ──

def _state(data_dir):
    from review.core.dataset_state import DatasetState
    ds = DatasetState()
    ds.begin_load(data_dir)
    return SimpleNamespace(dataset=ds)


def test_a_legacy_sidecar_is_not_deleted_under_suspension(tmp_path):
    """Found by review: the migration writes persistence.json, then deletes the
    legacy files it absorbed. With the write silently dropped the user's ratios
    were in neither file afterwards."""
    from review.persistence import _doc
    (tmp_path / "ratios.json").write_text(json.dumps([{"name": "r"}]))
    with _io.writes_suspended(tmp_path):
        _doc.read(_state(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ratios.json"]


def test_an_unreadable_doc_is_not_renamed_aside_under_suspension(tmp_path):
    from review.persistence import _doc
    (tmp_path / "persistence.json").write_text("{not json")
    state = _state(tmp_path)
    with _io.writes_suspended(tmp_path):
        _doc.read(state)
        assert _doc.write(state, {"ratios": []}) is False
    assert sorted(p.name for p in tmp_path.iterdir()) == ["persistence.json"]


def test_text_writes_and_v2_backups_are_refused_too(tmp_path):
    from review.domain.sample_definitions import _backup_pre_v2
    info = tmp_path / "pipeline_info.json"
    info.write_text("{}")
    with _io.writes_suspended(tmp_path):
        with pytest.raises(_io.WritesSuspended):
            _io.atomic_write_text(tmp_path / "m_A01.csv", "a,b\n")
        with pytest.raises(_io.WritesSuspended):
            _backup_pre_v2(info)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pipeline_info.json"]


def test_a_sidecar_above_the_results_folder_is_suspended_too(tmp_path):
    """``--dataset plate/out`` with the sidecar at ``plate/``: every
    definitions and gating write lands outside the folder given."""
    out = tmp_path / "plate" / "out"
    out.mkdir(parents=True)
    (tmp_path / "plate" / "pipeline_info.json").write_text("{}")
    roots = M.suspended_roots(out)
    assert roots == [out, tmp_path / "plate" / "pipeline_info.json"]
    # The parent folder itself is not suspended — it may hold other datasets.
    with _io.writes_suspended(*roots):
        assert _io.is_suspended(tmp_path / "plate" / "pipeline_info.json")
        assert not _io.is_suspended(tmp_path / "plate" / "other.json")


def test_a_rerun_clears_what_the_last_run_left_and_nothing_else(tmp_path):
    figures = tmp_path / "figures"
    figures.mkdir()
    for name in ("01_old.png", "01_old.svg", "notes.png"):
        (figures / name).write_text("x")
    for name in ("report.pdf", "manifest.json", "mine.txt"):
        (tmp_path / name).write_text("x")
    M.clear_previous_output(tmp_path)
    assert sorted(p.name for p in figures.iterdir()) == ["notes.png"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["figures", "mine.txt"]


def test_a_non_durable_history_line_skips_the_fsync(tmp_path, monkeypatch):
    import os as _os
    calls = []
    monkeypatch.setattr(_os, "fsync", lambda fd: calls.append(fd))
    P.append_event(tmp_path, "reset", source="review", detail={"scopes": []},
                   durable=False)
    assert calls == []
    P.append_event(tmp_path, "reset", source="review", detail={"scopes": []})
    assert len(calls) == 1
