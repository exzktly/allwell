"""Audit E22 — a superseded Cell Browser loader must stop early.

Rapid paging / filter changes / a dataset swap used to leave a pile of
unbounded daemon threads all still reading the shared zipfile cache (which a
swap can invalidate mid-read). The loader now checks the latest generation
token before every compose and bails the instant it is superseded, and exposes
``cancel()`` for teardown.
"""
from __future__ import annotations

import time
import types

from well_viewer.features.image_browser.cell_browser import worker as cb_worker
from well_viewer.features.image_browser.cell_browser.worker import CellImageLoader


def _fake_app():
    # load_page reads app._app_state.image_view for the LUT/tint snapshot.
    return types.SimpleNamespace(_app_state=types.SimpleNamespace(image_view=None))


class _Desc:
    def __init__(self, i):
        self.nucleus_id = i
        self.well = "A1"
        self.tp_token = "0"
        self.fov = "f0"

    def __repr__(self):
        return f"_Desc({self.nucleus_id})"


def _wait_stable(seq, *, timeout=5.0):
    """Block until *seq* stops growing for a short quiet window."""
    deadline = time.time() + timeout
    last = -1
    while time.time() < deadline:
        cur = len(seq)
        if cur == last:
            return
        last = cur
        time.sleep(0.02)


def test_superseded_loader_stops_before_all_composed(monkeypatch):
    composed = []

    def _fake_compose(app, desc, *a, **k):
        composed.append(desc.nucleus_id)
        time.sleep(0.005)
        return None

    monkeypatch.setattr(cb_worker, "compose_cell_rgb", _fake_compose)

    loader = CellImageLoader(app=_fake_app())
    loader.load_page([_Desc(i) for i in range(60)], 0, ["cy5"], False, gen=1)

    while len(composed) < 3:  # let a few through
        time.sleep(0.001)
    loader.cancel()          # supersede, as a newer page / dataset swap would
    _wait_stable(composed)

    assert len(composed) < 60, "supersede must short-circuit the compose loop"


def test_uninterrupted_loader_composes_the_whole_page(monkeypatch):
    composed = []
    monkeypatch.setattr(cb_worker, "compose_cell_rgb",
                        lambda app, desc, *a, **k: composed.append(desc.nucleus_id))

    loader = CellImageLoader(app=_fake_app())
    loader.load_page([_Desc(i) for i in range(8)], 0, ["cy5"], False, gen=1)
    _wait_stable(composed)

    assert len(composed) == 8


def test_cancel_bumps_latest_gen():
    loader = CellImageLoader(app=_fake_app())
    loader._latest_gen = 5
    loader.cancel()
    assert loader._latest_gen == 6
