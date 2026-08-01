"""Audit A10 — the shared per-well worker-pool driver.

``process_well_zips`` and ``process_well_folders`` hand-rolled the identical
skip / dispatch / collect / failure-handling scaffolding around a
``ProcessPoolExecutor``. Both now delegate to ``_run_well_worker_pool`` with
mode-specific callables. These tests exercise that shared scaffolding with a
fake executor (no real StarDist workers), covering skip-complete, dispatch,
force-override, worker-exception surfacing, and the early-return path.
"""
from __future__ import annotations

from pathlib import Path

import process_microscopy as pm


class _FakeFuture:
    """Enough of ``concurrent.futures.Future`` for the pool driver.

    ``cancel`` / ``cancelled`` are part of that surface: a suspend cancels the
    wells not yet started, and the driver skips any future that comes back
    cancelled rather than calling ``result()`` on it (which would raise
    ``CancelledError`` and be reported as a worker failure).
    """

    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self._cancelled = False

    def result(self):
        if self._exc is not None:
            raise self._exc
        return self._result

    def cancel(self) -> bool:
        self._cancelled = True
        return True

    def cancelled(self) -> bool:
        return self._cancelled


class _FakeExecutor:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch(monkeypatch):
    monkeypatch.setattr(pm, "ProcessPoolExecutor", _FakeExecutor)
    monkeypatch.setattr(pm, "as_completed", lambda d: list(d))
    monkeypatch.setattr(pm, "_ensure_stardist_runtime_deps", lambda: None)


def test_skips_complete_dispatches_rest_and_surfaces_failure(tmp_path, monkeypatch):
    _patch(monkeypatch)
    reported = []
    monkeypatch.setattr(pm, "_report_worker_failure",
                        lambda well, out, exc: reported.append(well))

    submitted, swept = [], []

    def submit_task(executor, well, path):
        submitted.append(well)
        if well == "A2":
            return _FakeFuture(exc=RuntimeError("boom"))
        return _FakeFuture(([{"nuc": well}], []))

    pm._run_well_worker_pool(
        items=[("A1", Path("a1")), ("A2", Path("a2")), ("A3", Path("a3"))],
        output_dir=tmp_path, workers=2, force=False, incremental=False,
        force_cpu=False, threads_per_worker=0, shared_kwargs_template={},
        mode_label="Test",
        stale_sweep=lambda out: swept.append(out),
        output_exists=lambda well: well == "A3",   # A3 already complete → skipped
        submit_task=submit_task,
    )

    assert swept == [tmp_path]                    # stale sweep ran once, on output_dir
    assert set(submitted) == {"A1", "A2"}         # A3 skipped
    assert reported == ["A2"]                     # A2's worker exception surfaced


def test_returns_failed_wells_for_exit_code(tmp_path, monkeypatch):
    # audit E5: the driver returns the failed-well list so main() can exit
    # non-zero. Here A2's worker raises and A3 reports a per-well failure; both
    # must appear in the returned list.
    _patch(monkeypatch)
    monkeypatch.setattr(pm, "_report_worker_failure", lambda well, out, exc: None)

    def submit_task(executor, well, path):
        if well == "A2":
            return _FakeFuture(exc=RuntimeError("boom"))       # driver-level failure
        if well == "A3":
            return _FakeFuture(([], ["A3"]))                   # worker-reported failure
        return _FakeFuture(([{"nuc": well}], []))

    failed = pm._run_well_worker_pool(
        items=[("A1", Path("a1")), ("A2", Path("a2")), ("A3", Path("a3"))],
        output_dir=tmp_path, workers=2, force=False, incremental=False,
        force_cpu=False, threads_per_worker=0, shared_kwargs_template={},
        mode_label="Test",
        stale_sweep=lambda out: None,
        output_exists=lambda well: False,
        submit_task=submit_task,
    )
    assert set(failed) == {"A2", "A3"}


def test_partial_fov_failures_count_wells_not_fov_names(tmp_path, monkeypatch):
    """Regression: a well that finishes with per-FOV-group failures writes a
    partial CSV/zip (so it looks complete on the plate map) and returns the
    failed FOV-group *names* in its ``failed`` list. Those names must NOT be
    counted as separate failed wells — the driver returns the affected WELL
    label once, not one entry per failed FOV-group. Previously 12 failed
    FOV-groups across 2 wells were reported as "12 wells failed"."""
    _patch(monkeypatch)
    monkeypatch.setattr(pm, "_report_worker_failure", lambda well, out, exc: None)

    def submit_task(executor, well, path):
        if well == "A1":
            # 3 FOV-groups failed inside an otherwise-complete well.
            return _FakeFuture(([{"nuc": 1}], ["A1_f01.tif", "A1_f02.tif", "A1_f03.tif"]))
        if well == "A2":
            return _FakeFuture(exc=RuntimeError("boom"))   # failed entirely
        return _FakeFuture(([{"nuc": 2}], []))             # A3 clean

    failed = pm._run_well_worker_pool(
        items=[("A1", Path("a1")), ("A2", Path("a2")), ("A3", Path("a3"))],
        output_dir=tmp_path, workers=3, force=False, incremental=False,
        force_cpu=False, threads_per_worker=0, shared_kwargs_template={},
        mode_label="Test",
        stale_sweep=lambda out: None,
        output_exists=lambda well: False,
        submit_task=submit_task,
    )

    # A1 (partial) contributes ONE well label despite 3 failed FOV-groups; A2
    # (entire failure) contributes one; A3 (clean) contributes none.
    assert sorted(failed) == ["A1", "A2"]
    assert failed.count("A1") == 1


def test_parent_counts_nuclei_without_retaining_records(tmp_path, monkeypatch, caplog):
    """Memory-leak regression: the driver must NOT accumulate every well's
    per-cell records in the parent (that held the whole plate's cell dicts in
    the long-lived parent just to len() them — the tens-of-GB leak). It keeps a
    running integer instead; the summary line reports the correct total nuclei
    across wells, proving the records themselves are never needed in the parent."""
    import logging
    _patch(monkeypatch)

    def submit_task(executor, well, path):
        # Distinct record counts per well: 2 + 3 = 5 total.
        n = {"A1": 2, "A2": 3}[well]
        return _FakeFuture(([{"nuc": i} for i in range(n)], []))

    with caplog.at_level(logging.INFO, logger="process_microscopy"):
        pm._run_well_worker_pool(
            items=[("A1", Path("a1")), ("A2", Path("a2"))],
            output_dir=tmp_path, workers=2, force=False, incremental=False,
            force_cpu=False, threads_per_worker=0, shared_kwargs_template={},
            mode_label="Test",
            stale_sweep=lambda out: None,
            output_exists=lambda well: False,
            submit_task=submit_task,
        )

    summary = next(m for m in caplog.messages if "nuclei :" in m)
    assert "nuclei : 5" in summary


def test_all_complete_returns_empty_failed_list(tmp_path, monkeypatch):
    # The early "all complete" path returns [] (no failures) so main() exits 0.
    _patch(monkeypatch)
    failed = pm._run_well_worker_pool(
        items=[("A1", Path("a1"))],
        output_dir=tmp_path, workers=1, force=False, incremental=False,
        force_cpu=False, threads_per_worker=0, shared_kwargs_template={},
        mode_label="Test",
        stale_sweep=lambda out: None,
        output_exists=lambda well: True,
        submit_task=lambda e, w, p: None,
    )
    assert failed == []


def test_all_complete_returns_before_stardist_check(tmp_path, monkeypatch):
    _patch(monkeypatch)
    ensure = []
    monkeypatch.setattr(pm, "_ensure_stardist_runtime_deps",
                        lambda: ensure.append(1))
    submitted = []
    pm._run_well_worker_pool(
        items=[("A1", Path("a1"))],
        output_dir=tmp_path, workers=1, force=False, incremental=False,
        force_cpu=False, threads_per_worker=0, shared_kwargs_template={},
        mode_label="Test",
        stale_sweep=lambda out: None,
        output_exists=lambda well: True,          # every well already complete
        submit_task=lambda e, w, p: submitted.append(w),
    )
    assert submitted == []                        # nothing dispatched
    assert ensure == []                           # early-returned before the check


def test_force_bypasses_skip(tmp_path, monkeypatch):
    _patch(monkeypatch)
    submitted = []
    pm._run_well_worker_pool(
        items=[("A1", Path("a1"))],
        output_dir=tmp_path, workers=1, force=True, incremental=False,
        force_cpu=False, threads_per_worker=0, shared_kwargs_template={},
        mode_label="Test",
        stale_sweep=lambda out: None,
        output_exists=lambda well: True,          # would skip, but force overrides
        submit_task=lambda e, w, p: (submitted.append(w) or _FakeFuture(([], []))),
    )
    assert submitted == ["A1"]


def test_incremental_routes_every_well(tmp_path, monkeypatch):
    _patch(monkeypatch)
    submitted = []
    pm._run_well_worker_pool(
        items=[("A1", Path("a1")), ("A2", Path("a2"))],
        output_dir=tmp_path, workers=2, force=False, incremental=True,
        force_cpu=False, threads_per_worker=0, shared_kwargs_template={},
        mode_label="Test",
        stale_sweep=lambda out: None,
        output_exists=lambda well: True,          # complete, but incremental routes all
        submit_task=lambda e, w, p: (submitted.append(w) or _FakeFuture(([], []))),
    )
    assert set(submitted) == {"A1", "A2"}
