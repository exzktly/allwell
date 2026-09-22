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
    frame_index_for,
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


# ── the file's own loop order ────────────────────────────────────────────────
#
# An ND2 numbers its sequences by a row-major walk of the experiment's loops
# **as they are nested in that file**, and NIS-Elements writes both nestings:
# a plate scanned position by position is ``["P", "T"]``, and a multipoint
# time-course that visits every position within a round is ``["T", "P"]``.
# A reader that assumes one reads every plane of the other from the wrong
# place, with the archive complete, correctly named and reported as a success.
#
# These are parametrised over the orders rather than written once, because the
# fixture could previously emit only ``"PT"`` — the order the code assumed —
# so every assertion in this file agreed with the bug by construction
# (CLAUDE.md §7.8 rule 2: build the fixture so the right and wrong answers
# differ).

_ORDERS = ["PT", "TP", "PTZ", "TPZ", "PZT"]


@pytest.mark.parametrize("order", _ORDERS)
def test_the_layout_records_the_loop_order_the_file_was_written_in(tmp_path, order):
    layout = read_nd2_layout(write_nd2(tmp_path / f"{order}.nd2", n_pos=2, n_t=3,
                                       n_c=1, n_z=2, loop_order=order))
    assert layout.axis_order == list(order)


@pytest.mark.parametrize("order", _ORDERS)
def test_every_coordinate_reads_its_own_plane_whatever_the_loop_order(
        tmp_path, order):
    """The assertion the old suite could not make.

    Each plane carries a value derived from all four of its coordinates, so a
    plane fetched from the wrong sequence is a *different number* rather than
    a different-looking image. Asserted as the value, never as "a plane came
    back" — an implementation reading one fixed frame for everything would
    satisfy the latter.
    """
    n_pos, n_t, n_c, n_z = 2, 3, 2, (2 if "Z" in order else 1)
    path = write_nd2(tmp_path / f"{order}.nd2", n_pos=n_pos, n_t=n_t, n_c=n_c,
                     n_z=n_z, loop_order=order, channel_names=["DAPI", "GFP"])
    layout = read_nd2_layout(path)

    with Nd2Reader(path, layout) as reader:
        for pos in range(n_pos):
            for t in range(n_t):
                for c in range(n_c):
                    plane = reader.read_projection(pos, t, c)
                    # read_projection max-projects, so the expected value is
                    # the brightest Z of that coordinate.
                    want = max(plate_value(pos, t, c, z) for z in range(n_z))
                    assert int(np.unique(plane)[0]) == want, (
                        f"{order}: position {pos}, timepoint {t}, channel {c}")


@pytest.mark.parametrize("order,extents,coord,expected", [
    # (loop order, (P, T, Z), (position, t, z), the sequence index)
    ("PT", (4, 3, 1), (2, 1, 0), 2 * 3 + 1),
    ("TP", (4, 3, 1), (2, 1, 0), 1 * 4 + 2),        # time is the outer loop
    ("PTZ", (4, 3, 2), (2, 1, 1), ((2 * 3) + 1) * 2 + 1),
    ("TPZ", (4, 3, 2), (2, 1, 1), ((1 * 4) + 2) * 2 + 1),
    ("PZT", (4, 3, 2), (2, 1, 1), ((2 * 2) + 1) * 3 + 1),
])
def test_the_sequence_index_is_built_from_the_order_not_from_an_assumption(
        tmp_path, order, extents, coord, expected):
    """Asserted against arithmetic done by hand from the loop order.

    Deliberately **not** against the reader's own formula, which is what the
    test this replaces did: it asserted ``position * size_t + t`` on a fixture
    built to that same rule, so it passed for the broken implementation on
    every file the fixture could not write.
    """
    n_pos, n_t, n_z = extents
    path = write_nd2(tmp_path / f"{order}.nd2", n_pos=n_pos, n_t=n_t, n_c=1,
                     n_z=n_z, loop_order=order)
    layout = read_nd2_layout(path)

    assert frame_index_for(layout, *coord) == expected
    with Nd2Reader(path, layout) as reader:
        assert reader.frame_index(*coord) == expected


# ── Z is a coordinate axis, so the stack has to be gathered ──────────────────

def test_a_z_stack_is_max_projected_across_its_own_sequences(tmp_path):
    """``AXIS.frame_coords()`` is ``{S, C, X, Y}`` — Z is never inside a frame.

    An implementation that read one frame and projected "whatever leads" was
    unreachable for any real ND2, so no projection happened at all: the plane
    returned was Z=0's. The fixture writes each Z a distinct value, so the
    brightest and the first are different numbers.
    """
    n_z = 4
    path = write_nd2(tmp_path / "z.nd2", n_pos=2, n_t=2, n_c=1, n_z=n_z,
                     loop_order="PTZ")
    layout = read_nd2_layout(path)
    assert layout.size_z == n_z

    with Nd2Reader(path, layout) as reader:
        plane = reader.read_projection(1, 1, 0)

    brightest = max(plate_value(1, 1, 0, z) for z in range(n_z))
    first = plate_value(1, 1, 0, 0)
    assert brightest != first, "fixture does not distinguish the two answers"
    assert int(np.unique(plane)[0]) == brightest


def test_the_projection_reads_one_frame_per_z_and_no_more(tmp_path):
    """A stack costs its own depth in reads, not the whole acquisition.

    Guards the shape as well as the value: an implementation calling
    ``asarray()`` would return the right pixels and read the entire file into
    memory once per plane.
    """
    path = write_nd2(tmp_path / "z.nd2", n_pos=3, n_t=2, n_c=1, n_z=3,
                     loop_order="PTZ")
    layout = read_nd2_layout(path)

    with Nd2Reader(path, layout) as reader:
        handle = reader._handle
        reads: list[int] = []
        real = handle.read_frame
        handle.read_frame = lambda i: (reads.append(i), real(i))[1]
        called = []
        handle.asarray = lambda *a, **k: called.append(1)
        reader.read_projection(2, 1, 0)

    assert len(reads) == 3, f"expected one read per Z plane, got {reads}"
    assert called == [], "read_projection must not call asarray()"


# ── the elapsed-hour scan uses the same index as the conversion ──────────────

@pytest.mark.parametrize("order", ["PT", "TP"])
def test_the_frame_times_are_the_timepoints_own_whatever_the_loop_order(
        tmp_path, order):
    """Sampling by ``t`` alone walked *positions* on a multipoint time-course.

    It then reported the stage's traverse across the plate as the experiment's
    whole duration — a twelve-hour timecourse named as a ninety-second one —
    with ``reasons`` empty, so nothing said the times were not the file's.
    """
    hours = [0.0, 4.0, 8.0, 12.0]
    path = write_nd2(tmp_path / f"{order}.nd2", n_pos=8, n_t=len(hours), n_c=1,
                     loop_order=order,
                     times_ms=[h * 3_600_000.0 for h in hours])

    scan = read_nd2_layout(path, read_times=True).time_scan

    assert scan is not None
    assert scan.reasons == []
    assert scan.hours_by_index == {i: h for i, h in enumerate(hours)}


# ── an RGB file is refused, not warned about ─────────────────────────────────

def test_an_rgb_file_is_refused_rather_than_written_as_nonsense():
    """Three samples per pixel is not one intensity to quantify.

    It used to be a warning only, so the plan stayed runnable — and
    ``_plane_from_frame`` then asked "is the leading axis a Z stack?" of the
    RGB sample axis, a predicate outside its domain, turning an 8x8 image into
    an 8x3 one. Asserted on the layout rather than through a file because the
    fixture ties its component count to the channel count and cannot write one.
    """
    from pipeline.nd2_read import Nd2Layout

    layout = Nd2Layout(n_positions=2, size_t=1, size_c=1, width=8, height=8,
                       dtype="uint8", channel_names=["C0"],
                       raw_channel_names=["C0"], is_rgb=True)

    report = layout.validation_report("plate.nd2")
    assert "RGB" in report
    assert "greyscale" in report


def test_a_frame_that_does_not_resolve_to_one_plane_is_refused(tmp_path):
    """Rather than reducing an axis the reader cannot name.

    The shape that reached this was an RGB frame, but the rule is general: a
    plausible image of the wrong pixels is the one output this converter must
    not be able to produce.
    """
    from pipeline.nd2_read import _plane_from_frame

    with pytest.raises(Nd2ReadError) as caught:
        _plane_from_frame(np.zeros((8, 8, 3), dtype="uint8"), 0, 1)

    assert "2-D plane" in str(caught.value)


@pytest.mark.parametrize("n_z", [1, 3])
def test_a_plane_outlives_the_handle_it_was_read_through(tmp_path, n_z):
    """``read_frame`` returns a view into the file's memory map.

    A plane handed back as-is therefore dies when the reader closes, and a
    caller that keeps one — which nothing forbids — segfaults with no Python
    traceback (CLAUDE.md §7.9's hardest class to diagnose). Parametrised over
    both depths on purpose: projecting a *stack* allocates a new array through
    ``np.maximum`` and was accidentally safe, so a single-plane file was the
    only one that could crash, which is the common case.

    Asserted as ``owndata`` **and** as the value read after the close, because
    the flag alone would pass for an array that owns the wrong pixels.
    """
    path = write_nd2(tmp_path / "z.nd2", n_pos=2, n_t=2, n_c=1, n_z=n_z,
                     loop_order="PTZ")
    layout = read_nd2_layout(path)

    with Nd2Reader(path, layout) as reader:
        plane = reader.read_projection(1, 1, 0)

    # Bound to a plain bool *before* asserting, and asserted before any pixel
    # is read. Against a regressed implementation the array is a dangling
    # view, so letting pytest build its failure message from the array itself
    # segfaults inside the reporting — which CI's JUnit gate can swallow, and
    # a guard that crashes instead of failing is a guard that reports nothing.
    owns_its_memory = bool(plane.flags.owndata)
    assert owns_its_memory, "the plane is a view into the closed file"
    want = max(plate_value(1, 1, 0, z) for z in range(n_z))
    assert int(np.unique(plane)[0]) == want


# ── a refusal that names which of four things went wrong ─────────────────────

@pytest.mark.parametrize("names,fragment", [
    # NIS multipoint naming: a field number appended to the well.
    (["A1_1", "A1_2", "B1_1", "B1_2"], "cannot read as a 96-well position"),
    # A descriptive prefix, which NIS also writes.
    (["Point A1", "Point A1", "Point B1", "Point B1"],
     "cannot read as a 96-well position"),
    # A 384-well plate: row I is outside A-H.
    (["I01", "I01", "J01", "J01"], "cannot read as a 96-well position"),
    # The acquisition comes back to a well later in the run.
    (["A01", "B01", "A01", "B01"], "returns to A01, B01 later in the run"),
    # Wells with different numbers of fields.
    (["A01", "A01", "A01", "B01"], "do not all take the same number of fields"),
])
def test_the_refusal_names_which_way_the_well_names_failed(tmp_path, names,
                                                           fragment):
    """All five used to produce one identical sentence, and for three of them
    it asserted two causes that had not occurred: it said the names were
    present and blamed field counts and repeats, when the real cause is that
    the name is not a well label at all. Two of those three carry a mapping
    the file genuinely holds and were sent to the grid regardless."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=len(names), n_t=1, n_c=1,
                     well_names=names)

    problem = read_nd2_layout(path).well_mapping_problem()

    assert fragment in problem
    assert read_nd2_layout(path).wells_from_positions() == ([], 0)


def test_a_plate_whose_names_are_wells_has_no_problem_to_report(tmp_path):
    """The other direction: a message must not appear for a file that maps."""
    path = write_nd2(tmp_path / "p.nd2", n_pos=4, n_t=1, n_c=1,
                     well_names=["C07", "C07", "A02", "A02"])
    layout = read_nd2_layout(path)

    assert layout.well_mapping_problem() == ""
    assert layout.wells_from_positions() == (["C07", "A02"], 2)


def test_channel_names_that_cannot_be_read_are_reported_not_only_survived(
        tmp_path):
    """Losing the names is a degradation, so it carries a reason.

    The archives are written ``c0``/``c1`` and those names reach the
    filenames and Analyze's channel tokens, so a run that lost them and said
    nothing is output that looks exactly like output from a file with no
    channel names of its own.
    """
    path = write_nd2(tmp_path / "p.nd2", n_pos=2, n_c=2, drop_probe=True)

    layout = read_nd2_layout(path)

    assert layout.channel_names == ["c0", "c1"]
    assert any("named by position" in w for w in layout.warnings), (
        f"nothing recorded the loss: {layout.warnings}")
