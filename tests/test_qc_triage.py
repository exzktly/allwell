"""QC triage (Home → QC Triage): the rows, the decisions, the field-exclusion
layer, and its place at the inclusion chokepoint.

Fast lane: ``collect`` and ``controller`` run against a real ``DatasetState``
in a plain namespace. The one test that presses the buttons of the real tab is
``tests/test_qc_triage_in_a_real_app.py``.
"""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace


from pipeline.field_quality import FIELD_QUALITY_BLOCK
from review.core.dataset_state import DatasetState
from review.features.home.triage import collect as tc
from review.features.home.triage import controller as tcc

WELLS = ("A01", "A02", "A03", "B01", "B02", "B03")


def _fields(n=3, focus=27.0):
    return [{"fov": str(i + 1), "timepoint": "0", "focus": focus + i * 0.01,
             "sat_frac": 0.0, "dyn_range": 900.0 + i, "nuc_count": 300 + i,
             "nuc_area_median": 150.0 + i, "bg_level": 100.0 + i}
            for i in range(n)]


def _fq_block(bad_well="B02", bad_fov="2"):
    """Six wells, three fields each, every metric varying (so the scorer
    flags rather than declines), with one field badly defocused."""
    wells = {}
    for w in WELLS:
        flds = _fields(focus=27.0 + WELLS.index(w) * 0.05)
        for f in flds:
            if w == bad_well and f["fov"] == bad_fov:
                f["focus"] = 2.0
        wells[w] = {"well": w, "fields": flds, "measured": 3, "measured_of": 3}
    return {"wells": wells, "partial_wells": [], "measured": 18, "measured_of": 18}


def _run_check(wells=None, **extra):
    block = {"source": "run", "started_at": "", "finished_at": "",
             "skipped": False, "partial_scan": False,
             "wells": wells or {w: {"health": "complete", "covered_any": 3,
                                    "missing": [], "missing_count": 0,
                                    "failed": []} for w in WELLS},
             "findings": [], "errors": {"files": 0, "by_type": {}}}
    block.update(extra)
    return block


def _ctx(tmp_path, info, *, integrity=None):
    ds = DatasetState()
    ds.begin_load(tmp_path)
    for w in WELLS:
        ds.well_paths[w] = tmp_path / f"measurements_{w}.csv"
    ds.pipeline_info = info
    ds.fov_integrity.update(integrity or {})
    (tmp_path / "pipeline_info.json").write_text(json.dumps(info))
    calls = []
    saved = []
    return SimpleNamespace(
        state=SimpleNamespace(dataset=ds, qc=SimpleNamespace(params=None)),
        persistence=SimpleNamespace(save=saved.append),
        features=SimpleNamespace(call=lambda *a, **k: calls.append(a) or True),
        _calls=calls, _saved=saved)


def _kinds(rows):
    return [(r.kind, r.well, r.fov) for r in rows]


# ── every source that cannot speak says so ──────────────────────────────────

def test_no_record_is_a_row_not_an_empty_table(tmp_path):
    rows = tc.triage_rows(_ctx(tmp_path, {}))
    assert _kinds(rows) == [(tc.KIND_RECORD, "", "")]
    assert "no completeness record" in rows[0].detail.lower()


def test_a_clean_plate_is_an_empty_table_and_says_so(tmp_path):
    ctx = _ctx(tmp_path, {"run_check": _run_check()})
    rows = tc.triage_rows(ctx)
    assert rows == []
    assert tc.summary(ctx, rows).startswith("Nothing needs attention")


def test_an_unscorable_field_quality_block_is_a_row(tmp_path):
    block = _fq_block()
    block["partial_wells"] = ["A01"]
    block["wells"]["A01"]["measured_of"] = 5
    block["measured_of"] = 20
    rows = tc.triage_rows(_ctx(tmp_path, {"run_check": _run_check(),
                                          FIELD_QUALITY_BLOCK: block}))
    assert [r.kind for r in rows] == [tc.KIND_FIELD_QUALITY]
    assert "could not be scored" in rows[0].detail
    assert not rows[0].can_exclude


# ── processing problems offer a rerun, never an exclusion ───────────────────

def test_well_shortfalls_each_name_their_well_and_offer_a_rerun(tmp_path):
    wells = {w: {"health": "complete", "covered_any": 3, "missing": [],
                 "missing_count": 0, "failed": []} for w in WELLS}
    wells["A02"] = {"health": "missing", "covered_any": 3, "missing": [],
                    "missing_count": 0, "failed": []}
    wells["A03"] = {"health": "partial", "covered_any": 0,
                    "missing": [["1", "0h"], ["2", "0h"]], "missing_count": 2,
                    "failed": []}
    wells["B01"] = {"health": "partial", "covered_any": 2,
                    "missing": [["3", "0h"]], "missing_count": 4,
                    "failed": ["B01_3_0h_NIR.tif"]}
    info = {"run_check": _run_check(wells), "schema": "well_fov_timepoint_channel",
            "schema_fields": ["well", "fov", "timepoint", "channel"], "separator": "_"}
    rows = tc.triage_rows(_ctx(tmp_path, info))
    assert _kinds(rows) == [
        (tc.KIND_NO_CSV, "A02", ""),
        # Never processed: one row, not one per missing field.
        (tc.KIND_UNSTARTED, "A03", ""),
        (tc.KIND_MISSING_MORE, "B01", ""),
        (tc.KIND_FAILED, "B01", "3"),
        (tc.KIND_MISSING, "B01", "3"),
    ]
    unlisted = next(r for r in rows if r.kind == tc.KIND_MISSING_MORE)
    assert "3 more missing" in unlisted.detail
    assert all(r.can_rerun and not r.can_exclude for r in rows)
    assert tc.rerun_wells(rows) == ["A02", "A03", "B01"]


def _error_file(errors_dir, name, exc, *, age=0.0):
    errors_dir.mkdir(exist_ok=True)
    path = errors_dir / name
    path.write_text(f"Exception type: {exc}\nTraceback ...\n")
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def test_only_this_run_s_traceback_files_are_listed(tmp_path):
    from datetime import datetime, timezone
    started = datetime.fromtimestamp(time.time() - 60, tz=timezone.utc)
    info = {"run_check": _run_check(started_at=started.strftime("%Y-%m-%dT%H:%M:%SZ"))}
    ctx = _ctx(tmp_path, info)
    _error_file(tmp_path / "errors", "B03_2_pid10.txt", "MemoryError")
    _error_file(tmp_path / "errors", "A01_1_pid9.txt", "OSError", age=86400)
    rows = tc.triage_rows(ctx)
    assert _kinds(rows) == [(tc.KIND_ERROR_FILE, "B03", "")]
    assert rows[0].detail.startswith("MemoryError — errors/B03_2_pid10.txt")
    assert rows[0].can_rerun


def test_with_no_start_time_every_traceback_is_listed_and_says_it_may_be_old(tmp_path):
    ctx = _ctx(tmp_path, {"run_check": _run_check()})
    _error_file(tmp_path / "errors", "A01_1_pid9.txt", "OSError", age=86400)
    (row,) = tc.triage_rows(ctx)
    assert "may be from an earlier run" in row.detail


# ── acquisition problems offer an exclusion ─────────────────────────────────

def test_a_flagged_field_is_one_excludable_row(tmp_path):
    rows = tc.triage_rows(_ctx(tmp_path, {"run_check": _run_check(),
                                          FIELD_QUALITY_BLOCK: _fq_block()}))
    assert _kinds(rows) == [(tc.KIND_FIELD_QUALITY, "B02", "2")]
    assert rows[0].can_exclude and not rows[0].can_rerun
    assert rows[0].stage == tc.STAGE_ACQUISITION


def test_a_field_in_an_untrusted_well_cannot_be_excluded_and_says_why(tmp_path):
    rows = tc.triage_rows(_ctx(tmp_path, {"run_check": _run_check(),
                                          FIELD_QUALITY_BLOCK: _fq_block()},
                               integrity={"B02": False}))
    assert not rows[0].can_exclude
    assert "FOV labels" in rows[0].exclude_refusal


# ── decisions ───────────────────────────────────────────────────────────────

def test_exclude_records_reason_saves_and_reprojects(tmp_path):
    ctx = _ctx(tmp_path, {"run_check": _run_check(), FIELD_QUALITY_BLOCK: _fq_block()})
    (row,) = tc.triage_rows(ctx)
    assert tcc.exclude_fields(ctx, [row], "  blurred  ") == 1
    meta = ctx.state.dataset.field_exclusions[("B02", "2", "0")]
    assert meta["reason"] == "blurred" and meta["at"]
    assert ctx._saved == ["field_exclusions"]
    assert ("qc.recompute_all", ctx) in ctx._calls
    assert tc.row_state(ctx, row) == tc.STATE_EXCLUDED
    # Excluding again is not a second change.
    assert tcc.exclude_fields(ctx, [row], "again") == 0
    assert ctx._saved == ["field_exclusions"]


def test_an_empty_reason_excludes_nothing(tmp_path):
    ctx = _ctx(tmp_path, {"run_check": _run_check(), FIELD_QUALITY_BLOCK: _fq_block()})
    (row,) = tc.triage_rows(ctx)
    assert tcc.exclude_fields(ctx, [row], "   ") == 0
    assert not ctx.state.dataset.field_exclusions


def test_reopen_puts_an_excluded_field_back(tmp_path):
    ctx = _ctx(tmp_path, {"run_check": _run_check(), FIELD_QUALITY_BLOCK: _fq_block()})
    (row,) = tc.triage_rows(ctx)
    tcc.exclude_fields(ctx, [row], "blurred")
    assert tcc.reopen(ctx, [row]) == 1
    assert ctx.state.dataset.field_exclusions == {}
    assert tc.row_state(ctx, row) == tc.STATE_OPEN


def test_accept_then_reopen(tmp_path):
    wells = {"A01": {"health": "missing", "covered_any": 3, "missing": [],
                     "missing_count": 0, "failed": []}}
    ctx = _ctx(tmp_path, {"run_check": _run_check(wells)})
    (row,) = tc.triage_rows(ctx)
    assert tcc.accept(ctx, [row], "known dead well") == 1
    assert tc.row_state(ctx, row) == tc.STATE_ACCEPTED
    assert ctx.state.dataset.triage_decisions[row.key]["note"] == "known dead well"
    assert tc.summary(ctx, [row]) == "1 finding(s), 0 open."
    assert tcc.reopen(ctx, [row]) == 1
    assert tc.row_state(ctx, row) == tc.STATE_OPEN
    assert ctx._saved == ["triage_decisions", "triage_decisions"]


def test_a_decision_key_does_not_move_when_its_detail_does(tmp_path):
    """Keys are built from identity, not from the sentence: the count of
    unlisted missing groups changes as a rerun recovers some, and the accepted
    decision must still be found."""
    def rows_with(count):
        wells = {"A01": {"health": "partial", "covered_any": 1,
                         "missing": [["1", "0h"]], "missing_count": count,
                         "failed": []}}
        return tc.triage_rows(_ctx(tmp_path, {"run_check": _run_check(wells)}))
    before = {r.kind: r.key for r in rows_with(9)}
    after = {r.kind: r.key for r in rows_with(5)}
    assert before == after


def test_rerun_hands_off_exactly_the_selected_wells(tmp_path, monkeypatch):
    from review.features.home import controller as hc
    seen = {}
    monkeypatch.setattr(hc, "open_in_analyze",
                        lambda ctx, **kw: seen.update(kw))
    wells = {"A01": {"health": "missing", "covered_any": 3, "missing": [],
                     "missing_count": 0, "failed": []},
             "B03": {"health": "missing", "covered_any": 3, "missing": [],
                     "missing_count": 0, "failed": []}}
    ctx = _ctx(tmp_path, {"run_check": _run_check(wells)})
    rows = tc.triage_rows(ctx)
    assert tcc.rerun_wells(ctx, rows[1:]) == ["B03"]
    assert seen == {"force_wells": ["B03"], "only_wells": ["B03"]}
