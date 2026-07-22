"""Application Log drawer: the ring buffer must capture the app's log records.

Regression for "nothing gets written to the application drawer": the ring
handler was attached to the *root* logger, but the whole app logs under the
``"well_viewer"`` logger, which sets ``propagate = False`` — so root never saw
the records and the drawer stayed empty.
"""
import logging

import pytest

pytest.importorskip("PySide6")

import allwell


def _fresh_app():
    # Bypass the heavy shell __init__; the ring-buffer wiring only needs these.
    app = allwell.AllWellApp.__new__(allwell.AllWellApp)
    app._log_ring = []
    app._log_drawer = None
    app._log_ring_attached = False
    return app


def _detach_ring_handlers():
    for name in ("well_viewer", ""):
        lg = logging.getLogger(name)
        lg.handlers = [h for h in lg.handlers
                       if type(h).__name__ != "_RingHandler"]


def test_ring_captures_well_viewer_records_despite_propagate_false():
    # Mimic the production config: well_viewer isolated from root.
    wv = logging.getLogger("well_viewer")
    prev_prop, prev_level = wv.propagate, wv.level
    wv.propagate = False
    app = _fresh_app()
    try:
        app._attach_log_ring_buffer()
        logging.getLogger("well_viewer.persistence.bar_groups").info("saved groups")
        logging.getLogger("well_viewer").warning("heads up")
        assert any("saved groups" in ln for ln in app._log_ring)
        assert any("heads up" in ln for ln in app._log_ring)
    finally:
        _detach_ring_handlers()
        wv.propagate, wv.level = prev_prop, prev_level


def test_attach_is_idempotent():
    app = _fresh_app()
    try:
        app._attach_log_ring_buffer()
        app._attach_log_ring_buffer()  # second call must not double-attach
        n = sum(type(h).__name__ == "_RingHandler"
                for h in logging.getLogger("well_viewer").handlers)
        assert n == 1
        assert app._log_ring_attached is True
    finally:
        _detach_ring_handlers()


def test_info_level_is_enabled_so_info_records_show():
    wv = logging.getLogger("well_viewer")
    prev_level = wv.level
    wv.setLevel(logging.WARNING)  # would otherwise drop info()
    app = _fresh_app()
    try:
        app._attach_log_ring_buffer()
        logging.getLogger("well_viewer").info("progress tick")
        assert any("progress tick" in ln for ln in app._log_ring)
    finally:
        _detach_ring_handlers()
        wv.setLevel(prev_level)
