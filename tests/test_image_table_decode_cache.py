"""Image Table decode-cache reuse: the parallel prefetch decodes each unique
(well, channel, variant) at most once and skips keys already in the persistent
cache — so a second Generate on an unchanged dataset performs zero new reads.

Exercises ``_prefetch_cell_arrays`` directly with stub widgets + a stub loader,
so it doesn't need image archives or a full app. Imports the controller (Qt), so
it runs in CI's fast lane rather than the local Qt-free subset.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import controller as C  # noqa: E402


class _Combo:
    def __init__(self, text: str):
        self._t = text

    def currentText(self) -> str:
        return self._t


def _cell(well, chan, tp="0", fov="1"):
    return {
        "well_cb": _Combo(well), "chan_cb": _Combo(chan),
        "tp_cb": _Combo(tp), "fov_cb": _Combo(fov),
    }


def _patch(monkeypatch, loaded):
    """Stub the per-row helpers + the array loader; record decoded keys."""
    monkeypatch.setattr(C, "_row_display_opts", lambda app, r: (False, False, ""))
    monkeypatch.setattr(C, "_find_ratio_for_channel", lambda app, chan: None)
    monkeypatch.setattr(C, "_is_nuc_seg", lambda chan: False)

    def _fake_load(app, cache, well, chan, tp, fov, *, use_tophat=False):
        key = (well, chan.lower(), "tophat" if use_tophat else "raw")
        loaded.append(key)
        cache[key] = object()  # populate so a later prefetch skips it
        return cache[key]

    monkeypatch.setattr(C, "_load_array", _fake_load)


def test_prefetch_decodes_each_unique_key_once_then_skips_cached(monkeypatch):
    loaded: list = []
    _patch(monkeypatch, loaded)
    app = SimpleNamespace(_app_state=SimpleNamespace(
        channel=SimpleNamespace(fluor_channels=["gfp"])))
    from well_viewer.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state,
                               runtime=AppRuntime(app))
    cache: dict = {}
    # Two wells × the same channel, each repeated in two cells → 2 unique keys.
    cells = [
        [_cell("A01", "GFP"), _cell("A01", "GFP")],
        [_cell("A02", "GFP"), _cell("A02", "GFP")],
    ]

    C._prefetch_cell_arrays(app._ctx, cells, cache)
    assert sorted(loaded) == [("A01", "gfp", "raw"), ("A02", "gfp", "raw")]
    assert set(cache) == {("A01", "gfp", "raw"), ("A02", "gfp", "raw")}

    # Second Generate on the warm cache: nothing new is decoded.
    loaded.clear()
    C._prefetch_cell_arrays(app._ctx, cells, cache)
    assert loaded == []


def test_prefetch_skips_ratio_channels(monkeypatch):
    loaded: list = []
    _patch(monkeypatch, loaded)
    # Treat "RATIO" as a ratio channel → must not be prefetched as a raw image.
    monkeypatch.setattr(
        C, "_find_ratio_for_channel",
        lambda app, chan: object() if chan == "RATIO" else None,
    )
    app = SimpleNamespace(_app_state=SimpleNamespace(
        channel=SimpleNamespace(fluor_channels=["gfp"])))
    from well_viewer.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state,
                               runtime=AppRuntime(app))
    cache: dict = {}
    cells = [[_cell("A01", "RATIO"), _cell("A02", "GFP"), _cell("A03", "GFP")]]
    C._prefetch_cell_arrays(app._ctx, cells, cache)
    assert ("A01", "ratio", "raw") not in cache
    assert sorted(loaded) == [("A02", "gfp", "raw"), ("A03", "gfp", "raw")]
