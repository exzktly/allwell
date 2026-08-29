"""Scoped region delete on the Segmentation tab.

A drag in Delete mode can be armed to apply beyond the frame it is drawn on.
Off-screen frames have no loaded mask, so membership there is decided by the
per-cell centroid columns — the same geometry the crop layer uses, read with the
opposite fail-safe direction (an unplaceable cell is never deleted).

Qt-free; needs pandas. Covers the shared geometry helper, the target resolution
across wells / FOVs / timepoints, and the default scope still meaning "this
frame only".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from well_viewer.features.crop.region import (
    centroids_inside, compute_crop_pass, has_centroid_columns,
)
from well_viewer.features.image_browser.review_image.collect import (
    collect_region_delete_targets,
)


# ── the shared centroid geometry ─────────────────────────────────────────────

def _df(rows, cols, **extra):
    data = {"shape_centroid_row": rows, "shape_centroid_col": cols}
    data.update(extra)
    return pd.DataFrame(data)


def test_has_centroid_columns():
    assert has_centroid_columns(_df([1], [1]))
    assert not has_centroid_columns(pd.DataFrame({"nucleus_id": [1]}))
    assert not has_centroid_columns(pd.DataFrame())


def test_inside_outside_and_boundary():
    # region is (row0, col0, row1, col1), bounds inclusive
    df = _df([10, 50, 20, 100], [10, 50, 20, 100])
    inside = centroids_inside(df, (20, 20, 60, 60))
    assert list(inside) == [False, True, True, False]


def test_nan_centroid_is_never_deleted():
    df = _df([float("nan"), 30], [30, float("nan")])
    assert list(centroids_inside(df, (0, 0, 100, 100))) == [False, False]


def test_missing_columns_and_no_region_delete_nothing():
    df = pd.DataFrame({"nucleus_id": [1, 2]})
    assert list(centroids_inside(df, (0, 0, 100, 100))) == [False, False]
    assert list(centroids_inside(_df([1], [1]), None)) == [False]


def test_crop_still_fails_open_on_unplaceable_cells():
    """The crop keeps what it cannot place; the region delete spares it. Both
    read the same helper, so this guards the direction of the inversion."""
    df = _df([float("nan"), 500], [float("nan"), 500])
    assert list(compute_crop_pass(df, (0, 0, 100, 100))) == [1, 0]
    assert list(centroids_inside(df, (0, 0, 100, 100))) == [False, False]


# ── target resolution across a scope ─────────────────────────────────────────

def _rows(well, spec, *, centroids=True):
    """Build a well frame from ``[(fov, tp, nid, row, col), …]``."""
    data = {
        "well": [well] * len(spec),
        "fov": [s[0] for s in spec],
        "timepoint": [s[1] for s in spec],
        "nucleus_id": [s[2] for s in spec],
    }
    if centroids:
        data["shape_centroid_row"] = [s[3] for s in spec]
        data["shape_centroid_col"] = [s[4] for s in spec]
    return pd.DataFrame(data)


def _ctx(frames):
    """A ctx stub whose ``get_rows`` serves ``{well: DataFrame}``."""
    return SimpleNamespace(
        state=SimpleNamespace(
            dataset=SimpleNamespace(
                well_paths={w: f"/tmp/{w}.csv" for w in frames},
                cache=dict(frames),
            ),
        ),
    )


@pytest.fixture(autouse=True)
def _patch_get_rows(monkeypatch):
    """``collect_region_delete_targets`` fetches rows through the data service;
    serve them from the stub ctx's cache instead of the filesystem."""
    import well_viewer.features.image_browser.review_image.collect as collect

    monkeypatch.setattr(
        collect, "get_rows", lambda ctx, well: ctx.state.dataset.cache.get(well),
    )


_INSIDE = (10, 10)      # a centroid inside the test region
_OUTSIDE = (900, 900)   # and one outside it
_REGION = (0, 0, 100, 100)


def test_targets_span_wells_fovs_and_timepoints():
    spec = [
        ("1", "0", "1", *_INSIDE),
        ("1", "0", "2", *_OUTSIDE),
        ("2", "0", "3", *_INSIDE),
        ("1", "24", "4", *_INSIDE),
    ]
    ctx = _ctx({"A01": _rows("A01", spec), "B02": _rows("B02", spec)})
    targets, stats = collect_region_delete_targets(
        ctx, _REGION,
        wells={"A01", "B02"}, fovs={"1", "2"}, tps={"0", "24"},
    )
    assert sorted(targets) == [
        ("A01", "1", "0", "1"), ("A01", "1", "24", "4"), ("A01", "2", "0", "3"),
        ("B02", "1", "0", "1"), ("B02", "1", "24", "4"), ("B02", "2", "0", "3"),
    ]
    assert stats["wells"] == 2
    assert stats["frames"] == 6
    assert stats["scanned_wells"] == 2


def test_fov_and_timepoint_filters_narrow_the_scope():
    spec = [
        ("1", "0", "1", *_INSIDE),
        ("2", "0", "2", *_INSIDE),
        ("1", "24", "3", *_INSIDE),
    ]
    ctx = _ctx({"A01": _rows("A01", spec)})
    targets, _ = collect_region_delete_targets(
        ctx, _REGION, wells={"A01"}, fovs={"1"}, tps={"0"},
    )
    assert targets == [("A01", "1", "0", "1")]


def test_skip_leaves_the_drawn_frame_to_the_mask_rule():
    spec = [("1", "0", "1", *_INSIDE), ("2", "0", "2", *_INSIDE)]
    ctx = _ctx({"A01": _rows("A01", spec)})
    targets, stats = collect_region_delete_targets(
        ctx, _REGION, wells={"A01"}, fovs={"1", "2"}, tps={"0"},
        skip=("A01", "1", "0"),
    )
    assert targets == [("A01", "2", "0", "2")]
    assert stats["frames"] == 1


def test_default_scope_is_the_drawn_frame_alone():
    """The un-armed case: one well, one FOV, one timepoint, and that frame
    skipped — so the scoped pass contributes nothing and the delete is exactly
    the single-frame delete it has always been."""
    spec = [("1", "0", "1", *_INSIDE), ("2", "0", "2", *_INSIDE)]
    ctx = _ctx({"A01": _rows("A01", spec)})
    targets, stats = collect_region_delete_targets(
        ctx, _REGION, wells={"A01"}, fovs={"1"}, tps={"0"},
        skip=("A01", "1", "0"),
    )
    assert targets == []
    assert stats["frames"] == 0 and stats["wells"] == 0


def test_well_without_centroids_is_reported_not_silently_skipped():
    spec = [("1", "0", "1", *_INSIDE)]
    ctx = _ctx({
        "A01": _rows("A01", spec),
        "B02": _rows("B02", spec, centroids=False),
    })
    targets, stats = collect_region_delete_targets(
        ctx, _REGION, wells={"A01", "B02"}, fovs={"1"}, tps={"0"},
    )
    assert targets == [("A01", "1", "0", "1")]
    assert stats["no_centroid_wells"] == ["B02"]
    assert stats["scanned_wells"] == 2


def test_unreadable_well_is_reported():
    def _boom(ctx, well):
        raise OSError("unreadable")

    import well_viewer.features.image_browser.review_image.collect as collect
    ctx = _ctx({"A01": None})
    orig = collect.get_rows
    collect.get_rows = _boom
    try:
        targets, stats = collect_region_delete_targets(
            ctx, _REGION, wells={"A01"}, fovs={"1"}, tps={"0"},
        )
    finally:
        collect.get_rows = orig
    assert targets == []
    assert stats["unreadable_wells"] == ["A01"]


def test_empty_scope_selects_nothing():
    """An armed field the user emptied means "no targets", never "all"."""
    spec = [("1", "0", "1", *_INSIDE)]
    ctx = _ctx({"A01": _rows("A01", spec)})
    for kwargs in (
        {"wells": set(), "fovs": {"1"}, "tps": {"0"}},
        {"wells": {"A01"}, "fovs": set(), "tps": {"0"}},
        {"wells": {"A01"}, "fovs": {"1"}, "tps": set()},
    ):
        targets, _ = collect_region_delete_targets(ctx, _REGION, **kwargs)
        assert targets == []


def test_keys_match_the_override_map_normalisation():
    """FOV / timepoint / nucleus are normalised the way the read path
    normalises them, so a written override is findable again: leading-zero
    FOV stripped, ``:g`` timepoint, integer nucleus id."""
    spec = [("01", "T01", "7.0", *_INSIDE)]
    ctx = _ctx({"A01": _rows("A01", spec)})
    targets, _ = collect_region_delete_targets(
        ctx, _REGION, wells={"A01"}, fovs={"1"}, tps={"1"},
    )
    assert targets == [("A01", "1", "1", "7")]


def test_well_label_is_kept_exactly_as_the_dataset_spells_it():
    """The readers key off ``dataset.well_paths`` labels, which are
    ``extract_well_token(stem) or stem`` — so a CSV whose stem is not a well
    position keeps its raw stem. Canonicalising the well here would upper-case
    it into something no reader ever asks for, and the delete would report
    success while changing nothing."""
    spec = [("1", "0", "1", *_INSIDE)]
    ctx = _ctx({"measurements_ctrl": _rows("measurements_ctrl", spec)})
    targets, _ = collect_region_delete_targets(
        ctx, _REGION, wells={"measurements_ctrl"}, fovs={"1"}, tps={"0"},
    )
    assert targets == [("measurements_ctrl", "1", "0", "1")]


def test_armed_well_missing_from_the_dataset_is_reported():
    spec = [("1", "0", "1", *_INSIDE)]
    ctx = _ctx({"A01": _rows("A01", spec)})
    targets, stats = collect_region_delete_targets(
        ctx, _REGION, wells={"A01", "H12"}, fovs={"1"}, tps={"0"},
    )
    assert targets == [("A01", "1", "0", "1")]
    assert stats["unknown_wells"] == ["H12"]


# ── scope resolution ─────────────────────────────────────────────────────────

def test_resolve_delete_scope_defaults_to_the_displayed_frame():
    from well_viewer.features.image_browser.review_image.callbacks import (
        delete_scope_is_armed, resolve_delete_scope,
    )
    from well_viewer.features.image_browser.review_image.state import (
        ReviewImageState,
    )

    ctx = SimpleNamespace(state=SimpleNamespace(review_image=ReviewImageState()))
    assert not delete_scope_is_armed(ctx)
    # FOV and timepoint are normalised; the well is passed through untouched,
    # because the readers key off the dataset's own label.
    assert resolve_delete_scope(ctx, "a1", "01", "T01") == ({"a1"}, {"1"}, {"1"})

    ctx.state.review_image.delete_scope_wells = {"b2", "C03"}
    assert delete_scope_is_armed(ctx)
    wells, fovs, tps = resolve_delete_scope(ctx, "a1", "01", "T01")
    assert wells == {"b2", "C03"}
    # the un-armed fields still track the displayed frame
    assert (fovs, tps) == ({"1"}, {"1"})


def test_zero_timepoint_survives_normalisation():
    """``normalize_tp(0.0)`` used to return "" because 0.0 is falsy, so a
    numeric ``t=0`` baseline — the first column of most time courses — read as
    "no timepoint" and dropped out of every scope and every override key."""
    from well_viewer.data.image_identity import normalize_tp
    from well_viewer.core.ui_helpers import norm_timepoint

    assert normalize_tp(0.0) == "0"
    assert normalize_tp(0) == "0"
    assert norm_timepoint(None, 0.0) == "0"
    assert normalize_tp(None) == ""
    assert normalize_tp("") == ""


# ── the whole apply path ─────────────────────────────────────────────────────

@pytest.fixture
def apply_ctx(monkeypatch):
    """A ctx stub complete enough to run ``apply_box_delete`` for real.

    Two wells, two FOVs, one timepoint. The displayed frame is A01/FOV 1, whose
    label mask puts nucleus 1 inside the drawn box and nucleus 2 outside it —
    matching the centroids, so the mask rule and the centroid rule agree and the
    counts stay readable.
    """
    np = pytest.importorskip("numpy")
    import well_viewer.features.image_browser.review_image.callbacks as cbs
    from well_viewer.features.image_browser.review_image.state import (
        ReviewImageState,
    )

    for name in ("refresh_review_image_ctx", "render_review_image_display",
                 "apply_curation_to_cache"):
        monkeypatch.setattr(cbs, name, lambda *a, **k: None)
    monkeypatch.setattr(
        "well_viewer.core.selection_controller.invalidate_stats_cache",
        lambda ctx: None,
    )

    spec = [("1", "0", "1", 10, 10), ("1", "0", "2", 900, 900),
            ("2", "0", "3", 10, 10)]
    frames = {w: _rows(w, spec) for w in ("A01", "B02")}

    # 1000×1000 label mask: nucleus 1 in the top-left corner, nucleus 2 far away.
    mask = np.zeros((1000, 1000), dtype=np.int32)
    mask[5:15, 5:15] = 1
    mask[890:910, 890:910] = 2

    label = SimpleNamespace(_mask_arr=mask)
    ctx = _ctx(frames)
    ctx.state.review_image = ReviewImageState()
    ctx.state.preview = SimpleNamespace(selected_well="A01")
    ctx.runtime = SimpleNamespace(
        review_image=SimpleNamespace(
            label=label,
            fov_menu=SimpleNamespace(currentText=lambda: "1"),
            tp_cb=SimpleNamespace(currentText=lambda: "0"),
        ),
        review=SimpleNamespace(included_overrides={}),
    )
    ctx.widgets = SimpleNamespace(get_or_none=lambda name: None)
    ctx.persistence = SimpleNamespace(schedule_save=lambda name: None)
    ctx.status: list = []
    ctx.notify = SimpleNamespace(
        set_status=ctx.status.append,
        toast=lambda msg, **kw: ctx.status.append(f"TOAST:{msg}"),
    )
    return ctx


def _box(ctx):
    """Drag the top-left 0–100 px box, in mask pixels (x0, y0, x1, y1)."""
    from well_viewer.features.image_browser.review_image.callbacks import (
        apply_box_delete,
    )
    apply_box_delete(ctx, 0, 0, 100, 100)


def test_unarmed_delete_touches_only_the_displayed_frame(apply_ctx):
    _box(apply_ctx)
    assert set(apply_ctx.runtime.review.included_overrides) == {
        ("A01", "1", "0", "1"),
    }
    assert apply_ctx.status[-1].startswith("Box delete: marked 1 cell(s)")


def test_armed_delete_reaches_the_other_wells_and_fovs(apply_ctx):
    ri = apply_ctx.state.review_image
    ri.delete_scope_wells = {"A01", "B02"}
    ri.delete_scope_fovs = {"1", "2"}
    _box(apply_ctx)
    assert set(apply_ctx.runtime.review.included_overrides) == {
        ("A01", "1", "0", "1"),   # drawn frame, by pixel overlap
        ("A01", "2", "0", "3"),   # same well, other FOV, by centroid
        ("B02", "1", "0", "1"),
        ("B02", "2", "0", "3"),
    }
    assert all(v == "0" for v in apply_ctx.runtime.review.included_overrides.values())
    status = apply_ctx.status[-1]
    assert "4 cell(s) Included=0" in status
    assert "1 in this frame (pixel overlap)" in status
    assert "3 across 3 other frame(s) in 2 well(s) (centroid inside)" in status


def test_scope_excluding_the_displayed_frame_says_so(apply_ctx):
    apply_ctx.state.review_image.delete_scope_wells = {"B02"}
    _box(apply_ctx)
    assert set(apply_ctx.runtime.review.included_overrides) == {
        ("B02", "1", "0", "1"),
    }
    assert "this frame is outside the armed scope" in apply_ctx.status[-1]


def test_nothing_in_range_reports_rather_than_going_quiet(apply_ctx):
    apply_ctx.state.review_image.delete_scope_wells = set()
    _box(apply_ctx)
    assert apply_ctx.runtime.review.included_overrides == {}
    assert "0 cell(s) Included=0" in apply_ctx.status[-1]


def test_baseline_timepoint_is_deletable(apply_ctx):
    """The frames in this fixture are all at ``t=0``. A falsy-zero bug in the
    timepoint normaliser made that whole timepoint unaddressable — the drawn
    frame fell outside its own scope and nothing was deleted."""
    ri = apply_ctx.state.review_image
    ri.delete_scope_wells = {"A01", "B02"}
    ri.delete_scope_tps = {"0"}
    _box(apply_ctx)
    assert ("A01", "1", "0", "1") in apply_ctx.runtime.review.included_overrides
    assert "in this frame (pixel overlap)" in apply_ctx.status[-1]


def test_reported_count_is_what_was_written_not_what_was_matched(apply_ctx):
    """Two CSV rows for one (fov, tp, nucleus_id) are two hits and one
    override. Reporting the hits would overstate the edit."""
    dup = _rows("B02", [("1", "0", "1", *_INSIDE), ("1", "0", "1", *_INSIDE)])
    apply_ctx.state.dataset.cache["B02"] = dup
    apply_ctx.state.review_image.delete_scope_wells = {"B02"}
    _box(apply_ctx)
    assert len(apply_ctx.runtime.review.included_overrides) == 1
    assert "1 cell(s) Included=0" in apply_ctx.status[-1]


def test_zero_area_box_and_missing_mask_are_not_silent(apply_ctx):
    from well_viewer.features.image_browser.review_image.callbacks import (
        apply_box_delete,
    )
    apply_box_delete(apply_ctx, 50, 50, 50, 50)
    assert "no area" in apply_ctx.status[-1]

    apply_ctx.runtime.review_image.label = SimpleNamespace(_mask_arr=None)
    _box(apply_ctx)
    assert "no segmentation mask" in apply_ctx.status[-1]
