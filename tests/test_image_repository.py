"""Unit tests for the ImageRepository facade (roadmap B5).

Pure-Python: a minimal fake ctx over a real ``DatasetState`` — no Qt, no app,
no images on disk. The assertions are on the **outbound calls**, because that is
what the facade is: every method is a composition over functions that already
existed, and what can go wrong is the composition — a dataset field dropped on
its way to the discovery scan, or one of the two caches left behind on a swap.
Checking a return value the delegate produced would pass either way.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from well_viewer.core.dataset_state import DatasetState
from well_viewer.data import image_repository as repo_mod
from well_viewer.data.image_repository import ImageRepository


def _ctx(dataset: DatasetState):
    return SimpleNamespace(state=SimpleNamespace(dataset=dataset))


def _dataset() -> DatasetState:
    ds = DatasetState()
    ds.data_dir = Path("/plates/run7/out")
    ds.in_dir = Path("/plates/run7/in")
    ds.pipeline_info = {"schema": "well_fov_tp"}
    ds.fov_tp_extractor = lambda stem: ("1", "0")
    return ds


def test_discovery_is_handed_every_dataset_field_it_needs(monkeypatch):
    """The four fields each call site used to assemble by hand.

    Dropping any one of them is silent — the scan still returns, with fewer
    images or images matched by the wrong schema — so they are asserted
    individually rather than by comparing one blob.
    """
    seen = {}

    def _fake_find(data_dir, well_label, **kwargs):
        seen["data_dir"] = data_dir
        seen["well_label"] = well_label
        seen.update(kwargs)
        return ({}, {}, {}, {})

    import well_viewer.data.image_discovery as discovery
    monkeypatch.setattr(discovery, "find_well_images_and_masks", _fake_find)

    ds = _dataset()
    result = ImageRepository(_ctx(ds)).images_for_well("A01", "gfp")

    assert result == ({}, {}, {}, {})
    assert seen["data_dir"] == ds.data_dir
    assert seen["well_label"] == "A01"
    assert seen["fluor_token"] == "gfp"
    assert seen["in_dir"] == ds.in_dir
    assert seen["_fov_tp_extractor"] is ds.fov_tp_extractor
    assert seen["_pipeline_info"] is ds.pipeline_info


def test_array_passes_the_ref_and_the_greyscale_flag(monkeypatch):
    calls = []

    import well_viewer.data.image_discovery as discovery
    monkeypatch.setattr(discovery, "open_imgref_as_array",
                        lambda ref, greyscale=False: calls.append((ref, greyscale)) or "arr")

    ref = object()
    assert ImageRepository.array(ref, greyscale=True) == "arr"
    assert ImageRepository.array(ref) == "arr"
    assert calls == [(ref, True), (ref, False)]


def test_array_needs_no_context():
    """It is a staticmethod on purpose: a resolved ref names its file
    completely, and the Playground's worker thread decodes without a ctx."""
    assert isinstance(ImageRepository.__dict__["array"], staticmethod)


def test_invalidate_drops_both_caches(monkeypatch):
    """The one that bit: dropping one cache and keeping the other leaves the app
    choosing between a stale picture and a stale archive handle."""
    dropped = []

    import well_viewer.data.image_discovery as discovery
    import well_viewer.data.zipfile_cache as zipcache
    monkeypatch.setattr(discovery, "invalidate_decode_cache",
                        lambda: dropped.append("decode"))
    monkeypatch.setattr(zipcache, "invalidate", lambda path=None: dropped.append("zip"))

    ImageRepository.invalidate()

    assert sorted(dropped) == ["decode", "zip"]


def test_the_repository_is_wired_onto_the_state():
    """Reached as ``ctx.state.images``, attached where ``ctx.state.repo`` is."""
    pytest.importorskip("PySide6")
    from well_viewer.core.app_state import AppState
    from well_viewer.viewer_context import ViewerContext

    state = AppState()
    ctx = ViewerContext(state=state, bus=None, widgets=None)

    assert isinstance(state.images, ImageRepository)
    assert ctx.state.images is state.images


def test_it_does_not_wrap_the_grid_loaders():
    """A deliberate absence, pinned so it is not "fixed" by accident.

    ``frame_load.load_frame_array`` / ``load_mask_array`` take the caller's
    inventory cache and are called with ``ctx=None`` on one path; routing them
    through a context-bound facade would add a hop and break that path. The
    reasoning is in the module docstring — this makes the decision visible to
    anyone who goes looking for the methods and does not find them.
    """
    assert not hasattr(ImageRepository, "frame")
    assert not hasattr(ImageRepository, "mask")
    assert "load_frame_array" in repo_mod.__doc__, (
        "the absence is only safe while the reason for it is written down")
