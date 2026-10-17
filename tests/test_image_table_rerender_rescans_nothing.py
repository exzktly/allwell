"""A zoom / LUT / spacing re-render of the Image Table walks no folders.

Reported: "even clicking on the zoom percentage on the image table hangs
forever, 2 mins", on a dataset on a network drive. A click on the percentage is
``image_table_set_zoom(ctx, 1.0)``, which re-runs ``image_table_generate``, and
every Generate cleared the record of well/channel scans that came up empty. An
empty scan is kept for the session only when it is definitive (both folders
exist and the walk finished), so on a dataset whose input folder is not local
every re-render scanned each missing well/channel again — and a scan that finds
no images falls back to walking the input and output trees, which ``rglob``
listed in full before discarding everything below three levels.

What holds it now:

* a re-render keeps the empty-scan record (``reset_scan_misses(retry_misses=False)``);
  only the Generate button (``retry_missing=True``) and an export retry it;
* ``image_table_set_zoom`` re-renders without retrying;
* the fallback walk (``image_discovery._walk_capped``) returns exactly what the
  ``rglob`` filter returned, in the same order, without entering a directory
  whose entries would be deeper than the cap.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from review.data import image_discovery as idisc
from review.features.image_browser.image_table import controller as C
from review.features.image_browser.image_table import image_compose as IC


def _ctx(**dataset):
    ds = dict(data_dir=None, in_dir=None, pipeline_info={}, fov_tp_extractor=None)
    ds.update(dataset)
    return SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(**ds),
        image_table=SimpleNamespace(scan_misses=set(), raw_fallbacks={1},
                                    partial_scans={2}, scale_bar_refusals={3}),
    ))


def test_a_rerender_keeps_the_empty_scans_and_a_generate_retries_them(monkeypatch, tmp_path):
    calls = {"n": 0}

    def _scan(*_a, **_kw):
        calls["n"] += 1
        return {}, {}, {}, {}

    monkeypatch.setattr(idisc, "find_well_images_and_masks", _scan)
    # The input folder is not there (a remote run's raw images were never
    # brought back), so the miss is not definitive and is never cached.
    ctx = _ctx(data_dir=tmp_path, in_dir=tmp_path / "in")
    cache: dict = {}
    IC._load_well_channel(ctx, cache, "A01", "gfp")
    assert calls["n"] == 1

    IC.reset_scan_misses(ctx, retry_misses=False)   # zoom / LUT / spacing
    IC._load_well_channel(ctx, cache, "A01", "gfp")
    assert calls["n"] == 1, "a re-render scanned a well/channel already known empty"
    st = ctx.state.image_table
    assert st.raw_fallbacks == st.partial_scans == st.scale_bar_refusals == set(), \
        "the per-pass status records must still describe this pass only"

    IC.reset_scan_misses(ctx)                        # the Generate button
    IC._load_well_channel(ctx, cache, "A01", "gfp")
    assert calls["n"] == 2


def test_clicking_the_zoom_percentage_rerenders_without_retrying(monkeypatch):
    seen = []
    monkeypatch.setattr(C, "image_table_generate",
                        lambda ctx, **kw: seen.append(kw))
    ctx = SimpleNamespace(
        state=SimpleNamespace(image_table=SimpleNamespace(zoom=1.5, last_render={(0, 0): 1})),
        runtime=SimpleNamespace(image_table=SimpleNamespace(zoom_lbl=None)),
    )
    C.image_table_set_zoom(ctx, 1.0)
    assert seen == [{}], "a zoom asked for missing folders to be walked again"


def test_generate_defaults_to_not_retrying():
    import inspect
    from review.features.image_browser.image_table.controller import (
        image_table_generate as real,
    )
    assert inspect.signature(real).parameters["retry_missing"].default is False


def test_the_generate_button_retries_missing_folders():
    """The one caller that must retry. Read from the source because the button's
    handler is a closure inside ``build_image_table_tab``; building the whole
    tab for one keyword is the slow lane's job."""
    import ast
    import inspect

    from review.features.image_browser.image_table import tab_view

    tree = ast.parse(inspect.getsource(tab_view))
    handler = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_generate")
    calls = [n for n in ast.walk(handler) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "image_table_generate"]
    assert calls and all(
        any(k.arg == "retry_missing" and getattr(k.value, "value", None) is True
            for k in c.keywords) for c in calls), \
        "the Generate button no longer retries folders that came up empty"


def _tree(root: Path, depth: int, fanout: int = 2) -> None:
    def _fill(d: Path, level: int) -> None:
        d.mkdir(parents=True, exist_ok=True)
        for i in range(fanout):
            (d / f"img_{level}_{i}.tif").write_bytes(b"x")
        if level < depth:
            for i in range(fanout):
                _fill(d / f"sub{i}", level + 1)
    _fill(root, 1)


def test_the_capped_walk_is_the_rglob_filter_without_the_deep_part(tmp_path, monkeypatch):
    root = tmp_path / "out"
    _tree(root, depth=6)
    base = len(root.parts)
    expected = sorted(p for p in root.rglob("*") if len(p.parts) - base <= 3)

    visited = []
    real_walk = os.walk

    def _spy(top, **kw):
        for entry in real_walk(top, **kw):
            visited.append(len(Path(entry[0]).parts) - base)
            yield entry

    monkeypatch.setattr(idisc.os, "walk", _spy)
    assert idisc._walk_capped(root, 3) == expected
    assert max(visited) == 2, "the walk entered folders below the depth it keeps"
