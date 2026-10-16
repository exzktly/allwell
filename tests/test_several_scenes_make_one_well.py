"""Several scenes naming one well are that well's fields of view.

ZEN writes one scene per imaged position and names each after the well it sits
in, so a 96-well plate imaged at seven positions per well is six hundred and
sixty scenes naming ninety-six wells. That is the ordinary shape of a
multi-position plate scan, and the converter refused it — ``wells_from_files``
required the scenes to map one to one onto wells, so the whole plate fell back
to the grid or to nothing.

``CziSet.scene_well_groups`` groups the scenes by the well they name, and
``resolve_well_map`` lays each well's scenes end to end as its fields. A repeat
is never a revisit: CZI carries time on its own axis, so a well imaged again
later in a run is another ``T``.

**Every fixture here interleaves the wells** — ``A01, A02, A01, A02, …``
rather than ``A01, A01, A02, A02`` — and that is not decoration. The mapping
this replaced chopped the flat position list into consecutive blocks, and on a
*blocked* fixture that produces exactly the right answer, so no assertion of
any strength could tell the two apart (CLAUDE.md §7.8 rule 2). Interleaved,
the wrong mapping puts A02's images in A01's archive.

The plane values are asserted, not the member counts: ``plate_tiles`` fills
each plane with a number derived from its own coordinates, so an image written
under the wrong name is a different number rather than merely a
different-looking image.
"""

from __future__ import annotations

import zipfile

import numpy as np
import pytest
import tifffile

from datasets.czi_import.apply import convert
from datasets.czi_import.czi_import_compute import (
    ConversionParams,
    plan_conversion,
    read_source,
)
from pipeline.czi_set import read_czi_set
from tests._czi_fixture import plate_metadata_xml, plate_tiles, write_czi

#: ``plate_tiles`` fills scene *s*'s single plane with this value.
def _value(scene: int, tile: int = 0) -> int:
    return scene * 10_000 + tile * 1_000


def _one_file(tmp_path, scene_wells, *, fovs=1, timepoints=1, name="Exp.czi"):
    """One CZI whose scenes name *scene_wells*, in that order."""
    return write_czi(
        tmp_path / name,
        plate_tiles(wells=len(scene_wells), fovs=fovs, timepoints=timepoints,
                    channels=1),
        xml=plate_metadata_xml(
            scene_wells=dict(enumerate(scene_wells)), channels=["DAPI"]))


def _split(tmp_path, scene_wells):
    """One CZI per scene, as ZEN's Split Scenes export writes them.

    Named ``s00.czi``, ``s01.czi``, … rather than after their wells, so that
    the grouping is read from the scene metadata and the sorted filename order
    is acquisition order rather than plate order.
    """
    folder = tmp_path / "split"
    folder.mkdir()
    for index, well in enumerate(scene_wells):
        write_czi(folder / f"s{index:02d}.czi",
                  plate_tiles(wells=1, fovs=1, timepoints=1, channels=1),
                  xml=plate_metadata_xml(scene_wells={0: well},
                                         channels=["DAPI"]))
    return folder


def _run(src, dest, log=None, **kwargs):
    params = ConversionParams(experiment="E", well_source="file", **kwargs)
    return convert(str(src), str(dest), params,
                   log=(log.append if log is not None else lambda _m: None),
                   is_cancelled=lambda: False,
                   progress=lambda _d, _t: None, well_progress=None)


def _planes(dest, well):
    """``{fov index: the one pixel value that field's image holds}``."""
    out: dict[int, int] = {}
    with zipfile.ZipFile(dest / "in" / f"{well}.zip") as archive:
        for member in archive.namelist():
            fov = int(member.split("_f")[1].split("_")[0])
            with archive.open(member) as handle:
                out[fov] = int(tifffile.imread(handle).flat[0])
    return out


# ── the grouping itself ──────────────────────────────────────────────────────

def test_the_set_groups_its_scenes_by_the_well_they_name(tmp_path):
    source = read_czi_set(_one_file(tmp_path, ["A01", "A02", "A01", "A02"]))

    assert source.scene_well_groups() == [("A01", [0, 2]), ("A02", [1, 3])]
    assert source.wells_from_files() == ["A01", "A02"]


def test_the_wells_are_in_acquisition_order_not_plate_order(tmp_path):
    """First appearance, which is the order the stage visited them — the same
    rule the one-scene-per-well mapping always used."""
    source = read_czi_set(_one_file(tmp_path, ["H12", "A01", "H12"]))

    assert source.wells_from_files() == ["H12", "A01"]


# ── and what it converts to ──────────────────────────────────────────────────

def test_a_well_s_scenes_become_its_fields_in_acquisition_order(tmp_path):
    """The whole feature, asserted as the pixel values that landed in each
    field of each archive. Interleaved, so the block-chopping mapping this
    replaced would put scene 1 — value 10000 — in A01's f1."""
    dest = tmp_path / "out"

    _run(_one_file(tmp_path, ["A01", "A02", "A01", "A02", "A01", "A02"]), dest)

    assert sorted(p.name for p in (dest / "in").glob("*.zip")) == [
        "A01.zip", "A02.zip"]
    assert _planes(dest, "A01") == {0: _value(0), 1: _value(2), 2: _value(4)}
    assert _planes(dest, "A02") == {0: _value(1), 1: _value(3), 2: _value(5)}


def test_the_tiles_inside_each_scene_are_fields_too(tmp_path):
    """Two scenes of two tiles is four fields, ordered scene-major: a well's
    fields are its scenes laid end to end, each contributing its own tiles."""
    dest = tmp_path / "out"

    _run(_one_file(tmp_path, ["A01", "A02", "A01", "A02"], fovs=2), dest)

    assert _planes(dest, "A01") == {
        0: _value(0, 0), 1: _value(0, 1), 2: _value(2, 0), 3: _value(2, 1)}


def test_a_split_scene_export_groups_across_its_files(tmp_path):
    """The reported shape: one file per imaged position, several positions per
    well. Nothing inside any one file says it is part of a plate."""
    dest = tmp_path / "out"

    _run(_split(tmp_path, ["A01", "A02", "A01", "A02", "A01", "A02"]), dest)

    assert sorted(p.name for p in (dest / "in").glob("*.zip")) == [
        "A01.zip", "A02.zip"]
    # Every file holds its own scene 0, so the value is the same in all six —
    # what the mapping decides is which archive each lands in, and as which
    # field. Three fields per well, from three different files.
    assert sorted(_planes(dest, "A01")) == [0, 1, 2]
    with zipfile.ZipFile(dest / "in" / "A01.zip") as archive:
        assert len(archive.namelist()) == 3


# ── what the page says before the run ────────────────────────────────────────

def test_the_plan_counts_the_fields_a_well_actually_has(tmp_path):
    src = _one_file(tmp_path, ["A01", "A02", "A01", "A02", "A01", "A02"])
    params = ConversionParams(experiment="E", well_source="file")

    plan = plan_conversion(read_source(src, params), params)

    assert plan.well_labels == ["A01", "A02"]
    assert plan.fovs_per_well == 3
    assert plan.field_counts == [3, 3]
    assert plan.n_scenes == 6
    assert "2 well(s) A01→A02" in plan.summary()
    assert "3 FOV(s)" in plan.summary()
    assert "6 image(s)" in plan.summary()


def test_the_set_s_own_line_says_the_scenes_were_grouped(tmp_path):
    """"96 well(s)" over a 660-scene export reads exactly like a plate that
    only ever held ninety-six scenes, so both numbers are printed."""
    source = read_czi_set(_one_file(tmp_path, ["A01", "A02", "A01", "A02"]))

    assert "(2, holding the 4 scene(s) between them)" in source.describe()


# ── a plate whose wells were not all imaged the same number of times ─────────

def test_an_uneven_plate_converts_with_each_well_s_own_fields(tmp_path):
    """Nothing obliges an acquisition to visit every well equally, and
    refusing the plate for it would be the missing-plane refusal again."""
    dest = tmp_path / "out"

    _run(_one_file(tmp_path, ["A01", "A02", "A01", "A02", "A01"]), dest)

    assert _planes(dest, "A01") == {0: _value(0), 1: _value(2), 2: _value(4)}
    assert _planes(dest, "A02") == {0: _value(1), 1: _value(3)}


def test_the_readout_names_the_wells_that_hold_fewer(tmp_path):
    """A plate described by its largest well and nothing else is
    indistinguishable from a plate whose wells all hold that many (§7)."""
    src = _one_file(tmp_path, ["A01", "A02", "A01", "A02", "A01"])
    params = ConversionParams(experiment="E", well_source="file")

    plan = plan_conversion(read_source(src, params), params)

    assert plan.fovs_per_well == 3
    assert plan.field_counts == [3, 2]
    assert "1 well(s) hold fewer than 3 field(s): A02 (2)." in plan.summary()
    assert "up to 3 FOV(s)" in plan.summary()
    # Five images, not the six a 2 x 3 rectangle would have.
    assert "5 image(s)" in plan.summary()


def test_the_run_log_says_it_too(tmp_path):
    lines: list[str] = []

    _run(_one_file(tmp_path, ["A01", "A02", "A01", "A02", "A01"]),
         tmp_path / "out", log=lines)

    assert any("1 well(s) hold fewer than 3 field(s): A02 (2)." in line
               for line in lines), lines


def test_an_even_plate_says_nothing_about_evenness(tmp_path):
    """Seeing the sentence has to mean something (§7.8: "it warns" is
    satisfied by a readout that always warns)."""
    src = _one_file(tmp_path, ["A01", "A02", "A01", "A02"])
    params = ConversionParams(experiment="E", well_source="file")
    lines: list[str] = []

    plan = plan_conversion(read_source(src, params), params)
    _run(src, tmp_path / "out", log=lines)

    assert plan.uneven_note == ""
    assert "hold fewer" not in plan.summary()
    assert not any("hold fewer" in line for line in lines)


def test_progress_is_denominated_by_the_images_the_run_writes(tmp_path):
    """The total came from ``n_wells x fovs_per_well``, which on an uneven
    plate is a rectangle the run never fills — so the bar would stop short of
    its own end and the run would look interrupted."""
    seen: list[tuple[int, int]] = []
    params = ConversionParams(experiment="E", well_source="file")

    convert(str(_one_file(tmp_path, ["A01", "A02", "A01", "A02", "A01"])),
            str(tmp_path / "out"), params, log=lambda _m: None,
            is_cancelled=lambda: False,
            progress=lambda done, total: seen.append((done, total)),
            well_progress=None)

    assert seen[-1] == (5, 5), seen[-1]


def test_a_short_well_resumes_on_its_own_field_count(tmp_path):
    """Resume asks whether the archive holds every member these settings call
    for. Asking for the plate's nominal three of a well that has two would
    convert it again on every attempt, for ever."""
    from pipeline.czi_explode import explode_czi

    src = _one_file(tmp_path, ["A01", "A02", "A01", "A02", "A01"])
    dest = tmp_path / "out"
    _run(src, dest)
    lines: list[str] = []

    # Driven through the exploder, which is where ``resume`` is decided —
    # ``ConversionParams`` has no such field; the page sets it from the
    # destination marker.
    explode_czi(src, dest / "in", well_source="file", experiment="E",
                resume=True, log_fn=lines.append)

    assert any("2 of 2 well(s) are already converted" in line
               for line in lines), lines


# ── and the settings that interact with it ───────────────────────────────────

def test_a_fovs_per_well_override_is_checked_against_the_grouped_count(tmp_path):
    """It used to be checked against the tiles in one scene, which on a plate
    of three-scene wells is a third of the truth."""
    src = _one_file(tmp_path, ["A01", "A02", "A01", "A02", "A01", "A02"])
    params = ConversionParams(experiment="E", well_source="file",
                              fovs_per_well=3)

    plan = plan_conversion(read_source(src, params), params)

    assert plan.fovs_per_well == 3
    with pytest.raises(Exception, match="holds 3 field"):
        bad = ConversionParams(experiment="E", well_source="file",
                               fovs_per_well=1)
        plan_conversion(read_source(src, bad), bad)


def test_a_subset_run_writes_only_the_wells_it_was_given(tmp_path):
    """The plate picker's wells are the grouped labels, not scene names."""
    dest = tmp_path / "out"

    _run(_one_file(tmp_path, ["A01", "A02", "A01", "A02"]), dest,
         well_subset=("A02",))

    assert [p.name for p in (dest / "in").glob("*.zip")] == ["A02.zip"]
    assert _planes(dest, "A02") == {0: _value(1), 1: _value(3)}


def test_a_well_named_once_still_has_one_field(tmp_path):
    """The case every plate had before this, unchanged."""
    dest = tmp_path / "out"

    _run(_one_file(tmp_path, ["A01", "A02"]), dest)

    assert _planes(dest, "A01") == {0: _value(0)}
    assert _planes(dest, "A02") == {0: _value(1)}


def test_the_images_are_real_and_not_a_count(tmp_path):
    """Guarding the helper the assertions above are made through: a
    ``_planes`` that returned an empty mapping would satisfy none of them,
    but one that read the wrong axis could satisfy all."""
    dest = tmp_path / "out"

    _run(_one_file(tmp_path, ["A01", "A01"]), dest)

    with zipfile.ZipFile(dest / "in" / "A01.zip") as archive:
        names = sorted(archive.namelist())
        assert names == ["E_DAPI_A01_f0_0h.tif", "E_DAPI_A01_f1_0h.tif"]
        with archive.open(names[1]) as handle:
            plane = tifffile.imread(handle)
    assert plane.shape == (4, 6)
    assert np.array_equal(plane, np.full((4, 6), _value(1), dtype=plane.dtype))
