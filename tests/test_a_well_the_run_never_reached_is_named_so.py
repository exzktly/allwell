"""A well the run never reached is called that, not "staged, never processed".

Reported: a remote batched nanowell run lost its heartbeat part-way through the
plate. The host suspended the batch in flight and ran no more, so only the first
batch — the empty reference well and the next two — came back. Home then said
58 of 61 wells were "staged, never processed", which reads as though the run
was asked to process them and silently skipped them. The record was right about
the files (input present, nothing written) and wrong about why: the run never
got to them.

On disk the two cases are identical, so only the run can tell them apart. The
client-side check after a remote run now records ``ended_early`` from the
pipeline's exit status, and Home words those wells as not reached. A run that
ended cleanly keeps the old wording, because there a well with no output really
is one the run was given and wrote nothing for.
"""

from __future__ import annotations

import json
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import completeness
from review.features.home import collect as hc
from review.features.home.plate_panel import state_label, well_tooltip
from review.features.home.triage import collect as triage


def _well(covered_any):
    return {"intent": 4, "covered_any": covered_any, "covered_csv": covered_any,
            "missing_count": 4 - covered_any, "missing": [], "csv_state": "ok",
            "has_in": True, "has_out_zip": bool(covered_any), "has_out_folder": False,
            "failed": [], "health": "complete" if covered_any == 4 else "partial"}


# A plate that came back after the first batch: one finished well, two never reached.
WELLS = {"A01": _well(4), "A02": _well(0), "A03": _well(0)}


def _ctx(**flags):
    block = completeness.build_block(
        {}, finished_at="2026-11-01T12:00:00Z", **flags)
    block["wells"] = WELLS
    return types.SimpleNamespace(state=types.SimpleNamespace(
        dataset=types.SimpleNamespace(
            data_dir=Path("/data/plate"), pipeline_info={"run_check": block},
            well_paths=dict.fromkeys(WELLS, "x"), well_labels={}),
        selection=types.SimpleNamespace(selections=[], plot_groups=[], notes_text="")))


# ── the record ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("flags,expected", [
    ({}, False),
    ({"ended_early": True}, True),
    # A suspend is an early end, and records written before `ended_early`
    # existed carry only `suspended`.
    ({"suspended": True}, True),
])
def test_the_record_says_whether_the_run_reached_its_end(flags, expected):
    assert completeness.build_block({}, **flags)["ended_early"] is expected


def test_the_client_side_check_writes_it_into_the_sidecar(tmp_path):
    from services.remote_fetch import run_client_side_completeness_check

    (tmp_path / "pipeline_info.json").write_text(json.dumps({
        "schema": "exp:channel:well:fov:timepoint", "separator": "_"}))
    run_client_side_completeness_check(None, tmp_path, log_fn=lambda _m: None, ended_early=True)
    info = json.loads((tmp_path / "pipeline_info.json").read_text())
    assert info["run_check"]["ended_early"] is True


@pytest.mark.parametrize("ok", [True, False])
def test_verification_records_the_pipeline_s_exit_status_as_the_early_end(tmp_path, monkeypatch, ok):
    """The caller's sequence, not the helper's API: ``verify`` is what a remote
    run calls with ``ok=(rc == 0)``, and a lost heartbeat or node reaches it as
    a non-zero exit."""
    from services import remote_fetch

    run = SimpleNamespace(local_output_dir=tmp_path, remote_output_dir="/r/out", local_input_dir=tmp_path,
                          incremental=False, nanowell=False, expected_wells=set(), force_wells=frozenset(),
                          force=False)
    verifier = remote_fetch.FetchedOutputVerifier(SimpleNamespace(ssh_alias="gpu"), run, None, threading.Event())
    seen = []
    monkeypatch.setattr(verifier, "_rsync_optional", lambda *a, **k: True)
    monkeypatch.setattr(verifier, "_verify_local_manifest", lambda *, log: [])
    monkeypatch.setattr(verifier, "_reconcile_fetch", lambda *, log: "")
    monkeypatch.setattr(remote_fetch, "pull_nanowell_results", lambda v, *, log: [])
    monkeypatch.setattr(remote_fetch, "refresh_pipeline_info_available_from_outputs", lambda *a, **k: None)
    monkeypatch.setattr(remote_fetch, "run_client_side_completeness_check",
                        lambda *a, **k: seen.append(k.get("ended_early")) or {})
    verifier.verify(ok, log=lambda _m: None)
    assert seen and seen[0] is (not ok)


# ── what Home says ──────────────────────────────────────────────────────────

def test_the_wells_keep_their_painted_state_either_way():
    """The colour is the files' fact and is unchanged; only the explanation moves."""
    for ctx in (_ctx(), _ctx(ended_early=True)):
        assert hc.plate_cells(ctx) == {"A01": hc.CELL_COMPLETE, "A02": hc.CELL_INPUT_ONLY,
                                       "A03": hc.CELL_INPUT_ONLY}


def test_after_an_early_end_the_health_row_names_them_not_reached():
    rows = {r["label"]: r for r in hc.health_rows(_ctx(ended_early=True))}
    row = rows["Not reached — run ended early"]
    assert row["wells"] == ["A02", "A03"]
    assert "ended before it got to these wells" in row["value"]
    assert "Staged, never processed" not in rows


def test_after_a_clean_end_the_health_row_is_unchanged():
    rows = {r["label"]: r for r in hc.health_rows(_ctx())}
    assert rows["Staged, never processed"]["wells"] == ["A02", "A03"]
    assert rows["Staged, never processed"]["value"] == "input is present and nothing was written"


def test_the_next_step_says_not_reached_after_an_early_end():
    assert hc.next_step(_ctx(ended_early=True))["text"] == (
        "2 well(s) were not reached — the run ended before it processed them.")
    assert hc.next_step(_ctx())["text"] == "2 well(s) were staged and never processed."


def test_the_tooltip_and_the_legend_say_it_too():
    cells = hc.plate_cells(_ctx(ended_early=True))
    assert well_tooltip("A02", cells, WELLS, ended_early=True).startswith(
        "A02 · not reached — run ended early · 0 of 4")
    assert well_tooltip("A02", cells, WELLS).startswith("A02 · staged, never processed · 0 of 4")
    assert state_label(hc.CELL_INPUT_ONLY, ended_early=True) == "Not reached — run ended early"
    assert state_label(hc.CELL_INPUT_ONLY) == "Staged, never processed"
    assert state_label(hc.CELL_COMPLETE, ended_early=True) == "Complete"
    legend = {s: label for s, label, _n in hc.plate_legend(_ctx(ended_early=True))}
    assert legend[hc.CELL_INPUT_ONLY] == "not reached"


def test_qc_triage_says_it_as_well(monkeypatch):
    monkeypatch.setattr(triage, "_schema", lambda ctx: None)
    early = [r.detail for r in triage._well_rows(_ctx(ended_early=True)) if r.kind == triage.KIND_UNSTARTED]
    clean = [r.detail for r in triage._well_rows(_ctx()) if r.kind == triage.KIND_UNSTARTED]
    assert early == ["The run ended before it reached this well: its input is present and nothing was "
                     "written for it."] * 2
    assert clean == ["Input is present and nothing was written for it."] * 2
