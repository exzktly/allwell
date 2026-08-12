"""The Image Table draws only the cells the analysis counts.

A pipeline mask holds every object that was segmented, including cells deleted
by hand on the Segmentation tab and cells a crop excluded. The Image Table used
to outline all of them and paint all of them white in its Binary (mask) view,
so a curated-away cell looked segmented and counted while every plot, statistic
and export had already dropped it — and nothing on screen said which was right.

The filter is applied once, where the mask is loaded, so the boundary overlay,
the Binary (mask) view, the Binary (threshold) view and the mask channel's own
pass all follow from it. These tests assert on the array that seam returns and
on the pixels composed from it, not on the inclusion decision behind them: a
test that read the excluded set back would pass happily while the grid stayed
wrong.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from well_viewer.data.inclusion import (
    drop_excluded_labels, excluded_nids_by_frame, excluded_nids_for_frame,
)


def _df(rows):
    """rows: (nucleus_id, fov, timepoint, Included, QC_Pass, Crop_Pass)."""
    return pd.DataFrame(
        rows, columns=["nucleus_id", "fov", "timepoint",
                       "Included", "QC_Pass", "Crop_Pass"])


def _ctx(cache, *, version=1, has_slice=True):
    state = SimpleNamespace(
        dataset=SimpleNamespace(
            cache=cache,
            well_paths={k: f"/tmp/{k}.csv" for k in cache},
            fov_integrity={},
        ),
        feature_slice=lambda name: (
            SimpleNamespace(override_version=version)
            if has_slice and name == "review_image" else None),
    )
    return SimpleNamespace(state=state)


# ── which cells are dropped ──────────────────────────────────────────────────
def test_manual_delete_crop_and_qc_all_drop_a_cell():
    df = _df([
        (1, "1", "0", 1, 1, 1),     # counted
        (2, "1", "0", 0, 1, 1),     # deleted by hand
        (3, "1", "0", 1, 1, 0),     # outside the crop
        (4, "1", "0", 1, 0, 1),     # failed QC
    ])
    ctx = _ctx({"A01": df})
    assert excluded_nids_for_frame(ctx, "A01", "1", "0") == frozenset({2, 3, 4})


def test_exclusions_are_keyed_by_frame_not_by_well():
    """The same nucleus id exists in every field. Dropping it everywhere
    because it was deleted in one would erase cells the user never touched."""
    df = _df([
        (1, "1", "0", 0, 1, 1),
        (1, "2", "0", 1, 1, 1),
        (1, "1", "24", 1, 1, 1),
    ])
    ctx = _ctx({"A01": df})
    assert excluded_nids_for_frame(ctx, "A01", "1", "0") == frozenset({1})
    assert excluded_nids_for_frame(ctx, "A01", "2", "0") == frozenset()
    assert excluded_nids_for_frame(ctx, "A01", "1", "24") == frozenset()


def test_frame_tokens_are_matched_in_canonical_form():
    """The CSV spells its tokens however the pipeline wrote them; the Image
    Table cell carries the raw filename token. `001`/`1` and `T24`/`24` are the
    same frame, or the filter would silently match nothing."""
    df = _df([(7, "001", "T24", 0, 1, 1)])
    ctx = _ctx({"A01": df})
    assert excluded_nids_for_frame(ctx, "A01", "1", "24") == frozenset({7})


def test_nothing_excluded_is_the_cheap_path():
    df = _df([(1, "1", "0", 1, 1, 1), (2, "1", "0", 1, 1, 1)])
    ctx = _ctx({"A01": df})
    assert excluded_nids_by_frame(ctx, "A01") == {}


def test_memo_is_rebuilt_when_the_inclusion_version_moves():
    """Deleting a cell must show up on the next render. The memo is keyed by
    the counter every inclusion edit bumps."""
    df = _df([(1, "1", "0", 1, 1, 1)])
    cache = {"A01": df}
    ctx = _ctx(cache, version=1)
    assert excluded_nids_for_frame(ctx, "A01", "1", "0") == frozenset()
    df.loc[0, "Included"] = 0
    assert excluded_nids_for_frame(ctx, "A01", "1", "0") == frozenset()  # memo
    ctx = _ctx(cache, version=2)
    assert excluded_nids_for_frame(ctx, "A01", "1", "0") == frozenset({1})


def test_without_an_inclusion_counter_nothing_is_memoised():
    """A build with no Review-Image slice has no counter to key on, so the map
    is recomputed rather than served stale."""
    df = _df([(1, "1", "0", 1, 1, 1)])
    ctx = _ctx({"A01": df}, has_slice=False)
    assert excluded_nids_for_frame(ctx, "A01", "1", "0") == frozenset()
    df.loc[0, "Included"] = 0
    assert excluded_nids_for_frame(ctx, "A01", "1", "0") == frozenset({1})


# ── the label filter ─────────────────────────────────────────────────────────
def test_drop_excluded_labels_zeroes_only_those_labels():
    labels = np.array([[0, 1, 1], [2, 2, 3]], dtype=np.int32)
    out = drop_excluded_labels(labels, frozenset({2}))
    assert out.tolist() == [[0, 1, 1], [0, 0, 3]]


def test_drop_excluded_labels_is_a_no_op_with_nothing_to_drop():
    labels = np.array([[1, 2]], dtype=np.int32)
    assert drop_excluded_labels(labels, frozenset()) is labels
    assert drop_excluded_labels(None, frozenset({1})) is None


# ── the seam the whole tab reads through ─────────────────────────────────────
def test_load_mask_array_drops_excluded_cells(monkeypatch):
    """Asserted at the boundary: every mask the tab draws from — boundaries,
    Binary (mask), Binary (threshold), the mask channel's LUT pass — comes back
    through this one call."""
    from well_viewer.features.image_browser.image_table import image_compose

    raw = np.array([[1, 1, 0], [2, 2, 3]], dtype=np.int32)
    monkeypatch.setattr(image_compose, "load_mask_array",
                        lambda *a, **k: raw.copy())
    df = _df([
        (1, "1", "0", 1, 1, 1),
        (2, "1", "0", 1, 1, 0),     # cropped out
        (3, "1", "0", 0, 1, 1),     # deleted
    ])
    ctx = _ctx({"A01": df})
    out = image_compose._load_mask_array(ctx, {}, "A01", "0", "1")
    assert out.tolist() == [[1, 1, 0], [0, 0, 0]]


def test_excluded_cells_lose_their_boundary(monkeypatch):
    """The pixels, not the decision: an excluded cell draws no outline."""
    from well_viewer.features.image_browser.image_table import image_compose

    labels = np.zeros((8, 8), dtype=np.int32)
    labels[1:4, 1:4] = 1      # kept
    labels[5:8, 5:8] = 2      # excluded
    monkeypatch.setattr(image_compose, "load_mask_array",
                        lambda *a, **k: labels.copy())
    df = _df([(1, "1", "0", 1, 1, 1), (2, "1", "0", 1, 1, 0)])
    ctx = _ctx({"A01": df})

    def _outline_pixels(mask):
        rgb = image_compose._compose_image_table_rgb(
            np.zeros((8, 8), dtype=np.float32), mask,
            lo=0, hi=1, cmap_name="", is_overlay=False,
            show_boundaries=True)
        return np.all(rgb == [255, 255, 255], axis=-1)

    both = _outline_pixels(labels)
    filtered = _outline_pixels(
        image_compose._load_mask_array(ctx, {}, "A01", "0", "1"))
    assert both[1:4, 1:4].any() and both[5:8, 5:8].any()
    assert filtered[1:4, 1:4].any()          # the kept cell still outlined
    assert not filtered[5:8, 5:8].any()      # the excluded one is gone
