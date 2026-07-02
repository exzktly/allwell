"""Audit L1 — quitting mid-run must stop the Analyze pipeline runner.

AnalyzeTab is a child widget, so its own closeEvent never fires; the app root's
closeEvent must stop the runner instead, or the local subprocess is orphaned and
a remote run leaks its SLURM allocation + staging + scratch. Tests the extracted
`_stop_analyze_runner` helper (the full AllWellApp.closeEvent needs a live Qt
top-level window and is exercised by the slow full-app suite).
"""
from __future__ import annotations

import types

from allwell import _stop_analyze_runner


def test_stops_a_running_runner():
    calls = []
    runner = types.SimpleNamespace(is_running=True, stop=lambda: calls.append("stop"))
    _stop_analyze_runner(types.SimpleNamespace(_runner=runner))
    assert calls == ["stop"]


def test_noop_when_not_running():
    calls = []
    runner = types.SimpleNamespace(is_running=False, stop=lambda: calls.append("stop"))
    _stop_analyze_runner(types.SimpleNamespace(_runner=runner))
    assert calls == []


def test_safe_when_pane_or_runner_absent():
    _stop_analyze_runner(None)                       # no analyze pane
    _stop_analyze_runner(types.SimpleNamespace())    # pane without a _runner
    # (no exception = pass)


def test_stop_failure_is_swallowed_not_propagated():
    def _boom():
        raise RuntimeError("stop failed")
    runner = types.SimpleNamespace(is_running=True, stop=_boom)
    # A failing stop() during shutdown must not block the app from closing.
    _stop_analyze_runner(types.SimpleNamespace(_runner=runner))
