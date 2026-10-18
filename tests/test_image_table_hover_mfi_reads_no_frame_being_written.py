"""The Image Table's hover MFI lookup never reads a frame another thread is writing.

The lookup runs on a background thread (``_hover_mfi``). The row cache's held
frames are rewritten in place — by the cell gating pass on its ``QThread``, by
the Cell Gating panel's jobs on theirs (``review.core.progress_jobs``), by
manual curation on the GUI thread — and the lookup used to read those held
frames from its thread (``narrow_rows`` hands a held well back as held) and
write the shared image cache. A failure there was swallowed and returned
``None``, the same value background reads as, so a torn read showed up as a
cell with no MFI and nothing said otherwise.

What holds it now, each driven through the real ``cell_mfi`` on a real
``AppState`` / ``ViewerContext``:

* the lookup works on a copy of the held frame taken on the GUI thread, so a
  writer that starts while the lookup is in flight — the interleaving is made
  deterministic with events around the real runner thread — cannot tear what
  it reads;
* the thread writes its mask scan into a private copy of the image cache, never
  the shared one the UI-thread Generate uses;
* while a panel job or a gating pass outside the runner is writing, no lookup is
  started and no copy taken; the readout says it is waiting, and the next hover
  after the writer ends asks again;
* a lookup that fails says "MFI: unavailable" instead of reading as background.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from review.core import progress_jobs
from review.features.image_browser.image_table import _hover_mfi, image_compose

WELL = "A01"
#: Two cells in one frame, nucleus 3 and nucleus 4, both included.
MASK = np.array([[0, 3], [4, 4]], dtype=np.int32)
AT_3, AT_4 = (1, 0), (0, 1)          # (img_x, img_y) of each nucleus in MASK


def _held() -> pd.DataFrame:
    return pd.DataFrame({
        "well": [WELL] * 2,
        "fov": ["1", "1"],
        "timepoint": ["0", "0"],
        "nucleus_id": [3, 4],
        "gfp_mean_intensity": [30.0, 40.0],
        "Included": [1, 1],
    })


@pytest.fixture
def ctx(tmp_path):
    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.event_bus import EventBus
    from review.core.widget_registry import WidgetRegistry
    from review.data.well_repository import WellRepository
    from review.features.registry import attach_feature_state, build_feature_registry
    from review.viewer_context import ViewerContext

    build_feature_registry()
    state = AppState()
    attach_feature_state(state)
    state.dataset.data_dir = tmp_path
    state.dataset.well_paths = {WELL: Path(tmp_path) / f"{WELL}.csv"}
    state.dataset.fov_integrity = {}
    state.dataset.cache[WELL] = _held()
    store: dict = {}
    c = ViewerContext(state=state, bus=EventBus(), widgets=WidgetRegistry(None, store=store),
                      runtime=AppRuntime(None, store=store))
    state.repo = WellRepository(c)
    return c


@pytest.fixture(autouse=True)
def _quiet_runner(monkeypatch):
    """No Qt notifier (nothing here has an event loop to deliver it) and a fresh
    runner, shut down at the end so no daemon thread outlives the test."""
    monkeypatch.setattr(_hover_mfi, "_notifier", lambda: None)
    monkeypatch.setattr(_hover_mfi, "_RUNNER", None)
    yield
    _hover_mfi.shutdown()


def _wait_landed(label) -> None:
    deadline = time.monotonic() + 10
    while not label._mfi_rec["done"]:
        assert time.monotonic() < deadline, "the hover lookup never landed"
        time.sleep(0.005)


def _hover(ctx, label, at):
    return _hover_mfi.cell_mfi(ctx, label, "gfp", WELL, "0", "1", *at)


def test_a_writer_starting_mid_lookup_cannot_tear_what_the_lookup_reads(ctx, monkeypatch):
    """The real runner thread is held inside the mask read; meanwhile a writer
    rewrites ``Included`` in place, one row at a time, and the lookup is let go
    with that rewrite half done (nucleus 4 excluded, nucleus 3 not yet). A
    lookup reading the held frame sees the half-written frame — nucleus 4 drops
    out of the mask and reads as background, nucleus 3 keeps its value: a state
    the frame was never in. Working on a copy taken at the hover, both cells
    read as they stood when the cursor arrived."""
    reading, go = threading.Event(), threading.Event()

    def _paused_mask(_ctx, _cache, _well, _tp, _fov):
        reading.set()
        assert go.wait(10), "the test never released the lookup"
        return MASK.copy()

    monkeypatch.setattr(image_compose, "load_mask_array", _paused_mask)
    cell_3 = SimpleNamespace()
    try:
        assert _hover(ctx, cell_3, AT_3) is _hover_mfi.MFI_PENDING
        assert reading.wait(10), "the lookup never started"
        held = ctx.state.dataset.cache[WELL]
        held.loc[held["nucleus_id"] == 4, "Included"] = 0      # the writer, half way
    finally:
        go.set()
    _wait_landed(cell_3)
    held.loc[held["nucleus_id"] == 3, "Included"] = 0          # the writer finishes
    # One label holds one frame's mask, so both nuclei are read through it.
    assert _hover(ctx, cell_3, AT_3) == 30.0
    assert _hover(ctx, cell_3, AT_4) == 40.0, (
        "the lookup read the held frame while it was being rewritten: nucleus 4 "
        "was dropped from the mask by a half-done rewrite and reads as background")


def test_the_lookup_thread_never_writes_the_shared_image_cache(ctx, monkeypatch):
    def _scan_into(_ctx, cache, _well, _tp, _fov):
        cache[("probe", "__mask__", "raw")] = "written by the hover thread"
        return MASK.copy()

    monkeypatch.setattr(image_compose, "load_mask_array", _scan_into)
    label = SimpleNamespace()
    _hover(ctx, label, AT_3)
    _wait_landed(label)
    shared = ctx.state.image_table.image_cache
    assert ("probe", "__mask__", "raw") not in shared, (
        "the hover thread wrote into the image cache the UI-thread Generate uses")
    assert _hover(ctx, label, AT_3) == 30.0


def _record_submits(monkeypatch) -> list:
    submitted: list = []
    monkeypatch.setattr(_hover_mfi, "_submit_mfi",
                        lambda job, on_dropped=None: submitted.append(job))
    return submitted


def test_no_lookup_starts_while_a_panel_job_is_writing(ctx, monkeypatch):
    submitted = _record_submits(monkeypatch)
    runner = progress_jobs._runner(ctx)
    runner.current = object()                 # a re-projection is in flight
    label = SimpleNamespace()
    got = _hover(ctx, label, AT_3)
    assert submitted == [], "a hover lookup started while a panel job rewrites the frames"
    assert got is _hover_mfi.MFI_WAITING
    assert _hover_mfi.mfi_suffix(got) == "  MFI: waiting for the cell update…"
    runner.current = None                     # the job ended
    assert _hover(ctx, label, AT_3) is _hover_mfi.MFI_PENDING
    assert len(submitted) == 1, "the next hover after the job did not ask again"


def test_no_lookup_starts_while_a_load_s_gating_pass_is_writing(ctx, monkeypatch):
    """Through the real start of a gating pass outside the runner (the route a
    load and a Scatter Plot gate take), with the ``QThread`` replaced by a stub
    that reports itself running until told otherwise."""
    from review.features.home.cell_gating import controller as cg

    class _Signal:
        def connect(self, _slot) -> None:
            return None

    class _StubPass:
        def __init__(self, _ctx) -> None:
            self.progress = self.finished_ok = self.failed = _Signal()
            self.ended = False
            self.running = False

        def start(self) -> None:
            self.running = True

        def isRunning(self) -> bool:
            return self.running

    monkeypatch.setattr(cg, "GatingWorker", _StubPass)
    monkeypatch.setattr(cg, "cell_gating_refresh_included_count", lambda *a, **k: None)
    submitted = _record_submits(monkeypatch)
    worker = cg.cell_gating_start_gating_worker(ctx)
    label = SimpleNamespace()
    _hover(ctx, label, AT_3)
    assert submitted == [], "a hover lookup started while a load's gating pass rewrites the frames"
    worker.ended = True                       # the pass stopped writing
    assert _hover(ctx, label, AT_3) is _hover_mfi.MFI_PENDING
    assert len(submitted) == 1


def test_a_failed_lookup_says_unavailable_not_background(ctx, monkeypatch):
    def _broken(*_a):
        raise OSError("the share went away")

    monkeypatch.setattr(image_compose, "load_mask_array", _broken)
    label = SimpleNamespace()
    _hover(ctx, label, AT_3)
    _wait_landed(label)
    got = _hover(ctx, label, AT_3)
    assert got is not None, "a failed lookup reads as background (no MFI suffix at all)"
    assert _hover_mfi.mfi_suffix(got) == "  MFI: unavailable"
    assert _hover_mfi.mfi_suffix(None) == ""
    assert _hover_mfi.mfi_suffix(12.34) == "  MFI:12.3"


@pytest.mark.parametrize("layer", ["Gate_Pass", "QC_Pass", "FOV_Pass", "Border_Pass"])
def test_the_copy_carries_every_verdict_so_a_dropped_cell_stays_dropped(ctx, monkeypatch, layer):
    """A copy without one verdict column would fail open — that verdict reads
    as all-pass — and quote an MFI for a cell every plot has dropped. Each
    layer excludes nucleus 3 here, so a copy that lost it reads 30.0."""
    held = ctx.state.dataset.cache[WELL]
    held[layer] = [0, 1]
    monkeypatch.setattr(image_compose, "load_mask_array", lambda *_a: MASK.copy())
    label = SimpleNamespace()
    _hover(ctx, label, AT_3)
    _wait_landed(label)
    assert _hover(ctx, label, AT_3) is None, f"the copy lost {layer}: a dropped cell quotes an MFI"
    assert _hover(ctx, label, AT_4) == 40.0
