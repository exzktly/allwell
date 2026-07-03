"""Audit E8 / E18 — smFISH "Apply to All" robustness.

* E8: a crash in the worker must route through done_cb so the UI gets a terminal
  signal instead of hanging forever.
* E18: a second concurrent "Apply to All" is rejected (a double-click otherwise
  ran two workers over the same CSVs and clobbered the shared cancel event).
"""
from __future__ import annotations

import types
from pathlib import Path

from well_viewer.features.smfish import tab_view
from well_viewer.features.smfish.worker import apply_global_threshold_async


# ── E8 ──────────────────────────────────────────────────────────────────
def test_worker_crash_routes_to_done_cb():
    done: list[str] = []

    class _BadMap:
        def items(self):
            raise RuntimeError("boom")

    t = apply_global_threshold_async(
        out_dir=Path("/tmp"),
        well_to_zip=_BadMap(),          # .items() raises inside _run
        channel="ch",
        threshold=1.0,
        classifier=None,
        fov_tp_extractor=None,
        status_cb=lambda _m: None,
        done_cb=lambda m: done.append(m),
    )
    t.join(timeout=5)
    assert not t.is_alive()
    assert done and "failed" in done[0].lower()   # terminal signal fired


# ── E18 ─────────────────────────────────────────────────────────────────
def test_apply_to_all_rejects_a_reentrant_run(monkeypatch):
    statuses: list[str] = []
    launched = {"n": 0}
    monkeypatch.setattr(tab_view, "_smfish_set_status",
                        lambda _app, msg: statuses.append(msg))
    monkeypatch.setattr(tab_view, "apply_global_threshold_async",
                        lambda **_k: launched.__setitem__("n", launched["n"] + 1))

    app = types.SimpleNamespace(_smfish_out_dir="x", _smfish_apply_running=True)
    tab_view._smfish_apply_to_all(app)

    assert launched["n"] == 0, "a second concurrent run must not launch a worker"
    assert any("already running" in s.lower() for s in statuses)
