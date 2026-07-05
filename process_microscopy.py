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
import re
import shutil
import time
import zipfile
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

# StarDist and csbdeep (which import TensorFlow) are intentionally NOT imported
# here at module level.  They are imported inside _worker_init, after the
# TF_NUM_INTRAOP/INTEROP_THREADS env vars have been set, so TF picks up the
# thread limits before it initialises its internal thread pools.

# ---------------------------------------------------------------------------
# Per-worker StarDist model (loaded once at worker startup, not per image)
# ---------------------------------------------------------------------------

# Per-worker Cellpose model cache + the Cellpose segmenters live in the Qt-free
# ``pipeline.cellpose`` module (Torch is imported lazily there, so importing it
# does not load Torch). Re-exported below so ``_CELLPOSE_MODELS`` /
# ``_segment_cellpose`` / ``_cellpose_model`` resolve under this module too.
from pipeline.cellpose import (  # noqa: E402,F401
    _CELLPOSE_MODELS,
    _cellpose_model,
    _segment_cellpose,
)
# The StarDist segmenters + their per-worker model/FOV state live in the Qt-free
# ``pipeline.stardist`` module (TensorFlow is imported lazily there; the model
# is injected by ``_worker_init`` via ``set_model``). Re-exported so the
# segmentation dispatch resolves them under this module too.
import pipeline.stardist as _stardist  # noqa: E402
from pipeline.stardist import (  # noqa: E402,F401
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
from pipeline.io import (  # noqa: E402,F401
    _AtomicTextHandle, _fsync_path,
    _safe_atomic_text_write,
    _safe_imwrite,
    _safe_well,
    _verify_files_complete,
    _write_single_csv,
    incremental_done_fov_keys,
    merge_images_into_zip,
    merge_records_into_csv,
    save_overlay,
    scratch_base_for,
    write_csvs_per_well,
)


def _report_worker_failure(well_label: str, output_dir: "Path | None", exc: BaseException) -> None:
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


def _dump_well_failure_trace(output_dir: Path, well_label: str, exc: BaseException) -> "Path | None":
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
    except Exception:                # noqa: BLE001
        return None


def _dump_fov_failure_trace(
    output_dir: Path, well_label: str, fov_stem: str, exc: BaseException,
) -> "Path | None":
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
    except Exception:                # noqa: BLE001
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
    parent's pipe.
    """
    import sys
    import io

    def _fd_ok(fd: int) -> bool:
        try:
            os.fstat(fd)
            return True
        except OSError:
            return False

    def _wrap_fd(fd: int):
        # ``closefd=False`` is critical — the StreamHandler outlives the
        # main script, but we must not close the parent's pipe end when
        # GC eventually drops this wrapper.
        try:
            raw = os.fdopen(fd, "wb", buffering=0, closefd=False)
            return io.TextIOWrapper(
                raw, encoding="utf-8", errors="replace",
                line_buffering=True, write_through=True,
            )
        except OSError:
            return None

    def _probe(stream) -> bool:
        if stream is None:
            return False
        try:
            stream.write("")
            stream.flush()
            return True
        except Exception:  # noqa: BLE001 — broken streams raise anything
            return False

    if not _probe(sys.stdout):
        wrapper = _wrap_fd(1) if _fd_ok(1) else None
        if wrapper is not None:
            sys.stdout = wrapper
    if not _probe(sys.stderr):
        # Prefer fd 2 if it's live; otherwise alias to sys.stdout so the
        # logging StreamHandler still has somewhere to write. The parent
        # spawns us with ``stderr=STDOUT`` so the two streams merge
        # downstream anyway.
        wrapper = _wrap_fd(2) if _fd_ok(2) else None
        if wrapper is None:
            wrapper = sys.stdout
        sys.stderr = wrapper


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

# Valid well names: A1/A01 – H12  (rows A-H, columns 1-12 or 01-12)
_WELL_RE = re.compile(r"^([A-Ha-h])(\d{1,2})$")


def is_valid_well_name(stem: str) -> bool:
    """Return True if *stem* (filename without extension) is a valid 96-well position."""
    m = _WELL_RE.match(stem)
    if not m:
        return False
    col = int(m.group(2))
    return 1 <= col <= 12


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
        if is_valid_well_name(p.stem):
            # Normalise to upper-case, zero-padded column: a1 → A01
            m = _WELL_RE.match(p.stem)
            well = f"{m.group(1).upper()}{int(m.group(2)):02d}"
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
        if is_valid_well_name(p.name):
            m = _WELL_RE.match(p.name)
            well = f"{m.group(1).upper()}{int(m.group(2)):02d}"
            found.append((well, p))
    log.info("Found %d well folder(s) in %s", len(found), input_dir)
    return sorted(found, key=lambda x: x[0])


def _canonical_well_label(token: str) -> str | None:
    """Return canonical 96-well label (e.g. B03) for *token*, or None if invalid.

    Thin wrapper over :func:`util.well_token.canonical_well_label` so the
    pipeline and the GUI tools (WellPlateZipper, analyze_tab,
    services/input_resolution_service) all share one parser.
    """
    from util.well_token import canonical_well_label as _cwl
    return _cwl(token)


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
    members_to_extract: "set[str] | None" = None,
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
        return 0

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    # Build the zip via the same atomic-rename pattern the imwrite helper
    # uses: stage at <out_zip>.tmp, fsync, replace. Any caller that sees
    # *out_zip* gets either the complete archive or the previous one.
    tmp_zip = out_zip.with_name(f"__aw_tmp_{out_zip.name}.pid{os.getpid()}")
    try:
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for img_path in image_files:
                zf.write(img_path, arcname=img_path.name)
        _fsync_path(tmp_zip)
        os.replace(str(tmp_zip), str(out_zip))
    except BaseException:
        try:
            if tmp_zip.exists():
                tmp_zip.unlink()
        except OSError:
            pass
        raise

    log.info(
        "Compressed %d image(s)  ->  %s", len(image_files), out_zip.name
    )
    return len(image_files)


def compress_folder_images_to_zip_and_remove(folder: Path, out_zip: Path) -> int:
    """
    Compress image files in *folder* to *out_zip* and remove *folder*.

    Returns the number of image files added to the archive. If no image files
    are found, the source folder is left untouched.
    """
    n = compress_images_to_zip(folder, out_zip)
    if n <= 0:
        return 0
    remove_directory(folder)
    return n


def remove_directory(path: Path, *, retries: int = 4, backoff: float = 0.5) -> None:
    """Remove *path* and all its contents, with retries for remote filesystems.

    Networked / remote filesystems (SMB / NFS / AppleShare) routinely fail
    ``shutil.rmtree`` on the first attempt because:

    * The OS hasn't yet released file handles that backed recently-written
      files (lazy close on the server side).
    * macOS Finder reinjects ``.DS_Store`` sidecars and ``._<file>``
      AppleDouble metadata into the directory while it's being deleted.
    * Server-side indexers and antivirus scanners briefly hold scanned
      files open.

    The previous implementation logged a single warning and gave up,
    leaving stale ``_tmp_extract_<well>`` / ``_tmp_images_<well>``
    directories behind on every pipeline run. The retry loop here:

    1. Strips the read-only bit on individual files that raise
       ``PermissionError`` and retries that file.
    2. Sleeps with a linear backoff between full ``rmtree`` attempts so
       the kernel / server has time to release lazy handles.
    3. Logs at WARNING only after every attempt fails — so a transient
       remote-FS hiccup no longer pollutes the GUI log.
    """
    import stat as _stat

    if not path.exists():
        return

    def _on_rm_error(func, target, exc_info):
        # ``onerror`` runs per-file. PermissionError typically means a
        # read-only attribute set (common on Windows / SMB shares); flip
        # it and retry. Anything else: propagate so the outer attempt
        # falls through to the retry/backoff path.
        exc = exc_info[1] if exc_info else None
        if isinstance(exc, PermissionError):
            try:
                os.chmod(target, _stat.S_IWRITE | _stat.S_IREAD)
                func(target)
                return
            except OSError:
                pass
        if exc is not None:
            raise exc
        raise OSError(f"unknown rmtree error on {target!r}")

    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            shutil.rmtree(path, onerror=_on_rm_error)
            if attempt > 0:
                log.info(
                    "Removed %s after %d retry(ies) (remote-FS lazy release).",
                    path, attempt,
                )
            else:
                log.debug("Removed temporary directory: %s", path)
            return
        except Exception as exc:  # noqa: BLE001 — every Errno → keep trying
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(backoff * (attempt + 1))

    if last_exc is not None:
        log.warning(
            "Could not remove %s after %d attempts: %s — leftover files "
            "can be removed manually.",
            path, retries, last_exc,
        )

# ---------------------------------------------------------------------------
# Filename schema helpers
# ---------------------------------------------------------------------------

#: Ordered list of recognised field names (the vocabulary for schema slots).
SCHEMA_FIELDS = ("experiment", "channel", "well", "fov", "timepoint", "ignore")

#: Default schema — matches the original hardcoded convention.
DEFAULT_SCHEMA = "experiment:channel:well:fov:timepoint"
DEFAULT_SEP    = "_"
DEFAULT_SEGMENTATION_METHOD = "stardist_nuclei"


def parse_schema(schema_str: str) -> list[str]:
    """
    Parse a colon-delimited schema string into a list of field names.

    Each token must be one of SCHEMA_FIELDS.  Unknown tokens are silently
    replaced with "ignore" so that old schema strings remain forward-compatible
    when new field names are added later.

    Example:
        "experiment:channel:well:fov:timepoint"
        -> ["experiment", "channel", "well", "fov", "timepoint"]
    """
    known = set(SCHEMA_FIELDS)
    return [
        tok if tok in known else "ignore"
        for tok in schema_str.strip().split(":")
        if tok  # skip empty tokens from trailing colons etc.
    ]


def validate_schema(schema: list[str]) -> list[str]:
    """
    Return a list of human-readable error messages for *schema*.
    An empty list means the schema is valid.

    Rules:
      • "channel" must appear exactly once.
      • "well"    must appear exactly once.
    """
    errors: list[str] = []
    if schema.count("channel") != 1:
        errors.append(
            f'"channel" must appear exactly once in the schema '
            f'(found {schema.count("channel")}).'
        )
    if schema.count("well") != 1:
        errors.append(
            f'"well" must appear exactly once in the schema '
            f'(found {schema.count("well")}).'
        )
    return errors


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

def _parse_timepoint_hours(tp: str) -> float:
    """
    Convert a timepoint string to fractional hours.
    Returns float("nan") when the string cannot be parsed at all.

    Formats tried in order:
      1. DDdHHhMMm  e.g. "02d04h30m" -> 52.5  (full or partial components)
      2. Standalone unit suffix  e.g. "48h" -> 48.0, "2d" -> 48.0, "30m" -> 0.5
      3. Pure number  e.g. "48" or "1.5" -> treated as hours
      4. Prefixed ordinal  e.g. "T01", "t3", "day2", "D02" -> ordinal value
         (strip leading non-digit chars, use the numeric part as a plain
          index so relative ordering is preserved)
    """
    s = tp.strip()
    if not s:
        return float("nan")

    # 1. Full DDdHHhMMm (all components optional, but at least one required)
    m = re.fullmatch(
        r"(?:(\d{1,4})d)?(?:(\d{1,2})h)?(?:(\d{1,2})m)?",
        s, re.IGNORECASE,
    )
    if m and any(m.groups()):
        days  = int(m.group(1) or 0)
        hours = int(m.group(2) or 0)
        mins  = int(m.group(3) or 0)
        return days * 24.0 + hours + mins / 60.0

    # 2. Standalone unit: "48h", "2d", "30m", "90min"
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(h(?:ours?)?|d(?:ays?)?|m(?:in(?:utes?)?)?)",
                     s, re.IGNORECASE)
    if m:
        val  = float(m.group(1))
        unit = m.group(2)[0].lower()
        if unit == "h":
            return val
        if unit == "d":
            return val * 24.0
        if unit == "m":
            return val / 60.0

    # 3. Pure number (treat as hours)
    try:
        return float(s)
    except ValueError:
        pass

    # 4. Prefixed ordinal: strip leading non-digit characters, keep the number.
    #    e.g. "T01" -> 1.0, "day02" -> 2.0, "tp_3" -> 3.0
    m = re.search(r"(\d+(?:\.\d+)?)$", s)
    if m:
        return float(m.group(1))

    return float("nan")


def parse_filename(
    path: "Path | str",
    schema: "list[str] | None" = None,
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
from pipeline.background import (  # noqa: E402,F401
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


def _load_image_source(source) -> "np.ndarray | None":
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


def _split_background_wells(value) -> "list[str]":
    """Parse the comma-separated background-well value into a clean, de-duped list.

    Accepts a single well (``"H12"``), a comma-separated string (``"H12, G12"``),
    or an already-split list. Order-preserving; case-insensitive de-dupe.
    """
    items = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
    out: "list[str]" = []
    seen: set = set()
    for it in items:
        w = str(it or "").strip()
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


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
    tokens: "list[str]",
    schema: "list[str] | None",
    sep: str,
    background_wells: "list[str]",
    max_samples: int = 30,
) -> "dict[str, dict[str, list[np.ndarray]]]":
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
    wells = {w.lower() for w in _split_background_wells(background_wells)}
    if chan_idx < 0 or not wells:
        return {}
    want = {str(t).strip().lower() for t in (tokens or [])}

    def _token_of(stem: str, *, require_well: bool) -> "str | None":
        parts = stem.split(sep)
        if chan_idx >= len(parts):
            return None
        if require_well:
            if well_idx < 0 or well_idx >= len(parts) \
                    or parts[well_idx].strip().lower() not in wells:
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
    by_token_tp: "dict[str, dict[str, list]]" = defaultdict(lambda: defaultdict(list))

    def _add(stem: str, source) -> None:
        tok = _token_of(stem, require_well=False)
        if tok is not None:
            by_token_tp[tok][_tp_of(stem)].append(source)

    # 1) per-well zip(s) (default mode): <input_dir>/<WELL>.zip. The well is
    #    established by the zip name, so members need not carry the well token.
    zip_hit = False
    for well, zpath in find_well_zips(input_dir):
        if well.lower() not in wells:
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
            if well.lower() not in wells:
                continue
            for p in sorted(folder.rglob("*.tif*")):
                _add(p.stem, p)

    # 3) loose TIFFs anywhere under input_dir — the filename must carry the well.
    if not by_token_tp:
        for p in sorted(input_dir.rglob("*.tif*")):
            tok = _token_of(p.stem, require_well=True)
            if tok is not None:
                by_token_tp[tok][_tp_of(p.stem)].append(p)

    def _load_capped(sources) -> "list[np.ndarray]":
        step = max(1, len(sources) // max_samples)
        return [a for a in (_load_image_source(s)
                            for s in sources[::step][:max_samples]) if a is not None]

    out: "dict[str, dict[str, list[np.ndarray]]]" = {}
    for tok, by_tp in by_token_tp.items():
        per_tp: "dict[str, list[np.ndarray]]" = {}
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



def build_background_models(
    input_dir: Path,
    *,
    tokens: "list[str]",
    schema: "list[str] | None",
    sep: str,
    background_wells: "list[str] | str" = "",
    max_samples: int = 30,
) -> "dict[str, dict[str, np.ndarray]]":
    """Per-channel, **per-timepoint** additive background fields from the empty/
    background well(s) (for ``--background empty_well``). Pools frames across all
    named blank wells and groups by timepoint via :func:`sample_empty_well_images`,
    then builds one :func:`estimate_background_field` per (channel, timepoint) plus
    a pooled all-timepoints field. Returns ``{token_lower: {tp_key: field,
    _POOLED_TP: field}}``; channels with no usable empty-well images omitted.
    """
    stacks = sample_empty_well_images(
        input_dir, tokens=tokens, schema=schema, sep=sep,
        background_wells=background_wells, max_samples=max_samples)
    models: "dict[str, dict[str, np.ndarray]]" = {}
    for tok, by_tp in stacks.items():
        per_tp: "dict[str, np.ndarray]" = {}
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
    selected_keys: "list[str] | None",
) -> "dict[int, dict[str, float]]":
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

    out: "dict[int, dict[str, float]]" = {}
    for i, lab in enumerate(label_ids):
        rec: "dict[str, float]" = {}
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
from pipeline.flatfield import (  # noqa: E402,F401
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
) -> "dict[int, float]":
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
    out: "dict[int, float]" = {int(n): 0.0 for n in ids}
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
    best_count: "dict[int, int]" = {}
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
    tokens: "list[str]",
    schema: "list[str] | None",
    sep: str,
    mode: str,
    background_wells: "list[str] | str" = "",
    max_samples: int = 30,
) -> "dict[str, dict[str, np.ndarray]]":
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

    models: "dict[str, dict[str, np.ndarray]]" = {}
    if mode == "empty_well":
        if not _split_background_wells(background_wells):
            return {}
        stacks = sample_empty_well_images(
            input_dir, tokens=tokens, schema=schema, sep=sep,
            background_wells=background_wells, max_samples=max_samples)
        for tok, by_tp in stacks.items():
            per_tp: "dict[str, np.ndarray]" = {}
            for tp_key, imgs in by_tp.items():
                field = estimate_basic_flatfield(imgs)
                if field is not None:
                    per_tp[tp_key] = field
            if per_tp:
                models[tok] = per_tp
    else:  # basic — whole-plate sampling from loose/folder TIFFs on disk.
        by_token: "dict[str, list[Path]]" = {}
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
    DEFAULT_CELLS_PER_IMAGE_CAP as _AUTO_THRESHOLD_CELLS_PER_IMAGE_CAP,
    pick_endpoint_timepoints as _pick_endpoint_timepoints,
    sample_cell_and_bg as _sample_cell_and_bg,
)
from util.aw_safe import swallowed


def _estimate_thresholds_standalone(
    output_dir: Path,
    *,
    fluor_channels: "list[str]",
    filename_schema: str,
    filename_sep: str,
    log: logging.Logger,
    cells_per_image_cap: int = _AUTO_THRESHOLD_CELLS_PER_IMAGE_CAP,
    rng_seed: "int | None" = None,
) -> "dict[str, float]":
    """Otsu-based per-channel threshold estimator that depends only on
    modules already imported by this file. Returns ``{channel: threshold}``.
    """
    import io as _io
    import random as _random

    channels = [str(c).strip().lower() for c in fluor_channels if str(c).strip()]
    if not channels:
        return {}
    output_dir = Path(output_dir)
    if not output_dir.exists():
        log.warning("Auto-threshold: output dir not found (%r)", str(output_dir))
        return {}

    schema_fields = [f.strip().lower() for f in str(filename_schema or "").split(":")
                     if f.strip()]
    sep = filename_sep or "_"

    def _parse_fields(stem: str) -> "dict[str, str]":
        parts = stem.split(sep)
        return {f: (parts[i] if i < len(parts) else "")
                for i, f in enumerate(schema_fields)}

    rng = _random.Random(rng_seed)
    per_channel: "dict[str, list[float]]" = {c: [] for c in channels}
    # Track how many candidate (label, fluor) image pairs we attempted to
    # decode per channel and how many failed (audit E9). A decode failure was
    # only logged at DEBUG — invisible at the default level — so a channel
    # whose tophat images are mostly corrupt would silently compute its Otsu
    # threshold from a small, biased subset. We surface a WARN in the summary
    # when a meaningful fraction was dropped.
    per_channel_attempted: "dict[str, int]" = {c: 0 for c in channels}
    per_channel_decode_failed: "dict[str, int]" = {c: 0 for c in channels}

    well_zips = sorted(p for p in output_dir.glob("*_out.zip") if not p.name.startswith("."))
    if not well_zips:
        log.info("Auto-threshold: no processed *_out.zip wells in %s", output_dir)
        return {}

    log.info("Auto-threshold: scanning %d well(s) for channels: %s",
             len(well_zips), ", ".join(channels))

    for w_idx, zpath in enumerate(well_zips, start=1):
        try:
            zf = zipfile.ZipFile(zpath, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            log.warning("Auto-threshold: cannot open %s: %s", zpath.name, exc)
            continue
        with zf:
            label_members: "dict[tuple[str, str], str]" = {}     # (fov, tp) -> name
            tophat_members: "dict[tuple[str, str, str], str]" = {}  # (ch, fov, tp) -> name
            for name in zf.namelist():
                if "/" in name or name.startswith("."):
                    continue
                base, _, ext = name.rpartition(".")
                if not base or ext.lower() not in ("tif", "tiff"):
                    continue
                if base.endswith("_labels"):
                    stem = base[: -len("_labels")]
                    fields = _parse_fields(stem)
                    fov = (fields.get("fov") or "").strip()
                    # Accept either canonical "timepoint" or the legacy
                    # "tp" alias. ``args.filename_schema`` is passed in
                    # *raw* (not via parse_schema), so a user who typed
                    # "experiment:channel:well:fov:tp" lands here with
                    # schema_fields == [..., "tp"]; dropping the
                    # fallback (PR #247 C2 fix) silently produced zero
                    # samples for every channel on those datasets.
                    tp = (fields.get("timepoint") or fields.get("tp") or "").strip()
                    # Single-timepoint datasets: when the filename has
                    # fewer tokens than the schema has fields, the
                    # timepoint slot parses as "" and `if fov and tp`
                    # below would drop every label silently. Use a
                    # synthetic "0" sentinel so the auto-threshold
                    # still aggregates per-fov samples.
                    if fov and not tp:
                        tp = "0"
                    if fov and tp:
                        label_members[(fov, tp)] = name
                elif base.endswith("_tophat"):
                    stem = base[: -len("_tophat")]
                    fields = _parse_fields(stem)
                    fov = (fields.get("fov") or "").strip()
                    tp = (fields.get("timepoint") or fields.get("tp") or "").strip()
                    if fov and not tp:
                        tp = "0"
                    ch = (fields.get("channel") or "").strip().lower()
                    if fov and tp and ch in per_channel:
                        tophat_members[(ch, fov, tp)] = name

            if not label_members:
                continue
            tps_seen = {tp for (_fov, tp) in label_members.keys()}
            picked_tps = set(_pick_endpoint_timepoints(tps_seen))
            if not picked_tps:
                continue
            log.info("Auto-threshold: %s → timepoints %s",
                     zpath.name, ", ".join(sorted(picked_tps)))

            for (fov, tp), label_name in label_members.items():
                if tp not in picked_tps:
                    continue
                for ch in channels:
                    fluor_name = tophat_members.get((ch, fov, tp))
                    if fluor_name is None:
                        continue
                    per_channel_attempted[ch] += 1
                    try:
                        labels_arr = imread(_io.BytesIO(zf.read(label_name)))
                        fluor_arr = imread(_io.BytesIO(zf.read(fluor_name)))
                    except Exception as exc:
                        per_channel_decode_failed[ch] += 1
                        log.debug("Auto-threshold: skipping %s / %s: %s",
                                  label_name, fluor_name, exc)
                        continue
                    if labels_arr.ndim == 3:
                        labels_arr = labels_arr[..., 0]
                    if fluor_arr.ndim == 3:
                        fluor_arr = fluor_arr[..., 0]
                    means, bg_values = _sample_cell_and_bg(
                        labels_arr, fluor_arr,
                        cap=cells_per_image_cap, rng=rng,
                    )
                    if not means:
                        continue
                    per_channel[ch].extend(means)
                    per_channel[ch].extend(bg_values)
        log.info("Auto-threshold: completed well %d/%d (%s)",
                 w_idx, len(well_zips), zpath.name)

    thresholds: "dict[str, float]" = {}
    for ch in channels:
        # A biased threshold reports itself: if a large fraction of this
        # channel's tophat/label pairs failed to decode, the Otsu estimate is
        # computed from a non-representative subset (audit E9).
        attempted = per_channel_attempted.get(ch, 0)
        failed = per_channel_decode_failed.get(ch, 0)
        if attempted and failed / attempted >= 0.25:
            log.warning(
                "Auto-threshold: %s — %d/%d image pair(s) failed to decode "
                "(%.0f%%); the threshold is estimated from the remaining "
                "subset and may be biased. Check for corrupt *_out.zip members.",
                ch.upper(), failed, attempted, 100.0 * failed / attempted,
            )
        values = per_channel.get(ch) or []
        if len(values) < 2:
            log.info("Auto-threshold: skipping %s — only %d sample(s)",
                     ch.upper(), len(values))
            continue
        arr = np.asarray(values, dtype=np.float64)
        if arr.max() <= arr.min():
            log.info("Auto-threshold: skipping %s — constant distribution (min == max == %.3g)",
                     ch.upper(), float(arr.min()))
            continue
        try:
            # Round the calculated threshold to the nearest integer before it is
            # saved — the gating cut is an intensity value, and a clean integer
            # default reads better and matches what the user can type back in.
            thr = float(round(float(threshold_otsu(arr))))
        except Exception as exc:
            log.warning("Auto-threshold: Otsu failed for %s: %s", ch.upper(), exc)
            continue
        thresholds[ch] = thr
        log.info("Auto-threshold: %s → %.4g (n=%d)", ch.upper(), thr, int(arr.size))
    return thresholds


def _apply_thresholds_to_pipeline_info(
    output_dir: Path,
    thresholds: "dict[str, float]",
    log: logging.Logger,
) -> "dict[str, float]":
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
    written: "dict[str, float]" = {}
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
        tmp = info_path.with_suffix(info_path.suffix + ".tmp")
        tmp.write_text(json.dumps(existing, indent=2))
        tmp.replace(info_path)
    except OSError as exc:
        log.warning("Auto-threshold: cannot write %s: %s", info_path, exc)
        return {}
    return written


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
    fluor_paths: "list[Path]",
    fluor_tokens: "list[str]",
    output_dir: Path,
    nuclear_token: str,
    tophat_nir: bool,
    tophat_radius_nir: int,
    tophat_fluor: "list[bool]",
    tophat_radius_fluor: "list[int]",
    fft_small_nir: int = 0,
    fft_small_fluor: "list[int] | None" = None,
    mesh_ratio: float = 3.0,
    mesh_sigma: float = 3.0,
    save_tophat_images: bool = True,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
    smfish_tokens: "list[str] | None" = None,
    segmentation_method: str = DEFAULT_SEGMENTATION_METHOD,
    cytoplasm_token: str = "",
    min_nucleus_area_px: int = 50,
    shape_features: "list[str] | None" = None,
    flatfield_mode: str = "none",
    flatfield_models: "dict | None" = None,
    background_method: str = "sliding_disk",
    background_models: "dict | None" = None,
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

_GROUP_IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def _grouping_channel_idx(schema: "list[str]") -> "int | None":
    """The schema's ``channel`` slot index, or None for substring-fallback mode."""
    return schema.index("channel") if "channel" in schema else None


def _is_nuclear_filename(
    name: str, stem: str, nuclear_token: str, channel_idx: "int | None", sep: str,
) -> bool:
    """Whether *name* is a nuclear file (audit A1) — schema ``channel`` slot, or
    substring fallback. Shared so dir + zip scanners classify identically."""
    if channel_idx is not None:
        parts = stem.split(sep)
        return channel_idx < len(parts) and parts[channel_idx] == nuclear_token
    return nuclear_token in name


def _derive_fluor_filename(
    name: str, stem: str, suffix: str, nuclear_token: str, fluor_token: str,
    channel_idx: "int | None", sep: str,
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
    fluor_tokens: "list[str]",
    channel_idx: "int | None",
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
    fluor_tokens: "list[str]",
    dest_dir: Path,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> "tuple[list[tuple[Path, list[Path]]], set[str]]":
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
    fluor_tokens: "list[str]",
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> "list[tuple[Path, list[Path]]]":
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

    groups: "list[tuple[Path, list[Path]]]" = [
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


def _purge_stardist_cache() -> "list[str]":
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
    roots: "list[Path]" = []
    kh = os.environ.get("KERAS_HOME")
    if kh:
        roots.append(Path(kh))
    roots.append(Path(os.path.expanduser("~")) / ".keras")
    removed: "list[str]" = []
    seen: "set[str]" = set()
    for models in (r / "models" for r in roots):
        if not models.is_dir():
            continue
        targets: "set[Path]" = set()
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
    pids: "list[int]" = []
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
def _stardist_download_lock(timeout: "float | None" = None):
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
        except Exception as exc:                                 # noqa: BLE001
            if not any(m in str(exc).lower() for m in _CORRUPT_MODEL_MARKERS):
                raise
            removed = _purge_stardist_cache()
            log.warning(
                "StarDist model cache was corrupt (%s: %s) — purged %s and "
                "re-downloading once.",
                exc.__class__.__name__, exc, removed or "(nothing found to remove)")
            return StarDist2D.from_pretrained(name)


def _worker_init(force_cpu: bool = False, threads_per_worker: int = 0,
                 segmentation_method: str = DEFAULT_SEGMENTATION_METHOD) -> None:
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

    Platform logic
    --------------
    On macOS, tensorflow-metal registers Metal as a GPU device automatically.
    Calling tf.config.set_visible_devices([], "GPU") disables Metal too (not
    just CUDA) so we must NOT call it on macOS unless --cpu_only is set.
    On all other platforms we default to CPU to avoid unexpected CUDA usage.
    """
    import sys as _sys

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
            _sys2.stderr = _sys2.stdout                     # noqa: F841
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

# ── Per-FOV checkpoint helpers (v5.2.0) ─────────────────────────────────────
#
# When a worker is killed mid-well, the next invocation should pick up where
# the previous one left off rather than redoing every FOV in the well. The
# scheme:
#
#   * After each FOV finishes (records computed, QC TIFs written), the worker
#     writes <tmp_images>/.fov_<stem>.records.json (atomic) then touches
#     <tmp_images>/.fov_<stem>.done. The records JSON is the cached output of
#     :func:`process_image_group` for that FOV.
#   * On startup, the worker scans <tmp_images> for valid checkpoint pairs and
#     skips those FOVs in the per-group loop, loading their records from disk.
#     Partially-written records (no .done marker) are reprocessed; orphan
#     __aw_tmp_* scratch files are deleted.
#   * On successful end-of-well (zip + CSV both written), the whole tmp_images
#     dir is removed in the usual finally-block — checkpoints are inherently
#     transient and never survive a successful well.
#
# Recovery cost for a killed-mid-well run drops from "redo every FOV in the
# well" to "redo only the in-flight FOV at kill time."

# Schema version stored in each records.json. Bumped if the records dict
# layout changes — older checkpoints with a different version are discarded
# on load so a freshly-built pipeline never reads stale records that would
# poison the per-well CSV.
_FOV_CHECKPOINT_SCHEMA = 1


def _json_safe(records: "list[dict]") -> "list[dict]":
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


def _checkpoint_paths(tmp_images: Path, fov_id: str) -> "tuple[Path, Path]":
    """Return ``(records_json, done_marker)`` for a given FOV id."""
    return (
        tmp_images / f".fov_{fov_id}.records.json",
        tmp_images / f".fov_{fov_id}.done",
    )


def _load_fov_checkpoint(tmp_images: Path, fov_id: str) -> "list[dict] | None":
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
    tmp_images: Path, fov_id: str, records: "list[dict]",
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
        os.fsync(fh.fileno())
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
    groups: "list[tuple[Path, list[Path]]]",
    shared_kwargs: dict,
    well_label: str,
    fov_threads: int = 1,
    errors_dir: "Path | None" = None,
) -> "tuple[list[dict], list[str]]":
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

    def _run_group(nuclear_path: Path, fluor_paths: "list[Path]") -> "list[dict]":
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
        nuclear_path: Path, fluor_paths: "list[Path]",
    ) -> "list[dict]":
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

    if fov_threads == 1 or len(groups) == 1:
        for nuclear_path, fluor_paths in groups:
            try:
                all_records.extend(_process_or_resume(nuclear_path, fluor_paths))
            except Exception as exc:           # noqa: BLE001
                _record_fov_failure(nuclear_path, exc)
            _fov_done += 1
            log.info("Well %s — FOV %d/%d done", well_label, _fov_done, _fov_total)
    else:
        with ThreadPoolExecutor(max_workers=fov_threads) as tex:
            future_map = {
                tex.submit(_process_or_resume, nuc, fluors): nuc
                for nuc, fluors in groups
            }
            for fut in _as_completed(future_map):
                nuclear_path = future_map[fut]
                try:
                    all_records.extend(fut.result())
                except Exception as exc:       # noqa: BLE001
                    _record_fov_failure(nuclear_path, exc)
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
    fluor_tokens: "list[str]",
    csv_prefix: str,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
    incremental: bool = False,
    incremental_prior_dir: "Path | None" = None,
) -> "tuple[list[dict], list[str]]":
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
                return [], []

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

            if incremental:
                if records:
                    added = merge_records_into_csv(csv_path, records)
                    log.info("Well %-12s -> %s  (+%d row(s), merged)",
                             well_label, csv_path.name, added)
                else:
                    log.warning("Well %s: no records produced; CSV not updated.", well_label)
                n_compressed = merge_images_into_zip(out_zip, tmp_images)
                log.info("Well %s: merged %d new image(s) -> %s",
                         well_label, n_compressed, out_zip.name)
            else:
                if records:
                    _write_single_csv(records, csv_path)
                    log.info("Well %-12s -> %s  (%d rows)", well_label, csv_path.name, len(records))
                else:
                    log.warning("Well %s: no records produced; CSV not written.", well_label)

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
    fluor_tokens: "list[str]",
    csv_prefix: str,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
    incremental: bool = False,
    incremental_prior_dir: "Path | None" = None,
) -> "tuple[list[dict], list[str]]":
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

    tmp_images = output_dir / f"_tmp_images_{well_label}"
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
                return [], []

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

                # Atomically rename staging dir to final output folder.
                if final_out.exists():
                    remove_directory(final_out)
                tmp_images.rename(final_out)
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

def output_exists_for_well(output_dir: Path, well_label: str, csv_prefix: str) -> bool:
    """
    Return True if processable output already exists for *well_label*.

    Both outputs must be present and non-empty to count as complete:
      • <output_dir>/<well>_out.zip       – compressed QC images
      • <output_dir>/<csv_prefix>_<well>.csv – per-well measurements

    This prevents re-processing wells that completed in a prior run. Note that a
    well whose CSV + out.zip exist counts as complete here even if some of its
    FOV-groups failed (the outputs cover only the successful FOV-groups), so a
    plain re-run SKIPS such partial wells. To reprocess the missing FOV-groups,
    re-run with --incremental (they are absent from the CSV done-set and so are
    re-processed) or --force (redo the whole well).
    """
    safe_well = _safe_well(well_label)
    out_zip  = output_dir / f"{well_label}_out.zip"
    csv_path = output_dir / f"{csv_prefix}_{safe_well}.csv"
    zip_ok = out_zip.exists()  and out_zip.stat().st_size  > 0
    csv_ok = csv_path.exists() and csv_path.stat().st_size > 0
    return zip_ok and csv_ok


def output_exists_for_well_folder(
    output_dir: Path,
    well_label: str,
    csv_prefix: str,
    compressed_output: bool = False,
) -> bool:
    """
    Return True if folder-mode output already exists for *well_label*.

    Both outputs must be present and non-empty to count as complete:
      • <output_dir>/<well>/                   – output image folder (must contain ≥1 file), OR
        <output_dir>/<well>_out.zip            – output image archive when *compressed_output* is True
      • <output_dir>/<csv_prefix>_<well>.csv   – per-well measurements
    """
    safe_well  = _safe_well(well_label)
    out_folder = output_dir / well_label
    out_zip    = output_dir / f"{well_label}_out.zip"
    csv_path   = output_dir / f"{csv_prefix}_{safe_well}.csv"
    folder_ok  = out_folder.is_dir() and any(out_folder.iterdir())
    zip_ok     = out_zip.exists() and out_zip.stat().st_size > 0
    csv_ok     = csv_path.exists() and csv_path.stat().st_size > 0
    out_ok     = zip_ok if compressed_output else folder_ok
    return out_ok and csv_ok


def _unique_tokens_preserve_order(tokens: "list[str]") -> "list[str]":
    """Stable de-dupe for channel tokens, ignoring case and surrounding whitespace."""
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        cleaned = str(tok or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key not in seen:
            out.append(cleaned)
            seen.add(key)
    return out


# ---------------------------------------------------------------------------
# Zip-aware orchestration
# ---------------------------------------------------------------------------

def _sweep_stale_scratch_dirs(root: Path, *, extra_extract: bool) -> None:
    """Remove stale ``_tmp_*`` staging dirs from a previous crashed run under
    *root*, but keep any ``_tmp_images_*`` dir holding ``.fov_*.done`` checkpoint
    markers (v5.2.0) so the worker resumes from them. ``extra_extract`` also
    sweeps the zip-mode ``_tmp_extract_*`` scratch (audit A10)."""
    if extra_extract:
        for _stale in sorted(root.glob("_tmp_extract_*")):
            if _stale.is_dir():
                log.warning("Removing stale temporary directory from previous run: %s", _stale.name)
                remove_directory(_stale)
    for _stale in sorted(root.glob("_tmp_images_*")):
        if not _stale.is_dir():
            continue
        if any(_stale.glob(".fov_*.done")):
            n_done = sum(1 for _ in _stale.glob(".fov_*.done"))
            log.info(
                "Keeping checkpoint dir %s (%d FOV(s) already done)",
                _stale.name, n_done,
            )
            continue
        log.warning("Removing stale temporary directory from previous run: %s", _stale.name)
        remove_directory(_stale)


def _run_well_worker_pool(
    *,
    items: "list[tuple[str, Path]]",
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
    for well_label, path in items:
        if not force and not incremental and output_exists(well_label):
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

    _ensure_stardist_runtime_deps()

    if incremental:
        log.info("Incremental: %d well(s) total — merging only new FOV-groups.",
                 len(to_process))
    log.info("%s mode: %d well(s) to process, %d worker(s).", mode_label, len(to_process), workers)

    all_records: list[dict] = []
    # Two DISTINCT failure classes, kept apart so the summary isn't misleading:
    #   * wells_failed_entirely — the worker raised, so the well produced NO
    #     output at all (no CSV, no out.zip); it is absent from the plate map.
    #   * partial_wells{well -> failed FOV-group names} — the well finished and
    #     wrote a CSV + out.zip from its successful FOV-groups (so it shows as
    #     complete / green on the plate map), but some FOV-groups failed and are
    #     missing from that output.
    # Previously both were flattened into one list and per-FOV failure *names*
    # were counted as "wells failed", so a run with e.g. 12 failed FOV-groups
    # across a couple of otherwise-complete wells reported "12 well(s)/pair(s)
    # failed" while those wells looked done — exactly the confusing mismatch this
    # separation fixes.
    wells_failed_entirely: list[str] = []
    partial_wells: dict[str, list[str]] = {}

    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_worker_init,
        initargs=(force_cpu, threads_per_worker,
                  shared_kwargs_template.get("segmentation_method", DEFAULT_SEGMENTATION_METHOD)),
    ) as executor:
        future_to_well = {
            submit_task(executor, well_label, path): well_label
            for well_label, path in to_process
        }
        for future in as_completed(future_to_well):
            well_label = future_to_well[future]
            try:
                records, failed = future.result()
                all_records.extend(records)
                if failed:
                    partial_wells[well_label] = list(failed)
            except Exception as exc:           # noqa: BLE001
                _report_worker_failure(well_label, output_dir, exc)
                wells_failed_entirely.append(well_label)

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
        len(items), len(to_process), n_skipped, len(all_records),
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
    well_zips: "list[tuple[str, Path]]",
    shared_kwargs_template: dict,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    workers: int,
    csv_prefix: str,
    force: bool = False,
    incremental: bool = False,
    incremental_prior_dir: "Path | None" = None,
    force_cpu: bool = False,
    threads_per_worker: int = 0,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> "list[str]":
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

    def _submit(executor, well_label, zip_path):
        return executor.submit(
            process_well_zip_task,
            well_label, zip_path, output_dir, shared_kwargs_template,
            nuclear_token, fluor_tokens, csv_prefix, schema, sep,
            incremental, incremental_prior_dir,
        )

    return _run_well_worker_pool(
        items=well_zips, output_dir=output_dir, workers=workers,
        force=force, incremental=incremental, force_cpu=force_cpu,
        threads_per_worker=threads_per_worker,
        shared_kwargs_template=shared_kwargs_template, mode_label="Zip",
        # Zip staging (extract + images) lives under the scratch base.
        stale_sweep=lambda out: _sweep_stale_scratch_dirs(
            scratch_base_for(out), extra_extract=True),
        output_exists=lambda well: output_exists_for_well(output_dir, well, csv_prefix),
        submit_task=_submit,
    )


def process_well_folders(
    input_dir: Path,
    output_dir: Path,
    well_folders: "list[tuple[str, Path]]",
    shared_kwargs_template: dict,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    workers: int,
    csv_prefix: str,
    force: bool = False,
    incremental: bool = False,
    incremental_prior_dir: "Path | None" = None,
    force_cpu: bool = False,
    threads_per_worker: int = 0,
    schema: "list[str] | None" = None,
    sep: str = DEFAULT_SEP,
) -> "list[str]":
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

    def _submit(executor, well_label, folder_path):
        return executor.submit(
            process_well_folder_task,
            well_label, folder_path, output_dir, shared_kwargs_template,
            nuclear_token, fluor_tokens, csv_prefix,
            schema, sep, incremental, incremental_prior_dir,
        )

    return _run_well_worker_pool(
        items=well_folders, output_dir=output_dir, workers=workers,
        force=force, incremental=incremental, force_cpu=force_cpu,
        threads_per_worker=threads_per_worker,
        shared_kwargs_template=shared_kwargs_template, mode_label="Folder",
        # Folder-mode staging (_tmp_images_*) lives directly under output_dir;
        # there is no zip-extract scratch to sweep.
        stale_sweep=lambda out: _sweep_stale_scratch_dirs(out, extra_extract=False),
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


def _effective_fluor_tokens_for_sidecar(
    fluor_tokens: "list[str]",
    *,
    nuclear_token: str,
    segmentation_method: str,
    cytoplasm_token: str,
) -> "list[str]":
    """Channels that end up with quantified intensity columns in the CSVs."""
    ordered = [str(nuclear_token or "").strip(), *[str(tok or "").strip() for tok in fluor_tokens]]
    if segmentation_method == "stardist_seeded_watershed_cell":
        ordered.append(str(cytoplasm_token or "").strip())
    out: "list[str]" = []
    seen: "set[str]" = set()
    for tok in ordered:
        if not tok:
            continue
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
    return out


def _parse_numeric_token(value: str) -> "float | None":
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _collect_image_stems(input_dir: Path) -> "set[str]":
    stems: "set[str]" = set()
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
) -> "list[str]":
    fields = [f.strip().lower() for f in filename_schema.split(":")]
    try:
        field_idx = fields.index(field_name)
    except ValueError:
        return []
    values: "set[str]" = set()
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
) -> "list[str]":
    def _sort_key(tp: str):
        h = _parse_timepoint_hours(tp)
        return (
            h != h,  # NaN sentinel sorts last (_parse_timepoint_hours -> float)
            (h if h == h else 0.0),
            tp,
        )
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
) -> "list[str]":
    def _sort_key(fov: str):
        n = _parse_numeric_token(fov)
        return (n is None, n or 0.0, fov)
    return _collect_available_schema_values(
        input_dir,
        filename_schema=filename_schema,
        filename_sep=filename_sep,
        field_name="fov",
        sort_key=_sort_key,
    )


def write_pipeline_info(
    output_dir: Path,
    *,
    input_dir: Path,
    filename_schema: str,
    filename_sep: str,
    nuclear_token: str,
    fluor_tokens: "list[str]",
    smfish_tokens: "list[str]",
    segmentation_method: str,
    cytoplasm_token: str,
    min_nucleus_area_px: int,
    shape_features: "list[str] | None" = None,
    qc_options: "dict | None" = None,
    phase_token: str = "",
    execution_options: "dict | None" = None,
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
        "fluor_tokens": _effective_fluor_tokens_for_sidecar(
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
            for preserved_key in (
                "cell_gating",
                "sample_definitions",
                "ratios",
                "notes",
            ):
                if preserved_key in existing and preserved_key not in info:
                    info[preserved_key] = existing[preserved_key]
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(info, indent=2))
        os.replace(tmp, p)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
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
            "Channel token of the phase / brightfield image segmented by the "
            "'cellpose_labelfree' preset. Ignored by the other methods."
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
                       "0 (default) = auto-select based on cpu_count (always 4). "
                       "Common values: "
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

    return p

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser    = build_parser()
    args      = parser.parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()

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
        # macOS: tensorflow-metal handles GPU scheduling internally.
        # Use fewer workers (Metal serialises GPU calls anyway) and don't
        # pin as many CPU threads — Metal does its own thread management.
        workers            = max(1, args.tf_threads if args.tf_threads > 0 else 2)
        threads_per_worker = tf_threads
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
    log.info("CPUs     : %d total, %d available (1 reserved for main process)",
             cpu_count, available)
    log.info("Device   : %s", _device_str)
    log.info("TF threads/worker : %d  (workers: %d x %d = %d cores)",
             tf_threads, workers, tf_threads, workers * tf_threads)
    if workers_override:
        log.info("Workers override : --workers=%d", args.workers)

    fluor_tokens_for_quant = [args.nuclear_token, *list(args.fluor_tokens)]
    if (
        args.segmentation_method == "stardist_seeded_watershed_cell"
        and args.cytoplasm_token
    ):
        fluor_tokens_for_quant.append(args.cytoplasm_token)
    fluor_tokens_for_quant = _unique_tokens_preserve_order(fluor_tokens_for_quant)

    # Per-channel top-hat flags and radii: one entry per fluor token.
    n_fluor            = len(fluor_tokens_for_quant)
    tophat_fluor       = [not args.no_tophat_fluor] * n_fluor
    tophat_radius_fluor = [args.tophat_radius_fluor] * n_fluor
    fft_small_fluor     = [args.fft_small_fluor] * n_fluor

    log.info("Segmentation method : %s", args.segmentation_method)
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
    )

    # If the selected input is an "in" directory, normalise any loose TIF/TIFF
    # files into per-well folders before selecting processing mode.
    if input_dir.name.lower() == "in":
        organize_loose_tifs_into_well_folders(input_dir, schema=schema, sep=sep)

    # Precompute per-channel flat-field models once (basic / empty_well). The
    # gaussian mode needs no precompute (estimated per image in the worker), and
    # 'none' is a no-op. Done after the loose-TIF organize so the files are on
    # disk under input_dir.
    if args.flatfield in ("basic", "empty_well"):
        shared_kwargs["flatfield_models"] = build_flatfield_models(
            input_dir,
            tokens=fluor_tokens_for_quant,
            schema=schema,
            sep=sep,
            mode=args.flatfield,
            background_wells=_split_background_wells(args.background_well),
        )
        log.info("Flat-field (%s): built field(s) for channel(s): %s",
                 args.flatfield, sorted(shared_kwargs["flatfield_models"]) or "(none found)")

    # Precompute per-channel additive background fields once (--background
    # empty_well). top_hat / sliding_disk estimate per image in the worker.
    if args.background == "empty_well":
        shared_kwargs["background_models"] = build_background_models(
            input_dir,
            tokens=[args.nuclear_token] + list(fluor_tokens_for_quant),
            schema=schema,
            sep=sep,
            background_wells=_split_background_wells(args.background_well),
        )
        log.info("Background (empty_well): built field(s) for channel(s): %s",
                 sorted(shared_kwargs["background_models"]) or "(none found)")

    # ── Decide mode: zip, folder, or flat ───────────────────────────────────
    well_zips    = find_well_zips(input_dir)
    well_folders = find_well_folders(input_dir) if not well_zips else []

    # Per-well failures are surfaced here so main() can signal them via the
    # process exit code (audit E5) rather than always exiting 0.
    failed_wells: "list[str]" = []
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

    # Auto-threshold pass: now that every well has been processed and the
    # output zips + pipeline_info sidecar are on disk, compute a default
    # ThreshFracOn cut per channel via Otsu on a balanced per-cell /
    # background-pixel distribution. The result lands in
    # ``pipeline_info.json``'s ``cell_gating.thresh_frac_on`` block and the
    # Cell Gating tab picks it up the next time the dataset is loaded.
    #
    # The implementation lives entirely inside this module (see
    # ``_estimate_thresholds_standalone`` above) so the pipeline can produce
    # auto-thresholds even when ``well_viewer`` is not installed — the only
    # third-party dependencies are skimage's threshold_otsu and tifffile's
    # imread, which the rest of the pipeline already uses. Any failure is
    # logged and the pipeline continues — auto-threshold is informational
    # only and the user can always set thresholds manually in the GUI.
    #
    # v5.4.7: skipped when ``--no_auto_threshold`` is set (AllWell's
    # batched remote runner passes this so the per-batch pass doesn't
    # overwrite the prior batch's defaults with incomplete-plate data;
    # the runner re-runs it once locally after all batches drain).
    if args.no_auto_threshold:
        log.info("Auto-threshold skipped (--no_auto_threshold).")
    else:
        try:
            thresholds = _estimate_thresholds_standalone(
                output_dir,
                fluor_channels=fluor_tokens_for_quant,
                filename_schema=args.filename_schema,
                filename_sep=args.filename_sep,
                log=log,
            )
            written = _apply_thresholds_to_pipeline_info(output_dir, thresholds, log)
            if written:
                log.info(
                    "Auto-threshold defaults written for %d channel(s): %s",
                    len(written),
                    ", ".join(f"{ch.upper()}={v:.4g}" for ch, v in written.items()),
                )
            else:
                log.info("Auto-threshold pass produced no new defaults.")
        except Exception as exc:
            log.warning("Auto-threshold pass failed: %s", exc)

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
