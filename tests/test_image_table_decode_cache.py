"""Image Table decode-cache reuse: the parallel prefetch decodes each unique
(well, channel, variant, timepoint, fov) at most once and skips keys already
in ``state.image_table.prefetched_cells`` — so a second Generate on an
unchanged dataset performs zero new reads.

Exercises ``_prefetch_cell_arrays`` directly with the plain-data render model +
a stub loader, so it doesn't need image archives or a full app. Imports the
controller (Qt), so it runs in CI's fast lane rather than the Qt-free subset.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import controller as C


def _cell(well, chan, tp="0", fov="1"):
    return {"well": well, "chan": chan, "tp": tp, "fov": fov}


def _patch(monkeypatch, loaded):
    """Stub the per-row helpers + the array loader; record decoded keys."""
    monkeypatch.setattr(C, "_row_display_opts", lambda app, r, chan=None: (False, False, ""))
    monkeypatch.setattr(C, "_find_ratio_for_channel", lambda app, chan: None)
    monkeypatch.setattr(C, "_is_nuc_seg", lambda chan: False)

    def _fake_load(app, cache, well, chan, tp, fov, *, use_tophat=False):
        key = (well, chan.lower(), "tophat" if use_tophat else "raw")
        loaded.append(key)
        cache[key] = object()  # populate so a later prefetch skips it
        return cache[key]

    monkeypatch.setattr(C, "_load_array", _fake_load)


def _make_ctx(app):
    from well_viewer.core.app_runtime import AppRuntime
    app._ctx = SimpleNamespace(app=app, state=app._app_state,
                               runtime=AppRuntime(app))
    return app._ctx


def test_prefetch_decodes_each_unique_key_once_then_skips_cached(monkeypatch):
    loaded: list = []
    _patch(monkeypatch, loaded)
    app = SimpleNamespace(_app_state=SimpleNamespace(
        channel=SimpleNamespace(fluor_channels=["gfp"]),
        image_table=SimpleNamespace(prefetched_cells=set())))
    ctx = _make_ctx(app)
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
        channel=SimpleNamespace(fluor_channels=["gfp"]),
        image_table=SimpleNamespace(prefetched_cells=set())))
    ctx = _make_ctx(app)
    cache: dict = {}
    cells = [[_cell("A01", "RATIO"), _cell("A02", "GFP"), _cell("A03", "GFP")]]
    C._prefetch_cell_arrays(ctx, cells, cache)
    assert ("A01", "ratio", "raw") not in cache
    assert sorted(loaded) == [("A02", "gfp", "raw"), ("A03", "gfp", "raw")]


def test_prefetch_warms_every_distinct_timepoint_and_fov_not_just_one(monkeypatch):
    """Regression: the prefetch used to warm only ONE representative (tp, fov)
    per well+channel, so a grid faceted by timepoint or FOV (the Image
    Table's core use case) left most cells' actual pixel decode to happen
    later, sequentially, on the GUI thread. Every distinct cell must now get
    its own warm task."""
    loaded: list = []
    _patch(monkeypatch, loaded)
    app = SimpleNamespace(_app_state=SimpleNamespace(
        channel=SimpleNamespace(fluor_channels=["gfp"]),
        image_table=SimpleNamespace(prefetched_cells=set())))
    ctx = _make_ctx(app)
    cache: dict = {}
    # Same well+channel, three distinct timepoints -- a facet-by-timepoint row.
    cells = [[_cell("A01", "GFP", tp="0"), _cell("A01", "GFP", tp="24"),
             _cell("A01", "GFP", tp="48")]]

    C._prefetch_cell_arrays(ctx, cells, cache)
    # The stub loader's own cache key collapses tp/fov away (it only tracks
    # well/chan/variant), so assert on `loaded` -- which records one entry
    # per _load_array call, one per distinct (well, chan, tp, fov) task.
    assert len(loaded) == 3

    # Second Generate on the same grid: every one of those was marked warmed.
    loaded.clear()
    C._prefetch_cell_arrays(ctx, cells, cache)
    assert loaded == []
