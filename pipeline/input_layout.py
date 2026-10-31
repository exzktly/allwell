"""The pipeline's one input layout — per-well ``<WELL>.zip`` archives — and the
refusal of every other.

A run reads one archive per well, named by its plate position (``A01.zip``).
Loose images and per-well folders are not input: **Datasets → Zip loose folder**
converts them, and both the Analyze pane and remote staging refuse them before
the pipeline starts. This module is the pipeline's own copy of that refusal, for
the CLI and for any caller that reaches the pipeline without either.

The rule the refusal holds: **a file the run would silently skip stops the run.**
Ignoring a layout is not the same as not supporting it — a loose image beside
the archives, or a folder for a well that has no archive, would be left out of
the results while the run reported success, and the output would be
indistinguishable from a whole plate's. So each is named, by file and by well,
with the remedy.

A per-well folder *beside its own well's archive* is the one non-zip entry that
does not stop the run. Earlier versions left such folders behind on purpose
(their archiver keeps a ``.jpg``, a zero-byte frame or scratch it declined to
pack), and refusing them would refuse every plate those versions produced. The
archive is what the run reads; a folder image the archive does not carry is
logged as not analysed.

Every file question goes through ``util.image_exts.is_run_input_member`` rather
than a suffix test of its own: a plate carries AppleDouble sidecars,
``__MACOSX/`` members and dotfiles that have an image suffix and are not the
user's data.
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
    "ZIP_LOOSE_FOLDER_REMEDY",
    "find_well_zips",
    "refuse_non_zip_input",
]

#: The remedy every refusal of non-zip input names. One sentence, shared by the
#: pipeline, the Analyze pane and remote staging, so the three cannot send the
#: user to different places.
ZIP_LOOSE_FOLDER_REMEDY = (
    "Analysis reads one <WELL>.zip archive per well. Convert loose images and "
    "per-well folders first with Datasets → Zip loose folder, then run on the "
    "folder it produces."
)


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


def _well_folders(input_dir: Path) -> list[tuple[str, Path]]:
    """The well-named subdirectories of *input_dir*, as ``(well, path)``."""
    found: list[tuple[str, Path]] = []
    try:
        entries = subdirs_in(input_dir)
    except OSError:
        return found
    for p in entries:
        if p.name.startswith(".") or p.name.startswith("_"):
            continue
        well = _canonical_well_label(p.name)
        if well is not None:
            found.append((well, p))
    return sorted(found, key=lambda x: x[0])


def _images_in(folder: Path) -> list[str]:
    """The run-input image names directly inside *folder*, sorted."""
    try:
        return sorted(p.name for p in files_in(folder) if is_run_input_member(p.name))
    except OSError:
        return []


def _note_folder_beside_archive(well: str, folder: Path, archive: Path, images: list[str]) -> None:
    """Log the images in *folder* that *archive* — what the run reads — lacks."""
    held: set[str] = set()
    readable = False
    with swallow(f"reading {archive.name} to compare with {folder.name}"):
        with zipfile.ZipFile(archive) as zf:
            # Case-folded: on a case-insensitive volume the archive can hold
            # `IMG.TIF` where the folder holds `img.tif`, and they are the same file.
            held = {Path(n).name.lower() for n in zf.namelist()}
        readable = True
    if not readable:
        # An unreadable archive is a corrupt archive, not a stranded folder;
        # the well's own task reports it when it fails to open.
        log.warning("Input warning: %s could not be read, so %s's folder could "
                    "not be compared against it.", archive.name, well)
        return
    missing = [n for n in images if n.lower() not in held]
    if missing:
        log.warning(
            "Input note: %s holds %d file(s) its archive does not carry (%s). "
            "They stay on disk and are not analysed; %s.zip is what this run reads.",
            folder.name, len(missing), ", ".join(missing[:4]), well)


def _names(items: list[str]) -> str:
    return ", ".join(items[:4]) + ("…" if len(items) > 4 else "")


def refuse_non_zip_input(input_dir: Path, well_zips) -> None:
    """Stop a run whose input holds images it would not read.

    Refuses, naming each: loose images at the top level of *input_dir*; a
    well-named folder holding images for a well with no archive; and an input
    with no well archive at all. A folder beside its own well's archive is only
    logged — see the module docstring for why.
    """
    zip_by_well = {str(w): Path(p) for w, p in well_zips}
    problems: list[str] = []
    try:
        loose = sorted(p.name for p in files_in(input_dir) if is_run_input_member(p.name))
    except OSError:
        loose = []
    if loose:
        problems.append(f"{len(loose)} loose image(s) at the top level: {_names(loose)}")
    stranded: list[str] = []
    for well, folder in _well_folders(input_dir):
        images = _images_in(folder)
        if not images:
            continue
        archive = zip_by_well.get(well)
        if archive is None:
            stranded.append(f"{folder.name}/ ({len(images)} image(s))")
        else:
            _note_folder_beside_archive(well, folder, archive, images)
    if stranded:
        problems.append(f"{len(stranded)} well folder(s) with no <WELL>.zip: {_names(stranded)}")
    if not problems and not well_zips:
        problems.append("no per-well <WELL>.zip archives")
    if not problems:
        return
    raise SystemExit(
        f"Input error: {input_dir} holds input this run would not read —\n  "
        + "\n  ".join(problems)
        + f"\n\n{ZIP_LOOSE_FOLDER_REMEDY} Refusing rather than analysing part "
        "of the plate and reporting success."
    )
