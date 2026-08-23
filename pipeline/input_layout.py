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
from util.image_exts import is_run_input_member
from util.well_token import canonical_well_label as _canonical_well_label

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

    for p in sorted(input_dir.glob("*.zip")):
        if p.name.startswith("."):
            log.debug("Skipping hidden file: %s", p.name)
            continue
        # Canonicalises to upper-case, zero-padded column (a1 → A01) and
        # returns None for anything that is not a 96-well position.
        well = _canonical_well_label(p.stem)
        if well is not None:
            found.append((well, p))
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
        entries = sorted(input_dir.iterdir())
    except OSError:
        return found
    for p in entries:
        if not p.is_dir() or p.name.startswith(".") or p.name.startswith("_"):
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
        held: set[str] = set()
        if archive is not None and archive.exists():
            with swallow(f"reading {archive.name} to compare with {folder.name}"):
                with zipfile.ZipFile(archive) as zf:
                    held = {Path(n).name for n in zf.namelist()}
        missing = [n for n in images if n not in held]
        if missing:
            stranded[str(well)] = missing

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
