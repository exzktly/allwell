"""The ND2 fixture writes bytes the real parser reads.

This file is the evidence the rest of the ND2 tests rest on. ``tests/_nd2_fixture``
lays ND2 bytes down by hand, and the parser on the other side — the ``nd2``
package — is **not ours**, so a round trip through it is evidence about the
format rather than a stub agreeing with the code that built it (CLAUDE.md
§7.8). If this file goes red, nothing else in the ND2 suite means what it says.
"""

from __future__ import annotations

import numpy as np
import pytest

nd2 = pytest.importorskip("nd2")

from tests._nd2_fixture import plate_value, write_nd2  # noqa: E402


def test_the_parser_reads_back_the_plate_shape_that_was_written(tmp_path):
    path = write_nd2(tmp_path / "plate.nd2", n_pos=3, n_t=4, n_c=2)
    with nd2.ND2File(path) as f:
        # The **order** as well as the contents: ``sizes`` is ordered to match
        # ``shape`` and that order is what decides which sequence index holds
        # which image. Dict equality ignores it, so this assertion passed for a
        # file written in any nesting at all.
        expected = {"P": 3, "T": 4, "C": 2, "Y": 8, "X": 8}
        assert dict(f.sizes) == expected
        assert list(f.sizes) == list(expected)
        assert f.dtype == np.dtype("uint16")


def test_the_well_names_survive_the_round_trip(tmp_path):
    """Written into each position's ``dPosName``, which is where a Nikon plate
    scan puts the well it was taken from — the ND2 analogue of a CZI scene
    name. ``C07, A02, H11`` rather than ``A01, A02, A03`` so that scene order,
    sorted order and plate order all differ."""
    path = write_nd2(tmp_path / "plate.nd2", n_pos=3,
                     well_names=["C07", "A02", "H11"])
    with nd2.ND2File(path) as f:
        xy = next(loop for loop in f.experiment if loop.type == "XYPosLoop")
        assert [pt.name for pt in xy.parameters.points] == ["C07", "A02", "H11"]


def test_the_channel_names_survive_the_round_trip(tmp_path):
    path = write_nd2(tmp_path / "plate.nd2", n_c=2, channel_names=["DAPI", "GFP"])
    with nd2.ND2File(path) as f:
        assert [c.channel.name for c in f.metadata.channels] == ["DAPI", "GFP"]


def test_every_plane_reads_back_at_its_own_coordinate(tmp_path):
    """The property the mapping tests depend on: each plane is filled with a
    value derived from all three of its coordinates, so a plane read from the
    wrong place is a *different number* rather than a different-looking image.

    This is also the check that caught the one thing reading the format could
    not have told me. The first fixture wrote each frame's pixels straight
    after its chunk header; the file opened, reported the right shape, and read
    every plane as zeros, because the reader locates pixels at a fixed 4096
    bytes from the chunk's own start."""
    path = write_nd2(tmp_path / "plate.nd2", n_pos=3, n_t=4, n_c=2)
    with nd2.ND2File(path) as f:
        array = f.asarray()
    for pos in range(3):
        for t in range(4):
            for c in range(2):
                value = int(np.unique(array[pos, t, c])[0])
                assert value == plate_value(pos, t, c), (
                    f"position {pos}, timepoint {t}, channel {c}")


def test_the_acquisition_times_survive_the_round_trip(tmp_path):
    """Four hours apart, written into the frame-times cache the reader reads
    them from — not into each frame's own metadata, which it ignores."""
    hours = [0.0, 4.0, 8.0, 12.0]
    path = write_nd2(tmp_path / "plate.nd2", n_pos=2, n_t=4,
                     times_ms=[h * 3_600_000 for h in hours])
    with nd2.ND2File(path) as f:
        read = [f.frame_metadata(i).channels[0].time.relativeTimeMs / 3_600_000
                for i in range(4)]
    assert read == pytest.approx(hours)


def test_a_file_with_no_times_reads_without_them(tmp_path):
    """The case the converter has to fall back from, and it must not raise."""
    path = write_nd2(tmp_path / "plate.nd2", n_pos=2, n_t=3, times_ms=None)
    with nd2.ND2File(path) as f:
        assert f.sizes["T"] == 3
