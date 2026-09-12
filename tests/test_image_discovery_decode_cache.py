"""The app-wide decoded-array LRU in ``data/image_discovery``.

``open_imgref_as_array`` is the single choke point every pixel-decoding path uses;
memoizing it makes a near-identical re-render (a second Image Table Generate, a
crop/zoom tweak) nearly free on the decode axis. These tests pin the cache
contract without needing real image archives — the underlying decoder is stubbed
with a counter.
"""
from __future__ import annotations

import numpy as np
import pytest

from review.data import image_discovery as D


@pytest.fixture
def _ref(tmp_path):
    # ImgRef needs a real backing file so the mtime key term resolves.
    p = tmp_path / "A01_GFP_A01_1_1.tif"
    p.write_bytes(b"not-a-real-tiff")
    return D.ImgRef(disk_path=p)


@pytest.fixture(autouse=True)
def _fresh_cache():
    D.invalidate_decode_cache()
    yield
    D.invalidate_decode_cache()


def _stub(monkeypatch, calls):
    def _decode(*, ref, greyscale, **_kw):
        calls.append((ref.full_path_str, greyscale))
        # A distinct array per call so identity/caching is observable.
        return np.zeros((4, 4), dtype=np.float32)
    monkeypatch.setattr(D, "_preview_open_imgref_as_array", _decode)


def test_second_call_is_cached(monkeypatch, _ref):
    calls: list = []
    _stub(monkeypatch, calls)
    a = D.open_imgref_as_array(_ref, greyscale=True)
    b = D.open_imgref_as_array(_ref, greyscale=True)
    assert a is b, "second decode of the same ref must return the cached object"
    assert len(calls) == 1, "underlying decoder must run only once"


def test_greyscale_flag_is_part_of_key(monkeypatch, _ref):
    calls: list = []
    _stub(monkeypatch, calls)
    grey = D.open_imgref_as_array(_ref, greyscale=True)
    colour = D.open_imgref_as_array(_ref, greyscale=False)
    assert grey is not colour
    assert len(calls) == 2, "grey vs colour load of the same file are distinct keys"


def test_invalidate_forces_redecode(monkeypatch, _ref):
    calls: list = []
    _stub(monkeypatch, calls)
    D.open_imgref_as_array(_ref, greyscale=True)
    D.invalidate_decode_cache()
    D.open_imgref_as_array(_ref, greyscale=True)
    assert len(calls) == 2, "invalidate must drop the cached pixels"


def test_entry_cap_evicts_lru(monkeypatch, tmp_path):
    calls: list = []
    _stub(monkeypatch, calls)
    cache = D._DecodedArrayCache(max_entries=2, max_bytes=1 << 30)
    monkeypatch.setattr(D, "_DECODE_CACHE", cache)
    refs = []
    for i in range(3):
        p = tmp_path / f"img{i}.tif"
        p.write_bytes(b"x")
        refs.append(D.ImgRef(disk_path=p))
    for r in refs:                       # fill 3 into a 2-slot cache
        D.open_imgref_as_array(r, greyscale=True)
    assert len(calls) == 3
    # refs[0] was evicted (LRU); re-requesting it re-decodes, refs[2] still hot.
    D.open_imgref_as_array(refs[2], greyscale=True)
    D.open_imgref_as_array(refs[0], greyscale=True)
    assert [c[0] for c in calls[3:]] == [refs[0].full_path_str]


def test_byte_budget_evicts(monkeypatch, tmp_path):
    # A tiny byte budget forces eviction even under the entry cap.
    cache = D._DecodedArrayCache(max_entries=100, max_bytes=4 * 4 * 4 + 1)  # ~one 4x4 f32
    monkeypatch.setattr(D, "_DECODE_CACHE", cache)
    calls: list = []
    _stub(monkeypatch, calls)
    r1 = D.ImgRef(disk_path=tmp_path / "a.tif"); (tmp_path / "a.tif").write_bytes(b"x")
    r2 = D.ImgRef(disk_path=tmp_path / "b.tif"); (tmp_path / "b.tif").write_bytes(b"x")
    D.open_imgref_as_array(r1, greyscale=True)
    D.open_imgref_as_array(r2, greyscale=True)   # evicts r1 (over byte budget)
    D.open_imgref_as_array(r1, greyscale=True)   # re-decodes
    assert len(calls) == 3
