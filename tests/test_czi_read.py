"""Reading a Zeiss CZI (``pipeline.czi_read``).

Every case here is written against **real ZISRAW bytes** laid down by
``tests/_czi_fixture``, not against a mocked parser. That is the whole point:
the module's job is to agree with a binary format, and a test that stubs the
format has confirmed only that the code agrees with itself.

Each plane's pixel value encodes every one of its coordinates
(``scene, tile, t, c, z`` → ``s*10000 + m*1000 + t*100 + c*10 + z``), so a
plane read from the wrong place is a *different number* rather than a
different-looking image — which is what lets these assert the indexing rather
than the shapes.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests._czi_fixture import (
    Tile,
    plate_metadata_xml,
    plate_tiles,
    write_czi,
)
from pipeline.czi_read import (
    CziReader,
    CziReadError,
    _read_file_header,
    _read_metadata_xml,
    read_czi_layout,
    well_from_scene_name,
)


def _expected(scene: int, tile: int, t: int, c: int, z: int) -> int:
    """The value ``plate_tiles`` writes into the plane at these coordinates."""
    return (((scene * 10 + tile) * 10 + t) * 10 + c) * 10 + z


# ── the layout ───────────────────────────────────────────────────────────────

def test_layout_reports_the_dimensions_the_file_actually_stores(tmp_path):
    path = write_czi(
        tmp_path / "plate.czi",
        plate_tiles(wells=3, fovs=2, timepoints=2, channels=2, z=3,
                    shape=(4, 6)),
        xml=plate_metadata_xml(scene_wells={0: "A01", 1: "A02", 2: "B05"},
                               channels=["DAPI", "GFP"]),
    )
    layout = read_czi_layout(path)

    assert layout.scenes == [0, 1, 2]
    assert layout.tiles == {0: [0, 1], 1: [0, 1], 2: [0, 1]}
    assert (layout.size_t, layout.size_c, layout.size_z) == (2, 2, 3)
    assert (layout.width, layout.height) == (6, 4)
    assert layout.dtype == "uint16"
    assert layout.n_positions == 6
    assert layout.fovs_per_scene == 2
    assert layout.channel_names == ["DAPI", "GFP"]
    assert layout.timepoint_tokens == ["t0", "t1"]
    assert layout.wells_from_metadata() == ["A01", "A02", "B05"]
    assert layout.validation_report("plate.czi") == ""


def test_a_plane_reads_back_at_the_coordinates_it_was_written_to(tmp_path):
    """The indexing, asserted as values rather than as shapes.

    A reader that returned the *first* tile for every request would pass a
    shape/dtype check on every one of these; it fails on the first value.
    """
    path = write_czi(
        tmp_path / "plate.czi",
        plate_tiles(wells=2, fovs=2, timepoints=2, channels=2, z=2),
    )
    layout = read_czi_layout(path)
    with CziReader(path, layout) as reader:
        for scene in (0, 1):
            for tile in (0, 1):
                for t in (0, 1):
                    for c in (0, 1):
                        for z in (0, 1):
                            plane = reader.read_plane(scene, tile, t, c, z)
                            assert plane[0, 0] == _expected(scene, tile, t, c, z)


def test_a_z_stack_is_collapsed_by_maximum_intensity(tmp_path):
    """The projection takes the brightest plane, not the first or the last.

    The fixture's values increase with Z, so "take the last plane" would also
    pass — hence the extra tile below, whose *middle* Z is the bright one.
    """
    tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=1, z=3)
    # Overwrite the stack: dim, bright, dim. Only a max sees the 900.
    for tile, value in zip(tiles, (5, 900, 7)):
        tile.pixels = np.full_like(tile.pixels, value)
    path = write_czi(tmp_path / "stack.czi", tiles)
    with CziReader(path) as reader:
        assert reader.read_projection(0, 0, 0, 0).max() == 900


@pytest.mark.parametrize("tell", [True, "type", "stored"])
def test_pyramid_subblocks_are_not_mistaken_for_acquisition_tiles(tmp_path, tell):
    """A CZI written for viewing carries downsampled copies beside the real
    tiles. Counted as acquisition they would double the plane count and, read
    as data, would be a correctly named image of the wrong thing.

    Parametrised over the two tells *separately*, not only over a tile carrying
    both. A writer may set ``PyramidType`` and leave ``StoredSize`` alone, or
    the reverse, and a reader needing both would keep every copy while a test
    using a tile that carries both would go on passing.
    """
    tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=1)
    tiles.append(Tile(pixels=np.zeros((4, 6), np.uint16), pyramid=tell))
    path = write_czi(tmp_path / "pyramid.czi", tiles)
    layout = read_czi_layout(path)

    assert layout.n_blocks == 1
    assert layout.total_images == 1
    assert layout.validation_report("pyramid.czi") == ""


def test_the_file_is_still_readable_without_its_subblock_directory(tmp_path):
    """The directory is written last, so an acquisition killed part-way leaves
    every tile it wrote and no index to them. The tiles are self-describing, so
    the plate is recovered by walking the segments — and the layout says it had
    to, because that is a fact about the file the user should hear."""
    path = write_czi(
        tmp_path / "killed.czi",
        plate_tiles(wells=2, fovs=1, timepoints=1, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "C01", 1: "C02"},
                               channels=["Cy5"]),
        omit_directory=True,
    )
    layout = read_czi_layout(path)

    assert layout.n_blocks == 2
    assert layout.from_directory is False
    assert layout.wells_from_metadata() == ["C01", "C02"]
    assert any("scanned tile by tile" in w for w in layout.warnings)
    with CziReader(path, layout) as reader:
        assert reader.read_plane(1, 0, 0, 0, 0)[0, 0] == _expected(1, 0, 0, 0, 0)


# ── where the wells come from ────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("A1", "A01"),
    ("a1", "A01"),
    ("H12", "H12"),
    ("P1-A1", "A01"),
    ("Scene-1-B07", "B07"),
    ("B7-01", "B07"),
    ("Scene-1", ""),
    ("", ""),
    ("A13", ""),            # column 13 is off a 96-well plate
    ("SPECIMENC12", ""),    # a well spelled inside a longer word is not a well
])
def test_a_scene_name_resolves_to_a_well_only_when_it_really_names_one(
        name, expected):
    assert well_from_scene_name(name) == expected


def test_a_well_named_only_on_the_scene_element_is_still_found(tmp_path):
    path = write_czi(
        tmp_path / "named.czi",
        plate_tiles(wells=2, fovs=1, timepoints=1, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "D04", 1: "D05"},
                               channels=["GFP"], scene_name_only=True),
    )
    assert read_czi_layout(path).wells_from_metadata() == ["D04", "D05"]


def test_a_partly_named_scene_set_is_refused_rather_than_half_used(tmp_path):
    """All or nothing. A mapping where the named scenes land on their real
    wells and the rest land wherever the grid put them is right exactly where
    a user is most likely to spot-check it."""
    path = write_czi(
        tmp_path / "half.czi",
        plate_tiles(wells=3, fovs=1, timepoints=1, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "A01", 2: "A03"},
                               channels=["GFP"]),
    )
    layout = read_czi_layout(path)

    assert layout.scene_wells == {0: "A01", 2: "A03"}
    assert layout.wells_from_metadata() == []


def test_two_scenes_claiming_one_well_are_one_well(tmp_path):
    """Not a clash: they are the two positions imaged in A01, and the
    conversion writes them as that well's two fields of view. This asked for
    ``[]`` until a real plate showed the shape is ordinary — see
    ``tests/test_several_scenes_make_one_well.py``.

    The distinct count, so that this answers the question the same way
    ``CziSet.wells_from_files`` does; the per-scene labels are
    ``layout.scene_wells``, which is what the grouping is built from.
    """
    path = write_czi(
        tmp_path / "dup_well.czi",
        plate_tiles(wells=2, fovs=1, timepoints=1, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "A01", 1: "A01"},
                               channels=["GFP"]),
    )
    layout = read_czi_layout(path)

    assert layout.wells_from_metadata() == ["A01"]
    assert "wells named by the file (1, holding the 2 scene(s) between them)" \
        in layout.describe()


# ── what the reader refuses, and why ─────────────────────────────────────────

def test_a_file_that_is_not_a_czi_says_so(tmp_path):
    path = tmp_path / "renamed.czi"
    path.write_bytes(b"II*\0" + b"\0" * 200)
    with pytest.raises(CziReadError, match="not a CZI file"):
        read_czi_layout(path)


def test_a_missing_plane_is_named_rather_than_converted_as_a_blank(tmp_path):
    """Writing a zero-filled image for a plane the file does not hold is the
    silent substitution CLAUDE.md §7 is about: it is indistinguishable from a
    real image of a dark field."""
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]                                   # scene 0, channel 1
    path = write_czi(tmp_path / "short.czi", tiles)
    layout = read_czi_layout(path)

    assert layout.total_images == 4
    assert layout.missing_images == 1
    # Counted and said out loud. The conversion goes ahead and skips it — what
    # it must never do is write something in its place, which is what the test
    # below pins on the reader itself.
    note = layout.shortfall_note("short.czi")
    assert "3 of the 4 image(s)" in note and "1 are missing" in note


def test_reading_a_plane_the_file_lacks_raises_rather_than_returning_zeros(
        tmp_path):
    tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=2)
    del tiles[1]
    path = write_czi(tmp_path / "short.czi", tiles)
    with CziReader(path) as reader:
        with pytest.raises(CziReadError, match="holds no image"):
            reader.read_plane(0, 0, 0, 1, 0)


def test_a_duplicated_coordinate_is_refused(tmp_path):
    """Two tiles under one coordinate means the index cannot say which is the
    image, which is what a file concatenated from two acquisitions looks like."""
    tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=1)
    tiles.append(Tile(pixels=np.zeros((4, 6), np.uint16)))
    path = write_czi(tmp_path / "twice.czi", tiles)
    layout = read_czi_layout(path)

    assert layout.duplicate_blocks == 1
    assert "twice" in layout.validation_report("twice.czi")


def test_an_axis_the_filename_schema_has_no_field_for_is_refused(tmp_path):
    """Its planes would all be written to one name and only the last would
    survive — without the run reporting a single missing image."""
    tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=1)
    tiles[0].extra = {"H": (0, 3)}
    path = write_czi(tmp_path / "phase.czi", tiles)
    layout = read_czi_layout(path)

    assert layout.extra_axes == {"H": 3}
    assert "H dimension" in layout.validation_report("phase.czi")


def test_scenes_with_different_tile_counts_are_refused(tmp_path):
    tiles = [t for t in plate_tiles(wells=2, fovs=2, timepoints=1, channels=1)
             if not (t.scene == 1 and t.tile == 1)]
    path = write_czi(tmp_path / "ragged.czi", tiles)
    layout = read_czi_layout(path)

    assert layout.fovs_per_scene == 0
    assert "different numbers of tiles" in layout.validation_report("ragged.czi")


def test_two_channels_reducing_to_one_filename_token_are_refused(tmp_path):
    """``zipfile`` stores both copies and a reader returns only the last, so
    one channel becomes permanently unreachable while the run reports having
    written both — and ``atomic_zip``'s verification cannot see it, because the
    duplicate name is on both sides of its comparison."""
    path = write_czi(
        tmp_path / "collide.czi",
        plate_tiles(wells=1, fovs=1, timepoints=1, channels=2),
        xml=plate_metadata_xml(channels=["488 nm", "488nm"]),
    )
    layout = read_czi_layout(path)

    assert layout.channel_names == ["488nm", "488nm"]
    report = layout.validation_report("collide.czi")
    assert "'488nm'" in report and "'488 nm'" in report


def test_channel_tokens_are_compared_case_insensitively(tmp_path):
    """``GFP`` and ``gfp`` are two member names that collapse into one key
    downstream, because every consumer of a channel token folds case."""
    path = write_czi(
        tmp_path / "case.czi",
        plate_tiles(wells=1, fovs=1, timepoints=1, channels=2),
        xml=plate_metadata_xml(channels=["GFP", "gfp"]),
    )
    assert "reduce to the filename token" in (
        read_czi_layout(path).validation_report("case.czi"))


def test_an_unnamed_channel_falls_back_to_a_positional_token(tmp_path):
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=1, fovs=1, timepoints=1, channels=2))
    assert read_czi_layout(path).channel_names == ["c0", "c1"]


def test_a_colour_pixel_format_is_refused_by_name(tmp_path):
    """Bgr24 has three interleaved samples per pixel; picking one would be an
    unannounced decision about which channel the numbers came from."""
    tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=1)
    path = write_czi(tmp_path / "colour.czi", tiles)
    raw = path.read_bytes()
    # Rewrite every stored PixelType from Gray16 (1) to Bgr24 (3). The value
    # sits two bytes into each DirectoryEntryDV, right after its "DV" tag.
    path.write_bytes(raw.replace(b"DV\x01\x00\x00\x00", b"DV\x03\x00\x00\x00"))
    layout = read_czi_layout(path)

    assert layout.pixel_types == {3}
    assert "Bgr24" in layout.validation_report("colour.czi")


def test_lossy_jpeg_tiles_are_refused_before_anything_is_quantified(tmp_path):
    tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=1)
    tiles[0].compression = 1
    path = write_czi(tmp_path / "lossy.czi", tiles)
    report = read_czi_layout(path).validation_report("lossy.czi")

    assert "lossy" in report
    assert "not the ones the camera measured" in report


def test_an_unknown_compression_method_is_refused_by_number(tmp_path):
    tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=1)
    tiles[0].compression = 77
    path = write_czi(tmp_path / "odd.czi", tiles)
    assert "compression method 77" in (
        read_czi_layout(path).validation_report("odd.czi"))


def test_a_file_with_no_tiles_at_all_says_so(tmp_path):
    path = write_czi(tmp_path / "empty.czi", [])
    assert "no image tiles" in read_czi_layout(path).validation_report("empty.czi")


def test_a_sound_file_is_read_through_its_directory_in_two_seeks(tmp_path):
    """The fast path, asserted because its absence is silent.

    The two offsets in the file header were once read one field late — the
    directory offset came back as the metadata offset — and nothing broke:
    the directory read failed, ``read_czi_layout`` fell back to walking every
    segment, and the walk recovers the same layout. So every file took the
    damaged-file recovery path, carried its "interrupted acquisition" warning,
    and paid a whole pass over itself, while the suite stayed green because the
    only assertion about ``from_directory`` was the one below that wants it
    False.
    """
    path = write_czi(
        tmp_path / "sound.czi",
        plate_tiles(wells=2, fovs=1, timepoints=1, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "A01", 1: "A02"},
                               channels=["GFP"]),
    )
    layout = read_czi_layout(path)

    assert layout.from_directory is True
    assert layout.warnings == []
    assert layout.wells_from_metadata() == ["A01", "A02"]


def test_the_metadata_is_found_through_the_header_not_only_by_walking(tmp_path):
    """The other half of the same slip: with the offsets read one field late,
    the metadata offset came back as ``UpdatePending`` — zero — and the XML was
    recovered only because the segment walk went looking for it. A file the
    walk declines to scan would have lost its channel and well names."""
    path = write_czi(
        tmp_path / "meta.czi",
        plate_tiles(wells=1, fovs=1, timepoints=1, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "D06"}, channels=["Cy5"]),
    )
    with open(path, "rb") as fh:
        directory_position, metadata_position = _read_file_header(fh)
        assert directory_position > 0
        assert metadata_position > 0
        assert _read_metadata_xml(fh, metadata_position).startswith(
            "<ImageDocument>")


def test_channels_may_have_different_stack_depths(tmp_path):
    """A 3-plane fluorescent stack beside a single brightfield plane is an
    ordinary Zeiss acquisition. Counting stored tiles against
    positions x T x C x max(Z) called it two thirds incomplete and told the
    user to re-export a file that was never truncated — and no re-export
    satisfies it, so the plate was simply unconvertible."""
    tiles = [t for t in plate_tiles(wells=2, fovs=1, timepoints=1, channels=2,
                                    z=3)
             if not (t.c == 1 and t.z > 0)]
    path = write_czi(tmp_path / "mixed_z.czi", tiles)
    layout = read_czi_layout(path)

    assert layout.size_z == 3
    assert layout.total_images == 4          # 2 scenes x 1 tile x 1 T x 2 C
    assert layout.missing_images == 0
    assert layout.validation_report("mixed_z.czi") == ""


def test_a_shallower_channel_still_projects(tmp_path):
    """The reader must not ask for Z planes a coordinate does not have.
    ``range(size_z)`` raised on the single-plane channel of the file above."""
    tiles = [t for t in plate_tiles(wells=1, fovs=1, timepoints=1, channels=2,
                                    z=3)
             if not (t.c == 1 and t.z > 0)]
    path = write_czi(tmp_path / "mixed_z.czi", tiles)
    with CziReader(path) as reader:
        deep = reader.read_projection(0, 0, 0, 0)
        shallow = reader.read_projection(0, 0, 0, 1)

    assert deep[0, 0] == _expected(0, 0, 0, 0, 2)    # the brightest Z
    assert shallow[0, 0] == _expected(0, 0, 0, 1, 0)


def test_a_coordinate_with_no_planes_at_all_is_still_missing(tmp_path):
    """The relaxation above must not become "nothing is ever missing"."""
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2, z=2)
    tiles = [t for t in tiles if not (t.scene == 1 and t.c == 1)]
    path = write_czi(tmp_path / "gone.czi", tiles)
    layout = read_czi_layout(path)

    assert layout.total_images == 4
    assert layout.missing_images == 1


def test_a_gap_is_reported_and_not_refused(tmp_path):
    """It used to be a refusal, and refusing was wrong twice over.

    A split-scene export is read a file at a time, so one file short of a
    plane refused the whole plate — six hundred and sixty sound wells lost for
    one — and it refused after the read, so the hour spent getting there
    bought nothing. The sentence it refused with also gave a count and no
    coordinate, which is not something anybody can act on.

    So the shortfall is a note now, and the note has to carry both numbers:
    how many images the file holds and how many the axes imply. A note saying
    only "some are missing" would be the same unactionable thing in a quieter
    voice.
    """
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2, z=2)
    tiles = [t for t in tiles if not (t.scene == 1 and t.c == 1)]
    path = write_czi(tmp_path / "gone.czi", tiles)
    layout = read_czi_layout(path)

    assert layout.validation_report("gone.czi") == "", (
        "a gap is not a reason to refuse the file")
    note = layout.shortfall_note("gone.czi")
    assert "gone.czi" in note
    assert "3 of the 4 image(s)" in note, note
    assert "1 are missing" in note, note


def test_a_complete_file_has_nothing_to_say_about_shortfall(tmp_path):
    """The note is silent when there is nothing to report, so that seeing it
    means something (§7.8: "it says something" is satisfied by a note that
    always does)."""
    path = write_czi(tmp_path / "whole.czi",
                     plate_tiles(wells=2, fovs=1, timepoints=2, channels=2))
    layout = read_czi_layout(path)

    assert layout.missing_images == 0
    assert layout.shortfall_note("whole.czi") == ""
