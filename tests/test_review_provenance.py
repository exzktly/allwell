"""Review's analysis changes reach the dataset's provenance log
(``review/persistence/provenance.py``), recorded at the write itself.

Every write here goes through a real persistence path — ``_doc.set_section``
on a real ``DatasetState``, the real gating writer, the real reset runner — so
the tests measure the seam the app uses rather than a call to the recorder.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from review.core.dataset_state import DatasetState
from review.persistence import _doc, _io, provenance, reset_scopes
from util import provenance_log as P


def _ctx(data_dir: Path):
    ds = DatasetState()
    ds.begin_load(data_dir)
    return SimpleNamespace(state=SimpleNamespace(dataset=ds))


def _events(directory):
    return [e for e in P.read_events(directory).events]


def _changed(directory):
    return [(e["detail"]["domain"], e["detail"]["before"], e["detail"]["after"])
            for e in _events(directory) if e["event"] == "analysis_changed"]


# ── the roster ──────────────────────────────────────────────────────────────

def _analysis_domains() -> set[str]:
    return {d for scope in reset_scopes.ANALYSIS_SCOPES for d in scope.domains}


def _roster_gaps(analysis: set[str], stored: set[str], under: dict) -> list[str]:
    gaps = sorted(analysis - stored - set(under))
    stale = sorted((stored | set(under)) - analysis)
    dangling = sorted(v for v in under.values() if v not in stored)
    return ([f"unrecorded: {d}" for d in gaps] + [f"not analysis: {d}" for d in stale]
            + [f"recorded under a domain with no block: {d}" for d in dangling])


def test_every_analysis_domain_reaches_the_log():
    """A domain added to a reset scope is analysis state; its changes must be
    recorded, or the history silently stops covering it."""
    problems = _roster_gaps(_analysis_domains(), set(provenance.STORED_AT.values()),
                            provenance.RECORDED_UNDER)
    assert not problems, (
        "review/persistence/provenance.py STORED_AT / RECORDED_UNDER disagree "
        "with reset_scopes.ANALYSIS_SCOPES:\n  " + "\n  ".join(problems))


def test_the_roster_guard_bites():
    analysis = _analysis_domains() | {"new_domain"}
    assert "unrecorded: new_domain" in _roster_gaps(
        analysis, set(provenance.STORED_AT.values()), provenance.RECORDED_UNDER)
    assert "not analysis: view_state" in _roster_gaps(
        _analysis_domains(), set(provenance.STORED_AT.values()) | {"view_state"},
        provenance.RECORDED_UNDER)


def test_view_state_is_not_analysis_and_is_not_recorded(tmp_path):
    ctx = _ctx(tmp_path)
    _doc.set_section(ctx, "view_state", {"tab": "Bar"})
    assert P.read_events(tmp_path).missing


# ── persistence.json ────────────────────────────────────────────────────────

def test_a_section_change_is_one_event_with_its_digests(tmp_path):
    ctx = _ctx(tmp_path)
    _doc.set_section(ctx, "ratios", {"r1": {"num": "GFP", "den": "RFP"}})
    _doc.set_section(ctx, "ratios", {"r1": {"num": "GFP", "den": "NIR"}})
    assert _changed(tmp_path) == [
        ("ratios", None, P.block_digest({"r1": {"num": "GFP", "den": "RFP"}})),
        ("ratios", P.block_digest({"r1": {"num": "GFP", "den": "RFP"}}),
         P.block_digest({"r1": {"num": "GFP", "den": "NIR"}})),
    ]
    event = _events(tmp_path)[0]
    assert event["source"] == "review" and event["detail"]["file"] == "persistence.json"


def test_rewriting_the_same_value_records_nothing(tmp_path):
    ctx = _ctx(tmp_path)
    _doc.set_section(ctx, "qc_params", {"min_area": 10})
    _doc.set_section(ctx, "view_state", {"tab": "Bar"})     # rewrites the doc
    _doc.set_section(ctx, "qc_params", {"min_area": 10})
    assert [d for d, *_ in _changed(tmp_path)] == ["qc_params"]


def test_removing_a_section_records_an_absent_after(tmp_path):
    ctx = _ctx(tmp_path)
    _doc.set_section(ctx, "crop_region", {"region": [0, 0, 5, 5]})
    _doc.set_section(ctx, "crop_region", None)
    assert _changed(tmp_path)[-1][2] is None


def test_the_log_sits_beside_a_sidecar_one_level_up(tmp_path):
    # The Analyze layout: Review loads out/ while pipeline_info.json is above it.
    (tmp_path / "pipeline_info.json").write_text("{}")
    out = tmp_path / "out"
    out.mkdir()
    _doc.set_section(_ctx(out), "ratios", {"r": 1})
    assert len(_changed(tmp_path)) == 1
    assert P.read_events(out).missing


def test_a_failed_write_records_nothing(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)

    def _refuse(*a, **k):
        raise PermissionError("held open")

    monkeypatch.setattr(_io, "_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(_io.os, "replace", _refuse)
    _doc.set_section(ctx, "ratios", {"r": 1})       # _doc.write logs and returns
    monkeypatch.undo()
    assert P.read_events(tmp_path).missing


def test_a_deferred_write_is_recorded_when_the_writer_runs_it(tmp_path):
    ctx = _ctx(tmp_path)
    queued = []
    with _io.background_writes(queued.append):
        _doc.set_section(ctx, "ratios", {"r": 1})
    assert P.read_events(tmp_path).missing, "recorded before the write landed"
    ok, _ = queued[0]()
    assert ok and [d for d, *_ in _changed(tmp_path)] == ["ratios"]


# ── pipeline_info.json and cell_overrides.json ──────────────────────────────

def test_the_gating_writer_records_cell_gating_and_nothing_else(tmp_path):
    from review.domain.gating_state import save_gating_to_pipeline_info
    (tmp_path / "pipeline_info.json").write_text(json.dumps(
        {"fluor_tokens": ["GFP"], "cell_gating": {"area_min": 5}}))
    save_gating_to_pipeline_info(tmp_path, {"area_min": 50})
    assert _changed(tmp_path) == [
        ("cell_gating", P.block_digest({"area_min": 5}),
         P.block_digest({"area_min": 50}))]


def test_a_pipeline_change_between_loads_is_not_attributed_to_review(tmp_path):
    """The "before" is read at the write, so a block the pipeline changed since
    Review last wrote is not reported as Review's change."""
    from review.domain.gating_state import save_gating_to_pipeline_info
    info = tmp_path / "pipeline_info.json"
    info.write_text(json.dumps({"cell_gating": {"area_min": 5}}))
    save_gating_to_pipeline_info(tmp_path, {"area_min": 50})
    # A run rewrites the sidecar outside Review, changing the gating block.
    info.write_text(json.dumps({"cell_gating": {"area_min": 50, "thresh_frac_on": {"GFP": 30}},
                                "smfish_thresholds": {"GFP": 2}}))
    save_gating_to_pipeline_info(tmp_path, {"area_min": 50, "thresh_frac_on": {"GFP": 30}})
    assert len(_changed(tmp_path)) == 1


def test_cell_overrides_are_recorded_as_a_whole_file(tmp_path):
    _io.atomic_write_json(tmp_path / "cell_overrides.json", {"B4": {"7": 0}})
    assert [d for d, *_ in _changed(tmp_path)] == ["cell_overrides"]


def test_an_unwatched_file_is_never_read_or_recorded(tmp_path):
    _io.atomic_write_json(tmp_path / "bar_groups.json", {"a": 1})
    assert P.read_events(tmp_path).missing


def test_a_file_that_was_not_json_before_records_nothing(tmp_path, caplog):
    (tmp_path / "persistence.json").write_text("{not json")
    with caplog.at_level("WARNING", logger="review"):
        _io.atomic_write_json(tmp_path / "persistence.json", {"ratios": {"r": 1}})
    assert P.read_events(tmp_path).missing
    assert "will not be recorded" in caplog.text


def test_a_recording_failure_never_fails_the_write(tmp_path, monkeypatch, caplog):
    def _boom(*a, **k):
        raise RuntimeError("log unavailable")
    monkeypatch.setattr(provenance, "append_event", _boom)
    with caplog.at_level("WARNING"):
        _io.atomic_write_json(tmp_path / "persistence.json", {"ratios": {"r": 1}})
    assert json.loads((tmp_path / "persistence.json").read_text()) == {"ratios": {"r": 1}}
    assert "record the provenance" in caplog.text


# ── reset ───────────────────────────────────────────────────────────────────

def test_a_reset_records_the_scopes_that_completed(tmp_path, monkeypatch):
    calls = []
    scopes = (
        reset_scopes.ResetScope("ok", "", "", (), lambda ctx: calls.append("ok")),
        reset_scopes.ResetScope("bad", "", "", (), lambda ctx: 1 / 0),
    )
    monkeypatch.setattr(reset_scopes, "ANALYSIS_SCOPES", scopes)
    ctx = _ctx(tmp_path)
    assert reset_scopes.clear_analysis(ctx, ["ok", "bad"]) == ["ok"]
    (event,) = _events(tmp_path)
    assert event["event"] == "reset" and event["detail"]["scopes"] == ["ok"]


@pytest.mark.parametrize("keys", [[], ["bad"]])
def test_a_reset_that_cleared_nothing_records_nothing(tmp_path, monkeypatch, keys):
    monkeypatch.setattr(reset_scopes, "ANALYSIS_SCOPES", (
        reset_scopes.ResetScope("bad", "", "", (), lambda ctx: 1 / 0),))
    reset_scopes.clear_analysis(_ctx(tmp_path), keys)
    assert P.read_events(tmp_path).missing
