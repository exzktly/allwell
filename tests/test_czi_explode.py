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
    flat_positions,
    resolve_well_map,
)
from pipeline.czi_read import read_czi_layout


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
    assert flat_positions(read_czi_layout(path)) == [
        (0, 0), (0, 1), (1, 0), (1, 1)]


def test_the_file_s_own_scene_names_become_the_wells(tmp_path):
    path = _named_plate(tmp_path, wells=3, fovs=2, timepoints=1, channels=1)
    well_map = resolve_well_map(read_czi_layout(path))

    assert well_map.wells == ["C07", "A02", "H11"]
    assert well_map.fovs_per_well == 2
    assert well_map.from_file is True
    assert well_map.positions[1] == [(1, 0), (1, 1)]
    # The file's order, not the plate's: sorted these would read A02, C07, H11.
    assert well_map.wells != sorted(well_map.wells)


def test_the_grid_lays_the_acquisition_order_over_the_plate(tmp_path):
    """Four positions over a 2×2 block, serpentine — so the second row runs
    *backwards*, which is the half of this a raster would get wrong."""
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=4, fovs=1, timepoints=1, channels=1))
    well_map = resolve_well_map(read_czi_layout(path), well_source="grid",
                                grid_rows=2, grid_cols=2)

    assert well_map.wells == ["A01", "A02", "B02", "B01"]
    assert well_map.from_file is False
    assert well_map.positions == [[(0, 0)], [(1, 0)], [(2, 0)], [(3, 0)]]


def test_a_raster_is_not_read_as_a_snake(tmp_path):
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=4, fovs=1, timepoints=1, channels=1))
    well_map = resolve_well_map(read_czi_layout(path), well_source="grid",
                                grid_rows=2, grid_cols=2, serpentine=False)
    assert well_map.wells == ["A01", "A02", "B01", "B02"]


def test_a_grid_that_does_not_divide_the_positions_is_refused(tmp_path):
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=5, fovs=1, timepoints=1, channels=1))
    with pytest.raises(ValueError, match="not divisible by the well grid"):
        resolve_well_map(read_czi_layout(path), well_source="grid",
                         grid_rows=2, grid_cols=2, name="bare.czi")


def test_asking_the_file_for_wells_it_does_not_name_points_at_the_grid(tmp_path):
    path = write_czi(tmp_path / "bare.czi",
                     plate_tiles(wells=2, fovs=1, timepoints=1, channels=1))
    with pytest.raises(ValueError, match="Switch the well source to the plate grid"):
        resolve_well_map(read_czi_layout(path), name="bare.czi")


def test_a_fovs_per_well_that_contradicts_the_file_is_refused(tmp_path):
    """Silently preferring one of the two would make the *other* one a setting
    that is displayed, accepted and ignored."""
    path = _named_plate(tmp_path, wells=2, fovs=2, timepoints=1, channels=1)
    with pytest.raises(ValueError, match="holds 2 tile"):
        resolve_well_map(read_czi_layout(path), fovs_per_well=3,
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


def test_an_unsound_source_is_refused_before_a_single_archive_is_written(tmp_path):
    tiles = plate_tiles(wells=2, fovs=1, timepoints=1, channels=2)
    del tiles[1]
    path = write_czi(tmp_path / "short.czi", tiles)
    out = tmp_path / "dest" / "in"

    with pytest.raises(ValueError, match="incomplete"):
        explode_czi(path, out, well_source="grid", grid_rows=1, grid_cols=2)
    assert not list(out.glob("*.zip")) if out.exists() else True


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
