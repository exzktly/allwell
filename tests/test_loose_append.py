"""Tests for folding loose image files into already-grouped well zips.

Covers ``services.input_resolution_service.append_loose_images_to_zips`` — the
step that lets a user drop extra TIFFs next to existing per-well zips and have
them absorbed (appended into the matching zip, or a fresh zip for a new well)
before the pipeline runs. Stdlib-only, so it runs without the imaging stack.
"""

import zipfile
from pathlib import Path

from services.input_resolution_service import (
    append_loose_images_to_zips,
    resolve_input_output,
    select_incremental_scope,
)

SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


def _make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_appends_new_file_into_existing_well_zip(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    (tmp_path / "exp_w1_A01_2_T01.tif").write_bytes(b"newfov")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 1
    assert wells == {"A01"}
    names = set(zipfile.ZipFile(tmp_path / "A01.zip").namelist())
    assert names == {"exp_w1_A01_1_T01.tif", "exp_w1_A01_2_T01.tif"}
    # original member preserved verbatim
    assert zipfile.ZipFile(tmp_path / "A01.zip").read("exp_w1_A01_1_T01.tif") == b"old"
    # loose source consumed
    assert not (tmp_path / "exp_w1_A01_2_T01.tif").exists()


def test_creates_zip_for_new_well(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    (tmp_path / "exp_w1_C03_1_T01.tif").write_bytes(b"newwell")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 1
    assert "C03" in wells
    assert (tmp_path / "C03.zip").exists()
    assert zipfile.ZipFile(tmp_path / "C03.zip").namelist() == ["exp_w1_C03_1_T01.tif"]
    assert not (tmp_path / "exp_w1_C03_1_T01.tif").exists()


def test_duplicate_member_keeps_existing_and_keeps_loose(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    (tmp_path / "exp_w1_A01_1_T01.tif").write_bytes(b"newer-dup")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 0
    assert wells == set()
    # existing member untouched, loose duplicate left in place (not deleted)
    assert zipfile.ZipFile(tmp_path / "A01.zip").read("exp_w1_A01_1_T01.tif") == b"old"
    assert (tmp_path / "exp_w1_A01_1_T01.tif").exists()


def test_unparsable_and_nonimage_files_untouched(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    bad = tmp_path / "exp_w1_ZZ9_1_T01.tif"   # ZZ9 is not a valid 96-well token
    bad.write_bytes(b"junk")
    notes = tmp_path / "readme.txt"
    notes.write_bytes(b"hi")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 0
    assert bad.exists() and notes.exists()


def test_no_op_when_no_well_zip_present(tmp_path):
    # Folder-grouped / loose-only layouts are left to the normal zipper path.
    (tmp_path / "exp_w1_A01_1_T01.tif").write_bytes(b"x")
    (tmp_path / "A01").mkdir()

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 0
    assert wells == set()
    assert (tmp_path / "exp_w1_A01_1_T01.tif").exists()


def _opts(tmp_path, **over):
    o = dict(filename_schema=SCHEMA, filename_sep=SEP,
             flatfield="gaussian", background="sliding_disk", background_well="")
    o.update(over)
    return o


def _stage_with_new_loose(tmp_path, *, well="A01", bg=False):
    _make_zip(tmp_path / f"{well}.zip", {f"exp_w1_{well}_1_T01.tif": b"old"})
    (tmp_path / f"exp_w1_{well}_2_T01.tif").write_bytes(b"new")


def test_scope_incremental_for_per_well_models(tmp_path):
    _stage_with_new_loose(tmp_path)
    opts = _opts(tmp_path)
    select_incremental_scope(opts, tmp_path)
    assert opts.get("incremental") is True
    assert not opts.get("force")


def test_scope_forces_full_for_basic_flatfield(tmp_path):
    _stage_with_new_loose(tmp_path)
    opts = _opts(tmp_path, flatfield="basic")
    select_incremental_scope(opts, tmp_path)
    assert opts.get("force") is True
    assert opts.get("incremental") is False


def test_scope_forces_full_when_empty_well_background_touched(tmp_path):
    # New blank lands on the empty-well background well at a timepoint that
    # already had a blank (T01) → that timepoint's per-timepoint field changes,
    # so already-processed wells at T01 are stale → force a full reprocess.
    _stage_with_new_loose(tmp_path, well="A01")   # existing T01 + new FOV at T01
    opts = _opts(tmp_path, background="empty_well", background_well="A01")
    select_incremental_scope(opts, tmp_path)
    assert opts.get("force") is True


def test_scope_incremental_when_empty_well_background_new_timepoint(tmp_path):
    # Empty-well background, and the appended blanks are for a NEW timepoint
    # (T02, not yet in the well). The empty-well field is per-timepoint, so
    # existing timepoints can't go stale → stay incremental, don't force a full
    # reprocess. Regression for: adding a new timepoint to the background well
    # needlessly triggered a full re-analysis.
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    (tmp_path / "exp_w1_A01_1_T02.tif").write_bytes(b"new-tp")
    opts = _opts(tmp_path, background="empty_well", background_well="A01")
    select_incremental_scope(opts, tmp_path)
    assert opts.get("incremental") is True
    assert not opts.get("force")


def test_scope_incremental_when_empty_well_background_untouched(tmp_path):
    # Empty-well model in use, but the new files are for a different well.
    _stage_with_new_loose(tmp_path, well="A01")
    opts = _opts(tmp_path, background="empty_well", background_well="H12")
    select_incremental_scope(opts, tmp_path)
    assert opts.get("incremental") is True
    assert not opts.get("force")


def test_scope_noop_when_nothing_appended(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})  # no loose files
    opts = _opts(tmp_path)
    select_incremental_scope(opts, tmp_path)
    assert not opts.get("incremental")
    assert not opts.get("force")


def test_resolve_routes_loose_next_to_zip_to_append_not_folder_grouping(tmp_path):
    """Loose TIFs dropped into an ``in/`` that already holds well zips (a new
    timepoint added to a grouped dataset) must NOT be grouped into per-well
    folders — those folders are silently dropped by the zip-preferring pipeline,
    so the new images would never be processed. ``resolve_input_output`` must
    leave them in place for ``append_loose_images_to_zips`` to fold into the
    matching zip and then ``select_incremental_scope`` flips on incremental.

    Regression for: "the new timepoint that was added does not show up in
    Review even after rerunning the whole pipeline."
    """
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    _make_zip(in_dir / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    loose = in_dir / "exp_w1_A01_1_T02.tif"   # SAME well, NEW timepoint
    loose.write_bytes(b"new-timepoint")

    input_dir, output_dir, on_success = resolve_input_output(
        in_dir, filename_schema=SCHEMA, filename_sep=SEP)

    # Routed to the append path: returns the in/ dir untouched, no folder made.
    assert input_dir == in_dir
    assert not (in_dir / "A01").exists()        # the bug: a dropped A01/ folder
    assert loose.exists()                        # still loose, awaiting append
    assert zipfile.ZipFile(in_dir / "A01.zip").namelist() == ["exp_w1_A01_1_T01.tif"]

    # Now the scope step folds it in and selects incremental.
    opts = _opts(in_dir)
    select_incremental_scope(opts, input_dir, log_fn=None)
    assert opts.get("incremental") is True
    assert not opts.get("force")
    assert not loose.exists()                     # consumed into the zip
    names = set(zipfile.ZipFile(in_dir / "A01.zip").namelist())
    assert names == {"exp_w1_A01_1_T01.tif", "exp_w1_A01_1_T02.tif"}


def test_resolve_routes_loose_in_subfolder_next_to_zip_to_append(tmp_path):
    """Same as above but the user selected the parent dir (raw) whose ``in/``
    subfolder holds the zips + the new loose TIFs."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    _make_zip(in_dir / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    loose = in_dir / "exp_w1_A01_1_T02.tif"
    loose.write_bytes(b"new-timepoint")

    input_dir, output_dir, on_success = resolve_input_output(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert input_dir == in_dir
    assert not (in_dir / "A01").exists()
    assert loose.exists()
    assert output_dir == tmp_path / "out"


def test_result_zip_is_valid_and_short_well_token_canonicalised(tmp_path):
    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    # short, lowercase well token "a1" → canonical "A01"
    (tmp_path / "exp_w1_a1_2_T01.tif").write_bytes(b"new")

    appended, wells = append_loose_images_to_zips(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)

    assert appended == 1
    assert wells == {"A01"}
    assert zipfile.is_zipfile(tmp_path / "A01.zip")
    assert "exp_w1_a1_2_T01.tif" in zipfile.ZipFile(tmp_path / "A01.zip").namelist()


def test_the_scope_reader_reads_the_blank_list_the_pipeline_way(tmp_path):
    """One answer to "which wells are blanks", shared with the run itself.

    Both scope readers used to carry a local whitespace-splitting regex that read
    ``"H12 G12"`` as two wells, where ``util.well_token.split_background_wells``
    — what the pipeline itself uses — reads it as one. Two answers to one
    question about one plate. The same divergence was found and fixed once in
    the remote runner, where it killed every batch of a run, and these two copies
    survived it.

    Asserted against ``blank_wells_for_run`` rather than against a list written
    here, so this cannot drift from the pipeline the way the regex did.
    """
    from services.input_resolution_service import _blank_well_tokens
    from util.well_token import blank_wells_for_run, canonical_well_label

    def canonical(background_well="", background_well_map=""):
        return {canonical_well_label(w)
                for w in blank_wells_for_run(background_well, background_well_map)
                if canonical_well_label(w)}

    for opts in (
        {"background_well": "H12, G12"},
        {"background_well": "H12;G12"},
        {"background_well": "H12 G12"},        # one token, not two
        {"background_well": "h12"},
        {"background_well_map": "H12=A01; G12=B01"},
        {"background_well": "F12", "background_well_map": "H12=A01"},
        {},
    ):
        assert _blank_well_tokens(opts) == canonical(
            opts.get("background_well", ""),
            opts.get("background_well_map", "")), opts

    # And the specific case the regex got wrong: whitespace does not separate
    # wells, so this names one well that does not exist rather than two that do.
    assert _blank_well_tokens({"background_well": "H12 G12"}) == set()
    assert _blank_well_tokens({"background_well": "H12, G12"}) == {"H12", "G12"}


def test_a_blank_named_only_in_the_pairing_map_is_still_a_blank(tmp_path):
    """The scope readers decide whether new blank frames stale existing wells.

    A blank the map names and ``--background_well`` does not was invisible to
    that decision, so a re-run that added frames to it would leave every well it
    corrects holding numbers built from the old reference.
    """
    from services.input_resolution_service import _blank_well_tokens

    assert _blank_well_tokens({"background_well_map": "H12=A01,A02"}) == {"H12"}
