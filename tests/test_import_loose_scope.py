"""Import Loose's in-place mode must state everything it will delete inside.

The in-place conversion's scope is **every** well-named subfolder under the
source, not only the wells it groups out of the loose TIFs: a folder a user (or
an earlier tool) organised by hand is zipped and has its TIFs deleted too. That
is deliberate — it is what leaves the folder in one uniform, ready-to-run layout,
and what lets an interrupted run resume, since a crashed attempt's already
grouped wells have no loose TIFs left to find.

What was wrong is that nothing said so. ``scan_folder`` is loose-TIF-only and
non-recursive, so the readout, the plate preview and the confirmation dialog all
described a run over N wells while the run acted on N plus however many folders
were already there. On the pane's one destructive path, preview and apply have
to be the same list.

Qt-free (the compute half) and fast lane. The page-level wiring is checked
through the accessors the dialog reads, not by opening the dialog — a modal
blocks forever under the offscreen QPA.
"""

from __future__ import annotations

import zipfile

from datasets.import_loose.apply import _zip_well_folders
from datasets.import_loose.import_loose_compute import scan_folder

_SCHEMA = "experiment:channel:well:fov:timepoint"


def _folder_with_loose_tifs_and_pre_existing_wells(root):
    root.mkdir(parents=True)
    # What the user pointed the tool at.
    (root / "exp_GFP_A01_F001_24h.tif").write_bytes(b"loose-a01")
    (root / "exp_GFP_B02_F001_24h.tif").write_bytes(b"loose-b02")
    # What was already sitting beside it, with a companion file the zip's
    # .tif/.tiff filter will not take.
    (root / "C03").mkdir()
    (root / "C03" / "exp_GFP_C03_F001_0h.tif").write_bytes(b"pre-existing")
    (root / "C03" / "acquisition.log").write_text("not an image")
    (root / "D04").mkdir()
    (root / "D04" / "exp_GFP_D04_F001_0h.tif").write_bytes(b"pre-existing-2")
    # Folders that are not wells, and a well folder holding no images: neither
    # is in scope, so neither may be announced.
    (root / "notes").mkdir()
    (root / "notes" / "readme.txt").write_text("free text")
    (root / "E05").mkdir()
    (root / "E05" / "plan.txt").write_text("no images here")
    return root


def test_the_preview_names_the_well_folders_an_in_place_run_will_also_zip(tmp_path):
    src = _folder_with_loose_tifs_and_pre_existing_wells(tmp_path / "plate")

    preview = scan_folder(src, filename_schema=_SCHEMA, filename_sep="_")

    assert preview.wells == ("A01", "B02")
    assert preview.existing_well_folders == ("C03", "D04")
    note = preview.existing_folder_note()
    assert "C03" in note and "D04" in note
    assert "deleted" in note
    # Not a well, and a well folder with no TIFs in it: out of scope both ways.
    assert "notes" not in note
    assert "E05" not in note


def test_the_announced_folders_are_exactly_the_ones_the_run_converts(tmp_path):
    """Preview and apply, measured against each other rather than each against
    its own idea of the folder."""
    src = _folder_with_loose_tifs_and_pre_existing_wells(tmp_path / "plate")
    announced = scan_folder(
        src, filename_schema=_SCHEMA, filename_sep="_").existing_well_folders

    converted = _zip_well_folders(src)

    assert converted == len(announced) == 2
    assert {z.stem for z in src.glob("*.zip")} == set(announced)
    assert zipfile.ZipFile(src / "C03.zip").namelist() == ["exp_GFP_C03_F001_0h.tif"]
    # The companion file kept the folder standing; the TIF it archived is gone.
    assert (src / "C03" / "acquisition.log").exists()
    assert not (src / "C03" / "exp_GFP_C03_F001_0h.tif").exists()


def test_a_folder_with_no_pre_existing_well_folders_says_nothing(tmp_path):
    src = tmp_path / "plate"
    src.mkdir()
    (src / "exp_GFP_A01_F001_24h.tif").write_bytes(b"img")

    preview = scan_folder(src, filename_schema=_SCHEMA, filename_sep="_")
    assert preview.existing_well_folders == ()
    assert preview.existing_folder_note() == ""


def test_the_run_summary_names_the_extra_scope(tmp_path, monkeypatch):
    """The log drawer is hidden by default, so the count has to reach the
    summary — what the dashboard headline shows and what the Job Queue records.

    ``run_zipper`` shells out to a subprocess; it is stubbed to do what it does
    to the folder (group the loose TIFs into per-well folders) without the
    subprocess, so this exercises ``convert_in_place``'s own bookkeeping.
    """
    import datasets.import_loose.apply as apply_mod
    import services.input_resolution_service as irs

    src = _folder_with_loose_tifs_and_pre_existing_wells(tmp_path / "plate")

    def _fake_run_zipper(tif_folder, out_folder, *, progress_fn=None, **_kwargs):
        for tif in sorted(tif_folder.glob("*.tif")):
            well = tif.name.split("_")[2]
            (out_folder / well).mkdir(exist_ok=True)
            tif.rename(out_folder / well / tif.name)
            if progress_fn:
                progress_fn(well)

    monkeypatch.setattr(irs, "run_zipper", _fake_run_zipper)

    summary = apply_mod.convert_in_place(
        src, filename_schema=_SCHEMA, filename_sep="_")

    assert "C03" in summary and "D04" in summary
    assert "already in the source" in summary
