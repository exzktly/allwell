from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from services.subprocess_kill import signal_subprocess
from services.subprocess_text import TEXT_UTF8
from util.aw_safe import swallow, swallowed
from util.dir_scan import files_and_subdirs, files_in
from util.image_exts import is_run_input_member
from util.well_token import blank_wells_for_run, canonical_well_label
from util.retry_io import retry_io
from util.zip_write import (
    atomic_zip, replace_folder_with_archive)

HERE = Path(__file__).resolve().parent.parent
ZIPPER_SCRIPT = HERE / "pipeline" / "well_plate_zipper.py"
#: Kept for the well_plate_zipper subprocess, which takes glob patterns on its
#: command line and answers a different question — packing a well *folder*,
#: where `.tif`/`.tiff` is the deliberate rule. `tif_files_in` no longer uses
#: it: deciding which loose files are this run's input is
#: `util.image_exts.is_run_input_member`'s job.
TIF_GLOB = ("*.tif", "*.tiff", "*.TIF", "*.TIFF")  # aw-image-exts-ok: zipper argv

# Consolidation reads and writes on whatever volume the user pointed at as the
# input folder, and it moves the whole plate: every loose TIF into a per-well
# archive. On a network mount that load is enough to drop the session, and the
# unguarded write turned the blip into a failed run before analysis started
# (``OSError: [Errno 9] Bad file descriptor``). ``atomic_zip`` stages and
# commits with ``os.replace``, so re-running an attempt is safe.
_CONSOLIDATION_ADVICE = (
    "A repeated failure here usually means the input volume is a network "
    "mount dropping its session under load. Copy the plate to local storage "
    "and point All-Well at that copy."
)


def find_zipper_script() -> Path | None:
    return ZIPPER_SCRIPT if ZIPPER_SCRIPT.exists() else None


def build_zipper_cmd(
    zipper: Path,
    tif_folder: Path,
    out_folder: Path,
    filename_schema: str,
    filename_sep: str,
    *,
    move_source_tifs: bool = False,
) -> list[str]:
    """Build the argv for the well_plate_zipper subprocess.

    Frozen bundle: re-invoke the launcher with the ``--run-zipper`` sentinel,
    which routes argv into ``well_plate_zipper.main()`` (mirrors the pipeline's
    ``--run-pipeline`` path in :mod:`services.pipeline_service`). Passing the
    ``.py`` path there would make ``sys.executable`` (the .app binary) relaunch
    the GUI instead of running the zipper — the frozen-only failure this guards.
    From source, invoke the interpreter on the script path as before.
    """
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--run-zipper"]
    else:
        cmd = [sys.executable, str(zipper)]
    cmd += [
        "--search-dir",
        str(tif_folder),
        "--output-dir",
        str(out_folder),
        "--filename_schema",
        filename_schema,
        "--filename_sep",
        filename_sep,
    ]
    if move_source_tifs:
        cmd.append("--move-source-tifs")
    return cmd


def build_bootstrap_cmd(bootstrap: Path, alias: str) -> list[str]:
    """Build the argv for the remote-provisioning bootstrap subprocess.

    Frozen bundle: re-invoke the launcher with the ``--run-bootstrap`` sentinel,
    which routes argv into ``services/remote_bootstrap.main()`` (mirrors the
    ``--run-zipper`` / ``--run-pipeline`` paths). Passing the ``.py`` path there
    would make ``sys.executable`` (the .app binary) relaunch the GUI instead of
    running the bootstrap — the frozen-only failure this guards. From source,
    invoke the interpreter on the script path as before.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-bootstrap", alias]
    return [sys.executable, str(bootstrap), alias]


def tif_files_in(folder: Path) -> list[Path]:
    """The loose input images directly inside *folder*.

    Four things this had to stop doing, all of them the same mistake — judging
    items it is not entitled to judge. Two branches of the audit remediation
    found it independently and fixed different halves; this is both.

    It globbed ``*.tif``/``*.tiff`` only, while the pipeline groups ``.png``,
    ``.jpg`` and ``.jpeg`` too, so a PNG plate looked like a folder with no
    images in it and the zipper had nothing to consume.

    It matched AppleDouble sidecars: ``._Plate_A01_F1.tif`` is four hundred
    bytes of macOS resource fork with an image suffix, and every file copied to
    a non-HFS volume has one — the commonest way a real plate arrives with
    exactly twice as many "images" as it has images.

    It used ``folder.glob`` with no ``is_file()``, so a dangling symlink named
    ``*.tif`` came back as an input image. ``well_plate_zipper.group_by_well``
    skips such a link as unreadable, so the two disagreed in the one direction
    that matters: the zipper never grouped it, and the deletion pass counted
    its well as landed and unlinked it.

    And none of that is cosmetic, because of what the count feeds. It is the
    ``loose_count`` the readout under the input field prints, the ``tifs > 3``
    threshold that decides whether the whole folder is treated as a zipper
    input, ``count_expected_wells``' fallback, and — most consequentially — the
    ``source_tifs`` tuples the post-run deletion closures are built from. A
    predicate applied to files it is not entitled to judge, in the place that
    decides what gets deleted.

    ``is_run_input_member`` is that entitlement question, asked once for the
    whole repo, and it is what makes the four fixes one rule rather than four.
    """
    return input_images_among(files_in(folder))


def input_images_among(files: list[Path]) -> list[Path]:
    """The run-input images in an already-made listing, order preserved.

    :func:`tif_files_in` with the directory read already done, for a caller
    that has one listing and several questions to ask of it. The rule is stated
    once, here, so the two cannot drift — which is the whole subject of the
    docstring above.
    """
    return [p for p in files if is_run_input_member(p.name)]


def _is_well_named(p: Path) -> bool:
    """Match `A1` / `A01` … `H12` against the well-token canonicaliser."""
    from util.well_token import is_valid_well_name
    return is_valid_well_name(p.stem)


def _well_zip_among(files: list[Path]) -> bool:
    """True iff *files* holds at least one well-named ``<WELL>.zip``.

    Takes an already-made listing so a caller that has one does not read the
    directory again to ask this. ``Path.match`` rather than a suffix
    comparison, because that is the rule ``glob("*.zip")`` applied and it is
    platform-dependent in a way a hand-written test is not: case-sensitive on
    POSIX, case-insensitive on Windows.
    """
    return any(p.match("*.zip") and _is_well_named(p) for p in files)


def has_well_zip(folder: Path) -> bool:
    """True iff *folder* contains at least one well-named ``<WELL>.zip``.

    A *file* named ``<WELL>.zip``: a directory of that name is not an archive,
    and answering True for one sent the whole folder down the fold-loose path,
    where opening it as a zip fails and the loose images beside it are then
    reported as already folded.
    """
    try:
        return _well_zip_among(files_in(folder))
    except OSError:
        return False


def _schema_field_index(schema: str, field: str) -> int:
    fields = [f.strip().lower() for f in str(schema or "").split(":")]
    return fields.index(field) if field in fields else -1


def _blank_well_tokens(opts: dict) -> set[str]:
    """The canonical wells this run treats as blanks, read the pipeline's way.

    Both scope readers — the preview under the input field and the run that
    executes it — need this, and they had a copy each: a local
    whitespace-splitting regex that read ``"H12 G12"`` as two wells where
    ``util.well_token.split_background_wells`` reads it as one. Two answers to
    one question about the same plate, and the same divergence had already been
    found and fixed once in the remote runner, where it killed every batch of a
    run. ``blank_wells_for_run`` is the single reading, and it also sees a blank
    named only in the pairing map.
    """
    return {
        canonical_well_label(w)
        for w in blank_wells_for_run(opts.get("background_well", ""),
                                     opts.get("background_well_map", ""))
        if canonical_well_label(w)
    }


def _loose_timepoints_for_wells(
    folder: Path, wells: set[str], *, schema: str, sep: str,
) -> set[str]:
    """Raw timepoint tokens of loose TIFs in *folder* belonging to *wells*.

    Tokens are compared verbatim (a dataset names its timepoints consistently),
    which is enough to tell a brand-new timepoint from one already on disk.
    """
    from util.well_token import canonical_well_label
    wi = _schema_field_index(schema, "well")
    ti = _schema_field_index(schema, "timepoint")
    if wi < 0 or ti < 0:
        return set()
    targets = {canonical_well_label(w) for w in wells if w}
    out: set[str] = set()
    for p in tif_files_in(folder):
        parts = p.stem.split(sep)
        if wi < len(parts) and ti < len(parts) \
                and canonical_well_label(parts[wi]) in targets:
            tok = parts[ti].strip()
            if tok:
                out.add(tok)
    return out


def _zip_timepoints(zip_path: Path, *, schema: str, sep: str) -> set[str]:
    """Raw timepoint tokens already present in a ``<WELL>.zip``'s members."""
    ti = _schema_field_index(schema, "timepoint")
    if ti < 0 or not zip_path.exists():
        return set()
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile):
        return set()
    out: set[str] = set()
    for name in names:
        if name.endswith("/"):
            continue
        parts = Path(name).stem.split(sep)
        if ti < len(parts):
            tok = parts[ti].strip()
            if tok:
                out.add(tok)
    return out


def _has_well_content_in(files: list[Path], subdirs: list[Path]) -> bool:
    """:func:`has_well_content` over a listing already made.

    The archive half is `has_well_zip`'s rule rather than a second spelling of
    it — the two decide the same thing about the same folder, one branch apart
    in `_plan_grouped_root`, so a directory named `A01.zip` must not be an
    archive to one and not to the other.
    """
    from util.well_token import is_valid_well_name
    return _well_zip_among(files) or any(
        is_valid_well_name(p.name) for p in subdirs)


def has_well_content(folder: Path) -> bool:
    """Return True iff *folder* contains *well-named* zip files OR
    well-named subdirectories.

    A stray ``archive.zip`` used to be enough to short-circuit the zipper
    even though the pipeline would then warn on every non-well zip — the
    user saw a flood of skipped-file warnings and concluded the pipeline
    was broken. Judge the basename with the shared canonicaliser to avoid
    that, so this module and the pipeline that then reads the folder agree
    on what counts as a well (ARCHITECTURE §15).

    The archive has to be a *file*: a directory named ``A01.zip`` — what an
    expander calls an archive it has unpacked in place — is not one, and
    counting it as one sent the whole folder down a branch where opening it
    fails. Same rule as :func:`has_well_zip`, and the same reason.
    """
    try:
        files, subdirs = files_and_subdirs(folder)
    except OSError:
        return False
    return _has_well_content_in(files, subdirs)


def dir_has_entries(folder: Path) -> bool:
    """True iff *folder* holds at least one entry of any kind.

    The weakest useful statement about a directory: it exists and is not empty.
    Used to tell an ``in/`` whose contents this module does not recognise as
    wells (which is still usable input — the pipeline reads it) apart from an
    ``in/`` that is genuinely empty (which is not, and must say so).
    """
    try:
        return any(folder.iterdir())
    except OSError:
        return False


# ── the one layout decision ──────────────────────────────────────────────────
# Analyze asks this question twice: once under the input field, to tell the user
# what pressing Run will do, and once at Run, to actually do it. Those were two
# separate implementations, and they disagreed — the readout accepted any zip in
# ``in/`` while the resolver required a well-named one, so a folder holding
# ``Plate1_A1.zip`` displayed "using as input" and then failed the run with
# "Cannot determine input layout". One planner, two consumers, no drift.

#: The selected path is a packed OME-TIFF, which Analyze cannot read.
PACKED_OME = "packed_ome"
#: The selected path is not a directory at all.
NOT_A_DIR = "not_a_dir"
#: The grouped root already holds per-well zips / folders; nothing to prepare.
GROUPED = "grouped"
#: The grouped root holds loose TIFs and no well zips — group them in place.
GROUP_LOOSE = "group_loose"
#: The grouped root holds loose TIFs *alongside* well zips — fold them in.
FOLD_LOOSE = "fold_loose"
#: The selected root holds enough loose TIFs to run well_plate_zipper into ``in/``.
ZIP_ROOT = "zip_root"
#: An ``in/`` that holds something, none of which parses as a well.
UNRECOGNISED = "unrecognised"
#: An ``in/`` that exists and is empty.
EMPTY_IN = "empty_in"
#: The folder is there and is a directory, but listing it failed.
UNREADABLE = "unreadable"
#: Nothing usable anywhere.
NO_INPUT = "no_input"

#: Shown when the user points Analyze at a packed OME-TIFF. Analyze processes
#: folders of per-well images; the Datasets converter is what de-interleaves a
#: packed file into one.
PACKED_OME_MESSAGE = (
    "⚠ This is a packed OME-TIFF. Convert it to a dataset folder first in the "
    "Datasets tab → “Convert OME-TIFF”, then open that folder here "
    "to process it."
)


#: Actions that describe input the run can proceed with.
RUNNABLE_ACTIONS = frozenset(
    {GROUPED, GROUP_LOOSE, FOLD_LOOSE, ZIP_ROOT, UNRECOGNISED}
)


@dataclass(frozen=True)
class InputPlan:
    """What Analyze will do with the folder the user selected.

    *input_dir* and *output_dir* are the pair the pipeline will be handed, and
    are ``None`` for the actions that cannot run. *loose_count* is the number of
    loose TIFs the action is about (0 where the action is not about loose TIFs),
    and *message* is the one-line readout, phrased for the user.
    """

    action: str
    input_dir: Path | None
    output_dir: Path | None
    loose_count: int
    message: str

    @property
    def runnable(self) -> bool:
        return self.action in RUNNABLE_ACTIONS


def _unreadable_plan(folder: Path, exc: OSError) -> InputPlan:
    """The plan for a directory that exists and could not be listed.

    A directory can be traversable and not listable — a share whose ACL grants
    execute and not read, a mount that has gone stale — and the two are told
    apart only by trying. ``Path.is_dir()`` says yes and the listing then fails.

    This needs its own action because the alternatives are both wrong.
    ``Path.glob`` answers such a directory with an empty list, so the folder
    read as `EMPTY_IN`, *"nothing to process"* — a statement about the plate,
    made from a failure to look at it, and exactly the silent substitution the
    pipeline is not allowed to make. Letting the ``OSError`` out is no better
    here: this is reached from a ``textChanged`` slot, where an exception leaves
    the path field showing the new folder and every label beside it still
    describing the previous one.
    """
    return InputPlan(
        UNREADABLE, None, None, 0,
        f"Could not read {folder.name}/ — {exc.strerror or exc}. "
        f"The folder is there; its contents could not be listed.",
    )


def _plan_grouped_root(root: Path, output_dir: Path, *, named_in: bool,
                       schema: tuple[str, str] = ("", "_")) -> InputPlan:
    """Classify a folder that is being used as the pipeline's input directly."""
    # One listing answers almost everything this decision needs: which loose
    # images are in the folder, whether any well archives are, whether any
    # well-named folders are, and — through `_plan_loose_append` below — which
    # of those images would be folded in. Each of those used to read the
    # directory for itself, so a readout for a plate of a few thousand loose
    # images enumerated it three times, and the three listings could disagree
    # about a file written between them.
    #
    # `dir_has_entries` below is the exception and keeps its own listing on
    # purpose: it asks whether the folder holds an entry of *any* kind, which
    # includes the ones this listing drops because they are neither a file nor
    # a directory — a dangling symlink, a socket. Answering it from here would
    # turn a folder holding only such an entry from usable-but-unrecognised
    # into empty. It short-circuits on the first entry, and it is reached only
    # when the folder has no images, no archives and no well folders in it.
    try:
        entries, subdirs = files_and_subdirs(root)
    except OSError as exc:
        return _unreadable_plan(root, exc)
    loose_files = input_images_among(entries)
    loose = len(loose_files)
    if loose > 0:
        if _well_zip_among(entries):
            # The number the *run* will fold, not the number of files with a
            # TIF suffix, when the caller supplied the schema that decides it.
            # ``plan_loose_append`` is the function ``append_loose_images_to_zips``
            # acts on, so this readout and that run cannot disagree.
            fold_schema, fold_sep = schema
            folded = loose
            if fold_schema:
                folded = _plan_loose_append(
                    root, filename_schema=fold_schema,
                    filename_sep=fold_sep, loose_files=loose_files).total
            if folded == 0:
                # Files are lying there and none of them is new. Saying "will
                # fold 0" invites the user to wonder what went wrong; saying
                # nothing at all hides that the folder is not as tidy as it
                # looks. Neither: this is a grouped root with nothing to add.
                return InputPlan(
                    GROUPED, root, output_dir, 0,
                    f"✓ Using {root.name}/ as input — the {loose} loose "
                    f"TIF(s) beside it are already in the well zips, or are "
                    f"not named as this schema's images",
                )
            return InputPlan(
                FOLD_LOOSE, root, output_dir, folded,
                f"✓ Will fold {folded} loose TIF(s) into the existing well "
                f"zips in {root.name}/",
            )
        return InputPlan(
            GROUP_LOOSE, root, output_dir, loose,
            f"✓ Will group {loose} loose TIF(s) into per-well folders "
            f"in {root.name}/",
        )
    if _has_well_content_in(entries, subdirs):
        return InputPlan(
            GROUPED, root, output_dir, 0,
            "✓ Using selected folder as input" if named_in
            else f"✓ Found {root.name}/ subfolder — using as input",
        )
    if dir_has_entries(root):
        # Usable, but say what was not recognised: a run that then processes
        # nothing has at least told the user why before it started.
        return InputPlan(
            UNRECOGNISED, root, output_dir, 0,
            f"✓ Using {root.name}/ as input — no well-named zips or "
            f"folders recognised in it",
        )
    return InputPlan(
        EMPTY_IN, None, None, 0,
        f"The {root.name}/ folder is empty — nothing to process.",
    )


def groupable_tif_count(files, filename_schema: str, filename_sep: str) -> int | None:
    """How many of *files* carry a well token under this schema.

    ``None`` when the question cannot be asked — no schema given, or a schema
    with no ``well`` field — because a count of zero and a count that was never
    computed are different answers and only one of them is safe to put next to
    a tick.

    Mirrors ``pipeline.well_plate_zipper._extract_well_from_filename``: the
    well's position in the schema, then :func:`util.well_token.canonical_well_label`
    on the token there. The same rule ``datasets.import_loose.import_loose_compute``
    mirrors, and for the same reason — the preview and the thing that does the
    work must not disagree about what counts as a match.
    """
    if not filename_schema:
        return None
    fields = [f.strip().lower() for f in filename_schema.split(":") if f.strip()]
    if "well" not in fields:
        return None
    at = fields.index("well")
    sep = filename_sep or "_"
    n = 0
    for f in files:
        parts = Path(f).stem.split(sep)
        if len(parts) > at and canonical_well_label(parts[at]) is not None:
            n += 1
    return n


def plan_input_layout(raw: Path, *, filename_schema: str = "",
                     filename_sep: str = "_") -> InputPlan:
    """Decide how *raw* — the folder the user selected — becomes pipeline input.

    Pure: reads the filesystem, changes nothing. :func:`resolve_input_output`
    executes the plan and ``AnalyzeTab._refresh_output`` displays it, so the
    readout under the input field cannot disagree with what Run then does.

    *filename_schema* / *filename_sep* are what make that last sentence true
    for the fold-loose case, and they are optional only because two internal
    callers want the layout and not the count. Given them, the count comes from
    :func:`plan_loose_append` — the function the *run* uses — instead of from a
    glob. The two disagree by construction: the glob counts every file with a
    TIF suffix, while the append rejects a name whose well token will not parse
    and a file already present in its target archive. Measured on a folder with
    one new image, one duplicate and one calibration frame, the readout said
    "Will fold 3 loose TIF(s)" and the run folded one.

    A folder *named* ``in`` is the input; a folder that *contains* ``in/`` feeds
    the pipeline from that subfolder. The two are classified by the same
    function, which is the point — the contains-``in/`` path used to demand
    recognisable well content that the named-``in`` path never asked for, so an
    ``in/`` full of, say, ``Plate1_A1.zip`` worked when selected directly and
    failed when its parent was selected.

    Loose TIFs sitting at the selected root still win over an ``in/`` that has
    nothing recognisable in it: that is a fresh drop next to a folder a previous
    interrupted run created, and it must still reach well_plate_zipper.
    """
    if detect_ome_input(raw) is not None:
        return InputPlan(PACKED_OME, None, None, 0, PACKED_OME_MESSAGE)
    if not raw.is_dir():
        return InputPlan(NOT_A_DIR, None, None, 0, "Not a directory.")

    schema = (filename_schema, filename_sep)
    if raw.name.lower() == "in":
        return _plan_grouped_root(raw, raw.parent / "out", named_in=True,
                                  schema=schema)

    in_sub = raw / "in"
    out_dir = raw / "out"
    if in_sub.is_dir():
        nested = _plan_grouped_root(in_sub, out_dir, named_in=False,
                                    schema=schema)
        if nested.action not in (UNRECOGNISED, EMPTY_IN):
            return nested
    else:
        nested = None

    try:
        loose = tif_files_in(raw)
    except OSError as exc:
        return _unreadable_plan(raw, exc)
    tifs = len(loose)
    if tifs > 3:
        # The count has to be the one the grouping will actually reach, for the
        # reason this function's docstring already gives about the fold-loose
        # branch: a glob counts every file with a TIF suffix, while grouping
        # rejects a name whose well token will not parse. That was fixed there
        # and missed here, and the gap is visible the moment anything else on
        # screen reports the same folder honestly — a schema detection that
        # refuses sat directly above a tick promising to group eight files it
        # could not have grouped.
        groupable = groupable_tif_count(loose, filename_schema, filename_sep)
        if groupable is None or groupable == tifs:
            message = f"✓ Will group {tifs} loose TIF file(s) by well → in/"
        elif groupable == 0:
            message = (f"None of the {tifs} loose TIF file(s) carry a well in "
                       f"the filename schema — nothing would be grouped.")
        else:
            short = tifs - groupable
            verb = "does" if short == 1 else "do"
            message = (f"✓ Will group {groupable} of {tifs} loose TIF file(s) "
                       f"by well → in/ ({short} {verb} not match the schema)")
        return InputPlan(ZIP_ROOT, in_sub, out_dir, tifs, message)
    if nested is not None:
        return nested
    return InputPlan(
        NO_INPUT, None, None, tifs,
        "No TIF files or in/ folder found in selected directory.",
    )


def _layout_error(plan: InputPlan, raw: Path) -> str:
    """The long-form version of an unrunnable plan, for the run-time exception.

    The readout has room for one line; a failed run has room to explain. Both
    describe the same verdict, so they are built from the same plan.
    """
    if plan.action == PACKED_OME:
        return plan.message
    if plan.action == NOT_A_DIR:
        return f"Not a directory:\n{raw}"
    if plan.action == UNREADABLE:
        return (
            f"{plan.message}\n\n"
            "The folder exists, but listing what is in it failed. On a network "
            "share or a mounted volume this is usually a permissions problem "
            "or a dropped connection — the run is not started, because what is "
            "in the folder is unknown rather than known to be nothing.\n\n"
            f"Folder selected: {raw}"
        )
    if plan.action == EMPTY_IN:
        return (
            f"{plan.message}\n\n"
            "The folder that holds the images is there, but there is nothing "
            "in it — no per-well zips, no per-well folders, no TIF files.\n\n"
            f"Folder selected: {raw}"
        )
    return (
        "Cannot determine input layout.\n\n"
        "Expected one of:\n"
        '  \u2022 Selected folder is named "in"\n'
        '  \u2022 Selected folder contains a sub-folder named "in"\n'
        "  \u2022 Selected folder contains more than 3 TIF files\n"
        "    (these will be grouped into per-well folders first)\n\n"
        f"Folder selected: {raw}\n"
        f"TIF files found: {plan.loose_count}"
    )


def _well_in_grouped_root(root: Path, target: str) -> bool:
    """True iff *root* holds *target* as a per-well ``<WELL>.zip`` or ``<WELL>/``
    folder (non-recursive, mirroring the pipeline's grouped-input lookup)."""
    from util.well_token import canonical_well_label
    try:
        files, subdirs = files_and_subdirs(root)
    except OSError:
        return False
    # per-well zip: <root>/<WELL>.zip — a *file*, by `_well_zip_among`'s rule.
    # This guard is what `_background_well_validation_error` asks before a run
    # starts, so a directory named `A01.zip` answering yes here is a run allowed
    # to start against a background well the pipeline will not find.
    if any(p.match("*.zip") and canonical_well_label(p.stem) == target
           for p in files):
        return True
    # per-well folder:  <root>/<WELL>/
    return any(canonical_well_label(p.name) == target for p in subdirs)


def well_present_in_input(input_dir: Path, well: str, *,
                          schema: list[str] | None = None,
                          sep: str = "_") -> bool:
    """True iff *well* (any 96-well form, e.g. ``'c11'`` / ``'C11'``) is present
    in *input_dir* in any layout the pipeline reads: a per-well ``<WELL>.zip``,
    a per-well folder, or loose TIFFs whose schema ``well`` field matches.

    Mirrors the empty-well sampling in :func:`process_microscopy.sample_empty_well_images`
    (non-recursive zip / folder lookup, recursive loose-TIFF parse) so the
    Analyze pre-run check agrees with what the pipeline will actually find. Used
    to block a run when ``--flatfield empty_well`` / ``--background empty_well``
    name a background well that doesn't exist.

    The Analyze form validates the *selected* folder, but a folder that holds an
    ``in/`` subfolder (the common ``<dataset>/in`` + ``<dataset>/out`` layout)
    feeds the pipeline from that ``in/``, not the parent — see
    :func:`resolve_input_output`. The grouped-layout lookup therefore also
    searches ``<input_dir>/in`` so a well packaged as ``in/<WELL>.zip`` /
    ``in/<WELL>/`` isn't reported missing when it's really one level down.
    """
    from util.well_token import canonical_well_label
    target = canonical_well_label(well or "")
    if target is None:
        return False
    # 1 + 2) grouped layouts, in the selected dir and (when present) its ``in/``.
    roots = [input_dir]
    in_sub = input_dir / "in"
    if input_dir.name.lower() != "in" and in_sub.is_dir():
        roots.append(in_sub)
    for root in roots:
        if _well_in_grouped_root(root, target):
            return True
    # 3) loose TIFFs — parse the 'well' field from the filename schema. The
    # recursive walk from the selected dir already descends into ``in/``.
    fields = [str(f).strip().lower() for f in (schema or [])]
    if "well" not in fields:
        return False
    well_idx = fields.index("well")
    try:
        for p in input_dir.rglob("*.tif*"):
            parts = p.stem.split(sep)
            if well_idx < len(parts) and canonical_well_label(parts[well_idx]) == target:
                return True
    except OSError:
        return False
    return False


def iter_well_tokens(input_dir: Path) -> list[str]:
    """Canonical well tokens (``A01``) present in a *resolved* input dir.

    Reads the same already-grouped layouts the pipeline does — per-well
    ``<WELL>.zip`` files and per-well ``<WELL>/`` folders — and returns the
    canonical tokens in sorted order. Used to surface grouping progress when the
    inputs are already grouped/zipped: the zipper is skipped in that case and so
    emits no per-well progress of its own, leaving the Group stage stuck at 0 and
    the wells jumping straight from empty to processing.
    """
    from util.well_token import canonical_well_label
    seen: set[str] = set()
    out: list[str] = []
    try:
        files, subdirs = files_and_subdirs(input_dir)
    except OSError:
        return out
    # Archives and folders interleave in one name-sorted sequence, because that
    # is the order the tokens come back in: a plate holding `A01.zip` beside
    # `A02/` must not report A02 after every zip in the folder.
    candidates = sorted(
        [(p.name, canonical_well_label(p.stem))
         for p in files if p.suffix.lower() == ".zip"]
        + [(p.name, canonical_well_label(p.name)) for p in subdirs],
        key=lambda pair: pair[0],
    )
    for _name, tok in candidates:
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def iter_all_well_tokens(raw: Path, *, filename_schema: str,
                        filename_sep: str) -> list[str]:
    """Every distinct well the run will read from *raw*, canonical and sorted.

    The UNION of already-grouped wells (per-well zips / folders) and the well
    tokens parsed out of loose TIFs, taking the layout from
    :func:`plan_input_layout` so it reads the folder the run will actually read.
    A partially-grouped ``in/`` — some well folders plus leftover loose TIFs —
    therefore reports the whole plate rather than the grouped few.

    :func:`iter_well_tokens` alone is not this: it reads the grouped layouts and
    nothing else, so a plate that is still all loose TIFs answers empty. That
    distinction is why this exists as its own function — the Analyze pairing
    editor offers these wells, and offering none for the commonest
    not-yet-grouped input would be an empty plate.

    Best-effort by design, like the count built on it: a layout nothing can be
    read from answers empty rather than raising, and
    :func:`resolve_input_output` is what says there is no input at all.
    """
    from util.well_token import canonical_well_label

    fields = [f.strip().lower() for f in str(filename_schema or "").split(":")]
    well_idx = fields.index("well") if "well" in fields else -1

    def _loose_toks(folder: Path) -> set[str]:
        if well_idx < 0:
            return set()
        toks: set[str] = set()
        for p in tif_files_in(folder):
            parts = p.stem.split(filename_sep)
            tok = canonical_well_label(parts[well_idx]) if well_idx < len(parts) else None
            if tok:
                toks.add(tok)
        return toks

    toks: set[str] = set()
    grouped = plan_input_layout(raw).input_dir
    if grouped is not None:
        toks |= set(iter_well_tokens(grouped)) | _loose_toks(grouped)
        if grouped != raw:
            toks |= _loose_toks(raw)  # loose TIFs at the selected root
    return sorted(toks)


def count_expected_wells(raw: Path, *, filename_schema: str, filename_sep: str) -> int:
    """Best-effort count of distinct wells the run will produce, for the Analyze
    progress denominator.

    The length of :func:`iter_all_well_tokens`, with two fallbacks of its own. A
    layout nothing can be counted in falls back to the loose-TIF count and then
    to 1, because this is a progress denominator and a zero would leave the
    dashboard dividing by nothing.

    And a folder that cannot be *listed* falls back to 1 as well, which is newly
    possible: the listing helpers raise where ``Path.glob`` used to swallow the
    refusal and hand back an empty list. The caller asks for this *before*
    ``resolve_input_output``, so raising from here pre-empted the refusal that
    names the folder and the errno, and surfaced instead as a bare ``[Errno 13]``
    through ``pipeline_runner``'s broad handler with nothing logged by the
    resolver at all — while the readout went on showing the long UNREADABLE
    sentence. One folder, two answers.
    """
    try:
        toks = iter_all_well_tokens(raw, filename_schema=filename_schema,
                                    filename_sep=filename_sep)
        return len(toks) or len(tif_files_in(raw)) or 1
    except OSError:
        return 1


@dataclass(frozen=True)
class LooseAppendPlan:
    """Which loose images would be folded into which well zips, and how many.

    The read-only half of :func:`append_loose_images_to_zips`. Everything it
    reports comes from ``iterdir`` and each archive's central directory, so it
    can be asked before the user has committed to anything — which is what lets
    the Analyze pane describe a run it has not started.
    """

    #: canonical well → the loose files that are not already in its archive.
    by_well: dict[str, list[Path]] = field(default_factory=dict)
    #: Wells whose archive already held at least one member. Carried because
    #: the append logs "created" or "updated" from it, and an archive that
    #: exists but is empty is "created" — the planner has the member list in
    #: hand, so deriving this from ``target.exists()`` at the write instead
    #: silently changed that word for exactly that case.
    nonempty: frozenset[str] = frozenset()

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.by_well.values())

    @property
    def wells(self) -> set[str]:
        return set(self.by_well)


def plan_loose_append(
    grouped_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
    log_fn: Callable[[str], None] | None = None,
    loose_files: list[Path] | None = None,
) -> LooseAppendPlan:
    """Decide which loose images would be folded in, writing nothing.

    Split out of :func:`append_loose_images_to_zips` so the same decision can
    be *previewed*. Nothing here mutates: a plan that is never acted on leaves
    the input directory exactly as it found it.

    The duplicate rule is the subtle part and belongs here rather than at the
    call site. A loose file whose basename is already a member of the target
    archive is not new data — it is the same image the user dropped twice, or
    the leftovers of an interrupted append — so it is neither counted nor
    deleted, and a run whose every loose file is a duplicate has nothing new to
    process however many files are lying there.

    *loose_files* is :func:`tif_files_in`'s answer for *grouped_dir*, for a
    caller that already has it. The layout readout does — it counts the loose
    images to decide whether there is anything to fold at all — and every
    function this used to reach read that same directory again for itself, so
    one readout of a plate holding a few thousand loose images enumerated it
    three times over. Passing it is an optimisation and never required for
    correctness: omitted, this reads the directory itself.
    """
    # Gate: only a zip-grouped layout qualifies. Folder-only / loose-only
    # layouts are the normal zipper's job. Asked here rather than in
    # `_plan_loose_append` so a caller that has already established it — the
    # layout readout has, which is why it reached the fold branch at all — does
    # not read the directory a second time to be told what it knows.
    if not has_well_zip(grouped_dir):
        return LooseAppendPlan()
    return _plan_loose_append(
        grouped_dir, filename_schema=filename_schema,
        filename_sep=filename_sep, log_fn=log_fn, loose_files=loose_files)


def _plan_loose_append(
    grouped_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
    log_fn: Callable[[str], None] | None = None,
    loose_files: list[Path] | None = None,
) -> LooseAppendPlan:
    """:func:`plan_loose_append` with the well-archive gate already answered."""
    from util.well_token import canonical_well_label

    empty = LooseAppendPlan()
    fields = [str(f).strip().lower() for f in str(filename_schema or "").split(":")]
    if "well" not in fields:
        return empty
    well_idx = fields.index("well")

    # Loose top-level image files (not zips, not dirs), grouped by well. The
    # rule for which of them count is `tif_files_in`'s, asked once for the whole
    # repo: this branch decides what `plan_input_layout` tells the user it is
    # about to do, and the copy of the rule that used to live here took
    # `.tif`/`.tiff` alone, so a PNG plate was described as holding no loose
    # images at all.
    by_well: dict[str, list[Path]] = {}
    if loose_files is None:
        try:
            loose_files = tif_files_in(grouped_dir)
        except OSError:
            return empty
    for p in loose_files:
        parts = p.stem.split(filename_sep)
        token = canonical_well_label(parts[well_idx]) if well_idx < len(parts) else None
        if token is None:
            if log_fn:
                log_fn(f"[append] Skipping loose file (no valid well token): {p.name}\n")
            continue
        by_well.setdefault(token, []).append(p)

    if not by_well:
        return empty

    planned: dict[str, list[Path]] = {}
    had_members: set[str] = set()
    for well, files in sorted(by_well.items()):
        target = grouped_dir / f"{well}.zip"
        existing: set[str] = set()
        if target.exists():
            try:
                with zipfile.ZipFile(target, "r") as zf:
                    existing = set(zf.namelist())
            except (OSError, zipfile.BadZipFile):
                existing = set()
        new_files = [f for f in files if f.name not in existing]
        skipped = len(files) - len(new_files)
        if skipped and log_fn:
            log_fn(
                f"[append] {well}: skipped {skipped} loose file(s) already in "
                f"{target.name} (kept existing).\n"
            )
        if new_files:
            planned[well] = new_files
            if existing:
                had_members.add(well)
    return LooseAppendPlan(by_well=planned, nonempty=frozenset(had_members))


def append_loose_images_to_zips(
    grouped_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[int, set[str]]:
    """Fold loose image files sitting next to grouped well zips into those zips.

    When a directory already holds per-well ``<WELL>.zip`` archives and the user
    drops additional loose TIFFs alongside them, those files would otherwise be
    ignored (``has_well_content`` short-circuits the zipper). This parses each
    loose file's well token, appends it into the matching ``<WELL>.zip`` (or
    creates a new ``<WELL>.zip`` for a well that doesn't have one yet), and
    deletes the loose source once it is durably in the zip.

    Rules:
      * Only acts when *grouped_dir* already contains ≥1 well-named zip (i.e. a
        zip-grouped layout). Folder-only / loose-only layouts are left to the
        normal zipper path.
      * Duplicate member name (the loose file's basename already inside the zip)
        → keep the existing member, skip the loose file (left in place).
      * Each appended loose file is unlinked right after the verified atomic
        rewrite; skipped duplicates are not deleted.

    Returns ``(files_appended, wells_touched)`` where *wells_touched* are the
    canonical well tokens whose zip gained at least one member (used by the
    caller to drive incremental reprocessing).

    **Which** files those are is :func:`plan_loose_append`'s answer, not a
    second one kept here: the Analyze pane shows the user what a run will do
    before they press Run, and a preview that decides "what counts as a new
    loose image" for itself is a preview that can disagree with the run it is
    describing.
    """
    appended = 0
    wells_touched: set[str] = set()

    plan = plan_loose_append(
        grouped_dir, filename_schema=filename_schema,
        filename_sep=filename_sep, log_fn=log_fn,
    )
    for well, new_files in sorted(plan.by_well.items()):
        target = grouped_dir / f"{well}.zip"
        existing = well in plan.nonempty

        def _append(target=target, new_files=new_files) -> None:
            with atomic_zip(target) as zf:
                if target.exists():
                    with zipfile.ZipFile(target, "r") as src:
                        for name in src.namelist():
                            zf.writestr(src.getinfo(name), src.read(name))
                for f in new_files:
                    zf.write(f, arcname=f.name)

        retry_io(
            _append, what=target.name, advice=_CONSOLIDATION_ADVICE,
            log_fn=log_fn,
        )

        created = "created" if not existing else "updated"
        # Durable in the zip now — remove the loose sources. A file that will
        # not unlink is a disk-space problem rather than a data one: the
        # archive beside it already holds the bytes, and the next run finds the
        # leftover and re-packs an identical member. It still gets a log line,
        # because a folder quietly accumulating loose copies of everything
        # already archived is exactly the kind of thing nobody notices until a
        # volume fills.
        for f in new_files:
            with swallow(f"removing {f.name}, already archived into {target.name}"):
                f.unlink()
        appended += len(new_files)
        wells_touched.add(well)
        if log_fn:
            log_fn(
                f"[append] {well}: appended {len(new_files)} loose image(s) into "
                f"{target.name} ({created}).\n"
            )

    return appended, wells_touched


@dataclass(frozen=True)
class ScopePlan:
    """What a run will do about work an earlier run already did, and why.

    *mode* is ``"unchanged"`` (nothing new to fold in, so the run's own
    incremental / force settings stand), ``"incremental"`` (new FOVs only), or
    ``"force"`` (a full reprocess, because a cross-well model would otherwise
    be built from a different set of images than the outputs it is compared
    against). *reason* is the sentence both the run log and the Analyze pane's
    pre-run readout print — neither composes its own, so a preview cannot
    describe a decision the run does not then make.
    """

    mode: str
    #: What the run log says once the fold has happened — past tense, because
    #: by the time it is printed it has.
    reason: str
    #: What the pre-run readout says *before* it happens. The same verdict and
    #: the same justification, in the tense that is true at the time: a preview
    #: printing "Appended 3 loose image(s)" has appended nothing, and a readout
    #: that claims work it has not done is worse than one that says nothing.
    preview: str = ""


def _scope_verdict(
    appended: int,
    wells_touched: set[str],
    *,
    flatfield: str,
    background: str,
    bg_tokens: set[str],
    new_bg_tps: set[str],
    existing_bg_tps: set[str],
) -> ScopePlan:
    """The scope decision itself, over facts both callers can supply.

    Takes counts rather than a directory so it can be reached from the preview
    — which has planned the append but not performed it — and from the run,
    which has performed it. One rule, asked twice.
    """
    basic_model = flatfield == "basic" or background == "basic"
    empty_well_in_use = flatfield == "empty_well" or background == "empty_well"
    bg_touched = bool(bg_tokens & wells_touched)
    stale_existing_tp = bool(new_bg_tps & existing_bg_tps)
    images = f"{appended} loose image(s) into {len(wells_touched)} well(s)"
    if basic_model or (empty_well_in_use and bg_touched and stale_existing_tp):
        why = ("basic flat-field/background is sampled across all wells"
               if basic_model
               else "the empty-well background well gained blanks at an "
                    "already-processed timepoint")
        return ScopePlan(
            "force",
            f"Appended {images}; forcing a full reprocess ({why}).",
            f"Will fold in {images} and reprocess the plate in full ({why}).")
    return ScopePlan(
        "incremental",
        f"Appended {images}; processing only new FOVs (incremental).",
        f"Will fold in {images} and compute only the new fields "
        f"(incremental).")


def plan_incremental_scope(
    opts: dict,
    input_dir: Path,
) -> ScopePlan:
    """What :func:`select_incremental_scope` will decide, without doing it.

    Every input to the decision is readable without writing anything: which
    loose files would be folded in (:func:`plan_loose_append`), and which
    timepoints the background well already holds. So the preview reaches the
    same verdict by the same function the run uses, rather than predicting it.

    The one thing it cannot foresee is an I/O failure during the append itself,
    which makes the run skip the fold entirely. That is a fault the run reports
    when it happens; it is not something a preview can honestly promise
    against, and pretending otherwise would be the kind of guess this returns a
    named ``"unchanged"`` mode to avoid.
    """
    schema = str(opts.get("filename_schema") or "")
    sep = str(opts.get("filename_sep") or "_")
    try:
        plan = plan_loose_append(
            input_dir, filename_schema=schema, filename_sep=sep)
    except OSError:
        return ScopePlan("unchanged", "", "")
    if not plan.total:
        return ScopePlan("unchanged", "", "")

    bg_tokens = _blank_well_tokens(opts)
    flatfield = str(opts.get("flatfield", "none") or "none")
    background = str(opts.get("background", "") or "")
    empty_well_in_use = flatfield == "empty_well" or background == "empty_well"
    new_bg_tps: set[str] = set()
    existing_bg_tps: set[str] = set()
    if empty_well_in_use and bg_tokens:
        new_bg_tps = _loose_timepoints_for_wells(
            input_dir, bg_tokens, schema=schema, sep=sep)
        for w in bg_tokens:
            if w:
                existing_bg_tps |= _zip_timepoints(
                    input_dir / f"{w}.zip", schema=schema, sep=sep)
    return _scope_verdict(
        plan.total, plan.wells,
        flatfield=flatfield, background=background, bg_tokens=bg_tokens,
        new_bg_tps=new_bg_tps, existing_bg_tps=existing_bg_tps,
    )


def select_incremental_scope(
    opts: dict,
    input_dir: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> None:
    """Append loose images next to grouped well zips, then choose the run scope.

    Mutates *opts* in place: if loose files were folded into existing/new well
    zips, switch the run to FOV-level **incremental** mode (process only the new
    FOVs, merge into the existing per-well CSV + out-zip). Falls back to a full
    ``--force`` reprocess when the run uses cross-well models the incremental
    path can't keep consistent — a ``basic`` flat-field / background (sampled
    across all wells) or an ``empty_well`` model whose designated background well
    just received new files.

    Qt-free so it stays testable and reusable; the GUI passes a ``log_fn`` that
    forwards to its run-log queue.
    """
    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    flatfield = str(opts.get("flatfield", "none") or "none")
    background = str(opts.get("background", "") or "")
    bg_tokens = _blank_well_tokens(opts)
    empty_well_in_use = flatfield == "empty_well" or background == "empty_well"

    # Snapshot the background well's timepoints BEFORE the append consumes the
    # loose files. The empty-well field is per-timepoint, so newly-added blanks
    # only make ALREADY-PROCESSED wells stale when they land on a timepoint that
    # already had a blank (that timepoint's field changes). Blanks for a
    # brand-new timepoint only affect that timepoint's own wells, which the
    # incremental run processes fresh — no full reprocess needed. (A pre-existing
    # timepoint that had no blank of its own used the pooled fallback; that rare
    # case isn't tracked here.)
    existing_bg_tps: set[str] = set()
    new_bg_tps: set[str] = set()
    if empty_well_in_use and bg_tokens:
        new_bg_tps = _loose_timepoints_for_wells(
            input_dir, bg_tokens,
            schema=opts["filename_schema"], sep=opts["filename_sep"])
        for w in bg_tokens:
            if w:
                existing_bg_tps |= _zip_timepoints(
                    input_dir / f"{w}.zip",
                    schema=opts["filename_schema"], sep=opts["filename_sep"])

    try:
        appended, wells_touched = append_loose_images_to_zips(
            input_dir,
            filename_schema=opts["filename_schema"],
            filename_sep=opts["filename_sep"],
            log_fn=log_fn,
        )
    except OSError as exc:
        _log(f"[append] Skipped loose-file append ({exc}).\n")
        return
    if not appended:
        return

    verdict = _scope_verdict(
        appended, wells_touched,
        flatfield=flatfield, background=background,
        bg_tokens=bg_tokens, new_bg_tps=new_bg_tps,
        existing_bg_tps=existing_bg_tps,
    )
    if verdict.mode == "force":
        opts["force"] = True
        opts["incremental"] = False
    else:
        opts["incremental"] = True
    _log(f"[append] {verdict.reason}\n")


def run_zipper(
    tif_folder: Path,
    out_folder: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
    progress_fn: Callable[[str], None] | None = None,
    filename_schema: str,
    filename_sep: str,
    proc_hook: Callable[[subprocess.Popen | None], None] | None = None,
    move_source_tifs: bool = False,
) -> None:
    zipper = find_zipper_script()
    if zipper is None:
        raise RuntimeError(
            f"pipeline/well_plate_zipper.py not found. Expected: {ZIPPER_SCRIPT}")

    out_folder.mkdir(parents=True, exist_ok=True)
    cmd = build_zipper_cmd(
        zipper, tif_folder, out_folder, filename_schema, filename_sep,
        move_source_tifs=move_source_tifs,
    )
    if log_fn:
        log_fn(f"[zipper] $ {' '.join(cmd)}\n")

    well_pat = re.compile(r"^([A-H]\d{2}):\s+\d+\s+files", re.IGNORECASE)
    popen_kwargs: dict = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # A filename can carry any byte the filesystem allows, and the zipper
        # echoes every one it groups. Decoding strictly means one such byte
        # raises out of the reader below — see the `finally` there for why
        # that was worse than a lost log line.
        **TEXT_UTF8,
        bufsize=1,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    # Put the zipper in its own session / process group so the runner's
    # Stop button can signal it the same way it signals the pipeline.
    # Without this, the zipper inherited the GUI's session and Stop
    # could never reach it.
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    proc = subprocess.Popen(cmd, **popen_kwargs)
    if proc_hook is not None:
        try:
            proc_hook(proc)
        except Exception:
            swallowed()  # was: pass (silent except)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if log_fn:
                log_fn(f"[zipper] {line}\n")
            m = well_pat.match(line.strip())
            if m and progress_fn:
                progress_fn(m.group(1).upper())
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Grouping step exited with code {proc.returncode}.")
    finally:
        # Reap it whatever ended the read. The zipper is spawned into its own
        # process group so Stop can reach it, which also means nothing else
        # will reap it: an exception escaping the loop above left it running,
        # rewriting the user's input directory, while the caller reported the
        # grouping had failed.
        if proc.poll() is None:
            with swallow("stopping the zipper after its output stopped"):
                signal_subprocess(proc)
        with swallow("waiting for the zipper subprocess"):
            proc.wait(timeout=30)
        if proc_hook is not None:
            with swallow("clearing the zipper process hook"):
                proc_hook(None)


def _consolidate_mixed_to_zips(
    folder: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> bool:
    """Normalise a *mixed* grouped dir (some ``<well>.zip`` + some ``<well>/``
    folders) to all-zips, so the pipeline reads every well.

    A hard-killed run can leave ``in/`` with zips for the wells it finished
    compressing and bare folders for the rest. ``process_microscopy`` prefers
    zips — ``well_folders`` is ignored whenever ANY zip is present — so the
    folder-only wells would be silently dropped. This zips each well folder that
    has no matching zip yet (atomic tmp→rename) and removes the now-redundant
    folder, mirroring the remote ``_stage_mixed_mode`` behaviour. Folders for
    wells that already have a zip are left untouched (the zip wins), as are
    folders holding no TIFs at all — packing one would publish a member-less
    ``<WELL>.zip``. No-op unless the dir is genuinely mixed. Returns True iff it
    zipped at least one folder.
    """
    from util.well_token import canonical_well_label

    try:
        files, subdirs = files_and_subdirs(folder)
    except OSError:
        return False
    zip_wells = {
        canonical_well_label(p.stem)
        for p in files
        if p.suffix.lower() == ".zip" and canonical_well_label(p.stem)
    }
    if not zip_wells:
        return False  # pure folders / pure loose — folder mode handles it
    leftover: dict[str, Path] = {}
    for p in subdirs:
        tok = canonical_well_label(p.name)
        if tok and tok not in zip_wells:
            leftover[tok] = p
    if not leftover:
        return False

    if log_fn:
        log_fn(
            f"[zipper] Mixed input ({len(zip_wells)} zip(s) + "
            f"{len(leftover)} bare well folder(s)) — zipping the leftover "
            f"folder(s) so every well is processed.\n"
        )
    zipped_any = False
    for well, well_dir in sorted(leftover.items()):
        target = folder / f"{well}.zip"
        # Input images, by the one definition — this packs the *user's* well
        # folder into the archive the pipeline segments from, so a narrower
        # rule here silently drops a PNG plate's images out of the run.
        tifs = tif_files_in(well_dir)
        if not tifs:
            # Nothing to pack. Writing the archive anyway would publish a
            # member-less <WELL>.zip — which macOS refuses to expand and the
            # pipeline reads as a well whose images vanished — and deleting the
            # folder would destroy whatever non-TIF content it does hold.
            if log_fn:
                log_fn(
                    f"[zipper]   {well}: folder holds no TIFs — left as-is "
                    "(no zip written)\n"
                )
            continue
        def _pack(target=target, tifs=tifs) -> None:
            with atomic_zip(target) as zf:
                for t in tifs:
                    zf.write(t, arcname=t.name)

        retry_io(
            _pack, what=target.name, advice=_CONSOLIDATION_ADVICE, log_fn=log_fn,
        )
        # Drop the TIFs the archive now holds, and the folder only when the
        # archive covers everything in it. The zero-TIF branch above already
        # refuses to destroy non-image content; this is the same rule applied
        # to the branch where the folder *does* yield images, which is where
        # the content was actually being lost.
        outcome = replace_folder_with_archive(well_dir, tifs)
        zipped_any = True
        if log_fn:
            tail = (
                ""
                if outcome.folder_removed
                else (f" — folder kept, {len(outcome.kept)} file(s) not "
                      f"archived: {', '.join(outcome.kept)}")
            )
            log_fn(
                f"[zipper]   {well}: folder → {well}.zip "
                f"({len(tifs)} file(s)){tail}\n"
            )
    return zipped_any


def _fold_well_folders_into_existing_zips(
    grouped: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> int:
    """Merge each ``<WELL>/`` folder into the ``<WELL>.zip`` beside it.

    The complement of :func:`_consolidate_mixed_to_zips`, which deliberately
    leaves such a folder alone because there the zip is the *finished* artefact
    of a half-killed run and the folder is its redundant twin. On the resume
    path the folder is the opposite thing: images the zipper has just grouped,
    which are new to a well that already has an archive. Left as a folder they
    are invisible, because ``process_microscopy`` reads ``well_folders`` only
    when there are no zips at all — so the run reports success having analysed
    nothing new, and the source images have been deleted.

    **A same-named member is replaced when its bytes differ.** The folder is
    what the zipper has just written from the user's current images, so where
    the two disagree the folder is the newer truth; keeping the archive's copy
    would analyse the pixels the user replaced and delete the replacement.
    Where the bytes agree the member is carried over untouched, so re-running
    is a no-op rather than a duplication.

    Only the folder's own top level is packed, matching
    :func:`_consolidate_mixed_to_zips`. A ``masks/`` or ``analysis/``
    subdirectory is not run input; flattening one into the well's *input*
    archive would both destroy it as an identifiable artefact and offer it back
    to the next scan as an image to segment.

    Returns the number of wells folded.
    """
    from util.image_exts import is_group_image
    from util.well_token import canonical_well_label

    folded = 0
    files: list[Path] = []
    subdirs: list[Path] = []
    with swallow(f"listing the grouped directory {grouped.name}"):
        files, subdirs = files_and_subdirs(grouped)
    # By *name*, not by path, at both steps. `files_and_subdirs` sorts the Path
    # objects, and `PurePath` comparison casefolds on Windows — which is the
    # right default for the scan that strides a sample, and the wrong one here,
    # where both of these collapse several entries onto one well token: where
    # `a1.zip` and `A01.zip` both exist the dict keeps whichever comes last, and
    # the folder order decides whose images land in the archive last. Its two
    # sibling sites (`_grouped_member_index` and `iter_well_tokens`) were given
    # the same key for the same reason, and a third that merely inherited path
    # order would read as a decision rather than the omission it was.
    zips = {
        canonical_well_label(q.stem): q
        for q in sorted(files, key=lambda q: q.name)
        if q.suffix.lower() == ".zip" and canonical_well_label(q.stem)
    }
    for entry in sorted(subdirs, key=lambda q: q.name):
        tok = canonical_well_label(entry.name)
        target = zips.get(tok) if tok else None
        if target is None:
            continue          # no archive to fold into; the consolidator owns it
        # Scoped to one well on purpose. Wrapping the whole loop meant a single
        # unreadable archive stopped folding for every well after it, and those
        # wells then kept a `<WELL>/` folder the pipeline never reads while
        # their loose sources were deleted as consumed.
        with swallow(f"folding {entry.name} into {target.name}"):
            folded += _fold_one_well(entry, target, log_fn=log_fn,
                                     is_image=is_group_image)
    return folded


def _fold_one_well(entry: Path, target: Path, *,
                   log_fn: Callable[[str], None] | None,
                   is_image: Callable[[str], bool]) -> int:
    """Fold one ``<WELL>/`` folder into the ``<WELL>.zip`` beside it."""
    images = [q for q in files_in(entry) if is_image(q.name)]
    by_name = {q.name: q for q in images}

    with zipfile.ZipFile(target) as src:
        # `(size, CRC)` from the central directory, so deciding what is new
        # costs no decompression at all.
        carried = [
            info for info in src.infolist()
            if not _member_is_superseded(info, by_name.get(
                info.filename.rsplit("/", 1)[-1]))
        ]
        kept_names = {i.filename.rsplit("/", 1)[-1] for i in carried}
        incoming = [q for q in images if q.name not in kept_names]
        if incoming:
            with atomic_zip(target) as dst:
                for info in carried:
                    # One member at a time, and the original `ZipInfo` — so a
                    # `ZIP_STORED` input archive is not silently re-deflated,
                    # every member keeps its timestamp (the upload cache key
                    # and rsync's quick-skip both read those), and the whole
                    # archive is never resident in memory at once.
                    dst.writestr(info, src.read(info))
                for q in incoming:
                    dst.write(q, arcname=q.name)

    # The archive now holds every image in *images* — the ones it already
    # carried and the ones just written — so those are the unit of deletion,
    # not the folder. Anything the image filter skipped (an OME companion XML,
    # an acquisition log, a mask subdirectory, a Finder `.DS_Store`) exists
    # nowhere else, and the folder stays behind holding exactly that remainder.
    outcome = replace_folder_with_archive(entry, images)
    if log_fn and (incoming or not outcome.folder_removed):
        tail = (
            ""
            if outcome.folder_removed
            else (f" — folder kept, {len(outcome.kept)} file(s) not "
                  f"archived: {', '.join(outcome.kept)}")
        )
        log_fn(
            f"[zipper] Folded {len(incoming)} image(s) into "
            f"{target.name}{tail}.\n"
        )
    return 1 if incoming else 0


def _member_is_superseded(info: zipfile.ZipInfo, replacement: Path | None) -> bool:
    """Whether the folder holds a *different* file under this member's name.

    Same name and same bytes means the member stands. Same name, different
    bytes means the folder wins: it is what the zipper just wrote from the
    user's current images.
    """
    if replacement is None:
        return False
    superseded = True
    with swallow(f"comparing {info.filename} against the folder's copy"):
        superseded = (replacement.stat().st_size != info.file_size
                      or _file_crc(replacement) != info.CRC)
    return superseded


def _file_crc(path: Path) -> int:
    """CRC-32 of *path*, read in chunks so a large TIF does not land in RAM."""
    import zlib

    crc = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            crc = zlib.crc32(chunk, crc)
    return crc


def _grouped_member_index(grouped: Path) -> dict[str, tuple[int, int | None]]:
    """Every image already grouped under *grouped*, as ``name -> (size, crc)``.

    A resolved input dir holds ``<WELL>/`` folders, ``<WELL>.zip`` archives, or
    — mid-resume — both. Reading only one of them is what made every loose TIF
    look unconsumed on a plate that had already been zipped.

    **Identity is the bytes, not the name.** Answering "is this loose TIF
    already grouped?" from the basename alone is a reuse predicate keyed on
    less than what invalidates the reuse: a user who re-exports a plate with a
    corrected exposure writes the same filenames, so every corrected image
    reads as already consumed. The archive keeps the old pixels, the run
    analyses them and reports success, and the loose originals are deleted as
    redundant. So the size is recorded, and for a zip member the CRC-32 the
    central directory already carries — both free — and the loose file is
    hashed only when a same-named entry has the same size, which is the one
    case a cheaper comparison cannot decide.

    A folder child's CRC is ``None`` because computing it means reading the
    file; :func:`_is_already_grouped` reads it then, and only then.
    """
    from util.image_exts import is_group_image

    index: dict[str, tuple[int, int | None]] = {}
    with swallow(f"listing already-grouped members under {grouped.name}"):
        files, subdirs = files_and_subdirs(grouped)
        # Folders and archives in one sorted sequence. On the rare basename
        # that appears in both, the *last* writer wins, so the walk order is
        # part of the answer — and this is the answer that decides whether a
        # user's loose original is deleted as already grouped. The old order
        # was `iterdir`'s, i.e. the filesystem's, so it was arbitrary rather
        # than files-then-folders; sorting makes it repeatable, and keeping the
        # two kinds interleaved keeps each collision on the side that a listing
        # of the folder would put it.
        dirs = set(subdirs)
        for entry in sorted(files + subdirs, key=lambda q: q.name):
            if entry in dirs:
                for child in files_in(entry):
                    if is_group_image(child.name):
                        with swallow(f"sizing {child.name}"):
                            index[child.name] = (child.stat().st_size, None)
                continue
            if entry.suffix.lower() != ".zip":
                continue
            with swallow(f"reading {entry.name}"), \
                    zipfile.ZipFile(entry) as zf:
                for info in zf.infolist():
                    base = info.filename.rsplit("/", 1)[-1]
                    if base and not base.startswith(".") \
                            and is_group_image(base):
                        index[base] = (info.file_size, info.CRC)
    return index


def _is_already_grouped(tif: Path, grouped: Path,
                        index: dict[str, tuple[int, int | None]]) -> bool:
    """Whether *tif*'s bytes are already inside *grouped*.

    False for a name that is not there at all, for one that is there at a
    different size, and for one that is there at the same size but different
    content — the re-exported plate. Only the last case costs a read, and it
    costs one pass over the loose file rather than a decompression of the
    archive.

    A read that fails answers False. Being unable to tell whether a copy exists
    is not evidence that one does, and this answer decides whether the user's
    original is deleted.
    """
    entry = index.get(tif.name)
    if entry is None:
        return False
    size, crc = entry
    same = False
    with swallow(f"comparing {tif.name} against its grouped copy"):
        if tif.stat().st_size == size:
            if crc is None:
                # A folder child: its CRC was not computed up front, because
                # doing so for a whole plate costs a read of every file that
                # a size mismatch would have settled for free.
                sibling = next(
                    (q for q in grouped.rglob(tif.name) if q.is_file()), None)
                same = sibling is not None and _file_crc(tif) == _file_crc(sibling)
            else:
                same = _file_crc(tif) == crc
    return same


def _wells_claimed_by(paths, *, filename_schema: str,
                      filename_sep: str) -> dict[Path, str | None]:
    """Map each loose TIF to the canonical well its own filename claims.

    One definition, shared by both zipper call sites that delete their sources
    afterwards. A name that does not decompose under the run's schema maps to
    ``None`` and is therefore never deleted: a schema mismatch has to cost the
    user a failed run, not their images.

    The schema is read through :func:`util.schema.parse_schema`, which is what
    the zipper itself uses. Splitting on ``":"`` here was a second reading of
    the same string that disagreed with it in two ways — `parse_schema` drops
    empty tokens, so a schema with a doubled colon shifted the well index apart
    between the two, and it does not lower-case, so a capitalised field name
    was a real field to one and ``ignore`` to the other. Either disagreement
    decides which of the user's files are deleted.
    """
    from util.schema import parse_schema
    from util.well_token import canonical_well_label

    fields = parse_schema(str(filename_schema or ""))
    well_idx = fields.index("well") if "well" in fields else -1
    out: dict[Path, str | None] = {}
    for p in paths:
        tok = None
        if well_idx >= 0:
            parts = p.stem.split(filename_sep)
            if well_idx < len(parts):
                tok = canonical_well_label(parts[well_idx])
        out[p] = tok
    return out


def _delete_sources_that_landed(source_tifs, grouped_dir: Path, *,
                                filename_schema: str, filename_sep: str) -> None:
    """Unlink only the loose TIFs whose well actually got grouped.

    Keyed on the well token rather than on a file appearing inside a well
    folder, because the pipeline may compress those folders into ``<WELL>.zip``
    during the run. A file whose well never appeared is kept.
    """
    grouped_wells = set(iter_well_tokens(grouped_dir))
    for tif, tok in _wells_claimed_by(
            source_tifs, filename_schema=filename_schema,
            filename_sep=filename_sep).items():
        if tok and tok in grouped_wells:
            with swallow(f"removing the consumed source {tif.name}"):
                tif.unlink()


def _group_loose_tifs_in_place(
    folder: Path,
    output_dir: Path,
    *,
    log_fn: Callable[[str], None] | None,
    progress_fn: Callable[[str], None] | None,
    filename_schema: str,
    filename_sep: str,
    proc_hook: Callable[[subprocess.Popen | None], None] | None,
    move_source_tifs: bool,
) -> tuple[Path, Path, Callable[[], None] | None]:
    """Group loose TIFs that live *directly inside* ``folder`` into per-well
    subfolders within ``folder`` itself.

    This covers the case where a user puts ungrouped TIFs inside an ``in/``
    folder (or selects an ``in/`` folder full of loose TIFs) — including a
    *partially-grouped* ``in/`` left by an interrupted prior run (some
    ``in/<well>/`` folders already exist, the rest of the wells still loose).
    Such a folder used to be mistaken for already-grouped input, so the grouper
    never ran on the leftovers. Here we run well_plate_zipper with
    ``search == output == folder`` so the loose TIFs become ``folder/<well>/``
    folders the pipeline reads in folder mode; existing well folders are left
    untouched (the walk is non-recursive) and added to where wells overlap.

    Returns ``(folder, output_dir, on_success)``.
    """
    source_tifs = tuple(tif_files_in(folder))
    if log_fn:
        log_fn(
            f"[zipper] Found {len(source_tifs)} loose TIF(s) inside "
            f"{folder.name}/ — grouping into per-well folders in place.\n"
        )
    run_zipper(
        folder,
        folder,
        log_fn=log_fn,
        progress_fn=progress_fn,
        filename_schema=filename_schema,
        filename_sep=filename_sep,
        proc_hook=proc_hook,
        move_source_tifs=move_source_tifs,
    )
    if not has_well_content(folder):
        raise ValueError(
            "The grouping step ran but produced no well folders.\n\n"
            "Common causes:\n"
            "  • The 'well' position in the Filename Schema does not match the actual well token in your filenames.\n"
            "  • The separator character does not match your filenames.\n"
            "  • The well token is not a valid 96-well plate position (A01–H12).\n\n"
            f"Schema used: {filename_schema}  |  Separator: '{filename_sep}'"
        )

    # If grouping produced folders alongside pre-existing well zips, normalise to
    # all-zips so the pipeline (which prefers zips) doesn't drop the new wells.
    _consolidate_mixed_to_zips(folder, log_fn=log_fn)

    if move_source_tifs:
        # Sources were moved into the well folders; nothing left to clean up.
        return folder, output_dir, None

    def _on_success() -> None:
        _delete_sources_that_landed(
            source_tifs, folder,
            filename_schema=filename_schema, filename_sep=filename_sep)

    return folder, output_dir, _on_success


_OME_SUFFIXES = (".ome.tif", ".ome.tiff")


def detect_ome_input(raw: Path) -> Path | None:
    """Return a packed OME-TIFF at *raw*, or ``None`` if it isn't one.

    Accepts either the ``.ome.tif`` / ``.ome.tiff`` file selected directly, or a
    directory containing exactly one such file at its top level. Analyze uses this
    to spot a raw OME-TIFF and redirect the user to the Datasets converter; the
    converter itself does the de-interleave (``datasets.ome_import``).
    """
    if raw.is_file() and raw.name.lower().endswith(_OME_SUFFIXES):
        return raw
    if raw.is_dir():
        try:
            hits = [p for p in files_in(raw)
                    if p.name.lower().endswith(_OME_SUFFIXES)]
        except OSError:
            return None
        if len(hits) == 1:
            return hits[0]
    return None


def resolve_input_output(
    raw: Path,
    *,
    log_fn: Callable[[str], None] | None = None,
    progress_fn: Callable[[str], None] | None = None,
    filename_schema: str,
    filename_sep: str,
    proc_hook: Callable[[subprocess.Popen | None], None] | None = None,
    move_source_tifs: bool = False,
) -> tuple[Path, Path, Callable[[], None] | None]:
    """Resolve the raw input folder into ``(input_dir, output_dir,
    on_success)``.

    ``on_success`` is ``None`` for inputs that needed no zipping
    (already-zipped folders, an ``in/`` subfolder with well content),
    AND for the loose-TIF case when ``move_source_tifs=True`` (the
    zipper has already removed the sources, so there's nothing for
    the post-success hook to delete).

    For the default ``move_source_tifs=False`` loose-TIF case the
    returned callback deletes ``raw/*.tif`` so a successful pipeline
    run converts the dataset from "loose TIFs at top level" to
    "zips in ``raw/in/``" (the next run picks up the zips and skips
    the zipper entirely).

    The caller MUST invoke ``on_success`` only on a clean pipeline
    completion. On any failure (zipper error, pipeline non-zero
    exit, user cancel), skip it — the loose TIFs stay intact so
    the user can retry from scratch.
    """
    # The *same* plan the readout showed, which means the same arguments. This
    # call omitted the schema while holding it in its own signature, so the run
    # recomputed a schema-less plan: the readout said "Will fold 1 loose TIF(s)"
    # and this side logged 3 — the audit's own worked example, reproduced by the
    # fix for it, with only the surface it appears on moved.
    #
    # The two can also disagree about the *action*, and that is not cosmetic:
    # an all-duplicate folder is `grouped` with the schema and `fold_loose`
    # without it, and `fold_loose` returns early while `grouped` falls through
    # to the resume tail, which consumes loose images at the dataset root and
    # returns the closure that deletes them after a clean run.
    #
    # Measured on a plate holding `in/A01.zip`, a duplicate of one of its
    # members loose inside `in/`, and two genuinely ungrouped `B02` images at
    # the root: with the schema the action is `grouped`, the B02 images are
    # consumed into the archives and their loose copies deleted only once the
    # pipeline completes cleanly. Without it the action was `fold_loose`, which
    # returns before the root is looked at — so those two images were never
    # processed at all. The change is a fix, and it is written down here
    # because the same `folded == 0` also fires when nothing parses as this
    # schema's images, i.e. when the user typed the wrong separator. Measured
    # for that case too: the zipper matches nothing and the `landed`/`claimed`
    # check in the tail refuses the run rather than deleting anything.
    plan = plan_input_layout(raw, filename_schema=filename_schema,
                             filename_sep=filename_sep)
    if not plan.runnable:
        raise ValueError(_layout_error(plan, raw))

    grouped = plan.input_dir
    out_dir = plan.output_dir
    assert grouped is not None and out_dir is not None  # guaranteed by `runnable`

    if plan.action == FOLD_LOOSE:
        # Loose TIFs dropped next to existing well zips — e.g. a new
        # timepoint/FOV added to an already-grouped dataset. Do NOT group these
        # into per-well folders: a folder for a well that already has a
        # <WELL>.zip is silently ignored by the zip-preferring pipeline (see
        # _consolidate_mixed_to_zips), so the new images would never be
        # processed. Leave them for append_loose_images_to_zips (run by
        # select_incremental_scope) to fold into the matching zips / create new
        # ones and drive FOV-level incremental reprocessing.
        if log_fn:
            log_fn(
                f"[zipper] Found {plan.loose_count} loose TIF(s) alongside "
                f"existing well zip(s) in {grouped.name}/ — folding them into "
                f"the matching zips for incremental reprocessing.\n"
            )
        _consolidate_mixed_to_zips(grouped, log_fn=log_fn)
        return grouped, out_dir, None

    if plan.action == GROUP_LOOSE:
        # A pre-existing grouped root that still holds loose, ungrouped TIFs —
        # whether a brand-new drop or the leftovers of a partial prior run (some
        # <well>/ folders already present) — is grouped in place so every well
        # gets consumed, not just the ones a prior run reached.
        return _group_loose_tifs_in_place(
            grouped,
            out_dir,
            log_fn=log_fn,
            progress_fn=progress_fn,
            filename_schema=filename_schema,
            filename_sep=filename_sep,
            proc_hook=proc_hook,
            move_source_tifs=move_source_tifs,
        )

    if plan.action == UNRECOGNISED:
        # Nothing in here parses as a well, but the pipeline is still the thing
        # that reads it — the same acceptance a folder named "in" has always
        # had. Say what was not recognised rather than failing the run with a
        # message listing a condition the user has already met.
        if log_fn:
            log_fn(
                f"[zipper] Using {grouped}/ as input — no well-named zips or "
                f"folders were recognised in it, so the pipeline will report "
                f"what it can read.\n"
            )
        _consolidate_mixed_to_zips(grouped, log_fn=log_fn)
        return grouped, out_dir, None

    if plan.action == ZIP_ROOT:
        # Snapshot the loose TIFs BEFORE the zipper runs so the
        # post-success cleanup deletes exactly the inputs the user
        # had at start-of-run — not any incidental .tif files that
        # might land in raw/ later (the zipper's own output goes to
        # raw/in/, not raw/, so this is mostly defensive).
        source_tifs = tuple(tif_files_in(raw))
        if log_fn:
            log_fn(
                f"[zipper] Found {len(source_tifs)} TIF files — grouping them into per-well folders in in/\n"
            )
        run_zipper(
            raw,
            grouped,
            log_fn=log_fn,
            progress_fn=progress_fn,
            filename_schema=filename_schema,
            filename_sep=filename_sep,
            proc_hook=proc_hook,
            move_source_tifs=move_source_tifs,
        )
        if not grouped.is_dir() or not has_well_content(grouped):
            raise ValueError(
                "The grouping step ran but produced no well folders or zip files.\n\n"
                "Common causes:\n"
                "  \u2022 The 'well' position in the Filename Schema does not match the actual well token in your filenames.\n"
                "  \u2022 The separator character does not match your filenames.\n"
                "  \u2022 The well token is not a valid 96-well plate position (A01\u2013H12).\n\n"
                f"Schema used: {filename_schema}  |  Separator: '{filename_sep}'"
            )

        if move_source_tifs:
            # The zipper already moved the source TIFs; no post-success
            # cleanup needed and nothing safe to undo on failure either
            # (the source files are gone in their old location regardless
            # of pipeline outcome).
            return grouped, out_dir, None

        def _on_success() -> None:
            """Delete loose TIFs from raw/ once the pipeline completes
            cleanly. raw/in/{well}.zip already has the data; the
            loose copies in raw/ are now redundant."""
            for tif in source_tifs:
                with swallow(f"removing {tif.name}, already archived into in/"):
                    tif.unlink()

        return grouped, out_dir, _on_success

    # GROUPED. Normalise a mixed (zips + bare folders) input dir left by a
    # hard-killed run to all-zips, so no folder-only well is dropped by the
    # zip-preferring pipeline.
    _consolidate_mixed_to_zips(grouped, log_fn=log_fn)
    if grouped == raw:
        # The user selected the grouped folder itself; there is no enclosing
        # dataset folder to hold leftover loose TIFs from a partial run.
        return grouped, out_dir, None

    # v5.x.x resume support: ``raw/in/`` already has well content,
    # but a prior ``--move-source-tifs`` run may have crashed
    # mid-way and left some TIFs at ``raw/`` (only some were
    # moved). Detect that state and re-run the zipper to consume
    # ONLY the truly-unconsumed ones — for copy-mode reruns where
    # every loose TIF already has a same-named copy under
    # ``raw/in/<well>/``, the zipper is a no-op and skipping it
    # avoids re-copying the same files (which would show up as
    # noisy "zipping" log lines against files already in place
    # from a prior successful copy).
    loose_at_top = tif_files_in(raw)
    if loose_at_top:
        # Both already-grouped layouts, not just one. Scanning directories
        # alone means that in the zip-grouped layout — which is what a plate
        # looks like after any completed run — this set is always empty, so
        # every loose TIF reads as unconsumed and the zipper is re-run against
        # files that are already inside the archives.
        existing = _grouped_member_index(grouped)
        leftover_tifs = [
            p for p in loose_at_top
            if not _is_already_grouped(p, grouped, existing)
        ]
    else:
        leftover_tifs = []

    if leftover_tifs:
        if log_fn:
            log_fn(
                f"[zipper] Resume: found {len(leftover_tifs)} unconsumed "
                f"loose TIF(s) at top level after partial run; consuming "
                f"into existing raw/in/ well folders.\n"
            )
        source_tifs = tuple(leftover_tifs)
        run_zipper(
            raw,
            grouped,
            log_fn=log_fn,
            progress_fn=progress_fn,
            filename_schema=filename_schema,
            filename_sep=filename_sep,
            proc_hook=proc_hook,
            move_source_tifs=move_source_tifs,
        )
        # The two checks the other two zipper call sites make and this one did
        # not. `has_well_content` cannot serve here — the resume layout already
        # has well content, so it is True whatever the zipper did — so the
        # question asked instead is whether any of *these* files' wells landed.
        # Without it, a wrong separator or schema meant the zipper matched
        # nothing, nothing refused the run, and `_on_success_resume` then
        # deleted every source file: the images existed nowhere afterwards.
        landed = set(iter_well_tokens(grouped))
        claimed = _wells_claimed_by(
            source_tifs, filename_schema=filename_schema,
            filename_sep=filename_sep)
        if not any(tok in landed for tok in claimed.values() if tok):
            raise ValueError(
                "The grouping step ran but none of the loose images at the "
                "dataset root were grouped into a well.\n\n"
                "Common causes:\n"
                "  \u2022 The 'well' position in the Filename Schema does not match "
                "the actual well token in your filenames.\n"
                "  \u2022 The separator character does not match your filenames.\n"
                "  \u2022 The well token is not a valid 96-well plate position "
                "(A01\u2013H12).\n\n"
                f"Schema used: {filename_schema}  |  Separator: '{filename_sep}'"
            )

        # Grouping beside pre-existing zips leaves `<WELL>/` folders next to
        # `<WELL>.zip`, and the pipeline reads folders only when there are no
        # zips at all — so without both of these the new images are never
        # processed. The two are complements: fold merges a folder into the
        # archive its well already has, consolidate packs one that has none.
        _fold_well_folders_into_existing_zips(grouped, log_fn=log_fn)
        _consolidate_mixed_to_zips(grouped, log_fn=log_fn)

        if move_source_tifs:
            return grouped, out_dir, None

        def _on_success_resume() -> None:
            _delete_sources_that_landed(
                source_tifs, grouped,
                filename_schema=filename_schema, filename_sep=filename_sep)

        return grouped, out_dir, _on_success_resume

    # All loose TIFs at top level (if any) are already represented
    # under raw/in/. If the prior run was copy-mode they're
    # redundant and the on_success cleanup will tidy them up;
    # otherwise (move-mode prior run, or no loose TIFs at all) the
    # state is already fully consumed and there's nothing to do.
    if loose_at_top:
        if log_fn:
            log_fn(
                f"[zipper] Resume: all {len(loose_at_top)} loose TIF(s) at "
                f"top level already have a copy under raw/in/. Skipping "
                f"zipper re-run; will clean up the loose copies on "
                f"successful pipeline completion.\n"
            )
        already_copied = tuple(loose_at_top)

        def _on_success_post_copy() -> None:
            _delete_sources_that_landed(
                already_copied, grouped,
                filename_schema=filename_schema, filename_sep=filename_sep)

        return grouped, out_dir, _on_success_post_copy
    return grouped, out_dir, None
