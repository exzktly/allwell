"""Exploding a CZI into per-well archives (``pipeline.czi_explode``).

The questions here are all one question — *did this plane land under the right
name?* — so every assertion reads the pixel value back out of the archive and
compares it with the coordinates it was written from. A test that checked only
member names and counts would pass against an exploder that shuffled the planes
among them, which is the mapping bug this tool can actually have.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest
import tifffile

from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi
from pipeline.czi_explode import (
    CziExplodeCancelled,
    explode_czi,
    resolve_well_map,
)
from pipeline.czi_set import read_czi_set


def _value(scene: int, tile: int, t: int, c: int, z: int = 0) -> int:
    """The value ``plate_tiles`` writes into the plane at these coordinates."""
    return (((scene * 10 + tile) * 10 + t) * 10 + c) * 10 + z


def _member(archive: Path, name: str) -> np.ndarray:
    with zipfile.ZipFile(archive) as zf:
        return tifffile.imread(io.BytesIO(zf.read(name)))


#: The wells ``_named_plate``'s scenes claim, in scene order. Deliberately not
#: A01, A02, A03: with sequential labels, an exploder that ignored the file's
#: names and numbered the wells across the plate itself would produce exactly
#: the same answer, and every assertion below would pass against it. Scrambled,
#: sorted and plate order all differ, so only the real mapping fits.
SCENE_WELLS = ("C07", "A02", "H11", "B05", "D12")


def _named_plate(tmp_path, **kwargs) -> Path:
    """A CZI whose scenes name the wells in :data:`SCENE_WELLS`, in order."""
    wells = kwargs.get("wells", 2)
    tiles = plate_tiles(**kwargs)
    labels = {i: SCENE_WELLS[i] for i in range(wells)}
    channels = ["DAPI", "GFP"][: kwargs.get("channels", 2)]
    return write_czi(tmp_path / "Plate7.czi", tiles,
                     xml=plate_metadata_xml(scene_wells=labels,
                                            channels=channels))


# ── the mapping ──────────────────────────────────────────────────────────────

def test_positions_are_scene_major(tmp_path):
    """A stage finishes one well's tiles before moving to the next, so that is
    the order a grid mapping lays its labels over. Tile-major would give the
    same *count*, so this asserts the sequence."""
    path = _named_plate(tmp_path, wells=2, fovs=2, timepoints=1, channels=1)
    assert [(p.scene, p.tile) for p in read_czi_set(path).positions] == [
        (0, 0), (0, 1), (1, 0), (1, 1)]


def test_the_file_s_own_scene_names_become_the_wells(tmp_path):
    path = _named_plate(tmp_path, wells=3, fovs=2, timepoints=1, channels=1)
    well_map = resolve_well_map(read_czi_set(path))

    assert well_map.wells == ["C07", "A02", "H11"]
    assert well_map.fovs_per_well == 2
    assert well_map.from_file is True
    assert [(p.scene, p.tile) for p in well_map.positions[1]] == [(1, 0), (1, 1)]
    assert {p.path for p in well_map.positions[1]} == {path}
    # The file's order, not the plate's: sorted these would read A02, C07, H11.
    assert well_map.wells != sorted(well_map.wells)


def test_the_grid_lays_the_acquisition_order_over_the_plate(tmp_path):
    """Four positions over a 2×2 block, serpentine — so the second row runs
    *backwards*, which is the half of this a raster would get wrong."""
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=4, fovs=1, timepoints=1, channels=1))
    well_map = resolve_well_map(read_czi_set(path), well_source="grid",
                                grid_rows=2, grid_cols=2)

    assert well_map.wells == ["A01", "A02", "B02", "B01"]
    assert well_map.from_file is False
    assert [[(p.scene, p.tile) for p in group]
            for group in well_map.positions] == [[(0, 0)], [(1, 0)], [(2, 0)],
                                                 [(3, 0)]]


def test_a_raster_is_not_read_as_a_snake(tmp_path):
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=4, fovs=1, timepoints=1, channels=1))
    well_map = resolve_well_map(read_czi_set(path), well_source="grid",
                                grid_rows=2, grid_cols=2, serpentine=False)
    assert well_map.wells == ["A01", "A02", "B01", "B02"]


def test_a_grid_that_does_not_divide_the_positions_is_refused(tmp_path):
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=5, fovs=1, timepoints=1, channels=1))
    with pytest.raises(ValueError, match="not divisible by the well grid"):
        resolve_well_map(read_czi_set(path), well_source="grid",
                         grid_rows=2, grid_cols=2, name="bare.czi")


def test_asking_the_file_for_wells_it_does_not_name_points_at_the_grid(tmp_path):
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=2, fovs=1, timepoints=1, channels=1))
    with pytest.raises(ValueError, match="Switch the well source to the plate grid"):
        resolve_well_map(read_czi_set(path), name="bare.czi")


def test_a_fovs_per_well_that_contradicts_the_file_is_refused(tmp_path):
    """Silently preferring one of the two would make the *other* one a setting
    that is displayed, accepted and ignored."""
    path = _named_plate(tmp_path, wells=2, fovs=2, timepoints=1, channels=1)
    with pytest.raises(ValueError, match="holds 2 field"):
        resolve_well_map(read_czi_set(path), fovs_per_well=3,
                         name="Plate7.czi")


# ── what gets written ────────────────────────────────────────────────────────

def test_every_plane_lands_in_the_member_named_after_its_coordinates(tmp_path):
    path = _named_plate(tmp_path, wells=2, fovs=2, timepoints=2, channels=2)
    out = tmp_path / "dest" / "in"
    result = explode_czi(path, out, experiment="Plate7")

    assert result.wells == ["A02", "C07"]
    assert result.images_written == 2 * 2 * 2 * 2
    assert result.channels == ["DAPI", "GFP"]
    assert result.timepoints == ["t0", "t1"]
    assert result.fovs_per_well == 2

    channels = {0: "DAPI", 1: "GFP"}
    for scene, well in enumerate(SCENE_WELLS[:2]):
        for tile in (0, 1):
            for t in (0, 1):
                for c in (0, 1):
                    name = (f"Plate7_{channels[c]}_{well}_f{tile}_t{t}.tif")
                    plane = _member(out / f"{well}.zip", name)
                    assert plane[0, 0] == _value(scene, tile, t, c), name


def test_a_z_stack_reaches_the_archive_max_projected(tmp_path):
    """``plate_tiles`` increases the value with Z, so the projection of a
    three-deep stack is the z=2 plane's value and nothing else."""
    path = _named_plate(tmp_path, wells=1, fovs=1, timepoints=1, channels=1, z=3)
    out = tmp_path / "dest" / "in"
    explode_czi(path, out, experiment="E")
    plane = _member(out / "C07.zip", "E_DAPI_C07_f0_t0.tif")
    assert plane[0, 0] == _value(0, 0, 0, 0, z=2)


def test_the_experiment_token_defaults_to_the_filename(tmp_path):
    path = _named_plate(tmp_path, wells=1, fovs=1, timepoints=1, channels=1)
    out = tmp_path / "dest" / "in"
    explode_czi(path, out)
    assert zipfile.ZipFile(out / "C07.zip").namelist() == [
        "Plate7_DAPI_C07_f0_t0.tif"]


def test_a_grid_mapped_plate_writes_the_grid_s_wells(tmp_path):
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=4, fovs=1, timepoints=1, channels=1))
    out = tmp_path / "dest" / "in"
    result = explode_czi(path, out, well_source="grid", grid_rows=2,
                         grid_cols=2, experiment="G")

    assert result.wells == ["A01", "A02", "B01", "B02"]
    # Serpentine: the third position is B02, not B01 — so the *value* in each
    # archive is what says the order was applied and not merely the labels.
    assert _member(out / "B02.zip", "G_c0_B02_f0_t0.tif")[0, 0] == _value(2, 0, 0, 0)
    assert _member(out / "B01.zip", "G_c0_B01_f0_t0.tif")[0, 0] == _value(3, 0, 0, 0)


def test_a_source_short_of_a_plane_converts_and_says_what_it_skipped(tmp_path):
    """It used to refuse, and refusing lost far more than it protected: on a
    split export one file short of a plane took the whole plate with it, after
    the read rather than before it.

    So the plane is **skipped** — never substituted, because a zero-filled
    image is indistinguishable from a real dark field — the rest of the well
    converts, and the result says which well came out short and by how much.
    """
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]                                   # scene 0, channel 1
    path = write_czi(tmp_path / "short.czi", tiles)
    out = tmp_path / "dest" / "in"

    result = explode_czi(path, out, experiment="E", well_source="grid",
                         grid_rows=1, grid_cols=2, workers=1)

    assert sorted(p.name for p in out.glob("*.zip")) == ["A01.zip", "A02.zip"]
    # Three of the four images, and the missing one named by its well rather
    # than merely subtracted from a total.
    assert result.images_written == 3
    assert result.images_missing == {"A01": 1}
    # The archive is short exactly the member that had no data, and holds no
    # placeholder standing in for it.
    with zipfile.ZipFile(out / "A01.zip") as archive:
        members = sorted(archive.namelist())
    # The fixture names no channels, so they are the positional c0 / c1 —
    # and what survived is c0, which is the channel the deleted tile was not.
    assert members == ["E_c0_A01_f0_t0.tif"]


def test_what_was_skipped_reaches_the_run_log(tmp_path):
    """A result nobody prints is a result nobody sees. The closing line is the
    one thing in this log a reader must not scroll past, so it comes last and
    on its own."""
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]
    path = write_czi(tmp_path / "short.czi", tiles)
    lines: list[str] = []

    explode_czi(path, tmp_path / "dest" / "in", experiment="E",
                well_source="grid", grid_rows=1, grid_cols=2, workers=1,
                log_fn=lines.append)

    closing = "\n".join(lines)
    assert "1 image(s) had no data in the source" in closing
    assert "A01 (1)" in closing
    assert "are not complete" in closing


# ── progress, resume and cancellation ────────────────────────────────────────

def test_progress_is_reported_per_well_and_for_the_run(tmp_path):
    path = _named_plate(tmp_path, wells=2, fovs=1, timepoints=2, channels=2)
    out = tmp_path / "dest" / "in"
    run: list[tuple[int, int]] = []
    per_well: list[tuple[str, int, int]] = []
    explode_czi(path, out, experiment="E", workers=1,
                progress_fn=lambda d, t: run.append((d, t)),
                well_progress_fn=lambda w, d, t: per_well.append((w, d, t)))

    assert run[0] == (0, 8) and run[-1] == (8, 8)
    # Each well accounts for FOVs × timepoints × channels = 4 planes, and its
    # last tick says so — a denominator taken from the run total would read 8.
    assert ("C07", 4, 4) in per_well
    assert ("A02", 4, 4) in per_well


def test_a_resumed_run_keeps_the_wells_it_already_finished(tmp_path):
    path = _named_plate(tmp_path, wells=3, fovs=1, timepoints=1, channels=1)
    out = tmp_path / "dest" / "in"
    explode_czi(path, out, experiment="E")
    kept = (out / "A02.zip").stat().st_mtime_ns

    result = explode_czi(path, out, experiment="E", resume=True)

    assert result.wells_resumed == ["A02", "C07", "H11"]
    assert result.images_written == 0
    assert (out / "A02.zip").stat().st_mtime_ns == kept


def test_a_well_written_under_different_settings_is_not_treated_as_finished(
        tmp_path):
    """The member *names*, not a count: an archive from another mapping holds
    the right number of images under the wrong names, and resuming onto it
    leaves a dataset stitched from two mappings with nothing saying so."""
    path = _named_plate(tmp_path, wells=1, fovs=1, timepoints=1, channels=1)
    out = tmp_path / "dest" / "in"
    explode_czi(path, out, experiment="OLD")

    result = explode_czi(path, out, experiment="NEW", resume=True)

    assert result.wells_resumed == []
    assert result.images_written == 1
    assert "NEW_DAPI_C07_f0_t0.tif" in zipfile.ZipFile(out / "C07.zip").namelist()


def test_cancelling_leaves_no_half_written_archive(tmp_path):
    """Each archive is published atomically, so a cancelled run leaves whole
    wells or nothing — never a ``<WELL>.zip`` holding part of a well."""
    path = _named_plate(tmp_path, wells=4, fovs=1, timepoints=1, channels=1)
    out = tmp_path / "dest" / "in"
    seen = {"planes": 0}

    def _abort() -> bool:
        seen["planes"] += 1
        return seen["planes"] > 2

    with pytest.raises(CziExplodeCancelled):
        explode_czi(path, out, experiment="E", workers=1, should_abort=_abort)

    for archive in out.glob("*.zip"):
        with zipfile.ZipFile(archive) as zf:
            assert zf.namelist() == [f"E_DAPI_{archive.stem}_f0_t0.tif"]
            assert zf.testzip() is None


# ── a plate split into one file per scene ────────────────────────────────────

def _split_plate(tmp_path, wells=("C07", "A02", "H11"), **kwargs):
    """ZEN's *Split Scenes* export: one single-scene file per well."""
    folder = tmp_path / "Exp7_split"
    folder.mkdir(parents=True, exist_ok=True)
    for well in wells:
        write_czi(folder / f"Exp7_{well}.czi",
                  plate_tiles(wells=1, **kwargs),
                  xml=plate_metadata_xml(scene_wells={0: well},
                                         channels=["DAPI", "GFP"]))
    return folder


def test_a_split_scene_folder_converts_as_one_plate(tmp_path):
    folder = _split_plate(tmp_path, fovs=2, timepoints=2, channels=2)
    out = tmp_path / "dest" / "in"
    result = explode_czi(folder, out, experiment="Exp7")

    assert result.wells == ["A02", "C07", "H11"]
    assert result.images_written == 3 * 2 * 2 * 2
    assert sorted(p.name for p in out.glob("*.zip")) == [
        "A02.zip", "C07.zip", "H11.zip"]


def test_each_well_gets_the_images_from_its_own_file(tmp_path):
    """The assertion that separates a working split conversion from one that
    read the first file three times: every member of every archive here is
    scene 0 of a *different* file, so a reader stuck on one file would write
    the same pixels into all three wells and the counts would still be right.

    ``plate_tiles`` gives every single-scene file the same values, so the value
    alone cannot tell them apart — the files are made distinguishable here on
    purpose, by shifting each one's pixels by its own offset.
    """
    folder = tmp_path / "Exp7_split"
    folder.mkdir()
    for offset, well in enumerate(("C07", "A02", "H11")):
        tiles = plate_tiles(wells=1, fovs=1, timepoints=1, channels=1)
        for tile in tiles:
            tile.pixels = np.full_like(tile.pixels, 100 + offset)
        write_czi(folder / f"Exp7_{well}.czi", tiles,
                  xml=plate_metadata_xml(scene_wells={0: well},
                                         channels=["DAPI"]))
    out = tmp_path / "dest" / "in"
    explode_czi(folder, out, experiment="E")

    # File order is by name: Exp7_A02, Exp7_C07, Exp7_H11 → offsets 1, 0, 2.
    assert _member(out / "C07.zip", "E_DAPI_C07_f0_t0.tif")[0, 0] == 100
    assert _member(out / "A02.zip", "E_DAPI_A02_f0_t0.tif")[0, 0] == 101
    assert _member(out / "H11.zip", "E_DAPI_H11_f0_t0.tif")[0, 0] == 102


def test_a_named_subset_of_the_folder_converts_only_those_files(tmp_path):
    """What the page's tick-box does when the user unticks it."""
    folder = _split_plate(tmp_path, fovs=1, timepoints=1, channels=2)
    out = tmp_path / "dest" / "in"
    result = explode_czi(folder, out, experiment="E",
                         files=[folder / "Exp7_H11.czi"])

    assert result.wells == ["H11"]
    assert sorted(p.name for p in out.glob("*.zip")) == ["H11.zip"]


@pytest.mark.parametrize("pick_member", [False, True])
def test_a_split_plate_takes_its_experiment_token_from_the_folder(
        tmp_path, pick_member):
    """Not from the file the user happened to click.

    Parametrised over *which path the exploder is handed*, because the two
    cases are the same only by accident. Selecting the folder makes the
    fallback — the selected path's own stem — already correct, so a version
    that ignored the set entirely passed that case. Selecting a member and
    widening it is the case the helper exists for: ``Exp7_A02`` would stamp one
    scene's well into every archive on the plate, H11's included.
    """
    folder = _split_plate(tmp_path, fovs=1, timepoints=1, channels=1)
    members = sorted(folder.glob("*.czi"))
    selected = members[0] if pick_member else folder
    out = tmp_path / "dest" / "in"

    explode_czi(selected, out, files=members)

    assert zipfile.ZipFile(out / "H11.zip").namelist() == [
        "Exp7split_DAPI_H11_f0_t0.tif"]


def test_a_grid_over_a_split_folder_uses_file_name_order(tmp_path):
    """Unnamed per-scene files still convert: the grid is laid over the files
    in the order ZEN wrote them, which is their sorted name order."""
    folder = tmp_path / "bare"
    folder.mkdir()
    for index in range(4):
        write_czi(folder / f"scan_{index}.czi",
                  plate_tiles(wells=1, fovs=1, timepoints=1, channels=1))
    out = tmp_path / "dest" / "in"
    result = explode_czi(folder, out, well_source="grid", grid_rows=2,
                         grid_cols=2, experiment="G")

    assert result.wells == ["A01", "A02", "B01", "B02"]
    assert sorted(p.name for p in out.glob("*.zip")) == [
        "A01.zip", "A02.zip", "B01.zip", "B02.zip"]


def test_files_that_disagree_are_refused_before_anything_is_written(tmp_path):
    folder = tmp_path / "mixed"
    folder.mkdir()
    write_czi(folder / "a_A01.czi",
              plate_tiles(wells=1, fovs=1, timepoints=1, channels=2),
              xml=plate_metadata_xml(scene_wells={0: "A01"},
                                     channels=["DAPI", "GFP"]))
    write_czi(folder / "b_A02.czi",
              plate_tiles(wells=1, fovs=1, timepoints=1, channels=1),
              xml=plate_metadata_xml(scene_wells={0: "A02"}, channels=["DAPI"]))
    out = tmp_path / "dest" / "in"

    with pytest.raises(ValueError, match="number of channels"):
        explode_czi(folder, out)
    assert not (out.exists() and list(out.glob("*.zip")))
