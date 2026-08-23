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
    """`_collect_image_stems` took `.tif`/`.tiff` in all three of its loops.

    The pipeline groups `.png`, `.jpg` and `.jpeg` as well, so a PNG plate
    published an empty `available_timepoints` and an empty `available_fovs`,
    and Review read it as a plate with no timepoints at all. The file already
    imported the one definition of the set at line 74 and then re-spelled a
    narrower one at three call sites.
    """
    from process_microscopy import _collect_image_stems

    d = tmp_path / "in"
    d.mkdir()
    (d / "Plate_DAPI_A01_F1_T1.png").write_bytes(b"x")
    (d / "Plate_GFP_A01_F1_T1.png").write_bytes(b"x")
    (d / "._Plate_DAPI_A01_F1_T1.png").write_bytes(b"junk")
    (d / ".DS_Store").write_bytes(b"junk")
    with zipfile.ZipFile(d / "B02.zip", "w") as zf:
        zf.writestr("Plate_DAPI_B02_F1_T1.png", b"x")
        zf.writestr("__MACOSX/._Plate_DAPI_B02_F1_T1.png", b"junk")

    stems = _collect_image_stems(d)

    assert stems == {"Plate_DAPI_A01_F1_T1", "Plate_GFP_A01_F1_T1",
                     "Plate_DAPI_B02_F1_T1"}, sorted(stems)


def test_a_well_folder_layout_is_read_too(tmp_path):
    """The second of the three loops. Converting one and not the others is the
    half-sweep this whole group is about."""
    from process_microscopy import _collect_image_stems

    d = tmp_path / "in"
    (d / "A01").mkdir(parents=True)
    (d / "A01" / "Plate_DAPI_A01_F1_T1.jpg").write_bytes(b"x")
    (d / "A01" / "._Plate_DAPI_A01_F1_T1.jpg").write_bytes(b"junk")

    assert _collect_image_stems(d) == {"Plate_DAPI_A01_F1_T1"}


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
    from well_viewer.features.home import collect
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
