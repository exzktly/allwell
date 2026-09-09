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


def test_a_supplied_listing_plans_exactly_what_reading_the_folder_would(tmp_path):
    """``loose_files`` is an optimisation, never a second definition of the rule.

    ``_plan_grouped_root`` counts the loose images to decide whether there is
    anything to fold, and then hands that same list here rather than making the
    function list the directory again — one enumeration per readout instead of
    two, on a volume where an enumeration is a round trip. The parameter is
    therefore only safe while the two answers are identical.

    Be clear about what this can and cannot say. The equality of the two plans
    is close to a tautology, since the no-argument path calls the very function
    the fixture calls to build the argument — an implementation that ignored
    ``loose_files`` outright would satisfy it. What it does pin is the explicit
    plan below: the duplicate that neither counts, the unparseable name both
    skip, and the non-image left alone. That the parameter is *used* is
    ``test_planning_the_fold_reads_the_directory_once_per_readout``'s job, by
    counting listings, and it is the test that fails if the parameter is
    dropped.
    """
    from services.input_resolution_service import plan_loose_append, tif_files_in

    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    (tmp_path / "exp_w1_A01_1_T01.tif").write_bytes(b"dupe")   # already a member
    (tmp_path / "exp_w1_A01_2_T01.tif").write_bytes(b"newfov")
    (tmp_path / "exp_w1_B02_1_T01.tif").write_bytes(b"newwell")
    (tmp_path / "notes.txt").write_bytes(b"not an image")
    (tmp_path / "unparseable.tif").write_bytes(b"no well token")

    read_it = plan_loose_append(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    supplied = plan_loose_append(
        tmp_path, filename_schema=SCHEMA, filename_sep=SEP,
        loose_files=tif_files_in(tmp_path))

    assert supplied.by_well == read_it.by_well
    assert supplied.nonempty == read_it.nonempty
    # …and the plan is the one the folder actually calls for, so an agreement
    # between two empty plans cannot pass this.
    assert {w: [p.name for p in files] for w, files in supplied.by_well.items()} == {
        "A01": ["exp_w1_A01_2_T01.tif"],
        "B02": ["exp_w1_B02_1_T01.tif"],
    }
    assert supplied.nonempty == frozenset({"A01"})


def test_planning_the_fold_reads_the_directory_once_per_readout(tmp_path):
    """The layout readout enumerates the input folder once, not twice.

    ``plan_input_layout`` asks two questions of the same directory — how many
    loose images are in it, and which of them would be folded — and used to read
    it once for each. Counted rather than eyeballed because the duplicate
    listing is invisible in the answer: both versions produce the identical
    readout, and only the number of round trips differs.
    """
    import os

    from services.input_resolution_service import plan_input_layout

    grouped = tmp_path / "in"
    grouped.mkdir()
    _make_zip(grouped / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    (grouped / "exp_w1_A01_2_T01.tif").write_bytes(b"newfov")

    real = os.scandir
    listings: list[str] = []

    def counting(path, *args, **kwargs):
        listings.append(str(path))
        return real(path, *args, **kwargs)

    os.scandir = counting
    try:
        plan = plan_input_layout(
            tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    finally:
        os.scandir = real

    assert plan.action == "fold_loose" and plan.loose_count == 1
    assert listings.count(str(grouped)) == 1, (
        f"the grouped folder was listed {listings.count(str(grouped))} times "
        f"for one readout: {listings}")


def test_every_layout_lists_the_input_folder_once(tmp_path):
    """Not only the fold-loose branch — the one a finished plate takes as well.

    A zip-grouped `in/` is what every dataset looks like after one completed
    run, so it is the state a re-opened plate is in and the one whose cost is
    paid most often. It reaches `_plan_grouped_root`'s `loose == 0` branch,
    which asked `has_well_content` and re-read the whole folder to do it.
    """
    import os

    from services.input_resolution_service import plan_input_layout

    def listings_of(target: Path, selected: Path) -> int:
        real_scandir, real_listdir = os.scandir, os.listdir
        seen: list[str] = []

        def scandir(path, *args, **kwargs):
            seen.append(str(path))
            return real_scandir(path, *args, **kwargs)

        def listdir(path, *args, **kwargs):
            seen.append(str(path))
            return real_listdir(path, *args, **kwargs)

        os.scandir, os.listdir = scandir, listdir
        try:
            plan_input_layout(
                selected, filename_schema=SCHEMA, filename_sep=SEP)
        finally:
            os.scandir, os.listdir = real_scandir, real_listdir
        return seen.count(str(target))

    zipped = tmp_path / "zipped" / "in"
    zipped.mkdir(parents=True)
    for well in ("A01", "A02"):
        _make_zip(zipped / f"{well}.zip", {f"exp_w1_{well}_1_T01.tif": b"x"})
    assert listings_of(zipped, tmp_path / "zipped") == 1

    foldered = tmp_path / "foldered" / "in"
    foldered.mkdir(parents=True)
    for well in ("A01", "A02"):
        (foldered / well).mkdir()
        (foldered / well / f"exp_w1_{well}_1_T01.tif").write_bytes(b"x")
    assert listings_of(foldered, tmp_path / "foldered") == 1

    loose = tmp_path / "loose" / "in"
    loose.mkdir(parents=True)
    (loose / "exp_w1_A01_1_T01.tif").write_bytes(b"x")
    assert listings_of(loose, tmp_path / "loose") == 1


def test_a_folder_that_cannot_be_listed_is_said_so_not_called_empty(tmp_path):
    """An unreadable folder gets its own verdict, and it is not runnable.

    A directory can be traversable and not listable — a share whose ACL grants
    execute but not read, a stale mount — and `Path.glob` answered such a folder
    with an empty list, so the readout said "the in/ folder is empty — nothing
    to process": a claim about the plate, made from a failure to look at it.
    Simulated here by making the listing raise, because the permission bits
    themselves do not stop root.
    """
    import errno
    import os

    from services.input_resolution_service import (
        RUNNABLE_ACTIONS, plan_input_layout,
    )

    grouped = tmp_path / "in"
    grouped.mkdir()
    (grouped / "exp_w1_A01_1_T01.tif").write_bytes(b"x")

    real_scandir, real_listdir = os.scandir, os.listdir

    def refuse(path, *args, **kwargs):
        if str(path) == str(grouped):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_scandir(path, *args, **kwargs)

    def refuse_listdir(path, *args, **kwargs):
        if str(path) == str(grouped):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_listdir(path, *args, **kwargs)

    os.scandir, os.listdir = refuse, refuse_listdir
    try:
        plan = plan_input_layout(
            tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    finally:
        os.scandir, os.listdir = real_scandir, real_listdir

    assert plan.action == "unreadable", plan.message
    assert plan.action not in RUNNABLE_ACTIONS
    assert "Permission denied" in plan.message
    assert "empty" not in plan.message.lower()


def test_a_directory_named_like_an_archive_does_not_pass_the_well_guard(tmp_path):
    """The pre-run check that refuses a background well the input does not hold.

    `_background_well_validation_error` asks `well_present_in_input`, which asks
    `_well_in_grouped_root`. A directory called `A01.zip` answering yes there is
    a run allowed to start against a background well the pipeline will not find,
    so the empty-well correction is never built — the guard's entire purpose,
    defeated by the one entry it is not entitled to call an archive.
    """
    from services.input_resolution_service import well_present_in_input

    grouped = tmp_path / "in"
    grouped.mkdir()
    (grouped / "A01.zip").mkdir()

    assert well_present_in_input(grouped, "A01") is False

    _make_zip(grouped / "A02.zip", {"exp_w1_A02_1_T01.tif": b"x"})
    assert well_present_in_input(grouped, "A02") is True
    (grouped / "A03").mkdir()
    assert well_present_in_input(grouped, "A03") is True


def test_a_directory_named_like_a_well_archive_is_not_one(tmp_path):
    """``has_well_zip`` asks for a well-named *file*, not a well-named entry.

    A folder called ``A01.zip`` — an archive somebody expanded in place, and
    the name the expander gives it — used to answer this question True, because
    the check was a name glob with no file test behind it. Everything
    downstream then treats the plate as zip-grouped: the readout offers to fold
    the loose images beside it, and the fold opens ``A01.zip`` as an archive,
    fails, reads the failure as "the archive holds nothing", and reports every
    loose image as new. The wrong branch for the whole folder, from one entry.
    """
    from services.input_resolution_service import has_well_zip, plan_input_layout

    grouped = tmp_path / "in"
    grouped.mkdir()
    (grouped / "A01.zip").mkdir()
    (grouped / "exp_w1_A01_1_T01.tif").write_bytes(b"loose")

    assert has_well_zip(grouped) is False

    # …and the readout says the plate needs grouping, not folding. The folder
    # has to be an `in/` with loose images in it, or `_plan_grouped_root` — the
    # function this is about — is never reached and the plan comes back
    # `no_input`, which is what the first version of this test measured.
    plan = plan_input_layout(tmp_path, filename_schema=SCHEMA, filename_sep=SEP)
    assert plan.action == "group_loose", plan.message
    assert "group 1 loose" in plan.message


def test_a_real_well_archive_still_answers_yes(tmp_path):
    """The companion to the test above: the exclusion is of folders only."""
    from services.input_resolution_service import has_well_zip

    _make_zip(tmp_path / "A01.zip", {"exp_w1_A01_1_T01.tif": b"old"})
    assert has_well_zip(tmp_path) is True
    assert has_well_zip(tmp_path / "nope") is False
