"""A batched resume starts the run at once; wells already here are painted, whatever the output mode.

The report, on a SLURM host: a resumed batched run downloaded everything the earlier attempt had left on the
server before it staged, uploaded or asked for a node; and wells fully downloaded before were not coloured.

* The pre-staging recovery (``services.remote_recover``) pulled every finished well, one after another, before
  anything else. A batched run takes those wells itself (``services.remote_batch_resume``): the Fetcher downloads
  them while the batches upload and compute. The recovery is skipped for it.
* The wells already complete here are painted by staging's "up to date" markers — which, behind that recovery,
  arrived only after it. And a well the end-of-run retry rescued reported its "zip" half only when
  ``<well>_out.zip`` existed locally, which in FOLDER mode (extracted) or CSV-only (not kept) it never does, so
  its download half never registered and it stayed uncoloured.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import services.pipeline_runner as pr
from services.remote_fetch_place import local_output_mb
from services.remote_run_state import OutputFilter


def _runner(lines):
    runner = pr.PipelineRunner.__new__(pr.PipelineRunner)
    runner._remote_runner, runner._output_filter = None, "full"
    runner._stop_requested = threading.Event()
    runner._enqueue = lines.append
    return runner


def test_a_batched_run_does_not_pull_the_earlier_attempt_s_wells_before_starting(monkeypatch):
    calls = []
    monkeypatch.setattr(pr, "recover_finished_outputs", lambda *a, **k: calls.append(a) or SimpleNamespace(
        ok=True, pulled=["A01"], still_missing=[]))
    runner = _runner([])
    argv = ["--input_dir", "in", "--output_dir", "out"]

    assert runner._recover_remote_outputs(Path("out"), argv, {"remote_batch_size": 12, "incremental": True}) is True
    assert runner._recover_remote_outputs(Path("out"), argv, {"remote_batch_size": 12}) is False
    assert calls == [], "a batched run downloaded the earlier attempt's wells before staging"

    assert runner._recover_remote_outputs(Path("out"), argv, {}), "a single-shot run still recovers"
    assert len(calls) == 1


def test_a_rescued_well_reports_its_download_in_every_output_mode(tmp_path):
    run = SimpleNamespace(local_output_dir=tmp_path, output_filter=OutputFilter.FULL)
    (tmp_path / "A01_out.zip").write_bytes(b"z" * 2048)
    (tmp_path / "A02_out").mkdir()
    (tmp_path / "A02_out" / "img.tif").write_bytes(b"x" * 4096)
    assert local_output_mb(run, "A01") == 2048 / (1024 * 1024)
    assert local_output_mb(run, "A02") == 4096 / (1024 * 1024), "FOLDER mode: the extracted output is the output"
    assert local_output_mb(run, "A03") is None, "nothing local is not a download"
    csv_only = SimpleNamespace(local_output_dir=tmp_path, output_filter=OutputFilter.CSV_ONLY)
    assert local_output_mb(csv_only, "A03") == 0.0, "CSV-only keeps no zip, and is still downloaded"


def test_the_retry_s_line_for_a_folder_mode_well_paints_it_downloaded(tmp_path):
    """Through the tracker that colours the plate: both halves, and the well reads downloaded."""
    tracker = pr.ProgressTracker()
    mb = local_output_mb(SimpleNamespace(local_output_dir=tmp_path, output_filter=OutputFilter.CSV_ONLY), "B03")
    events = tracker.parse(f"[remote] ↓ B03: {mb:.1f} MB zip in 1s (end-of-run retry)\n")
    events += tracker.parse("[remote] ↓ B03: 1 CSV file(s) in 1s (end-of-run retry)\n")
    assert ("well_stage", ("B03", "download")) in events
