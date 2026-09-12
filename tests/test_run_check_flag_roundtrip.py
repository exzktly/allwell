"""The completeness flag, from the form's checkbox to the pipeline and back.

Three hops, and each one inverts or preserves the sense in a way that is easy
to get backwards: the form states the positive ("check completeness"), the
pipeline flag is the negative (``--no_run_check``), the sidecar records the
negative because it stores ``vars(args)``, and the job queue stores the
positive. A sign error anywhere silently turns the default inside out.

Also covers the remote suppression, which must not be mistaken for the user's
own opt-out — they write different things, and one of them is a lie if it
reaches the user's dataset.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from analyze.domain.job_edit import _opts_as_pinfo_keys
from analyze.log_pane import _completeness_line
from services.pipeline_service import build_pipeline_args


def _args(**opts):
    base = {
        "nuclear_token": "DAPI", "fluor_tokens": ["DAPI"],
        "csv_prefix": "measurements",
        "filename_schema": "exp:channel:well:fov:timepoint",
        "filename_sep": "_",
    }
    base.update(opts)
    return build_pipeline_args(
        Path("process_microscopy.py"), Path("/in"), Path("/out"), base)


# ── form → pipeline ─────────────────────────────────────────────────────────

def test_the_checkbox_on_emits_no_flag():
    assert "--no_run_check" not in _args(run_check=True)


def test_the_checkbox_off_emits_the_negative_flag():
    assert "--no_run_check" in _args(run_check=False)


def test_an_opts_dict_from_before_the_checkbox_means_on():
    """A queued job saved by an older build has no ``run_check`` key. It must
    default to on, matching the checkbox's own default, rather than silently
    turning the check off for every job in the queue."""
    assert "--no_run_check" not in _args()


def test_the_pipeline_parser_accepts_what_the_form_emits():
    import process_microscopy as pm

    argv = _args(run_check=False)
    parsed = pm.build_parser().parse_args(argv[2:])
    assert parsed.no_run_check is True


# ── job queue round-trip ────────────────────────────────────────────────────

def test_the_job_queue_stores_the_positive_and_hands_back_the_negative():
    assert _opts_as_pinfo_keys({"run_check": False})["no_run_check"] is True
    assert _opts_as_pinfo_keys({"run_check": True})["no_run_check"] is False


def test_a_job_without_the_key_gains_no_opinion():
    assert "no_run_check" not in _opts_as_pinfo_keys({"input_dir": "/in"})


# ── the card's completeness line ────────────────────────────────────────────

def test_an_absent_record_says_so_rather_than_implying_a_shortfall():
    assert _completeness_line({}) == "Completeness: not recorded for this run"


def test_turned_off_is_its_own_sentence():
    line = _completeness_line({"run_check": {"skipped": True}})
    assert "turned off" in line
    assert "0/0" not in line, "a skipped check has no counts to report"


def test_a_clean_run_reports_its_counts():
    line = _completeness_line({"run_check": {
        "totals": {"wells": 96, "complete": 96, "partial": 0}}})
    assert "96/96 well(s) complete" in line
    assert "partial" not in line


def test_a_short_run_names_the_shortfall():
    line = _completeness_line({"run_check": {
        "totals": {"wells": 96, "complete": 92, "partial": 4}}})
    assert "92/96 well(s) complete" in line
    assert "4 partial" in line


@pytest.mark.parametrize(("key", "expected"), [
    ("suspended", "run was suspended"),
    ("partial_scan", "check did not finish"),
])
def test_the_line_says_when_the_numbers_are_not_the_whole_story(key, expected):
    line = _completeness_line({"run_check": {
        "totals": {"wells": 96, "complete": 60, "partial": 36}, key: True}})
    assert expected in line


def test_a_non_dict_record_does_not_crash_the_card():
    assert _completeness_line({"run_check": "yes"}).endswith("not recorded for this run")


# ── remote suppression is not the user's opt-out ────────────────────────────

def test_the_client_check_drops_whatever_the_fetch_brought_back(tmp_path):
    """The remote always runs with ``--no_run_check``, so the sidecar that
    comes back carries the same "turned off" stub a user's own flag would. If
    that survived, a user who never touched the checkbox would be told they had
    turned the check off — and on a run whose client-side check then failed,
    that is the only thing they would be told.
    """
    from services.remote_runner import run_client_side_completeness_check

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "pipeline_info.json").write_text(json.dumps({
        "schema": "exp:channel:well:fov:timepoint",
        "separator": "_",
        "execution_options": {"csv_prefix": "measurements"},
        "run_check": {"version": 1, "skipped": True,
                      "skipped_reason": "--no_run_check"},
    }))

    run_client_side_completeness_check(None, out_dir, log_fn=lambda _m: None)

    info = json.loads((out_dir / "pipeline_info.json").read_text())
    assert info.get("run_check", {}).get("skipped") is not True, (
        "the remote's suppression stub must not survive as a user opt-out")
    assert info["schema"], "the rest of the sidecar survives"


def test_a_sidecar_with_no_schema_leaves_no_misleading_record(tmp_path):
    """Nothing to compare against means no record — which reads as "not
    checked", the honest answer — rather than a record claiming everything is
    fine because nothing could be measured."""
    from services.remote_runner import run_client_side_completeness_check

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "pipeline_info.json").write_text(json.dumps({"separator": "_"}))

    assert run_client_side_completeness_check(
        None, out_dir, log_fn=lambda _m: None) == {}
    assert "run_check" not in json.loads(
        (out_dir / "pipeline_info.json").read_text())


def test_the_remote_argv_always_suppresses_the_check():
    """Every remote launch, single or batched. A record written on the far side
    would inventory the uploaded split rather than the plate, and would be
    overwritten by the fetch in any case."""
    import services.remote_runner as rr

    argv = ["python", "process_microscopy.py",
            "--input_dir", "/local/in", "--output_dir", "/local/out",
            "--nuclear_token", "DAPI"]
    assert rr._local_input_dir_from_argv(argv).name == "in"
    assert rr._local_input_dir_from_argv(
        ["python", "x.py", "--input_dir=/local/plate"]).name == "plate"
    assert rr._local_input_dir_from_argv(["python", "x.py"]) is None
