"""Audit E16 — a failing debounced autosave must surface, not vanish.

The persistence registry's ``_flush`` runs a domain's ``save`` from a
``QTimer.singleShot`` callback. A raise there (e.g. a disk-full write in
``view_state`` / ``sample_definitions``) has no caller to catch it, so the
user's edits would silently fail to persist. ``_flush`` now logs the failure
instead of letting it escape into the Qt event loop.
"""
from __future__ import annotations

import logging

import pytest

from review.persistence.registry import PersistenceRegistry


class _FakeApp:
    pass


def test_flush_logs_and_swallows_a_raising_save(caplog):
    reg = PersistenceRegistry(_FakeApp())

    def _boom(_app):
        raise OSError("disk full")

    reg.register("view_state", save=_boom)
    reg._pending.add("view_state")

    with caplog.at_level(logging.ERROR, logger="review"):
        # Must NOT raise — the failure is logged, not propagated.
        reg._flush("view_state")

    assert "view_state" in caplog.text
    assert any(r.levelno >= logging.ERROR for r in caplog.records)
    # Pending set is cleared even though the save failed.
    assert "view_state" not in reg._pending


def test_immediate_save_still_propagates(caplog):
    # The explicit (non-debounced) save path must keep raising so callers that
    # show a user-facing dialog still see the failure.
    reg = PersistenceRegistry(_FakeApp())
    reg.register("view_state", save=lambda _app: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        reg.save("view_state")
