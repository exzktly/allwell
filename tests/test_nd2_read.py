"""Reading a Nikon ND2's shape, wells, channels and timing.

The adapter over the ``nd2`` package. Every case here goes through a file
``tests/_nd2_fixture`` wrote and the real library read back, so what is being
tested is the adapter *and* the understanding of the format underneath it —
not a stub of the library agreeing with the code that calls it (CLAUDE.md
§7.8).

Each plane the fixture writes carries a value derived from all three of its
coordinates, so a plane read from the wrong place is a *different number*
rather than a different-looking image. That is what caught the one defect
reading the library's own source could not have prevented: its
``_actual_frame_shape`` documents ``(height, width, channels, components)``,
and ``read_frame`` returns the channels **leading**, so indexing the last axis
gave a plane of the right shape and dtype holding channel 0's pixels under
channel 1's name.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("nd2")

from pipeline.nd2_read import (  # noqa: E402
    Nd2ReadError,
    Nd2Reader,
    read_nd2_layout,
)
from tests._nd2_fixture import plate_value, write_nd2  # noqa: E402


# ── shape and names ──────────────────────────────────────────────────────────

def test_the_layout_reports_the_plate_the_file_holds(tmp_path):
    path = write_nd2(tmp_path / "p.nd2", n_pos=3, n_t=4, n_c=2,
                     channel_names=["DAPI", "GFP"])
    layout = read_nd2_layout(path)
    assert (layout.n_positions, layout.size_t, layout.size_c) == (3, 4, 2)
    assert layout.channel_names == ["DAPI", "GFP"]
    assert layout.dtype == "uint16"
    assert layout.total_images == 3 * 4 * 2
    assert layout.validation_report("p.nd2") == ""


def test_a_single_channel_file_still_reports_one_channel(tmp_path):
    """``ND2File.sizes`` drops a singleton C axis outright, so a reader taking
    the count from that dict alone reports zero channels for an ordinary
    single-stain plate."""
    layout = read_nd2_layout(write_nd2(tmp_path / "p.nd2", n_pos=2, n_c=1))
    assert layout.size_c == 1
    assert len(layout.channel_names) == 1


# ── wells from the positions' own names ──────────────────────────────────────

def test_one_position_per_well_reads_one_field_each(tmp_path):
    path = write_nd2(tmp_path / "p.nd2", n_pos=3,
                     well_names=["C07", "A02", "H11"])
    assert read_nd2_layout(path).wells_from_positions() == (
        ["C07", "A02", "H11"], 1)


def test_positions_naming_one_well_are_that_well_s_fields(tmp_path):
    """How a Nikon plate scan says "two fields of view in each well"."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=4,
                     well_names=["C07", "C07", "A02", "A02"])
    assert read_nd2_layout(path).wells_from_positions() == (["C07", "A02"], 2)


def test_a_well_with_a_different_number_of_fields_is_refused(tmp_path):
    """Three fields in one well and one in the next cannot lay over a plate —
    every well has to carry the same fields for the dataset to line up."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=4,
                     well_names=["C07", "C07", "C07", "A02"])
    assert read_nd2_layout(path).wells_from_positions() == ([], 0)


def test_an_unnamed_position_refuses_the_whole_mapping(tmp_path):
    """All or nothing. A partial mapping is the worst of the three outcomes:
    the named positions land on their real wells and the rest wherever the
    grid put them, producing a plate that is right in the places a user is
    most likely to spot-check."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=3, well_names=["C07", "", "H11"])
    assert read_nd2_layout(path).wells_from_positions() == ([], 0)


def test_a_well_visited_twice_in_one_run_is_refused(tmp_path):
    """Grouped over *runs* of equal names, not over the set of them: a plate
    that returns to a well later is two visits, and the second visit's images
    would overwrite the first's."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=4,
                     well_names=["C07", "A02", "C07", "A02"])
    assert read_nd2_layout(path).wells_from_positions() == ([], 0)


def test_two_channels_reducing_to_one_token_are_refused_by_name(tmp_path):
    """Both would be written to one member name and only the last would be
    readable, while the run reported writing both."""
    path = write_nd2(tmp_path / "p.nd2", n_c=2, channel_names=["488 nm", "488nm"])
    report = read_nd2_layout(path).validation_report("p.nd2")
    assert "reduce to the filename token" in report
    assert "488 nm" in report and "488nm" in report


# ── pixels ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n_c", [1, 2, 3])
def test_every_coordinate_reads_its_own_plane(tmp_path, n_c):
    """The desync check, parametrised over the channel count because the
    channel axis is identified by its length. See this module's docstring for
    what it caught."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=3, n_t=4, n_c=n_c)
    layout = read_nd2_layout(path)
    with Nd2Reader(path, layout) as reader:
        for pos in range(3):
            for t in range(4):
                for c in range(n_c):
                    plane = reader.read_projection(pos, t, c)
                    assert int(np.unique(plane)[0]) == plate_value(pos, t, c), (
                        f"position {pos}, timepoint {t}, channel {c}")


def test_a_plane_is_read_one_frame_at_a_time(tmp_path, monkeypatch):
    """Not by reading the file and slicing it. The first version called
    ``asarray()`` per plane, which is correct and loads the whole acquisition
    into memory once per image — on a plate of any size that is the difference
    between a conversion and a machine that stops responding."""
    import nd2

    path = write_nd2(tmp_path / "p.nd2", n_pos=2, n_t=2, n_c=1)
    layout = read_nd2_layout(path)
    calls: list[str] = []
    real_asarray = nd2.ND2File.asarray
    monkeypatch.setattr(
        nd2.ND2File, "asarray",
        lambda self, *a, **k: (calls.append("asarray"), real_asarray(self, *a, **k))[1])
    with Nd2Reader(path, layout) as reader:
        reader.read_projection(1, 1, 0)
    assert calls == [], "read_projection read the whole file"


def test_the_frame_index_walks_one_position_s_series_contiguously(tmp_path):
    """The position loop is outer and the time loop nests inside it, so
    position 1's timepoints follow position 0's. ``scan_timepoint_hours``
    samples the first position's series and depends on this agreeing."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=3, n_t=4)
    layout = read_nd2_layout(path)
    with Nd2Reader(path, layout) as reader:
        assert [reader.frame_index(0, t) for t in range(4)] == [0, 1, 2, 3]
        assert [reader.frame_index(1, t) for t in range(4)] == [4, 5, 6, 7]


# ── timing ───────────────────────────────────────────────────────────────────

def test_the_elapsed_hours_come_from_the_frame_times(tmp_path):
    path = write_nd2(tmp_path / "p.nd2", n_pos=3, n_t=4,
                     times_ms=[0, 4 * 3_600_000, 8 * 3_600_000, 12 * 3_600_000])
    scan = read_nd2_layout(path, read_times=True).time_scan
    assert scan.reasons == []
    assert scan.hours_by_index == pytest.approx({0: 0.0, 1: 4.0, 2: 8.0, 3: 12.0})


def test_the_series_is_measured_from_its_own_first_reading(tmp_path):
    """A file whose run started an hour into the session still begins at 0 h
    elapsed — the tokens are elapsed times, not clock times."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=2, n_t=3,
                     times_ms=[3_600_000, 2 * 3_600_000, 3 * 3_600_000])
    scan = read_nd2_layout(path, read_times=True).time_scan
    assert scan.hours_by_index == pytest.approx({0: 0.0, 1: 1.0, 2: 2.0})


def test_a_file_with_no_frame_times_declines_with_a_reason(tmp_path):
    """Never empty-handed: a plate named by position with nothing said about
    it is indistinguishable from one whose writer recorded no times."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=2, n_t=3, times_ms=None)
    scan = read_nd2_layout(path, read_times=True).time_scan
    assert scan.hours_by_index == {}
    assert scan.reasons == ["the file records no acquisition time for its frames"], (
        "the reason has to be one a user can act on — the library's own "
        "IndexError reads 'list index out of range'")


def test_a_layout_read_without_times_carries_none(tmp_path):
    assert read_nd2_layout(write_nd2(tmp_path / "p.nd2")).time_scan is None


# ── the library's own sharp edge ─────────────────────────────────────────────

def test_a_file_whose_planes_carry_no_probe_still_reads(tmp_path):
    """``nd2`` 0.9.0's ``_get_excitation`` binds its ``excitation`` local only
    inside ``if probe:`` and then reads it unconditionally, so
    ``ND2File.metadata`` raises ``UnboundLocalError`` on a plane with no
    fluorescent-probe block. Real NIS files carry one and a user's file is not
    obliged to, and refusing such a plate would turn a library bug into a file
    that cannot be converted. The channel *names* are what is lost, and
    positional tokens are names a run can carry."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=2, n_c=2, drop_probe=True)
    layout = read_nd2_layout(path)
    assert layout.size_c == 2
    assert layout.channel_names == ["c0", "c1"]
    assert layout.validation_report("p.nd2") == ""


def test_a_file_that_only_looks_like_an_nd2_is_refused_as_one(tmp_path):
    """``nd2`` parses lazily, so this is not covered by guarding the open.

    ``ND2File(path)`` succeeds on anything carrying the right magic bytes and
    the ``ValueError`` for a broken header comes out of the first attribute
    read instead — past a caller that catches this module's own error, which
    is the boundary this module exists to hold. A truncated or half-written
    file is exactly the shape that hits it.
    """
    broken = tmp_path / "broken.nd2"
    broken.write_bytes(b"\xda\xce\xbe\x0a" + b"\0" * 200)

    with pytest.raises(Nd2ReadError) as caught:
        read_nd2_layout(broken)

    assert "broken.nd2" in str(caught.value)
