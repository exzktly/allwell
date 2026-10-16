"""Which of the three input layouts a plate is in, and what that costs.

A run's input directory arrives in one of three shapes — per-well ``<WELL>.zip``
archives, per-well ``<WELL>/`` folders, or loose images at the top level. These
three functions decide which, and they moved out of ``process_microscopy``
together because they are one cluster with one subject, and because that module
carries a line budget whose point is that a cohesive cluster leaves rather than
the file growing again.

The rule they share, and the reason they belong together: **the layouts are not
exclusive, and whatever picks one has to say what it is doing with the others.**
A plate can hold zips and folders at once — a resumed run leaves the folder
beside the archive it produced — and it can hold loose images that
``process_microscopy.organize_loose_tifs_into_well_folders`` has just moved into
folders. Folder mode is used only when there are no zips at all, so on a mixed
plate the folders are ignored. That is right for the redundant twin and it is
data loss for images the user has just added, and
:func:`refuse_images_only_in_ignored_folders` is what separates the two.

Every file question here goes through ``util.image_exts.is_run_input_member``
rather than a suffix test of its own: a plate carries AppleDouble sidecars,
``__MACOSX/`` members and dotfiles that have an image suffix and are not the
user's data, and these functions count and refuse on the answer.

``organize_loose_tifs_into_well_folders`` stays in ``process_microscopy``: it
needs ``parse_filename``, and importing that here would close a cycle.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from util.aw_safe import swallow
from util.dir_scan import files_in, subdirs_in
from util.image_exts import is_run_input_member
from util.well_token import canonical_well_label as _canonical_well_label, is_well_archive

log = logging.getLogger(__name__)

__all__ = [
    "find_well_folders",
    "find_well_zips",
    "refuse_images_only_in_ignored_folders",
]


def find_well_zips(input_dir: Path) -> list[tuple[str, Path]]:
    """
    Scan *input_dir* (non-recursively) for .zip files whose stem matches a
    96-well plate position.

    Returns a list of (well_label, zip_path) sorted by well_label.
    Zips with non-plate names are logged as warnings and skipped.
    """
    found: list[tuple[str, Path]] = []
    skipped: list[Path] = []

    # An archive is a file: a *directory* named ``A01.zip`` matched the glob this
    # replaced, and was then handed on as a well whose every image group failed
    # to open. The Analyze planner's ``has_well_zip`` already says such a thing is
    # not a well archive, and the two have to agree — the planner is what decides
    # whether a run may start at all. One enumeration, no stat per entry.
    for p in files_in(input_dir):
        if p.suffix.lower() != ".zip":
            continue
        if p.name.startswith("."):
            log.debug("Skipping hidden file: %s", p.name)
            continue
        # The shared rule (util.well_token.is_well_archive), so the run and every check of its input agree.
        # Canonicalises to upper-case, zero-padded column (a1 → A01).
        if is_well_archive(p):
            found.append((_canonical_well_label(p.stem), p))
        else:
            skipped.append(p)

    if skipped:
        log.warning(
            "%d zip file(s) skipped (name not a valid 96-well position):",
            len(skipped),
        )
        for p in skipped:
            log.warning("  %s", p.name)

    log.info("Found %d well zip file(s) in %s", len(found), input_dir)
    return found


def find_well_folders(input_dir: Path) -> list[tuple[str, Path]]:
    """
    Scan *input_dir* (non-recursively) for subdirectories whose name matches
    a 96-well plate position (e.g. A01/, B12/).

    Returns a list of (well_label, folder_path) sorted by well_label.
    """
    found: list[tuple[str, Path]] = []
    try:
        # From the listing's entry type. Asked per entry, this cost one stat per
        # *loose image* on a FLAT plate — the layout it is called for — which on
        # Windows is a file handle opened and closed over the wire apiece.
        entries = subdirs_in(input_dir)
    except OSError:
        return found
    for p in entries:
        if p.name.startswith(".") or p.name.startswith("_"):
            continue
        well = _canonical_well_label(p.name)
        if well is not None:
            found.append((well, p))
    log.info("Found %d well folder(s) in %s", len(found), input_dir)
    return sorted(found, key=lambda x: x[0])


def refuse_images_only_in_ignored_folders(input_dir: Path, well_zips) -> None:
    """Stop rather than silently analyse a fraction of the plate.

    `well_folders` is read only when there are no zips at all, so a plate that
    holds both processes the zips and ignores every folder. That is quiet in
    the ordinary resumed case — the folder is the redundant twin of a finished
    archive — and it is data loss in the case just above it:
    `organize_loose_tifs_into_well_folders` has already *moved* the user's
    loose images into `in/<WELL>/`, and if any zip exists those images are now
    somewhere the run will never look. The run then reports success having
    analysed everything except what the user just added.

    Refused only when a folder holds an input image the sibling archive does
    not already carry, so a resumed plate whose folders are genuinely redundant
    still runs. Named by well and by file, because the remedy — fold them in,
    or move the archives aside — depends on which files they are.
    """
    zip_by_well = {str(w): Path(p) for w, p in dict(well_zips or {}).items()} \
        if isinstance(well_zips, dict) else {str(w): Path(p) for w, p in well_zips}
    stranded: dict[str, list[str]] = {}
    for well, folder in find_well_folders(input_dir):
        images = sorted(
            q.name for q in folder.iterdir()
            if q.is_file() and is_run_input_member(q.name)
        )
        if not images:
            continue
        archive = zip_by_well.get(str(well))
        if archive is None or not archive.exists():
            # **The only case worth refusing.** This well has no archive, so
            # folder mode would have been its only route into the run — and
            # folder mode is off because some *other* well has a zip. Its
            # images are invisible to the run, and `organize_loose_tifs_into_
            # well_folders` may already have moved them here from the plate
            # root, so nothing else holds them.
            stranded[str(well)] = images
            continue
        # From here the well *will* be processed, from its archive. A file the
        # archive does not carry is not analysed, and it is also not deleted —
        # it is still in the folder, where `replace_folder_with_archive` left
        # it precisely because the archive did not take it. That is a warning,
        # never a refusal: refusing here bricked every plate the pipeline
        # itself had produced, because its own archiver deliberately skips
        # `.jpg`, zero-byte files and `__aw_tmp_` scratch, and the remedy the
        # refusal offered ("fold them into the archive") was the thing the
        # pipeline had just declined to do.
        held: set[str] = set()
        readable = False
        with swallow(f"reading {archive.name} to compare with {folder.name}"):
            with zipfile.ZipFile(archive) as zf:
                # Case-folded: on a case-insensitive volume the archive can
                # hold `IMG.TIF` where the folder holds `img.tif`, and they are
                # the same file. Comparing them raw refused a plate for a
                # difference the filesystem does not make.
                held = {Path(n).name.lower() for n in zf.namelist()}
            readable = True
        if not readable:
            # An unreadable archive is a corrupt archive, not a stranded
            # folder. Saying "these images are not in the zip" when the zip
            # could not be opened blames the wrong artefact and offers a
            # remedy that cannot work.
            log.warning(
                "Input warning: %s could not be read, so %s's folder could "
                "not be compared against it; the run continues and folder "
                "mode is not used because a zip is present.",
                archive.name, well)
            continue
        missing = [n for n in images if n.lower() not in held]
        if missing:
            log.warning(
                "Input note: %s holds %d file(s) its archive does not carry "
                "(%s). They stay on disk and are not analysed; %s.zip is what "
                "this run reads.",
                folder.name, len(missing), ", ".join(missing[:4]), well)

    if not stranded:
        return
    detail = "; ".join(
        f"{w}: {', '.join(names[:4])}{'…' if len(names) > 4 else ''}"
        for w, names in sorted(stranded.items())
    )
    raise SystemExit(
        f"Input error: {len(stranded)} well folder(s) under {input_dir} hold "
        f"image(s) that are not in the matching <WELL>.zip, and folder mode is "
        f"not used when any zip is present — so these images would be silently "
        f"left out of the run:\n  {detail}\n\n"
        f"Fold them into the matching archive, or move the archives aside so "
        f"the run uses folder mode for the whole plate. Refusing rather than "
        f"analysing part of the plate and reporting success."
    )
