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

import process_microscopy as pm  # noqa: E402
from services.pipeline_runner import classify_log_line  # noqa: E402


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
    records, failed = pm._process_groups_in_worker(
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
    records, failed = pm._process_groups_in_worker(
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
    assert classify_log_line(line) == expected
