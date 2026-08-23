"""
process_microscopy.py
---------------------
Production-ready pipeline to segment nuclei with StarDist and quantify
GFP intensities per nucleus from paired fluorescence microscopy images.

Input modes (per-well, grouped upstream)
----------------------------------------
--input_dir must hold input grouped by 96-well plate position, in one of
two layouts:
  • Zip mode (preferred): per-well archives A01.zip … H12.zip.
  • Folder mode: per-well subfolders A01/ … H12/ (compressed to
    <well>.zip during the run).

For each well the script will:
  1. Validate the name against the 96-well plate pattern (A-H, 01-12).
  2. Extract / read its images into a temporary staging area.
  3. Run the full segmentation + quantification pipeline,
     writing QC images to a per-well staging area.
  4. Compress all output images (masks + overlays) into
         <output_dir>/<well>_out.zip
  5. Delete the temporary directories (extracted input + staged images).
  6. Per-well CSVs are written to <output_dir> as usual (not zipped).

Loose TIFs dropped directly in an input directory named "in" are grouped
into per-well folders first (see organize_loose_tifs_into_well_folders).
The GUI Analyze tab and the remote runner always group input into one of
the above layouts before invoking this script.

Usage
-----
python process_microscopy.py \\
    --input_dir  /data/raw \\        # may contain A01.zip … H12.zip
    --output_dir /data/results \\
    --nuclear_token NIR \\
    --gfp_token GFP \\
    --tophat_radius_nir 100 \\
    --tophat_radius_gfp 100 \\
    --no_tophat_nir \\
    --no_tophat_gfp \\
    --workers 4 \\
    --csv_prefix measurements

Filename convention (underscore-separated):
    <ExperimentName>_<Well>_<FOV>_<Timepoint>_NIR.tif
    e.g.  Exp01_B03_F001_T0001_NIR.tif
"""

from __future__ import annotations

import argparse
import importlib.util
import contextlib
import json
import logging
import os
import shutil
import time
import zipfile
from util.json_write import write_json_atomic
from collections import defaultdict
from pathlib import Path
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed

import tifffile                                      # noqa: F401  (side-effects)
from tifffile import imread
import numpy as np
from scipy import ndimage as ndi
from skimage.filters import threshold_otsu
from skimage.segmentation import watershed
from util.channel_roles import phase_role_conflicts, quantified_channel_tokens
from util.fov import sort_key as _fov_sort_key
from util.image_exts import GROUP_IMAGE_EXTS
from util.timepoint import parse_hours as _tp_parse_hours, sort_key as _tp_sort_key
from util.well_token import (
    split_background_wells as _split_background_wells,
    background_well_key as _bg_well_key,
    canonical_well_label as _canonical_well_label,
)
from util.schema import (  # noqa: F401  (re-exported for call sites and tests)
    SCHEMA_FIELDS,
    DEFAULT_SCHEMA,
    DEFAULT_SEP,
    parse_schema,
    validate_schema,
)
from util.zip_write import atomic_zip, replace_folder_with_archive, staging_path

# StarDist and csbdeep (which import TensorFlow) are intentionally NOT imported
# here at module level.  They are imported inside _worker_init, after the
# TF_NUM_INTRAOP/INTEROP_THREADS env vars have been set, so TF picks up the
# thread limits before it initialises its internal thread pools.

# ---------------------------------------------------------------------------
# Per-worker StarDist model (loaded once at worker startup, not per image)
# ---------------------------------------------------------------------------

# The Cellpose model cache + segmenters live in Qt-free ``pipeline.cellpose``
# (Torch is imported lazily there). Their re-exports resolve via a PEP 562 module
# ``__getattr__`` — a DEFERRED import — so ``pipeline/cellpose.py`` can be
# physically absent in a StarDist-only build (Cellpose profiled out) without
# breaking this module's import; ``pm._segment_cellpose`` etc. still resolve on
# first access.
_CELLPOSE_REEXPORTS = ("_CELLPOSE_MODELS", "_cellpose_model", "_segment_cellpose")


def __getattr__(name: str):  # PEP 562: only fires for names not in globals()
    if name in _CELLPOSE_REEXPORTS:
        import pipeline.cellpose as _cp
        return getattr(_cp, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
# The StarDist segmenters + their per-worker model/FOV state live in the Qt-free
# ``pipeline.stardist`` module (TensorFlow is imported lazily there; the model
# is injected by ``_worker_init`` via ``set_model``). Re-exported so the
# segmentation dispatch resolves them under this module too.
import pipeline.stardist as _stardist
import pipeline.suspend as _suspend
from pipeline.stardist import (
    _segment_stardist_nuclei,
    _segment_stardist_seeded_watershed_cell,
)

# Segmentation-method groups. StarDist methods need the TF model loaded in
# _worker_init. Cellpose is a *general* segmenter (any channel); the
# 'cellpose_labelfree' preset is the only label-free method (it remaps the
# nuclear slot onto the phase channel in main()). classical_threshold is also a
# general routine (Otsu + watershed on the chosen channel).
_STARDIST_METHODS = ("stardist_nuclei", "stardist_seeded_watershed_cell")
_CELLPOSE_METHODS = ("cellpose", "cellpose_labelfree")
_LABELFREE_METHODS = ("cellpose_labelfree",)


# ---------------------------------------------------------------------------
# Robust file I/O helpers
#
# Every image that the pipeline writes is later read back by the same worker
# (zip-up, atomic-rename of the staging dir, or downstream tools). Two
# observed failure modes when many tifs are written in quick succession on
# slower / network-backed filesystems:
#
#   1. ``tifffile.imwrite(path)`` returns before the OS has flushed the
#      page cache, so a follow-up ``zipfile.write(path)`` can read a stale
#      / zero-length file or a half-written file and produce a corrupt
#      archive — sometimes manifesting later as ``OSError: Bad file
#      descriptor`` deep in libtiff/libpng C code.
#
#   2. A crash mid-write leaves a partial file on disk; the next run picks
#      it up and fails with cryptic decode errors.
#
# ``_safe_imwrite`` writes to ``__aw_tmp_<stem>.pid<N><ext>``, fsyncs the
# bytes, and then renames atomically. Any caller that subsequently sees
# ``path`` reads either a complete file or no file at all. Likewise
# ``_safe_atomic_write`` wraps the per-well CSV write with the same
# guarantee.
# ---------------------------------------------------------------------------


# Atomic output writers (images / CSVs / overlay) moved to the Qt-free
# ``pipeline.io`` module. Re-exported so the many call sites here (and the
# remote worker) are unchanged. See that module for the implementations + the
# tmp -> fsync -> rename rationale.
from pipeline.capacity import ALLOW_LOW_DISK_HELP
from pipeline.io import (  # noqa: F401
    _AtomicTextHandle, _fsync_path,
    _commit_staging_dir,
    _safe_atomic_text_write,
    _safe_imwrite,
    _safe_well,
    _sweep_stale_scratch_dirs,
    _sweep_stale_scratch_dirs_all,
    _verify_files_complete,
    _write_single_csv,
    incremental_done_fov_keys,
    OutputCapacityMonitor,
    preflight_scratch_for_run,
    merge_images_into_zip,
    merge_records_into_csv,
    remove_directory,
    retry_write,
    save_overlay,
    scratch_base_for,
    write_csvs_per_well,
)
from pipeline.well_skip import (
    _canonical_force_wells,
    incremental_for_well as _incremental_for_well,
    output_exists_for_well,
    output_exists_for_well_folder,
)
from pipeline.run_report import (
    clear_run_report,
    finalize_run_report,
    record_well_completeness,
)
from pipeline.completeness import record_run_check
from pipeline.sidecar import USER_SIDE_BLOCKS


def _report_worker_failure(well_label: str, output_dir: Path | None, exc: BaseException) -> None:
    """Log a worker failure with classification + a pointer to the trace file.

    Generic ``OSError: [Errno 9] Bad file descriptor`` and ``BrokenProcessPool``
    re-raised at the parent's ``future.result()`` call site otherwise look
    identical, even though they mean very different things (an internal error
    vs. the worker process dying outside Python). Spell out the difference and
    point the user at the per-well trace file written by the worker.
    """
    err_dir = (output_dir / "errors") if output_dir is not None else None
    err_hint = f"  (see worker traces in {err_dir})" if err_dir is not None else ""
    cls = type(exc).__name__
    if cls == "BrokenProcessPool":
        log.error(
            "FAILED well %s: worker process died unexpectedly (%s: %s).%s "
            "This typically means the worker was killed by the OS — most "
            "commonly out-of-memory, signal, or a native crash inside a "
            "C extension (StarDist / TF / tifffile). Reduce --workers or "
            "the per-well memory footprint and try again.",
            well_label, cls, exc, err_hint,
        )
        return
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 9:
        log.error(
            "FAILED well %s: %s — Bad file descriptor (errno 9).%s "
            "If the trace file is empty the worker's stderr was likely "
            "broken before the error site; the per-well trace file is the "
            "authoritative source.",
            well_label, cls, err_hint, exc_info=True,
        )
        return
    log.error("FAILED well %s: %s.%s", well_label, exc, err_hint, exc_info=True)


def _dump_well_failure_trace(output_dir: Path, well_label: str, exc: BaseException) -> Path | None:
    """Persist a worker-side exception trace for *well_label*.

    ProcessPoolExecutor pickles the exception back to the parent, but if the
    worker's stderr is muted (e.g. broken pipe to the GUI log queue) the
    underlying traceback never reaches the user — they only see a generic
    ``OSError: [Errno 9] Bad file descriptor`` re-raised at the parent's
    ``future.result()`` call site. Writing the full ``traceback.format_exc()``
    to ``<output_dir>/errors/<well>_pid<N>.txt`` guarantees a readable trace
    even when the live log path is broken.

    Returns the trace-file path on success, or None if the trace could not be
    written (e.g. read-only output dir).
    """
    import traceback as _tb
    try:
        err_dir = output_dir / "errors"
        err_dir.mkdir(parents=True, exist_ok=True)
        err_path = err_dir / f"{well_label}_pid{os.getpid()}.txt"
        with err_path.open("w") as fh:
            fh.write(f"Well: {well_label}\n")
            fh.write(f"PID:  {os.getpid()}\n")
            fh.write(f"Exception type: {type(exc).__name__}\n")
            fh.write("\n")
            fh.write(_tb.format_exc())
        return err_path
    except Exception:
        return None


def _dump_fov_failure_trace(
    output_dir: Path, well_label: str, fov_stem: str, exc: BaseException,
) -> Path | None:
    """Persist a worker-side exception trace for a single FOV-group.

    The per-well analogue (:func:`_dump_well_failure_trace`) only fires when a
    *whole* well fails. An individual FOV-group failure is caught inside the
    worker (the well keeps going and writes a partial CSV/zip), logged with
    ``exc_info=True`` — but never persisted. If the live log stream is muted or
    the line scrolls past, the cause is lost and the well silently drops a
    timepoint. Writing the full ``traceback.format_exc()`` to
    ``<output_dir>/errors/<well>_<fov>_pid<N>.txt`` guarantees a durable,
    readable trace for exactly the "why did this timepoint fail?" case.

    *output_dir* must be the persistent dataset output dir (NOT the per-well
    ``tmp_images`` staging dir, which is deleted on cleanup).

    Returns the trace-file path on success, or None if it could not be written.
    """
    import traceback as _tb
    try:
        err_dir = output_dir / "errors"
        err_dir.mkdir(parents=True, exist_ok=True)
        safe_fov = _safe_well(str(fov_stem))
        err_path = err_dir / f"{_safe_well(well_label)}_{safe_fov}_pid{os.getpid()}.txt"
        with err_path.open("w") as fh:
            fh.write(f"Well:      {well_label}\n")
            fh.write(f"FOV-group: {fov_stem}\n")
            fh.write(f"PID:       {os.getpid()}\n")
            fh.write(f"Exception type: {type(exc).__name__}\n")
            fh.write("\n")
            fh.write(_tb.format_exc())
        return err_path
    except Exception:
        return None


def _ensure_stardist_runtime_deps() -> None:
    """
    Fail fast with a clear message when StarDist runtime deps are missing.

    Without this preflight check, missing packages can cause worker startup
    failures that surface only as a generic BrokenProcessPool-style error.
    """
    missing: list[str] = []
    if importlib.util.find_spec("csbdeep") is None:
        missing.append("csbdeep")
    if importlib.util.find_spec("stardist") is None:
        missing.append("stardist")

    if missing:
        deps = ", ".join(missing)
        raise RuntimeError(
            "Missing required StarDist dependency package(s): "
            f"{deps}. Install them in this environment before running Analyze."
        )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _ensure_pipeline_io_ready() -> None:
    """Re-bind ``sys.stdout`` / ``sys.stderr`` when the frozen-launcher's
    windowed PyInstaller bootloader leaves one or both set to ``None``.

    The bundled launcher is invoked from the GUI as
    ``subprocess.Popen([sys.executable, "--run-pipeline"],
    stdout=PIPE, stderr=STDOUT, ...)`` (see
    ``services/pipeline_service.spawn_pipeline``). The parent attaches a
    pipe to the child's fd 1, and the child's fd 2 is duped to fd 1 by
    ``stderr=STDOUT``. In source mode both file descriptors carry a
    valid Python text wrapper, so ``logging.basicConfig`` below attaches
    its ``StreamHandler`` to a writable ``sys.stderr`` and every
    ``log.info(...)`` reaches the GUI's progress queue.

    In a frozen windowed (``console=False``) build the PyInstaller
    bootloader still detaches Python from the absent terminal — it
    leaves ``sys.stdout = None`` / ``sys.stderr = None`` even though
    the underlying file descriptors are perfectly fine. The
    ``StreamHandler`` then either binds to ``None`` (every emit raises
    ``AttributeError`` and the message is silently dropped) or stays
    bound to a broken stream. Symptom: the GUI progress bar freezes
    immediately after the pipeline launches and only "thaws" once the
    job finishes by other means.

    This shim restores a working text wrapper around fd 1 / fd 2 when
    needed, so the module-level ``logging.basicConfig`` below picks up
    a valid sink and every subsequent ``log.<level>(...)`` reaches the
    parent's pipe. The mechanism is shared with the ``WellPlateZipper``
    grouping subprocess (which has the identical frozen-windowed stdio
    problem) via :func:`util.frozen_io.ensure_std_streams`.
    """
    from util.frozen_io import ensure_std_streams
    ensure_std_streams()


_ensure_pipeline_io_ready()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 96-well plate helpers
# ---------------------------------------------------------------------------
#
# The 96-well parser (A1/A01 – H12, rows A-H, columns 1-12) is
# ``util.well_token.canonical_well_label``, imported above as
# ``_canonical_well_label``. The pipeline used to carry its own regex plus an
# is-valid wrapper, validate a name with one and then re-match it with the
# other to build the canonical label. The two agreed on every token except one:
# the canonicaliser strips surrounding whitespace and the pipeline's ``^…$``
# match did not, so a directory or zip named ``"A1 "`` was a well to the
# Analyze-side input resolver and not a well to the pipeline that then
# processed the same folder. One parser, one answer, one parse per name.


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


def organize_loose_tifs_into_well_folders(
    input_dir: Path,
    *,
    schema: list[str],
    sep: str,
) -> None:
    """
    Move loose top-level TIF/TIFF images in *input_dir* into per-well folders.

    If destination files already exist, files are skipped (no overwrite).
    """
    loose_images = [
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".tif", ".tiff"}
    ]
    if not loose_images:
        return

    files_by_well: dict[str, list[Path]] = defaultdict(list)
    unparsable = 0
    for img in loose_images:
        try:
            parsed = parse_filename(img, schema=schema, sep=sep)
        except Exception:
            unparsable += 1
            continue
        well = _canonical_well_label(parsed.get("well", ""))
        if well is None:
            unparsable += 1
            continue
        files_by_well[well].append(img)

    if not files_by_well:
        if unparsable:
            log.info(
                "Found %d loose TIF/TIFF file(s) in %s, but none had a valid 96-well token.",
                len(loose_images),
                input_dir,
            )
        return

    moved = 0
    skipped_existing = 0
    for well, files in sorted(files_by_well.items()):
        well_dir = input_dir / well
        well_dir.mkdir(parents=True, exist_ok=True)
        for src in files:
            dst = well_dir / src.name
            if dst.exists():
                skipped_existing += 1
                log.warning(
                    "Loose image not moved (destination exists, no overwrite): %s",
                    dst,
                )
                continue
            shutil.move(str(src), str(dst))
            moved += 1

    log.info(
        "Organized loose images in %s: moved %d file(s) into %d well folder(s), "
        "skipped %d existing destination(s), ignored %d unparsable file(s).",
        input_dir,
        moved,
        len(files_by_well),
        skipped_existing,
        unparsable,
    )

# ---------------------------------------------------------------------------
# Zip extraction / compression helpers
# ---------------------------------------------------------------------------

def extract_zip(
    zip_path: Path,
    dest_dir: Path,
    members_to_extract: set[str] | None = None,
) -> int:
    """
    Extract image members of *zip_path* flat into *dest_dir*.

    When *members_to_extract* is provided, only those base filenames are
    extracted. This allows zip-mode processing to pull only the channel files
    required by discovered image groups instead of unpacking every image.

    CSV files and other non-image files inside the zip are skipped so
    that outputs from a previous run packaged back into the input zip
    cannot interfere with the current run or end up re-zipped in output.

    Returns the number of image files extracted.
    """
    image_exts = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
    wanted = set(members_to_extract or [])
    dest_dir.mkdir(parents=True, exist_ok=True)
    skipped: list[str] = []
    extracted = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            # Strip any directory structure inside the zip; extract flat.
            member_name = Path(member.filename).name
            if not member_name:          # skip directory entries
                continue
            if member_name.startswith("."):
                skipped.append(member_name)
                continue
            if Path(member_name).suffix.lower() not in image_exts:
                skipped.append(member_name)
                continue
            if wanted and member_name not in wanted:
                continue
            target = dest_dir / member_name
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted += 1
    if skipped:
        log.debug(
            "Skipped %d non-image file(s) in %s: %s",
            len(skipped), zip_path.name, ", ".join(skipped),
        )
    if wanted:
        log.info("Extracted %d selected image(s) from %s -> %s", extracted, zip_path.name, dest_dir)
    else:
        log.info("Extracted %s  ->  %s", zip_path.name, dest_dir)
    return extracted


def compress_images_to_zip(image_dir: Path, out_zip: Path) -> int:
    """
    Compress all image files (TIF/TIFF/PNG) found in *image_dir* into
    *out_zip*.  Returns the number of files added.
    CSV files and any other non-image files are explicitly excluded.

    Stray ``.tmp`` files left by a crashed atomic-write are skipped, and any
    zero-byte file is logged + dropped so a partial / never-flushed write
    can't be packaged into a corrupt archive.
    """
    return len(_compress_images_to_zip(image_dir, out_zip))


def _compress_images_to_zip(image_dir: Path, out_zip: Path) -> list[Path]:
    """The body of :func:`compress_images_to_zip`, returning the files packed.

    A caller that then removes *image_dir* needs to know *which* files the
    archive took, not how many: the extension filter, the ``.tmp`` skip and
    :func:`_verify_files_complete` between them can all leave a file behind,
    and deleting the directory takes those with it. The public function keeps
    its count return for the call sites that only report a number.
    """
    include_exts = {".tif", ".tiff", ".png"}
    candidate_files = sorted(
        p for p in image_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in include_exts
        and not p.name.endswith(".tmp")
        # Stale _safe_imwrite / _AtomicTextHandle scratch files. The
        # `__aw_tmp_` prefix is unique enough that user filenames
        # don't false-positive (the old `.pid` substring would drop
        # e.g. `exp.pidgin.A01.tif`).
        and not p.name.startswith("__aw_tmp_")
    )
    image_files = _verify_files_complete(candidate_files)

    if not image_files:
        log.warning("No image files found in %s to compress.", image_dir)
        return []

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    # Build the zip via the same atomic-rename pattern the imwrite helper
    # uses: stage at __aw_tmp_<out_zip>.pid<N>, fsync, verify, replace. Any
    # caller that sees *out_zip* gets either the complete archive or the
    # previous one — see util.zip_write.
    tmp_zip = staging_path(out_zip)

    def _cleanup_tmp() -> None:
        try:
            if tmp_zip.exists():
                tmp_zip.unlink()
        except OSError:
            pass

    def _build() -> None:
        try:
            with atomic_zip(out_zip) as zf:
                for img_path in image_files:
                    zf.write(img_path, arcname=img_path.name)
        except BaseException:
            _cleanup_tmp()
            raise

    # The out.zip is one of only two writes that must land on the output volume
    # (the other is the per-well CSV), so it carries the same retry shield the
    # image writer has always had — a transient fault here used to fail the
    # whole well with a bare errno. Safe to re-run: staged to __aw_tmp_ and
    # committed with os.replace.
    retry_write(_build, what=out_zip.name, cleanup=_cleanup_tmp, path=out_zip)

    log.info(
        "Compressed %d image(s)  ->  %s", len(image_files), out_zip.name
    )
    return image_files


def compress_folder_images_to_zip_and_remove(folder: Path, out_zip: Path) -> int:
    """
    Compress image files in *folder* to *out_zip*, then delete the files the
    archive took.

    Returns the number of image files added to the archive. If no image files
    are found, the source folder is left untouched.

    *folder* itself is removed only when the archive covers everything in it.
    The archive holds TIF/TIFF/PNG, so a folder carrying a companion XML, an
    acquisition log or a mask subdirectory keeps those and survives — removing
    it as a unit destroyed them with no copy anywhere. The same rule holds for
    the files :func:`_verify_files_complete` drops: a zero-byte image is
    reported and excluded, and it must not then be deleted along with the
    directory it sits in.
    """
    packed = _compress_images_to_zip(folder, out_zip)
    if not packed:
        return 0
    outcome = replace_folder_with_archive(folder, packed, remove_dir=remove_directory)
    if not outcome.folder_removed:
        log.info(
            "Kept %s: %d file(s) the archive does not carry (%s)",
            folder.name, len(outcome.kept), ", ".join(outcome.kept),
        )
    return len(packed)


# ---------------------------------------------------------------------------
# Filename schema helpers
# ---------------------------------------------------------------------------

# The schema vocabulary and its parser live in ``util.schema`` — the single
# source of truth shared with WellPlateZipper, which reads the same filenames in
# its grouping pre-pass. Re-exported here because the pipeline's own call sites
# and the tests import them from this module.
DEFAULT_SEGMENTATION_METHOD = "stardist_nuclei"


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

def _parse_timepoint_hours(tp: str) -> float:
    """Convert a timepoint string to fractional hours; ``nan`` when unparseable.

    A ``nan``-returning adapter over :func:`util.timepoint.parse_hours`, which
    is the shared implementation and returns ``None``. The ``nan`` sentinel is
    kept because this module's callers are written against it — notably the
    ``h != h`` tests that decide raw-passthrough and sort-last — and because a
    float return keeps ``timepoint_hours`` a float column with no ``None`` to
    special-case on the way into the CSV.

    One consequence worth knowing: the literal token ``"nan"`` parses to
    ``nan`` too, so this return value cannot distinguish "the token said nan"
    from "I could not read the token". Both current callers treat them the
    same, but a new caller that must tell them apart should call
    ``util.timepoint.parse_hours`` and test for ``None``.
    """
    hours = _tp_parse_hours(tp)
    return float("nan") if hours is None else hours


_SHORT_FILENAME_WARNED: set = set()


def _warn_short_filename(stem: str, n_parts: int, n_slots: int, sep: str) -> None:
    """Warn once per filename shape that the schema's FOV slot went unfilled.

    Deduped on the shape (token count), not the filename, so a plate of 600
    files produces one line per naming convention rather than 600.
    """
    key = (n_parts, n_slots, sep)
    if key in _SHORT_FILENAME_WARNED:
        return
    _SHORT_FILENAME_WARNED.add(key)
    log.warning(
        "Filename %r splits into %d %r-separated token(s) but the schema has %d "
        "field(s) — the 'fov' slot is empty, so every row of these wells will be "
        "written with no field-of-view label and their fields will pool into one "
        "downstream. Check --filename_schema / --sep against the actual filenames.",
        stem, n_parts, sep, n_slots,
    )


def parse_filename(
    path: Path | str,
    schema: list[str] | None = None,
    sep: str = DEFAULT_SEP,
) -> dict:
    """
    Decompose a filename into metadata fields using a user-supplied schema.

    Parameters
    ----------
    path   : Path or str — file path or bare stem.
    schema : ordered list of field names from SCHEMA_FIELDS.
             Defaults to the legacy 5-slot convention
             ["experiment", "channel", "well", "fov", "timepoint"].
    sep    : separator character used to split the filename stem.
             Defaults to "_".

    The stem is split on *sep* and each token is assigned to the
    corresponding schema field.  Slots labelled "ignore" are consumed but
    not stored.  If the stem has fewer tokens than schema slots the missing
    fields default to "" (or NaN for timepoint_hours).

    Returns a dict with keys:
        filename, experiment, channel, well, fov,
        timepoint, timepoint_hours
    (always present; empty / NaN when absent from the schema or filename).
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)

    p    = Path(path) if not isinstance(path, Path) else path
    stem = p.stem if p.suffix else str(path)
    parts = stem.split(sep)

    # Build result with all known non-ignore fields defaulted to "".
    result: dict = {
        "filename":        p.name if p.suffix else str(path),
        "experiment":      "",
        "channel":         "",
        "well":            "",
        "fov":             "",
        "timepoint":       "",
        "timepoint_hours": float("nan"),
    }

    # A filename with fewer tokens than the schema has slots leaves the trailing
    # fields empty. That is legitimate for ``timepoint`` (handled below: a
    # single-timepoint dataset often carries no timepoint token) but never for
    # ``fov``: an empty FOV token is written to every row of the well, the
    # viewer maps it onto field "1", and the well's fields silently pool into
    # one — which makes its per-field statistics wrong rather than missing. Say
    # so once per (schema, token-count) shape so a mis-specified schema is
    # visible in the run log instead of surfacing later as a plot with no error
    # bars. Not fatal: the rest of the row is still good data.
    if len(parts) < len(schema) and "fov" in schema:
        fov_slot = schema.index("fov")
        if fov_slot >= len(parts):
            _warn_short_filename(stem, len(parts), len(schema), sep)

    for i, field in enumerate(schema):
        if field == "ignore":
            continue
        token = parts[i] if i < len(parts) else ""
        if field == "timepoint":
            result["timepoint"]       = token
            # Empty timepoint slot — common when the schema includes
            # "timepoint" but the dataset is single-timepoint and the
            # filenames don't carry a timepoint token. NaN would
            # propagate into the CSV's timepoint_hours column and
            # break every downstream comparison. Treat as t=0.
            result["timepoint_hours"] = (
                0.0 if not token.strip()
                else _parse_timepoint_hours(token)
            )
        else:
            result[field] = token

    return result

# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def load_2d_gray(path: Path) -> np.ndarray:
    """Load a TIFF (or other format) as a 2-D float32 array."""
    img = imread(str(path))
    while img.ndim > 2:
        img = img[0]
    return img.astype(np.float32)


# Background-correction math moved to ``pipeline.background.py`` (a Qt-free
# cohesive module). Re-exported here so ``pm.<name>`` call sites and the remote
# worker are unchanged. See that module for the implementations.
from pipeline.background import (  # noqa: F401
    BACKGROUND_METHODS,
    apply_tophat,
    combined_empty_well_correct,
    estimate_background_field,
    subtract_background,
    _floor_after_subtract,
    _fft_butterworth_bandpass,
    _isotropic_disk_tophat,
    _match_background_scale,
    _robust_grid_background,
)
def _subtract_background_step(img: np.ndarray, *, radius: int,
                              background_method: str, bg_field, fft_small: float = 0.0,
                              mesh_ratio: float = 3.0, mesh_sigma: float = 3.0,
                              combined_ew: bool = False, ff_field=None) -> np.ndarray:
    """The background half of per-channel correction, shared by the batch
    pipeline (``process_image_group._correct``) and the interactive Playground.

    In combined empty-well mode this IS the full correction
    ``(raw − B)/(B/mean(B))``; otherwise it is ``subtract_background(method)``.
    Extracted so both call sites run the exact same dispatch — a change to any
    method or to the combined rule affects the pipeline and the Playground
    identically.
    """
    if combined_ew:
        return combined_empty_well_correct(img, bg_field, ff_field)
    return subtract_background(img, radius, background_method, bg_field,
                               fft_small=fft_small, mesh_ratio=mesh_ratio,
                               mesh_sigma=mesh_sigma)


def correct_single_frame(image, *, flatfield_mode: str = "none",
                         background_method: str = "none", radius: int = 50,
                         fft_small: float = 0.0, mesh_ratio: float = 3.0,
                         mesh_sigma: float = 3.0, ff_field=None,
                         bg_field=None) -> np.ndarray:
    """Full flat-field + background correction for ONE raw frame, in the pipeline's
    order (flat-field divide → background subtract), with the combined empty-well
    special case folded in. Mirrors the orchestration of
    ``process_image_group`` so the interactive Playground produces exactly what a
    batch run would bake into the saved image.

    ``ff_field`` / ``bg_field`` are precomputed per-channel fields (None when the
    mode doesn't need them; ``gaussian`` self-estimates from the frame). For the
    combined case (both flat-field and background ``empty_well``), pass the
    additive ``bg_field`` (B) and the mean-1 ``ff_field`` (= B/mean(B)).
    """
    img = np.asarray(image, dtype=np.float32)
    combined_ew = (flatfield_mode == "empty_well" and background_method == "empty_well")
    # Flat-field up front unless folded into the combined step.
    if flatfield_mode and flatfield_mode != "none" and not combined_ew:
        ff = estimate_gaussian_flatfield(img) if flatfield_mode == "gaussian" else ff_field
        img = apply_flatfield(img, ff)
    return _subtract_background_step(
        img, radius=radius, background_method=background_method, bg_field=bg_field,
        fft_small=fft_small, mesh_ratio=mesh_ratio, mesh_sigma=mesh_sigma,
        combined_ew=combined_ew, ff_field=ff_field)


def _load_image_source(source) -> np.ndarray | None:
    """Load a 2-D float32 image from either a ``Path`` on disk or a
    ``(zip_path, member_name)`` tuple (a TIFF stored inside a per-well zip).
    Returns None on any read error so sampling can skip it."""
    import io as _io
    try:
        if isinstance(source, tuple):
            zpath, member = source
            with zipfile.ZipFile(zpath, "r") as zf:
                arr = imread(_io.BytesIO(zf.read(member)))
            while arr.ndim > 2:
                arr = arr[0]
            return arr.astype(np.float32)
        return load_2d_gray(source)
    except (OSError, zipfile.BadZipFile, ValueError, KeyError) as exc:
        log.debug("skipping unreadable image source %r: %s", source, exc)
        return None
    except Exception:  # unexpected (a bug, not bad data) — surface, still skip
        log.warning("unexpected error loading image %r; skipping", source, exc_info=True)
        return None


# Empty-well models are keyed ``{token: {tp_key: field, _POOLED_TP: field}}`` so a
# per-timepoint field tracks background drift; ``_POOLED_TP`` is the
# all-timepoints fallback used when a sample's timepoint has no blank frame.
_POOLED_TP = "__pooled__"


def _empty_well_tp_key(token_value) -> str:
    """Canonical timepoint key for grouping/matching empty-well frames.

    Parseable forms (``T01`` / ``24`` / ``24h`` / ``day1``) collapse to
    ``f"{hours:g}"`` so the empty-well frames and the sample image key identically;
    opaque labels pass through stripped. Empty when there's no timepoint.
    """
    raw = str(token_value or "").strip()
    if not raw:
        return ""
    h = _parse_timepoint_hours(raw)
    return raw if h != h else f"{h:g}"   # h != h ⇒ NaN (unparseable)


def _empty_well_field(models, token: str, tp_key: str):
    """Look up the per-(channel, timepoint) empty-well field, falling back to the
    pooled all-timepoints field when this timepoint has no blank frame. Returns
    None when the channel has no model at all."""
    per_tok = (models or {}).get(str(token).lower())
    if not isinstance(per_tok, dict):
        return per_tok            # defensive: legacy flat shape
    field = per_tok.get(tp_key)
    return field if field is not None else per_tok.get(_POOLED_TP)


def sample_empty_well_images(
    input_dir: Path,
    *,
    tokens: list[str],
    schema: list[str] | None,
    sep: str,
    background_wells: list[str],
    max_samples: int = 30,
) -> dict[str, dict[str, list[np.ndarray]]]:
    """Sample empty/background-well frames, pooled across the given *background_
    wells* and **grouped by timepoint**.

    Returns ``{token_lower: {tp_key: [array, ...]}}`` — per-channel, per-timepoint
    stacks the empty-well estimators consume. ``tp_key`` is the canonical
    timepoint (:func:`_empty_well_tp_key`); the special ``_POOLED_TP`` key holds
    every frame for that channel across all timepoints (the missing-timepoint
    fallback). Frames from all named blank wells (and all their FOVs) at a given
    timepoint are pooled. Reads from whichever input layout holds each well: a
    per-well ``<WELL>.zip`` (default), a per-well folder, or loose TIFFs.

    Background drifts over time, so a per-timepoint field corrects each timepoint
    on its own; pooling multiple blank wells de-noises the estimate.
    """
    fields = list(schema or [])
    chan_idx = fields.index("channel") if "channel" in fields else -1
    well_idx = fields.index("well") if "well" in fields else -1
    tp_idx = fields.index("timepoint") if "timepoint" in fields else -1
    wells = {_bg_well_key(w) for w in _split_background_wells(background_wells)}
    if chan_idx < 0 or not wells:
        return {}
    want = {str(t).strip().lower() for t in (tokens or [])}

    def _token_of(stem: str, *, require_well: bool) -> str | None:
        parts = stem.split(sep)
        if chan_idx >= len(parts):
            return None
        if require_well:
            if well_idx < 0 or well_idx >= len(parts) \
                    or _bg_well_key(parts[well_idx]) not in wells:
                return None
        tok = parts[chan_idx].strip().lower()
        if want and tok not in want:
            return None
        return tok

    def _tp_of(stem: str) -> str:
        parts = stem.split(sep)
        if 0 <= tp_idx < len(parts):
            return _empty_well_tp_key(parts[tp_idx])
        return ""

    # source := Path on disk | (zip_path, member_name); grouped (token, tp_key).
    by_token_tp: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    def _add(stem: str, source) -> None:
        tok = _token_of(stem, require_well=False)
        if tok is not None:
            by_token_tp[tok][_tp_of(stem)].append(source)

    # 1) per-well zip(s) (default mode): <input_dir>/<WELL>.zip. The well is
    #    established by the zip name, so members need not carry the well token.
    zip_hit = False
    for well, zpath in find_well_zips(input_dir):
        if _bg_well_key(well) not in wells:
            continue
        zip_hit = True
        try:
            with zipfile.ZipFile(zpath, "r") as zf:
                for member in zf.infolist():
                    name = Path(member.filename).name
                    if not name or name.startswith(".") \
                            or Path(name).suffix.lower() not in (".tif", ".tiff"):
                        continue
                    _add(Path(name).stem, (zpath, member.filename))
        except (OSError, zipfile.BadZipFile):
            continue

    # 2) per-well folder(s): <input_dir>/<WELL>/...  (well set by folder name).
    if not zip_hit:
        for well, folder in find_well_folders(input_dir):
            if _bg_well_key(well) not in wells:
                continue
            for p in sorted(folder.rglob("*.tif*")):
                _add(p.stem, p)

    # 3) loose TIFFs anywhere under input_dir — the filename must carry the well.
    if not by_token_tp:
        for p in sorted(input_dir.rglob("*.tif*")):
            tok = _token_of(p.stem, require_well=True)
            if tok is not None:
                by_token_tp[tok][_tp_of(p.stem)].append(p)

    def _load_capped(sources) -> list[np.ndarray]:
        step = max(1, len(sources) // max_samples)
        return [a for a in (_load_image_source(s)
                            for s in sources[::step][:max_samples]) if a is not None]

    out: dict[str, dict[str, list[np.ndarray]]] = {}
    for tok, by_tp in by_token_tp.items():
        per_tp: dict[str, list[np.ndarray]] = {}
        pooled_sources: list = []
        for tp_key, sources in by_tp.items():
            imgs = _load_capped(sources)
            if imgs:
                per_tp[tp_key] = imgs
            pooled_sources.extend(sources)
        pooled = _load_capped(pooled_sources)
        if pooled:
            per_tp[_POOLED_TP] = pooled
        if per_tp:
            out[tok] = per_tp
    return out



def _require_empty_well_models(what: str, mode: str, models, wells, source) -> None:
    """Stop a run that asked for empty-well correction and could not build it.

    A missing model is not a degraded correction, it is **no correction**:
    ``_empty_well_field`` returns None and the frame passes through untouched.
    The output then carries the raw background while looking exactly like a
    corrected well — there is no marker on the image, no column in the CSV and
    nothing in the run record to tell the two apart later.

    That is the one thing this codebase is not allowed to do (CLAUDE.md §7), so
    it fails instead. It fired for real: a batched remote run stages one batch at
    a time, and every batch that did not happen to contain the blank silently
    produced uncorrected numbers.

    Only when blanks were actually named — a run with none is a configuration
    the GUI already refuses, and failing here would only restate it worse.
    """
    if mode != "empty_well" or not wells or models:
        return
    raise SystemExit(
        f"--{what.replace('-', '')} empty_well was requested with background "
        f"well(s) {', '.join(wells)}, but no usable frames for them were found "
        f"in {source}. Nothing would be corrected, and the output would be "
        f"indistinguishable from a corrected run — so this run stops instead. "
        f"Check the well name(s) against the archives present, and on a batched "
        f"remote run check that --background_dir points at the directory the "
        f"blank was uploaded to."
    )


def build_background_models(
    input_dir: Path,
    *,
    tokens: list[str],
    schema: list[str] | None,
    sep: str,
    background_wells: list[str] | str = "",
    max_samples: int = 30,
    background_dir: Path | None = None,
) -> dict[str, dict[str, np.ndarray]]:
    """Per-channel, **per-timepoint** additive background fields from the empty/
    background well(s) (for ``--background empty_well``). Pools frames across all
    named blank wells and groups by timepoint via :func:`sample_empty_well_images`,
    then builds one :func:`estimate_background_field` per (channel, timepoint) plus
    a pooled all-timepoints field. Returns ``{token_lower: {tp_key: field,
    _POOLED_TP: field}}``; channels with no usable empty-well images omitted.
    """
    stacks = sample_empty_well_images(
        background_dir or input_dir, tokens=tokens, schema=schema, sep=sep,
        background_wells=background_wells, max_samples=max_samples)
    models: dict[str, dict[str, np.ndarray]] = {}
    for tok, by_tp in stacks.items():
        per_tp: dict[str, np.ndarray] = {}
        for tp_key, imgs in by_tp.items():
            field = estimate_background_field(imgs)
            if field is not None:
                per_tp[tp_key] = field
        if per_tp:
            models[tok] = per_tp
    return models


SMFISH_LOG_KERNEL = np.array([
    [-4, -1, 0, -1, -4],
    [-1, 2, 3, 2, -1],
    [0, 3, 4, 3, 0],
    [-1, 2, 3, 2, -1],
    [-4, -1, 0, -1, -4],
], dtype=np.float32)


def apply_smfish_log(image: np.ndarray) -> np.ndarray:
    from scipy.ndimage import convolve
    return convolve(image.astype(np.float32), SMFISH_LOG_KERNEL)


def compute_shape_features(
    labels: np.ndarray,
    selected_keys: list[str] | None,
) -> dict[int, dict[str, float]]:
    """Per-cell morphology features keyed by label id.

    Computes the user-selected shape features (``selected_keys``) **plus** the
    always-on centroids (needed by cell tracking) in a single
    ``skimage.measure.regionprops_table`` pass over the label image — the same
    pass QC debris-removal will reuse. Returns ``{label_id: {shape_col: value}}``
    using the ``shape_`` column names from :mod:`pipeline.shape_features` (the
    Qt-free catalog shared with the viewer). Returns ``{}`` when there are no
    labels. Channel-independent: no intensity image is needed.
    """
    import pipeline.shape_features as sfc
    from skimage.measure import regionprops_table

    props = sfc.regionprops_properties(selected_keys)  # base regionprop names (incl. centroid)
    if not props or labels is None or not np.any(labels):
        return {}
    table = regionprops_table(labels, properties=("label",) + tuple(props))
    label_ids = table["label"]
    emit_keys = list(sfc.normalize_selection(selected_keys)) + list(sfc.ALWAYS_ON_KEYS)

    out: dict[int, dict[str, float]] = {}
    for i, lab in enumerate(label_ids):
        rec: dict[str, float] = {}
        for key in emit_keys:
            feat = sfc.BY_KEY.get(key)
            if feat is None:
                continue
            if key == "circularity":
                area = float(table["area"][i])
                per = float(table["perimeter"][i])
                rec[feat.column] = float(4.0 * np.pi * area / (per * per)) if per > 0 else 0.0
            elif key == "centroid_row":
                rec[feat.column] = float(table["centroid-0"][i])
            elif key == "centroid_col":
                rec[feat.column] = float(table["centroid-1"][i])
            else:
                rec[feat.column] = float(table[feat.regionprop][i])
        out[int(lab)] = rec
    return out


# ── Quality control: flat-field correction + debris removal ──────────────────

FLATFIELD_MODES = ("none", "empty_well", "gaussian", "basic")
# Flat-field (illumination) estimators live in the Qt-free repo-root
# ``pipeline.flatfield`` module so the pipeline and the GUI share one implementation
# (the GUI recomputes them for the Segmentation tab's flat-field validation
# view). Imported under the original names so the rest of this module is
# unchanged.
from pipeline.flatfield import (  # noqa: F401
    _FLATFIELD_FLOOR,
    _FLATFIELD_SIGMA,
    _normalize_flatfield,
    apply_flatfield,
    estimate_basic_flatfield,
    estimate_gaussian_flatfield,
)


def compute_region_areas(
    image: np.ndarray,
    labels: np.ndarray,
    *,
    close_radius: int = 2,
) -> dict[int, float]:
    """Per-label area (px) of the connected bright *region* each nucleus sits in.

    StarDist mis-segments a bright debris blob into many small star-convex
    labels, so per-cell size can't reject it (each fragment is small). This
    reconstructs the image-level region the fragments belong to: the Otsu-bright
    foreground **unioned with the label mask**, morphologically closed so the
    densely-packed fragments (or a solid bright blob) re-merge into one connected
    component. A normal isolated nucleus sits in a small component (≈ its own
    size); every fragment of a debris blob shares the same *large* component.

    Returns ``{label_id: region_area_px}`` so a post-run "max region area" gate
    rejects the whole blob at once. Pure numpy / skimage — unit-tested without
    the app. Returns all-zero (no gating) when the image is unusable.
    """
    lab = np.asarray(labels)
    ids = np.unique(lab)
    ids = ids[ids != 0]
    out: dict[int, float] = {int(n): 0.0 for n in ids}
    if not out:
        return out
    img = np.asarray(image, dtype=np.float32)
    if img.shape != lab.shape or img.size == 0:
        return out

    from skimage.measure import label as _cc_label
    fg = (lab > 0)
    finite = img[np.isfinite(img)]
    if finite.size and float(finite.max()) > float(finite.min()):
        try:
            from skimage.filters import threshold_otsu
            fg = fg | (img > float(threshold_otsu(finite)))
        except Exception:
            swallowed()  # was: pass (silent except)
    if close_radius and close_radius > 0:
        try:
            from scipy.ndimage import binary_closing as _binary_closing
            from skimage.morphology import disk
            fg = _binary_closing(fg, structure=disk(int(close_radius)))
        except Exception:
            swallowed()  # was: pass (silent except)
    # closing can erode label pixels near the image border; re-union the labels
    # so every nucleus always anchors a component at least its own size.
    fg = np.asarray(fg, dtype=bool) | (lab > 0)

    comp = _cc_label(fg)
    comp_area = np.bincount(comp.ravel())  # area (px) of each connected component
    # Dominant component per nucleus label (its pixels lie in one component).
    sel = lab > 0
    ncomp = int(comp.max()) + 1
    keyed = lab[sel].astype(np.int64) * ncomp + comp[sel].astype(np.int64)
    ukey, ucount = np.unique(keyed, return_counts=True)
    best_count: dict[int, int] = {}
    for k, cnt in zip(ukey.tolist(), ucount.tolist()):
        l, c = divmod(k, ncomp)
        if c == 0:
            continue
        if cnt > best_count.get(l, 0):
            best_count[l] = cnt
            out[int(l)] = float(comp_area[c])
    return out


def build_flatfield_models(
    input_dir: Path,
    *,
    tokens: list[str],
    schema: list[str] | None,
    sep: str,
    mode: str,
    background_wells: list[str] | str = "",
    max_samples: int = 30,
    background_dir: Path | None = None,
) -> dict[str, dict[str, np.ndarray]]:
    """Precompute per-channel flat-field models for the ``basic`` / ``empty_well`` modes.

    Returns the uniform nested shape ``{token_lower: {tp_key: field, _POOLED_TP:
    field}}`` so the per-image lookup (:func:`_empty_well_field`) is the same for
    both modes. For ``empty_well`` the field is built **per timepoint** (and a
    pooled fallback) from frames pooled across all named blank wells, via
    :func:`sample_empty_well_images`. ``basic`` samples the whole plate
    (time-collapsed) from TIFFs on disk and stores one ``_POOLED_TP`` field per
    channel. Each field is a mean-1 :func:`estimate_basic_flatfield`; channels
    with no usable images are omitted (so :func:`apply_flatfield` no-ops them).
    """
    if mode not in ("basic", "empty_well"):
        return {}
    fields = list(schema or [])
    chan_idx = fields.index("channel") if "channel" in fields else -1
    if chan_idx < 0:
        return {}
    want = {str(t).strip().lower() for t in (tokens or [])}

    models: dict[str, dict[str, np.ndarray]] = {}
    if mode == "empty_well":
        if not _split_background_wells(background_wells):
            return {}
        # Only the empty-well branch reads the blank; `basic` samples the whole
        # plate and must keep looking at the run's own input.
        stacks = sample_empty_well_images(
            background_dir or input_dir, tokens=tokens, schema=schema, sep=sep,
            background_wells=background_wells, max_samples=max_samples)
        for tok, by_tp in stacks.items():
            per_tp: dict[str, np.ndarray] = {}
            for tp_key, imgs in by_tp.items():
                field = estimate_basic_flatfield(imgs)
                if field is not None:
                    per_tp[tp_key] = field
            if per_tp:
                models[tok] = per_tp
    else:  # basic — whole-plate sampling from loose/folder TIFFs on disk.
        by_token: dict[str, list[Path]] = {}
        for path in sorted(input_dir.rglob("*.tif*")):
            parts = path.stem.split(sep)
            if chan_idx >= len(parts):
                continue
            tok = parts[chan_idx].strip().lower()
            if want and tok not in want:
                continue
            by_token.setdefault(tok, []).append(path)
        for tok, paths in by_token.items():
            step = max(1, len(paths) // max_samples)
            imgs = [a for a in (_load_image_source(p)
                                for p in paths[::step][:max_samples]) if a is not None]
            if not imgs:
                continue
            field = estimate_basic_flatfield(imgs)
            if field is not None:
                models[tok] = {_POOLED_TP: field}
    return models




def _segment_classical(image: np.ndarray) -> np.ndarray:
    """Label-free segmentation by Otsu threshold + distance-transform watershed.

    Splits touching objects without any deep-learning model: Otsu (via
    ``threshold_otsu``) gives the foreground mask; the EDT peaks seed a
    watershed (both already imported for the seeded-watershed mode). Reads the
    designated phase channel. No new dependency.
    """
    from skimage.feature import peak_local_max
    from skimage.measure import label as _label
    img = np.asarray(image, dtype=np.float32)
    try:
        thresh = threshold_otsu(img)
    except ValueError:
        thresh = float(np.mean(img))
    mask = img > thresh
    if not np.any(mask):
        return np.zeros(img.shape, dtype=np.int32)
    distance = ndi.distance_transform_edt(mask)
    # Seed one marker per local distance maximum (≈ one per object).
    coords = peak_local_max(distance, min_distance=5, labels=mask)
    markers = np.zeros(distance.shape, dtype=np.int32)
    for i, (y, x) in enumerate(coords, start=1):
        markers[y, x] = i
    if markers.max() == 0:
        # No peaks (e.g. a single blob) — fall back to connected components.
        return _label(mask).astype(np.int32)
    labels = watershed(-distance, markers=markers, mask=mask)
    return np.asarray(labels, dtype=np.int32)


# ---------------------------------------------------------------------------
# Auto-threshold estimation (self-contained — runs without well_viewer)
# ---------------------------------------------------------------------------
#
# Walks ``<output_dir>/*_out.zip``, picks the first/middle/last timepoint of
# every well, samples per-cell means (from the ``*_labels`` mask) plus a
# matched random background pixel (from the ``*_tophat`` fluor image) per
# channel, and runs Otsu on the pooled distribution. The thresholds are
# merged into ``pipeline_info.json``'s ``cell_gating.thresh_frac_on`` block
# (preserving any user-set values).
#
# The per-image sampler and timepoint helpers live in
# ``pipeline.auto_threshold`` so the GUI's Cell Gating tab and this pipeline
# run produce identical defaults on the same dataset. ``pipeline.auto_threshold``
# depends only on ``numpy`` + ``random`` so the pipeline-only deployment
# story is preserved.

from pipeline.auto_threshold import (
    DEFAULT_CELLS_PER_IMAGE_CAP as _AUTO_THRESHOLD_CELLS_PER_IMAGE_CAP,  # noqa: F401
    estimate_thresholds_from_outputs,
    pick_endpoint_timepoints as _pick_endpoint_timepoints,  # noqa: F401
    sample_cell_and_bg as _sample_cell_and_bg,  # noqa: F401
)
from pipeline.threshold_optimize import estimate_thresholds_from_controls
from util.aw_safe import swallowed


def _apply_thresholds_to_pipeline_info(
    output_dir: Path,
    thresholds: dict[str, float],
    log: logging.Logger,
) -> dict[str, float]:
    """Merge ``thresholds`` into ``output_dir/pipeline_info.json`` under
    ``cell_gating.thresh_frac_on``. User-set values are preserved.
    """
    if not thresholds:
        return {}
    info_path = output_dir / "pipeline_info.json"
    if not info_path.exists():
        info_path = output_dir.parent / "pipeline_info.json"
    if not info_path.exists():
        log.info("Auto-threshold: pipeline_info.json not found — defaults not persisted.")
        return {}
    try:
        existing = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Auto-threshold: cannot read %s: %s", info_path, exc)
        return {}
    if not isinstance(existing, dict):
        return {}

    cell_gating = dict(existing.get("cell_gating") or {})
    tfo = dict(cell_gating.get("thresh_frac_on") or {})
    written: dict[str, float] = {}
    for ch, thr in thresholds.items():
        if ch in tfo:  # never overwrite a user-set value
            continue
        tfo[ch] = float(thr)
        written[ch] = float(thr)
    if not written:
        return {}
    cell_gating["thresh_frac_on"] = tfo
    existing["cell_gating"] = cell_gating
    try:
        write_json_atomic(info_path, existing)
    except OSError as exc:
        log.warning("Auto-threshold: cannot write %s: %s", info_path, exc)
        return {}
    return written


def _segmentation_tokens(args) -> set[str]:
    """Channel token(s) that ARE the segmentation input, lower-cased.

    ``--nuclear_token`` (already remapped onto ``--phase_token`` for the
    label-free methods by the time this runs) plus ``--cytoplasm_token`` under
    seeded watershed — the same pair that joins ``fluor_tokens_for_quant``.
    """
    toks = {str(getattr(args, "nuclear_token", "") or "").strip().lower()}
    if (getattr(args, "segmentation_method", "") == "stardist_seeded_watershed_cell"
            and getattr(args, "cytoplasm_token", "")):
        toks.add(str(args.cytoplasm_token).strip().lower())
    return {t for t in toks if t}


def run_auto_threshold_pass(args, output_dir: Path, fluor_tokens_for_quant: list[str],
                            log: logging.Logger) -> dict[str, float]:
    """Write the post-run per-channel ThreshFracOn defaults. Three modes:

    * ``--no_auto_threshold`` — skip entirely.
    * no control wells — Otsu on a pooled cell-mean / background-pixel
      distribution (:func:`pipeline.auto_threshold.estimate_thresholds_from_outputs`).
    * both control wells — the threshold maximising the relative fraction-on gap
      between them, read from the per-well CSVs this run just wrote.

    In both non-skip modes the segmentation channel is written **0**: it defines
    cell boundaries rather than being something to gate on. Zero is not absent —
    absent means the viewer's 50.0 default, whereas 0 counts every cell with a
    positive intensity as ON, which is how the channel is neutralised.

    Returns the channels actually written (existing values are never
    overwritten). Never raises: auto-threshold is informational and the run's
    real output is already on disk.
    """
    if getattr(args, "no_auto_threshold", False):
        log.info("Auto-threshold skipped (--no_auto_threshold).")
        return {}
    try:
        seg_tokens = _segmentation_tokens(args)
        non_seg = [c for c in fluor_tokens_for_quant
                   if str(c).strip().lower() not in seg_tokens]
        pos = str(getattr(args, "pos_control_well", "") or "").strip()
        neg = str(getattr(args, "neg_control_well", "") or "").strip()
        if pos and neg:
            log.info("Auto-threshold: control wells %s (positive) vs %s (negative).",
                     pos, neg)
            thresholds = estimate_thresholds_from_controls(
                output_dir,
                fluor_channels=non_seg,
                csv_prefix=args.csv_prefix,
                pos_well=pos,
                neg_well=neg,
                log=log,
            )
        else:
            thresholds = estimate_thresholds_from_outputs(
                output_dir,
                fluor_channels=non_seg,
                filename_schema=args.filename_schema,
                filename_sep=args.filename_sep,
                log=log,
            )
        # Which readout channels the estimate actually produced, before the
        # segmentation channel's unconditional zero joins them — the two say
        # very different things about the run and used to be reported as one.
        estimated = sorted(thresholds)
        for tok in seg_tokens:
            thresholds[tok] = 0.0
        written = _apply_thresholds_to_pipeline_info(output_dir, thresholds, log)
        # Estimating nothing for every readout channel is a failure, and it has
        # to be reported on its own terms rather than as part of the
        # what-got-written summary: the segmentation channel's unconditional
        # zero *is* a write, so it makes `written` truthy and the run reads as a
        # success that happened to produce one value. That is exactly how the
        # reported run looked.
        if non_seg and not estimated:
            log.warning(
                "Auto-threshold: no threshold could be estimated for any of %s. "
                "The per-well output archives yielded no usable image pairs — "
                "see the per-well lines above for why.",
                ", ".join(c.upper() for c in non_seg),
            )
        if written:
            log.info(
                "Auto-threshold defaults written for %d channel(s): %s",
                len(written),
                ", ".join(f"{ch.upper()}={v:.4g}" for ch, v in written.items()),
            )
        elif estimated or not non_seg:
            log.info("Auto-threshold pass produced no new defaults "
                     "(every channel already has a stored value).")
        return written
    except Exception as exc:
        log.warning("Auto-threshold pass failed: %s", exc)
        return {}


def build_processed_output_path(
    src_path: Path,
    *,
    output_dir: Path,
    suffix: str = "",
    ext_override: str | None = None,
) -> Path:
    """Build output path as input stem + suffix + extension.

    Pass ``suffix="_tophat"`` / ``"_labels"`` etc. directly. The default
    extension is copied from *src_path* so processed outputs preserve the
    source file type (``.tif`` / ``.tiff`` etc.) unless explicitly overridden.

    (The reader-side ``image_identity.ROLE_CATALOG`` is the canonical suffix
    table. A previous "preferred" catalog-driven branch here imported it from
    ``well_viewer`` — a violation of the §6.2 pipeline-self-sufficiency contract,
    and dead anyway since no caller passed ``role=``. If catalog-driven *writing*
    is ever revived, move the catalog to a Qt-free ``*_core`` module first.)
    """
    ext = str(ext_override) if ext_override is not None else str(src_path.suffix)
    if not ext:
        ext = ".tif"
    return output_dir / f"{src_path.stem}{suffix}{ext}"

# ---------------------------------------------------------------------------
# Per-image worker (runs in a subprocess)
# ---------------------------------------------------------------------------

# ── process_image_group phases ──────────────────────────────────────────────
# process_image_group() is a thin orchestrator over these five phases (T4). Each
# is a verbatim lift of the corresponding block from the original monolith — the
# leaf work (background subtraction, segmentation, shape/region features, atomic
# image writes) still lives in pipeline.background / _stardist / _cellpose /
# _io and the shape_features helpers; these just group the per-FOV sequencing so
# the orchestrator reads as correct -> segment -> write -> quantify.


def _load_image_group(nuclear_path, fluor_paths, fluor_tokens):
    """Load the nuclear frame + every fluor channel; verify shape consistency."""
    log.info("Processing %s  (%d fluor channel(s): %s)",
             nuclear_path.name, len(fluor_paths), ", ".join(fluor_tokens))
    _stardist.set_fov_label(nuclear_path.name)

    _t = time.perf_counter()
    nir_raw = load_2d_gray(nuclear_path)
    log.info("  load nuclear: %.1f s  (shape %s)", time.perf_counter() - _t, nir_raw.shape)

    # Load every fluorescent channel and verify shape consistency.
    fluor_raws: list[np.ndarray] = []
    for fp in fluor_paths:
        _t = time.perf_counter()
        img = load_2d_gray(fp)
        if img.shape != nir_raw.shape:
            raise ValueError(
                f"Shape mismatch: nuclear {nir_raw.shape} vs "
                f"{fp.name} {img.shape}"
            )
        fluor_raws.append(img)
        log.info("  load fluor:  %.1f s  %s", time.perf_counter() - _t, fp.name)
    return nir_raw, fluor_raws


def _correct_image_group(
    nir_raw, fluor_raws, *, nuclear_path, nuclear_token, fluor_tokens,
    tophat_nir, tophat_radius_nir, tophat_fluor, tophat_radius_fluor,
    fft_small_nir, fft_small_fluor, flatfield_mode, flatfield_models,
    background_method, background_models, smfish_tokens, mesh_ratio, mesh_sigma,
    schema, sep, phase_token="",
):
    """Flat-field + per-channel background correction (+ smFISH LoG).

    Returns ``(nir_raw, fluor_raws, nir_corr, fluor_tophat, fluor_corr)``. The
    *raws* are returned because the flat-field step rewrites them in place — and
    the (flat-fielded) raws are what segmentation (watershed/cellpose) and the
    overlay image consume downstream, so the orchestrator must use these, not the
    originals.

    *phase_token* designates the phase/brightfield channel: that channel skips
    BOTH flat-field and background subtraction, so its ``nir_corr`` /
    ``fluor_tophat`` entry is the untouched original — which the writer then
    saves under the usual ``_tophat`` suffix. Background-correcting a
    phase-contrast image is meaningless (and, in label-free mode, feeds Cellpose
    a raw brightfield image, which is what its model expects).
    """
    _ph = str(phase_token or "").strip().lower()

    def _is_phase(token: str) -> bool:
        return bool(_ph) and str(token or "").strip().lower() == _ph
    # ── Flat-field (illumination) + background correction ────────────────────
    # Normal order is flat-field (divide) → background (subtract). The one
    # exception is the proper *combined* empty-well model: when both flat-field
    # and background use the empty well, naive divide-then-subtract mixes scales
    # (the additive field is at raw scale but applied after the rescale). The
    # physically-correct correction is (raw − B)/(B/mean(B)) — subtract the
    # measured additive background B first, then divide by the mean-1 illumination
    # field — which assumes the background tracks the illumination (true for media
    # autofluorescence under uneven excitation). In that case both are folded into
    # one per-channel step below and the standalone flat-field is skipped.
    combined_ew = (flatfield_mode == "empty_well" and background_method == "empty_well")

    # This image group's timepoint, used to pick the per-timepoint empty-well
    # field (background drifts over time). All images in a group share the
    # nuclear frame's fov/timepoint. Falls back to the pooled all-timepoints
    # field via _empty_well_field when this timepoint has no blank frame.
    _grp_tp = _empty_well_tp_key(
        parse_filename(nuclear_path, schema=schema, sep=sep).get("timepoint", ""))

    def _flatfield_for(token: str, img: np.ndarray):
        if flatfield_mode == "gaussian":
            return estimate_gaussian_flatfield(img)
        return _empty_well_field(flatfield_models, token, _grp_tp)

    def _bg_for(token: str):
        return _empty_well_field(background_models, token, _grp_tp)

    # Flat-field up front for every channel, unless folded into the combined step.
    if flatfield_mode and flatfield_mode != "none" and not combined_ew:
        _t = time.perf_counter()
        # Skip flat-field on the phase channel so its raw is left untouched.
        if not _is_phase(nuclear_token):
            nir_raw = apply_flatfield(nir_raw, _flatfield_for(nuclear_token, nir_raw))
        fluor_raws = [
            f if _is_phase(tok) else apply_flatfield(f, _flatfield_for(tok, f))
            for f, tok in zip(fluor_raws, fluor_tokens)
        ]
        log.info("  flatfield:   %.1f s  (mode=%s)", time.perf_counter() - _t, flatfield_mode)

    # Background subtraction per channel (gated by the top-hat toggle). In
    # combined empty-well mode this step IS the full correction
    # (raw − B)/(B/mean(B)); otherwise it is subtract_background(method) where
    # method ∈ none / top_hat / sliding_disk / empty_well.
    fft_small_fluor = fft_small_fluor if fft_small_fluor is not None else [0] * len(fluor_raws)

    def _correct(img: np.ndarray, token: str, radius: int, enabled: bool,
                 small_px: int = 0) -> np.ndarray:
        # Flat-field is already applied up front (above); this is the background
        # half only, shared verbatim with the interactive Playground via
        # _subtract_background_step.
        if not enabled:
            return img
        return _subtract_background_step(
            img, radius=radius, background_method=background_method,
            bg_field=_bg_for(token), fft_small=small_px,
            mesh_ratio=mesh_ratio, mesh_sigma=mesh_sigma,
            combined_ew=combined_ew,
            ff_field=_flatfield_for(token, img) if combined_ew else None)

    _t = time.perf_counter()
    if _is_phase(nuclear_token):
        # Phase channel: no correction — the raw is the output (resaved as-is).
        nir_corr = nir_raw
        log.info("  bg_nir:      skipped (phase channel %r — resaving original)",
                 nuclear_token)
    else:
        nir_corr = _correct(nir_raw, nuclear_token, tophat_radius_nir, tophat_nir,
                            fft_small_nir)
        log.info("  bg_nir:      %.1f s  (method=%s%s, radius %d, enabled=%s)",
                 time.perf_counter() - _t, background_method,
                 " +combined-empty_well" if combined_ew else "",
                 tophat_radius_nir, tophat_nir)

    fluor_tophat: list[np.ndarray] = []
    for i, (fraw, do_th, radius) in enumerate(
            zip(fluor_raws, tophat_fluor, tophat_radius_fluor)):
        _t = time.perf_counter()
        if _is_phase(fluor_tokens[i]):
            # Phase channel: no correction — pass the raw through unchanged.
            fluor_tophat.append(fraw)
            log.info("  bg_%s:  skipped (phase channel — resaving original)",
                     fluor_tokens[i].lower())
            continue
        fc = _correct(fraw, fluor_tokens[i], radius, do_th, fft_small_fluor[i])
        fluor_tophat.append(fc)
        log.info("  bg_%s:  %.1f s  (method=%s, radius %d, enabled=%s)",
                 fluor_tokens[i].lower(), time.perf_counter() - _t,
                 background_method, radius, do_th)

    smfish_set = set(smfish_tokens or [])
    fluor_corr = list(fluor_tophat)
    for i, tok in enumerate(fluor_tokens):
        if tok in smfish_set:
            _t = time.perf_counter()
            fluor_corr[i] = apply_smfish_log(fluor_tophat[i])
            log.info("  smfish_log_%s: %.1f s", tok.lower(), time.perf_counter() - _t)
    return nir_raw, fluor_raws, nir_corr, fluor_tophat, fluor_corr


def _segment_image_group(
    *, segmentation_method, nir_corr, fluor_raws, fluor_tokens, cytoplasm_token,
    tophat_radius_fluor, min_nucleus_area_px, cellpose_model, cellpose_diameter,
    cellpose_flow_threshold, cellpose_cellprob_threshold, cellpose_nuclei_channel,
):
    """Dispatch to the configured segmenter -> label image (+ watershed extras).

    Returns ``(labels, nuclear_points, cytoplasm_tophat, cytoplasm_binary_mask)``;
    the last three are ``None`` for every method except seeded-watershed cell.
    """
    _t = time.perf_counter()
    labels: np.ndarray
    nuclear_points: np.ndarray | None = None
    cytoplasm_tophat: np.ndarray | None = None
    cytoplasm_binary_mask: np.ndarray | None = None
    if segmentation_method == "stardist_seeded_watershed_cell":
        if not cytoplasm_token:
            raise ValueError(
                "segmentation_method=stardist_seeded_watershed_cell requires --cytoplasm_token."
            )
        if cytoplasm_token not in fluor_tokens:
            raise ValueError(
                f"Cytoplasm token '{cytoplasm_token}' is missing from fluor token list: {fluor_tokens}"
            )
        cyto_idx = fluor_tokens.index(cytoplasm_token)
        labels, nuclear_points, cytoplasm_tophat, cytoplasm_binary_mask = (
            _segment_stardist_seeded_watershed_cell(
                nir_corr=nir_corr,
                cytoplasm_raw=fluor_raws[cyto_idx],
                tophat_radius_fluor=tophat_radius_fluor[cyto_idx],
                min_nucleus_area_px=min_nucleus_area_px,
            )
        )
        log.info(
            "  watershed:   %.1f s  (%d cells detected)",
            time.perf_counter() - _t,
            len(np.unique(labels)) - 1,
        )
    elif segmentation_method in _CELLPOSE_METHODS:
        # General Cellpose segments the chosen Segmentation channel (nir_corr).
        # The 'cellpose_labelfree' preset is identical mechanics: main() already
        # remapped the nuclear slot onto the phase channel, and forces cyto3, so
        # nir_corr is the phase image and the params are the brightfield preset.
        nucleus_image = None
        nuc_tok = str(cellpose_nuclei_channel or "").strip().lower()
        if nuc_tok:
            lowered = [t.lower() for t in fluor_tokens]
            if nuc_tok in lowered:
                nucleus_image = fluor_raws[lowered.index(nuc_tok)]
        # Deferred import so a StarDist-only build (Cellpose profiled out) can
        # omit pipeline/cellpose.py entirely; the error only surfaces here, when
        # a Cellpose method is actually selected.
        from pipeline.cellpose import _segment_cellpose
        labels = _segment_cellpose(
            nir_corr, model_type=cellpose_model, diameter=cellpose_diameter,
            flow_threshold=cellpose_flow_threshold,
            cellprob_threshold=cellpose_cellprob_threshold,
            nucleus_image=nucleus_image,
        )
        log.info("  cellpose:    %.1f s  (%d cells detected; model=%s)",
                 time.perf_counter() - _t, len(np.unique(labels)) - 1, cellpose_model)
    elif segmentation_method == "classical_threshold":
        labels = _segment_classical(nir_corr)
        log.info("  classical:   %.1f s  (%d objects detected)",
                 time.perf_counter() - _t, len(np.unique(labels)) - 1)
    else:
        labels = _segment_stardist_nuclei(nir_corr)
        log.info("  stardist:    %.1f s  (%d nuclei detected)",
                 time.perf_counter() - _t, len(np.unique(labels)) - 1)
    return labels, nuclear_points, cytoplasm_tophat, cytoplasm_binary_mask


def _write_processed_images(
    *, save_tophat_images, nuclear_path, fluor_paths, fluor_tokens, output_dir,
    nir_raw, nir_corr, fluor_tophat, fluor_corr, smfish_set, segmentation_method,
    nuclear_points, cytoplasm_tophat, cytoplasm_binary_mask, labels,
    background_method,
):
    """Write the background-corrected tifs, label image, and overlay."""
    # Save QC images.
    # Keep schema tokens in output stems so output names stay harmonized with
    # input naming conventions and schema-driven resolvers.
    #
    # NB: the ``_tophat`` suffix is **legacy naming** — these are the
    # *background-corrected* images (nir_corr / fluor_tophat), i.e. the output of
    # whatever --background method ran (top_hat / sliding_disk / empty_well /
    # none, after any flat-field), not specifically a top-hat. The suffix is kept
    # because the Review side reads ``_tophat`` files; only the label is stale.
    _t = time.perf_counter()
    if save_tophat_images:
        th_nir_path = build_processed_output_path(
            nuclear_path,
            output_dir=output_dir,
            suffix="_tophat",
        )
        _safe_imwrite(th_nir_path, np.clip(nir_corr, 0, None).astype(np.float32))
        for fc, tok, fp in zip(fluor_tophat, fluor_tokens, fluor_paths):
            th_path = build_processed_output_path(
                fp,
                output_dir=output_dir,
                suffix="_tophat",
            )
            _safe_imwrite(th_path, np.clip(fc, 0, None).astype(np.float32))
        for i, (tok, fp) in enumerate(zip(fluor_tokens, fluor_paths)):
            if tok in smfish_set:
                smfish_path = build_processed_output_path(
                    fp,
                    output_dir=output_dir,
                    suffix="_smfish",
                )
                _safe_imwrite(smfish_path, np.clip(fluor_corr[i], 0, None).astype(np.float32))
        if segmentation_method == "stardist_seeded_watershed_cell":
            assert nuclear_points is not None
            assert cytoplasm_tophat is not None
            assert cytoplasm_binary_mask is not None
            _safe_imwrite(
                build_processed_output_path(
                    nuclear_path,
                    output_dir=output_dir,
                    suffix="_nuclear_points",
                ),
                nuclear_points.astype(np.int32),
            )
            _safe_imwrite(
                build_processed_output_path(
                    nuclear_path,
                    output_dir=output_dir,
                    suffix="_cytoplasm_tophat",
                ),
                np.clip(cytoplasm_tophat, 0, None).astype(np.float32),
            )
            _safe_imwrite(
                build_processed_output_path(
                    nuclear_path,
                    output_dir=output_dir,
                    suffix="_cytoplasm_otsu_mask",
                ),
                cytoplasm_binary_mask.astype(np.uint8),
            )
        log.info("  bg-corrected tifs written  (method=%s, suffix=_tophat [legacy])",
                 background_method)
    _safe_imwrite(
        build_processed_output_path(
            nuclear_path,
            output_dir=output_dir,
            suffix="_labels",
        ),
        labels,
    )
    save_overlay(
        nir_raw,
        labels,
        build_processed_output_path(
            nuclear_path,
            output_dir=output_dir,
            suffix="_overlay",
            ext_override=".png",
        ),
    )
    log.info("  save:        %.1f s", time.perf_counter() - _t)



def _quantify_image_group(
    *, labels, fluor_tophat, fluor_tokens, nir_corr, nuclear_path, schema, sep,
    shape_features,
):
    """Per-nucleus intensity + shape/region quantification -> record dicts."""
    # Quantify each nucleus across all fluorescent channels.
    meta    = parse_filename(nuclear_path, schema=schema, sep=sep)
    nuc_ids = np.unique(labels)
    nuc_ids = nuc_ids[nuc_ids != 0]
    if len(nuc_ids) == 0:
        log.info("  quantify:    0.0 s  (0 nuclei)")
        return []

    _t = time.perf_counter()
    records: list[dict] = []
    idx = np.asarray(nuc_ids, dtype=np.int32)
    # ``scipy.ndimage``'s centered statistics (``standard_deviation`` /
    # ``variance``) internally compute ``sums / counts`` over the *full* label
    # histogram — every integer from 0 to ``labels.max()`` — before selecting our
    # ``index`` entries (scipy _measurements.py:646). The seeded-watershed method
    # routinely emits *non-contiguous* label ids (a marker whose basin lands
    # entirely outside the cytoplasm mask leaves a gap), so those gap ids have a
    # zero count and scipy hits 0/0 -> a stream of "invalid value encountered in
    # divide" RuntimeWarnings. The per-cell results for the ids we actually pass
    # in ``idx`` are unaffected (they always have count >= 1), so this is pure log
    # noise; silence just the numpy FP flags around the measurement calls rather
    # than relabelling (which would renumber every cell's nucleus_id).
    area = ndi.sum(np.ones_like(labels, dtype=np.float32), labels=labels, index=idx)
    fluor_stats: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, str]] = []
    # Quantification is always computed from top-hat corrected fluorescence
    # images. smFISH LoG outputs are only produced as optional intermediates.
    with np.errstate(invalid="ignore", divide="ignore"):
        for fc, tok in zip(fluor_tophat, fluor_tokens):
            fluor_stats.append((
                ndi.sum(fc, labels=labels, index=idx),
                ndi.mean(fc, labels=labels, index=idx),
                ndi.maximum(fc, labels=labels, index=idx),
                ndi.minimum(fc, labels=labels, index=idx),
                ndi.standard_deviation(fc, labels=labels, index=idx),
                tok.lower(),
            ))

    # Per-cell morphology (selected shape features + always-on centroids), one
    # regionprops pass over the label image. Channel-independent → emitted with
    # a shape_ prefix, merged into each cell record by nucleus id.
    # ``solidity`` is always included (even if unselected) so the post-run QC
    # debris gate (well_viewer/features/qc) can be edited without re-segmenting;
    # area is already emitted as the base ``area_px`` column.
    _qc_shape_keys = list(shape_features or [])
    if "solidity" not in _qc_shape_keys:
        _qc_shape_keys.append("solidity")
    shape_by_id = compute_shape_features(labels, _qc_shape_keys)

    # Per-cell bright-region area (always emitted) so the post-run QC gate can
    # reject debris that StarDist shattered into many small cells — a size gate
    # on this region area catches the blob that per-cell size misses.
    region_by_id = compute_region_areas(nir_corr, labels)

    for i, nid in enumerate(nuc_ids):
        rec: dict = {
            **meta,
            "nucleus_id": int(nid),
            "area_px":    int(area[i]),
        }
        for total, mean, maxv, minv, stdv, pfx in fluor_stats:
            rec[f"{pfx}_total_intensity"] = float(total[i])
            rec[f"{pfx}_mean_intensity"]  = float(mean[i])
            rec[f"{pfx}_max_intensity"]   = float(maxv[i])
            rec[f"{pfx}_min_intensity"]   = float(minv[i])
            rec[f"{pfx}_std_intensity"]   = float(stdv[i])
        rec.update(shape_by_id.get(int(nid), {}))
        rec["qc_region_area"] = float(region_by_id.get(int(nid), 0.0))
        records.append(rec)
    log.info("  quantify:    %.1f s  (%d nuclei)", time.perf_counter() - _t, len(records))

    return records


def process_image_group(
    nuclear_path: Path,
    fluor_paths: list[Path],
    fluor_tokens: list[str],
    output_dir: Path,
    nuclear_token: str,
    tophat_nir: bool,
    tophat_radius_nir: int,
    tophat_fluor: list[bool],
    tophat_radius_fluor: list[int],
    fft_small_nir: int = 0,
    fft_small_fluor: list[int] | None = None,
    mesh_ratio: float = 3.0,
    mesh_sigma: float = 3.0,
    save_tophat_images: bool = True,
    schema: list[str] | None = None,
    sep: str = DEFAULT_SEP,
    smfish_tokens: list[str] | None = None,
    segmentation_method: str = DEFAULT_SEGMENTATION_METHOD,
    cytoplasm_token: str = "",
    min_nucleus_area_px: int = 50,
    shape_features: list[str] | None = None,
    flatfield_mode: str = "none",
    flatfield_models: dict | None = None,
    background_method: str = "sliding_disk",
    background_models: dict | None = None,
    qc_remove_debris: bool = False,
    cellpose_diameter: float = 0.0,
    cellpose_model: str = "cyto3",
    cellpose_flow_threshold: float = 0.4,
    cellpose_cellprob_threshold: float = 0.0,
    cellpose_nuclei_channel: str = "",
    phase_token: str = "",
) -> list[dict]:
    """
    Process one nuclear image together with an arbitrary number of fluorescent
    channel images.  Returns a list of per-nucleus measurement dicts.

    *fluor_paths*        : one Path per fluorescent channel (same order as
                           *fluor_tokens*).
    *fluor_tokens*       : channel token strings (e.g. ["GFP", "mCherry"]).
    *tophat_fluor*       : per-channel top-hat enable flags.
    *tophat_radius_fluor*: per-channel top-hat radii.
    *phase_token*        : if set, the channel with this token (nuclear or one
                           of the fluor channels) skips ALL background correction
                           and is resaved from the original image.

    Output CSV columns for each fluor channel are prefixed with the lower-cased
    token, e.g. "gfp_total_intensity", "mcherry_mean_intensity".
    """
    nir_raw, fluor_raws = _load_image_group(nuclear_path, fluor_paths, fluor_tokens)

    nir_raw, fluor_raws, nir_corr, fluor_tophat, fluor_corr = _correct_image_group(
        nir_raw, fluor_raws, nuclear_path=nuclear_path, nuclear_token=nuclear_token,
        fluor_tokens=fluor_tokens, tophat_nir=tophat_nir,
        tophat_radius_nir=tophat_radius_nir, tophat_fluor=tophat_fluor,
        tophat_radius_fluor=tophat_radius_fluor, fft_small_nir=fft_small_nir,
        fft_small_fluor=fft_small_fluor, flatfield_mode=flatfield_mode,
        flatfield_models=flatfield_models, background_method=background_method,
        background_models=background_models, smfish_tokens=smfish_tokens,
        mesh_ratio=mesh_ratio, mesh_sigma=mesh_sigma, schema=schema, sep=sep,
        phase_token=phase_token,
    )

    labels, nuclear_points, cytoplasm_tophat, cytoplasm_binary_mask = (
        _segment_image_group(
            segmentation_method=segmentation_method, nir_corr=nir_corr,
            fluor_raws=fluor_raws, fluor_tokens=fluor_tokens,
            cytoplasm_token=cytoplasm_token, tophat_radius_fluor=tophat_radius_fluor,
            min_nucleus_area_px=min_nucleus_area_px, cellpose_model=cellpose_model,
            cellpose_diameter=cellpose_diameter,
            cellpose_flow_threshold=cellpose_flow_threshold,
            cellpose_cellprob_threshold=cellpose_cellprob_threshold,
            cellpose_nuclei_channel=cellpose_nuclei_channel,
        )
    )

    # QC debris gating is NON-DESTRUCTIVE: every segmented cell is kept and
    # quantified rather than zeroed out of the mask (which used to delete the
    # cells' rows entirely). The size/solidity thresholds are recorded in
    # pipeline_info.json (qc_options) and applied as a reversible per-cell flag
    # in the Review QC view, where they can be edited without re-segmenting and
    # compose with manual curation. ``shape_solidity`` is emitted unconditionally
    # below so that gate is editable post-run. See well_viewer/features/qc/.
    if qc_remove_debris:
        log.info("  debris:      gating deferred to Review QC layer "
                 "(non-destructive; thresholds recorded in pipeline_info)")

    smfish_set = set(smfish_tokens or [])
    _write_processed_images(
        save_tophat_images=save_tophat_images, nuclear_path=nuclear_path,
        fluor_paths=fluor_paths, fluor_tokens=fluor_tokens, output_dir=output_dir,
        nir_raw=nir_raw, nir_corr=nir_corr, fluor_tophat=fluor_tophat,
        fluor_corr=fluor_corr, smfish_set=smfish_set,
        segmentation_method=segmentation_method, nuclear_points=nuclear_points,
        cytoplasm_tophat=cytoplasm_tophat,
        cytoplasm_binary_mask=cytoplasm_binary_mask, labels=labels,
        background_method=background_method,
    )

    return _quantify_image_group(
        labels=labels, fluor_tophat=fluor_tophat, fluor_tokens=fluor_tokens,
        nir_corr=nir_corr, nuclear_path=nuclear_path, schema=schema, sep=sep,
        shape_features=shape_features,
    )

# ---------------------------------------------------------------------------
# Directory scan
# ---------------------------------------------------------------------------

# The one definition of "would the pipeline group this file as input?" —
# shared with the remote upload planner, which decides from the same
# question which members survive a rewritten archive. See util/image_exts.
_GROUP_IMAGE_EXTS = set(GROUP_IMAGE_EXTS)


def _grouping_channel_idx(schema: list[str]) -> int | None:
    """The schema's ``channel`` slot index, or None for substring-fallback mode."""
    return schema.index("channel") if "channel" in schema else None


def _is_nuclear_filename(
    name: str, stem: str, nuclear_token: str, channel_idx: int | None, sep: str,
) -> bool:
    """Whether *name* is a nuclear file (audit A1) — schema ``channel`` slot, or
    substring fallback. Shared so dir + zip scanners classify identically."""
    if channel_idx is not None:
        parts = stem.split(sep)
        return channel_idx < len(parts) and parts[channel_idx] == nuclear_token
    return nuclear_token in name


def _derive_fluor_filename(
    name: str, stem: str, suffix: str, nuclear_token: str, fluor_token: str,
    channel_idx: int | None, sep: str,
) -> str:
    """Derive one fluor channel's *filename* from a nuclear name (audit A1);
    ``""`` if the channel slot is out of range. Shared by both scanners."""
    if channel_idx is not None:
        parts = stem.split(sep)
        if channel_idx >= len(parts):
            return ""
        parts[channel_idx] = fluor_token
        return sep.join(parts) + suffix
    return name.replace(nuclear_token, fluor_token)


def _collect_image_groups(
    entries,
    *,
    nuclear_token: str,
    fluor_tokens: list[str],
    channel_idx: int | None,
    sep: str,
    exists,
    on_missing_fluor,
):
    """Core nuclear+fluor grouping shared by the dir and zip scanners (audit A1
    — segmentation depends on both parsing identically).

    *entries* is ``[(name, ctx)]`` — ``name`` the parseable basename, ``ctx``
    opaque per-caller state (nuclear ``Path`` for dir, ``None`` for zip).
    ``exists(ctx, fluor_name)`` tests presence; missing fluors go to
    ``on_missing_fluor(fluor_name)``. Returns ``(groups, incomplete)`` =
    ``([(name, ctx, [fluor_name...])], [(name, ctx)])``.
    """
    groups: list = []
    incomplete: list = []
    for name, ctx in entries:
        p = Path(name)
        stem, suffix = p.stem, p.suffix
        if not _is_nuclear_filename(name, stem, nuclear_token, channel_idx, sep):
            continue
        fluor_names = [
            _derive_fluor_filename(name, stem, suffix, nuclear_token, tok, channel_idx, sep)
            for tok in fluor_tokens
        ]
        if any(not fn for fn in fluor_names):
            incomplete.append((name, ctx))
            continue
        missing = [fn for fn in fluor_names if not exists(ctx, fn)]
        if missing:
            incomplete.append((name, ctx))
            for fn in missing:
                on_missing_fluor(fn)
            continue
        groups.append((name, ctx, fluor_names))
    return groups, incomplete


def find_image_groups_in_zip(
    zip_path: Path,
    nuclear_token: str,
    fluor_tokens: list[str],
    dest_dir: Path,
    schema: list[str] | None = None,
    sep: str = DEFAULT_SEP,
) -> tuple[list[tuple[Path, list[Path]]], set[str]]:
    """
    Discover complete image groups from filenames inside *zip_path*.

    Returns:
      - groups as paths rooted at *dest_dir* (where selected members are later extracted)
      - set of base member names that must be extracted to satisfy those groups
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)
    channel_idx = _grouping_channel_idx(schema)

    by_name: set[str] = set()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            base = Path(member.filename).name
            if not base or base.startswith("."):
                continue
            if Path(base).suffix.lower() not in _GROUP_IMAGE_EXTS:
                continue
            if base in by_name:
                log.warning(
                    "Zip %s contains duplicate basename '%s'; using first occurrence.",
                    zip_path.name,
                    base,
                )
                continue
            by_name.add(base)

    grp, incomplete = _collect_image_groups(
        [(base, None) for base in sorted(by_name)],
        nuclear_token=nuclear_token, fluor_tokens=fluor_tokens,
        channel_idx=channel_idx, sep=sep,
        exists=lambda _ctx, fn: fn in by_name,
        on_missing_fluor=lambda fn: log.warning(
            "  missing fluor file in zip %s: %s", zip_path.name, fn),
    )

    groups: list[tuple[Path, list[Path]]] = []
    needed_members: set[str] = set()
    for base, _ctx, fluor_names in grp:
        needed_members.add(base)
        needed_members.update(fluor_names)
        groups.append((dest_dir / base, [dest_dir / fn for fn in fluor_names]))

    if incomplete:
        log.warning(
            "%d nuclear file(s) skipped in %s due to missing fluorescent channel(s).",
            len(incomplete),
            zip_path.name,
        )

    log.info(
        "Found %d image group(s) in %s  (%d fluor channel(s): %s)",
        len(groups), zip_path.name, len(fluor_tokens), ", ".join(fluor_tokens),
    )
    return groups, needed_members


def find_image_groups(
    input_dir: Path,
    nuclear_token: str,
    fluor_tokens: list[str],
    schema: list[str] | None = None,
    sep: str = DEFAULT_SEP,
) -> list[tuple[Path, list[Path]]]:
    """
    Recursively scan *input_dir* for nuclear-channel files and locate all
    fluorescent-channel counterparts for each.

    Returns a list of (nuclear_path, [fluor_path, ...]) tuples where the
    nuclear file and every fluorescent file exist on disk.  Groups where any
    fluorescent channel is missing are skipped with a warning.

    Channel identification uses the schema's "channel" slot for position-aware
    matching.  Falls back to substring replacement when no "channel" slot exists.
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)
    channel_idx = _grouping_channel_idx(schema)

    all_images = [
        p for p in sorted(input_dir.rglob("*"))
        if p.is_file()
        and not p.name.startswith(".")
        and not any(part.startswith(".") for part in p.parts)
        and p.suffix.lower() in _GROUP_IMAGE_EXTS
    ]
    all_set = set(all_images)

    grp, incomplete = _collect_image_groups(
        [(p.name, p) for p in all_images],
        nuclear_token=nuclear_token, fluor_tokens=fluor_tokens,
        channel_idx=channel_idx, sep=sep,
        exists=lambda ctx, fn: (ctx.parent / fn) in all_set,
        on_missing_fluor=lambda fn: log.warning("  missing fluor file: %s", fn),
    )

    groups: list[tuple[Path, list[Path]]] = [
        (ctx, [ctx.parent / fn for fn in fluor_names])
        for _name, ctx, fluor_names in grp
    ]

    if incomplete:
        log.warning(
            "%d nuclear file(s) skipped due to missing fluorescent channel(s):",
            len(incomplete),
        )
        for _name, ctx in incomplete:
            log.warning("  %s", ctx.name)

    log.info(
        "Found %d image group(s) in %s  (%d fluor channel(s): %s)",
        len(groups), input_dir, len(fluor_tokens), ", ".join(fluor_tokens),
    )
    return groups


# Backward-compatible alias kept for any external callers.
# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Worker initialiser
# ---------------------------------------------------------------------------

def _ensure_pkg_resources_shim() -> None:
    """Install a minimal ``pkg_resources`` stand-in if setuptools' real
    one is missing.

    setuptools >= 80 and recent Python builds drop ``pkg_resources``,
    but stardist's ``bioimageio_utils`` still does
    ``from pkg_resources import get_distribution`` at import time. Back
    that single call with ``importlib.metadata`` so the worker keeps
    loading even when setuptools no longer ships the legacy module.
    """
    import sys as _sys
    try:
        import pkg_resources  # noqa: F401
        return
    except Exception:
        swallowed()  # was: pass (silent except)
    import types
    from importlib import metadata as _md

    class _DistInfo:
        def __init__(self, version: str) -> None:
            self.version = version

    def _get_distribution(name: str) -> _DistInfo:
        try:
            return _DistInfo(_md.version(name))
        except _md.PackageNotFoundError as exc:
            raise Exception(f"DistributionNotFound: {exc}") from exc

    shim = types.ModuleType("pkg_resources")
    shim.get_distribution = _get_distribution           # type: ignore[attr-defined]
    shim.DistributionNotFound = Exception               # type: ignore[attr-defined]
    shim.VersionConflict = Exception                    # type: ignore[attr-defined]
    shim.parse_version = lambda v: v                    # type: ignore[attr-defined]
    _sys.modules["pkg_resources"] = shim


# Substrings in an exception message that indicate a corrupt / truncated cached
# model file (HDF5 weights or its download archive) rather than a transient
# network / SSL problem — so we know it is safe to purge the cache and re-fetch.
_CORRUPT_MODEL_MARKERS = (
    "truncated file",
    "unable to synchronously open file",
    "file signature not found",
    "bad object header",
    "not a valid hdf5",
    "unable to open file",
    "bad magic number",         # truncated zip archive
    "file is not a zip file",
)


def _purge_stardist_cache() -> list[str]:
    """Remove cached StarDist pretrained-model files so the next
    ``from_pretrained`` re-downloads them.

    ``StarDist2D.from_pretrained`` caches the model under the keras cache
    (``$KERAS_HOME`` or ``~/.keras/models/…``) as ``weights_best.h5`` +
    ``config.json``. A run interrupted mid-download (e.g. the worker was killed,
    or an earlier failure aborted the pool) can leave a **truncated**
    ``weights_best.h5``; the next run reuses it and h5py dies with
    ``OSError: Unable to synchronously open file (truncated file: eof = …)``.

    Deletes any model folder containing a StarDist weights file (and stray
    ``*.h5`` / partial download archives) under each keras cache root. Returns
    the paths removed, for logging.
    """
    roots: list[Path] = []
    kh = os.environ.get("KERAS_HOME")
    if kh:
        roots.append(Path(kh))
    roots.append(Path(os.path.expanduser("~")) / ".keras")
    removed: list[str] = []
    seen: set[str] = set()
    for models in (r / "models" for r in roots):
        if not models.is_dir():
            continue
        targets: set[Path] = set()
        for p in models.rglob("*"):
            if not p.is_file():
                continue
            low = p.name.lower()
            if low == "weights_best.h5" or low.endswith("weights_best.h5"):
                targets.add(p.parent)                       # whole model folder
            elif low.endswith((".h5", ".zip", ".part", ".download", ".tmp")):
                targets.add(p)                              # stray / partial file
        for t in targets:
            key = str(t)
            if key in seen:
                continue
            seen.add(key)
            try:
                if t.is_dir():
                    shutil.rmtree(t, ignore_errors=True)
                else:
                    t.unlink()
                removed.append(key)
            except OSError:
                pass
    return removed


# Cross-process download serialisation (see _stardist_download_lock).
_STARDIST_LOCK_TIMEOUT = 120.0   # s; max wait on a *live* download before going unlocked
_STARDIST_LOCK_STALE   = 180.0   # s; reclaim a no-live-PID lock older than this
_STARDIST_LOCK_POLL    = 0.25    # s between acquisition attempts


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a local PID (the download workers all share
    the host). Unknown/unsupported (e.g. Windows ``os.kill(pid, 0)``) → assume
    alive, so we never reclaim a lock we can't prove is dead."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False                       # definitively gone
    except PermissionError:
        return True                        # exists, owned by another user
    except OSError:
        return True                        # can't tell → assume alive (safe)
    return True


def _stardist_lock_is_dead(lockdir: Path) -> bool:
    """A leaked lock is reclaimable when its recorded holder PID(s) are no longer
    running (instant — the crashed/killed-worker case), or — no PID recorded, a
    reused PID, or a hung holder — it is older than the stale window."""
    pids: list[int] = []
    try:
        for p in lockdir.iterdir():
            if p.name.startswith("pid.") and p.name[4:].isdigit():
                pids.append(int(p.name[4:]))
    except OSError:
        return False
    if pids and not any(_pid_alive(pid) for pid in pids):
        return True                        # all recorded holders dead → reclaim now
    try:
        return (time.time() - lockdir.stat().st_mtime) > _STARDIST_LOCK_STALE
    except OSError:
        return False


def _stardist_cache_models_dir() -> Path:
    """Primary keras model-cache dir — ``$KERAS_HOME`` else ``~/.keras``, then
    ``/models`` — where ``from_pretrained`` downloads the weights. Created if
    missing. Mirrors the root logic in :func:`_purge_stardist_cache`."""
    root = os.environ.get("KERAS_HOME") or os.path.join(os.path.expanduser("~"), ".keras")
    models = Path(root) / "models"
    models.mkdir(parents=True, exist_ok=True)
    return models


@contextlib.contextmanager
def _stardist_download_lock(timeout: float | None = None):
    """Cross-process advisory lock serialising the StarDist model download/purge.

    All spawn-pool workers run ``_worker_init`` → ``from_pretrained`` at once; on
    a cold cache they download the same file concurrently and truncate each
    other (the recurring "cache corrupt" failure), and an unguarded purge can
    delete another worker's in-flight download. Atomic ``os.mkdir`` of a lock
    directory is the primitive: exactly one process wins and downloads; the rest
    block, then load a *warm* cache. Degrades safely — on timeout, an unusable
    cache dir, or any lock error, it proceeds WITHOUT the lock rather than fail
    the run, and reclaims a stale lock left by a dead holder.
    """
    if timeout is None:                 # resolve at call time so tests can patch it
        timeout = _STARDIST_LOCK_TIMEOUT
    try:
        lockdir = _stardist_cache_models_dir() / ".allwell_stardist_download.lock"
    except OSError:
        yield                       # cache dir unusable — run unsynchronised
        return
    deadline = time.monotonic() + timeout
    held = False
    while True:
        try:
            os.mkdir(lockdir)
            held = True
            # Record our PID so other workers can reclaim instantly if we die.
            try:
                (lockdir / f"pid.{os.getpid()}").touch()
            except OSError:
                pass
            break
        except FileExistsError:
            # Reclaim immediately if the holder is dead (PID gone) — the leaked-
            # lock-after-crash case — so a rerun never stalls for minutes; a
            # genuinely live download is waited on up to the (short) timeout.
            if _stardist_lock_is_dead(lockdir):
                shutil.rmtree(lockdir, ignore_errors=True)
                if lockdir.exists():            # couldn't remove → don't spin
                    log.warning("StarDist download lock could not be reclaimed — "
                                "proceeding without it.")
                    break
                continue
            if time.monotonic() >= deadline:
                log.warning("StarDist download lock held by a live process "
                            ">%.0fs — proceeding without it.", timeout)
                break
            time.sleep(_STARDIST_LOCK_POLL)
        except OSError:
            break                   # can't create the lock (perms/fs) — unsynchronised
    try:
        yield
    finally:
        if held:
            shutil.rmtree(lockdir, ignore_errors=True)


def _load_stardist_pretrained(StarDist2D, name: str = "2D_versatile_fluo"):
    """Load a StarDist pretrained model, recovering once from a corrupt cache.

    If the first load fails because the cached weights / archive is truncated
    (see :data:`_CORRUPT_MODEL_MARKERS`) — the classic symptom of a previous
    run that died mid-download — purge the cache and re-download exactly once.
    Any other error (and a second failure) propagates unchanged.

    The whole body runs under :func:`_stardist_download_lock` so concurrent
    workers serialise: only one downloads (the rest hit a warm cache), and the
    purge+retry can't delete another worker's in-flight download.
    """
    with _stardist_download_lock():
        try:
            return StarDist2D.from_pretrained(name)
        except Exception as exc:
            if not any(m in str(exc).lower() for m in _CORRUPT_MODEL_MARKERS):
                raise
            removed = _purge_stardist_cache()
            log.warning(
                "StarDist model cache was corrupt (%s: %s) — purged %s and "
                "re-downloading once.",
                exc.__class__.__name__, exc, removed or "(nothing found to remove)")
            return StarDist2D.from_pretrained(name)


def _worker_init(force_cpu: bool = False, threads_per_worker: int = 0,
                 segmentation_method: str = DEFAULT_SEGMENTATION_METHOD,
                 stardist_params: dict | None = None) -> None:
    """
    Called once in each worker process before any image pairs are processed.

    *force_cpu*          When True, forces StarDist onto CPU regardless of
                         platform.  When False, the effective device is chosen
                         automatically:
                           macOS  -> Metal GPU via tensorflow-metal if installed,
                                     otherwise CPU.
                           Other  -> CPU only (CUDA available if a CUDA-enabled
                                     TF build is installed and force_cpu=False).
    *threads_per_worker* CPU threads per worker for TF/BLAS inference.
    *stardist_params*    Keyword dict for :class:`pipeline.stardist.StarDistParams`
                         (prob/nms thresholds + the tiling cap). None = defaults.

    Platform logic
    --------------
    On macOS, tensorflow-metal registers Metal as a GPU device automatically.
    Calling tf.config.set_visible_devices([], "GPU") disables Metal too (not
    just CUDA) so we must NOT call it on macOS unless --cpu_only is set.
    On all other platforms we default to CPU to avoid unexpected CUDA usage.
    """
    import sys as _sys

    from pipeline.suspend import install_handlers as _install_suspend
    _install_suspend()          # a group signal reaches every worker
    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

    # Determine effective device policy before any TF import.
    _on_mac        = _sys.platform == "darwin"
    _effective_cpu = force_cpu or (not _on_mac)

    if threads_per_worker > 0:
        # Pin CPU thread pools before any library imports.
        t = str(threads_per_worker)
        os.environ["OMP_NUM_THREADS"]        = t
        os.environ["OPENBLAS_NUM_THREADS"]   = t
        os.environ["MKL_NUM_THREADS"]        = t
        os.environ["NUMEXPR_NUM_THREADS"]    = t
        os.environ["TF_NUM_INTRAOP_THREADS"] = t
        os.environ["TF_NUM_INTEROP_THREADS"] = t

    # Raise the per-worker open-file ceiling. macOS's default soft limit
    # (256) is easily exceeded by TF + StarDist + tifffile in a well with
    # many FOVs; the resulting EMFILE typically surfaces as a confusing
    # OSError downstream. We push the soft limit up to whatever the kernel
    # already permits (hard limit, capped at 4096) without requiring root.
    try:
        import resource as _resource
        _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
        _target = max(_soft, min(_hard, 4096))
        if _target > _soft:
            _resource.setrlimit(_resource.RLIMIT_NOFILE, (_target, _hard))
    except (ImportError, ValueError, OSError):
        # resource is POSIX-only; ignore on platforms / environments where
        # the bump isn't permitted.
        pass

    # Restore stdout/stderr before configuring logging — the worker may
    # have inherited a windowed-PyInstaller-bootloader-detached
    # sys.stdout / sys.stderr (see ``_ensure_pipeline_io_ready`` for the
    # full rationale). Otherwise ``logging.basicConfig`` binds its
    # StreamHandler to ``None`` and every log line vanishes.
    _ensure_pipeline_io_ready()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  [worker %(process)d]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Belt-and-braces: even after the shim above, an existing handler
    # bound to ``None`` (from ``logging.basicConfig`` having run at
    # module import before we got a chance to repair sys.stderr) needs
    # to be repointed at the live stream so the worker's logs actually
    # reach the parent's pipe.
    try:
        import sys as _sys2
        if _sys2.stderr is not None and not getattr(_sys2.stderr, "closed", False):
            _sys2.stderr.write("")
            _sys2.stderr.flush()
        else:
            raise OSError("sys.stderr is None or closed")
    except (OSError, AttributeError):
        try:
            import os as _os2
            _os2.dup2(1, 2)
            _sys2.stderr = _sys2.stdout
            for _h in list(logging.getLogger().handlers):
                if isinstance(_h, logging.StreamHandler):
                    _h.stream = _sys2.stdout
        except Exception:
            swallowed()  # was: pass (silent except)

    # ── Device note for logging ────────────────────────────────────────────────
    if force_cpu:
        device_note = "CPU-only (--cpu_only)"
    elif _on_mac:
        device_note = "Metal GPU (macOS) if tensorflow-metal installed, else CPU"
    else:
        device_note = "CPU-only (non-macOS default)"
    thr_note = (f"{threads_per_worker} TF thread(s)" if threads_per_worker > 0
                else "TF default threads")

    # Install the per-run StarDist settings (detection thresholds + the tiling
    # cap) before any predict.
    _stardist.set_params(_stardist.StarDistParams(**(stardist_params or {})))

    # Label-free methods (cellpose / classical_threshold) don't use the StarDist
    # TF model — skip the (heavy, network-touching) load entirely. Cellpose
    # lazy-loads its torch model on first use; classical needs none.
    if segmentation_method not in _STARDIST_METHODS:
        log.info("Worker %d ready (%s; label-free '%s' — StarDist not loaded).",
                 os.getpid(), device_note, segmentation_method)
        return

    log.info("Worker %d loading StarDist (%s, %s).", os.getpid(), device_note, thr_note)

    # Import StarDist and its TF dependency HERE, after env vars are set.
    # This is the first point in the worker where TF is allowed to initialise.
    # stardist's bioimageio_utils imports ``pkg_resources``, which was
    # removed from setuptools >= 80 / Python 3.12+. Install a tiny shim
    # backed by importlib.metadata before stardist loads so the worker
    # doesn't crash with ``ModuleNotFoundError: pkg_resources``.
    _ensure_pkg_resources_shim()
    # Frozen builds only: silence harmless-for-inference TF summary / AutoGraph
    # noise before TF is first imported (see pipeline.stardist for the details).
    _stardist.quiet_frozen_tf_logging()
    from csbdeep.utils import normalize as _normalize   # noqa: F401 — triggers TF init
    from stardist.models import StarDist2D as _StarDist2D

    # Disable GPU on non-Mac platforms (or when --cpu_only is set).
    # On macOS we leave GPU visible so tensorflow-metal can use Metal.
    if _effective_cpu:
        import tensorflow as tf
        tf.config.set_visible_devices([], "GPU")

    _t0 = time.perf_counter()
    # from_pretrained downloads the model weights via urllib if they are not
    # already cached locally (~/.keras/models/).  On machines where the SSL
    # certificate store cannot be verified (e.g. corporate proxies, some HPC
    # clusters) this raises an SSL error.  The override below disables
    # certificate verification for this one call only; the original context
    # factory is restored immediately in the finally block so no other network
    # code in the worker is affected. _load_stardist_pretrained additionally
    # recovers from a truncated cache left by an interrupted earlier run (it
    # purges the corrupt weights and re-downloads once).
    import ssl as _ssl
    _orig_ssl_ctx = _ssl._create_default_https_context
    _ssl._create_default_https_context = _ssl._create_unverified_context
    try:
        _stardist.set_model(_load_stardist_pretrained(_StarDist2D, "2D_versatile_fluo"))
    finally:
        _ssl._create_default_https_context = _orig_ssl_ctx
    log.info("Worker %d model loaded in %.1f s.", os.getpid(), time.perf_counter() - _t0)

# ---------------------------------------------------------------------------
# Well-level task functions (run inside worker processes)
# ---------------------------------------------------------------------------

# Consecutive OSErrors that mean the storage is gone rather than flaky.
# _safe_imwrite already retried each write five times, so three groups = 15 tries.
_MAX_CONSECUTIVE_IO_FAILURES = 3


# ── Per-FOV checkpoint helpers ──────────────────────────────────────────────
#
# A worker killed mid-well should pick up where it left off rather than redoing
# every FOV. After each FOV finishes (records computed, QC TIFs written) the
# worker writes <tmp_images>/.fov_<stem>.records.json atomically, then touches
# <tmp_images>/.fov_<stem>.done — the marker last, so a half-written records
# file never reads as done. On startup the worker scans <tmp_images> for valid
# pairs and loads those FOVs from disk instead of reprocessing them; records
# with no marker are redone and orphan __aw_tmp_* files deleted. A successful
# end-of-well removes the whole dir in the usual finally-block: checkpoints are
# transient and never survive a well that finished. Recovery cost for a killed
# run drops from "redo every FOV in the well" to "redo the in-flight FOV".

# Schema version stored in each records.json. Bumped if the records dict
# layout changes — older checkpoints with a different version are discarded
# on load so a freshly-built pipeline never reads stale records that would
# poison the per-well CSV.
_FOV_CHECKPOINT_SCHEMA = 1


def _json_safe(records: list[dict]) -> list[dict]:
    """Cast numpy scalars in record dicts to plain Python types so the
    records survive a JSON round-trip. Mutates a copy, not the input."""
    out: list[dict] = []
    for r in records:
        clean: dict = {}
        for k, v in r.items():
            # numpy scalar → Python builtin via .item(); everything else
            # passes through. Avoids the ``import numpy`` cost in the
            # common case where the records are already clean.
            item = getattr(v, "item", None)
            clean[k] = item() if callable(item) and v is not None else v
        out.append(clean)
    return out


def _fov_stem(nuclear_path: Path) -> str:
    """Stable per-FOV identifier within a well: the source file's stem.
    process_microscopy already keys QC filenames on this stem (see
    ``_qc_output_path`` around line 1305), so any QC file for this FOV
    starts with ``stem``."""
    return nuclear_path.stem


def _checkpoint_paths(tmp_images: Path, fov_id: str) -> tuple[Path, Path]:
    """Return ``(records_json, done_marker)`` for a given FOV id."""
    return (
        tmp_images / f".fov_{fov_id}.records.json",
        tmp_images / f".fov_{fov_id}.done",
    )


def _load_fov_checkpoint(tmp_images: Path, fov_id: str) -> list[dict] | None:
    """Return cached records for ``fov_id``, or None if no valid
    checkpoint exists.

    Treats a missing marker, a missing records file, an unreadable
    records file, or a schema-version mismatch all as "no checkpoint" —
    in every case the caller will reprocess the FOV and overwrite the
    on-disk state.
    """
    records_file, marker = _checkpoint_paths(tmp_images, fov_id)
    if not (marker.exists() and records_file.exists()):
        return None
    try:
        payload = json.loads(records_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != _FOV_CHECKPOINT_SCHEMA:
        return None
    records = payload.get("records")
    if not isinstance(records, list):
        return None
    return records


def _save_fov_checkpoint(
    tmp_images: Path, fov_id: str, records: list[dict],
) -> None:
    """Atomically persist ``records`` and create the ``.done`` marker.

    Writes records.json first, fsync-then-rename, then touches the marker
    — so a process killed between the two writes leaves NO marker and
    the next run reprocesses the FOV (rather than reading half-written
    records).
    """
    records_file, marker = _checkpoint_paths(tmp_images, fov_id)
    tmp = records_file.with_suffix(".records.json.tmp")
    payload = {"schema": _FOV_CHECKPOINT_SCHEMA, "records": _json_safe(records)}
    text = json.dumps(payload)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            # fsync isn't supported on every filesystem (e.g. some network
            # mounts and external volumes on macOS, where it raises OSError
            # "Invalid argument"/"Inappropriate ioctl for device"). The replace
            # below is still atomic without it. Matches the guarded fsync in
            # pipeline/io.py and well_viewer/persistence/_io.py.
            pass
    tmp.replace(records_file)
    # The marker is the atomicity boundary — present iff records_file
    # is on disk and complete.
    marker.touch()


def _prune_scratch_files(tmp_images: Path) -> None:
    """Remove ``__aw_tmp_*`` partials from a previous killed run.

    These come from ``_safe_imwrite`` writes that didn't complete. The
    zip step already filters them out, but leaving them on disk wastes
    space and can confuse a human inspecting the checkpoint state.
    """
    if not tmp_images.is_dir():
        return
    for p in tmp_images.iterdir():
        if p.name.startswith("__aw_tmp_"):
            try:
                p.unlink()
            except OSError:
                pass


def _process_groups_in_worker(
    groups: list[tuple[Path, list[Path]]],
    shared_kwargs: dict,
    well_label: str,
    fov_threads: int = 1,
    errors_dir: Path | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Process all image groups for one well within a single worker process.

    Each group is (nuclear_path, [fluor_path, ...]).  StarDist is already
    loaded by _worker_init, so the model is reused across every group.

    *fov_threads* controls concurrent FOV processing via ThreadPoolExecutor.
    fov_threads=1 (default) is safe for any backend; >1 benefits multi-core CPU.

    *errors_dir* is the persistent dataset output dir (NOT the ``tmp_images``
    staging dir); a per-FOV-group failure trace is written under
    ``<errors_dir>/errors/`` so "why did this timepoint fail?" has a durable
    answer even when the live log is muted. When None, no trace file is written.

    Returns (records, failed_names).

    v5.2.0: each FOV that completes successfully is persisted as a small
    JSON checkpoint inside ``output_dir`` (the well's ``tmp_images``).
    A subsequent invocation against the same partly-finished well loads
    those checkpoints and skips the corresponding FOVs entirely.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

    fov_threads = shared_kwargs.pop("_fov_threads", fov_threads)
    fov_threads = max(1, min(fov_threads, len(groups), os.cpu_count() or 1))
    kwargs = {k: v for k, v in shared_kwargs.items() if not k.startswith("_")}
    tmp_images: Path = kwargs["output_dir"]
    _prune_scratch_files(tmp_images)

    all_records: list[dict] = []
    failed:      list[str]  = []

    def _record_fov_failure(nuclear_path: Path, exc: BaseException) -> None:
        """Persist a per-FOV traceback + log it as an error the GUI surfaces."""
        trace_path = None
        if errors_dir is not None:
            trace_path = _dump_fov_failure_trace(
                errors_dir, well_label, _fov_stem(nuclear_path), exc)
        # Include the exception TYPE so the one-liner is self-explanatory and
        # classify_log_line tags it ERROR (and point at the persisted trace).
        log.error(
            "FAILED %s (%s: %s); FOV trace -> %s",
            nuclear_path.name, type(exc).__name__, exc,
            trace_path if trace_path is not None else "<unwritable>",
            exc_info=True,
        )
        failed.append(nuclear_path.name)
    # Per-well FOV-group progress, tagged with the well so the GUI can attribute
    # it correctly even when several wells process in parallel. One "image group"
    # = one FOV × timepoint.
    _fov_total = len(groups)
    _fov_done = 0
    log.info("Well %s — %d FOV-group(s)", well_label, _fov_total)

    def _run_group(nuclear_path: Path, fluor_paths: list[Path]) -> list[dict]:
        _t0 = time.perf_counter()
        recs = process_image_group(nuclear_path, fluor_paths, **kwargs)
        log.info("FOV %s processed in %.1f s (%d nuclei)",
                 nuclear_path.name, time.perf_counter() - _t0, len(recs))
        for r in recs:
            r["well"] = well_label
        # Persist this FOV's records so a kill before end-of-well
        # doesn't lose the work. The marker write is the last step in
        # _save_fov_checkpoint so a half-written records.json never
        # appears "done" to the next invocation.
        try:
            _save_fov_checkpoint(tmp_images, _fov_stem(nuclear_path), recs)
        except OSError as ckpt_exc:
            log.warning(
                "FOV %s — could not write checkpoint (%s); FOV will be "
                "reprocessed on restart.", nuclear_path.name, ckpt_exc,
            )
        return recs

    def _process_or_resume(
        nuclear_path: Path, fluor_paths: list[Path],
    ) -> list[dict]:
        """Load cached records if a checkpoint exists, else process the
        FOV from scratch."""
        fov_id = _fov_stem(nuclear_path)
        cached = _load_fov_checkpoint(tmp_images, fov_id)
        if cached is not None:
            log.info(
                "FOV %s — resumed from checkpoint (%d records)",
                nuclear_path.name, len(cached),
            )
            return cached
        return _run_group(nuclear_path, fluor_paths)

    # Storage that has run out does not recover mid-well. Without this a full
    # scratch volume is rediscovered once per FOV-group — every remaining group
    # fails the same way, each writing a traceback, and the run spends hours
    # producing complete-looking output for wells it has already gutted.
    _consecutive_io_failures = 0
    aborted_reason = ""

    def _note_failure(exc: BaseException) -> bool:
        """Count a failure; True when the well should stop attempting groups."""
        nonlocal _consecutive_io_failures, aborted_reason
        _consecutive_io_failures = (
            _consecutive_io_failures + 1 if isinstance(exc, OSError) else 0)
        if _consecutive_io_failures < _MAX_CONSECUTIVE_IO_FAILURES:
            return False
        aborted_reason = (
            f"{_consecutive_io_failures} consecutive I/O failures — the storage "
            f"this well writes to is not usable")
        return True

    if fov_threads == 1 or len(groups) == 1:
        for nuclear_path, fluor_paths in groups:
            if _suspend.is_suspended():
                break            # not attempted is not the same as failed
            if aborted_reason:
                failed.append(nuclear_path.name)
                continue
            try:
                all_records.extend(_process_or_resume(nuclear_path, fluor_paths))
                _consecutive_io_failures = 0
            except Exception as exc:
                _record_fov_failure(nuclear_path, exc)
                if _note_failure(exc):
                    log.error(
                        "Well %s — ABORTING after %s. The remaining FOV-group(s) "
                        "are not attempted; re-run with --incremental once the "
                        "storage problem is fixed.", well_label, aborted_reason,
                    )
            _fov_done += 1
            log.info("Well %s — FOV %d/%d done", well_label, _fov_done, _fov_total)
    else:
        _abort_logged = False
        with ThreadPoolExecutor(max_workers=fov_threads) as tex:
            future_map = {
                tex.submit(_process_or_resume, nuc, fluors): nuc
                for nuc, fluors in groups if not _suspend.is_suspended()
            }
            for fut in _as_completed(future_map):
                nuclear_path = future_map[fut]
                try:
                    all_records.extend(fut.result())
                    _consecutive_io_failures = 0
                except Exception as exc:
                    _record_fov_failure(nuclear_path, exc)
                    if _note_failure(exc) and not _abort_logged:
                        _abort_logged = True
                        log.error(
                            "Well %s — storage failure: %s. The in-flight "
                            "FOV-group(s) will finish or fail on their own; "
                            "re-run with --incremental once it is fixed.",
                            well_label, aborted_reason,
                        )
                _fov_done += 1
                log.info("Well %s — FOV %d/%d done", well_label, _fov_done, _fov_total)

    if failed:
        # The well still writes a CSV + out.zip from the FOV-groups that DID
        # succeed, so it will look complete on the plate map. Shout that the
        # output is partial and name the casualties, otherwise a per-FOV failure
        # (e.g. a transient write error) silently drops FOV-groups from the well.
        # A plain re-run SKIPS this well (its CSV/zip exist); use --incremental to
        # reprocess only the missing FOV-groups (they are absent from the CSV
        # done-set) or --force to redo the whole well.
        _err_hint = (
            f" Per-FOV tracebacks in {errors_dir / 'errors'}."
            if errors_dir is not None else ""
        )
        log.warning(
            "Well %s — PARTIAL: %d of %d FOV-group(s) FAILED (%s). The per-well "
            "CSV / out.zip cover only the %d successful FOV-group(s). Re-run with "
            "--incremental to recover the missing ones.%s",
            well_label, len(failed), _fov_total, ", ".join(failed),
            _fov_total - len(failed), _err_hint,
        )

    return all_records, failed


def process_well_zip_task(
    well_label: str,
    zip_path: Path,
    output_dir: Path,
    shared_kwargs_template: dict,
    nuclear_token: str,
    fluor_tokens: list[str],
    csv_prefix: str,
    schema: list[str] | None = None,
    sep: str = DEFAULT_SEP,
    incremental: bool = False,
    incremental_prior_dir: Path | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Top-level picklable task: full lifecycle for one zipped well.

    Runs inside a worker process (StarDist already loaded by _worker_init).
    1. Discover complete image groups from zip member names, then extract only
       required channel files to a temporary directory.
    2. Discover image groups (nuclear + all fluor channels) and process them.
    3. Write the per-well CSV.
    4. Compress output images to <output_dir>/<well>_out.zip.
    5. Remove temporary directories.

    When *incremental* is set and a prior per-well CSV exists, only the
    FOV-groups whose ``(fov, timepoint)`` is not already in that CSV are
    processed, and the new rows / images are merged into the existing CSV and
    ``<well>_out.zip`` (rather than overwritten). A new well — no prior CSV —
    processes fully. *incremental_prior_dir* is where the prior CSV is read from
    (defaults to *output_dir*; remote runs point it at the uploaded sidecar).

    Returns (records, failed_names).
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)
    log.info("Well %s — starting (pid %d)", well_label, os.getpid())
    tmp_extract = scratch_base_for(output_dir) / f"_tmp_extract_{well_label}"
    tmp_images  = scratch_base_for(output_dir) / f"_tmp_images_{well_label}"

    # tmp_extract is always per-run scratch: extraction can re-derive
    # itself from the source zip in seconds, no checkpoint value.
    if tmp_extract.exists():
        log.warning("Well %s: removing stale tmp dir %s", well_label, tmp_extract.name)
        remove_directory(tmp_extract)

    # tmp_images carries per-FOV checkpoints (v5.2.0). If it holds at
    # least one ``.fov_*.done`` marker, treat it as a partial-progress
    # dir for this well and keep its contents — _process_groups_in_worker
    # will pick up where the previous attempt stopped. Otherwise wipe
    # it (matches pre-v5.2.0 behavior on a clean restart).
    if tmp_images.exists():
        has_checkpoint = any(tmp_images.glob(".fov_*.done"))
        if has_checkpoint:
            log.info(
                "Well %s: resuming from %d FOV checkpoint(s) in %s",
                well_label,
                sum(1 for _ in tmp_images.glob(".fov_*.done")),
                tmp_images.name,
            )
        else:
            log.warning("Well %s: removing stale tmp dir %s", well_label, tmp_images.name)
            remove_directory(tmp_images)

    try:
        try:
            tmp_extract.mkdir(parents=True, exist_ok=True)
            tmp_images.mkdir(parents=True,  exist_ok=True)

            groups, needed_members = find_image_groups_in_zip(
                zip_path,
                nuclear_token,
                fluor_tokens,
                tmp_extract,
                schema=schema,
                sep=sep,
            )
            if not groups:
                log.warning("Well %s: no image groups found in %s.", well_label, zip_path.name)
                record_well_completeness(output_dir, well_label, 0, None, [])
                return [], []

            # How many FOV-groups this well's INPUT holds — captured before the
            # incremental filter narrows ``groups`` to the ones still missing,
            # because the completeness record compares the well's output against
            # its input, not against whatever this particular run attempted.
            groups_in_input = len(groups)
            safe_well = _safe_well(well_label)
            csv_path  = output_dir / f"{csv_prefix}_{safe_well}.csv"
            out_zip   = output_dir / f"{well_label}_out.zip"

            if incremental:
                prior = incremental_prior_dir or output_dir
                # CSV is the primary done-set; a processed well can have its
                # <well>_out.zip without a sibling CSV (remote-only materialise /
                # lost CSV) → recover done keys from the outputs too.
                done, csv_done = incremental_done_fov_keys(
                    prior, well_label, csv_prefix=csv_prefix,
                    safe_well=safe_well, schema=schema, sep=sep)
                if done and not csv_done:
                    log.info("Well %s — no prior CSV; recovered %d done FOV-group(s) "
                             "from processed outputs.", well_label, len(done))
                if done:
                    new_groups = []
                    for nuc, fl in groups:
                        meta = parse_filename(nuc, schema=schema, sep=sep)
                        if (meta["fov"], meta["timepoint"]) not in done:
                            new_groups.append((nuc, fl))
                    if not new_groups:
                        log.info("Well %s — up to date (no new FOVs).", well_label)
                        return [], []
                    log.info("Well %s — merging %d new FOV(s).", well_label, len(new_groups))
                    groups = new_groups
                    needed_members = set()
                    for nuc, fl in groups:
                        needed_members.add(nuc.name)
                        for f in fl:
                            needed_members.add(f.name)
                # else: no prior CSV or out.zip → new well → process all (full).

            n_extracted = extract_zip(zip_path, tmp_extract, members_to_extract=needed_members)
            log.info(
                "Well %s: extracted %d/%d required image file(s).",
                well_label,
                n_extracted,
                len(needed_members),
            )

            kwargs = {**shared_kwargs_template, "output_dir": tmp_images}
            records, failed = _process_groups_in_worker(
                groups, kwargs, well_label, errors_dir=output_dir)

            # The extracted inputs are dead weight from here on, and what
            # follows — building <well>_out.zip beside a staging dir that still
            # holds every intermediate — is this well's peak disk footprint.
            # Reclaiming now is the difference between fitting and not on the
            # usual single-volume setup. The finally block still removes it (it
            # has to cover the failure paths) and is a no-op once this has run.
            remove_directory(tmp_extract)

            if incremental:
                if records:
                    added = merge_records_into_csv(csv_path, records)
                    log.info("Well %-12s -> %s  (+%d row(s), merged)",
                             well_label, csv_path.name, added)
                else:
                    log.warning("Well %s: no records produced; CSV not updated.", well_label)
                record_well_completeness(
                    output_dir, well_label, groups_in_input, csv_path, failed)
                n_compressed = merge_images_into_zip(out_zip, tmp_images)
                log.info("Well %s: merged %d new image(s) -> %s",
                         well_label, n_compressed, out_zip.name)
            else:
                if records:
                    _write_single_csv(records, csv_path)
                    log.info("Well %-12s -> %s  (%d rows)", well_label, csv_path.name, len(records))
                else:
                    log.warning("Well %s: no records produced; CSV not written.", well_label)
                record_well_completeness(
                    output_dir, well_label, groups_in_input, csv_path, failed)

                n_compressed = compress_images_to_zip(tmp_images, out_zip)
                log.info("Well %s: %d image(s) -> %s", well_label, n_compressed, out_zip.name)

            # Mark the well as functionally complete *before* the
            # finally-block cleanup. On slow / unreliable storage (NAS,
            # network mounts) the subsequent ``remove_directory`` calls
            # can hang for minutes, which used to delay the well_done
            # event the GUI reads from "temporary directories removed".
            # All user-facing artefacts (CSV + out_zip) are already
            # written at this point, so the well *is* done — the
            # cleanup is internal housekeeping.
            log.info("Well %s — done.", well_label)
            return records, failed
        except BaseException as exc:
            trace_path = _dump_well_failure_trace(output_dir, well_label, exc)
            if trace_path is not None:
                log.error(
                    "Well %s failed (%s: %s); worker trace written to %s",
                    well_label, type(exc).__name__, exc, trace_path,
                    exc_info=True,
                )
            else:
                log.error(
                    "Well %s failed (%s: %s); could not write worker trace file",
                    well_label, type(exc).__name__, exc,
                    exc_info=True,
                )
            raise

    finally:
        remove_directory(tmp_extract)
        remove_directory(tmp_images)
        log.info("Well %s — temporary directories removed.", well_label)


def process_well_folder_task(
    well_label: str,
    well_folder: Path,
    output_dir: Path,
    shared_kwargs_template: dict,
    nuclear_token: str,
    fluor_tokens: list[str],
    csv_prefix: str,
    schema: list[str] | None = None,
    sep: str = DEFAULT_SEP,
    incremental: bool = False,
    incremental_prior_dir: Path | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Top-level picklable task: full lifecycle for one uncompressed well folder.

    Runs inside a worker process (StarDist already loaded by _worker_init).
    1. Discover image groups directly from *well_folder*.
    2. Process groups, writing QC images to a temporary staging directory.
    3. Write per-well CSV to <output_dir>/<csv_prefix>_<well>.csv.
    4. Rename staging directory to <output_dir>/<well_label>/.
    5. Compress input/output well folders to per-well zip files (always on;
       the pipeline only ever emits <well>.zip / <well>_out.zip).

    When *incremental* is set and a prior per-well CSV exists, only the new
    FOV-groups are processed and merged into the existing CSV and output folder /
    ``<well>_out.zip`` (see :func:`process_well_zip_task` for the contract).

    Returns (records, failed_names).
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)
    log.info("Well %s — starting (folder mode, pid %d)", well_label, os.getpid())

    # Folder-mode staging used to be pinned to ``output_dir`` (so the closing
    # commit could be a same-filesystem rename), which meant every intermediate
    # image landed on the output volume no matter what the user set the scratch
    # disk to — the single biggest source of I/O on a network-mounted output
    # dir. It now honours the scratch base like zip mode does;
    # ``_commit_staging_dir`` handles the cross-filesystem case.
    tmp_images = scratch_base_for(output_dir) / f"_tmp_images_{well_label}"
    # v5.2.0: preserve per-FOV checkpoints if any .fov_*.done markers
    # exist; otherwise wipe stale state. Same semantics as zip-mode.
    if tmp_images.exists():
        has_checkpoint = any(tmp_images.glob(".fov_*.done"))
        if has_checkpoint:
            log.info(
                "Well %s: resuming from %d FOV checkpoint(s) in %s",
                well_label,
                sum(1 for _ in tmp_images.glob(".fov_*.done")),
                tmp_images.name,
            )
        else:
            log.warning("Well %s: removing stale tmp dir %s", well_label, tmp_images.name)
            remove_directory(tmp_images)

    _committed = False
    try:
        try:
            tmp_images.mkdir(parents=True, exist_ok=True)

            groups = find_image_groups(
                well_folder, nuclear_token, fluor_tokens, schema=schema, sep=sep
            )
            if not groups:
                log.warning("Well %s: no image groups found in %s.", well_label, well_folder)
                record_well_completeness(output_dir, well_label, 0, None, [])
                return [], []

            # See the zip-mode task: the well's full input group count, captured
            # before the incremental filter narrows ``groups``.
            groups_in_input = len(groups)
            safe_well = _safe_well(well_label)
            csv_path  = output_dir / f"{csv_prefix}_{safe_well}.csv"
            out_zip   = output_dir / f"{well_label}_out.zip"
            final_out = output_dir / well_label

            if incremental:
                prior = incremental_prior_dir or output_dir
                # CSV is primary; recover done FOV-groups from the processed
                # outputs (zip or folder) so a missing CSV never re-segments.
                done, csv_done = incremental_done_fov_keys(
                    prior, well_label, csv_prefix=csv_prefix,
                    safe_well=safe_well, schema=schema, sep=sep)
                if done and not csv_done:
                    log.info("Well %s — no prior CSV; recovered %d done FOV-group(s) "
                             "from processed outputs.", well_label, len(done))
                if done:
                    new_groups = []
                    for nuc, fl in groups:
                        meta = parse_filename(nuc, schema=schema, sep=sep)
                        if (meta["fov"], meta["timepoint"]) not in done:
                            new_groups.append((nuc, fl))
                    if not new_groups:
                        log.info("Well %s — up to date (no new FOVs).", well_label)
                        return [], []
                    log.info("Well %s — merging %d new FOV(s).", well_label, len(new_groups))
                    groups = new_groups
                # else: no prior CSV or outputs → new well → process all (full).

            kwargs = {**shared_kwargs_template, "output_dir": tmp_images}
            records, failed = _process_groups_in_worker(
                groups, kwargs, well_label, errors_dir=output_dir)

            if incremental:
                # Merge new rows / images into the existing well outputs.
                if records:
                    added = merge_records_into_csv(csv_path, records)
                    log.info("Well %-12s -> %s  (+%d row(s), merged)",
                             well_label, csv_path.name, added)
                else:
                    log.warning("Well %s: no records produced; CSV not updated.", well_label)
                record_well_completeness(
                    output_dir, well_label, groups_in_input, csv_path, failed)
                n_out = merge_images_into_zip(out_zip, tmp_images)
                log.info("Well %s: merged %d new image(s) -> %s",
                         well_label, n_out, out_zip.name)
                # Incremental consumed only the new images; drop the staging dir
                # (the non-incremental path consumes it via rename/compress).
                remove_directory(tmp_images)
            else:
                if records:
                    _write_single_csv(records, csv_path)
                    log.info("Well %-12s -> %s  (%d rows)", well_label, csv_path.name, len(records))
                else:
                    log.warning("Well %s: no records produced; CSV not written.", well_label)
                record_well_completeness(
                    output_dir, well_label, groups_in_input, csv_path, failed)

                # Atomically rename staging dir to final output folder.
                if final_out.exists():
                    remove_directory(final_out)
                _commit_staging_dir(tmp_images, final_out)
                log.info("Well %s: output folder -> %s", well_label, final_out.name)

                n_out = compress_folder_images_to_zip_and_remove(final_out, out_zip)
                if n_out > 0:
                    log.info(
                        "Well %s: compressed output folder -> %s (%d image(s))",
                        well_label,
                        out_zip.name,
                        n_out,
                    )

            in_zip = well_folder.parent / f"{well_label}.zip"
            n_in = compress_folder_images_to_zip_and_remove(well_folder, in_zip)
            if n_in > 0:
                log.info(
                    "Well %s: compressed input folder -> %s (%d image(s))",
                    well_label,
                    in_zip.name,
                    n_in,
                )
            _committed = True

            # Mark the well as done before the (potential) staging-dir
            # cleanup in the finally block — same reasoning as the zip
            # task: cleanup can hang on NAS storage and we don't want
            # that to defer the GUI's well_done event.
            log.info("Well %s — done.", well_label)
            return records, failed
        except BaseException as exc:
            # Capture the worker-side traceback to disk before propagating.
            # ProcessPoolExecutor pickles the exception back to the parent,
            # but generic OSError("Bad file descriptor") re-raises at
            # future.result() with little context if the worker's stderr is
            # broken. The persisted trace gives the user the original site.
            trace_path = _dump_well_failure_trace(output_dir, well_label, exc)
            if trace_path is not None:
                log.error(
                    "Well %s failed (%s: %s); worker trace written to %s",
                    well_label, type(exc).__name__, exc, trace_path,
                    exc_info=True,
                )
            else:
                log.error(
                    "Well %s failed (%s: %s); could not write worker trace file",
                    well_label, type(exc).__name__, exc,
                    exc_info=True,
                )
            raise

    finally:
        if not _committed and tmp_images.exists():
            remove_directory(tmp_images)
            log.info("Well %s — staging directory removed.", well_label)


# ---------------------------------------------------------------------------
# Skip-detection helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Zip-aware orchestration
# ---------------------------------------------------------------------------

def _run_well_worker_pool(
    *,
    items: list[tuple[str, Path]],
    output_dir: Path,
    workers: int,
    force: bool,
    incremental: bool,
    force_cpu: bool,
    threads_per_worker: int,
    shared_kwargs_template: dict,
    mode_label: str,
    stale_sweep,
    output_exists,
    submit_task,
    force_wells=None,
) -> None:
    """Shared per-well ``ProcessPoolExecutor`` driver for the zip / folder modes
    (audit A10 — both hand-rolled the identical skip / dispatch / collect / log
    scaffolding). The mode-specific bits are callables: *stale_sweep(output_dir)*,
    *output_exists(well_label) -> bool*, and *submit_task(executor, well_label,
    path) -> Future* (the future resolves to ``(records, failed)``).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stale_sweep(output_dir)

    # Skip already-complete wells unless --force. In --incremental mode nothing
    # is skipped up front: every well is routed to the task, which decides
    # new-well / merge-new-FOVs / up-to-date per well.
    n_skipped = 0
    to_process: list[tuple[str, Path]] = []
    forced = _canonical_force_wells(force_wells)
    for well_label, path in items:
        if (not force and not incremental
                and _canonical_well_label(well_label) not in forced
                and output_exists(well_label)):
            log.info(
                "SKIP well %s — output already exists. Use --force to reprocess.",
                well_label,
            )
            n_skipped += 1
        else:
            to_process.append((well_label, path))

    if not to_process:
        log.info("All wells already complete.")
        log.info("%s mode complete. %d skipped.", mode_label, n_skipped)
        return []

    # Only the StarDist methods need csbdeep / stardist importable. Cellpose and
    # classical_threshold runs must not be blocked by a preflight for a
    # segmenter they never load — on a machine without the StarDist stack, an
    # unconditional check fails the run outright.
    if shared_kwargs_template.get(
            "segmentation_method", DEFAULT_SEGMENTATION_METHOD) in _STARDIST_METHODS:
        _ensure_stardist_runtime_deps()

    if incremental:
        log.info("Incremental: %d well(s) total — merging only new FOV-groups.",
                 len(to_process))
    log.info("%s mode: %d well(s) to process, %d worker(s).", mode_label, len(to_process), workers)

    # A running COUNT of per-cell records, not the records. Each well writes its
    # own CSV inside its worker, so the parent needs only the total for the
    # summary line below; accumulating the full list held every cell dict of the
    # plate in the long-lived parent just to call len() on it — tens of millions
    # of ~KB dicts, the tens-of-GB "the run eats all my RAM" leak.
    total_nuclei = 0
    # Two DISTINCT failure classes, kept apart so the summary isn't misleading:
    # wells_failed_entirely — the worker raised, so the well wrote nothing and is
    # absent from the plate map. partial_wells{well -> failed FOV-group names} —
    # the well wrote a CSV + out.zip from its successful groups, so it reads as
    # complete on the plate map while missing the groups listed. Flattened into
    # one list they reported per-FOV failure names as "wells failed", which is
    # how a run with 12 bad groups across two otherwise-complete wells claimed
    # "12 well(s)/pair(s) failed" while those wells looked done.
    wells_failed_entirely: list[str] = []
    partial_wells: dict[str, list[str]] = {}
    # Per-well results accumulate on the output volume for the whole plate, so
    # the run can start with room and end without it. Measured per completed
    # well and projected forward, which warns while there is still space to act.
    capacity = OutputCapacityMonitor(output_dir, len(to_process), log=log)

    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_worker_init,
        initargs=(force_cpu, threads_per_worker,
                  shared_kwargs_template.get("segmentation_method", DEFAULT_SEGMENTATION_METHOD),
                  dict(shared_kwargs_template.get("_stardist_params") or {})),
    ) as executor:
        future_to_well = {
            submit_task(executor, well_label, path): well_label
            for well_label, path in to_process
        }
        pending = set(future_to_well)
        for future in as_completed(future_to_well):
            pending.discard(future)
            well_label = future_to_well[future]
            if future.cancelled():
                continue                    # never started; suspend took it
            try:
                records, failed = future.result()
                total_nuclei += len(records)
                if failed:
                    partial_wells[well_label] = list(failed)
            except Exception as exc:
                _report_worker_failure(well_label, output_dir, exc)
                wells_failed_entirely.append(well_label)
            capacity.well_done()
            if _suspend.is_suspended():
                # Wells not yet started are dropped rather than each spinning up
                # a worker only to find the flag set. They are simply absent from
                # the output, which is what the completeness record already means.
                for f in pending:
                    f.cancel()

    n_partial_fovs = sum(len(v) for v in partial_wells.values())
    # Wells the exit code must flag: those with no output PLUS those with partial
    # output (deduped, order-stable). A partially-complete well is still a
    # failure for exit purposes — data is missing — so the remote layer must not
    # treat the run as clean success (audit E5).
    affected_wells: list[str] = list(wells_failed_entirely)
    affected_wells += [w for w in partial_wells if w not in wells_failed_entirely]

    log.info("=" * 60)
    log.info("%s mode complete.", mode_label)
    log.info(
        "Total wells : %d  |  processed : %d  |  skipped : %d  |  nuclei : %d  |  "
        "wells failed (no output) : %d  |  wells partial : %d  |  "
        "FOV-groups failed : %d",
        len(items), len(to_process), n_skipped, total_nuclei,
        len(wells_failed_entirely), len(partial_wells), n_partial_fovs,
    )
    if wells_failed_entirely:
        log.warning("Wells that failed entirely (no output written):")
        for name in wells_failed_entirely:
            log.warning("  %s", name)
    if partial_wells:
        log.warning(
            "Wells with PARTIAL output (complete on the plate map, but missing "
            "the FOV-group(s) below — re-run with --incremental to recover):"
        )
        for name, fovs in partial_wells.items():
            log.warning("  %s — %d failed FOV-group(s): %s",
                        name, len(fovs), ", ".join(fovs))
    # Surfaced to main() so a run with per-well failures sets a non-zero exit
    # code — otherwise an all-failed run exits 0 and the remote layer treats it
    # as success and cleans up the inputs (audit E5).
    return affected_wells


def process_well_zips(
    input_dir: Path,
    output_dir: Path,
    well_zips: list[tuple[str, Path]],
    shared_kwargs_template: dict,
    nuclear_token: str,
    fluor_tokens: list[str],
    workers: int,
    csv_prefix: str,
    force: bool = False,
    force_wells: set[str] | None = None,
    incremental: bool = False,
    incremental_prior_dir: Path | None = None,
    force_cpu: bool = False,
    threads_per_worker: int = 0,
    schema: list[str] | None = None,
    sep: str = DEFAULT_SEP,
) -> list[str]:
    """
    Dispatch one worker process per well zip.

    Each worker: extracts its zip, processes all image pairs sequentially
    (StarDist loaded once), writes the CSV, compresses QC images, and cleans up.
    Up to *workers* wells are processed in parallel.

    With *incremental* (and not *force*), wells that already have output are NOT
    skipped — they are routed to the task, which processes only their new
    FOV-groups and merges the result. New wells process fully; truly-unchanged
    wells return a cheap "up to date" no-op.
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)

    forced = _canonical_force_wells(force_wells)

    def _submit(executor, well_label, zip_path):
        return executor.submit(
            process_well_zip_task,
            well_label, zip_path, output_dir, shared_kwargs_template,
            nuclear_token, fluor_tokens, csv_prefix, schema, sep,
            _incremental_for_well(incremental, well_label, forced),
            incremental_prior_dir,
        )

    return _run_well_worker_pool(
        items=well_zips, output_dir=output_dir, workers=workers,
        force=force, force_wells=forced, incremental=incremental, force_cpu=force_cpu,
        threads_per_worker=threads_per_worker,
        shared_kwargs_template=shared_kwargs_template, mode_label="Zip",
        # Zip staging (extract + images) lives under the scratch base.
        stale_sweep=lambda out: _sweep_stale_scratch_dirs_all(out, extra_extract=True),
        output_exists=lambda well: output_exists_for_well(output_dir, well, csv_prefix),
        submit_task=_submit,
    )


def process_well_folders(
    input_dir: Path,
    output_dir: Path,
    well_folders: list[tuple[str, Path]],
    shared_kwargs_template: dict,
    nuclear_token: str,
    fluor_tokens: list[str],
    workers: int,
    csv_prefix: str,
    force: bool = False,
    force_wells: set[str] | None = None,
    incremental: bool = False,
    incremental_prior_dir: Path | None = None,
    force_cpu: bool = False,
    threads_per_worker: int = 0,
    schema: list[str] | None = None,
    sep: str = DEFAULT_SEP,
) -> list[str]:
    """
    Dispatch one worker process per well folder (uncompressed input).

    Each worker: discovers image groups in its well subfolder, processes them,
    writes the CSV, and saves output images to <output_dir>/<well>/.
    Up to *workers* wells are processed in parallel.

    With *incremental* (and not *force*), wells with existing output are routed
    to the task to merge only their new FOV-groups (see
    :func:`process_well_zips`).
    """
    if schema is None:
        schema = parse_schema(DEFAULT_SCHEMA)
    forced = _canonical_force_wells(force_wells)

    def _submit(executor, well_label, folder_path):
        return executor.submit(
            process_well_folder_task,
            well_label, folder_path, output_dir, shared_kwargs_template,
            nuclear_token, fluor_tokens, csv_prefix,
            schema, sep,
            _incremental_for_well(incremental, well_label, forced),
            incremental_prior_dir,
        )

    return _run_well_worker_pool(
        items=well_folders, output_dir=output_dir, workers=workers,
        force=force, force_wells=forced, incremental=incremental, force_cpu=force_cpu,
        threads_per_worker=threads_per_worker,
        shared_kwargs_template=shared_kwargs_template, mode_label="Folder",
        # Folder-mode staging (_tmp_images_*) lives under the scratch base, same
        # as zip mode; there is no zip-extract scratch to sweep. Legacy runs left
        # their staging under output_dir, so sweep both when they differ.
        stale_sweep=lambda out: _sweep_stale_scratch_dirs_all(out, extra_extract=False),
        output_exists=lambda well: output_exists_for_well_folder(
            output_dir, well, csv_prefix, compressed_output=True),
        submit_task=_submit,
    )

# ---------------------------------------------------------------------------
# pipeline_info.json sidecar
#
# This file is the contract between the analysis pipeline and the
# well-viewer / image-resolver consumers. It is written exclusively by
# this script — see ``main()`` — so that a successful run always pairs
# its outputs with the metadata describing how they were produced.
# ---------------------------------------------------------------------------

PIPELINE_INFO_FILENAME = "pipeline_info.json"
# Sidecar schema version. v1 = pre-feature (label-free seg / QC / shape features);
# v2 = may carry shape_features / qc_options / phase_token. Readers default a
# missing value to 1 (see well_viewer/domain/viewer_state.read_pipeline_info).
PIPELINE_INFO_SCHEMA_VERSION = 2


def _pipeline_info_jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _pipeline_info_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_pipeline_info_jsonable(v) for v in value]
    return value




def _collect_image_stems(input_dir: Path) -> set[str]:
    stems: set[str] = set()
    for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
        for p in input_dir.glob(pat):
            stems.add(p.stem)
    try:
        well_dirs = [p for p in input_dir.iterdir() if p.is_dir()]
    except OSError:
        well_dirs = []
    for well_dir in well_dirs:
        for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
            for p in well_dir.glob(pat):
                stems.add(p.stem)
    for z in input_dir.glob("*.zip"):
        try:
            with zipfile.ZipFile(z, "r") as zf:
                for member in zf.namelist():
                    name = Path(member).name
                    lower = name.lower()
                    if not lower.endswith((".tif", ".tiff")):
                        continue
                    stems.add(Path(name).stem)
        except Exception:
            swallowed()  # silent except
            continue
    return stems


def _collect_available_schema_values(
    input_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
    field_name: str,
    sort_key,
) -> list[str]:
    fields = [f.strip().lower() for f in filename_schema.split(":")]
    try:
        field_idx = fields.index(field_name)
    except ValueError:
        return []
    values: set[str] = set()
    for stem in _collect_image_stems(input_dir):
        parts = stem.split(filename_sep)
        if 0 <= field_idx < len(parts):
            tok = str(parts[field_idx]).strip()
            if tok:
                values.add(tok)
    return sorted(values, key=sort_key)


def _collect_available_timepoints(
    input_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
) -> list[str]:
    # Shared with the viewer and the remote runner so a dropdown, a plot axis
    # and this listing cannot order the same tokens differently.
    _sort_key = _tp_sort_key
    return _collect_available_schema_values(
        input_dir,
        filename_schema=filename_schema,
        filename_sep=filename_sep,
        field_name="timepoint",
        sort_key=_sort_key,
    )


def _collect_available_fovs(
    input_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
) -> list[str]:
    return _collect_available_schema_values(
        input_dir,
        filename_schema=filename_schema,
        filename_sep=filename_sep,
        field_name="fov",
        sort_key=_fov_sort_key,
    )


def write_pipeline_info(
    output_dir: Path,
    *,
    input_dir: Path,
    filename_schema: str,
    filename_sep: str,
    nuclear_token: str,
    fluor_tokens: list[str],
    smfish_tokens: list[str],
    segmentation_method: str,
    cytoplasm_token: str,
    min_nucleus_area_px: int,
    shape_features: list[str] | None = None,
    qc_options: dict | None = None,
    phase_token: str = "",
    execution_options: dict | None = None,
) -> Path:
    """Serialize the run metadata sidecar to ``<output_dir>/pipeline_info.json``."""
    if segmentation_method != "stardist_seeded_watershed_cell":
        cytoplasm_token = ""
    fields = [f.strip() for f in filename_schema.split(":")]
    info = {
        "schema_version": PIPELINE_INFO_SCHEMA_VERSION,
        "schema": filename_schema,
        "separator": filename_sep,
        "schema_fields": fields,
        "nuclear_token": str(nuclear_token or ""),
        "well_index": fields.index("well") if "well" in fields else -1,
        "channel_index": fields.index("channel") if "channel" in fields else -1,
        "fov_index": fields.index("fov") if "fov" in fields else -1,
        "tp_index": fields.index("timepoint") if "timepoint" in fields else -1,
        "fluor_tokens": quantified_channel_tokens(
            fluor_tokens,
            nuclear_token=nuclear_token,
            segmentation_method=segmentation_method,
            cytoplasm_token=cytoplasm_token,
        ),
        "smfish_tokens": list(smfish_tokens or []),
        "shape_features": [str(s).strip().lower() for s in (shape_features or []) if str(s).strip()],
        "qc_options": dict(qc_options or {}),
        "phase_token": str(phase_token or "").strip().lower(),
        "segmentation_method": segmentation_method,
        "cytoplasm_token": cytoplasm_token,
        "min_nucleus_area_px": int(min_nucleus_area_px),
        "available_timepoints": _collect_available_timepoints(
            input_dir, filename_schema=filename_schema, filename_sep=filename_sep,
        ),
        "available_fovs": _collect_available_fovs(
            input_dir, filename_schema=filename_schema, filename_sep=filename_sep,
        ),
        "execution_options": _pipeline_info_jsonable(dict(execution_options or {})),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    p = output_dir / PIPELINE_INFO_FILENAME
    # Preserve user-side blocks that the GUI writes back into this file
    # (cell_gating thresholds, saved selections, ratios, notes). Re-running
    # the pipeline against the same output dir must not clobber them.
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            # Corrupt existing sidecar: back it up + surface at ERROR rather
            # than silently overwriting (which drops the user-side blocks —
            # cell_gating/sample_definitions/ratios/notes — audit E3).
            backup = p.with_suffix(p.suffix + ".corrupt.bak")
            try:
                if not backup.exists():
                    shutil.copy2(p, backup)
            except OSError:
                backup = None
            log.error("existing %s unreadable (%s); backed up to %s",
                      p.name, exc, backup if backup is not None else "(backup failed)")
            existing = None
        if isinstance(existing, dict):
            for preserved_key in USER_SIDE_BLOCKS:
                if preserved_key in existing and preserved_key not in info:
                    info[preserved_key] = existing[preserved_key]
    write_json_atomic(p, info)
    return p


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Parallel StarDist nuclei segmentation + GFP quantification. "
            "Accepts a flat directory of images, a directory of per-well "
            "zip files (A01.zip … H12.zip), or a directory of per-well "
            "subfolders (A01/ … H12/)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    p.add_argument("--input_dir",  type=Path, required=True,
                   help="Directory of images, per-well zip files, or per-well subfolders")
    p.add_argument("--output_dir", type=Path, default=Path("results"),
                   help="Destination for CSVs, masks, overlays, and output folders/zips")
    p.add_argument("--scratch_dir", type=Path, default=None,
                   help="Directory for per-well working files (zip extraction, "
                        "intermediate images, FOV checkpoints). Overrides "
                        "$ALLWELL_SCRATCH_DIR; both default to ~/.allwell/scratch. "
                        "Keep this on fast LOCAL storage — pointing it at a "
                        "network mount puts the run's bulk I/O on a volume whose "
                        "dropped sessions surface as 'Bad file descriptor'.")

    p.add_argument("--nuclear_token", default="NIR",
                   help="Channel token identifying nuclear-channel files "
                        "(used for segmentation and quantified like other channels)")
    p.add_argument("--fluor_tokens", nargs="+", default=["GFP"],
                   metavar="TOKEN",
                   help="One or more channel tokens identifying fluorescent "
                        "channels to quantify (e.g. GFP mCherry DAPI). "
                        "Each token produces its own intensity columns in the CSV.")
    p.add_argument(
        "--segmentation_method",
        default=DEFAULT_SEGMENTATION_METHOD,
        choices=("stardist_nuclei", "stardist_seeded_watershed_cell",
                 "cellpose", "cellpose_labelfree", "classical_threshold"),
        help=(
            "Segmentation routine, run on the --nuclear_token channel (any "
            "fluorescent or brightfield channel) unless noted. 'stardist_nuclei' "
            "(default) and 'stardist_seeded_watershed_cell' (StarDist seeds + "
            "cytoplasm watershed) need a fluorescent stain. 'cellpose' is general "
            "Cellpose (tune --cellpose_model/diameter/flow/cellprob, optional "
            "--cellpose_nuclei_channel). 'classical_threshold' is Otsu + "
            "watershed. 'cellpose_labelfree' is a Cellpose cyto3 preset that "
            "segments the --phase_token (brightfield) channel."
        ),
    )
    p.add_argument(
        "--phase_token",
        default="",
        help=(
            "Channel token of the phase / brightfield image. Under every method "
            "this channel skips flat-field and background correction and is "
            "resaved from the original; the 'cellpose_labelfree' preset also "
            "segments it. It may not equal --nuclear_token (or --cytoplasm_token) "
            "under any other method."
        ),
    )
    p.add_argument(
        "--cellpose_model",
        default="cyto3",
        choices=("cyto3", "cyto2", "cyto", "nuclei"),
        help="Cellpose model for --segmentation_method=cellpose.",
    )
    p.add_argument(
        "--cellpose_diameter",
        type=float,
        default=0.0,
        help="Cellpose cell diameter in px (0 = auto-estimate).",
    )
    p.add_argument(
        "--cellpose_flow_threshold",
        type=float,
        default=0.4,
        help="Cellpose flow_threshold (mask recovery tolerance).",
    )
    p.add_argument(
        "--cellpose_cellprob_threshold",
        type=float,
        default=0.0,
        help="Cellpose cellprob_threshold (cell probability cut).",
    )
    p.add_argument(
        "--cellpose_nuclei_channel",
        default="",
        help="Optional fluor token used as Cellpose's nucleus channel "
             "(channels=[seg, nucleus]) for cyto models; empty = grayscale.",
    )
    p.add_argument(
        "--cytoplasm_token",
        default="",
        help=(
            "Channel token used as cytoplasm image for watershed mode. "
            "Required when --segmentation_method=stardist_seeded_watershed_cell. "
            "In watershed mode this channel is also quantified."
        ),
    )
    p.add_argument(
        "--min_nucleus_area_px",
        type=int,
        default=50,
        help="Minimum StarDist nucleus area (pixels) kept as watershed seeds.",
    )

    p.add_argument("--filename_schema", default=DEFAULT_SCHEMA,
                   help=(
                       "Colon-separated ordered field names describing the "
                       "filename structure.  Each token must be one of: "
                       + ", ".join(SCHEMA_FIELDS) + ".  "
                       "'channel' and 'well' must each appear exactly once.  "
                       "Slots whose content is not needed should be labelled "
                       "'ignore'.  "
                       f"Default: '{DEFAULT_SCHEMA}' matches the convention "
                       "Exp01_NIR_B03_F001_02d04h30m.tif"
                   ))
    p.add_argument("--filename_sep", default=DEFAULT_SEP,
                   help=(
                       "Single character that separates fields in image "
                       f"filenames.  Default: '{DEFAULT_SEP}'"
                   ))

    p.add_argument("--tophat_radius_nir", type=int, default=100,
                   help="Top-hat structuring element radius for the nuclear "
                        "channel (pixels)")
    p.add_argument("--tophat_radius_fluor", type=int, default=100,
                   help="Top-hat structuring element radius applied to every "
                        "fluorescent channel (pixels)")
    p.add_argument("--fft_small_nir", type=int, default=0,
                   help="For --background=fft_bandpass: small-structure (high-"
                        "frequency) cutoff for the nuclear channel (pixels). 0 "
                        "= high-pass only (no denoise); >1 attenuates features "
                        "smaller than this for a true band-pass.")
    p.add_argument("--fft_small_fluor", type=int, default=0,
                   help="For --background=fft_bandpass: small-structure (high-"
                        "frequency) cutoff applied to every fluorescent channel "
                        "(pixels). 0 = high-pass only.")
    p.add_argument("--mesh_ratio", type=float, default=3.0,
                   help="For --background=robust_mesh: tile size as a fraction of "
                        "the radius (tile = radius / mesh_ratio). Larger → "
                        "coarser, smoother background. Default 3.0.")
    p.add_argument("--mesh_sigma", type=float, default=3.0,
                   help="For --background=robust_mesh: per-tile sigma-clip "
                        "threshold. Smaller rejects bright object pixels more "
                        "aggressively (crowded fields). Default 3.0.")
    p.add_argument("--no_tophat_nir", action="store_true",
                   help="Disable white top-hat on the nuclear channel")
    p.add_argument("--no_tophat_fluor", action="store_true",
                   help="Disable white top-hat on all fluorescent channels")
    p.add_argument("--smfish_tokens", nargs="*", default=[],
                   help="Fluorescent channel tokens containing smFISH data. "
                        "A LoG spot-enhancement kernel is applied after tophat.")
    p.add_argument("--shape_features", nargs="*", default=[],
                   help="Per-cell shape features to emit as shape_<feature> columns "
                        "(e.g. eccentricity solidity perimeter major_axis_length). "
                        "Cell centroids (shape_centroid_row/col) are always emitted.")

    # ── Quality control ──────────────────────────────────────────────────────
    p.add_argument("--flatfield", choices=FLATFIELD_MODES, default="none",
                   help="Flat-field illumination correction applied before top-hat. "
                        "'gaussian' self-estimates a field per image; 'basic' estimates "
                        "one field per channel from a sample of the dataset (pure-numpy "
                        "BaSiC); 'empty_well' uses --background_well as the reference.")
    p.add_argument("--background_dir", default="",
                   help="Directory to read the --background_well blank(s) from, "
                        "when they do not live in --input_dir. A batched remote "
                        "run stages one batch at a time, so the blank is not in "
                        "most batches' input; it is uploaded once to its own "
                        "directory and named here, which is what lets every "
                        "batch build the same correction from it. Defaults to "
                        "--input_dir.")
    p.add_argument("--background_well", default="",
                   help="Empty/background well(s) used as the reference for "
                        "--flatfield=empty_well and/or --background=empty_well. One "
                        "well (e.g. H12) or several comma-separated (e.g. 'H12,G12') "
                        "to pool for a cleaner estimate. The field is built per "
                        "timepoint (background drifts over time), with a pooled "
                        "all-timepoints fallback for timepoints the blank lacks.")
    p.add_argument("--background", choices=BACKGROUND_METHODS, default="sliding_disk",
                   help="Background-subtraction method (applied per channel where "
                        "top-hat is enabled). 'top_hat' = fast square morphological "
                        "(leaves square halos on uneven background); "
                        "'sliding_disk' = smooth isotropic disk baseline (default; recommended "
                        "for structured/uneven background, no square halos); "
                        "'fov_median' = subtract a single scalar, the median "
                        "intensity of the whole field of view (uniform offset, "
                        "no spatial flattening; no radius/reference needed); "
                        "'empty_well' = subtract a "
                        "measured per-channel background from --background_well; "
                        "'robust_mesh' = sigma-clipped local background on a "
                        "coarse grid (SExtractor/Background2D style), robust to "
                        "bright/confluent objects on uneven background (uses the "
                        "radius as the background scale); 'fft_bandpass' = 2-D FFT "
                        "Butterworth high-pass (removes low-frequency background "
                        "regardless of object size; radius = large-structure "
                        "cutoff, --fft_small_* = optional high-frequency denoise).")
    p.add_argument("--qc_remove_debris", action="store_true",
                   help="Remove segmented regions whose size/solidity fall outside the "
                        "QC bounds below (debris, fragments, merged blobs).")
    p.add_argument("--qc_min_area", type=float, default=0.0,
                   help="Debris removal: minimum region area in px (0 = no lower bound).")
    p.add_argument("--qc_max_area", type=float, default=0.0,
                   help="Debris removal: maximum region area in px (0 = no upper bound).")
    p.add_argument("--qc_min_solidity", type=float, default=0.0,
                   help="Debris removal: minimum region solidity 0-1 (0 = no bound).")

    p.add_argument("--no_save_tophat",   action="store_true",
                   help="Do not save top-hat/smFISH intermediate TIFFs")
    # Input/output well compression is mandatory: folder-mode runs always
    # compress <input_dir>/<well>/ → <input_dir>/<well>.zip and each output well
    # folder → <output_dir>/<well>_out.zip. The pipeline only ever emits per-well
    # .zip files, so there is no flag to disable this.

    p.add_argument("--tf_threads", type=int, default=0,
                   help=(
                       "CPU threads per worker for TensorFlow/BLAS inference. "
                       "0 (default) = 4 (non-Mac) or 2 (macOS/Metal); smaller yields "
                       "more workers (cpu_count // tf_threads). Common values: "
                       "4  = default, suits most x86 CPUs (≤32 cores); "
                       "8  = for CPUs with large L3 caches (e.g. EPYC, Xeon Scalable) "
                       "     where inference scales beyond 4 threads; "
                       "16 = for very wide-cache server CPUs where per-image "
                       "     throughput still improves at 16 threads "
                       "(workers = cpu_count // tf_threads in all cases). "
                       "Ignored when running on GPU (--cpu_only not set)."
                   ))
    p.add_argument(
        "--workers",
        type=int,
        default=0,
        help=(
            "Number of parallel worker processes. "
            "0 (default) = auto-select from CPU availability and tf_threads."
        ),
    )

    p.add_argument(
        "--stardist_tile_px",
        type=int,
        default=0,
        help=(
            "Max pixels per tile SIDE for StarDist prediction (NOT the number of "
            "tiles): the frame is split into as many tiles as needed so no block "
            "exceeds this size, capping per-worker peak memory on large frames. "
            "0 (default) = OFF (predict the whole frame at once). On ~4K frames "
            "an untiled predict can use several GB per worker, so a few parallel "
            "workers exhaust RAM; e.g. 2048 splits a 4096-px frame into a 2x2 "
            "grid, 1024 into 4x4 (less RAM, a bit slower). Results are stitched."
        ),
    )

    p.add_argument(
        "--stardist_prob_thresh",
        type=float,
        default=-1.0,
        help=(
            "StarDist object-probability threshold (0-1). Candidate nuclei whose "
            "predicted objectness falls below it are dropped before polygon NMS. "
            "LOWER detects more nuclei, including dim / marginal ones; HIGHER "
            "keeps only confident detections. Negative (the default) = use the "
            "threshold the pretrained model ships with (~0.479 for "
            "2D_versatile_fluo)."
        ),
    )
    p.add_argument(
        "--stardist_nms_thresh",
        type=float,
        default=-1.0,
        help=(
            "StarDist polygon non-maximum-suppression overlap threshold (0-1). "
            "HIGHER lets more overlapping nuclei survive, which recovers touching "
            "nuclei in a confluent field but can double-count one; LOWER merges "
            "more aggressively, removing duplicates at the risk of fusing "
            "neighbours. Negative (the default) = the pretrained model's own "
            "value (0.3 for 2D_versatile_fluo)."
        ),
    )

    p.add_argument("--csv_prefix", default="measurements",
                   help="Filename prefix for per-well CSVs (<prefix>_<well>.csv)")

    p.add_argument("--force", action="store_true",
                   help=(
                       "Reprocess all wells even if output already exists. "
                       "By default, any well whose <well>_out.zip and "
                       "<csv_prefix>_<well>.csv are both present is skipped."
                   ))

    p.add_argument("--incremental", action="store_true",
                   help=(
                       "FOV-level incremental update. Instead of skipping a "
                       "well that already has output, process only the "
                       "FOV-groups not already present in its CSV and merge the "
                       "new rows / images into the existing <csv>_<well>.csv and "
                       "<well>_out.zip. New wells process fully; unchanged wells "
                       "are left as-is. Ignored when --force is given."
                   ))

    p.add_argument("--force_wells", nargs="*", default=None, metavar="WELL",
                   help=(
                       "Reprocess these wells in full, whatever else the run "
                       "decides — the per-well counterpart to --force. Given "
                       "with --incremental, the named wells ignore the "
                       "already-done set and re-segment from scratch, which is "
                       "the only way to regenerate a well whose measurements "
                       "CSV is missing while its <well>_out.zip survives: the "
                       "archive's own members make that well look complete to "
                       "an incremental run, so it would otherwise be skipped "
                       "forever. Well labels are matched canonically, so C8 "
                       "and C08 name the same well."
                   ))

    p.add_argument("--incremental_prior_dir", default=None,
                   help=(
                       "Directory holding the prior per-well CSVs used to build "
                       "the already-done (fov, timepoint) set for --incremental. "
                       "Defaults to the output directory (local runs). Remote "
                       "runs point this at the uploaded prior-CSV sidecar, since "
                       "the remote output dir starts empty."
                   ))

    p.add_argument("--cpu_only", action="store_true",
                   help=(
                       "Force StarDist to run on CPU even if a GPU is available. "
                       "Sets CUDA_VISIBLE_DEVICES='' in every worker process "
                       "before TensorFlow is initialised, preventing GPU use. "
                       "Useful for debugging, reproducibility, or when sharing "
                       "a GPU with other jobs."
                   ))

    p.add_argument("--allow_low_disk", action="store_true",
                   help=ALLOW_LOW_DISK_HELP.replace("\n\n", " "))

    p.add_argument("--no_auto_threshold", action="store_true",
                   help=(
                       "Skip the post-pipeline auto-threshold estimation. "
                       "Used by AllWell's batched remote-execution mode (v5.4.7+) "
                       "to suppress the per-batch threshold pass, which would "
                       "otherwise overwrite the previous batch's defaults using "
                       "incomplete data. The runner re-runs the estimation once "
                       "locally after all batches finish, against the full "
                       "downloaded output dir."
                   ))

    p.add_argument("--no_run_check", action="store_true",
                   help=(
                       "Skip the end-of-run completeness check. The check reads "
                       "back what the run wrote — how many (fov, timepoint) "
                       "groups each well's input held against how many its CSV "
                       "and output archive cover — and records the answer as the "
                       "'run_check' block in pipeline_info.json, which is what "
                       "the Review Home page reports. Skipping it costs seconds "
                       "and leaves the dataset with no record of whether it is "
                       "complete; a run skipped this way says so in the sidecar "
                       "rather than looking like a dataset that predates the "
                       "check. Remote runs suppress it internally and run it "
                       "client-side after the fetch instead, since the remote "
                       "output dir holds only the slice that was uploaded."
                   ))

    p.add_argument("--pos_control_well", default="",
                   help=(
                       "Well label of the positive control (e.g. B02). With "
                       "--neg_control_well, the end-of-run threshold pass picks "
                       "each non-segmentation channel's default by sweeping for "
                       "the cut that maximises the relative fraction-on gap "
                       "between these two wells, instead of Otsu-splitting cell "
                       "signal from background. Both or neither."
                   ))
    p.add_argument("--neg_control_well", default="",
                   help=(
                       "Well label of the negative control (e.g. B11). See "
                       "--pos_control_well."
                   ))

    return p

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser    = build_parser()
    args      = parser.parse_args()
    _suspend.install_handlers(log=log)   # SIGTERM/SIGINT → wind down, don't die
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()

    # Publish --scratch_dir through the env so the spawn-pool workers (which
    # inherit os.environ but not argv) resolve the same scratch base. Must
    # happen before the first scratch_base_for call.
    if args.scratch_dir is not None:
        os.environ["ALLWELL_SCRATCH_DIR"] = str(args.scratch_dir)

    if not input_dir.is_dir():
        parser.error(f"--input_dir does not exist or is not a directory: {input_dir}")

    # ── Schema validation ─────────────────────────────────────────────────────
    schema = parse_schema(args.filename_schema)
    sep    = args.filename_sep
    schema_errors = validate_schema(schema)
    if schema_errors:
        parser.error("Invalid --filename_schema:\n" + "\n".join(f"  {e}" for e in schema_errors))
    if args.min_nucleus_area_px <= 0:
        parser.error("--min_nucleus_area_px must be a positive integer.")
    # Both StarDist thresholds are probabilities. A value above 1 is rejected
    # rather than clamped: clamping would segment the plate with a parameter the
    # operator did not choose and never say so.
    for _flag, _val in (("--stardist_prob_thresh", args.stardist_prob_thresh),
                        ("--stardist_nms_thresh", args.stardist_nms_thresh)):
        try:
            _stardist.parse_threshold(_val)
        except ValueError as exc:
            parser.error(f"{_flag}: {exc}")
    if args.segmentation_method == "stardist_seeded_watershed_cell":
        if not args.cytoplasm_token:
            parser.error(
                "--cytoplasm_token is required when "
                "--segmentation_method=stardist_seeded_watershed_cell."
            )
        if args.cytoplasm_token == args.nuclear_token:
            parser.error("--cytoplasm_token must be different from --nuclear_token.")
    # Label-free methods segment the designated phase channel rather than a
    # fluorescent nuclear stain. The phase image takes the nuclear slot for the
    # grouping/segmentation machinery — remap the nuclear token onto it so all
    # existing per-FOV grouping is reused unchanged.
    phase_token = str(args.phase_token or "").strip()
    if args.segmentation_method in _LABELFREE_METHODS:
        if not phase_token:
            parser.error(
                f"--phase_token is required when --segmentation_method="
                f"{args.segmentation_method} (the phase/brightfield channel to segment)."
            )
        args.nuclear_token = phase_token
        # The label-free preset is Cellpose with a brightfield model.
        args.cellpose_model = "cyto3"
        log.info("Label-free segmentation (%s): Cellpose cyto3 on phase channel "
                 "%r as the segmentation input.", args.segmentation_method, phase_token)
    else:
        # Refuse a phase channel that is also a segmentation input. Checked here
        # and not only in the GUI: this is a self-sufficient entry point, and
        # remote runs arrive as argv.
        for slot in phase_role_conflicts(
                args.segmentation_method, phase_token=phase_token,
                nuclear_token=args.nuclear_token, cytoplasm_token=args.cytoplasm_token):
            parser.error(
                f"--phase_token {phase_token!r} is also --{slot}_token: the "
                f"phase/brightfield channel skips background correction and "
                f"cannot be segmented by --segmentation_method="
                f"{args.segmentation_method}. Name a different {slot} channel, "
                f"or use --segmentation_method=cellpose_labelfree."
            )
    log.info("Schema   : %s  (sep=%r)", args.filename_schema, sep)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Write the pipeline_info.json sidecar before any image work so the
    # well-viewer / image-resolver have schema metadata available even if
    # processing crashes partway through.
    try:
        info_path = write_pipeline_info(
            output_dir,
            input_dir=input_dir,
            filename_schema=args.filename_schema,
            filename_sep=args.filename_sep,
            nuclear_token=args.nuclear_token,
            fluor_tokens=list(args.fluor_tokens or []),
            smfish_tokens=list(args.smfish_tokens or []),
            segmentation_method=args.segmentation_method,
            cytoplasm_token=args.cytoplasm_token or "",
            min_nucleus_area_px=args.min_nucleus_area_px,
            shape_features=list(args.shape_features or []),
            qc_options={
                "flatfield": args.flatfield,
                "background": args.background,
                "background_well": args.background_well or "",
                "fft_small_nir": int(args.fft_small_nir or 0),
                "fft_small_fluor": int(args.fft_small_fluor or 0),
                "mesh_ratio": float(args.mesh_ratio or 3.0),
                "mesh_sigma": float(args.mesh_sigma or 3.0),
                "remove_debris": bool(args.qc_remove_debris),
                "min_area": float(args.qc_min_area or 0.0),
                "max_area": float(args.qc_max_area or 0.0),
                "min_solidity": float(args.qc_min_solidity or 0.0),
            },
            phase_token=args.phase_token or "",
            execution_options=vars(args),
        )
        log.info("Wrote sidecar : %s", info_path)
    except Exception as exc:  # log but don't abort the run
        log.warning("Could not write %s: %s", PIPELINE_INFO_FILENAME, exc)

    # ── Parallelism auto-configuration ────────────────────────────────────────
    # On GPU: TF manages its own scheduling — don't pin threads.
    # On CPU: StarDist inference sweet spot is ~4 threads per inference on
    #   modern x86 (L3 bandwidth saturates past that for typical microscopy
    #   image sizes).  Workers = floor(cpu_count / tf_threads) so every core
    #   is owned by exactly one worker with no contention.
    #   This scales automatically: 8 cores → 2 workers, 16 → 4, 30 → 7, 64 → 16.
    try:
        cpu_count = len(os.sched_getaffinity(0))
    except AttributeError:
        cpu_count = mp.cpu_count()
    # Reserve one core for the main process (orchestration, CSV writing, pool
    # management).  Without this, workers saturate all cores and the OS
    # time-slices the main process in, adding scheduling latency.
    available = max(1, cpu_count - 1)

    # Thread pinning applies whenever workers share CPU cores — regardless of
    # whether --cpu_only is set.  Without pinning, every TF worker tries to use
    # all available cores, causing contention even if no GPU is present.
    if args.tf_threads > 0:
        tf_threads = min(args.tf_threads, available)
    else:
        # Default sweet spot for StarDist is 4 threads, but clamp to the
        # available core budget so a 2-core host doesn't spawn 4 TF
        # threads on 1 reserved core (worse than going single-threaded).
        tf_threads = min(4, available)
    workers            = max(1, available // tf_threads)
    threads_per_worker = tf_threads

    import sys as _sys
    _on_mac = _sys.platform == "darwin"

    if args.cpu_only:
        # Explicit override: CPU regardless of platform
        pass
    elif _on_mac:
        # macOS/Metal: inference runs on the GPU, so scale the worker COUNT with
        # cores on a small per-worker CPU budget (8→3, 12→5, 16→7; was a flat 2).
        mac_threads = min(args.tf_threads, available) if args.tf_threads > 0 else 2
        tf_threads = threads_per_worker = mac_threads
        workers = max(2, available // mac_threads)
    # else: non-Mac CPU-only default already set above (workers = available //
    # tf_threads), so no branch needed.

    workers_override = False
    if args.workers > 0:
        workers_override = True
        workers = max(1, min(args.workers, available))
        if workers != args.workers:
            log.warning(
                "Requested --workers=%d exceeds available CPU budget (%d); using %d.",
                args.workers,
                available,
                workers,
            )

    # Effective device for logging
    if args.cpu_only:
        _device_str = "CPU only (--cpu_only)"
    elif _on_mac:
        _device_str = "Metal GPU (macOS) if tensorflow-metal installed, else CPU"
    else:
        _device_str = "CPU only (non-macOS)"

    log.info("Input    : %s", input_dir)
    log.info("Output   : %s", output_dir)
    # Named explicitly because it decides where the run's bulk I/O lands. If it
    # ever reads back as the output dir on a network mount, that is the
    # configuration behind "Bad file descriptor" well failures.
    _scratch_base = scratch_base_for(output_dir)
    log.info("Scratch  : %s%s", _scratch_base,
             "  (= output dir)" if _scratch_base == output_dir else "")
    log.info("CPUs     : %d total, %d available (1 reserved for main process)",
             cpu_count, available)
    log.info("Device   : %s", _device_str)
    log.info("TF threads/worker : %d  (workers: %d x %d = %d cores)",
             tf_threads, workers, tf_threads, workers * tf_threads)
    if workers_override:
        log.info("Workers override : --workers=%d", args.workers)

    fluor_tokens_for_quant = quantified_channel_tokens(
        args.fluor_tokens,
        nuclear_token=args.nuclear_token,
        segmentation_method=args.segmentation_method,
        cytoplasm_token=args.cytoplasm_token,
    )

    # Per-channel top-hat flags and radii: one entry per fluor token.
    n_fluor            = len(fluor_tokens_for_quant)
    tophat_fluor       = [not args.no_tophat_fluor] * n_fluor
    tophat_radius_fluor = [args.tophat_radius_fluor] * n_fluor
    fft_small_fluor     = [args.fft_small_fluor] * n_fluor

    log.info("Segmentation method : %s", args.segmentation_method)
    if args.segmentation_method in _STARDIST_METHODS:
        # A nucleus count is only interpretable next to the thresholds that
        # produced it, so the run states them even when they are the defaults.
        _prob = _stardist.parse_threshold(args.stardist_prob_thresh)
        _nms = _stardist.parse_threshold(args.stardist_nms_thresh)
        log.info("StarDist thresholds : prob=%s, nms=%s",
                 "model default" if _prob is None else f"{_prob:g}",
                 "model default" if _nms is None else f"{_nms:g}")
    if args.segmentation_method == "stardist_seeded_watershed_cell":
        log.info("Cytoplasm token     : %s", args.cytoplasm_token)
        log.info("Min nucleus area px : %d", args.min_nucleus_area_px)
    log.info("Fluor channels : %s", ", ".join(fluor_tokens_for_quant))
    log.info("Tophat fluor   : radius=%d, enabled=%s",
             args.tophat_radius_fluor, not args.no_tophat_fluor)
    log.info("Save tophat    : %s", not args.no_save_tophat)

    # kwargs forwarded to every process_image_group call.
    shared_kwargs = dict(
        output_dir=output_dir,
        nuclear_token=args.nuclear_token,
        fluor_tokens=fluor_tokens_for_quant,
        # The phase/brightfield channel (if any) skips background correction and
        # is resaved from the original. For label-free, nuclear_token was already
        # remapped onto this token above, so the same value flags the nuclear
        # slot; for other methods it flags one of the fluor channels.
        phase_token=phase_token.lower(),
        tophat_nir=not args.no_tophat_nir,
        tophat_radius_nir=args.tophat_radius_nir,
        tophat_fluor=tophat_fluor,
        tophat_radius_fluor=tophat_radius_fluor,
        fft_small_nir=args.fft_small_nir,
        fft_small_fluor=fft_small_fluor,
        mesh_ratio=args.mesh_ratio,
        mesh_sigma=args.mesh_sigma,
        save_tophat_images=not args.no_save_tophat,
        schema=schema,
        sep=sep,
        smfish_tokens=args.smfish_tokens,
        segmentation_method=args.segmentation_method,
        cytoplasm_token=args.cytoplasm_token,
        min_nucleus_area_px=args.min_nucleus_area_px,
        shape_features=list(args.shape_features or []),
        flatfield_mode=args.flatfield,
        flatfield_models={},
        background_method=args.background,
        background_models={},
        qc_remove_debris=args.qc_remove_debris,
        cellpose_diameter=args.cellpose_diameter,
        cellpose_model=args.cellpose_model,
        cellpose_flow_threshold=args.cellpose_flow_threshold,
        cellpose_cellprob_threshold=args.cellpose_cellprob_threshold,
        cellpose_nuclei_channel=args.cellpose_nuclei_channel,
        # Private (leading _) so it's filtered out of the process_image_group
        # kwargs but still read by _run_well_worker_pool to seed each worker's
        # StarDist settings via _worker_init/set_params. Mirrors _fov_threads.
        # A plain dict, not the frozen dataclass, because it crosses a spawn-pool
        # pickle boundary into initargs.
        _stardist_params=dict(
            prob_thresh=_stardist.parse_threshold(args.stardist_prob_thresh),
            nms_thresh=_stardist.parse_threshold(args.stardist_nms_thresh),
            tile_px=int(args.stardist_tile_px or 0),
        ),
    )

    # If the selected input is an "in" directory, normalise any loose TIF/TIFF
    # files into per-well folders before selecting processing mode.
    if input_dir.name.lower() == "in":
        organize_loose_tifs_into_well_folders(input_dir, schema=schema, sep=sep)

    # Precompute per-channel flat-field models once (basic / empty_well). The
    # gaussian mode needs no precompute (estimated per image in the worker), and
    # 'none' is a no-op. Done after the loose-TIF organize so the files are on
    # disk under input_dir.
    # Where the empty-well blank(s) are read from. Normally the run's own input,
    # but a batched remote run stages one batch at a time and the blank is not in
    # most batches — it is uploaded once to its own directory and named here.
    bg_wells = _split_background_wells(args.background_well)
    bg_src = Path(args.background_dir).expanduser() if args.background_dir else input_dir

    if args.flatfield in ("basic", "empty_well"):
        shared_kwargs["flatfield_models"] = build_flatfield_models(
            input_dir,
            tokens=fluor_tokens_for_quant,
            schema=schema,
            sep=sep,
            mode=args.flatfield,
            background_wells=bg_wells,
            background_dir=bg_src,
        )
        log.info("Flat-field (%s): built field(s) for channel(s): %s",
                 args.flatfield, sorted(shared_kwargs["flatfield_models"]) or "(none found)")
        _require_empty_well_models(
            "flat-field", args.flatfield, shared_kwargs["flatfield_models"],
            bg_wells, bg_src)

    # Precompute per-channel additive background fields once (--background
    # empty_well). top_hat / sliding_disk estimate per image in the worker.
    if args.background == "empty_well":
        shared_kwargs["background_models"] = build_background_models(
            input_dir,
            tokens=[args.nuclear_token] + list(fluor_tokens_for_quant),
            schema=schema,
            sep=sep,
            background_wells=bg_wells,
            background_dir=bg_src,
        )
        log.info("Background (empty_well): built field(s) for channel(s): %s",
                 sorted(shared_kwargs["background_models"]) or "(none found)")
        _require_empty_well_models(
            "background", args.background, shared_kwargs["background_models"],
            bg_wells, bg_src)

    # ── Decide mode: zip, folder, or flat ───────────────────────────────────
    well_zips    = find_well_zips(input_dir)
    well_folders = find_well_folders(input_dir) if not well_zips else []

    # ── Scratch preflight ───────────────────────────────────────────────────
    # Checked here, once both the well list and the channel count are known and
    # before a single image is read. The scratch volume is usually NOT the one
    # holding the dataset (§ pipeline.io.scratch_base_for), so "there is plenty
    # of room" beside the inputs says nothing about the volume the run actually
    # writes its transient working set to.
    preflight_scratch_for_run(
        scratch_base_for(output_dir), input_dir,
        n_wells=len(well_zips) + len(well_folders),
        # ``fluor_tokens_for_quant`` already leads with the segmentation
        # channel (see ``quantified_channel_tokens``), so adding one for it
        # counted that channel twice and inflated every per-group estimate by
        # a whole frame.
        n_channels=len(fluor_tokens_for_quant),
        workers=workers,
        allow_low_disk=bool(getattr(args, "allow_low_disk", False)),
        image_exts=_GROUP_IMAGE_EXTS,
        output_dir=output_dir,
        count_groups=lambda: (
            len(_collect_available_fovs(
                input_dir, filename_schema=":".join(schema), filename_sep=sep)) or 1
        ) * (
            len(_collect_available_timepoints(
                input_dir, filename_schema=":".join(schema), filename_sep=sep)) or 1
        ),
        log=log,
    )

    # Any completeness records from a previous run in this output dir would be
    # merged into this run's report; start clean.
    clear_run_report(output_dir)

    # The boundary the end-of-run completeness check counts tracebacks from.
    # ``errors/`` is never cleared and its filenames carry a pid but no run id,
    # so without a start time a clean re-run into this same output dir would
    # inherit the previous run's failures and report a healthy plate as broken.
    run_started_at = time.time()

    # Per-well failures are surfaced here so main() can signal them via the
    # process exit code (audit E5) rather than always exiting 0.
    failed_wells: list[str] = []
    if well_zips:
        log.info("Zip mode: %d well zip(s) detected.", len(well_zips))
        failed_wells = process_well_zips(
            input_dir=input_dir,
            output_dir=output_dir,
            well_zips=well_zips,
            shared_kwargs_template=shared_kwargs,
            nuclear_token=args.nuclear_token,
            fluor_tokens=fluor_tokens_for_quant,
            workers=workers,
            csv_prefix=args.csv_prefix,
            force=args.force,
            force_wells=args.force_wells,
            incremental=args.incremental,
            incremental_prior_dir=(
                Path(args.incremental_prior_dir)
                if args.incremental_prior_dir else None
            ),
            force_cpu=args.cpu_only,
            threads_per_worker=threads_per_worker,
            schema=schema,
            sep=sep,
        )
    elif well_folders:
        log.info("Folder mode: %d well folder(s) detected.", len(well_folders))
        failed_wells = process_well_folders(
            input_dir=input_dir,
            output_dir=output_dir,
            well_folders=well_folders,
            shared_kwargs_template=shared_kwargs,
            nuclear_token=args.nuclear_token,
            fluor_tokens=fluor_tokens_for_quant,
            workers=workers,
            csv_prefix=args.csv_prefix,
            force=args.force,
            force_wells=args.force_wells,
            incremental=args.incremental,
            incremental_prior_dir=(
                Path(args.incremental_prior_dir)
                if args.incremental_prior_dir else None
            ),
            force_cpu=args.cpu_only,
            threads_per_worker=threads_per_worker,
            schema=schema,
            sep=sep,
        )
    else:
        # No grouped input found. Every real entry point (the Analyze tab, the
        # remote runner, and the "in"-directory loose-TIF organizer above)
        # groups images into per-well zips or folders before invoking this
        # script, so reaching here means the input was never grouped. Fail
        # loudly and non-zero rather than silently processing nothing.
        import sys as _sys
        log.error(
            "No per-well zips (<WELL>.zip) or folders (<WELL>/) found in %s. "
            "Group the input first — run through the Analyze tab, or name the "
            "input directory 'in' so loose TIFs are grouped automatically. "
            "Exiting.",
            input_dir,
        )
        _sys.exit(1)

    # Fold each well's coverage into pipeline_info.json. Without this record a
    # partly-failed well is indistinguishable from a whole one — see
    # pipeline/run_report.py.
    finalize_run_report(
        output_dir, output_dir / PIPELINE_INFO_FILENAME,
        atomic_writer=_safe_atomic_text_write, log=log,
    )

    # Auto-threshold: every well is processed and the output zips, per-well CSVs
    # and sidecar are on disk, so write the default ThreshFracOn cut per channel
    # into ``cell_gating.thresh_frac_on``. See ``run_auto_threshold_pass`` for
    # the three modes. Stays inside the pipeline's own dependency set, so a run
    # produces thresholds even where ``well_viewer`` is not installed.
    run_auto_threshold_pass(args, output_dir, fluor_tokens_for_quant, log)

    # Completeness: read back what the run wrote and record whether it covers
    # its input, as the ``run_check`` block in the sidecar. Runs after
    # auto-threshold so the record describes the finished directory including
    # whatever that pass wrote, and before the exit-code decision because it
    # does not participate in it — a plate that came up short is recorded and
    # warned about, but whether the *run* failed is ``failed_wells``' question.
    record_run_check(
        info_path=output_dir / PIPELINE_INFO_FILENAME,
        output_dir=output_dir,
        in_containers=dict(well_zips or well_folders or []),
        schema=schema,
        sep=sep,
        csv_prefix=args.csv_prefix,
        started_at=run_started_at,
        mode="zip" if well_zips else "folder",
        atomic_writer=_safe_atomic_text_write,
        log=log,
        skip=bool(getattr(args, "no_run_check", False)),
        suspended=_suspend.is_suspended(),
    )

    # audit E5: a run with per-well failures must not exit 0 — otherwise the
    # remote layer reads success and deletes the inputs for wells that never
    # produced output. Auto-threshold (informational) still runs above so a
    # partly-successful run keeps its thresholds; the exit code reflects the
    # failures.
    if failed_wells:
        import sys as _sys
        log.error(
            "%d well(s) failed or produced partial output — exiting non-zero. "
            "See the per-well breakdown above; partial wells look complete on "
            "the plate map but are missing FOV-group(s) (recover with "
            "--incremental).",
            len(failed_wells),
        )
        _sys.exit(1)


if __name__ == "__main__":
    main()
