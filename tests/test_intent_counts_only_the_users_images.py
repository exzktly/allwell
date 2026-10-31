"""A plate holds more than the user's images, and intent must count only those.

Every *intent*-side question — how many FOV-groups does this plate hold, how
much disk will they need, which timepoints exist, which filenames should schema
inference learn from — was answered with a bare suffix test. A plate carries a
great deal that has an image suffix and is not an image:

* ``._Plate_A01_F1.tif`` — an AppleDouble sidecar, which macOS writes beside
  every file on a non-HFS volume. One per image, so a plate that has been
  through a USB stick or a network share carries a complete shadow set.
* ``__MACOSX/…`` — the same thing inside a zip, in its own directory.
* ``.DS_Store`` and any other leading-dot basename.
* zip directory entries, which have no bytes at all.

CLAUDE.md §7.1 names the class: *a domain predicate applied outside its domain*.
"Is this the requested channel?" was asked of every zip member including the
ones that are not images; this is the same mistake asked as "is this an image?"
of every directory entry.

The measurements each test pins were taken on the unfixed code.
"""
from __future__ import annotations

import zipfile

import pytest

from pipeline.capacity import sample_image_bytes
from pipeline.completeness import coverage_for_well
from util.image_exts import GROUP_IMAGE_EXTS, is_group_image, is_run_input_member

SCHEMA = "experiment:channel:well:fov:timepoint"
SEP = "_"


# ── the predicate itself ────────────────────────────────────────────────────

def test_the_suffix_test_and_the_membership_test_are_different_questions():
    """`is_group_image` is a suffix test and must stay one.

    Its docstring used to claim an AppleDouble twin "does not have" an image
    suffix. It does — that is the whole problem — and the gap between the claim
    and the behaviour is what let sidecars be counted as plate content in four
    places.
    """
    assert is_group_image("._Plate_A01_F1.tif"), (
        "the suffix test changed meaning; the done-side readers depend on it "
        "answering purely on the suffix")
    assert not is_run_input_member("._Plate_A01_F1.tif")


def test_everything_a_plate_carries_that_is_not_an_image_is_excluded():
    for name in ("._Plate_A01_F1_T1.tif", "__MACOSX/._x.tif", "__MACOSX/",
                 ".DS_Store", ".hidden.tif", "A01/", "", "Icon\r"):
        assert not is_run_input_member(name), name
    for name in ("Plate_A01_F1_T1.tif", "A01/Plate_A01_F1_T1.png",
                 "x.JPEG", "deep/nest/y.tiff"):
        assert is_run_input_member(name), name


# ── P6: a finished plate reported partial for ever ──────────────────────────

def test_a_finished_well_with_macos_twins_is_not_reported_partial(tmp_path):
    """Measured on the unfixed code: intent 4 on a two-image well, two keys
    permanently missing, state `partial` with nothing left to compute.

    The twins do not fall out of the key set as unparseable names would. They
    parse to a *different* ``(fov, timepoint)`` — the leading ``._`` shifts
    every schema field by one — so they land in `intent`, never in `done`, and
    no amount of re-running can satisfy them.
    """
    names = [f"Plate_DAPI_A01_F{i}_T1.tif" for i in (1, 2)]
    in_zip = tmp_path / "A01.zip"
    with zipfile.ZipFile(in_zip, "w") as zf:
        for n in names:
            zf.writestr(n, b"image")
        for n in names:
            zf.writestr("._" + n, b"resource fork")
        zf.writestr(".DS_Store", b"junk")
        zf.writestr("__MACOSX/._A01.zip", b"junk")

    out_zip = tmp_path / "A01_out.zip"
    with zipfile.ZipFile(out_zip, "w") as zf:
        for n in names:
            zf.writestr(n.replace(".tif", "_labels.tif"), b"labels")

    with zipfile.ZipFile(in_zip) as zf:
        members = zf.namelist()

    cov = coverage_for_well(
        "A01", in_members=members, csv_keys={("F1", "T1"), ("F2", "T1")},
        csv_state="ok", out_zip=out_zip, out_folder=None,
        schema=SCHEMA, sep=SEP, fov_field_present=True, skip_hidden=False)

    assert cov.intent == 2, (
        f"the plate holds two images and intent counted {cov.intent}; the "
        f"macOS twins were read as plate content")
    assert not cov.missing, (
        f"a finished well reported {len(cov.missing)} missing FOV-group(s) "
        f"that no run can ever produce: {cov.missing}")


# ── P8: the disk pre-flight sized a run from a sidecar ──────────────────────

def test_the_disk_preflight_sizes_the_run_from_a_real_image(tmp_path):
    """Measured on the unfixed code: 4,096 bytes for a plate of 112 MB frames —
    a 27,000-fold under-estimate, in the one check that exists to stop a run
    filling the volume.

    Two faults compounded. The loose branch returned the *first* file in sorted
    order rather than the largest, which the docstring three lines above
    already said it did not; and `._…` sorts before every real filename.
    """
    d = tmp_path / "in"
    d.mkdir()
    (d / "Plate_DAPI_A01_F1_T1.tif").write_bytes(b"\0" * 4_000_000)
    (d / "._Plate_DAPI_A01_F1_T1.tif").write_bytes(b"\0" * 4_096)

    assert sample_image_bytes(d, GROUP_IMAGE_EXTS) == 4_000_000, (
        "the run was sized from the AppleDouble sidecar, so the free-space "
        "check passes on a volume that cannot hold the run")


def test_the_zip_branch_sizes_from_a_real_member_too(tmp_path):
    """Half-swept is worse than unswept (§7.5 #5): the two branches answer the
    same question and a fix to one leaves the other holding the old rule."""
    d = tmp_path / "in"
    d.mkdir()
    with zipfile.ZipFile(d / "A01.zip", "w") as zf:
        zf.writestr("Plate_DAPI_A01_F1_T1.tif", b"\0" * 4_000_000)
        zf.writestr("__MACOSX/._Plate_DAPI_A01_F1_T1.tif", b"\0" * 4_096)

    assert sample_image_bytes(d, GROUP_IMAGE_EXTS) == 4_000_000


def test_the_largest_image_wins_not_the_first(tmp_path):
    """The docstring's own promise, which the code did not keep.

    A well archive routinely carries a thumbnail or a single-channel snapshot
    beside its full frames, and sizing from that under-estimates the need this
    check exists to catch — no sidecar required.
    """
    d = tmp_path / "in"
    d.mkdir()
    (d / "a_thumbnail.tif").write_bytes(b"\0" * 1_024)
    (d / "z_full_frame.tif").write_bytes(b"\0" * 2_000_000)

    assert sample_image_bytes(d, GROUP_IMAGE_EXTS) == 2_000_000


# ── P7: a PNG plate published no timepoints ─────────────────────────────────

def test_a_png_plate_reports_its_timepoints(tmp_path):
    """`_collect_image_stems` took `.tif`/`.tiff` only.

    The pipeline groups `.png`, `.jpg` and `.jpeg` as well, so a PNG plate
    published an empty `available_timepoints` and an empty `available_fovs`,
    and Review read it as a plate with no timepoints at all.
    """
    from process_microscopy import _collect_image_stems

    d = tmp_path / "in"
    d.mkdir()
    with zipfile.ZipFile(d / "A01.zip", "w") as zf:
        zf.writestr("Plate_DAPI_A01_F1_T1.png", b"x")
        zf.writestr("Plate_GFP_A01_F1_T1.png", b"x")
        zf.writestr("._Plate_DAPI_A01_F1_T1.png", b"junk")
        zf.writestr(".DS_Store", b"junk")
    with zipfile.ZipFile(d / "B02.zip", "w") as zf:
        zf.writestr("Plate_DAPI_B02_F1_T1.png", b"x")
        zf.writestr("__MACOSX/._Plate_DAPI_B02_F1_T1.png", b"junk")

    stems = _collect_image_stems(d)

    assert stems == {"Plate_DAPI_A01_F1_T1", "Plate_GFP_A01_F1_T1",
                     "Plate_DAPI_B02_F1_T1"}, sorted(stems)


# ── P9: numbers whole, images gone, reported complete ───────────────────────

def test_a_well_whose_output_archive_is_gone_is_not_complete():
    """`health` accepted `has_out_zip` and never read it.

    A well whose `_out.zip` had been deleted, or whose remote fetch brought
    back the CSV and lost the archive, answered `complete` on the strength of
    the CSV alone — and a plate of them summarised as a finished plate. The
    archive holds the masks and the corrected frames, so without it the rows
    cannot be reviewed, re-gated or re-thresholded. The well is not finished;
    it is half delivered, and CLAUDE.md §7 forbids the two being
    indistinguishable in the record whose whole job is to tell them apart.
    """
    from pipeline.completeness import health

    assert health(missing=(), csv_state="ok", has_in_container=True,
                  has_out_zip=False, has_out_folder=False) == "csv_only"
    assert health(missing=(), csv_state="ok", has_in_container=True,
                  has_out_zip=True, has_out_folder=False) == "complete"
    assert health(missing=(), csv_state="ok", has_in_container=True,
                  has_out_zip=False, has_out_folder=True) == "complete"


def test_every_consumer_paints_the_new_state_rather_than_falling_through():
    """A new state that lands in an `else` is the same silent substitution.

    Both consumers have a catch-all — `CELL_UNCHECKED` on the plate and no
    marker on the Validate dashboard — so an unhandled `csv_only` would render
    as "not checked", which is a third wrong answer rather than a fix.
    """
    import inspect

    from datasets.validate import dashboard
    from review.features.home import collect
    from pipeline.completeness import HEALTH_STATES

    assert "csv_only" in HEALTH_STATES
    assert "csv_only" in inspect.getsource(collect), (
        "the plate cell mapper does not name csv_only, so such a well paints "
        "as unchecked")
    assert "csv_only" in inspect.getsource(dashboard), (
        "the Validate dashboard does not name csv_only, so such a well gets "
        "no marker and no colour")


# ── P10: a well that found nothing, counted among the finished ──────────────

def test_a_well_that_yielded_no_groups_is_not_summarised_as_finished():
    """`groups_total=0, groups_covered=0` passed the incomplete test.

    The predicate was `groups_total > groups_covered`, and `0 > 0` is false, so
    a well whose input yielded no FOV-groups at all was counted among the
    finished wells and the plate summarised as whole. It is the opposite of
    finished: nothing about it was computed.

    The cause is always upstream and always worth naming — a filename schema
    that does not decompose the plate, a separator that groups nothing, an
    archive whose members are all sidecars — and unlike a genuinely partial
    well, `--incremental` cannot recover it. That is why it is a separate
    bucket rather than folded into `incomplete`.
    """
    from pipeline.run_report import found_nothing, is_incomplete, summarize

    empty = {"groups_total": 0, "groups_covered": 0}
    assert not is_incomplete(empty), (
        "the fixture is meant to be the case the old predicate let through")
    assert found_nothing(empty)

    report = {
        "wells": {"A01": empty, "B02": {"groups_total": 12, "groups_covered": 12}},
        "incomplete_wells": [],
        "empty_wells": ["A01"],
    }
    line = summarize(report)
    assert line, "a plate with a well that computed nothing summarised silently"
    assert "A01" in line and "NO INPUT GROUPS" in line, line
    assert "--incremental cannot recover it" in line, (
        "the remedy for a partial well is offered for a well it cannot help")


def test_a_genuinely_complete_plate_still_summarises_to_nothing():
    """Or every clean run grows a scary line and the signal is spent."""
    from pipeline.run_report import summarize

    assert summarize({
        "wells": {"A01": {"groups_total": 12, "groups_covered": 12}},
        "incomplete_wells": [], "empty_wells": [],
    }) == ""


def test_both_kinds_of_shortfall_are_reported_together():
    """A plate can hold both, and reporting only one hides the other."""
    from pipeline.run_report import summarize

    line = summarize({
        "wells": {"A01": {"groups_total": 0, "groups_covered": 0},
                  "B02": {"groups_total": 12, "groups_covered": 8}},
        "incomplete_wells": ["B02"], "empty_wells": ["A01"],
    })
    assert "NO INPUT GROUPS" in line and "INCOMPLETE" in line, line
    assert "A01" in line and "B02" in line


# ── P4: a container that is gone is not read as complete ────────────────────

def test_a_container_that_vanished_entirely_is_not_read_as_complete(tmp_path):
    """The other half: unreadable must not mean "nothing was asked for"."""
    from pipeline.completeness import resolve_input_container

    gone = tmp_path / "in" / "H12"
    assert resolve_input_container(gone) is None


# ── P5: images the run would not read stop it ──────────────────────────────

def _plate_with_one_archive(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    with zipfile.ZipFile(d / "A01.zip", "w") as zf:
        zf.writestr("P_DAPI_A01_F1_T1.tif", b"x")
    return d


def test_images_stranded_in_an_ignored_folder_stop_the_run(tmp_path):
    """A folder for a well with no archive is never read by a zips-only run.

    Running anyway would report success having analysed everything except
    those images. The refusal names the folder, because the remedy depends
    on which it is.
    """
    from pipeline.input_layout import find_well_zips, refuse_non_zip_input

    d = _plate_with_one_archive(tmp_path)
    (d / "B02").mkdir()
    (d / "B02" / "P_DAPI_B02_F1_T1.tif").write_bytes(b"x")

    with pytest.raises(SystemExit) as caught:
        refuse_non_zip_input(d, find_well_zips(d))

    said = str(caught.value)
    assert "well folder(s) with no <WELL>.zip: B02/ (1 image(s))" in said, said
    assert "Datasets → Zip loose folder" in said, said


def test_loose_images_beside_the_archives_stop_the_run(tmp_path):
    """Loose images used to be folded into the archives at Run; nothing does
    that now, so a run that went ahead would silently skip them."""
    from pipeline.input_layout import find_well_zips, refuse_non_zip_input

    d = _plate_with_one_archive(tmp_path)
    (d / "P_DAPI_A01_F2_T1.tif").write_bytes(b"x")
    (d / ".DS_Store").write_bytes(b"x")              # not the user's data

    with pytest.raises(SystemExit) as caught:
        refuse_non_zip_input(d, find_well_zips(d))

    said = str(caught.value)
    assert "1 loose image(s) at the top level: P_DAPI_A01_F2_T1.tif" in said, said


def test_an_input_with_no_archive_stops_the_run(tmp_path):
    from pipeline.input_layout import refuse_non_zip_input

    d = tmp_path / "in"
    d.mkdir()
    with pytest.raises(SystemExit, match="no per-well <WELL>.zip archives"):
        refuse_non_zip_input(d, [])


def test_a_redundant_folder_beside_its_archive_still_runs(tmp_path):
    """A folder an earlier version left beside its own well's archive is the
    archive's twin, not new input. Refusing it would refuse every plate those
    versions produced, so it is logged, not refused."""
    from pipeline.input_layout import find_well_zips, refuse_non_zip_input

    d = _plate_with_one_archive(tmp_path)
    (d / "A01").mkdir()
    (d / "A01" / "P_DAPI_A01_F1_T1.tif").write_bytes(b"x")

    refuse_non_zip_input(d, find_well_zips(d))


# ── the zipper is reaped whatever ends the read of its output ───────────────

def test_the_zipper_stream_replaces_undecodable_bytes(tmp_path, monkeypatch):
    """The zipper echoes every filename it groups, and a filename can carry
    any byte the filesystem allows.

    Asserted at the boundary — the kwargs that actually reach Popen. Reading
    the function's source for the two keywords passed as long as they were
    spelled there, and stopped the day the spelling moved into
    ``services.subprocess_text.TEXT_UTF8`` while the behaviour was unchanged.
    """
    import subprocess

    from datasets.import_loose import zipper

    seen: dict = {}

    class _FakeProc:
        stdout: list = []
        returncode = 0
        pid = 4242

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def _fake_popen(args, **kwargs):
        seen.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    zipper.run_zipper(
        tmp_path / "tifs", tmp_path / "in",
        filename_schema="well:field:z:c", filename_sep="_",
    )

    assert seen.get("errors") == "replace", (
        "the zipper's output is decoded strictly, so one byte from a filename "
        "raises out of the reader and the process is never reaped")
    assert seen.get("encoding") == "utf-8", (
        "the zipper's output is decoded with the client's locale — the ANSI "
        "codepage on Windows — rather than the UTF-8 it is written in")
    assert seen.get("text") is True


def test_the_zipper_is_reaped_even_when_the_read_stops_early():
    """It is spawned into its own process group so Stop can reach it, which
    also means nothing else will reap it. The `finally` cleared the process
    hook and never waited, so an exception from the loop left the zipper
    rewriting the user's input directory while the caller reported failure."""
    import ast
    import inspect
    import textwrap

    from datasets.import_loose import zipper

    src = textwrap.dedent(inspect.getsource(zipper.run_zipper))
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not any(isinstance(n, ast.For) and "proc.stdout" in ast.unparse(n.iter)
                   for n in node.body):
            continue
        tail = ast.unparse(ast.Module(body=list(node.finalbody), type_ignores=[]))
        assert "proc.wait" in tail, (
            "the finally around the zipper's read loop does not wait for it")
        assert "signal_subprocess" in tail, (
            "the finally does not signal the zipper, so wait() would block on "
            "a process still producing output")
        break
    else:
        raise AssertionError("no try wraps the zipper's read loop")


# ── Analyze touches no loose image; the grouper and the refusal agree ──────

def test_analyze_refuses_a_loose_plate_and_touches_none_of_it(tmp_path):
    """Analyze used to group loose images at Run and delete them after a clean
    run; a PNG channel the deleter counted and the grouper skipped was lost
    that way. Analyze now refuses loose images outright, so the property worth
    pinning is the stronger one: the refusal names the files and every one of
    them — PNG included — is still where it was, ungrouped and undeleted.

    Asserted on the files, not on the predicates: it is the directory the user
    loses.
    """
    from services.input_resolution_service import resolve_input_output

    root = tmp_path / "raw"
    root.mkdir()
    for well in ("A01", "A02"):
        (root / f"Exp_NIR_{well}_F001_00d00h.tif").write_bytes(b"nuclear")
        (root / f"Exp_GFP_{well}_F001_00d00h.png").write_bytes(b"fluor")
    before = sorted(p.name for p in root.iterdir())

    with pytest.raises(ValueError) as refused:
        resolve_input_output(root)

    assert "4 loose image(s)" in str(refused.value), str(refused.value)
    assert "Datasets → Zip loose folder" in str(refused.value)
    assert sorted(p.name for p in root.iterdir()) == before, "Analyze moved or deleted input"


def test_the_grouper_and_the_refusal_read_the_same_predicate():
    """The grouper and the refusal that sends the user to it must agree on
    what an input image is, so the refusal never names a file the grouper
    then skips."""
    import inspect

    from pipeline import input_layout, well_plate_zipper

    for mod in (well_plate_zipper, input_layout):
        src = inspect.getsource(mod)
        assert "is_run_input_member" in src, (
            f"{mod.__name__} does not use the shared input predicate, so the "
            f"side that refuses and the side that groups can disagree")
