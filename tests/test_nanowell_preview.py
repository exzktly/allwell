"""Test detection's analysis: one well at one timepoint, described for a person (tracker A4)."""

from __future__ import annotations

import sys

import pytest

import process_microscopy as pm
from pipeline import nanowell_run as nr
from pipeline.nanowell_preview import preview_timepoint
from tests._nanowell_run_fixture import build_well, paint_cell, threshold_segmenter
from tests.test_nanowell_cli import SCHEMA, WELL, _write_plate

SETTINGS = nr.NanowellSettings("100um")


@pytest.fixture(scope="module")
def plate(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("preview")
    rendered, record = build_well(timepoint="0h", overlap=0.03)           # a short overlap, so seams are fused
    truth = dict(zip(rendered.array.addresses, rendered.sites_um))
    for address in ((1, 2), (1, 3), (-2, 4)):
        paint_cell(rendered, truth[address], dx_um=3.0)
    root = _write_plate(tmp, rendered, record)
    return root / "in" / f"{WELL}.zip", tmp


def _preview(plate, tmp_path, **kwargs):
    zip_path, _ = plate
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return preview_timepoint(zip_path, WELL, "0h", SETTINGS, grouper=pm.find_image_groups_in_zip,
                             parse=pm.parse_filename, schema=SCHEMA, sep="_", phase_token="PHASE",
                             scratch=scratch, segmenter=threshold_segmenter(), **kwargs)


def test_the_preview_describes_the_well_it_was_asked_about(plate, tmp_path):
    result = _preview(plate, tmp_path)
    assert result.status == nr.OK and result.well == WELL and result.timepoint == "0h"
    assert result.montage is not None and result.montage.dtype.name == "uint8" and result.um_per_px > 0
    assert result.origin_method == "fiducial" and result.tile_count == 4
    assert result.pitch_um == pytest.approx(172.0, rel=0.01)
    by_address = {s["address"]: s for s in result.sites if s["address"]}
    assert len(by_address) > 200
    assert by_address["1,2"]["n_cells"] == 1 and by_address["0,1"]["n_cells"] == 0


def test_the_overlap_is_reported_against_the_one_a_nanowell_needs(plate, tmp_path):
    result = _preview(plate, tmp_path)
    # The measured diameter plus a tenth of it on each side, as the run's ownership needs (review F21a); the format's
    # 100 um gave exactly 120.
    assert result.diameter_um == pytest.approx(100.0, rel=0.01) and result.diameter_um != 100.0
    assert result.overlap_needed_um == pytest.approx(1.2 * result.diameter_um)
    measured = [o for o in result.overlap_um if o is not None]
    assert measured and all(o < result.overlap_needed_um for o in measured)       # 3 % of 3010 um = 90 um
    counts = result.counts()
    assert counts.get("fused", 0) > 0 and counts["empty"] > 100                  # sites on the seams are marked fused


def test_a_few_crops_show_the_rim_suppressed_site_beside_its_segmentation(plate, tmp_path):
    result = _preview(plate, tmp_path, n_crops=3)
    assert 1 <= len(result.crops) <= 3
    full = [c for c in result.crops if c["n_cells"]]
    empty = [c for c in result.crops if c["n_cells"] == 0]
    assert full and empty                                                # one with cells, one without
    for crop in result.crops:
        assert crop["rim"].shape == crop["labels"].shape and crop["rim"].ndim == 2
    assert all((c["labels"] > 0).any() for c in full) and not any((c["labels"] > 0).any() for c in empty)


def test_a_refusal_is_a_status_with_a_reason_not_an_exception(plate, tmp_path):
    zip_path, _ = plate
    (zip_path.parent / "acquisition.json").rename(zip_path.parent / "acquisition.json.bak")
    try:
        result = _preview(plate, tmp_path)
    finally:
        (zip_path.parent / "acquisition.json.bak").rename(zip_path.parent / "acquisition.json")
    assert result.status == "error" and "acquisition record" in result.reason and result.montage is None
    missing = preview_timepoint(zip_path, WELL, "99h", SETTINGS, grouper=pm.find_image_groups_in_zip,
                                parse=pm.parse_filename, schema=SCHEMA, sep="_", phase_token="PHASE",
                                scratch=tmp_path / "s2")
    assert missing.status == "error" and "99h" in missing.reason


def test_test_detection_refuses_a_described_grid_the_well_s_fields_do_not_fill(tmp_path):
    """The run places a described grid against its shape; Test detection must agree with it, or it shows a
    smaller grid as fine and the plate-wide run then refuses every well."""
    rendered, record = build_well(timepoint="0h")
    root = _write_plate(tmp_path, rendered, record)
    (root / "in" / "acquisition.json").unlink()
    pattern = {"grid": [3, 2], "scan": "top-left-row", "overlap_percent": 10.0, "pixel_size_um": 4.3}
    result = _preview((root / "in" / f"{WELL}.zip", tmp_path), tmp_path, pattern=pattern)
    assert result.status == nr.INCOMPLETE_GRID


def test_a_measuring_failure_keeps_the_location_and_says_why(plate, tmp_path):
    """Roll-up review X27: a segmenter that raised took the whole preview with it, the lattice it had found
    included. The location stands, with the failure named in the notes."""
    def broken(_tile):
        raise RuntimeError("the segmenter is not installed")

    result = preview_timepoint(plate[0], WELL, "0h", SETTINGS, grouper=pm.find_image_groups_in_zip,
                               parse=pm.parse_filename, schema=SCHEMA, sep="_", phase_token="PHASE",
                               scratch=_scratch(tmp_path), segmenter=broken)
    assert result.status == nr.OK and result.montage is not None and result.pitch_um == pytest.approx(172.0, rel=0.01)
    assert result.sites == []
    assert result.notes == ["Measuring failed (RuntimeError: the segmenter is not installed); the lattice and "
                            "addresses above stand."]


def _scratch(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return scratch


def test_test_detection_on_an_inferred_geometry_labels_every_site_of_a_square_array(tmp_path):
    """Tracker X35: Test detection locates the way the run will, so an inferred square array that is not a circle
    is measured against its own sites (it has no boundary radius) and every drawn address is shown."""
    rendered, record = build_well(timepoint="0h", lattice="square", array_shape="square", rotation=-15.0,
                                  radius_fraction=0.22, square_centre=False)
    zip_path = _write_plate(tmp_path, rendered, record) / "in" / f"{WELL}.zip"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = preview_timepoint(zip_path, WELL, "0h", nr.NanowellSettings("100um", geometry=nr.GEOMETRY_INFER),
                               grouper=pm.find_image_groups_in_zip, parse=pm.parse_filename, schema=SCHEMA, sep="_",
                               phase_token="PHASE", scratch=scratch, segmenter=threshold_segmenter())
    assert result.status == nr.OK and result.origin_method == "array_centre"
    assert {s["address"] for s in result.sites} == {f"{q},{r}" for q, r in rendered.array.addresses}


def test_test_detection_says_its_rim_is_one_timepoint_s_not_the_run_s(plate, tmp_path):
    """Tracker X35a-R5: the preview sees one timepoint, a run decides the rim over all of them and keeps an analysed
    dataset's roster; the preview says so rather than show a set the run may not write."""
    result = _preview(plate, tmp_path)
    assert any("a run decides them over all of the well's timepoints" in n for n in result.notes)


def _segmenter_that_cannot_load():
    """What ``pipeline.cellpose`` raises when the package is not installed: a RuntimeError from the ImportError."""
    def segment(_tile):
        try:
            raise ModuleNotFoundError("No module named 'cellpose'")
        except ModuleNotFoundError as exc:
            raise RuntimeError("Cellpose segmentation requires the 'cellpose' package") from exc
    return segment


def test_without_cellpose_the_nanowells_are_still_found_and_say_they_were_not_counted(plate, tmp_path):
    """Reported: Test detection failed because Cellpose was absent. Finding the nanowells needs no segmenter;
    only counting the cells in them does. So the array, its addresses and every site's state are shown, no site
    carries a count (None, never 0, so no nanowell reads as empty), and the notes say why."""
    (tmp_path / "a").mkdir()
    counted = _preview(plate, tmp_path / "a")
    result = preview_timepoint(plate[0], WELL, "0h", SETTINGS, grouper=pm.find_image_groups_in_zip,
                               parse=pm.parse_filename, schema=SCHEMA, sep="_", phase_token="PHASE",
                               scratch=_scratch(tmp_path), segmenter=_segmenter_that_cannot_load())
    assert result.status == nr.OK and result.counted is False
    assert [(s["address"], s["state"] if s["state"] != "empty" else "measured") for s in result.sites] == \
        [(s["address"], s["state"] if s["state"] != "empty" else "measured") for s in counted.sites]
    assert all(s["n_cells"] is None for s in result.sites)
    assert "empty" not in result.counts() and result.counts()["not counted"] > 200
    assert result.crops == []
    assert result.notes[0] == (
        "Cells were not counted: Cellpose could not be loaded (ModuleNotFoundError: No module named 'cellpose'). The "
        "nanowells, their addresses and states are found without it; a run needs it to count cells, so install it "
        "(see requirements) before starting one.")


def test_the_real_default_segmenter_without_cellpose_installed_is_the_same_case(plate, tmp_path):
    """The path the app takes: no segmenter handed in, so the preview builds Cellpose's, which cannot load here."""
    import importlib.util

    if importlib.util.find_spec("cellpose") is not None:
        pytest.skip("Cellpose is installed in this environment")
    result = preview_timepoint(plate[0], WELL, "0h", SETTINGS, grouper=pm.find_image_groups_in_zip,
                               parse=pm.parse_filename, schema=SCHEMA, sep="_", phase_token="PHASE",
                               scratch=_scratch(tmp_path))
    assert result.counted is False and len([s for s in result.sites if s["address"]]) > 200
    assert result.notes[0].startswith("Cells were not counted: Cellpose could not be loaded (")


def _installed_as(root, files: dict):
    """Write a fake package tree under *root* (``{"cellpose/__init__.py": source, ...}``) to put on the import path."""
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


_FAKE_TORCH = {"torch/__init__.py": (
    "class _Cuda:\n    @staticmethod\n    def is_available():\n        return False\n"
    "cuda = _Cuda()\nclass version:\n    cuda = None\n__version__ = '0'\n"
    "class device:\n    def __init__(self, kind):\n        self.type = kind\n")}


@pytest.mark.parametrize("label, files, cause", [
    # A Torch library that will not load: the import raises OSError, not ImportError (reported: Test detection
    # still failed without Cellpose, drawing no nanowell, with "Measuring failed (RuntimeError: Cellpose
    # segmentation requires the 'cellpose' package ...").
    ("a broken install", {"cellpose/__init__.py": "raise OSError('dlopen(libtorch_cpu.dylib): image not found')\n"},
     "OSError: dlopen(libtorch_cpu.dylib): image not found"),
    # Installed, but its model's weights cannot be fetched on first use (offline).
    ("an offline first use", {"cellpose/__init__.py": "", "cellpose/models.py": (
        "import urllib.error\nclass CellposeModel:\n    def __init__(self, *a, **k):\n"
        "        raise urllib.error.URLError('cannot download cyto3')\n"), **_FAKE_TORCH},
     "URLError: <urlopen error cannot download cyto3>"),
])
def test_cellpose_that_cannot_be_loaded_for_any_reason_is_the_same_case(plate, tmp_path, monkeypatch, label, files,
                                                                        cause):
    """Not only a missing package: whatever stops Cellpose loading leaves the nanowells found and uncounted, through
    the real default segmenter."""
    import sys

    from pipeline import cellpose as pc

    for name in [m for m in sys.modules if m == "cellpose" or m.startswith(("cellpose.", "torch"))]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(_installed_as(tmp_path / "site", files)))
    monkeypatch.setattr(pc, "_CELLPOSE_MODELS", {})
    result = preview_timepoint(plate[0], WELL, "0h", SETTINGS, grouper=pm.find_image_groups_in_zip,
                               parse=pm.parse_filename, schema=SCHEMA, sep="_", phase_token="PHASE",
                               scratch=_scratch(tmp_path))
    assert result.counted is False and len([s for s in result.sites if s["address"]]) > 200, label
    assert result.notes[0].startswith(f"Cells were not counted: Cellpose could not be loaded ({cause})")


def test_a_counted_preview_counts_its_fused_sites(plate, tmp_path):
    """A fused site (on a tile seam) is counted in a preview as on a tile — from a blended crop — and keeps its flag."""
    result = _preview(plate, tmp_path)
    assert result.counted is True and "not counted" not in result.counts()
    fused = [s for s in result.sites if s["fused"] and s["state"] in ("measured", "empty")]
    assert fused and all(s["n_cells"] is not None for s in fused)


# ── in a child process (tracker F23) ────────────────────────────────────────
def _job(plate):
    zip_path, _ = plate
    return {"zip": zip_path, "well": WELL, "timepoint": "0h", "settings": SETTINGS, "pattern": None,
            "schema": ":".join(SCHEMA), "sep": "_", "phase_token": "PHASE"}


def test_a_real_child_process_finds_the_nanowells_and_hands_them_back(plate, monkeypatch):
    """The source-tree child, spawned by path: its ``sys.path`` repair, the pickles both ways, and the analysis."""
    import subprocess

    import services.nanowell_preview_job as job_mod

    commands = []

    def runner(argv, **kwargs):
        commands.append(argv)
        return subprocess.run(argv, **kwargs)

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    result = job_mod.run_preview(_job(plate), runner=runner)
    assert len(commands) == 1 and commands[0][1].endswith("nanowell_preview_job.py")
    assert result.status == nr.OK and result.origin_method == "fiducial" and result.well == WELL
    assert len([s for s in result.sites if s["address"]]) > 200
    # Cellpose is not part of the test stack: without it the child says so and draws the array uncounted.
    assert result.counted is _cellpose_installed()


def test_a_child_killed_by_the_segmenter_s_crash_still_draws_the_nanowells_uncounted(plate, monkeypatch):
    """The first child dies natively, as ``import torch`` does on the reporter's Windows install (0xC0000138); the
    second is the real child with the segmenter refused, and the app's process loaded neither."""
    import subprocess

    import services.nanowell_preview_job as job_mod

    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:                        # a real native crash, with nothing written
            argv = [sys.executable, "-c", "import faulthandler; faulthandler._sigsegv()"]
        return subprocess.run(argv, **kwargs)

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    result = job_mod.run_preview(_job(plate), runner=runner)
    assert len(calls) == 2
    assert result.status == nr.OK and result.counted is False
    assert len([s for s in result.sites if s["address"]]) > 200
    note = result.notes[0]
    assert note.startswith("Cells were not counted") and "the process that loaded it crashed" in note
    assert "the app was not affected" in note


def _cellpose_installed() -> bool:
    """Asked of a fresh interpreter, as the child is one: this process may hold a stand-in ``cellpose`` another
    test left in ``sys.modules``."""
    import subprocess

    probe = "import importlib.util, sys; sys.exit(importlib.util.find_spec('cellpose') is None)"
    return subprocess.run([sys.executable, "-c", probe], check=False).returncode == 0


def test_the_crops_are_cut_from_the_tile_segmented_whole_as_the_run_segments_it(plate, tmp_path):
    """Tracker L3. A crop was once segmented on its own; Cellpose scales each image by its own percentiles, so a crop
    holding one nanowell was not segmented as the tile holding a hundred is, and its outlines were not the run's.
    Every call to the segmenter is now a whole tile, and each crop's labels are a window of one such call's."""
    import numpy as np

    calls = []
    inner = threshold_segmenter()

    def recording(rim):
        labels = inner(rim)
        calls.append((np.array(rim), labels))
        return labels

    zip_path, _ = plate
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = preview_timepoint(zip_path, WELL, "0h", SETTINGS, grouper=pm.find_image_groups_in_zip,
                               parse=pm.parse_filename, schema=SCHEMA, sep="_", phase_token="PHASE",
                               scratch=scratch, segmenter=recording, n_crops=3)
    assert result.crops and {rim.shape for rim, _ in calls} == {(700, 700)}
    for crop in result.crops:
        h, w = crop["rim"].shape
        assert any(np.array_equal(crop["rim"], rim[y:y + h, x:x + w].astype(np.float32))
                   and np.array_equal(crop["labels"], labels[y:y + h, x:x + w])
                   for rim, labels in calls for y in range(rim.shape[0] - h + 1) for x in range(rim.shape[1] - w + 1)
                   if rim[y, x] == crop["rim"][0, 0])


@pytest.mark.parametrize("model", ["cyto3", "livecell_cp3"])
def test_the_count_segments_with_the_model_the_settings_name(plate, tmp_path, monkeypatch, model):
    """Tracker L2, at the boundary: what reaches ``pipeline.cellpose._segment_cellpose`` is the setting's model."""
    import numpy as np

    from pipeline import cellpose

    asked = []

    def fake(image, *, model_type="cyto3", **_):
        asked.append(model_type)
        return np.zeros(np.shape(image), dtype=np.int32)

    monkeypatch.setattr(cellpose, "_segment_cellpose", fake)
    zip_path, _ = plate
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    preview_timepoint(zip_path, WELL, "0h", nr.NanowellSettings("100um", cellpose_model=model),
                      grouper=pm.find_image_groups_in_zip, parse=pm.parse_filename, schema=SCHEMA, sep="_",
                      phase_token="PHASE", scratch=scratch)
    assert asked and set(asked) == {model}
