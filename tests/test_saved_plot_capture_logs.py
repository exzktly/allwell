"""Audit E10 — saved-plot capture must not silently lose the user's group defs
/ stats config. On any capture error it returns None (skips that block) but now
logs it, instead of vanishing with a bare `except: return None`.
"""
from __future__ import annotations

import logging
import types

from review.features.saved_plots import collect


def test_capture_selections_logs_on_failure(caplog):
    class _Boom:
        @property
        def selections(self):
            raise RuntimeError("boom")

    ctx = types.SimpleNamespace(state=types.SimpleNamespace(selection=_Boom()))
    with caplog.at_level(logging.ERROR,
                         logger="review.features.saved_plots.collect"):
        assert collect._capture_selections(ctx) is None
    assert any("selections" in r.getMessage() for r in caplog.records), \
        "a failed selection capture must be logged, not swallowed"


def test_capture_selections_returns_none_when_empty():
    ctx = types.SimpleNamespace(
        state=types.SimpleNamespace(
            selection=types.SimpleNamespace(selections=[])))
    assert collect._capture_selections(ctx) is None
