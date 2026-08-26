"""Audit L3/L4 — the Review pane is a child widget (its closeEvent never
fires), so AllWellApp.closeEvent calls teardown_review_pane on quit: it signals
the smFISH worker to abort (L3) and disconnects the app-global focusChanged
watcher (L4).
"""
from __future__ import annotations

import threading
import types

from well_viewer.core.app_runtime import AppRuntime
from well_viewer.runtime_app import teardown_review_pane


def test_teardown_sets_smfish_cancel_event():
    ev = threading.Event()
    rt = AppRuntime()
    rt.smfish.cancel_event = ev
    app = types.SimpleNamespace(_ctx=types.SimpleNamespace(runtime=rt))
    teardown_review_pane(app)
    assert ev.is_set()


def test_teardown_is_none_safe():
    teardown_review_pane(None)  # no raise


def test_teardown_tolerates_missing_attrs():
    teardown_review_pane(types.SimpleNamespace())  # no _smfish_cancel_event / filter


def test_teardown_cancels_a_running_cell_export():
    """A per-cell export streams into a temp file beside the user's chosen
    destination and only renames it at the end, so a writer still running when
    the pane goes away leaves that hidden temp behind — in their folder, for
    good, because a daemon thread killed at interpreter exit never runs the
    ``finally`` that removes it.

    Cancelling has to put the sentinel as well as setting the event: a writer
    already blocked in ``Queue.get`` is not woken by the event alone.
    """
    import queue

    from well_viewer.features.export.worker import DONE

    from well_viewer.features.export.service import cancel_cell_export

    ev = threading.Event()
    chunks: queue.Queue = queue.Queue(maxsize=2)
    rt = AppRuntime()
    ctx = types.SimpleNamespace(runtime=rt)
    rt.export.cell_cancel = ev
    rt.export.cell_queue = chunks
    # Registered by ``export_cell_data`` while a run is in flight; teardown
    # reaches it through the runtime rather than importing the feature.
    rt.export.cell_stop = lambda: cancel_cell_export(ctx)
    app = types.SimpleNamespace(_ctx=ctx)

    teardown_review_pane(app)

    assert ev.is_set(), "the export's cancel event was never set"
    assert chunks.get_nowait() is DONE, (
        "the sentinel is what wakes a writer parked on Queue.get; the event "
        "alone leaves it blocked until the process dies"
    )
    assert rt.export.cell_cancel is None
