"""Audit E23 — the playground worker bridges must disconnect after firing.

``request_reference_fields`` created a fresh ``_ReferenceBridge`` QObject per
call and connected its ``done``/``failed`` signals but never disconnected them,
so each call leaked a connected object holding the widget/app captured by the
callbacks. The bridge now disconnects both signals once it fires (one-shot).

``request_stardist_labels`` — the live-segmentation preview — carries the same
one-shot contract, and is pressed repeatedly by design (that is the whole point
of tuning thresholds against one frame), so it is covered here too.
"""
from __future__ import annotations

import numpy as np

from review.features.image_browser.playground import worker as pg_worker
from review.features.image_browser.playground.worker import (
    _ReferenceBridge, _SegmentationBridge, request_reference_fields,
    request_stardist_labels,
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
    for b in (_ReferenceBridge(), _SegmentationBridge()):
        for sig in (b.done, b.failed):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass


# ── live StarDist preview bridge ─────────────────────────────────────────────

def test_segmentation_bridge_delivers_labels_and_disconnects(qtbot, monkeypatch):
    labels = np.arange(16, dtype=np.int32).reshape(4, 4)
    monkeypatch.setattr(pg_worker, "stardist_labels_for_frame",
                        lambda arr, **kw: labels)
    got = []
    bridge = request_stardist_labels(np.zeros((4, 4), dtype=np.float32),
                                     on_done=got.append)
    qtbot.waitUntil(lambda: len(got) == 1, timeout=3000)
    assert got[0] is labels

    bridge.done.emit(labels)
    qtbot.wait(50)
    assert len(got) == 1, "segmentation bridge should be one-shot"


def test_segmentation_bridge_reports_the_failure_by_name(qtbot, monkeypatch):
    def _boom(arr, **kw):
        raise RuntimeError("StarDist is not installed in this environment")

    monkeypatch.setattr(pg_worker, "stardist_labels_for_frame", _boom)
    fails = []
    bridge = request_stardist_labels(np.zeros((4, 4), dtype=np.float32),
                                     on_done=lambda _l: None,
                                     on_failed=fails.append)
    qtbot.waitUntil(lambda: len(fails) == 1, timeout=3000)
    # The message reaches the GUI intact — a preview that cannot run has to say
    # why, not just fail to draw an overlay.
    assert "not installed" in fails[0]

    bridge.failed.emit("again")
    qtbot.wait(50)
    assert len(fails) == 1


def test_missing_deps_are_named_not_guessed(monkeypatch):
    """The dependency probe names what is absent, and never imports it."""
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert pg_worker._missing_stardist_deps() == ["stardist", "csbdeep", "tensorflow"]
