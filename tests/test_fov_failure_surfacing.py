"""Regression tests for per-FOV-group failure diagnostics.

User report: on a rerun, 1-2 timepoints failed across all four wells, the
images were fine, and the log did not explain *why* — only the `--incremental`
hint. Root cause: unlike a whole-well failure (which persists a trace via
`_dump_well_failure_trace`), an individual FOV-group failure was only logged
with `exc_info=True` — never written to disk — and the `FAILED …` line was
often mis-classified as INFO by the GUI live-log, so the cause scrolled past.

These tests lock in the fix: a per-FOV-group failure now (1) persists a full
traceback under `<output_dir>/errors/`, and (2) surfaces as an ERROR line.
Pure file-IO + string logic — no StarDist/TF/Qt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("numpy")
pytest.importorskip("tifffile")

import process_microscopy as pm
from services.pipeline_runner import classify_log_line


def test_dump_fov_failure_trace_writes_readable_file(tmp_path):
    try:
        raise ValueError("boom in segmentation")
    except ValueError as exc:
        path = pm._dump_fov_failure_trace(tmp_path, "B3", "exp_nuc_B3_f01_T05", exc)

    assert path is not None
    assert path.parent == tmp_path / "errors"
    text = path.read_text()
    assert "B3" in text
    assert "exp_nuc_B3_f01_T05" in text
    assert "ValueError" in text
    assert "boom in segmentation" in text          # the real cause is captured


def test_process_groups_persists_trace_and_reports_partial(tmp_path, monkeypatch):
    """A FOV-group whose processing raises leaves a trace in the errors dir and
    is returned as a failed FOV (well still partial), without taking the whole
    well down."""
    tmp_images = tmp_path / "_tmp_images_B3"
    tmp_images.mkdir()
    output_dir = tmp_path / "dataset"           # persistent dataset dir
    output_dir.mkdir()

    def _boom(nuclear_path, fluor_paths, **kwargs):
        raise RuntimeError("transient device hiccup")

    monkeypatch.setattr(pm, "process_image_group", _boom)

    groups = [(Path("exp_nuc_B3_f01_T05.tif"), [])]
    records, failed, _reason, _skipped = pm._process_groups_in_worker(
        groups, {"output_dir": tmp_images}, "B3", errors_dir=output_dir,
    )

    assert records == []
    assert failed == ["exp_nuc_B3_f01_T05.tif"]
    traces = list((output_dir / "errors").glob("B3_*_pid*.txt"))
    assert len(traces) == 1
    body = traces[0].read_text()
    assert "RuntimeError" in body
    assert "transient device hiccup" in body


def test_process_groups_without_errors_dir_still_reports(tmp_path, monkeypatch):
    """When no errors_dir is supplied the failure is still tracked (no crash);
    only the persisted trace is skipped."""
    tmp_images = tmp_path / "_tmp_images_B3"
    tmp_images.mkdir()
    monkeypatch.setattr(
        pm, "process_image_group",
        lambda n, f, **k: (_ for _ in ()).throw(ValueError("x")),
    )
    records, failed, _reason, _skipped = pm._process_groups_in_worker(
        [(Path("exp_nuc_B3_f01_T05.tif"), [])], {"output_dir": tmp_images}, "B3",
    )
    assert failed == ["exp_nuc_B3_f01_T05.tif"]


@pytest.mark.parametrize("line,expected", [
    ("FAILED exp_nuc_B3_f01_T05.tif (OSError: 15334400 requested and 0 written)", "ERROR"),
    ("FAILED exp_nuc_B3_f01_T05.tif (KeyboardInterrupt: )", "ERROR"),   # no *Error substring
    ("Well B3 — PARTIAL: 1 of 10 FOV-group(s) FAILED", "ERROR"),
    ("Well B3 — done.", "INFO"),
    ("  seg B3: predict_instances 3.1s", "INFO"),
])
def test_classify_log_line_surfaces_failures(line, expected):
    """Lines with no level of their own still fall back to keyword heuristics."""
    assert classify_log_line(line) == expected


# ``process_microscopy`` logs "%(asctime)s  %(levelname)-8s  %(message)s", and
# workers insert "[worker N]" after the level. When the line states its severity
# there is nothing to infer — and inferring anyway got it wrong in both
# directions, most visibly on the end-of-run summary, which is logged at INFO
# and whose *column labels* contain the word "failed".
_SUMMARY = (
    "Total wells : 5  |  processed : 5  |  skipped : 0  |  nuclei : 30071  |  "
    "wells failed (no output) : 0  |  wells partial : 0  |  FOV-groups failed : 0"
)


@pytest.mark.parametrize("line,expected", [
    (f"2026-07-26 08:34:13,214  INFO      {_SUMMARY}", "INFO"),
    ("2026-07-26 08:34:13,214  INFO      [worker 4321]  Well A01 — done.", "INFO"),
    ("2026-07-26 08:34:13,214  WARNING   Wells that failed entirely:", "WARNING"),
    ("2026-07-26 08:34:13,214  ERROR     FAILED well A03: boom", "ERROR"),
    ("2026-07-26 08:34:13,214  CRITICAL  meltdown", "ERROR"),
    ("2026-07-26 08:34:13,214  DEBUG     tile 3 of 9", "INFO"),
])
def test_classify_log_line_trusts_the_emitted_level(line, expected):
    assert classify_log_line(line) == expected


def test_summary_line_is_not_an_error():
    """The regression this guards: a clean run whose summary rendered red.

    Every count in it is zero, and it is logged at INFO — but "failed" appears
    twice as a column label, which a substring test read as a failure.
    """
    assert "failed" in _SUMMARY
    assert classify_log_line(f"2026-07-26 08:34:13,214  INFO      {_SUMMARY}") == "INFO"


# ── a line's own severity beats a keyword sniffed out of its text ────────────

def test_a_note_about_the_errors_directory_is_not_an_error():
    """Reported: a red line saying the ``errors`` directory could not be found.

    Two things were wrong with it. The pipeline creates ``<output>/errors/``
    only when a well or FOV-group actually fails, so a clean run has none and
    the note is the *good* outcome — and ``classify_log_line`` sniffs keywords
    out of the whole line, paths included, so the name of the directory it was
    reporting the absence of painted it red. Good news, rendered as an alarm.

    The tell is that the identical line about ``pipeline_info.json`` came out
    INFO.
    """
    from services.pipeline_runner import classify_log_line

    errors = ("[remote] note: h:/out/errors/ not pulled (rc=23). The pipeline "
              "only creates errors/ when a well or FOV-group fails, so a clean "
              "run has none.\n")
    info = "[remote] note: h:/out/pipeline_info.json not pulled (rc=23).\n"
    assert classify_log_line(errors) == "INFO"
    assert classify_log_line(errors) == classify_log_line(info)


def test_an_explicit_tag_outranks_the_keyword_guess():
    from services.pipeline_runner import classify_log_line

    # The tag wins even though the text carries a red-flag keyword...
    assert classify_log_line("[info] no errors were recorded\n") == "INFO"
    assert classify_log_line("[warn] the CSV pull failed; retrying\n") == "WARNING"
    # ...and a tag that says error still means error.
    assert classify_log_line("[error] something went wrong\n") == "ERROR"


def test_a_real_failure_is_still_red():
    """The guard must not have bought its calm by going quiet on failures."""
    from services.pipeline_runner import classify_log_line

    for line in ("FAILED well A01: killed by signal 9\n",
                 "Traceback (most recent call last):\n",
                 "RuntimeError: 15334400 requested and 0 written\n",
                 "2026-08-01 10:00:00  ERROR     could not write A01\n"):
        assert classify_log_line(line) == "ERROR", line


def test_a_clean_runs_summary_is_still_not_red():
    """The lesson this classifier already carried: those are column labels."""
    from services.pipeline_runner import classify_log_line

    assert classify_log_line(
        "2026-08-01 10:00:00  INFO      wells failed (no output) : 0\n") == "INFO"
