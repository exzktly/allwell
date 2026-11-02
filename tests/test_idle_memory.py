"""Review gives its dataset caches back once the app has sat in the background.

See ``review/core/idle_memory.py``: on Windows, memory a background app holds
and does not touch goes to the page file, and Review held up to about half of
physical memory in caches for as long as a dataset stayed open.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from review.core import idle_memory, load_controller
from review.core.app_state import AppState
from review.data import image_discovery


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _filled_ctx():
    state = AppState()
    state.dataset.cache["A1"] = pd.DataFrame({"v": np.arange(1000.0)})
    state.dataset.cache["B2"] = pd.DataFrame({"v": np.arange(2000.0)})
    state.stats_cache.populations["k"] = np.zeros(500)
    inventory = {("A1", "gfp", 0): "a ref"}
    state.image_table = SimpleNamespace(image_cache={
        ("A1", "gfp", "raw"): inventory,                 # a file inventory: kept
        ("A1", "phase", 0.0): np.zeros((10, 10), np.float32),   # a nanowell frame: dropped
        ("nanowell-frame-of", "A1", "phase", 0.0): object(),
    })
    image_discovery._DECODE_CACHE.invalidate()
    image_discovery._DECODE_CACHE.put(("f",), np.zeros(300))
    return SimpleNamespace(state=state, runtime=SimpleNamespace(cell_gating=SimpleNamespace(worker=None)))


def test_a_release_empties_every_dataset_cache_and_counts_what_it_freed():
    ctx = _filled_ctx()
    rows = ctx.state.dataset.cache.held_bytes
    freed = idle_memory.release_idle_memory(ctx)
    assert freed == rows + 500 * 8 + 300 * 8 + 10 * 10 * 4
    assert len(ctx.state.dataset.cache) == 0
    assert ctx.state.dataset.cache.held_bytes == 0
    assert len(ctx.state.stats_cache.populations) == 0
    assert image_discovery._DECODE_CACHE.get(("f",)) is None
    assert list(ctx.state.image_table.image_cache) == [("A1", "gfp", "raw")]


def test_a_release_is_not_counted_as_the_plate_not_fitting():
    """The eviction count drives the 'plate does not fit, set the budget' message; idling is not that."""
    ctx = _filled_ctx()
    idle_memory.release_idle_memory(ctx)
    assert ctx.state.dataset.cache.evictions == 0
    assert "evicted" not in ctx.state.dataset.cache.describe()


def test_a_well_being_worked_on_is_kept():
    ctx = _filled_ctx()
    with ctx.state.dataset.cache.pinned("A1"):
        idle_memory.release_idle_memory(ctx)
        assert list(ctx.state.dataset.cache) == ["A1"]


def test_the_timer_runs_only_while_the_app_is_in_the_background(qapp, monkeypatch):
    ctx = _filled_ctx()
    releaser = idle_memory._IdleReleaser(ctx, qapp, 5)
    try:
        assert releaser.timer.interval() == 5 * 60_000
        releaser._on_state(Qt.ApplicationInactive)
        assert releaser.timer.isActive()
        releaser._on_state(Qt.ApplicationActive)
        assert not releaser.timer.isActive()
    finally:
        releaser.timer.stop()
        releaser.deleteLater()


def test_a_release_waits_for_a_load_or_gating_pass(qapp):
    ctx = _filled_ctx()
    ctx.state.plot.held_for = "gating"
    releaser = idle_memory._IdleReleaser(ctx, qapp, 5)
    try:
        releaser._fire()
        assert len(ctx.state.dataset.cache) == 2      # nothing released under the pass
        assert releaser.timer.isActive()              # asked again later
        ctx.state.plot.held_for = ""
        releaser._fire()
        assert len(ctx.state.dataset.cache) == 0
    finally:
        releaser.timer.stop()
        releaser.deleteLater()


@pytest.mark.parametrize("raw, minutes", [("", 5.0), ("0", 0.0), ("12.5", 12.5), ("soon", 5.0), ("-3", 0.0)])
def test_the_minutes_come_from_the_environment(monkeypatch, raw, minutes):
    monkeypatch.setenv(idle_memory.IDLE_ENV, raw)
    assert idle_memory.idle_minutes() == minutes


def test_zero_minutes_turns_it_off(qapp, monkeypatch):
    monkeypatch.setattr(idle_memory, "_installed", None)
    monkeypatch.setenv(idle_memory.IDLE_ENV, "0")
    idle_memory.install(_filled_ctx())
    assert idle_memory._installed is None


def test_every_finished_load_arms_it(monkeypatch):
    """The real caller: the load's last step, ``_say_loaded``, installs the release for its context."""
    armed = []
    monkeypatch.setattr(idle_memory, "install", armed.append)
    for name in ("_report_incomplete_wells", "_report_unusable_fov_labels", "_report_persistence_doc_state"):
        monkeypatch.setattr(load_controller, name, lambda ctx: None)
    ctx = _filled_ctx()
    app = SimpleNamespace(_set_status=lambda text: None)
    load_controller._say_loaded(ctx, app, 2, "plate", False)
    assert armed == [ctx]
