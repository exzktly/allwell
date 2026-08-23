"""A position with fewer planes is refused, not padded with its last frame.

`_extract_plane` clamped an out-of-range index with `min(t, shape - 1)`, and
the explode loop asks every position for `t` in `range(layout.size_t)` where
`size_t` is read from **series[0] only**. So a position that legitimately holds
fewer timepoints — an acquisition stopped early at one well, or a shorter
`<Image>` in the OME-XML — had its last frame written out again under every
later timepoint's name.

The output is a well whose `t1` is byte-identical to its `t0`, quantified as a
real second timepoint: a fold-change over time of exactly 1.000, which nothing
downstream can tell apart from a measured null. `_packed_plane` next door
already raised for a page it could not find; this half clamped.

None of the existing checks could see it. The file is not damaged, declares
nothing false, and has no missing planes — it is honestly describing two
differently shaped Images. `validation_report()` returned `""` and
`missing_planes` was 0. The module already collected the evidence, in
`axes_per_series`, and read it nowhere outside test fixtures.
"""
from __future__ import annotations

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from pipeline.ome_explode import _extract_plane, read_ome_layout  # noqa: E402


# ── the clamp itself ────────────────────────────────────────────────────────

def test_a_timepoint_past_the_end_raises_rather_than_repeating_the_last():
    arr = np.arange(2 * 4 * 4, dtype=np.uint16).reshape(2, 4, 4)

    assert _extract_plane(arr, "TYX", 0, 0)[0, 0] == 0
    assert _extract_plane(arr, "TYX", 1, 0)[0, 0] == 16

    with pytest.raises(ValueError, match="timepoint 2"):
        _extract_plane(arr, "TYX", 2, 0)


def test_a_channel_past_the_end_raises():
    arr = np.arange(2 * 4 * 4, dtype=np.uint16).reshape(2, 4, 4)

    with pytest.raises(ValueError, match="channel 2"):
        _extract_plane(arr, "CYX", 0, 2)


def test_a_series_with_no_time_axis_refuses_a_later_timepoint():
    """The same duplication by a different route.

    A series whose axes hold no `T` reaches neither index branch, so every
    timepoint used to resolve to the same single plane.
    """
    arr = np.arange(4 * 4, dtype=np.uint16).reshape(4, 4)

    assert _extract_plane(arr, "YX", 0, 0)[0, 0] == 0
    with pytest.raises(ValueError, match="no T axis"):
        _extract_plane(arr, "YX", 1, 0)


# ── the up-front refusal ────────────────────────────────────────────────────

def _ragged_ome(path, *, t_first: int = 2, t_second: int = 1):
    """Two stage positions, the second holding fewer timepoints.

    Written by `tifffile`'s own OME writer so the file is exactly what an
    acquisition stopped early at one well produces: a valid OME-TIFF whose two
    `<Image>` elements honestly declare different shapes.
    """
    first = np.stack([np.full((8, 8), 100 + t, np.uint16) for t in range(t_first)])
    second = np.stack([np.full((8, 8), 900 + t, np.uint16) for t in range(t_second)])
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        tw.write(first, metadata={"axes": "TYX"})
        tw.write(second, metadata={"axes": "TYX"})
    return path


def test_a_ragged_file_is_named_before_a_single_archive_is_written(tmp_path):
    path = _ragged_ome(tmp_path / "ragged.ome.tif")
    layout = read_ome_layout(path)

    assert layout.missing_planes == 0, (
        "the fixture should be a *sound* file by every existing check — "
        "that is what made this defect invisible")

    report = layout.validation_report("ragged.ome.tif")
    assert report, (
        "a file whose positions disagree about their timepoint count was "
        "reported as sound")
    assert "do not agree" in report and "timepoints" in report, report
    assert "position 2" in report, (
        f"the report must name which position is short: {report}")


def test_a_uniform_file_is_still_accepted(tmp_path):
    """The refusal must not reject the ordinary plate."""
    path = _ragged_ome(tmp_path / "uniform.ome.tif", t_first=2, t_second=2)
    layout = read_ome_layout(path)

    assert layout._ragged_positions() is None, layout.sizes_per_series
    assert "do not agree" not in layout.validation_report("uniform.ome.tif")


def test_positions_with_different_stack_depths_are_still_accepted():
    """Z is collapsed, so a deeper stack cannot duplicate a plane.

    `_extract_plane` reaches `elif ax in ("Z", "Y", "X"): continue` and
    max-projects the whole stack, and the explode loop indexes only T and C.
    Per-position autofocus routinely produces different depths; blocking such a
    file refused a sound plate and named a reason that was not true.
    """
    from pipeline.ome_explode import OmeLayout

    layout = OmeLayout(
        n_positions=2, size_t=2, size_c=1, size_z=5,
        channel_names=["DAPI"], timepoint_tokens=["t0", "t1"],
        axes_per_series=["TZYX", "TZYX"],
        sizes_per_series=[{"T": 2, "C": 1, "Z": 5}, {"T": 2, "C": 1, "Z": 3}],
    )

    assert layout._ragged_positions() is None, (
        "positions differing only in stack depth were refused")
    assert "do not agree" not in layout.validation_report("plate.ome.tif")


def test_a_short_channel_count_is_still_refused():
    """The other half of the same tuple, so narrowing it did not empty it."""
    from pipeline.ome_explode import OmeLayout

    layout = OmeLayout(
        n_positions=2, size_t=1, size_c=3, size_z=1,
        channel_names=["DAPI", "GFP", "RFP"], timepoint_tokens=["t0"],
        axes_per_series=["CYX", "CYX"],
        sizes_per_series=[{"T": 1, "C": 3, "Z": 1}, {"T": 1, "C": 2, "Z": 1}],
    )

    report = layout.validation_report("plate.ome.tif")
    assert "do not agree" in report and "channels" in report, report
