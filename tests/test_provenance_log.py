"""The dataset's provenance log (``util/provenance_log.py``) and the Analyze
run's two entries in it (``services/run_provenance.py``).

The runner tests drive the real ``PipelineRunner`` against a stand-in pipeline,
the way ``tests/test_a_run_end_to_end_records_every_well.py`` does, because the
property that matters is the one no API-level test can see: that a second run
*adds* to the history while it *replaces* the run journal (CLAUDE.md §7.7 #1).
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services import run_journal                                   # noqa: E402
from services import run_provenance                                # noqa: E402
from services.pipeline_runner import PipelineRunner               # noqa: E402
from util import provenance_log as P                               # noqa: E402


def _run_started_detail():
    return {"mode": "local", "input_dir": "/in", "argv": ["x"], "scope": {}}


# ── the line format and the roster ──────────────────────────────────────────

def test_a_line_is_exactly_the_record_and_one_newline():
    line = P.format_line("reset", source="review", detail={"scopes": ["gating"]},
                         now=0.0)
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    record = json.loads(line)
    assert record == {
        "schema_version": P.SCHEMA_VERSION,
        "ts": "1970-01-01T00:00:00+00:00",
        "event": "reset",
        "source": "review",
        "app_version": record["app_version"],
        "detail": {"scopes": ["gating"]},
    }
    from version import __version__
    assert record["app_version"] == __version__


def test_an_unknown_event_is_refused():
    with pytest.raises(ValueError, match="not a provenance event"):
        P.format_line("gate_moved", source="review", detail={})


def test_an_unknown_source_is_refused():
    with pytest.raises(ValueError, match="not a provenance source"):
        P.format_line("reset", source="pipeline", detail={"scopes": []})


def test_a_missing_detail_key_is_refused_by_name():
    with pytest.raises(ValueError, match="argv, scope"):
        P.format_line("run_started", source="analyze",
                      detail={"mode": "local", "input_dir": "/in"})


def test_a_key_present_as_none_is_accepted():
    # Unknown is a value; absent is a missing key. The roster tells them apart.
    P.format_line("run_finished", source="analyze",
                  detail={"mode": "local", "outcome": "ok", "run_check": None})


def test_no_block_has_no_digest_and_an_empty_block_has_one():
    assert P.block_digest(None) is None
    assert P.block_digest({}) is not None
    assert P.block_digest({"a": 1, "b": 2}) == P.block_digest({"b": 2, "a": 1})
    assert P.block_digest({"a": 1}) != P.block_digest({"a": 2})


# ── append and read ─────────────────────────────────────────────────────────

def test_events_append_in_order_and_read_back(tmp_path):
    assert P.append_event(tmp_path, "reset", source="review",
                          detail={"scopes": ["gating"]}, now=1.0)
    assert P.append_event(tmp_path, "reset", source="review",
                          detail={"scopes": ["ratios"]}, now=2.0)
    read = P.read_events(tmp_path)
    assert [e["detail"]["scopes"] for e in read.events] == [["gating"], ["ratios"]]
    assert read.complete


def test_appending_never_disturbs_the_bytes_already_there(tmp_path):
    path = P.log_path(tmp_path)
    path.write_bytes(b"PREEXISTING-NOT-JSON\n")
    P.append_event(tmp_path, "reset", source="review", detail={"scopes": []})
    raw = path.read_bytes()
    assert raw.startswith(b"PREEXISTING-NOT-JSON\n")
    read = P.read_events(tmp_path)
    assert read.unreadable == 1 and len(read.events) == 1
    assert not read.complete


def test_a_torn_last_line_is_counted_and_hides_nothing_before_it(tmp_path):
    P.append_event(tmp_path, "reset", source="review", detail={"scopes": ["a"]})
    P.append_event(tmp_path, "reset", source="review", detail={"scopes": ["b"]})
    path = P.log_path(tmp_path)
    path.write_bytes(path.read_bytes() + b'{"schema_version": 1, "ev')
    read = P.read_events(tmp_path)
    assert [e["detail"]["scopes"] for e in read.events] == [["a"], ["b"]]
    assert read.unreadable == 1


def test_a_newer_schema_line_is_foreign_not_unreadable(tmp_path):
    P.log_path(tmp_path).write_text(json.dumps(
        {"schema_version": P.SCHEMA_VERSION + 1, "event": "x"}) + "\n")
    read = P.read_events(tmp_path)
    assert read.events == [] and read.foreign == 1 and read.unreadable == 0
    assert not read.complete


def test_no_log_is_missing_and_an_empty_log_is_not(tmp_path):
    assert P.read_events(tmp_path).missing
    P.log_path(tmp_path).write_bytes(b"")
    read = P.read_events(tmp_path)
    assert not read.missing and read.events == [] and read.complete


def test_no_directory_writes_nothing_and_says_so(tmp_path):
    assert P.append_event(None, "reset", source="review", detail={"scopes": []}) is False


def test_an_unwritable_directory_is_false_not_a_raise(tmp_path, caplog):
    missing = tmp_path / "does-not-exist"
    with caplog.at_level("WARNING", logger="allwell.provenance"):
        assert P.append_event(missing, "reset", source="review",
                              detail={"scopes": []}) is False
    assert "was not recorded" in caplog.text


# ── run_check: only this run's ──────────────────────────────────────────────

def _write_sidecar(out_dir, finished_at, totals):
    (out_dir / "pipeline_info.json").write_text(json.dumps({"run_check": {
        "finished_at": finished_at, "skipped": False, "suspended": False,
        "totals": totals, "errors": {"files": 0, "by_type": {}}}}))


def _mark(out_dir):
    return run_provenance.begin_run(out_dir, mode="local", input_dir="/in",
                                    argv=[], opts={})


def test_a_run_check_left_from_before_this_run_is_not_quoted(tmp_path):
    # The previous run's healthy totals, left by a run that died first.
    _write_sidecar(tmp_path, "2020-01-01T00:00:00+00:00", {"complete": 96})
    mark = _mark(tmp_path)
    assert run_provenance.run_check_summary(mark) is None


def test_a_run_check_this_run_wrote_is_quoted_even_in_the_same_second(tmp_path):
    # Same timestamp to the second as the block before it: only the content
    # can tell them apart, which is why the check is a fingerprint.
    _write_sidecar(tmp_path, "2020-01-01T00:00:00+00:00", {"complete": 96})
    mark = _mark(tmp_path)
    _write_sidecar(tmp_path, "2020-01-01T00:00:00+00:00", {"complete": 95})
    assert run_provenance.run_check_summary(mark)["totals"] == {"complete": 95}


def test_a_first_run_into_an_empty_folder_is_quoted(tmp_path):
    mark = _mark(tmp_path)
    _write_sidecar(tmp_path, "2020-01-01T00:00:00+00:00", {"complete": 3})
    assert run_provenance.run_check_summary(mark)["totals"] == {"complete": 3}


# ── the real runner ─────────────────────────────────────────────────────────

#: Writes a run_check stamped "now", the way the real pipeline does at its end,
#: then exits with the requested code.
_FAKE_PIPELINE = '''
import json, sys
from datetime import datetime, timezone
out_dir, rc = sys.argv[1], int(sys.argv[2])
if rc == 0:
    with open(out_dir + "/pipeline_info.json", "w") as fh:
        json.dump({"run_check": {
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "totals": {"complete": 2}, "errors": {"files": 0}}}, fh)
print("Run complete.", flush=True)
sys.exit(rc)
'''


def _run(tmp_path, rc, opts):
    script = tmp_path / "fake_pipeline.py"
    script.write_text(_FAKE_PIPELINE)
    in_dir = tmp_path / "in"
    in_dir.mkdir(exist_ok=True)
    out_dir = tmp_path / "out"

    def _resolve(opts, log_q, **kw):
        return (in_dir, out_dir, None)

    def _build(pipeline, i, o, opts):
        return [sys.executable, str(script), str(out_dir), str(rc)]

    import services.pipeline_runner as pr
    real_build = pr.build_pipeline_args
    pr.build_pipeline_args = _build
    runner = PipelineRunner()
    try:
        runner.start(script, {"filename_schema": "well_fov_channel",
                              "filename_sep": "_", **opts},
                     resolve_dirs=_resolve, journal_spec={"opts": {}})
        deadline = time.time() + 60
        finished = False
        while time.time() < deadline and not finished:
            try:
                kind, _ = runner.log_q.get(timeout=0.5)
            except Exception:
                continue
            finished = kind == "finished"
        assert finished, "the run never reported finishing"
    finally:
        pr.build_pipeline_args = real_build
    return out_dir


def test_a_second_run_adds_to_the_history_while_the_journal_is_replaced(tmp_path):
    out_dir = _run(tmp_path, 0, {"only_wells": ["B4"], "force_wells": ["B4"]})
    out_dir = _run(tmp_path, 1, {})

    read = P.read_events(out_dir)
    assert [e["event"] for e in read.events] == [
        "run_started", "run_finished", "run_started", "run_finished"]
    first_start, first_end, _, second_end = read.events
    assert first_start["source"] == "analyze"
    assert first_start["detail"]["mode"] == "local"
    assert first_start["detail"]["scope"]["only_wells"] == ["B4"]
    assert first_start["detail"]["scope"]["force_wells"] == ["B4"]
    assert first_end["detail"]["outcome"] == "ok"
    assert first_end["detail"]["run_check"]["totals"] == {"complete": 2}
    # The failed run wrote no run_check, and the first run's block is still in
    # the sidecar: quoting it here would describe a failed run as healthy.
    assert second_end["detail"]["outcome"] == "error"
    assert second_end["detail"]["run_check"] is None
    # And the journal describes the second run only.
    assert run_journal.read_journal(out_dir).outcome == "error"
