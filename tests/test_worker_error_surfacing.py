"""Audit E6/E7/E12 — background failures must surface, not vanish.

* E6: PipelineRunner._run_pipeline_thread must emit an ``error`` (not just
  ``finished``) when the setup phase raises.
* E7: GatingWorker must emit ``failed`` instead of dying silently.
* E12: Fetcher._loop must survive a per-well fetch crash and keep going.
"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

import services.remote_runner as rr
from services.pipeline_runner import PipelineRunner
from well_viewer.features.grouping.cell_gating_tab_view import GatingWorker


# ── E6 ────────────────────────────────────────────────────────────────────
def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_pipeline_setup_crash_emits_error_and_finished():
    pr = PipelineRunner()

    def _bad_resolve(opts, log_q, proc_hook=None):
        raise RuntimeError("grouping boom")

    pr._run_pipeline_thread(Path("/pipe.py"), {}, _bad_resolve)

    events = _drain(pr.log_q)
    kinds = [k for (k, _payload) in events]
    assert "error" in kinds, f"setup crash must surface an error; got {kinds}"
    assert ("finished", None) in events
    # the error message carries the cause
    assert any(k == "error" and "boom" in str(v) for (k, v) in events)


# ── E7 ────────────────────────────────────────────────────────────────────
def test_gating_worker_emits_failed_on_crash(qapp, monkeypatch):
    # The worker's first step is get_all_fluor_gates(ctx); force it to raise so
    # _run_inner crashes and must route the error through the ``failed`` signal.
    import well_viewer.features.grouping.cell_gating_tab_view as cg

    def _boom(ctx):
        raise RuntimeError("gate boom")

    monkeypatch.setattr(cg, "get_all_fluor_gates", _boom)
    worker = GatingWorker(object())
    seen = []
    worker.failed.connect(lambda msg: seen.append(msg))
    worker.finished_ok.connect(lambda: seen.append("OK"))

    worker.run()  # direct, synchronous — no thread spin-up

    assert seen and "gate boom" in seen[0]
    assert "OK" not in seen


# ── E12 ───────────────────────────────────────────────────────────────────
def test_fetcher_loop_survives_one_bad_well():
    f = object.__new__(rr.Fetcher)
    f._stop = threading.Event()
    f._pending = queue.Queue()
    logs: list[str] = []
    f._log = logs.append

    processed: list[str] = []

    def _bad_fetch(item):
        processed.append(item)
        raise RuntimeError("rsync boom")

    f._fetch_one = _bad_fetch
    f._pending.put("A01")
    f._pending.put("A02")
    f._pending.put(None)  # sentinel → clean exit

    f._loop()  # must NOT propagate

    assert processed == ["A01", "A02"], "a bad well must not skip the rest"
    assert any("error" in m.lower() for m in logs)
