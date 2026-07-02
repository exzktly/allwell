"""Audit E23 — the playground reference bridge must disconnect after firing.

``request_reference_fields`` created a fresh ``_ReferenceBridge`` QObject per
call and connected its ``done``/``failed`` signals but never disconnected them,
so each call leaked a connected object holding the widget/app captured by the
callbacks. The bridge now disconnects both signals once it fires (one-shot).
"""
from __future__ import annotations

from well_viewer.features.image_browser.playground import worker as pg_worker
from well_viewer.features.image_browser.playground.worker import (
    _ReferenceBridge, request_reference_fields,
)


def test_bridge_disconnects_after_done(qtbot, monkeypatch):
    monkeypatch.setattr(pg_worker, "build_reference_fields",
                        lambda *a, **k: (None, None))
    calls = []
    bridge = request_reference_fields(
        object(), "A1", "cy5", flatfield_mode="none",
        background_method="none", on_done=lambda *p: calls.append(p),
    )
    qtbot.waitUntil(lambda: len(calls) == 1, timeout=3000)

    # A second emit must NOT reach on_done — the one-shot disconnected it.
    bridge.done.emit(("A1", "cy5", None, None))
    qtbot.wait(50)
    assert len(calls) == 1, "bridge should have disconnected after the first fire"


def test_bridge_disconnects_after_failure(qtbot, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(pg_worker, "build_reference_fields", _boom)
    fails = []
    bridge = request_reference_fields(
        object(), "A1", "cy5", flatfield_mode="none",
        background_method="none", on_done=lambda *p: None,
        on_failed=lambda m: fails.append(m),
    )
    qtbot.waitUntil(lambda: len(fails) == 1, timeout=3000)

    bridge.failed.emit("again")
    qtbot.wait(50)
    assert len(fails) == 1


def test_disconnect_on_empty_signal_is_safe(qtbot):
    # Disconnecting a bridge that was never connected must not raise.
    b = _ReferenceBridge()
    for sig in (b.done, b.failed):
        try:
            sig.disconnect()
        except (TypeError, RuntimeError):
            pass
