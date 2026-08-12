"""The Segmentation tab's Crop scope — which frames the crop rectangle filters.

The crop used to be unconditionally dataset-wide. It is now armed with the same
wells / FOVs / timepoints control the drag-delete on that tab uses, with the
opposite un-armed default: an untouched field means *every* value, so a crop
nobody has scoped behaves exactly as it did before.

Two halves are exercised here: the pure verdict (which rows a scoped crop is
allowed to judge, and that the ones it is not keep passing) and the popover
(what it seeds, what one edit arms, and what the button then reads).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from well_viewer.core.frame_scope import describe_scope
from well_viewer.features.crop.region import (
    FrameScope, compute_crop_pass, rows_in_scope,
)
from well_viewer.features.crop import controller as crop


def _df(rows, cols, fovs=None, tps=None):
    d = {"shape_centroid_row": rows, "shape_centroid_col": cols}
    if fovs is not None:
        d["fov"] = fovs
    if tps is not None:
        d["timepoint"] = tps
    return pd.DataFrame(d)


# ── rows_in_scope ─────────────────────────────────────────────────────────────
def test_unarmed_scope_covers_every_row():
    df = _df([2, 50], [3, 60], fovs=["1", "2"], tps=["0", "24"])
    assert list(rows_in_scope(df, None, "A01")) == [True, True]
    assert list(rows_in_scope(df, FrameScope(), "A01")) == [True, True]


def test_well_outside_the_scope_takes_the_whole_frame_out():
    df = _df([2, 50], [3, 60])
    scope = FrameScope(wells=frozenset({"B02"}))
    assert list(rows_in_scope(df, scope, "A01")) == [False, False]
    assert list(rows_in_scope(df, scope, "B02")) == [True, True]


def test_fov_and_timepoint_are_matched_per_row_in_canonical_form():
    """The CSV spells its tokens however the pipeline wrote them; the picker
    offers the canonical forms. ``001`` and ``1`` are one FOV, ``T24`` and
    ``24`` one timepoint, or a scope armed from the combos would match no row."""
    df = _df([1, 2, 3, 4], [1, 2, 3, 4],
             fovs=["001", "1", "2", "2"], tps=["T24", "0", "24", "0"])
    scope = FrameScope(fovs=frozenset({"1"}), tps=frozenset({"24"}))
    assert list(rows_in_scope(df, scope, "A01")) == [True, False, False, False]


def test_scope_tokens_are_normalised_too():
    """A scope hydrated from an older persistence.json can hold raw tokens."""
    df = _df([1, 2], [1, 2], fovs=["1", "2"], tps=["24", "0"])
    scope = FrameScope(fovs=frozenset({"01"}), tps=frozenset({"T24"}))
    assert list(rows_in_scope(df, scope, "A01")) == [True, False]


def test_missing_timepoint_column_matches_no_armed_timepoint():
    """Fail-open: the crop then applies to none of the frame's rows rather than
    to all of them."""
    df = _df([2, 50], [3, 60])
    scope = FrameScope(tps=frozenset({"24"}))
    assert list(rows_in_scope(df, scope, "A01")) == [False, False]


# ── the verdict ───────────────────────────────────────────────────────────────
def test_out_of_scope_cells_keep_passing_the_crop():
    """The decisive property: a cell outside the box but in a frame the crop is
    not armed for must stay included, or a scoped crop would be no different
    from the plate-wide one it replaced."""
    df = _df([2, 50], [3, 60])
    box = (0, 0, 10, 10)
    assert list(compute_crop_pass(df, box)) == [1, 0]
    in_scope = FrameScope(wells=frozenset({"A01"}))
    assert list(compute_crop_pass(df, box, in_scope, "A01")) == [1, 0]
    assert list(compute_crop_pass(df, box, in_scope, "B02")) == [1, 1]


def test_scope_narrows_by_timepoint_within_one_well():
    df = _df([2, 50, 2, 50], [3, 60, 3, 60], tps=["0", "0", "24", "24"])
    scope = FrameScope(tps=frozenset({"24"}))
    # Only the t=24 rows are judged; the t=0 outsider keeps passing.
    assert list(compute_crop_pass(df, (0, 0, 10, 10), scope, "A01")) == [1, 1, 1, 0]


def test_empty_field_selects_no_frames():
    """An emptied dropdown is not "all" — it names nothing, so the crop applies
    nowhere. Reading it as "all" would turn a stray click into a plate-wide
    crop."""
    df = _df([2, 50], [3, 60])
    scope = FrameScope(wells=frozenset())
    assert list(compute_crop_pass(df, (0, 0, 10, 10), scope, "A01")) == [1, 1]


# ── projection through the cache walk ────────────────────────────────────────
def _ctx(cache, region=None, **scope):
    ctx = SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(
                cache=cache, crop_region=region,
                well_paths={k: f"/tmp/{k}.csv" for k in cache},
                all_fovs_cache=["1", "2"], all_timepoints_cache=[0.0, 24.0],
                crop_scope_wells=scope.get("wells"),
                crop_scope_fovs=scope.get("fovs"),
                crop_scope_tps=scope.get("tps"))))
    ctx.app = ctx
    return ctx


def test_projection_only_crops_the_wells_in_scope():
    """The well label reaches the verdict through the projector walk — the
    frames themselves need carry no well column."""
    cache = {"A01": _df([2, 50], [3, 60]), "B02": _df([2, 50], [3, 60])}
    ctx = _ctx(cache, region=(0, 0, 10, 10), wells={"A01"})
    crop.apply_crop_to_cache(ctx)
    assert list(cache["A01"]["Crop_Pass"]) == [1, 0]
    assert list(cache["B02"]["Crop_Pass"]) == [1, 1]


# ── the readout ───────────────────────────────────────────────────────────────
def test_unarmed_readout_says_all_frames():
    ctx = _ctx({"A01": _df([2], [3])})
    assert describe_scope(ctx, crop.CROP_SCOPE) == "Crop scope: all frames"


def test_armed_readout_names_the_fields_it_narrows_and_the_ones_it_does_not():
    cache = {"A01": _df([2], [3]), "B02": _df([2], [3]), "C03": _df([2], [3])}
    ctx = _ctx(cache, wells={"A01", "B02"}, tps={"24"})
    assert describe_scope(ctx, crop.CROP_SCOPE) == (
        "Crop scope: 2 wells · all FOVs · 1 TP"
    )


# ── the outline and the verdict must not disagree ────────────────────────────
def test_frame_in_scope_matches_a_scope_holding_raw_tokens():
    """A scope hydrated from a hand-edited persistence.json can hold ``T24``
    where the combos read ``24``. Judging the frame by a second, un-normalised
    comparison drew the outline dashed while cells were being excluded."""
    from well_viewer.features.crop.region import frame_in_scope
    df = _df([1, 50], [1, 50], fovs=["1", "1"], tps=["24", "24"])
    scope = FrameScope(fovs=frozenset({"001"}), tps=frozenset({"T24"}))
    assert frame_in_scope(df, scope, "A01", "1", "24") is True
    assert list(compute_crop_pass(df, (0, 0, 10, 10), scope, "A01")) == [1, 0]


def test_frame_with_no_timepoint_column_is_out_of_scope_when_tps_are_armed():
    """The pickers offer a timepoint from ``pipeline_info`` that the rows do not
    carry, so no row can match it and the crop reaches nothing. Saying the frame
    is in scope would state a filter that is not running."""
    from well_viewer.features.crop.region import frame_in_scope
    df = _df([1, 50], [1, 50], fovs=["1", "1"])          # no timepoint column
    scope = FrameScope(tps=frozenset({"0"}))
    assert list(compute_crop_pass(df, (0, 0, 10, 10), scope, "A01")) == [1, 1]
    assert frame_in_scope(df, scope, "A01", "1", "0") is False


def test_frame_in_scope_falls_back_to_tokens_without_rows():
    """A well with images but no CSV rows still shows its crop."""
    from well_viewer.features.crop.region import frame_in_scope
    scope = FrameScope(wells=frozenset({"A01"}), tps=frozenset({"24"}))
    assert frame_in_scope(None, scope, "A01", "1", "24") is True
    assert frame_in_scope(None, scope, "A01", "1", "0") is False
    assert frame_in_scope(None, scope, "B02", "1", "24") is False


# ── clearing ─────────────────────────────────────────────────────────────────
def _full_ctx(**scope):
    """A ctx complete enough for the controller's clear / reset paths."""
    ctx = _ctx({"A01": _df([2, 50], [3, 60])}, region=(0, 0, 10, 10), **scope)
    ctx.state.dataset.reset_crop_scope = lambda: (
        setattr(ctx.state.dataset, "crop_scope_wells", None),
        setattr(ctx.state.dataset, "crop_scope_fovs", None),
        setattr(ctx.state.dataset, "crop_scope_tps", None),
    )
    ctx.statuses = []
    ctx.notify = SimpleNamespace(set_status=ctx.statuses.append)
    ctx.persistence = SimpleNamespace(schedule_save=lambda _d: None)
    ctx.widgets = SimpleNamespace(get_or_none=lambda _n: None)
    return ctx


def test_clear_crop_also_clears_the_scope():
    """A scope left armed behind a cleared box silently narrows the next crop
    the user draws — and would not survive a restart either, since the section
    is dropped once there is no region to save."""
    ctx = _full_ctx(wells={"A01"})
    crop.clear_crop_region(ctx)
    ds = ctx.state.dataset
    assert ds.crop_region is None
    assert (ds.crop_scope_wells, ds.crop_scope_fovs, ds.crop_scope_tps) == (
        None, None, None)
    assert "scope" in ctx.statuses[-1]


def test_clear_crop_with_a_scope_but_no_region_is_not_a_no_op():
    """Arming frames and then clearing must still disarm them, or the button
    goes on advertising a scope the user just cleared."""
    ctx = _full_ctx(wells={"A01"})
    ctx.state.dataset.crop_region = None
    crop.clear_crop_region(ctx)
    assert ctx.state.dataset.crop_scope_wells is None
    assert "nothing to clear" not in ctx.statuses[-1]


# ── the token memo ───────────────────────────────────────────────────────────
def test_frame_tokens_are_computed_once_per_frame(monkeypatch):
    """Normalising a well's tokens is the dominant cost of every scope question
    — ~430 ms on a 200k-row well — and both the row verdict and the outline's
    frame test ask it, the latter on every canvas redraw."""
    from well_viewer.data import image_identity
    from well_viewer.features.crop import region as region_mod

    calls: list[int] = []
    real = image_identity.frame_token_series
    monkeypatch.setattr(image_identity, "frame_token_series",
                        lambda df: (calls.append(1), real(df))[1])

    df = _df([1, 50], [1, 50], fovs=["1", "1"], tps=["24", "24"])
    scope = FrameScope(tps=frozenset({"24"}))
    for _ in range(3):
        region_mod.frame_in_scope(df, scope, "A01", "1", "24")
        rows_in_scope(df, scope, "A01")
    assert calls == [1]


def test_a_copied_frame_does_not_inherit_the_token_memo():
    """``df.attrs`` travels through ``df.copy()``; the key carries the frame's
    identity so a copy recomputes rather than reading another frame's tokens."""
    from well_viewer.features.crop import region as region_mod

    df = _df([1, 50], [1, 50], fovs=["1", "1"], tps=["24", "24"])
    region_mod._frame_tokens(df)
    other = df.copy()
    other["fov"] = ["9", "9"]
    fov_n, _tp_n = region_mod._frame_tokens(other)
    assert list(fov_n) == ["9", "9"]


def test_a_well_only_scope_needs_no_token_pass(monkeypatch):
    """The commonest scoping — a few wells, all FOVs and timepoints — is decided
    by the well alone, and this runs on every canvas redraw."""
    from well_viewer.data import image_identity
    from well_viewer.features.crop import region as region_mod

    monkeypatch.setattr(
        image_identity, "frame_token_series",
        lambda df: pytest.fail("token pass ran for a well-only scope"))
    df = _df([1, 50], [1, 50], fovs=["1", "1"], tps=["24", "24"])
    scope = FrameScope(wells=frozenset({"A01"}))
    assert region_mod.frame_in_scope(df, scope, "A01", "1", "24") is True
    assert list(rows_in_scope(df, scope, "A01")) == [True, True]
