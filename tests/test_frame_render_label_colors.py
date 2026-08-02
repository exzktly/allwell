"""Binary mode's per-label colouring.

Painting every cell the same white makes two nuclei that share a border into one
white shape. "Did the segmentation split those two?" is the main question the
Playground is asked, and an all-white fill is the one rendering that cannot
answer it. Colouring each label removes the ambiguity — provided the colours are
*stable* (a cell that changes colour between redraws reads as a different cell)
and provided the branch that has no labels does not pretend to have them.

Pure numpy in, numpy out — no Qt, no app fixture, so this is fast-lane.
"""

from __future__ import annotations

import numpy as np

from well_viewer.core.frame_render import build_frame_rgb, label_color_lut


def _two_touching_cells():
    """Labels 1 and 2 sharing a border — the case white-on-white destroys."""
    lab = np.zeros((16, 16), dtype=np.int32)
    lab[4:12, 4:8] = 1
    lab[4:12, 8:12] = 2
    return lab


def _frame():
    return np.full((16, 16), 500.0, dtype=np.float32)


def test_touching_labels_are_drawn_in_different_colours():
    lab = _two_touching_cells()
    rgb = build_frame_rgb(_frame(), lab, binary=True, label_colors=True)
    one = tuple(rgb[6, 6])
    two = tuple(rgb[6, 10])
    assert one != two
    assert one != (0, 0, 0) and two != (0, 0, 0)


def test_the_palette_is_stable_between_renders():
    """A fixed function of the label id, with no RNG: the same cell must be the
    same colour on every redraw or it reads as a different cell."""
    lab = _two_touching_cells()
    first = build_frame_rgb(_frame(), lab, binary=True, label_colors=True)
    second = build_frame_rgb(_frame(), lab, binary=True, label_colors=True)
    assert np.array_equal(first, second)


def test_consecutive_ids_are_far_apart_in_the_palette():
    """Segmenters hand touching nuclei consecutive ids, so those are exactly the
    pairs that must not come out similar."""
    lut = label_color_lut(12).astype(np.int32)
    gaps = np.abs(np.diff(lut[1:], axis=0)).sum(axis=1)
    assert gaps.min() > 60


def test_background_stays_black():
    lab = _two_touching_cells()
    rgb = build_frame_rgb(_frame(), lab, binary=True, label_colors=True)
    assert tuple(rgb[0, 0]) == (0, 0, 0)
    assert tuple(label_color_lut(4)[0]) == (0, 0, 0)


def test_colouring_off_is_the_old_all_white_fill():
    lab = _two_touching_cells()
    rgb = build_frame_rgb(_frame(), lab, binary=True, label_colors=False)
    assert tuple(rgb[6, 6]) == (255, 255, 255)
    assert tuple(rgb[6, 10]) == (255, 255, 255)


def test_only_the_cells_that_pass_the_threshold_are_coloured():
    lab = _two_touching_cells()
    rgb = build_frame_rgb(_frame(), lab, binary=True, label_colors=True,
                          above_ids={1}, threshold=100.0)
    assert tuple(rgb[6, 6]) != (0, 0, 0)
    assert tuple(rgb[6, 10]) == (0, 0, 0)


def test_the_per_pixel_fallback_has_no_labels_to_colour():
    """With no usable mask the fill is a pixel threshold. There is nothing to
    colour, and inventing colours would imply a per-cell distinction that this
    branch never made."""
    arr = np.zeros((16, 16), dtype=np.float32)
    arr[4:12, 4:12] = 900.0
    rgb = build_frame_rgb(arr, None, binary=True, label_colors=True,
                          threshold=100.0)
    assert tuple(rgb[6, 6]) == (255, 255, 255)


def test_a_mask_that_does_not_fit_the_frame_is_not_coloured():
    """Same rule: the mismatched mask is unusable, so the render falls through to
    the pixel threshold and must not claim per-label colours."""
    arr = np.full((16, 16), 900.0, dtype=np.float32)
    wrong = np.ones((8, 8), dtype=np.int32)
    rgb = build_frame_rgb(arr, wrong, binary=True, label_colors=True,
                          threshold=100.0)
    assert tuple(rgb[6, 6]) == (255, 255, 255)
