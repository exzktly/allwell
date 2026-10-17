"""The Playground's nanowell array search: from one well to the Analyze form's Nanowells settings.

Every fixture is a synthetic array drawn by ``tests._nanowell_synth`` at a known pitch, diameter, lattice and
outline, so each suggested field can be asserted as a value rather than as a property of one: the format a plate
of this size and pitch is, the geometry its outline needs, the custom numbers to type, the square's angle.
"""

from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pytest

from review.features.image_browser.playground import nanowell_compute as nc
from tests._nanowell_synth import TilePlan, render_tiles

PX = 4.3


def _frame(*, pitch=172.0, diameter=100.0, radius=1300.0, px=PX, **render):
    plan = TilePlan(1, 1, (900, 900), 0.0, px)
    return render_tiles(plan, pitch_um=pitch, diameter_um=diameter, radius_um=radius, noise_sigma=0.05,
                        blur_px=1.0, **render).images["F0"]


def _fields(suggestion) -> dict[str, str]:
    assert suggestion.ok, suggestion.reason
    return {f.label: f.value for f in suggestion.fields}


def test_the_labels_are_the_analyze_form_s_own():
    """The window tells a person what to pick in two combos: it must name their entries exactly."""
    from analyze.domain.nanowell_fields import GEOMETRY_CHOICES, PLATE_FORMAT_CHOICES
    from pipeline.nanowell_run import GEOMETRIES, PLATE_FORMATS

    assert nc.FORMAT_LABELS == dict(PLATE_FORMAT_CHOICES)
    assert nc.GEOMETRY_LABELS == dict(GEOMETRY_CHOICES)
    assert set(nc.FORMAT_LABELS) == set(PLATE_FORMATS) | {"custom"}
    assert set(nc.GEOMETRY_LABELS) == set(GEOMETRIES)


def test_a_100um_hexagonal_array_in_a_circle_is_the_format_s_own_geometry():
    s = nc.suggest_from_image(_frame(rotation_deg=12.0), PX)
    fields = _fields(s)
    assert fields["Plate format"] == "100 µm nanowells"
    assert fields["Array geometry"] == "Hexagonal lattice in a circle"
    # A known format sets both lengths, so neither custom field is filled in.
    assert fields["Nanowell diameter (µm)"] == "" and fields["Lattice pitch (µm)"] == ""
    assert fields["Square edge angle (°)"] == ""
    # The origin is the drawn central square, at the array's centre (450 px in a 900 px frame).
    assert s.square_px is not None and s.origin_px == pytest.approx((450.0, 450.0), abs=2.0)
    assert len(s.roster_px) == sum(s.roster_found) == len(s.wells_px)


def test_a_square_lattice_needs_the_inferred_geometry():
    fields = _fields(nc.suggest_from_image(_frame(lattice="square", array_shape="square", rotation_deg=-15.0,
                                                  square_centre=False), PX))
    assert fields["Array geometry"] == "Infer from the image"
    assert fields["Square edge angle (°)"] == ""          # no square drawn: nothing measured, the default stands


def test_a_hexagonal_array_whose_outline_is_not_a_circle_needs_the_inferred_geometry():
    fields = _fields(nc.suggest_from_image(_frame(array_shape="square", radius=1100.0, rotation_deg=5.0), PX))
    assert fields["Array geometry"] == "Infer from the image"


def test_a_plate_of_no_known_format_gets_its_measured_diameter_and_pitch():
    s = nc.suggest_from_image(_frame(pitch=120.0, diameter=60.0, px=2.5, radius=900.0), 2.5)
    fields = _fields(s)
    assert fields["Plate format"] == "Custom size"
    assert float(fields["Lattice pitch (µm)"]) == pytest.approx(120.0, abs=1.0)
    # A threshold reads a well a little small (the 100 µm wells above read ~97): within its own tolerance.
    assert float(fields["Nanowell diameter (µm)"]) == pytest.approx(60.0, rel=0.08)


def test_a_wrong_pixel_size_scales_every_length_and_so_changes_the_format():
    """The pixel size is the one input a person types; every suggestion is only as right as it is."""
    fields = _fields(nc.suggest_from_image(_frame(), PX / 2))
    assert fields["Plate format"] == "Custom size"
    assert float(fields["Lattice pitch (µm)"]) == pytest.approx(86.0, abs=1.0)


@pytest.mark.parametrize("pitch, diameter, expected", [
    (172.0, 100.0, "100um"), (143.0, 50.0, "50um"), (150.0, 100.0, "custom"), (172.0, 60.0, "custom")])
def test_a_known_format_is_chosen_only_when_its_size_and_pitch_both_agree(pitch, diameter, expected):
    assert nc.choose_format(pitch, diameter)[0] == expected


def test_a_custom_choice_beside_a_known_size_says_which_length_disagreed():
    name, note = nc.choose_format(150.0, 100.0)
    assert name == "custom" and "100 µm nanowells" in note and "150.0 µm" in note and "172" in note


def _turned_square(monkeypatch, extra_deg: float) -> None:
    """The central square scored with its edges *extra_deg* past where the drawing has them (the synthetic
    always draws the square along a lattice row)."""
    from dataclasses import replace

    from pipeline import nanowell_detect

    real = nanowell_detect.score_fiducial

    def turned(*args, **kwargs):
        candidate, scores = real(*args, **kwargs)
        return candidate, [replace(s, orientation_deg=s.orientation_deg + extra_deg) for s in scores]

    monkeypatch.setattr(nanowell_detect, "score_fiducial", turned)


def test_the_square_edge_angle_is_the_square_s_edges_against_the_lattice_row(monkeypatch):
    # The lattice is turned 12 degrees, so the square's edges read 32: what the form takes is the 20 between them.
    _turned_square(monkeypatch, 20.0)
    s = nc.suggest_from_image(_frame(rotation_deg=12.0), PX)
    assert _fields(s)["Square edge angle (°)"] == "20"
    assert any("30°" in n for n in s.notes)            # the hexagonal fold is named beside a non-default angle


def test_a_square_within_a_few_degrees_of_a_row_keeps_the_default(monkeypatch):
    _turned_square(monkeypatch, 3.0)
    s = nc.suggest_from_image(_frame(rotation_deg=12.0), PX)
    edge = next(f for f in s.fields if f.label == "Square edge angle (°)")
    assert edge.value == "" and "+3." in edge.note


def test_an_image_with_no_nanowells_says_why_and_suggests_nothing():
    rng = np.random.default_rng(0)
    s = nc.suggest_from_image(rng.normal(0.3, 0.02, (400, 400)).astype(np.float32), PX)
    assert not s.ok and s.fields == [] and "light" in s.reason


@pytest.mark.parametrize("px", [0.0, -1.0, float("nan")])
def test_a_pixel_size_that_is_not_a_length_is_refused(px):
    s = nc.suggest_from_image(_frame(), px)
    assert not s.ok and "pixel size" in s.reason


# ── a well archive ───────────────────────────────────────────────────────────
def _archive(tmp_path, *, record=True, channels=("Phase", "GFP")):
    import tifffile

    from tests._nanowell_run_fixture import build_well

    rendered, rec = build_well(rows=2, cols=2, rotation=9.0, radius_fraction=0.3)
    path = tmp_path / "A01.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, image in rendered.images.items():
            for channel in channels:
                buffer = io.BytesIO()
                # The fluorescence channel is blank: the stitched channel must be the phase one.
                tifffile.imwrite(buffer, image if channel == "Phase" else np.zeros_like(image))
                zf.writestr(f"exp_{channel}_A01_{name}_T0.tif", buffer.getvalue())
        # What a real archive also carries (CLAUDE.md §7.1 #4).
        zf.writestr(".DS_Store", b"\0")
        zf.writestr("__MACOSX/._exp_Phase_A01_F0_T0.tif", b"\0")
        zf.writestr("exp_Phase_A01/", b"")
    if record:
        (tmp_path / "acquisition.json").write_text(json.dumps(rec))
    return path


def test_a_well_archive_is_stitched_from_its_record_and_its_phase_tiles(tmp_path):
    montage = nc.montage_from_archive(_archive(tmp_path))
    assert montage.ok, montage.reason
    assert (montage.well, montage.timepoint, montage.channel) == ("A01", "T0", "Phase")
    assert dict(montage.facts)["Tiles"] == "4 (2 × 2 grid)"
    s = nc.suggest_from_image(montage.image, montage.um_per_px, covered=montage.covered, record_places_tiles=True)
    fields = _fields(s)
    assert fields["Plate format"] == "100 µm nanowells"
    assert fields["Array geometry"] == "Hexagonal lattice in a circle"
    grid = next(f for f in s.fields if f.key == "nanowell_grid")
    assert grid.value == "" and "acquisition record" in grid.note


def test_an_archive_with_no_record_beside_it_says_so_and_points_at_the_frame_route(tmp_path):
    montage = nc.montage_from_archive(_archive(tmp_path, record=False))
    assert not montage.ok and "acquisition record" in montage.reason and "From this frame" in montage.reason


def test_the_dataset_s_own_filename_schema_reads_the_archive(tmp_path):
    """A dataset named well-first is read with its own slots, not the default order."""
    src = _archive(tmp_path)
    renamed = tmp_path / "other" / "A01.zip"
    renamed.parent.mkdir()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(renamed, "w") as zout:
        for name in zin.namelist():
            if name.endswith(".tif") and not name.startswith("__MACOSX"):
                exp, channel, well, fov, tp = name[:-4].split("_")
                zout.writestr(f"{well}-{fov}-{tp}-{channel}.tif", zin.read(name))
    (renamed.parent / "acquisition.json").write_text((tmp_path / "acquisition.json").read_text())
    assert not nc.montage_from_archive(renamed).ok
    montage = nc.montage_from_archive(renamed, fields=("well", "fov", "timepoint", "channel"), sep="-")
    assert montage.ok, montage.reason
    assert montage.channel == "Phase"
