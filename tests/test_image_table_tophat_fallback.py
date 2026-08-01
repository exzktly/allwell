"""A corrected (top-hat) cell the correction pass never produced falls back to
the raw frame, per cell.

The Image Table's default render is "corrected", so every cell asks for the
top-hat image. Pipelines routinely correct only a subset of a well's frames —
a run that was interrupted, one re-run over fewer timepoints, a correction only
ever asked for on some fields — which leaves a top-hat dict that is *non-empty*
but does not cover every ``(fov, tp)`` in the grid.

``_load_array`` used to fall back to raw only when the whole well/channel had no
top-hat set at all. A partially covered well therefore drew the cells the
correction reached and left the rest with no image, even though the raw frame
for each of those cells was already sitting in ``cache[(well, channel, "raw")]``
— stashed by ``_load_well_channel`` for exactly this fallback. On a 4x10 grid
whose correction covered six of ten timepoints that is 24 cells drawn and 16
blank, so any later change (a LUT edit, a colormap, a re-Generate) appeared to
reach "only the first 24".

The fallback is now per cell, and every cell it serves uncorrected is recorded
so the Generate status line can say how many frames in the grid are not the
corrected ones the user asked for.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6")

from well_viewer.features.image_browser.image_table import image_compose as IC


class _Ref:
    """Stand-in ImgRef — only its identity matters here."""

    def __init__(self, name: str) -> None:
        self.name = name


def _ctx():
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(data_dir=None, in_dir=None, pipeline_info={},
                                    fov_tp_extractor=None),
            image_table=SimpleNamespace(scan_misses=set(), raw_fallbacks=set()),
        ),
    )


def _patch_scan(monkeypatch, *, raw_keys, tophat_keys):
    """``find_well_images_and_masks`` returning the given raw / top-hat coverage."""
    import well_viewer.data.image_discovery as idisc

    raw = {k: _Ref(f"raw{k}") for k in raw_keys}
    tophat = {k: _Ref(f"th{k}") for k in tophat_keys}
    monkeypatch.setattr(idisc, "find_well_images_and_masks",
                        lambda *_a, **_kw: (raw, {}, {}, tophat))
    return raw, tophat


def _patch_decode(monkeypatch, seen):
    """Record which ref each ``_load_array`` actually decoded."""
    def _open(ref, greyscale=False):
        seen.append(ref.name)
        return np.zeros((4, 4), dtype=np.float32)

    monkeypatch.setattr("well_viewer.data.image_discovery.open_imgref_as_array", _open)


# ── The regression ──────────────────────────────────────────────────────────


def test_cell_outside_the_tophat_coverage_falls_back_to_raw(monkeypatch):
    """The corrected set covers ("1", "0") only; the ("1", "5") cell must still
    draw, from the raw frame, instead of coming back as no image at all."""
    _patch_scan(monkeypatch,
                raw_keys=[("1", "0"), ("1", "5")],
                tophat_keys=[("1", "0")])
    seen: list[str] = []
    _patch_decode(monkeypatch, seen)

    ctx = _ctx()
    cache: dict = {}
    arr = IC._load_array(ctx, cache, "A01", "GFP", "5", "1", use_tophat=True)

    assert arr is not None, "a cell the correction never covered must still draw"
    assert seen == ["raw('1', '5')"]


def test_the_uncorrected_cell_is_recorded_not_silently_substituted(monkeypatch):
    """A grid mixing corrected and raw frames must be able to say so — the
    status line counts this record."""
    _patch_scan(monkeypatch,
                raw_keys=[("1", "0"), ("1", "5")],
                tophat_keys=[("1", "0")])
    _patch_decode(monkeypatch, [])

    ctx = _ctx()
    cache: dict = {}
    IC._load_array(ctx, cache, "A01", "GFP", "0", "1", use_tophat=True)
    assert ctx.state.image_table.raw_fallbacks == set(), "a covered cell is not a fallback"

    IC._load_array(ctx, cache, "A01", "GFP", "5", "1", use_tophat=True)
    assert ctx.state.image_table.raw_fallbacks == {("A01", "gfp", "5", "1")}


def test_a_covered_cell_still_gets_the_corrected_frame(monkeypatch):
    """The fallback must not fire where the correction does exist."""
    _patch_scan(monkeypatch,
                raw_keys=[("1", "0"), ("1", "5")],
                tophat_keys=[("1", "0"), ("1", "5")])
    seen: list[str] = []
    _patch_decode(monkeypatch, seen)

    ctx = _ctx()
    cache: dict = {}
    for tp in ("0", "5"):
        IC._load_array(ctx, cache, "A01", "GFP", tp, "1", use_tophat=True)
    assert seen == ["th('1', '0')", "th('1', '5')"]
    assert ctx.state.image_table.raw_fallbacks == set()


def test_a_well_with_no_tophat_at_all_still_falls_back(monkeypatch):
    """The whole-dict fallback this replaces must keep working."""
    _patch_scan(monkeypatch, raw_keys=[("1", "0")], tophat_keys=[])
    seen: list[str] = []
    _patch_decode(monkeypatch, seen)

    ctx = _ctx()
    assert IC._load_array(ctx, {}, "A01", "GFP", "0", "1", use_tophat=True) is not None
    assert seen == ["raw('1', '0')"]


def test_raw_render_never_reaches_for_the_corrected_frame(monkeypatch):
    """``render="raw"`` asks for raw and gets raw, fallback record untouched."""
    _patch_scan(monkeypatch, raw_keys=[("1", "0")], tophat_keys=[("1", "0")])
    seen: list[str] = []
    _patch_decode(monkeypatch, seen)

    ctx = _ctx()
    IC._load_array(ctx, {}, "A01", "GFP", "0", "1", use_tophat=False)
    assert seen == ["raw('1', '0')"]
    assert ctx.state.image_table.raw_fallbacks == set()


def test_an_ambiguous_empty_fov_is_still_refused(monkeypatch):
    """An empty FOV spanning several fields is a deliberate refusal, not a gap in
    the corrected set — retrying it against raw would resurrect exactly the
    arbitrary-field pick the refusal exists to prevent."""
    _patch_scan(monkeypatch,
                raw_keys=[("1", "0"), ("2", "0")],
                tophat_keys=[("1", "0"), ("2", "0")])
    _patch_decode(monkeypatch, [])

    ctx = _ctx()
    assert IC._load_array(ctx, {}, "A01", "GFP", "0", "", use_tophat=True) is None
    assert ctx.state.image_table.raw_fallbacks == set()


def test_reset_clears_the_raw_fallback_record(monkeypatch):
    """The record is per render pass, like ``scan_misses`` — a stale count would
    describe a pass the user is no longer looking at."""
    ctx = _ctx()
    ctx.state.image_table.scan_misses.add(("A01", "gfp", "raw"))
    ctx.state.image_table.raw_fallbacks.add(("A01", "gfp", "5", "1"))
    IC.reset_scan_misses(ctx)
    assert ctx.state.image_table.scan_misses == set()
    assert ctx.state.image_table.raw_fallbacks == set()


def test_reset_tolerates_a_state_without_the_record():
    """Older saved/stub states carry no ``raw_fallbacks`` — the reset must not
    raise for them (``image_table_generate`` calls it unconditionally)."""
    ctx = SimpleNamespace(state=SimpleNamespace(
        image_table=SimpleNamespace(scan_misses=set())))
    IC.reset_scan_misses(ctx)   # must not raise
