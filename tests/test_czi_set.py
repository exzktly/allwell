"""One or more CZI files read as one plate (``pipeline.czi_set``).

ZEN saves a plate scan two ways — one file holding every scene, or one file per
scene — and nothing inside a split export's members says it is part of a plate.
The plate is the folder, which is what this module decides and what these
check, against real ZISRAW bytes rather than a mocked reader.
"""

from __future__ import annotations

import pytest

from tests._czi_fixture import (
    Tile,
    plate_metadata_xml,
    plate_tiles,
    write_czi,
)
from pipeline.czi_read import CziReadError
from pipeline.czi_set import (
    discover_czi_files,
    read_czi_set,
    read_czi_set_safely,
    siblings_of,
)


def _scene_file(folder, name, well=None, *, channels=("DAPI", "GFP"),
                fovs=1, timepoints=1, scenes=1):
    folder.mkdir(parents=True, exist_ok=True)
    wells = {} if well is None else {i: well for i in range(scenes)}
    return write_czi(
        folder / name,
        plate_tiles(wells=scenes, fovs=fovs, timepoints=timepoints,
                    channels=len(channels)),
        xml=plate_metadata_xml(scene_wells=wells, channels=list(channels)),
    )


def _split_plate(tmp_path, wells=("C07", "A02", "H11"), **kwargs):
    folder = tmp_path / "Exp7_split"
    for well in wells:
        _scene_file(folder, f"Exp7_{well}.czi", well, **kwargs)
    return folder


# ── what a selection stands for ──────────────────────────────────────────────

def test_a_file_stands_for_itself_alone(tmp_path):
    """Picking one file and silently converting its neighbours is a surprise,
    so widening is the caller's decision and not this function's."""
    folder = _split_plate(tmp_path)
    picked = folder / "Exp7_A02.czi"
    assert discover_czi_files(picked) == [picked]


def test_a_folder_stands_for_the_czi_files_in_it_sorted(tmp_path):
    folder = _split_plate(tmp_path)
    assert [p.name for p in discover_czi_files(folder)] == [
        "Exp7_A02.czi", "Exp7_C07.czi", "Exp7_H11.czi"]


def test_a_nested_folder_is_not_swept_up(tmp_path):
    """Nothing recursive: a folder of folders is a different thing, and walking
    into one would silently convert a nested export as part of this plate."""
    folder = _split_plate(tmp_path, wells=("A01",))
    _scene_file(folder / "other", "Exp9_B02.czi", "B02")
    assert [p.name for p in discover_czi_files(folder)] == ["Exp7_A01.czi"]


def test_non_czi_files_beside_the_plate_are_ignored(tmp_path):
    folder = _split_plate(tmp_path, wells=("A01",))
    (folder / "notes.txt").write_text("hello")
    (folder / "Exp7.ome.tif").write_bytes(b"not a czi")
    assert [p.name for p in discover_czi_files(folder)] == ["Exp7_A01.czi"]


def test_siblings_of_a_member_are_the_whole_folder(tmp_path):
    folder = _split_plate(tmp_path)
    assert siblings_of(folder / "Exp7_A02.czi") == discover_czi_files(folder)


# ── a split-scene plate ──────────────────────────────────────────────────────

def test_a_split_scene_folder_reads_as_one_plate(tmp_path):
    folder = _split_plate(tmp_path, fovs=2, timepoints=2)
    czi_set = read_czi_set(folder)

    assert czi_set.problem == ""
    assert czi_set.n_files == 3
    assert czi_set.n_scenes == 3
    assert czi_set.n_positions == 6          # 3 scenes x 2 tiles
    assert czi_set.fovs_per_scene == 2
    assert czi_set.layout.size_t == 2
    assert czi_set.layout.channel_names == ["DAPI", "GFP"]
    # File-name order, which is the order ZEN writes a split export in — not
    # plate order, which would be A02, C07, H11 for these three.
    assert czi_set.wells_from_files() == ["A02", "C07", "H11"]


def test_each_position_carries_the_file_it_is_in(tmp_path):
    """The whole reason a position is not a bare ``(scene, tile)`` pair: a
    split plate has one file per well, and the exploder walks wells."""
    folder = _split_plate(tmp_path, wells=("A01", "B02"), fovs=2)
    czi_set = read_czi_set(folder)

    assert [(p.path.name, p.scene, p.tile) for p in czi_set.positions] == [
        ("Exp7_A01.czi", 0, 0), ("Exp7_A01.czi", 0, 1),
        ("Exp7_B02.czi", 0, 0), ("Exp7_B02.czi", 0, 1),
    ]


def test_a_single_file_is_a_set_of_one(tmp_path):
    """The same structure, so the exploder has no branch for the two shapes."""
    path = write_czi(
        tmp_path / "Whole.czi",
        plate_tiles(wells=2, fovs=1, timepoints=1, channels=1),
        xml=plate_metadata_xml(scene_wells={0: "A01", 1: "A02"},
                               channels=["DAPI"]),
    )
    czi_set = read_czi_set(path)

    assert czi_set.n_files == 1
    assert czi_set.n_scenes == 2
    assert czi_set.wells_from_files() == ["A01", "A02"]
    assert {p.path for p in czi_set.positions} == {path}


def test_an_explicit_file_list_overrides_what_the_path_stands_for(tmp_path):
    """What the page's tick-box does when it is unticked on a folder member."""
    folder = _split_plate(tmp_path)
    picked = folder / "Exp7_H11.czi"
    czi_set = read_czi_set(folder, files=[picked])

    assert czi_set.n_files == 1
    assert czi_set.wells_from_files() == ["H11"]


# ── where a split member's well comes from ───────────────────────────────────

def test_a_one_scene_file_may_be_named_by_its_filename(tmp_path):
    """ZEN's split export names each file after the scene it holds, and does
    not always keep the scene's own name inside it."""
    folder = tmp_path / "bare_split"
    for well in ("A01", "B02"):
        _scene_file(folder, f"Exp7_{well}.czi", None)
    assert read_czi_set(folder).wells_from_files() == ["A01", "B02"]


def test_the_scene_s_own_metadata_beats_the_filename(tmp_path):
    """The acquisition saying which well it scanned outranks a name a user or
    a copy step could have changed."""
    folder = tmp_path / "renamed"
    _scene_file(folder, "Exp7_A01.czi", "H12")
    assert read_czi_set(folder).wells_from_files() == ["H12"]


def test_a_whole_plate_file_is_not_named_by_its_filename(tmp_path):
    """The one-scene condition, and the reason for it. ``Exp_A1.czi`` holding
    ninety-six unnamed scenes is not one well of ninety-six fields, and reading
    it as one would produce a plate that is wrong everywhere at once."""
    path = write_czi(tmp_path / "Exp_A1.czi",
                     plate_tiles(wells=4, fovs=1, timepoints=1, channels=1))
    czi_set = read_czi_set(path)

    assert czi_set.n_scenes == 4
    assert czi_set.wells_from_files() == []


def test_a_partly_named_split_set_is_refused_rather_than_half_used(tmp_path):
    folder = tmp_path / "half"
    _scene_file(folder, "Exp7_A01.czi", "A01")
    _scene_file(folder, "scan2.czi", None)
    assert read_czi_set(folder).wells_from_files() == []


def test_two_files_claiming_one_well_are_refused(tmp_path):
    """The second file's images would overwrite the first file's archive."""
    folder = tmp_path / "clash"
    _scene_file(folder, "a.czi", "A01")
    _scene_file(folder, "b.czi", "A01")
    assert read_czi_set(folder).wells_from_files() == []


# ── what the files of a set must agree about ─────────────────────────────────

@pytest.mark.parametrize("kwargs,noun", [
    ({"channels": ("DAPI",)}, "number of channels"),
    ({"channels": ("DAPI", "mCherry")}, "channel names"),
    ({"timepoints": 3}, "number of timepoints"),
])
def test_files_that_disagree_about_the_filenames_are_refused(
        tmp_path, kwargs, noun):
    """They become one dataset, so a difference here means the same filename
    means different things in different wells — and the run reports success."""
    folder = tmp_path / "mixed"
    _scene_file(folder, "a_A01.czi", "A01")
    _scene_file(folder, "b_A02.czi", "A02", **kwargs)
    problem = read_czi_set(folder).problem

    assert noun in problem
    assert "a_A01.czi" in problem and "b_A02.czi" in problem


def test_files_may_disagree_about_z(tmp_path):
    """Z is projected away, so two files whose stacks are different depths
    produce the same images. ``ome_explode`` leaves Z out of its own ragged
    check for the same reason; refusing here would reject a sound plate."""
    folder = tmp_path / "depths"
    _scene_file(folder, "a_A01.czi", "A01")
    write_czi(folder / "b_A02.czi",
              plate_tiles(wells=1, fovs=1, timepoints=1, channels=2, z=4),
              xml=plate_metadata_xml(scene_wells={0: "A02"},
                                     channels=["DAPI", "GFP"]))
    czi_set = read_czi_set(folder)

    assert czi_set.problem == ""
    assert czi_set.wells_from_files() == ["A01", "A02"]


def test_an_unsound_member_is_reported_as_itself(tmp_path):
    """Not as a disagreement: "these two disagree about the channel names" is a
    confusing thing to say about a file that is short of an image, and it names
    the wrong remedy. Each file is checked on its own first."""
    folder = tmp_path / "broken"
    _scene_file(folder, "a_A01.czi", "A01")
    # Two scenes, two channels, with one channel of one scene absent — so the
    # file still *declares* two channels and is genuinely incomplete, rather
    # than being a sound one-channel file that merely differs from its sibling.
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]
    write_czi(folder / "b_A02.czi", tiles,
              xml=plate_metadata_xml(scene_wells={0: "A02", 1: "A03"},
                                     channels=["DAPI", "GFP"]))
    problem = read_czi_set(folder).problem

    assert "b_A02.czi" in problem and "incomplete" in problem


def test_an_unreadable_member_raises_but_the_safe_form_reports(tmp_path):
    folder = tmp_path / "junk"
    _scene_file(folder, "a_A01.czi", "A01")
    (folder / "b.czi").write_bytes(b"II*\0" + b"\0" * 200)

    with pytest.raises(CziReadError, match="not a CZI file"):
        read_czi_set(folder)
    assert "not a CZI file" in read_czi_set_safely(folder).problem


def test_an_empty_folder_says_so(tmp_path):
    (tmp_path / "nothing").mkdir()
    assert "No .czi files" in read_czi_set(tmp_path / "nothing").problem


# ── the third shape: a multi-file CZI ────────────────────────────────────────

def test_a_multi_file_czi_is_refused_by_name(tmp_path):
    """A single logical CZI split into numbered parts that share one subblock
    directory. Its tiles' offsets are into a sibling file, so reading them
    through one handle lands at an arbitrary position in the wrong one. Refused
    with the two exports this converter *does* read."""
    tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=2)
    tiles[1] = Tile(pixels=tiles[1].pixels, scene=0, tile=0, t=0, c=1, z=0,
                    file_part=1)
    path = write_czi(tmp_path / "huge.czi", tiles)
    problem = read_czi_set(path).problem

    assert "multi-file CZI" in problem
    assert "one file per scene" in problem
