"""A closed ``ReviewApp`` is freed: nothing outlives the window it belonged to.

Every closed window used to stay in memory — about 31 MB each with the mini
dataset loaded, measured by ``tests/perf/bench_app_retention.py`` (rows Q0–Q2 of
``_Docs/Markdowns/IMPLEMENTATION_REMEDIATION_PLAN.md``). Three roots held it,
and each test below pins one:

* **The Home notes box** was a Qt subclass declared inside its builder with
  methods closing over ``ctx``; its wrapper outlived the deleted widget and
  held the window. It is declared at module scope now, and
  ``tests/test_qt_subclasses_live_at_module_scope.py`` rules the shape out.
* **Four hidden per-tab channel combos** (Line, Bar, Heat Map, Distribution)
  were built with no parent and never placed in a layout, so they were
  top-level widgets that nothing ever deleted, each connected to a lambda over
  ``ctx``.
* **The background drain** of deferred tab builders re-armed a static
  ``QTimer.singleShot(0, callable)`` whose callable closes over the window; a
  window closed mid-drain stayed alive for the length of the chain, and every
  remaining tick ran a builder against deleted widgets.

These build the real app, so they are ``slow`` and run in the manually
dispatched slow lane, a file at a time. They deliberately avoid ``qtbot`` and
the ``viewer_app`` fixture: both hold the app, which is the thing being
measured.
"""

from __future__ import annotations

import gc
import logging
import weakref

import pytest

pytestmark = pytest.mark.slow


def _flush(qapp) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    for _ in range(5):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        qapp.processEvents()


def _close(qapp, app) -> weakref.ref:
    ref = weakref.ref(app)
    app.close()
    app.deleteLater()
    return ref


def test_a_closed_loaded_review_app_is_collected(qapp, mini_dataset):
    from review.runtime_app import ReviewApp

    app = ReviewApp()
    app._load_path(mini_dataset)
    qapp.processEvents()
    ref = _close(qapp, app)
    del app
    _flush(qapp)
    gc.collect()
    _flush(qapp)
    gc.collect()
    assert ref() is None, "a closed ReviewApp is still alive"


def test_closing_leaves_no_top_level_widget_behind(qapp, mini_dataset):
    """The hidden channel combos were parentless: four top-level widgets, plus
    their popup frames, survived every window. Asserted as the exact set of
    new top-level widgets (empty), so a single orphan names itself."""
    from PySide6.QtWidgets import QApplication

    from review.runtime_app import ReviewApp

    before = {id(w) for w in QApplication.topLevelWidgets()}
    app = ReviewApp()
    app._load_path(mini_dataset)
    qapp.processEvents()
    _close(qapp, app)
    del app
    _flush(qapp)
    gc.collect()
    left = [f"{type(w).__name__}({[w.itemText(i) for i in range(w.count())]})"
            if hasattr(w, "itemText") else type(w).__name__
            for w in QApplication.topLevelWidgets() if id(w) not in before]
    assert left == []


def test_a_window_closed_mid_drain_builds_nothing_after_it_is_gone(qapp, caplog):
    """Closed before its deferred tabs have drained, the window must take the
    drain with it: no builder may run against its deleted widgets, and the
    window must still be collected."""
    from review.runtime_app import ReviewApp

    caplog.set_level(logging.ERROR, logger="review.centre_view")
    app = ReviewApp()
    pending_before = len(app._centre_pending_builders)
    assert pending_before > 1, "the fixture needs tabs still waiting to drain"
    ref = _close(qapp, app)
    del app
    _flush(qapp)
    gc.collect()
    _flush(qapp)
    failed = [r.getMessage() for r in caplog.records
              if "Deferred build" in r.getMessage()]
    assert failed == []
    assert ref() is None
