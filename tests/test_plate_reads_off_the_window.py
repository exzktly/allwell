"""Views that read wells from disk do it on a worker, and say so, instead of freezing the window.

Reported: on a 60-well nanowell plate opened from a network share, going Sample Map → Distribution → Sample Map →
Cell Gating "hangs the app". Measured on a synthetic plate of that shape with the row cache too small to hold it,
each visit to Distribution read every well back from disk on the window's thread (8–10 s on a fast local disk), and
Cell Gating's first visit read every well again; from a share, minutes. ``review.core.background_read`` runs those
reads on a worker when the wells are not held, and draws only the newest result. These tests drive the real
Distribution builder in a minimal shell (``tests/_minimal_shell.py``) and read the drawn text back.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("pyarrow")

from review.core import background_read  # noqa: E402
from review.features.distribution.controller import READ_KEY, redraw_distribution  # noqa: E402
from tests.test_plots_draw_nanowell_well_medians import _footer, _shell  # noqa: E402


def _texts(fig) -> list[str]:
    return [t.get_text() for ax in fig.axes for t in ax.texts] + [t.get_text() for t in fig.texts]


@pytest.fixture
def dist(qtbot, tmp_path):
    ctx = _shell(qtbot, tmp_path, "review.features.distribution.tab_view.build_distribution_tab", "dist_bg")
    ctx.state.plot.observations = "cells"
    return ctx


def _reads_on(monkeypatch) -> list[str]:
    """The name of the thread every Parquet read ran on."""
    from review.data import parquet_cache

    threads: list[str] = []
    real = parquet_cache.read_cached

    def spy(path, columns=None):
        threads.append(threading.current_thread().name)
        return real(path, columns=columns)

    monkeypatch.setattr(parquet_cache, "read_cached", spy)
    return threads


def test_wells_not_in_memory_are_read_on_a_worker_and_the_plot_says_so(dist, qtbot, monkeypatch):
    dist.state.dataset.cache.clear()
    threads = _reads_on(monkeypatch)
    fig = dist.widgets.get("_distribution_fig")

    redraw_distribution(dist)

    assert "Reading 2 wells from disk…" in _texts(fig), _texts(fig)
    qtbot.waitUntil(lambda: not background_read.running(dist, READ_KEY), timeout=10_000)
    assert threads and threading.main_thread().name not in threads, threads
    assert "Reading 2 wells from disk…" not in _texts(fig)
    assert _footer(fig) == "Observations: cells · pooled from 3–4 occupied nanowells per well"


def test_a_plate_in_memory_draws_at_once_as_before(dist, monkeypatch):
    for well in ("A01", "B01"):
        assert dist.state.dataset.cache.get(well) is not None, "the fixture should hold its wells"
    redraw_distribution(dist)
    assert not background_read.running(dist, READ_KEY)
    assert _footer(dist.widgets.get("_distribution_fig")).startswith("Observations: cells")


def test_only_the_newest_read_is_drawn_and_a_superseded_one_is_released(dist, qtbot, monkeypatch):
    from review.features.distribution import controller

    released: list = []
    real_release = controller._release
    monkeypatch.setattr(controller, "_release", lambda collected: released.append(collected) or real_release(collected))
    drawn: list = []
    real_draw = controller._draw_distribution
    monkeypatch.setattr(controller, "_draw_distribution", lambda *a, **k: drawn.append(a[2]) or real_draw(*a, **k))

    dist.state.dataset.cache.clear()
    redraw_distribution(dist)
    dist.state.dataset.cache.clear()
    redraw_distribution(dist)
    qtbot.waitUntil(lambda: not background_read.running(dist, READ_KEY), timeout=10_000)
    qtbot.wait(200)                       # the superseded worker's result is delivered too, and must be dropped
    assert len(drawn) == 1, "a superseded read was drawn"
    assert len(released) == 1, "the superseded read was not released"


def test_a_result_for_another_dataset_is_dropped(dist, qtbot):
    got: list = []
    dropped: list = []
    gate = threading.Event()
    assert background_read.start(dist, "probe", lambda: (gate.wait(5), "old")[1], on_done=got.append,
                                 discard=dropped.append)
    dist.state.dataset.data_dir = dist.state.dataset.data_dir.parent / "another"
    gate.set()
    qtbot.waitUntil(lambda: bool(dropped), timeout=10_000)
    assert got == [] and dropped == ["old"]


def test_wells_not_held_names_only_the_wells_a_draw_would_read(dist):
    dist.state.dataset.cache.clear()
    assert background_read.wells_not_held(dist, ["A01", "B01", "A01", "Z99"]) == ["A01", "B01"]
