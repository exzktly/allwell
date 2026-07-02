"""Audit L3/L4 — the Review pane is a child widget (its closeEvent never
fires), so AllWellApp.closeEvent calls teardown_review_pane on quit: it signals
the smFISH worker to abort (L3) and disconnects the app-global focusChanged
watcher (L4).
"""
from __future__ import annotations

import threading
import types

from well_viewer.runtime_app import teardown_review_pane


def test_teardown_sets_smfish_cancel_event():
    ev = threading.Event()
    app = types.SimpleNamespace(_smfish_cancel_event=ev, _split_focus_filter=None)
    teardown_review_pane(app)
    assert ev.is_set()


def test_teardown_is_none_safe():
    teardown_review_pane(None)  # no raise


def test_teardown_tolerates_missing_attrs():
    teardown_review_pane(types.SimpleNamespace())  # no _smfish_cancel_event / filter
