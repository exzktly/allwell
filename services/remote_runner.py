"""Remote-execution runner for ``process_microscopy.py``.

Stages per-well zips to an SSH host, launches the pipeline remotely, and
streams outputs back as wells complete. Designed as a drop-in replacement
for ``spawn_pipeline()`` from the GUI's point of view: the Popen handle
returned by :meth:`RemoteRunner.spawn` looks identical (stdout pipe, pgid
kill works, return code semantics preserved) so ``PipelineRunner`` and
``ProgressTracker`` need only a single-line branch to opt in.

The five transfer-minimizing levers from the v5.0.0 plan are implemented
here:

* **Per-well zip granularity** — :class:`LocalZipper` runs the existing
  ``WellPlateZipper`` locally first, so a 1500-image plate ships as
  ~100 large zips instead of 1500 small TIFs.
* **Persistent dataset cache** — :class:`DatasetCache` keys uploads by a
  stat-based content hash so re-runs of the same input are a metadata
  roundtrip, not a re-upload.
* **CSV-by-default output filter** — :class:`OutputFilter` decides which
  files to materialize locally and toggles ``--no_save_tophat`` on the
  remote so QC TIFs aren't even produced when not wanted.
* **Pipelined per-well execution** — :class:`Fetcher` watches the
  pipeline's stdout for the existing ``— done.`` marker and starts
  pulling each well's output the moment it lands, overlapping
  download with later-well compute.
* **Verified cleanup** — :meth:`RemoteRunner.verify_and_cleanup` only
  fires the remote ``rm -rf`` after the local output manifest is
  confirmed complete; a missing local file aborts the cleanup so the
  user can retry without losing remote state.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import contextlib
from functools import lru_cache
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from collections.abc import Callable, Iterable

from services.remote_config import (
    DEFAULT_CONDA_ENV,
    DEFAULT_REMOTE_PYTHON,
    RemoteHost,
    resolve_rsync_binary,
    resolve_ssh_for_rsync,
    to_rsync_path,
    update_bandwidth,
)
# Stdlib-only (fov, tp) done-key helpers — shared with pipeline.io (audit A2).
# This module import must stay imaging-stack-free; incremental_keys is csv/
# zipfile/pathlib only, so it never drags numpy/tifffile into the remote client.
from util.fov import sort_key as fov_sort_key
from util.timepoint import sort_key as tp_sort_key
from pipeline.incremental_keys import (
    fov_tp_from_name,
    read_done_fov_keys,
    read_done_fov_keys_from_zip,
)
from util.aw_safe import swallowed
# The shared "retry an OSError with a backoff, then fail with an actionable
# message" shield. A remote run does as much I/O on the user's own volumes as a
# local one — it reads the whole plate off the input volume to stage the upload
# and writes every fetched well back onto the output volume — so it needs the
# same protection the pipeline's result writes have. ``pipeline.io`` owns the
# pipeline-side wrapper; this module cannot import it (that would drag numpy /
# tifffile into the remote client), which is why the loop lives in ``util``.
from util.retry_io import retry_io, volume_hint
from util.zip_write import atomic_zip


def _scratch_dir() -> Path:
    """Base directory for AllWell's local scratch — input staging, transit
    output zips, and incremental merge temps.

    Resolution order, matching ``pipeline.io.scratch_base_for`` exactly:

    1. ``$ALLWELL_SCRATCH_DIR`` — the redirectable-scratch env the GUI sets from
       its "Scratch disk" field, so a user whose data lives on a NAS but whose
       OS temp is a small system drive (Windows ``%TEMP%`` on ``C:``) can put
       the potentially-GB-sized staging rewrite on a roomy volume.
    2. ``~/.allwell/scratch`` — the default.
    3. The OS temp dir — last resort, when the home directory is unwritable.

    The two resolvers agree deliberately. They used to differ: the pipeline
    defaulted to ``~/.allwell/scratch`` while this one defaulted to the OS temp
    dir, so a remote run wrote into a path the user had never heard of and could
    not find — ``/var/folders/jj/…/T`` on macOS, and on Linux frequently a tmpfs,
    i.e. RAM. One default, stated in one place, is one thing to reason about when
    a volume fills.

    This module cannot import ``pipeline.io`` to share the constant (that would
    drag numpy and tifffile into the remote client, which
    ``test_architecture_contracts`` forbids), so the path is repeated here and
    ``test_remote_runner`` pins the two to the same value.
    """
    override = os.environ.get("ALLWELL_SCRATCH_DIR", "").strip()
    if override:
        try:
            p = Path(override).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            return p
        except OSError as exc:
            # Don't silently ignore a user-set scratch dir — falling back
            # elsewhere can later fill a small system drive with GB of staging
            # (audit E14). Surface it so the misconfiguration is visible.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "ALLWELL_SCRATCH_DIR=%r is unusable (%s); falling back to "
                "~/.allwell/scratch", override, exc)
    try:
        home = Path.home() / ".allwell" / "scratch"
        home.mkdir(parents=True, exist_ok=True)
        return home
    except OSError:
        return Path(tempfile.gettempdir())


def _new_scratch_dir(prefix: str) -> Path:
    """``mkdtemp`` rooted at :func:`_scratch_dir` (honours ALLWELL_SCRATCH_DIR)."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(_scratch_dir())))


def sweep_stale_scratch(*, max_age_h: float = 6.0,
                        log_fn: Callable[[str], None] | None = None) -> int:
    """Best-effort removal of leftover ``allwell-*`` scratch from runs killed
    before their cleanup ran (a hard quit skips the ``finally`` that deletes the
    staging dir, leaking a full input copy onto the scratch volume). Only
    removes entries older than *max_age_h* so a concurrent run's live staging is
    never touched. Returns the number removed."""
    base = _scratch_dir()
    cutoff = time.time() - max_age_h * 3600.0
    removed = 0
    try:
        entries = list(base.glob("allwell-*"))
    except OSError:
        return 0
    for p in entries:
        try:
            if p.stat().st_mtime > cutoff:
                continue
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
            removed += 1
        except OSError:
            continue
    if removed and log_fn:
        log_fn(f"[remote] cleaned {removed} stale scratch item(s) from {base}\n")
    return removed


# Tail of the exhausted-budget message for a client-side transfer. The remote
# runner's knobs are different from the pipeline's (there is no ``--workers``
# here), so it carries its own wording rather than the generic one.
_TRANSFER_ADVICE = (
    "A repeated failure on the same file usually means one of the volumes "
    "involved is a network mount that is dropping its session under load. "
    "Keep the input and output folders on local storage for the run and copy "
    "the results across afterwards, or re-run with the plate mounted over a "
    "steadier link."
)


def _staging_free_bytes(path: Path) -> int | None:
    """Free bytes on the staging volume, or None when it can't be determined."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


# Markers the pipeline's stdout emits; reused here so the Fetcher can
# trigger on the same lines the GUI's ProgressTracker already parses.
#
# v5.2.17: the actual line emitted by ``process_microscopy.py`` is
# ``log.info("Well %s — done.", well_label)``, which the basicConfig
# formatter renders as ``2026-05-27 10:30:45  INFO      Well A01 —
# done.`` — i.e. the line does NOT start with the well label, it
# starts with the timestamp. The previous regex anchored on ``^`` and
# was matched (not searched), so it never fired in production. As a
# result the Fetcher never pulled any per-well outputs and the user
# saw "rsync never completes". Match with ``.search()`` against the
# "Well A01 — done." substring instead. Also support the legacy bare
# ``A01 — done.`` form just in case anything still emits it.
#
# The separator is matched as a generic ``\s+\S+\s+`` token rather than the
# literal " — " so a per-well download still triggers if the em-dash is mangled
# in transit (a UTF-8 remote stream decoded with a cp1252 Windows locale arrives
# as "Well A01 â€" done."). Without this the Fetcher never fired per-well during
# the run — no download progress, and the out dir only filled from the
# end-of-run backstop.
_RE_WELL_DONE = re.compile(r"(?:\bWell\s+)?(?P<well>[A-Ha-h]\d{2})\s+\S+\s+done\.")
_RE_MODE_WELLS = re.compile(r"(?:Zip mode|Flat mode|Folder mode):\s+(\d+)\s+well")

# Default output-filter glob set: just CSVs and the top-level metadata
# sidecar. The remote also gets --no_save_tophat under this filter so QC
# files aren't even produced.
_CSV_ONLY_GLOBS: tuple[str, ...] = ("*.csv", "pipeline_info.json")


# Files this framework needs on the remote in order to run the pipeline.
# Kept minimal on purpose: process_microscopy.py plus its local-module
# imports, plus the requirements list (so the bootstrap script knows what
# pip-installs to do). Paths are relative to the repo root.
#
# Synced before every run by :meth:`RemoteRunner.spawn` so a user who
# edits any of these files locally doesn't end up running stale code on
# the remote — the per-run cost is a few hundred KB and a quick rsync
# stat-pass when nothing changed.
_REQUIRED_REMOTE_FILES: tuple[str, ...] = (
    "process_microscopy.py",
    # General Qt-free utilities (well-token parsing, logged-swallow) imported by
    # process_microscopy at module top level, so the remote needs the package.
    "util/__init__.py",
    "util/aw_safe.py",
    # The retry-with-backoff shield behind every pipeline write that lands on
    # the output volume (pipeline.io imports it at module scope).
    # Rebinds sys.stdout / sys.stderr when a frozen windowed build nulls them;
    # imported by process_microscopy at module load (and by WellPlateZipper). A
    # remote run never hits the frozen-windowed case, but the manifest must list
    # every ``util/`` file so the completeness guard (test_remote_manifest) passes.
    "util/frozen_io.py",
    "util/retry_io.py",
    # Serpentine plate-position labelling for packed OME-TIFF input. The
    # de-interleave runs client-side (before any remote sync), so a remote run
    # never imports this — but the manifest must list every ``util/`` file so
    # the completeness guard (test_remote_manifest) passes.
    "util/plate_grid.py",
    "util/schema.py",
    # FOV-token numeric ordering, imported by process_microscopy
    # (``_collect_available_fovs``) so the remote run needs it.
    "util/fov.py",
    # Channel-name → role heuristic (converter + Analyze). Client-side only, but
    # the manifest mirrors every ``util/`` file.
    "util/channel_roles.py",
    # Filename-schema inference for the Analyze input scan; client-side only.
    "util/schema_infer.py",
    "util/timepoint.py",
    "util/well_token.py",
    # Atomic + verified publication of the per-well archives, imported by
    # process_microscopy and pipeline.io — without it the remote run aborts
    # before it compresses its first well.
    "util/zip_write.py",
    # The Qt-free ``pipeline`` package: analysis-domain modules (background math,
    # auto-threshold, flat-field, shape-feature catalog) + the Cellpose/StarDist
    # segmenters and atomic writers. process_microscopy re-imports it at module
    # top level (Torch/TensorFlow stay lazy), so the remote needs the whole
    # package or the run aborts with ModuleNotFoundError.
    "pipeline/__init__.py",
    "pipeline/auto_threshold.py",
    "pipeline/background.py",
    "pipeline/cellpose.py",
    "pipeline/flatfield.py",
    "pipeline/incremental_keys.py",
    "pipeline/io.py",
    # Packed OME-TIFF de-interleave. Runs client-side before remote sync (the
    # remote receives ordinary loose TIFs), but listed so the manifest stays a
    # complete mirror of ``pipeline/`` per test_remote_manifest.
    "pipeline/ome_explode.py",
    "pipeline/run_report.py",
    "pipeline/shape_features.py",
    "pipeline/stardist.py",
    "pipeline/threshold_optimize.py",
    # v5.2.1: headless-only requirements (no PySide6/pyobjc/pyinstaller
    # — the remote doesn't run the GUI). See _Docs/requirements.txt for
    # the GUI superset.
    "_Docs/requirements-remote.txt",
)


def repo_root() -> Path:
    """Absolute path to the repo root from which required-file paths
    are resolved. Single source of truth used by the runner and the
    bootstrap script's Python helpers."""
    return Path(__file__).resolve().parent.parent


class OutputFilter(str, Enum):
    """What to bring back from the remote when a run completes."""

    FULL = "full"
    """Full equivalence — every file a local run would have produced."""

    CSV_ONLY = "csv-only"
    """Just the per-well CSVs and ``pipeline_info.json``. Also injects
    ``--no_save_tophat`` so the remote skips QC generation entirely."""


class LocalInputMode(str, Enum):
    """How the user's input would have been processed locally.

    Recorded up-front so the Fetcher can reconstruct an output tree that's
    indistinguishable from a local run.
    """

    ZIP = "zip"
    """Input was already ``{well}.zip`` files. Local zip-mode run would
    produce ``{well}_out.zip`` files in ``output_dir`` — Fetcher drops
    them in unchanged."""

    FOLDER = "folder"
    """Input was ``{well}/`` subfolders. Local run extracts the work into
    loose files in ``output_dir`` — Fetcher unzips each remote
    ``{well}_out.zip`` after fetch."""

    FLAT = "flat"
    """Input was loose TIFs in the root. Local run would compress them
    into per-well zips first (since ``compress_input_well_folders``
    defaults to True), then proceed identically to zip mode. Treated
    like ZIP from the Fetcher's perspective."""

    MIXED = "mixed"
    """Input has BOTH ``{well}.zip`` files AND ``{well}/`` folders for
    different wells — the signature of a previous run that died
    partway through ``compress_input_well_folders`` (or a manual
    half-conversion). v5.2.21: instead of silently picking ZIP mode
    and ignoring the remaining folders, treat the input as a resume
    point: trust the existing zips, and zip the leftover folders
    into the staging dir so the run includes every well."""


@dataclass
class RunEstimate:
    """Bandwidth-aware break-even estimate. Returned by
    :meth:`RemoteRunner.estimate` before each run so the user can see
    whether going remote is actually worth it on this network."""

    bytes_to_transfer: int
    transfer_s: float
    remote_compute_s: float
    local_compute_s: float

    @property
    def will_win(self) -> bool:
        return self.local_compute_s > self.transfer_s + self.remote_compute_s

    def format(self) -> str:
        gb = self.bytes_to_transfer / (1024**3)
        return (
            f"transfer {gb:.2f} GB ({_fmt_secs(self.transfer_s)}) + "
            f"remote compute {_fmt_secs(self.remote_compute_s)} = "
            f"{_fmt_secs(self.transfer_s + self.remote_compute_s)}  "
            f"vs local {_fmt_secs(self.local_compute_s)} — "
            f"{'remote wins' if self.will_win else 'LOCAL would be faster'}"
        )


def _fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    return f"{int(s // 60)}m{int(s % 60):02d}s"


def _zip_is_complete(path: Path) -> bool:
    """True iff *path* is a fully-transferred, valid zip.

    ``zipfile.is_zipfile`` looks for the end-of-central-directory record, which
    only exists once the whole archive has landed — a truncated / partial
    download fails the check. Lets the Fetcher distinguish "rsync errored but the
    file is actually here" from "the file is incomplete"."""
    try:
        return path.exists() and zipfile.is_zipfile(path)
    except OSError:
        return False


# ────────────────────────────────────────────────────────────────────────────
# LocalZipper
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class ZippedInput:
    """The output of :class:`LocalZipper`: a directory of ``{well}.zip``
    files plus the local input mode the user would have triggered."""

    zip_dir: Path
    mode: LocalInputMode
    well_zips: tuple[Path, ...]
    """Sorted ``{well}.zip`` paths inside ``zip_dir``."""

    cleanup: Callable[[], None] | None = None
    """Optional callback that removes any temporary staging directory the
    zipper created. ``None`` when the input was already in zip form."""

    on_pipeline_success: Callable[[], None] | None = None
    """Optional callback fired ONLY after a clean pipeline run-to-
    completion. Used to delete source loose TIFs / well folders that
    were zipped into ``input_dir`` in-place, so a successful run
    converts the user's input folder from loose-TIF mode to zip mode
    (next run sees only zips, skips the zipper entirely). Never
    fires on failure / cancel / partial completion — loose sources
    stay intact so the user can re-run from scratch."""

    incremental: bool = False
    """True when this is an incremental remote run: the staged zips carry only
    the new-FOV members (client-side split), so the Fetcher must MERGE the
    partial outputs into the existing local CSV / out-zip rather than overwrite."""

    up_to_date_wells: tuple[str, ...] = ()
    """Wells dropped from the upload because their local output already covers
    every input FOV. The runner emits a synthetic ``[remote] ✓ X: up to date``
    for each so the dashboard marks them complete."""


def stage_local_zips(
    input_dir: Path,
    *,
    filename_schema: str,
    filename_sep: str,
    fluor_tokens: Iterable[str] | None = None,
    nuclear_token: str | None = None,
    log_fn: Callable[[str], None] | None = None,
    proc_hook: Callable[[subprocess.Popen | None], None] | None = None,
    move_source_tifs: bool = False,
    incremental: bool = False,
    prior_output_dir: Path | None = None,
    csv_prefix: str = "measurements",
) -> ZippedInput:
    """Return a directory of per-well zips for ``input_dir``.

    For inputs that are already ``{well}.zip``, this is a no-op pointer.
    For loose-TIF or folder layouts, runs the existing
    ``WellPlateZipper`` (re-used via :mod:`services.input_resolution_service`)
    into a temp directory and returns that.

    Phase-3 selective staging: when ``fluor_tokens`` is restricted, the
    selective filter rewrites each well's zip to keep only the requested
    channels — cutting upload by the unused-channel fraction. The nuclear
    token is always kept (segmentation needs it).
    """
    from services.input_resolution_service import run_zipper

    # Reclaim local scratch leaked by a previously-killed run before we stage a
    # fresh (potentially multi-GB) copy of the input.
    sweep_stale_scratch(log_fn=log_fn)

    input_dir = input_dir.resolve()
    # Scanning the input volume to classify the layout is itself slow enough to
    # look like a hang on a spinning external disk, and it is the very first
    # thing that happens after "[remote] target:". Say so before starting.
    if log_fn is not None:
        log_fn(f"[remote] scanning input: {input_dir}\n")
    mode = _detect_local_input_mode(input_dir)
    if log_fn is not None:
        log_fn(f"[remote] input layout: {mode.value}\n")

    if mode is LocalInputMode.ZIP:
        well_zips = tuple(sorted(input_dir.glob("[A-H][0-9]*.zip")))
        if not well_zips:
            raise RuntimeError(
                f"Input dir reports zip-mode but contains no A01.zip-style files: {input_dir}"
            )
        if fluor_tokens is None and not incremental:
            # No filtering — use the user's zips directly (read-only).
            return ZippedInput(zip_dir=input_dir, mode=mode, well_zips=well_zips, cleanup=None)
        # v5.2.18: copy the user's zips into a staging dir BEFORE
        # filtering. Pre-v5.2.18 the filter rewrote zips in place at
        # ``input_dir`` — if the user mis-typed a fluor channel name
        # (e.g. "GP" for "GFP"), every fluor image got dropped from
        # the user's original dataset on disk. The user's input dir
        # must be treated as read-only. The incremental split rewrites
        # zips too, so it likewise requires staging.
        # Plan before moving anything. Reading each archive's member list is a
        # small read of its central directory, so the whole plate can be planned
        # for the cost of a few seeks even over a network mount — and then only
        # the members that will actually be uploaded are ever read. An earlier
        # shape copied every archive in full into scratch and *then* rewrote it
        # down to the members it wanted, which on a NAS-resident dataset dragged
        # the entire plate across the network before a single byte went to the
        # remote.
        if log_fn is not None:
            log_fn(f"[remote] planning {len(well_zips)} well archive(s)…\n")
        plans, up_to_date_list, zero_match = _plan_staging(
            well_zips,
            keep_tokens=(_required_tokens(nuclear_token, fluor_tokens)
                         if fluor_tokens is not None else None),
            incremental=incremental,
            prior_output_dir=prior_output_dir,
            csv_prefix=csv_prefix,
            filename_schema=filename_schema,
            filename_sep=filename_sep,
            log_fn=log_fn,
        )
        _raise_on_zero_channel_match(zero_match)
        # Opened only if something actually has to be rewritten; when the
        # filter drops nothing from any archive there is no local copy at all.
        staging = _open_staging_if_needed(plans, log_fn=log_fn)
        try:
            if log_fn is not None and staging is not None:
                _to_move = sum(len(p_.keep) for p_ in plans
                               if p_.keep is not None)
                n_rewrite = sum(1 for p_ in plans if p_.keep is not None)
                log_fn(f"[remote] rewriting {n_rewrite} well archive(s), "
                       f"{_to_move} member(s) → {staging}\n")
            staged_zips = _materialize_staging(plans, staging, log_fn=log_fn)
            up_to_date = tuple(up_to_date_list)
            return ZippedInput(
                zip_dir=staging or input_dir,
                mode=mode,
                well_zips=staged_zips,
                cleanup=(None if staging is None else
                         (lambda d=staging: shutil.rmtree(d, ignore_errors=True))),
                incremental=incremental,
                up_to_date_wells=up_to_date,
            )
        except Exception:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise

    if mode is LocalInputMode.MIXED:
        # v5.2.21: input has zips for some wells and folders for
        # others — the signature of a previous run that died partway
        # through compress_input_well_folders. Resume by trusting the
        # existing zips and zipping the leftover folders into a
        # staging dir.
        return _stage_mixed_mode(
            input_dir,
            fluor_tokens=fluor_tokens,
            nuclear_token=nuclear_token,
            log_fn=log_fn,
        )

    # Folder or flat mode: shell out to WellPlateZipper. The zipper writes
    # ``{well}.zip`` files directly into ``input_dir`` (alongside the
    # source loose TIFs / well folders). Source artifacts persist
    # through the entire run so a failure can be re-tried from scratch;
    # ``on_pipeline_success`` deletes them on clean completion so the
    # input dir converts from loose-TIF / folder mode to plain ZIP mode
    # for future runs (next ``_detect_local_input_mode`` call returns
    # ``ZIP`` and the zipper is skipped). This replaces the old behavior
    # of staging into ``tempfile.mkdtemp(prefix="allwell-zips-")``,
    # which (1) ate temp-dir space on machines with small /tmp, and
    # (2) discarded all zips at run end, forcing a full re-zip on
    # every re-run.
    #
    # Snapshot the source artifacts BEFORE running the zipper so the
    # post-success deletion sees exactly the set the user started with,
    # not the post-zipper state where the zipper's own output sits
    # alongside the originals.
    source_loose_tifs: tuple[Path, ...] = tuple()
    source_well_folders: tuple[Path, ...] = tuple()
    if mode is LocalInputMode.FLAT:
        source_loose_tifs = tuple(sorted(
            p for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF")
            for p in input_dir.glob(pat)
        ))
    elif mode is LocalInputMode.FOLDER:
        source_well_folders = tuple(sorted(
            p for p in input_dir.iterdir()
            if p.is_dir() and re.fullmatch(r"[A-H]\d{1,2}", p.name)
        ))
        # v5.x.x resume support: a prior ``--move-source-tifs`` run
        # may have crashed mid-way, leaving SOME TIFs in well
        # subfolders and SOME still loose at the top level. Capture
        # the leftovers — they go into the same snapshot as the
        # fresh-FLAT case so the post-success hook deletes them too
        # (or, for move-mode, the zipper itself will have consumed
        # them by run end and the unlink is a no-op caught by the
        # OSError handler).
        source_loose_tifs = tuple(sorted(
            p for pat in ("*.tif", "*.tiff", "*.TIF", "*.TIFF")
            for p in input_dir.glob(pat)
        ))
        if source_loose_tifs and log_fn:
            log_fn(
                f"[stage] resume: found {len(source_loose_tifs)} leftover "
                f"loose TIF(s) at top level + {len(source_well_folders)} "
                f"well folder(s); consuming leftovers into existing folders.\n"
            )

    # Capture zips that existed BEFORE this stage runs — we must NOT
    # delete those on a partial-zipper failure (they could be user
    # data from a prior run).
    pre_existing_zips = {p for p in input_dir.glob("[A-H][0-9]*.zip")}

    # Step 0 (v5.x.x): decide whether the zipper actually needs to
    # run. The framework gets here for FLAT mode (loose TIFs at top
    # level), fresh FOLDER mode (well subfolders), and the resume
    # cases (partial move, post-copy crash). The zipper only has work
    # to do if there are loose TIFs at top level that DON'T already
    # have a copy in their target well subfolder — i.e. truly
    # unconsumed sources. Re-copying already-consumed TIFs is just
    # wasted I/O (and on a copy-mode-after-crash retry, also produces
    # confusing logs as the user watches the zipper "do work" against
    # files that are already in their folders).
    def _basenames_inside_well_folders() -> set[str]:
        names: set[str] = set()
        for p in input_dir.iterdir():
            if not (p.is_dir() and re.fullmatch(r"[A-H]\d{1,2}", p.name)):
                continue
            for child in p.iterdir():
                if child.is_file() and child.suffix.lower() in (".tif", ".tiff"):
                    names.add(child.name)
        return names

    folder_basenames = _basenames_inside_well_folders()
    unconsumed_loose_tifs = tuple(
        p for p in source_loose_tifs if p.name not in folder_basenames
    )
    zipper_will_run = bool(unconsumed_loose_tifs) or (
        mode is LocalInputMode.FLAT and not folder_basenames
    )
    if mode is LocalInputMode.FOLDER and not unconsumed_loose_tifs and log_fn:
        log_fn(
            "[stage] all loose TIF(s) already copied into well folders "
            "from a prior run; skipping zipper re-run, going straight "
            "to zip-up.\n"
        )

    # Step 1: WellPlateZipper groups loose TIFs into per-well
    # subfolders (the v5.x.x ``--move-source-tifs`` flag and copy
    # default both produce folders, never zips — the zip-up step is
    # separate and lives in ``_zip_well_folder`` below). Skipped when
    # the prior-run detection above shows every loose TIF already has
    # a folder copy.
    if zipper_will_run:
        try:
            run_zipper(
                input_dir,
                input_dir,
                log_fn=log_fn,
                progress_fn=None,
                filename_schema=filename_schema,
                filename_sep=filename_sep,
                proc_hook=proc_hook,
                move_source_tifs=move_source_tifs,
            )
        except Exception:
            # Roll back ONLY the zips we created in this aborted run.
            # Source TIFs / folders stay intact so the user can re-run.
            for p in input_dir.glob("[A-H][0-9]*.zip"):
                if p not in pre_existing_zips:
                    try:
                        p.unlink()
                    except OSError:
                        pass
            raise

    # Step 2: convert each well folder into ``{well}.zip``. This is
    # the same atomic-tmp + rename pattern that ``_stage_mixed_mode``
    # uses for its leftover-folder zip-up. Without this step the
    # remote pipeline would have nothing to upload — pre-v5.x.x the
    # FLAT / FOLDER path through ``_stage_input_zips`` was broken
    # because the framework expected zips here but the zipper only
    # produces folders.
    folders_after_zipper = sorted(
        p for p in input_dir.iterdir()
        if p.is_dir() and re.fullmatch(r"[A-H]\d{1,2}", p.name)
    )
    newly_zipped: list[Path] = []
    try:
        for folder in folders_after_zipper:
            target = input_dir / f"{folder.name}.zip"
            if target.exists() and target in pre_existing_zips:
                # User-data zip from a prior run; leave it alone.
                continue
            n = _zip_well_folder(folder, target)
            if n > 0:
                newly_zipped.append(target)
                if log_fn:
                    log_fn(
                        f"[stage] zipped {folder.name} → {target.name} "
                        f"({n} TIF{'s' if n != 1 else ''}).\n"
                    )
    except Exception:
        # On a zip-up failure clean up partial zip artifacts (sources
        # stay intact for a retry).
        for p in newly_zipped:
            try:
                p.unlink()
            except OSError:
                pass
        raise

    well_zips = tuple(sorted(input_dir.glob("[A-H][0-9]*.zip")))
    if not well_zips:
        raise RuntimeError(
            "WellPlateZipper produced no per-well zips; check filename_schema and separator."
        )

    # The post-success hook needs to delete EVERY well folder the
    # zip-up step processed (both the snapshotted ones for FOLDER
    # mode and the freshly-created ones for FLAT mode). Re-scan now
    # rather than using the pre-zipper snapshot because FLAT-mode
    # folders didn't exist when we took the snapshot.
    folders_to_clean = tuple(folders_after_zipper)

    def _on_success() -> None:
        """Delete the source artifacts this stage consumed so the
        input dir is now pure zip mode:

        * loose top-level TIFs (snapshotted before the zipper ran —
          in copy mode they're the originals we need to remove; in
          move mode they're already gone and the unlink is a no-op
          caught by the OSError handler);
        * every well folder we zipped up (whether it pre-existed
          for FOLDER mode or the zipper just created it for FLAT
          mode — in either case we just successfully extracted the
          ``{well}.zip`` from it and the folder is redundant).
        """
        for tif in source_loose_tifs:
            try:
                tif.unlink()
            except OSError:
                pass
        for folder in folders_to_clean:
            try:
                shutil.rmtree(folder)
            except OSError:
                pass

    # Even in ``move_source_tifs`` mode the post-success hook still
    # has work to do (delete the well folders we just zipped up); only
    # the loose-TIF unlinks become no-ops. Don't short-circuit.
    _on_success_effective: Callable[[], None] | None = _on_success

    if fluor_tokens is not None:
        # Filter modifies zips in place. Safe here because the zips
        # we just wrote are scratch artifacts (the user's "real" data
        # is the loose TIFs / folders we snapshotted above, still
        # intact). The v5.2.18 "don't mutate user's input" invariant
        # is preserved by treating the loose sources as the canonical
        # form until the run succeeds.
        zin = _filter_channels_in_staging(
            input_dir, well_zips, mode,
            keep_tokens=_required_tokens(nuclear_token, fluor_tokens),
            log_fn=log_fn,
        )
        zin.on_pipeline_success = _on_success_effective
        return zin

    return ZippedInput(
        zip_dir=input_dir,
        mode=mode,
        well_zips=well_zips,
        cleanup=None,
        on_pipeline_success=_on_success_effective,
    )


def _detect_local_input_mode(input_dir: Path) -> LocalInputMode:
    """Mirror the discovery logic in process_microscopy's main(), with
    one extension over the local script: detect MIXED mode (zips +
    folders for different wells) so the staging path can resume from
    a half-completed prior run rather than silently dropping the
    unzipped wells.
    """
    zip_wells: set[str] = set()
    for p in input_dir.glob("[A-H][0-9]*.zip"):
        label = _canonical_well_from_path(p.stem)
        if label is not None:
            zip_wells.add(label)

    folder_wells: set[str] = set()
    # A raised OSError from iterdir() (e.g. the drive was yanked) propagates
    # naturally to the caller — no wrapping handler needed.
    for p in input_dir.iterdir():
        if not (p.is_dir() and re.fullmatch(r"[A-H]\d{1,2}", p.name)):
            continue
        label = _canonical_well_from_path(p.name)
        if label is None:
            continue
        # Empty folders don't count — they're often leftovers from
        # ``compress_folder_images_to_zip_and_remove`` that
        # interrupted between zip-write and folder-delete.
        try:
            if not any(p.iterdir()):
                continue
        except OSError:
            continue
        folder_wells.add(label)

    # MIXED iff there's at least one well with a folder that has no
    # matching zip AND at least one well that DOES have a zip.
    remaining = folder_wells - zip_wells
    if zip_wells and remaining:
        return LocalInputMode.MIXED
    if zip_wells:
        return LocalInputMode.ZIP
    if folder_wells:
        return LocalInputMode.FOLDER
    return LocalInputMode.FLAT


def _canonical_well_from_path(stem: str) -> str | None:
    """Normalize a path stem like ``A1`` or ``A01`` to canonical
    ``A01``. Returns None if the stem isn't a 96-well token."""
    from util.well_token import canonical_well_label
    return canonical_well_label(stem)


def _compute_run_id(cache_key: str, pipeline_argv: list[str]) -> str:
    """Deterministic run_id from input cache_key + pipeline-args
    hash. Same input + same pipeline config → same run_id, so
    re-running lands in the existing remote output dir and the
    pipeline's own ``output_exists_for_well`` logic skips wells
    whose ``{well}_out.zip`` already exists.

    v5.2.25: skips per-run-irrelevant flags so the cache slot stays
    stable across a forced-reprocess re-run, and so input/output
    dir paths don't churn the hash. ``--force`` users will hit the
    same dir and overwrite the cached outputs (which is what
    ``--force`` is supposed to mean)."""
    # Args that affect the bytes of the run output. Excludes the
    # input/output paths (those are derived from cache_key already)
    # and ``--force`` (a re-processing knob, not a config change).
    skip_with_value = {"--input_dir", "--output_dir"}
    skip_flags = {"--force"}
    filtered: list[str] = []
    i = 0
    while i < len(pipeline_argv):
        a = pipeline_argv[i]
        if a in skip_with_value:
            i += 2
            continue
        if a in skip_flags:
            i += 1
            continue
        filtered.append(a)
        i += 1
    digest = hashlib.blake2b("|".join(filtered).encode(), digest_size=8).hexdigest()
    return f"{cache_key[:16]}-{digest}"


def _required_tokens(
    nuclear_token: str | None, fluor_tokens: Iterable[str]
) -> set[str]:
    """Keep the nuclear channel + every requested fluor channel."""
    keep = {t.strip() for t in fluor_tokens if t and t.strip()}
    if nuclear_token:
        keep.add(nuclear_token.strip())
    return keep


def _zip_well_folder(folder: Path, out_zip: Path) -> int:
    """Atomically zip the TIFs in ``folder`` to ``out_zip``.

    Goes through :func:`util.zip_write.atomic_zip`, so it uses the same
    ``__aw_tmp_*.pidNNN`` staging name as
    ``process_microscopy.compress_folder_images_to_zip_and_remove`` and
    publishes only a verified archive. Returns the number of TIFs zipped
    (0 → folder had no images, caller should skip).

    Every TIF is read off the user's input volume, so the write carries the
    shared retry shield: on a network mount a dropped session mid-plate would
    otherwise fail the run outright with a bare errno.
    """
    tifs = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in (".tif", ".tiff")
    )
    if not tifs:
        return 0

    def _pack() -> None:
        with atomic_zip(out_zip) as zf:
            for t in tifs:
                zf.write(t, arcname=t.name)

    retry_io(_pack, what=out_zip.name, advice=_TRANSFER_ADVICE)
    return len(tifs)


def _stage_mixed_mode(
    input_dir: Path,
    *,
    fluor_tokens: Iterable[str] | None,
    nuclear_token: str | None,
    log_fn: Callable[[str], None] | None,
) -> ZippedInput:
    """Resume from a half-completed compression: zip the leftover
    folders, combine with the existing zips, and return the union.

    v5.2.21 introduced this entry point for the MIXED input mode.
    Detection in ``_detect_local_input_mode`` already established
    that at least one well has a zip AND at least one DIFFERENT well
    has a folder of TIFs — exactly the state a network-drop
    mid-``compress_input_well_folders`` leaves behind.

    v5.2.24: the newly-zipped wells now land in ``input_dir`` itself
    (next to the existing ``{well}.zip`` files) rather than in a
    throwaway temp dir. The user can SEE the new zips, and the next
    run picks them up as plain ZIP mode without re-zipping. Source
    folders are left in place so the user can verify what was kept
    before deleting them manually — the v5.2.21 same-well
    zip+folder detection ensures the leftover folders don't trigger
    a re-zip on the next run.

    The v5.2.18 invariant still holds for the channel-filter case:
    when ``fluor_tokens`` is restricted, ALL zips (existing + new)
    are copied into a temp staging dir and filtered there. The
    originals in ``input_dir`` — including the just-written ones —
    are never mutated by the filter.
    """
    existing_zip_paths = sorted(input_dir.glob("[A-H][0-9]*.zip"))
    existing_wells: dict[str, Path] = {}
    for p in existing_zip_paths:
        label = _canonical_well_from_path(p.stem)
        if label is not None:
            existing_wells[label] = p

    folder_wells: dict[str, Path] = {}
    for p in input_dir.iterdir():
        if not (p.is_dir() and re.fullmatch(r"[A-H]\d{1,2}", p.name)):
            continue
        label = _canonical_well_from_path(p.name)
        if label is None or label in existing_wells:
            continue
        try:
            if not any(p.iterdir()):
                continue
        except OSError:
            continue
        folder_wells[label] = p

    if not folder_wells:
        # Shouldn't reach here — _detect_local_input_mode wouldn't have
        # returned MIXED. Defensive: fall back to plain ZIP mode.
        return ZippedInput(
            zip_dir=input_dir,
            mode=LocalInputMode.ZIP,
            well_zips=tuple(existing_zip_paths),
            cleanup=None,
        )

    if log_fn:
        log_fn(
            f"[stage] resume detected: {len(existing_wells)} existing "
            f"zip(s), {len(folder_wells)} leftover folder(s) to zip "
            f"({sorted(folder_wells)[:5]}{'…' if len(folder_wells) > 5 else ''}).\n"
        )

    # v5.2.24: write new zips directly into input_dir. ``_zip_well_folder``
    # uses an atomic ``__aw_tmp_*.pidNNN`` rename so a mid-run failure
    # leaves either a complete zip or no zip — never a partial. Successful
    # zips persist across a failure: the next run sees fewer leftover
    # folders and resumes faster.
    new_zip_paths: list[Path] = []
    for label in sorted(folder_wells):
        folder = folder_wells[label]
        target = input_dir / f"{label}.zip"
        n = _zip_well_folder(folder, target)
        new_zip_paths.append(target)
        if log_fn:
            log_fn(
                f"[stage] zipped {folder.name} → {target.name} "
                f"({n} TIF{'s' if n != 1 else ''}) — persisted to "
                f"input dir.\n"
            )

    all_zips = tuple(sorted(existing_zip_paths + new_zip_paths))

    # Snapshot the leftover folders we just zipped; ``on_pipeline_success``
    # removes them so the next run is plain ZIP mode rather than MIXED.
    # Pre-v5.x.x policy was "leave folders for manual user verification";
    # the user explicitly opted into automatic deletion on clean
    # completion via the user-facing zipper-output question.
    consumed_folders = tuple(folder_wells[label] for label in sorted(folder_wells))

    def _on_success() -> None:
        for folder in consumed_folders:
            try:
                shutil.rmtree(folder)
            except OSError:
                pass

    if fluor_tokens is not None:
        # v5.2.18 invariant: the channel filter must NOT mutate the user's input
        # dir, so the filtered archives are built in a staging dir. Same
        # plan-then-materialise as plain ZIP mode: decide which members survive
        # from each archive's member list, then write only those. Copying every
        # archive in full and rewriting it afterwards — which this used to do —
        # pulls the whole plate off the input volume to discard most of it,
        # which is worst exactly where MIXED mode is most likely: a resumed run
        # against a slow network share.
        plans, _unused, zero_match = _plan_staging(
            all_zips,
            keep_tokens=_required_tokens(nuclear_token, fluor_tokens),
            log_fn=log_fn,
        )
        _raise_on_zero_channel_match(zero_match)
        staging = _open_staging_if_needed(plans, log_fn=log_fn)
        try:
            staged_zips = _materialize_staging(plans, staging, log_fn=log_fn)
            return ZippedInput(
                zip_dir=staging or input_dir,
                mode=LocalInputMode.MIXED,
                well_zips=staged_zips,
                cleanup=(None if staging is None else
                         (lambda d=staging: shutil.rmtree(d, ignore_errors=True))),
                on_pipeline_success=_on_success,
            )
        except Exception:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            raise

    # Non-filter path: zips live in input_dir, no cleanup needed.
    return ZippedInput(
        zip_dir=input_dir,
        mode=LocalInputMode.MIXED,
        well_zips=all_zips,
        cleanup=None,
        on_pipeline_success=_on_success,
    )


@dataclass(frozen=True)
class _StagePlan:
    """What of one source archive needs to reach the upload staging dir.

    ``keep`` is ``None`` when every member survives (the archive is taken
    wholesale) and a member-name tuple otherwise. Deciding this BEFORE any bytes
    move is the whole point: a zip's member list comes from its central
    directory — a small read at the end of the file — so planning the entire
    plate costs a handful of seeks even when the archives live on a NAS, and the
    materialise pass then reads only the members that are actually going to be
    uploaded.
    """

    src: Path
    keep: tuple[str, ...] | None
    n_members: int
    note: str = ""


def _raise_on_zero_channel_match(zero_match: list[str]) -> None:
    """Fail naming every well whose channel filter matched nothing.

    A filter that matches zero images is nearly always a mistyped channel name,
    and learning that one well at a time is useless — so the plan pass collects
    every offender and this raises once, before any bytes have moved.
    """
    if not zero_match:
        return
    sample = ", ".join(zero_match[:5])
    more = f" (and {len(zero_match) - 5} more)" if len(zero_match) > 5 else ""
    raise RuntimeError(
        f"Channel filter matched zero images in {len(zero_match)} well(s): "
        f"{sample}{more}. Likely a typo in the channel name(s) on the Analyze "
        f"tab — check your nuclear and fluor channel tokens against the "
        f"filenames inside the well zips."
    )


def _plan_staging(
    well_zips: tuple[Path, ...],
    *,
    keep_tokens: set[str] | None,
    incremental: bool = False,
    prior_output_dir: Path | None = None,
    csv_prefix: str = "measurements",
    filename_schema: str = "",
    filename_sep: str = "_",
    log_fn: Callable[[str], None] | None = None,
) -> tuple[list[_StagePlan], list[str], list[str]]:
    """Decide, per well, which members need staging — reading no member bytes.

    Returns ``(plans, up_to_date_wells, zero_channel_match_wells)``. A well whose
    every member is already processed contributes nothing to *plans*; a well
    whose channel filter matches nothing is named in the third list so the caller
    can fail with every offender at once (a filter matching zero images is nearly
    always a typo'd channel name, and finding out one well at a time is useless).
    """
    plans: list[_StagePlan] = []
    up_to_date: list[str] = []
    zero_match: list[str] = []
    n_total = len(well_zips)

    for idx, wz in enumerate(well_zips, start=1):
        try:
            with zipfile.ZipFile(wz, "r") as zf:
                members = zf.namelist()
        except (OSError, zipfile.BadZipFile) as exc:
            # Unreadable here means unreadable later too, but staging it whole
            # keeps the previous behaviour: the remote gets the archive and
            # reports the real error against the data, not against our planning.
            if log_fn:
                log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                       f"WARN unreadable ({exc.__class__.__name__}: {exc}); "
                       f"staging whole\n")
            plans.append(_StagePlan(src=wz, keep=None, n_members=0,
                                    note="unreadable"))
            continue
        if not members:
            if log_fn:
                log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                       f"WARN empty zip; skipping\n")
            continue

        keep = list(members)
        if keep_tokens:
            keep = [m for m in keep if _member_matches(m, keep_tokens)]
            if not keep:
                zero_match.append(wz.stem)
                if log_fn:
                    log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                           f"WARN zero channel matches; will fail after pass\n")
                continue

        well = _canonical_well_from_path(wz.stem)
        if incremental and prior_output_dir is not None and well is not None:
            # CSV is the primary done-set; fall back to the well's local output
            # zip so a missing / lost CSV doesn't re-upload already-done FOVs.
            done = read_done_fov_keys(prior_output_dir / f"{csv_prefix}_{well}.csv")
            done |= read_done_fov_keys_from_zip(
                prior_output_dir / f"{well}_out.zip",
                schema=filename_schema, sep=filename_sep)
            if done:
                keep = [
                    m for m in keep
                    if fov_tp_from_name(m, schema=filename_schema,
                                        sep=filename_sep) not in done
                ]
                if not keep:
                    up_to_date.append(well)
                    if log_fn:
                        log_fn(f"[remote] ✓ {well}: up to date (local) — "
                               f"not uploaded\n")
                    continue

        take_all = len(keep) == len(members)
        plans.append(_StagePlan(
            src=wz, keep=None if take_all else tuple(keep),
            n_members=len(members),
        ))
    return plans, up_to_date, zero_match


def _open_staging_if_needed(
    plans: list[_StagePlan], *, log_fn: Callable[[str], None] | None = None,
) -> Path | None:
    """A staging dir, but only if some archive actually has to be rewritten.

    Staging exists for exactly one reason: an archive the run has to rewrite —
    to drop unwanted channels, or the FOV-groups an incremental run has already
    processed — must not be written back over the user's input. An archive that
    survives its plan intact is not rewritten at all, so putting it in staging
    would move the same bytes twice: once onto the local scratch volume, then
    again to the remote. Those upload straight from where they already are.

    That is the common case, not an edge one. The Analyze tab always asks for
    selective channels, so a run whose channel list happens to cover every image
    in the plate takes the filtered path with a filter that drops nothing — and
    used to copy the whole plate into the OS temp dir to achieve it. Returning
    ``None`` here is what makes that run upload directly from the source.
    """
    rewrites = sum(1 for p in plans if p.keep is not None)
    if log_fn is not None and rewrites < len(plans):
        log_fn(f"[remote] {len(plans) - rewrites} archive(s) need no rewrite — "
               f"uploading those straight from the input, nothing copied "
               f"locally\n")
    if not rewrites:
        return None
    return _new_scratch_dir("allwell-stage-")


def _materialize_staging(
    plans: list[_StagePlan],
    staging: Path | None,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[Path, ...]:
    """Turn the plans into the list of archives to upload.

    A plan that keeps every member is not written anywhere: its source path goes
    into the returned list unchanged and rsync reads it from where it already
    lives. Copying it into scratch first would move the same bytes twice — once
    onto the local scratch volume, once to the remote — and the second copy is
    the only one anybody needs. *staging* is ``None`` when no plan needs a
    rewrite at all, which is the whole-plate case.

    A filtered plan is written member by member straight from the source into
    *staging*, so the members it drops are never read. That is what matters when
    the source is a network volume: an earlier shape copied every archive in
    full and only then rewrote it down to the members it wanted, so a run
    uploading a few hundred megabytes still dragged the whole plate across the
    network first.
    """
    staged: list[Path] = []
    n_total = len(plans)
    kept_members = 0
    total_members = 0
    passed_through = 0
    for idx, plan in enumerate(plans, start=1):
        total_members += plan.n_members
        if plan.keep is None:
            # Nothing to rewrite — upload it from where it is.
            kept_members += plan.n_members
            passed_through += 1
            staged.append(plan.src)
            continue
        if staging is None:                     # unreachable via the helper
            raise RuntimeError(
                f"{plan.src.name} needs rewriting but no staging dir was opened")
        dest = staging / plan.src.name
        t0 = time.monotonic()

        def _write(_plan=plan, _dest=dest) -> None:
            with atomic_zip(_dest) as dst:
                with zipfile.ZipFile(_plan.src, "r") as src:
                    for member in _plan.keep or ():
                        dst.writestr(src.getinfo(member), src.read(member))

        retry_io(
            _write,
            what=f"{plan.src.name} into the upload staging dir",
            cleanup=lambda d=dest: d.unlink(missing_ok=True),
            advice=_TRANSFER_ADVICE,
            log_fn=log_fn,
        )
        kept_members += len(plan.keep)
        if log_fn:
            mb = dest.stat().st_size / (1024 * 1024) if dest.exists() else 0.0
            log_fn(f"[remote] staged ({idx}/{n_total}): {plan.src.stem} → "
                   f"{len(plan.keep)}/{plan.n_members} member(s), "
                   f"{mb:.1f} MB in {_fmt_secs(time.monotonic() - t0)}\n")
        staged.append(dest)
    if log_fn and total_members and kept_members < total_members:
        log_fn(f"[remote] staging: summary — {kept_members}/{total_members} "
               f"member(s) across {n_total} well(s); the rest were never read "
               f"({passed_through} archive(s) uploaded from the input as-is).\n")
    return tuple(staged)


def _filter_channels_in_staging(
    zip_dir: Path,
    well_zips: tuple[Path, ...],
    mode: LocalInputMode,
    *,
    keep_tokens: set[str],
    log_fn: Callable[[str], None] | None,
) -> ZippedInput:
    """Rewrite each well zip to drop members whose name doesn't contain one
    of ``keep_tokens``.

    v5.2.18: renamed from ``_filter_channels_in_place``. The caller is
    expected to have already copied the user's zips into a staging
    temp dir — this function still rewrites in place RELATIVE to the
    files it's handed, so passing it the user's original input dir
    would corrupt the user's dataset (a pre-v5.2.18 bug).

    Phase-3 selective staging: this is the lever-1 multiplier — for a
    plate with 4 channels but only 2 in use, this cuts upload in half.
    Idempotent: a zip whose members are already filtered is a no-op.

    v5.2.20: emits a per-well log line so the user sees a steady stream
    of progress during a 96-well filter rather than wondering whether
    the GUI has hung. No-op wells (everything already matches) get an
    ``all N kept (no-op)`` line; rewritten wells report before/after
    size and elapsed time. The end-of-plate summary fires only when at
    least one well was actually rewritten.
    """
    if not keep_tokens:
        return ZippedInput(zip_dir=zip_dir, mode=mode, well_zips=well_zips, cleanup=None)

    n_total = len(well_zips)
    total_in = 0
    total_kept = 0
    rewrote = 0
    empty_wells: list[str] = []  # v5.2.18 typo-guard accumulator
    for idx, wz in enumerate(well_zips, start=1):
        try:
            with zipfile.ZipFile(wz, "r") as zf:
                members = zf.namelist()
            keep_mask = [_member_matches(m, keep_tokens) for m in members]
        except (OSError, zipfile.BadZipFile) as exc:
            if log_fn:
                log_fn(
                    f"[remote] filter ({idx}/{n_total}): {wz.stem} → "
                    f"WARN unreadable ({exc.__class__.__name__}: {exc}); skipping\n"
                )
            continue
        # Empty zip — nothing to filter, but distinct from "all kept".
        # all([]) is True in Python, which would otherwise silently
        # bucket this into the no-op branch with a misleading
        # "all 0 kept" line.
        if not members:
            if log_fn:
                log_fn(
                    f"[remote] filter ({idx}/{n_total}): {wz.stem} → "
                    f"WARN empty zip; skipping\n"
                )
            continue
        kept = sum(keep_mask)
        total_in += len(members)
        total_kept += kept
        # v5.2.18 typo guard: if the requested tokens match ZERO
        # images in a well, that's almost always a typo'd channel
        # name. Collect the affected wells and raise after the loop
        # so we can name them all at once.
        if kept == 0:
            empty_wells.append(wz.stem)
            if log_fn:
                log_fn(
                    f"[remote] filter ({idx}/{n_total}): {wz.stem} → "
                    f"WARN zero matches; will fail after pass\n"
                )
            continue
        if kept == len(members):
            if log_fn:
                log_fn(
                    f"[remote] filter ({idx}/{n_total}): {wz.stem} → "
                    f"all {kept} kept (no-op)\n"
                )
            continue
        # Re-write the zip with only the kept members.
        t0 = time.monotonic()
        size_before = wz.stat().st_size
        # The read handle is closed inside the atomic_zip block: publication
        # renames over `wz`, which Windows refuses while a handle is open.
        with atomic_zip(wz) as dst:
            with zipfile.ZipFile(wz, "r") as src:
                for member, keep in zip(members, keep_mask):
                    if keep:
                        dst.writestr(src.getinfo(member), src.read(member))
        size_after = wz.stat().st_size
        rewrote += 1
        if log_fn:
            mb_in = size_before / (1024 * 1024)
            mb_out = size_after / (1024 * 1024)
            elapsed = time.monotonic() - t0
            log_fn(
                f"[remote] filter ({idx}/{n_total}): {wz.stem} → "
                f"kept {kept}/{len(members)} ({mb_in:.1f}→{mb_out:.1f} MB) "
                f"in {_fmt_secs(elapsed)}\n"
            )

    # v5.2.18: raise AFTER the loop so every typo'd well is named in
    # the error message — earlier we accumulated them in empty_wells.
    if empty_wells:
        sample = ", ".join(empty_wells[:5])
        more = f" (and {len(empty_wells) - 5} more)" if len(empty_wells) > 5 else ""
        raise RuntimeError(
            f"Channel filter '{', '.join(sorted(keep_tokens))}' matched zero "
            f"images in {len(empty_wells)} well(s): {sample}{more}. "
            f"Likely a typo in the channel name(s) on the Analyze tab — "
            f"check your nuclear and fluor channel tokens against the "
            f"filenames inside the well zips."
        )

    # Only emit the summary when at least one well was actually
    # rewritten — pre-v5.2.20 the summary fired for any non-empty
    # plate, including the "all channels matched everywhere" case
    # where the line was pure noise.
    if log_fn and rewrote:
        dropped = total_in - total_kept
        log_fn(
            f"[remote] filter: summary — kept {total_kept}/{total_in} "
            f"images across {n_total} well(s), rewrote {rewrote}, "
            f"dropped {dropped}.\n"
        )
    return ZippedInput(zip_dir=zip_dir, mode=mode, well_zips=well_zips, cleanup=None)


def _member_matches(member: str, keep_tokens: set[str]) -> bool:
    """A zip member is kept if any keep_token is a substring of its
    basename (matches the same loose-token discipline process_microscopy
    uses when grouping channels)."""
    base = Path(member).name
    return any(tok in base for tok in keep_tokens)


def _filter_new_fovs_in_staging(
    well_zips: tuple[Path, ...],
    *,
    prior_output_dir: Path,
    csv_prefix: str,
    filename_schema: str,
    filename_sep: str,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Client-side incremental split: rewrite each staged well zip to keep only
    members whose ``(fov, timepoint)`` is NOT already in the well's local CSV,
    and drop wells whose every input FOV is already done.

    The remote then processes only genuinely-new FOVs (no remote-side diff), and
    the partial outputs are merged back locally on fetch. Operates on the staged
    copies only — never the user's originals. Returns
    ``(surviving_well_zips, up_to_date_wells)``.
    """
    survivors: list[Path] = []
    up_to_date: list[str] = []
    n_total = len(well_zips)

    for idx, wz in enumerate(well_zips, start=1):
        well = _canonical_well_from_path(wz.stem)
        if well is None:
            survivors.append(wz)
            continue
        # CSV is the primary done-set; fall back to the well's local output zip
        # so a missing/lost CSV doesn't make us re-upload already-done FOVs for a
        # full remote re-segmentation ("reanalyzed previously processed images").
        # The (fov, tp) key readers are the shared stdlib-only helpers in
        # pipeline.incremental_keys (audit A2) — no imaging-stack import.
        done = read_done_fov_keys(prior_output_dir / f"{csv_prefix}_{well}.csv")
        done |= read_done_fov_keys_from_zip(
            prior_output_dir / f"{well}_out.zip",
            schema=filename_schema, sep=filename_sep)
        if not done:
            survivors.append(wz)  # genuinely new well → upload in full
            continue
        try:
            with zipfile.ZipFile(wz, "r") as zf:
                members = zf.namelist()
        except (OSError, zipfile.BadZipFile) as exc:
            # A corrupt input zip is uploaded whole rather than incrementally —
            # name it so the user can tell why a "done" well re-uploads in full
            # (audit E11).
            if log_fn:
                log_fn(f"[remote] WARN {well}: input zip unreadable ({exc}); "
                       f"uploading in full — {wz.name}\n")
            survivors.append(wz)
            continue
        keep = [
            m for m in members
            if fov_tp_from_name(m, schema=filename_schema, sep=filename_sep) not in done
        ]
        if not keep:
            # Every input FOV already processed locally — skip the upload.
            try:
                wz.unlink()
            except OSError:
                pass
            up_to_date.append(well)
            if log_fn:
                log_fn(f"[remote] ✓ {well}: up to date (local) — not uploaded\n")
            continue
        if len(keep) == len(members):
            survivors.append(wz)  # nothing of this well's input is done yet
            continue
        # Read handle closed before publication — see the filter pass above.
        with atomic_zip(wz) as dst:
            with zipfile.ZipFile(wz, "r") as src:
                for m in keep:
                    dst.writestr(src.getinfo(m), src.read(m))
        survivors.append(wz)
        if log_fn:
            log_fn(
                f"[remote] split ({idx}/{n_total}): {well} → uploading "
                f"{len(keep)}/{len(members)} new-FOV file(s)\n"
            )
    return tuple(survivors), tuple(up_to_date)


def _scan_output_fov_tps(
    output_dir: Path, *, schema: str, sep: str,
) -> tuple[set[str], set[str]]:
    """``(timepoints, fovs)`` token sets recovered from the merged outputs in
    *output_dir* — the ``<well>_out.zip`` members (and any loose images / output
    well folders). Output member names keep the input stem, so the schema-indexed
    timepoint/FOV tokens come straight off them. Stdlib-only."""
    fields = [f.strip().lower() for f in str(schema or "").split(":")]
    ti = fields.index("timepoint") if "timepoint" in fields else -1
    fi = fields.index("fov") if "fov" in fields else -1
    tps: set[str] = set()
    fovs: set[str] = set()

    def _add(name: str) -> None:
        stem = Path(name).stem
        if not stem or Path(name).suffix.lower() not in (".tif", ".tiff", ".png"):
            return
        parts = stem.split(sep)
        if 0 <= ti < len(parts) and parts[ti].strip():
            tps.add(parts[ti].strip())
        if 0 <= fi < len(parts) and parts[fi].strip():
            fovs.add(parts[fi].strip())

    try:
        entries = list(output_dir.iterdir())
    except OSError:
        return tps, fovs
    for p in entries:
        if p.is_file() and p.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(p, "r") as zf:
                    for member in zf.namelist():
                        _add(Path(member).name)
            except (OSError, zipfile.BadZipFile):
                continue
        elif p.is_file() and p.suffix.lower() in (".tif", ".tiff", ".png"):
            _add(p.name)
        elif p.is_dir():
            try:
                for child in p.iterdir():
                    if child.is_file():
                        _add(child.name)
            except OSError:
                continue
    return tps, fovs


def _tp_sort_key(tok: str):
    r"""Chronological sort key for a timepoint token; unparseable tokens last.

    Delegates to :func:`util.timepoint.sort_key`. This used to inline its own
    parser — ``services/`` may not import ``well_viewer`` (ARCHITECTURE §6.2),
    so it could not share the viewer's — and the two drifted: a trailing ``\w*``
    in the unit pattern made it read ``"2hpCh"`` as two hours. ``util`` is
    stdlib-only, so it is importable from here and the divergence goes away.
    """
    return tp_sort_key(tok)


def refresh_pipeline_info_available_from_outputs(
    local_output_dir: Path, *, log_fn: Callable[[str], None] | None = None,
) -> bool:
    """Recompute ``available_timepoints`` / ``available_fovs`` in the local
    ``pipeline_info.json`` from the merged outputs in *local_output_dir*.

    After an incremental remote run the fetched sidecar only knows the
    timepoints/FOVs the remote processed (the uploaded new-FOV split), so the
    Review dropdowns built from it miss the rest of the dataset. The merged
    ``<well>_out.zip`` members carry the full input stem, so scanning the output
    dir recovers every ``(fov, timepoint)``. Union with whatever the sidecar
    already lists (never drop a known value) and write back, preserving every
    other key. Stdlib-only, best-effort: returns True iff the file was rewritten.
    """
    log = log_fn or (lambda _m: None)
    p = Path(local_output_dir) / "pipeline_info.json"
    if not p.exists():
        return False
    try:
        info = json.loads(p.read_text())
        if not isinstance(info, dict):
            return False
        schema = str(info.get("schema") or "")
        sep = str(info.get("separator") or "_")
        if not schema:
            return False
        tps, fovs = _scan_output_fov_tps(Path(local_output_dir), schema=schema, sep=sep)
        tp_union = sorted(set(tps) | set(info.get("available_timepoints") or []),
                          key=_tp_sort_key)
        fov_union = sorted(set(fovs) | set(info.get("available_fovs") or []),
                           key=fov_sort_key)
        if tp_union == list(info.get("available_timepoints") or []) \
                and fov_union == list(info.get("available_fovs") or []):
            return False  # already complete
        info["available_timepoints"] = tp_union
        info["available_fovs"] = fov_union
        tmp = p.with_suffix(p.suffix + ".tmp")
        payload = json.dumps(info, indent=2)

        def _cleanup_tmp() -> None:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

        def _publish() -> None:
            tmp.write_text(payload)
            os.replace(str(tmp), str(p))

        # Lands on the output volume like every other result, so it gets the
        # same shield — losing the sidecar to one transient fault would leave
        # Review's timepoint/FOV dropdowns short of the data that is on disk.
        retry_io(
            _publish, what=p.name, cleanup=_cleanup_tmp,
            advice=_TRANSFER_ADVICE, log_fn=log,
        )
        log(
            f"[remote] refreshed pipeline_info from merged outputs: "
            f"{len(tp_union)} timepoint(s), {len(fov_union)} FOV(s)\n"
        )
        return True
    except (OSError, json.JSONDecodeError) as exc:
        log(f"[remote] warn: pipeline_info refresh failed: {exc}\n")
        return False


# ────────────────────────────────────────────────────────────────────────────
# DatasetCache
# ────────────────────────────────────────────────────────────────────────────


def _remote_quote(s: str) -> str:
    """Shell-quote ``s`` for embedding in a remote bash command line,
    preserving leading-``~/`` expansion.

    Stock ``shlex.quote`` wraps everything in single quotes, which the
    remote bash treats as literal — so ``shlex.quote("~/foo")`` →
    ``'~/foo'``, and the remote ``mkdir -p '~/foo'`` creates a folder
    literally named ``~`` containing ``foo``. (This bit a user in
    v5.2.10 with a `[remote.j]` config whose ``remote_staging_dir`` was
    the default ``~/.allwell-staging``.)

    Fix: a leading ``~/`` is rewritten to ``"$HOME"`` followed by the
    single-quoted remainder. The remote bash expands ``$HOME`` inside
    the double-quoted half and keeps the rest literal:

        _remote_quote("~/foo bar")  →  '"$HOME"'\\''/foo bar'\\'
                                       ^^^^^^^^   ^^^^^^^^^^^^^
                                       expanded   literal (quoted)

    A bare ``~`` becomes ``"$HOME"`` directly. Everything else falls
    through to ``shlex.quote`` so spaces / metacharacters in regular
    paths stay safe.
    """
    if s == "~":
        return '"$HOME"'
    if s.startswith("~/"):
        return '"$HOME"' + shlex.quote(s[1:])
    return shlex.quote(s)


def _remote_join(remote_argv: list[str]) -> str:
    """Like ``shlex.join`` but tilde-aware. Use everywhere we build a
    remote command line for execution by the remote bash."""
    return " ".join(_remote_quote(a) for a in remote_argv)


# v5.2.13: bash snippet that locates conda.sh wherever conda is
# installed (``$HOME/miniforge3`` for bootstrap-managed installs OR
# whatever ``conda info --base`` reports for pre-existing system
# condas) and sources it. Mirrors the discovery logic in
# ``scripts/_bootstrap_lib.py`` so the runner doesn't care which path
# bootstrap actually used.
#
# Why ``conda run --no-capture-output -n <env>`` rather than the old
# hard-coded ``host.remote_python``: on hosts where the bootstrap
# found a pre-existing system conda (HPC module, ``/opt/conda``,
# etc.) the env lives somewhere like ``/opt/conda/envs/allwell``, not
# under ``~/miniforge3``. The hard-coded path then exits 127 — the
# v5.2.12 user hit exactly this. ``conda run`` resolves the python
# from inside the env so it works regardless of conda's install
# location.
_CONDA_ACTIVATE_SNIPPET = (
    'CONDA_SH=""; '
    'if [ -f "$HOME/miniforge3/etc/profile.d/conda.sh" ]; then '
    'CONDA_SH="$HOME/miniforge3/etc/profile.d/conda.sh"; '
    'elif command -v conda >/dev/null 2>&1; then '
    '_base=$(conda info --base 2>/dev/null); '
    '[ -n "$_base" ] && [ -f "$_base/etc/profile.d/conda.sh" ] '
    '&& CONDA_SH="$_base/etc/profile.d/conda.sh"; '
    'fi; '
    'if [ -z "$CONDA_SH" ]; then '
    'echo "[remote] ERROR: cannot find conda.sh on remote" >&2; '
    'exit 127; '
    'fi; '
    '. "$CONDA_SH"'
)


def _conda_run_prefix(env_name: str = DEFAULT_CONDA_ENV) -> list[str]:
    """Argv prefix that runs the following command inside conda env
    ``env_name``. ``--no-capture-output`` makes stdout/stderr stream
    through unchanged so the existing progress parser keeps working."""
    return ["conda", "run", "--no-capture-output", "-n", env_name]


# How the pipeline's conda environment is named on the remote: either by env
# *name* (``conda run -n allwell``, resolved through conda's own envs dirs) or
# by absolute *prefix* (``conda run -p /path/to/env``).
EnvSelector = tuple[str, str]

DEFAULT_ENV_SELECTOR: EnvSelector = ("name", DEFAULT_CONDA_ENV)


def _conda_env_selector(remote_python: str) -> tuple[EnvSelector, str]:
    """Resolve a host's ``remote_python`` to a ``conda run`` selector.

    Returns the selector plus a warning to show the user (empty when there is
    nothing to say).

    The Remotes dialog offers a **Remote python** field, and the run path used
    to ignore it entirely — every run was hard-coded to ``conda run -n allwell``
    no matter what the host was configured with. A user who pointed that field
    at a different environment (which is the only reason to touch it) got their
    setting silently dropped and the run died on the remote with conda's own
    ``EnvironmentLocationNotFound: not a conda environment``, naming a path they
    had never configured. The field is honoured here instead.

    Left at its default the field is only a *placeholder*, not a fact — the
    bootstrap creates the env inside whichever conda it found, which on a host
    with a pre-existing system conda is not under ``~/miniforge3``. So the
    default keeps the name selector and lets conda resolve it, which is what
    makes a system-conda install work at all.

    A value that is not shaped like ``<env>/bin/python`` cannot name an
    environment; rather than guess, the name selector is kept and the caller is
    told the setting was not usable.
    """
    value = (remote_python or "").strip()
    if not value or value == DEFAULT_REMOTE_PYTHON:
        return DEFAULT_ENV_SELECTOR, ""
    path = PurePosixPath(value)
    if path.parent.name == "bin" and path.name.startswith("python"):
        return ("prefix", str(path.parent.parent)), ""
    return DEFAULT_ENV_SELECTOR, (
        f"configured Remote python {value!r} is not shaped like "
        f"'<env>/bin/python', so it cannot name a conda environment; "
        f"falling back to the env named {DEFAULT_CONDA_ENV!r}"
    )


def _conda_run_prefix_for(selector: EnvSelector) -> list[str]:
    """``conda run`` argv prefix for *selector* (``-n name`` or ``-p prefix``)."""
    kind, value = selector
    return ["conda", "run", "--no-capture-output",
            "-p" if kind == "prefix" else "-n", value]


# Shell helpers for locating and validating a conda env on the remote. Shared
# with ``scripts/_bootstrap_lib.py`` so the runner's pre-launch check and the
# bootstrap's create-or-skip decision cannot disagree about whether an
# environment exists — the state where they *did* disagree is the one that
# stranded users (see :func:`_conda_env_guard`).
#
# Validity is conda's own test, ``is_conda_environment()``: a prefix is an
# environment iff it holds ``conda-meta/history``. A directory without that file
# is not an environment no matter what it contains, and conda refuses both to
# run in it and to create over it.
CONDA_ENV_SHELL_HELPERS = r"""
aw_env_prefix() {
    conda env list 2>/dev/null | awk -v n="$1" '$1 == n { print $NF }' | head -1
}
aw_env_is_valid() {
    [ -n "$1" ] && [ -f "$1/conda-meta/history" ]
}
aw_env_stale_dir() {
    for _c in "$HOME/.conda/envs/$1" "$(conda info --base 2>/dev/null)/envs/$1"; do
        if [ -d "$_c" ] && [ ! -f "$_c/conda-meta/history" ]; then
            printf '%s\n' "$_c"
            return 0
        fi
    done
    return 1
}
"""


def _conda_env_guard(selector: EnvSelector) -> str:
    """Bash that aborts with an actionable message if *selector* has no usable env.

    Without this the run reaches ``conda run`` and dies on conda's own
    ``EnvironmentLocationNotFound: not a conda environment: <path>`` — a line
    with no ``[remote]`` prefix, no host name, and no hint that the fix is to
    bootstrap the host. It is also indistinguishable, in a scrolling log, from
    an error the *pipeline* raised, which sends the diagnosis to the wrong
    machine entirely.

    Two states have to be told apart, because the fix differs and conda's own
    message conflates them:

    *No environment.* The host was never bootstrapped. Bootstrap it.

    *A directory that is not an environment.* Something exists at the prefix but
    holds no ``conda-meta/history`` — an env creation that failed part-way,
    which on a cluster is most often a full home-directory quota. This is the
    worse state: conda will neither run in it nor ``conda create`` over it, so
    the run fails *and* a plain re-bootstrap fails behind it. The message names
    the directory, because that is the thing that has to go.

    The check runs on the remote, immediately before the pipeline launches, so
    on a scheduler-backed host it runs **inside the allocation** — the compute
    node is where the env has to exist, and it is not always the login node's
    filesystem.
    """
    kind, value = selector
    value_q = _remote_quote(value)
    if kind == "prefix":
        # The prefix is given, so there is nothing to look up: it either is an
        # environment, is a directory that is not one, or is absent.
        resolve = (
            f'AW_PREFIX={value_q}; '
            f'AW_STALE=""; '
            f'if [ -d "$AW_PREFIX" ] && ! aw_env_is_valid "$AW_PREFIX"; then '
            f'AW_STALE="$AW_PREFIX"; fi'
        )
        what = f'conda env prefix {value}'
        # No double quotes anywhere in these strings: they are interpolated
        # into a double-quoted bash ``echo``, where a stray " ends the string
        # and the message arrives mangled (or the shell fails to parse it).
        missing_fix = ('Set this hosts Remote python to the env python, or run '
                       'Analyze -> Remotes -> Bootstrap to create the env.')
    else:
        resolve = (
            f'AW_PREFIX="$(aw_env_prefix {value_q})"; '
            f'AW_STALE="$(aw_env_stale_dir {value_q} || true)"'
        )
        what = f"conda env {value}"
        missing_fix = ('Run Analyze -> Remotes -> Bootstrap for this host to '
                       'create it, or set Remote python to an existing env '
                       'python.')
    # The guard never deletes: a run is not the time to modify the host, and
    # what is inside that directory is unreadable to us. Print the one command
    # that unblocks it and let the user decide.
    stale_fix = ('ACTION REQUIRED: remove that directory yourself, then re-run '
                 'Analyze -> Remotes -> Bootstrap to rebuild the env:')
    return (
        f'{CONDA_ENV_SHELL_HELPERS} '
        f'{resolve}; '
        f'if ! aw_env_is_valid "$AW_PREFIX"; then '
        f'echo "[remote] ERROR: {what} is not usable on $(hostname)." >&2; '
        f'if [ -n "$AW_STALE" ]; then '
        f'echo "[remote] $AW_STALE exists but is not a conda environment '
        f'(no conda-meta/history)." >&2; '
        f'echo "[remote] An env creation failed part-way — on a cluster a full '
        f'home-directory quota is the usual cause. conda will neither run in '
        f'it nor create over it." >&2; '
        f'echo "[remote] {stale_fix}" >&2; '
        f'echo "[remote]     rm -rf $AW_STALE" >&2; '
        f'echo "[remote] Check the quota first (quota -s, or du -sh \\$HOME) — '
        f'if that is what stopped the install, it will stop the next one too." '
        f'>&2; '
        f'else '
        f'echo "[remote] {missing_fix}" >&2; '
        f'fi; '
        f'echo "[remote] conda base: $(conda info --base 2>/dev/null || '
        f'echo unknown)" >&2; '
        f'echo "[remote] environments conda can see:" >&2; '
        # Prefix conda's own listing too. Every line of this block has to be
        # attributable to the remote at a glance — an unprefixed listing in the
        # middle of a labelled block reads like local output, which is the
        # confusion the whole guard exists to remove.
        f"conda env list 2>/dev/null | sed 's/^/[remote]   /' >&2 || true; "
        f'exit 127; '
        f'fi'
    )


def _compose_remote_cmd(
    launcher: str,
    remote_pidfile: str,
    remote_argv: list[str],
    *,
    cache_hit: bool,
    env_selector: EnvSelector = DEFAULT_ENV_SELECTOR,
) -> str:
    """Build the bash command string ssh runs on the remote login node.

    Pure (no IO) so it can be unit-tested without a live ssh. Two shapes:

    **Direct execution** (``launcher`` empty, or ``cache_hit`` — never
    allocate a compute node just to run the ``true`` no-op): the login
    shell records its own PID, activates conda, and execs the pipeline.
    This is byte-identical to the pre-``compute_launcher`` behaviour::

        echo $$ > <pidfile>; <conda activate>; exec <pipeline argv>

    **Scheduled execution** (``launcher`` set, e.g. an ``srun …`` line,
    and not a cache hit): the login shell records its PID then *execs the
    launcher*, which places a fresh allocation on a compute node the
    cluster schedules. Conda activation and the pipeline run **inside**
    that allocation (``bash -lc``) — that's where the env, the shared-FS
    conda install, and any requested GPU live. The job's own
    ``$SLURM_JOB_ID`` is written to ``<pidfile>.slurm`` (on the shared FS,
    so the login-side ``cancel()`` can ``scancel`` it as a second kill
    channel)::

        echo $$ > <pidfile>; exec srun … bash -lc '<conda>; echo $JOBID > <pidfile>.slurm; exec <pipeline argv>'

    The inner script is single-quoted for the *outer* login shell (via
    ``shlex.quote``) so the outer shell passes it through literally and
    the inner ``bash`` is the one that expands ``$HOME`` / ``$SLURM_JOB_ID``
    on the compute node.

    Both shapes check that *env_selector* actually names an environment before
    reaching ``conda run`` (:func:`_conda_env_guard`), so a host that was never
    bootstrapped — or one whose **Remote python** points somewhere that no
    longer exists — fails with a labelled, actionable message instead of
    conda's bare ``EnvironmentLocationNotFound``. A cache hit runs ``true`` and
    needs no environment at all, so it skips the check rather than failing a
    run that was never going to import anything.
    """
    pidfile_q = _remote_quote(remote_pidfile)
    launcher = launcher.strip()
    guard = "" if cache_hit else f"{_conda_env_guard(env_selector)}; "
    if cache_hit or not launcher:
        # PID echoed first so the cancel path can target the pipeline
        # directly; conda activated on the login shell; then exec.
        return (
            f"echo $$ > {pidfile_q}; "
            f"{_CONDA_ACTIVATE_SNIPPET}; "
            f"{guard}"
            f"exec {_remote_join(remote_argv)}"
        )

    slurm_sidecar_q = _remote_quote(f"{remote_pidfile}.slurm")
    # The ``[remote] scheduler: …`` echo runs *inside* the allocation, so it
    # only prints once the scheduler has granted a node — it streams back over
    # the same ssh stdout and is the user's "queue wait over, now running on
    # <node>" signal, naming the job id and partition for diagnosis.
    inner = (
        f"{_CONDA_ACTIVATE_SNIPPET}; "
        f"{guard}"
        f'echo "${{SLURM_JOB_ID:-}}" > {slurm_sidecar_q} 2>/dev/null || true; '
        f'echo "[remote] scheduler: allocation granted — job ${{SLURM_JOB_ID:-?}} '
        f'on ${{SLURMD_NODENAME:-$(hostname)}} '
        f'(partition ${{SLURM_JOB_PARTITION:-?}})"; '
        f"exec {_remote_join(remote_argv)}"
    )
    # shlex.split→join normalises whitespace AND re-quotes any token with
    # shell metacharacters (e.g. ``--constraint=a|b``) so the outer remote
    # login shell passes them to the scheduler literally instead of, say,
    # treating ``|`` as a pipe.
    launcher_str = shlex.join(shlex.split(launcher))
    return (
        f"echo $$ > {pidfile_q}; "
        f"exec {launcher_str} bash -lc {shlex.quote(inner)}"
    )


@lru_cache(maxsize=1)
def _rsync_version() -> tuple[int, int]:
    """Parsed ``(major, minor)`` of the local rsync, or ``(0, 0)`` if
    rsync is missing / unparseable. Cached for the process lifetime.
    Shared by ``_rsync_supports_info_flag`` (≥ 3.1) and
    ``_rsync_supports_append_verify`` (≥ 3.0) so we don't fork+exec
    ``rsync --version`` twice on startup."""
    try:
        result = subprocess.run(
            [resolve_rsync_binary(), "--version"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return (0, 0)
    if result.returncode != 0:
        return (0, 0)
    first_line = (result.stdout or "").splitlines()[0] if result.stdout else ""
    match = re.search(r"version\s+(\d+)\.(\d+)", first_line)
    if not match:
        return (0, 0)
    return int(match.group(1)), int(match.group(2))


def _rsync_supports_info_flag() -> bool:
    """Whether the local rsync accepts ``--info=...``.

    Added in rsync 3.1.0 (2013). macOS still ships the very old
    rsync 2.6.9 by default (Apple licensing — they froze before
    rsync went GPLv3); HPC clusters and minimal containers can
    have similar vintage. Those rsyncs reject ``--info=...`` with
    "rsync: --info=stats0: unknown option" → exit 1.

    Detected once at first call via ``rsync --version`` (which both
    old and new rsyncs support) and cached for the process lifetime.
    Returns False on any error so the caller defaults to the
    safer "omit --info" path.
    """
    return _rsync_version() >= (3, 1)


def _rsync_supports_append_verify() -> bool:
    """Whether the local rsync accepts ``--append-verify``.

    Added in rsync 3.0.0 (2008). macOS's bundled rsync 2.6.9 rejects
    it with "Unrecognized option --append-verify" → exit 1, which was
    the failure mode that motivated this probe (v5.2.14 user hit it
    on macOS). On legacy rsync we drop the flag entirely; ``--partial``
    alone is still enough to resume an interrupted transfer (just
    without the tail-checksum verification step)."""
    return _rsync_version() >= (3, 0)


def _rsync_ssh_transport(host: RemoteHost) -> list[str]:
    """Build the ``-e "ssh …"`` argument every rsync in this module
    should pass.

    rsync invokes ssh as a separate child process — it does NOT use
    the runner's ``host.ssh_argv()`` machinery. Without an explicit
    ``-e`` flag, rsync's ssh would:

    * **Skip the wizard-generated key.** v5.2.2 threaded ``ssh_key_path``
      through every direct ssh call, but the rsync calls in this module
      pass bare argv with no ``-i``, so rsync's underlying ssh falls
      through to ssh's default identity search and never tries
      ``~/.ssh/allwell_<alias>``. A keys-only remote then rejects the
      connection with "Permission denied".

    * **Refuse first-contact hosts.** No ``StrictHostKeyChecking=
      accept-new``, so a host that isn't in ``~/.ssh/known_hosts`` yet
      kills rsync with exit 255 "Host key verification failed", same
      class of failure the bootstrap's ``_ssh_run`` had before v5.2.3.

    v5.x.x: the ssh binary is pulled via :func:`resolve_ssh_for_rsync`
    (not :func:`resolve_ssh_binary`) so that on Windows we pick an
    ssh.exe from the SAME MSYS / Cygwin runtime as the resolved
    rsync.exe. Mismatched runtimes (MSYS2 rsync + Git for Windows
    ssh, the two ship different msys-2.0.dll versions) make
    Cygwin's fork-emulation fail with ``dup() in/out/err failed``
    before ssh runs, surfacing as rsync exit 12 / "connection
    unexpectedly closed".

    This helper centralises the policy so every call site (there are
    seven as of v5.2.5: cache upload, file sync, per-well fetch, glob
    pull, optional pull, two end-of-run retries) gets the same options.
    """
    ssh_inner = [
        resolve_ssh_for_rsync(),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        "-o", "BatchMode=yes",
    ]
    if host.ssh_key_path:
        key = str(Path(host.ssh_key_path).expanduser())
        ssh_inner += ["-i", key]
    # Join with shlex.quote so a path with spaces (rare on Linux, common
    # on macOS / Windows user dirs) survives the round-trip through the
    # shell rsync uses internally.
    return ["-e", " ".join(shlex.quote(a) for a in ssh_inner)]


class DatasetCache:
    """Manage the persistent per-well-zip cache on the remote.

    The cache key is a stat-based hash (size + mtime per well zip), which
    is what rsync itself uses as its quick-skip heuristic — re-running on
    the same dataset is a metadata roundtrip, not a re-upload.

    A change to any well's images bumps that well's zip mtime, which
    bumps the cache key — so the next run re-uploads. That's the correct
    cache-invalidation granularity: per-well, not per-image.
    """

    def __init__(self, host: RemoteHost):
        self.host = host

    @staticmethod
    def cache_key(zipped: ZippedInput) -> str:
        """Stable hash of the (basename, size, mtime) triple for each
        well zip in ``zipped.zip_dir``. blake2b at 16 bytes is more than
        enough collision resistance for a per-user cache.
        """
        h = hashlib.blake2b(digest_size=16)
        for wz in zipped.well_zips:
            stat = wz.stat()
            h.update(wz.name.encode("utf-8"))
            h.update(b"\0")
            h.update(str(stat.st_size).encode("ascii"))
            h.update(b"\0")
            h.update(str(int(stat.st_mtime)).encode("ascii"))
            h.update(b"\0")
        return h.hexdigest()

    def remote_dataset_dir(self, key: str) -> str:
        """Return the remote-side absolute path for a cache entry.

        Returns a shell-quoted path safe to embed in remote commands.
        """
        return f"{self.host.remote_staging_dir}/datasets/{key}"

    def ensure_uploaded(
        self,
        zipped: ZippedInput,
        *,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> tuple[str, int, float]:
        """Upload the per-well zips to ``datasets/<key>/`` on the remote.

        Returns ``(key, bytes_transferred, elapsed_s)``. On a cache hit,
        ``bytes_transferred`` will be ~0 — rsync only sends the file list.

        Cancellation: if ``stop_event`` fires mid-upload, the in-flight
        rsync is terminated and a partial state is left on the remote.
        That's fine — rsync's ``--partial`` flag means the next run
        resumes rather than restarting.
        """
        key = self.cache_key(zipped)
        remote_path = self.remote_dataset_dir(key)

        # Ensure remote target dir exists.
        self._ssh_run(["mkdir", "-p", remote_path], check=True)

        sources = [to_rsync_path(p) for p in zipped.well_zips]
        # The trailing slash on the remote dest is critical for rsync's
        # tree-merge semantics — without it rsync would create
        # `<key>/<key>/` on the second run.
        dest = f"{self.host.ssh_alias}:{remote_path}/"
        cmd = [
            resolve_rsync_binary(),
            *_rsync_ssh_transport(self.host),
            "-aH",
            "--partial",
        ]
        # v5.2.15: --append-verify needs rsync ≥ 3.0.0. macOS still
        # ships rsync 2.6.9 by default and rejects it with
        # "Unrecognized option --append-verify" → exit 1. Without the
        # flag, --partial alone still gives resume semantics — we just
        # lose the tail-checksum verification on the resumed bytes.
        if _rsync_supports_append_verify():
            cmd.append("--append-verify")
        # v5.2.10: --info= needs rsync ≥ 3.1.0. Old rsync (macOS's
        # bundled 2.6.9, some HPC clusters) rejects it with exit 1.
        # The progress callback degrades to "stats appear at end" on
        # legacy rsync, but the transfer itself still works.
        # v5.2.20: add ``name1`` so rsync emits a bare filename per
        # transferred file. ``_run_rsync_streaming`` forwards those
        # lines as ``[remote] ↑ <filename>`` so the user can see
        # which well is in flight.
        if _rsync_supports_info_flag():
            cmd.append("--info=name1,stats2,progress2")
        if log_fn:
            # Log the full flag list so the user can see every gating
            # flag (--append-verify, --info=name1,...) in the log;
            # pre-cleanup we sliced ``cmd[:6]`` which truncated before
            # the --info flag on modern rsync.
            log_fn(
                f"[remote] $ {' '.join(shlex.quote(c) for c in cmd)} … → {dest} "
                f"({len(sources)} file(s))\n"
            )
        cmd += [*sources, dest]

        # Bounded retry on transient network failures (v5.1.1). The
        # ``--partial`` flag means each retry resumes where the previous
        # attempt left off, so a flaky link doesn't restart the whole
        # upload from zero. Non-transient errors (permission, bad path)
        # break out of the loop on the first attempt. Shared with
        # ``upload_subset`` via ``_stream_rsync_with_retry`` (audit A11).
        sent_bytes, elapsed, rc, attempts_run = _stream_rsync_with_retry(
            cmd, log_fn=log_fn, progress_fn=progress_fn, stop_event=stop_event,
        )

        # v5.2.14: pre-v5.2.14 ``ensure_uploaded`` returned even when
        # rsync exited non-zero (e.g. rc=23 "partial transfer due to
        # error"). ``spawn()`` then started the pipeline against an
        # incomplete remote input dir — the user saw "pipeline failed"
        # without ever seeing the upstream upload failure.
        #
        # Fail loud here so the upload error is the user's first signal,
        # not a downstream "no wells found" or KeyError deep in the
        # pipeline. The retry loop above already handles transient
        # codes; reaching here with rc != 0 means a real error.
        if rc != 0:
            raise RuntimeError(
                f"rsync upload of well zips to {self.host.ssh_alias} "
                f"failed with exit code {rc} (after {attempts_run} "
                f"attempt{'s' if attempts_run != 1 else ''}). The "
                f"remote dataset at {remote_path} is incomplete. The "
                f"specific rsync error appears earlier in the log "
                f"(look for a line starting with 'rsync:' or 'rsync "
                f"error:')."
            )

        # v5.2.14: belt-and-braces verification. Some rsync failure
        # modes (notably source-side read errors on a subset of files)
        # can result in rc=0 with a partial transfer — rsync logs the
        # per-file error to stderr but still reports overall success.
        # Count zips on the remote and match against the local well
        # count before letting ``spawn`` proceed.
        #
        # v5.2.16: pipe the count script via ``_ssh_script`` (stdin to
        # remote bash) rather than ``_ssh_run`` (which would
        # ``shlex.quote`` the ``~/.allwell-staging/...`` path into
        # single quotes — killing tilde expansion on the remote and
        # always returning 0). The previous v5.2.14 implementation
        # falsely reported "expected 2 found 0" for runs whose files
        # had actually transferred fine.
        expected = len(zipped.well_zips)
        listing = self._ssh_script(
            f"ls -1 {remote_path}/*.zip 2>/dev/null | wc -l",
            check=False,
        )
        try:
            actual = int((listing.stdout or "0").strip())
        except (TypeError, ValueError):
            actual = -1
        if actual != expected:
            raise RuntimeError(
                f"Upload verification failed: expected {expected} well "
                f"zip(s) at {self.host.ssh_alias}:{remote_path}/, found "
                f"{actual}. rsync reported success but some files did "
                f"not reach the remote. Re-run to retry."
            )

        # Refresh the bandwidth estimate when we actually moved bytes —
        # cache hits would skew the average toward infinity.
        if sent_bytes > 1_000_000:  # >1 MB → meaningful sample
            mbps = (sent_bytes * 8) / elapsed / 1_000_000
            update_bandwidth(self.host.name, mbps)

        mb = sent_bytes / (1024**2)
        cache_hit = mb < 1
        # On a full cache hit rsync moves nothing and emits no per-file
        # "↑ <well>.zip" name lines, so the dashboard's Upload rail never
        # advanced (the user just saw it stuck at 0/N when the inputs were
        # already on the remote). Verification above confirmed every expected
        # zip is present — emit a per-well marker so the Upload step reads
        # complete. The dashboard de-dupes via sets, so this is harmless on a
        # fresh upload that also streamed live ↑ lines.
        if log_fn and cache_hit:
            for wz in zipped.well_zips:
                log_fn(f"[remote] ↑ {wz.stem}.zip already on remote\n")
        if log_fn:
            log_fn(
                f"[remote] upload: {mb:.1f} MB in {_fmt_secs(elapsed)}"
                f"{' (cache hit)' if cache_hit else ''}\n"
            )
        return key, sent_bytes, elapsed

    def upload_subset(
        self,
        well_zips: list[Path],
        remote_path: str,
        *,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> tuple[int, float, int]:
        """Upload a chosen subset of zips to ``remote_path``.

        Shares all the safety knobs of :meth:`ensure_uploaded` (the
        ``--info`` gating, the bounded retry on transient codes, the
        rsync transport policy from :func:`_rsync_ssh_transport`)
        but skips the cache-key bookkeeping — used by batched mode
        where ``remote_path`` is a single shared input dir that
        sees a rolling window of well-zips while the per-well
        Fetcher cleanup drains it.

        Returns ``(bytes_transferred, elapsed_s, returncode)``.
        """
        if not well_zips:
            return 0, 0.0, 0
        sources = [to_rsync_path(p) for p in well_zips]
        dest = f"{self.host.ssh_alias}:{remote_path}/"
        cmd = [
            resolve_rsync_binary(),
            *_rsync_ssh_transport(self.host),
            "-aH",
            "--partial",
        ]
        if _rsync_supports_append_verify():
            cmd.append("--append-verify")
        if _rsync_supports_info_flag():
            cmd.append("--info=name1,stats2,progress2")
        if log_fn:
            log_fn(
                f"[remote] $ {' '.join(shlex.quote(c) for c in cmd)} … → {dest} "
                f"({len(sources)} file(s))\n"
            )
        cmd += [*sources, dest]
        # Shared streaming retry loop (audit A11).
        sent_bytes, elapsed, rc, _attempts = _stream_rsync_with_retry(
            cmd, log_fn=log_fn, progress_fn=progress_fn, stop_event=stop_event,
        )
        return sent_bytes, elapsed, rc

    def gc(
        self, max_bytes: int, *, log_fn: Callable[[str], None] | None = None
    ) -> None:
        """LRU-evict old dataset caches until the staging dir is under
        ``max_bytes``.

        v5.2.11: gated on ``host.auto_gc``. Defaults to OFF so a
        shared-machine remote never has files deleted without the
        user opting in. Power users set ``auto_gc = true`` in
        remotes.toml; the field is documented on ``RemoteHost``.
        """
        if not self.host.auto_gc:
            if log_fn:
                log_fn(
                    "[remote] gc: skipped (auto_gc disabled). "
                    "Set auto_gc=true in remotes.toml to enable LRU "
                    "eviction of old dataset caches.\n"
                )
            return
        # Send the script via stdin so the remote bash parses
        # ~/.allwell-staging normally. Embedding it in a one-line ssh
        # arg always loses tilde expansion via outer shell quoting.
        listing = self._ssh_script(
            f"cd {self.host.remote_staging_dir}/datasets 2>/dev/null && "
            "du -sb -- */ 2>/dev/null | sort -nr || true",
            check=False,
        )
        if not listing or not listing.stdout:
            return
        entries: list[tuple[int, str]] = []
        for line in listing.stdout.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0].isdigit():
                entries.append((int(parts[0]), parts[1].rstrip("/")))
        total = sum(b for b, _ in entries)
        if total <= max_bytes:
            return
        # Sort by access time ascending (oldest first). We don't have an
        # easy LRU stamp without a stat round-trip, so approximate via the
        # current sort: largest-first is wrong, so refetch with atime.
        atime_cmd = self._ssh_script(
            f"cd {self.host.remote_staging_dir}/datasets 2>/dev/null && "
            "stat -c '%X %n' -- */ 2>/dev/null | sort -n || true",
            check=False,
        )
        if not atime_cmd or not atime_cmd.stdout:
            return
        ordered: list[str] = []
        for line in atime_cmd.stdout.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and parts[0].isdigit():
                ordered.append(parts[1].rstrip("/"))
        size_by_name = {n: b for b, n in entries}
        for name in ordered:
            if total <= max_bytes:
                break
            b = size_by_name.get(name, 0)
            self._ssh_run(
                ["rm", "-rf", f"{self.host.remote_staging_dir}/datasets/{name}"],
                check=False,
            )
            total -= b
            if log_fn:
                log_fn(f"[remote] gc: evicted dataset {name} ({b // (1024**2)} MB)\n")

    def _ssh_run(
        self, remote_argv: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str] | None:
        # v5.2.11: use the tilde-aware joiner so ``mkdir -p ~/foo`` reaches
        # the remote shell as ``mkdir -p "$HOME"/foo`` rather than as
        # ``mkdir -p '~/foo'`` (which creates a literal ``~`` directory).
        cmd = self.host.ssh_argv(allocate_tty=False) + [
            _remote_join(remote_argv),
        ]
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, check=check
            )
        except subprocess.CalledProcessError as exc:
            if check:
                raise
            return exc  # type: ignore[return-value]

    def _ssh_script(
        self, script: str, *, check: bool,
    ) -> subprocess.CompletedProcess[str] | None:
        """Pipe ``script`` to the remote bash via stdin.

        For multi-line / embedded-glob scripts, this is safer than
        building a one-line command via ``_ssh_run`` + ``_remote_join``:
        bash reads the script from stdin and parses it normally, so
        any ``~`` inside expands without us having to know about it.
        The price is one extra round-trip (no, the same number — ssh
        executes immediately) and the requirement that ``bash`` be on
        PATH on the remote (it is, for every host our framework supports).
        """
        cmd = self.host.ssh_argv(allocate_tty=False) + ["bash", "-s"]
        # v5.x.x: pipe the script as bytes, not as a text-mode string.
        # On Windows, text=True translates ``\n`` → ``\r\n`` when
        # writing to ssh's stdin pipe; the remote bash then sees ``\r``
        # at the end of every line and bails with
        # ``bash: line 1: $'\r': command not found``. Decode the
        # captured streams on the way back so the CompletedProcess
        # ``.stdout`` / ``.stderr`` remain ``str``.
        try:
            raw = subprocess.run(
                cmd, input=script.encode("utf-8"),
                capture_output=True, check=check,
            )
        except subprocess.CalledProcessError as exc:
            if isinstance(exc.stdout, (bytes, bytearray)):
                exc.stdout = exc.stdout.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, (bytes, bytearray)):
                exc.stderr = exc.stderr.decode("utf-8", errors="replace")
            if check:
                raise
            return exc  # type: ignore[return-value]
        return subprocess.CompletedProcess(
            raw.args, raw.returncode,
            raw.stdout.decode("utf-8", errors="replace") if raw.stdout else "",
            raw.stderr.decode("utf-8", errors="replace") if raw.stderr else "",
        )


_RSYNC_FILENAME_RE = re.compile(
    # Positive match: a single token (NO whitespace anywhere) that
    # either contains a path separator (e.g. ``subdir/foo.tif``) or
    # ends with a 1–8 character extension (e.g. ``A01.zip``,
    # ``process_microscopy.py``, ``_Docs/requirements-remote.txt``).
    r"^\S+(?:[/\\]\S+|\.[A-Za-z0-9]{1,8})$"
)


def _looks_like_rsync_filename(line: str) -> bool:
    """Return True iff ``line`` looks like an rsync ``--info=name1``
    filename emission (vs progress, stats, warning, or error noise).

    Uses a POSITIVE match rather than elimination. The original
    negate-known-prefixes approach was leaky — rsync emits dozens of
    status lines (``total size is X``, ``Number of files: N``,
    ``sending incremental file list``, ``created directory /foo``,
    ``file has vanished: x.tmp``, ``deleting old.zip``, etc.) and
    every new one would silently get misclassified as a "filename"
    until the prefix list was extended.

    The positive form keys on two observations: (a) every transferred
    filename rsync emits with ``--info=name1`` is a single
    whitespace-free token, and (b) every status/diagnostic line
    contains whitespace (the prose part). So a no-whitespace line
    that also looks like a path is the filename; anything else is
    noise.
    """
    if not line:
        return False
    return bool(_RSYNC_FILENAME_RE.match(line))


def _iter_rsync_lines(stdout) -> Iterable[str]:
    """Yield rsync stdout 'lines' split on BOTH ``\\n`` and ``\\r``.

    Critical for surfacing ``--info=progress2`` updates in real time:
    rsync terminates its in-place updating progress bar with ``\\r``,
    not ``\\n``. The default ``for line in proc.stdout`` only splits
    on ``\\n``, so during a multi-GB transfer of a single well zip
    the progress updates accumulate in the pipe buffer for minutes
    until rsync moves on to the next file (or finishes). On Windows
    where non-TTY pipes are block-buffered at the OS level, this
    becomes ten-plus-minute silences in the GUI log — users
    reasonably assume the pipeline has hung.

    Reads small chunks (raw bytes), splits on ``[\\r\\n]+``, decodes
    each chunk on its own. Empty splits (consecutive terminators,
    typical of ``\\r\\n`` from Windows-built tools) are filtered.
    Returns a generator so the caller can interleave per-line work
    with reading.
    """
    buf = b""
    line_re = re.compile(rb"[\r\n]+")
    while True:
        chunk = stdout.read(64)
        if not chunk:
            if buf:
                yield buf.decode("utf-8", errors="replace")
            return
        buf += chunk
        parts = line_re.split(buf)
        # Last element may be a partial line (no terminator yet);
        # keep it for the next iteration.
        for part in parts[:-1]:
            if part:
                yield part.decode("utf-8", errors="replace")
        buf = parts[-1]


def _run_rsync_streaming(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None,
    progress_fn: Callable[[float], None] | None,
    stop_event: threading.Event | None,
) -> tuple[int, int]:
    """Run rsync, stream its stdout, return ``(bytes_sent, returncode)``.

    Honors the stop_event by terminating the rsync subprocess. Parses the
    ``--info=stats2`` summary line at the end for the byte count.

    v5.1.1: return value gained the exit code so the caller can drive a
    bounded retry on transient network failures (rsync codes 10/12/30/35).

    v5.4.5: binary stdout + manual ``[\\r\\n]+`` splitting via
    ``_iter_rsync_lines`` so ``--info=progress2`` updates (which
    terminate with ``\\r``) reach the log promptly. Without this, the
    GUI shows up to ten minutes of silence between the
    ``[remote] $ rsync …`` line and the first per-file ↑ marker
    during the upload of a large well zip.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # Binary mode + small ``read`` chunks so we can split on
        # ``\r`` alongside ``\n``. ``text=True`` + ``for line in
        # stdout`` only saw ``\n`` and missed every progress update.
        bufsize=0,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    sent_bytes = 0
    progress_re = re.compile(r"(\d[\d,]*)\s+(\d+)%\s+(\S+)\s+\S+(?:\s+\(.*\))?")
    bytes_sent_re = re.compile(r"^[Tt]otal transferred file size:\s+([\d,]+)\s+bytes")
    bytes_actual_re = re.compile(r"^sent\s+([\d,]+)\s+bytes")
    # v5.2.20: when --info=name1 is on, rsync emits a bare filename
    # per transferred file. Detect them by elimination — they're
    # non-empty lines that aren't a progress line, a stats footer,
    # an rsync error line, or one of the well-known directory or
    # incremental-listing markers. Captures both the well-zip names
    # (e.g. "A01.zip") and the required-files names (e.g.
    # "process_microscopy.py", "_Docs/requirements-remote.txt").
    file_count = 0
    # Rate-limit the progress-bar surfacing — rsync can emit many
    # progress updates per second, and the GUI doesn't need
    # sub-second resolution. Emit at most every 2 seconds, plus the
    # final 100% line.
    last_progress_log = 0.0
    PROGRESS_LOG_MIN_INTERVAL = 1.0

    try:
        assert proc.stdout is not None
        for line in _iter_rsync_lines(proc.stdout):
            if stop_event and stop_event.is_set():
                proc.terminate()
                break
            # Progress lines (--info=progress2) print to a TTY-ish format
            # with carriage returns. We now read \r-delimited and can
            # surface them as percentage updates.
            m = progress_re.search(line)
            is_progress = m is not None
            if is_progress and progress_fn:
                try:
                    pct = float(m.group(2)) / 100.0
                    progress_fn(pct)
                except ValueError:
                    pass
            m_bytes = bytes_actual_re.match(line)
            if m_bytes:
                sent_bytes = int(m_bytes.group(1).replace(",", ""))
            elif sent_bytes == 0:
                m_bytes = bytes_sent_re.match(line)
                if m_bytes:
                    sent_bytes = int(m_bytes.group(1).replace(",", ""))
            if log_fn:
                if line.startswith(("sent ", "Total ", "rsync:", "rsync error")):
                    log_fn(f"[remote] {line}\n")
                elif is_progress:
                    # Rate-limited so a fast link doesn't flood the GUI
                    # with progress lines, but so the user does see
                    # SOMETHING during a long upload of a single file.
                    now = time.monotonic()
                    if now - last_progress_log >= PROGRESS_LOG_MIN_INTERVAL:
                        log_fn(f"[remote] ↑ {line}\n")
                        last_progress_log = now
                elif _looks_like_rsync_filename(line):
                    file_count += 1
                    log_fn(f"[remote] ↑ ({file_count}) {line}\n")
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return sent_bytes, proc.returncode if proc.returncode is not None else -1


# Exit codes rsync uses to signal a network-side failure (per rsync(1)
# manpage, "EXIT VALUES" section). Anything outside this set is treated
# as a configuration / permission / logic error and surfaces immediately
# without retry — re-running such an error wouldn't help and would mask
# the real problem.
#
#   10: error in socket I/O
#   11: error in file I/O
#   12: error in rsync protocol data stream
#   30: timeout in data send/receive
#   35: timeout waiting for daemon connection
#
# 11 is here because it is what a fault on the *local* end of a transfer looks
# like: rsync writing a fetched well onto a network-mounted output dir reports
# ``write failed on "…": Bad file descriptor (9)`` and exits 11 when the SMB
# session drops and reconnects mid-file. That is the same transient the
# pipeline's own writes retry through, and without it a single blip lost the
# well's CSVs for the rest of the run. Genuine, non-transient file errors
# (permission denied, no such file) exit 23 or 3 and still surface immediately.
_RSYNC_RETRY_CODES = {10, 11, 12, 30, 35}

# Backoff schedule for rsync retries — mirrors the git push/pull policy
# documented at the top of the runner's invocation env. Total wait
# across the three retries is 14 s, an upper bound the user can sit
# through without thinking the run is hung.
_RSYNC_RETRY_DELAYS = (2.0, 4.0, 8.0)


def _stream_rsync_with_retry(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None = None,
    progress_fn=None,
    stop_event: threading.Event | None = None,
) -> tuple[int, float, int, int]:
    """Run a *streaming* rsync (:func:`_run_rsync_streaming`) with bounded retry
    on transient network failures, resuming via ``--partial`` each attempt.

    The progress-streaming twin of :func:`_rsync_with_retry` (which *captures*
    output for small control-plane rsyncs). Consolidates the two byte-identical
    retry loops the ``ensure_uploaded`` + ``upload_subset`` transfer paths
    hand-rolled (audit A11). Non-transient rsync errors break out on the first
    attempt; a set ``stop_event`` breaks at the top of the next iteration.

    Returns ``(sent_bytes, elapsed_s, rc, attempts_run)`` — ``sent_bytes``
    accumulates the incremental bytes each resumed attempt sent.
    """
    t0 = time.monotonic()
    sent_bytes = 0
    rc = -1
    attempts_run = 0  # v5.2.15: actual count, for the error message
    for attempt, delay in enumerate((0.0,) + _RSYNC_RETRY_DELAYS):
        if stop_event is not None and stop_event.is_set():
            break
        if delay > 0:
            if log_fn:
                log_fn(
                    f"[remote] rsync retry {attempt}/{len(_RSYNC_RETRY_DELAYS)} "
                    f"in {delay:.0f}s (last exit code {rc})\n"
                )
            time.sleep(delay)
        attempt_bytes, rc = _run_rsync_streaming(
            cmd, log_fn=log_fn, progress_fn=progress_fn, stop_event=stop_event,
        )
        sent_bytes += attempt_bytes  # each resumed attempt sends incremental bytes
        attempts_run += 1
        if rc == 0 or rc not in _RSYNC_RETRY_CODES:
            break
    elapsed = max(time.monotonic() - t0, 1e-6)
    return sent_bytes, elapsed, rc, attempts_run


def _rsync_with_retry(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None = None,
    timeout: int | None = 300,
    capture_output: bool = True,
    cwd: str | None = None,
    stop_event: threading.Event | None = None,
) -> subprocess.CompletedProcess:
    """Run an rsync command, retrying on transient network failures.

    Returns the final ``CompletedProcess``. A success on the first try
    is the common path; transient failures (rsync exit codes 10/12/30/35
    or an OSError from the subprocess machinery itself) trigger up to
    three retries with exponential backoff. Non-transient failures
    (any other non-zero exit code) return immediately so logic /
    configuration errors don't get masked behind a retry storm.

    This is the v5.1.1 patch to the v5.0.0 runner — every rsync call
    site in the framework funnels through here.

    v5.2.12: ``cwd`` is passed through to ``subprocess.run`` so callers
    can use bare relative source paths (the only portable way to make
    ``rsync -aR`` recreate the right directory layout on the remote;
    the absolute-path-with-``/./``-pivot trick is fragile on older
    rsync versions, which mirror the full local path instead).

    v5.2.28: ``stop_event`` makes the rsync cancellable. When set, the
    in-flight rsync subprocess is terminated and the retry loop
    short-circuits with synthetic ``rc=130`` (SIGINT-style). The
    Fetcher passes its own stop event through here so the Stop
    button actually interrupts per-well downloads instead of
    waiting for each rsync to finish on its own.
    """
    log = log_fn or (lambda _m: None)
    delays = (0.0,) + _RSYNC_RETRY_DELAYS  # delay before each attempt
    last_result: subprocess.CompletedProcess | None = None
    last_exc: BaseException | None = None

    for attempt, delay in enumerate(delays):
        if stop_event is not None and stop_event.is_set():
            log("[remote] rsync cancelled by Stop request.\n")
            return subprocess.CompletedProcess(
                cmd, returncode=130, stdout="", stderr="cancelled",
            )
        if delay > 0:
            log(
                f"[remote] rsync retry {attempt}/{len(_RSYNC_RETRY_DELAYS)} "
                f"in {delay:.0f}s (last exit code "
                f"{last_result.returncode if last_result else '?'})\n"
            )
            # Cooperatively interruptible sleep — check stop_event
            # every 0.25s so a long backoff doesn't deafen Stop.
            slept = 0.0
            while slept < delay:
                if stop_event is not None and stop_event.is_set():
                    log("[remote] rsync cancelled during retry backoff.\n")
                    return subprocess.CompletedProcess(
                        cmd, returncode=130, stdout="", stderr="cancelled",
                    )
                step = min(0.25, delay - slept)
                time.sleep(step)
                slept += step
        try:
            result = _rsync_run_cancellable(
                cmd,
                timeout=timeout,
                capture_output=capture_output,
                cwd=cwd,
                stop_event=stop_event,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            last_exc = exc
            log(f"[remote] rsync attempt {attempt + 1} raised: {exc}\n")
            continue
        last_result = result
        last_exc = None
        if result.returncode == 0:
            return result
        if result.returncode == 130:
            # Stop fired mid-rsync — don't retry, return immediately.
            return result
        if result.returncode not in _RSYNC_RETRY_CODES:
            # Non-transient — return immediately so the caller sees the
            # real error.
            return result

    if last_result is not None:
        return last_result
    # Every attempt raised — synthesise a CompletedProcess so callers
    # have a uniform return type.
    return subprocess.CompletedProcess(
        cmd, returncode=255, stdout="", stderr=str(last_exc),
    )


def _rsync_run_cancellable(
    cmd: list[str],
    *,
    timeout: int | None,
    capture_output: bool,
    cwd: str | None,
    stop_event: threading.Event | None,
) -> subprocess.CompletedProcess:
    """``subprocess.run`` work-alike that polls ``stop_event`` while
    the child runs and terminates it on cancellation. v5.2.28.

    When ``stop_event`` is None this is equivalent to ``subprocess.run``
    — the only path that runs without cancellation is the bootstrap
    ``sync_required_files`` call, which is small and short-lived.

    ``timeout=None`` disables the wall-clock deadline entirely: the caller
    is relying on rsync's own ``--timeout`` (I/O-idle) + ssh ``ConnectTimeout``
    to bound stalls, so a large but steadily-progressing transfer is never
    killed mid-flight (the bug that made the final per-well fetch retry-loop
    and fail on big zips / slow links)."""
    if stop_event is None:
        return subprocess.run(
            cmd, capture_output=capture_output, text=True,
            timeout=timeout, cwd=cwd,
        )
    stdout_pipe = subprocess.PIPE if capture_output else None
    stderr_pipe = subprocess.PIPE if capture_output else None
    proc = subprocess.Popen(
        cmd, stdout=stdout_pipe, stderr=stderr_pipe, text=True, cwd=cwd,
    )
    deadline = None if timeout is None else time.monotonic() + timeout
    rc: int | None = None
    try:
        while True:
            try:
                rc = proc.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                if stop_event.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return subprocess.CompletedProcess(
                        cmd, returncode=130, stdout="", stderr="cancelled",
                    )
                if deadline is not None and time.monotonic() > deadline:
                    proc.kill()
                    raise subprocess.TimeoutExpired(cmd, timeout)
                continue
            break
    finally:
        if proc.poll() is None:
            proc.terminate()
    out, err = ("", "")
    if capture_output and proc.stdout is not None:
        try:
            out = proc.stdout.read()
        except Exception:
            out = ""
    if capture_output and proc.stderr is not None:
        try:
            err = proc.stderr.read()
        except Exception:
            err = ""
    assert rc is not None
    return subprocess.CompletedProcess(cmd, rc, out, err)


# ────────────────────────────────────────────────────────────────────────────
# RemoteRunner
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class _RemoteRun:
    """Internal handle: everything one in-flight run needs to track."""

    run_id: str
    cache_key: str
    remote_input_dir: str
    remote_output_dir: str
    remote_pidfile: str
    local_output_dir: Path
    local_input_mode: LocalInputMode
    # v5.2.29: tracked here so the verify step can check the right
    # local artifacts (CSV always; out_zip / extracted folder only
    # when the user opted into the full output set).
    output_filter: OutputFilter = OutputFilter.FULL
    expected_wells: set[str] = field(default_factory=set)
    fetched_wells: set[str] = field(default_factory=set)
    well_count_total: int = 0
    # Incremental remote run: fetched outputs are PARTIAL (new FOVs only), so the
    # Fetcher merges them into the existing local CSV / out-zip instead of
    # overwriting.
    incremental: bool = False


class _RemoteProcess:
    """Popen-compatible wrapper around the ssh client.

    Exposes the same surface (``stdout``, ``poll()``, ``wait()``, ``pid``,
    ``returncode``) so the existing ``_run_pipeline_subprocess`` reader
    loop in ``services/pipeline_runner.py`` works unchanged.

    The added value: a background Fetcher thread tees ``stdout`` while
    the main thread reads it, so each ``— done.`` line triggers a
    same-process per-well rsync without the GUI needing to know.
    """

    def __init__(
        self,
        ssh_proc: subprocess.Popen,
        run: _RemoteRun,
        fetcher: Fetcher,
    ):
        self._ssh = ssh_proc
        self._run = run
        self._fetcher = fetcher
        self._stdout_pipe: io.TextIOBase | None = None
        # Tee the ssh stdout into the fetcher AND back out to the caller.
        # The caller (PipelineRunner._run_pipeline_subprocess) reads from
        # self.stdout one line at a time; the fetcher also receives
        # every line via the same tee.
        self._tee_thread: threading.Thread | None = None
        self._line_q: queue.Queue[str | None] = queue.Queue()
        self._start_tee()

    # Popen-compatible attributes ───────────────────────────────────────

    @property
    def pid(self) -> int:
        return self._ssh.pid

    @property
    def returncode(self) -> int | None:
        return self._ssh.returncode

    @property
    def stdout(self) -> _QueueLineIterator:
        return self._lines

    def poll(self) -> int | None:
        return self._ssh.poll()

    def wait(self, timeout: float | None = None) -> int:
        rc = self._ssh.wait(timeout=timeout)
        # Drain remaining lines, then close the queue.
        if self._tee_thread is not None:
            self._tee_thread.join(timeout=5)
        self._line_q.put(None)
        self._fetcher.flush_and_join()
        return rc

    def terminate(self) -> None:
        self._fetcher.cancel()
        self._ssh.terminate()

    def kill(self) -> None:
        self._fetcher.cancel()
        self._ssh.kill()

    def send_signal(self, sig: int) -> None:
        self._ssh.send_signal(sig)

    # Internals ──────────────────────────────────────────────────────────

    def _start_tee(self) -> None:
        assert self._ssh.stdout is not None
        self._lines = _QueueLineIterator(self._line_q)

        def _pump(src: io.TextIOBase, q: queue.Queue[str | None]) -> None:
            try:
                for line in src:
                    self._fetcher.observe(line)
                    q.put(line)
            finally:
                q.put(None)

        self._tee_thread = threading.Thread(
            target=_pump,
            args=(self._ssh.stdout, self._line_q),
            daemon=True,
        )
        self._tee_thread.start()


def _parse_argv_flags(argv, multi: set[str], single: set[str]) -> dict:
    """Read *multi*- and *single*-valued flags out of a pipeline argv.

    A multi-valued flag (``--fluor_tokens GFP mCherry DAPI``) consumes entries
    until the next ``--``. A plain ``next(it)`` picks up only the FIRST, which is
    how the batched-remote threshold pass came to see one channel of many.
    """
    out: dict = {}
    argv = [str(a) for a in argv]
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in multi:
            values: list[str] = []
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                values.append(argv[i])
                i += 1
            out[arg] = values
            continue
        if arg in single:
            out[arg] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
            continue
        i += 1
    return out


def _auto_threshold_args_from_argv(argv):
    """Reconstruct enough of the pipeline's parsed args to re-run its threshold
    pass locally after a batched remote run.

    Rebuilding the arg object rather than re-implementing the pass is what keeps
    the local and remote answers identical when either gains a mode — this path
    previously drifted into thresholding only the first fluor channel.

    ``no_auto_threshold`` is forced False: the flag in *argv* is the batching
    suppression, not the user's choice, so honouring it would mean a batched run
    never got thresholds at all.
    """
    from types import SimpleNamespace

    flags = _parse_argv_flags(
        argv,
        multi={"--fluor_tokens"},
        single={
            "--nuclear_token", "--cytoplasm_token", "--segmentation_method",
            "--csv_prefix", "--filename_schema", "--filename_sep",
            "--pos_control_well", "--neg_control_well",
        },
    )
    return SimpleNamespace(
        no_auto_threshold=False,
        fluor_tokens=flags.get("--fluor_tokens", []),
        nuclear_token=flags.get("--nuclear_token", ""),
        cytoplasm_token=flags.get("--cytoplasm_token", ""),
        segmentation_method=flags.get("--segmentation_method", ""),
        csv_prefix=flags.get("--csv_prefix", "measurements"),
        filename_schema=flags.get("--filename_schema", ""),
        filename_sep=flags.get("--filename_sep", "_"),
        pos_control_well=flags.get("--pos_control_well", ""),
        neg_control_well=flags.get("--neg_control_well", ""),
    )


class _BatchedRemoteProcess:
    """``_RemoteProcess``-compatible wrapper that runs the pipeline in
    sequential batches of ``batch_size`` wells.

    Each batch: upload that batch's zips into the shared remote input
    dir, spawn the pipeline (sees just those zips because the Fetcher
    cleaned out the prior batch), tee its stdout to the caller +
    Fetcher, wait for it to exit. Combined returncode is the first
    non-zero or 0 on all-success. A single ``Fetcher`` instance
    persists across all batches with ``delete_after_download=True``
    so peak remote disk = ``(M wells of input) + (~1 well of output)``.

    Exposes the same Popen-compatible surface as ``_RemoteProcess``
    (``stdout``, ``poll()``, ``wait()``, ``terminate()``, ``kill()``,
    ``pid``, ``returncode``) so :meth:`pipeline_runner._run_pipeline_remote`
    can consume either transparently.
    """

    def __init__(
        self,
        runner: RemoteRunner,
        zipped: ZippedInput,
        local_output_dir: Path,
        pipeline_argv: list[str],
        batch_size: int,
        *,
        output_filter: OutputFilter,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ):
        self._runner = runner
        self._zipped = zipped
        self._local_output_dir = local_output_dir
        self._pipeline_argv = pipeline_argv
        self._batch_size = max(1, batch_size)
        self._output_filter = output_filter
        self._log = log_fn or (lambda _m: None)
        self._progress = progress_fn
        self._stop = stop_event or threading.Event()
        self._returncode: int | None = None
        self._line_q: queue.Queue[str | None] = queue.Queue()
        self._lines = _QueueLineIterator(self._line_q)
        self._current_proc: subprocess.Popen | None = None
        # Guards _current_proc: the worker thread reassigns it per batch while
        # the GUI thread reads it in terminate()/kill()/pid/send_signal (E20).
        self._current_proc_lock = threading.Lock()

        # Set up the shared run identifiers and remote dirs.
        # Keep the single-shot path's layout: input goes into the
        # persistent dataset cache (``datasets/<key>``) and output
        # into ``runs/<run_id>/out``. The "stale zips" diagnosis
        # of v5.4.7 was incorrect — the user cleared remote dirs
        # manually before the run that showed "processed 5, skipped
        # 5" — so we're not switching dirs blindly. The actual cause
        # of that log line is still under investigation; if it turns
        # out to be cache-dir pollution we can switch later, with
        # evidence.
        key = runner._cache.cache_key(zipped)
        run_id = "run-" + _compute_run_id(key, pipeline_argv)
        remote_input = runner._cache.remote_dataset_dir(key)
        remote_output = (
            f"{runner.host.remote_staging_dir}/runs/{run_id}/out"
        )
        remote_pidfile = (
            f"{runner.host.remote_staging_dir}/runs/{run_id}/run.pid"
        )
        runner._cache._ssh_run(
            ["mkdir", "-p", remote_input, remote_output],
            check=True,
        )

        runner._run = _RemoteRun(
            run_id=run_id,
            cache_key=key,
            remote_input_dir=remote_input,
            remote_output_dir=remote_output,
            remote_pidfile=remote_pidfile,
            local_output_dir=local_output_dir,
            local_input_mode=zipped.mode,
            output_filter=output_filter,
            incremental=zipped.incremental,
        )

        # One Fetcher persisting across batches. Pre-set the total well
        # count: each batch's pipeline banner reports just THAT
        # batch's count, which would corrupt the cross-batch progress
        # math if the Fetcher trusted the per-batch banner.
        self._fetcher = Fetcher(
            runner.host, runner._run,
            output_filter=output_filter,
            log_fn=log_fn, progress_fn=progress_fn,
            delete_after_download=True,
        )
        # ``well_count_total`` is set in ``_run_batches`` AFTER the
        # local-completion filter runs (a pre-filter set would lock
        # in the raw count and the GUI's progress denominator would
        # be wrong when some wells were already-done locally).

        self._worker = threading.Thread(
            target=self._run_batches, daemon=True,
        )
        self._worker.start()

    # Popen-compatible surface ─────────────────────────────────────────

    @property
    def pid(self) -> int:
        with self._current_proc_lock:
            proc = self._current_proc
        return proc.pid if proc else -1

    @property
    def returncode(self) -> int | None:
        return self._returncode

    @property
    def stdout(self) -> _QueueLineIterator:
        return self._lines

    def poll(self) -> int | None:
        if self._worker.is_alive():
            return None
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            # Timed out; the worker is still running — not a completion, so
            # report the current (possibly None→0) state without inventing a
            # success code.
            return self._returncode if self._returncode is not None else 0
        # The worker finished. A ``None`` code here means it crashed before
        # setting one — surface that as a failure rather than coercing it to 0
        # (which previously made a crashed batched run look successful and
        # triggered verify_and_cleanup to delete remote data — audit E1).
        if self._returncode is None:
            self._returncode = 1
        return self._returncode

    def terminate(self) -> None:
        self._stop.set()
        with self._current_proc_lock:
            proc = self._current_proc
        if proc is not None:
            proc.terminate()
        self._fetcher.cancel()

    def kill(self) -> None:
        self._stop.set()
        with self._current_proc_lock:
            proc = self._current_proc
        if proc is not None:
            proc.kill()
        self._fetcher.cancel()

    def send_signal(self, sig: int) -> None:
        with self._current_proc_lock:
            proc = self._current_proc
        if proc is not None:
            proc.send_signal(sig)

    # Internals ────────────────────────────────────────────────────────

    def _run_batches(self) -> None:
        try:
            all_zips = list(self._zipped.well_zips)

            # v5.4.7: local-completion filter. The remote pipeline's
            # own skip check (``output_exists_for_well``) looks at the
            # REMOTE output dir — but per-well Fetcher cleanup deletes
            # outputs there as soon as they land locally, so a batched
            # re-run never sees a cache hit on the remote side. Instead
            # check the LOCAL ``output_dir`` up front.
            #
            # v5.4.8: relaxed from "both ``{well}_out.zip`` AND the CSV"
            # to "CSV only". The zip's local presence depends on the
            # output mode the Fetcher used to materialize it:
            #
            # * ZIP / FLAT mode → ``shutil.copyfile`` lands the zip
            #   verbatim at ``local_output_dir/{well}_out.zip``.
            # * FOLDER mode → ``zipfile.extractall`` extracts the zip
            #   contents (loose QC files) and the zip itself is
            #   discarded.
            # * CSV_ONLY mode → the zip is dropped on the floor
            #   entirely.
            #
            # So the zip exists locally in only ONE of three Fetcher
            # paths — requiring it broke the filter for the other two
            # and the user saw "wells were re-processed even though
            # output already existed". The per-well CSV is written by
            # the pipeline regardless of mode and pulled by the
            # Fetcher unconditionally, so it's the canonical
            # "this well is done" signal.
            #
            # ``--force`` in the pipeline argv short-circuits the
            # filter (matches the single-shot path and
            # ``process_microscopy.py``'s own ``force`` semantics).
            force = "--force" in self._pipeline_argv
            csv_prefix = "measurements"
            it = iter(self._pipeline_argv)
            for arg in it:
                if arg == "--csv_prefix":
                    csv_prefix = next(it, csv_prefix)
                    break
            # Incremental already split client-side: the staged set holds only
            # wells with genuinely-new FOVs (up-to-date wells were dropped during
            # staging), and each kept zip carries only its new-FOV members. The
            # local-CSV "already complete" skip would wrongly drop these touched
            # wells (they DO have a local CSV), so bypass it for incremental.
            if not force and not self._zipped.incremental:
                skipped: list[Path] = []
                kept: list[Path] = []
                for p in all_zips:
                    well = p.stem.upper()
                    csv_path = self._local_output_dir / f"{csv_prefix}_{well}.csv"
                    if csv_path.exists() and csv_path.stat().st_size > 0:
                        skipped.append(p)
                    else:
                        kept.append(p)
                if skipped:
                    self._log(
                        f"[remote] batched mode: {len(skipped)} well(s) "
                        f"already complete locally (per-well CSV present "
                        f"under {self._local_output_dir.name}/) — skipping "
                        f"upload + compute. Pass --force to reprocess.\n"
                    )
                    if len(skipped) <= 8:
                        self._log(
                            "[remote] skipped wells: "
                            + ", ".join(p.stem for p in skipped)
                            + "\n"
                        )
                    all_zips = kept

            if not all_zips:
                self._log(
                    "[remote] batched mode: every well already complete "
                    "locally — nothing to do.\n"
                )
                self._fetcher.run.well_count_total = 0
                self._returncode = 0
                return

            self._fetcher.run.well_count_total = len(all_zips)

            batches = [
                all_zips[i:i + self._batch_size]
                for i in range(0, len(all_zips), self._batch_size)
            ]
            n_batches = len(batches)
            self._log(
                f"[remote] batched mode: {len(all_zips)} well(s) in "
                f"{n_batches} batch(es) of up to {self._batch_size} "
                f"(peak remote disk ≈ {self._batch_size} well-input)\n"
            )

            # Inject a synthetic "Zip mode: <total> wells" banner
            # BEFORE batch 1's stdout. The GUI's ``ProgressTracker``
            # locks on the first such banner via
            # ``if not self.well_total:``; without this, each batch's
            # own banner reports just THAT batch's count (e.g. 50)
            # and well_done climbs past 100% as later batches add up
            # against the wrong denominator. We always say "Zip mode"
            # because the remote pipeline sees zips on disk regardless
            # of the local input mode the user started from. The "—
            # batched" suffix is informational only; the regex
            # ``\s+well`` matches both "well" and "wells" so the
            # parenthetical doesn't disturb the match.
            self._line_q.put(f"Zip mode: {len(all_zips)} wells — batched\n")

            for batch_idx, batch_zips in enumerate(batches):
                if self._stop.is_set():
                    self._log(
                        f"[remote] stopped before batch "
                        f"{batch_idx + 1}/{n_batches}\n"
                    )
                    self._returncode = 130
                    return

                self._log(
                    f"[remote] batch {batch_idx + 1}/{n_batches}: "
                    f"uploading {len(batch_zips)} well-zip(s)\n"
                )
                # v5.4.7: scale per-batch upload progress so the
                # status label advances smoothly across the WHOLE
                # run instead of resetting from 0 → 1 once per
                # batch. Each batch's rsync calls progress_fn with
                # ``pct`` in [0, 1]; map to
                # ``(batch_idx + pct) / n_batches`` so batch 1's
                # uploads animate 0 → 1/n_batches, batch 2's
                # 1/n_batches → 2/n_batches, etc.
                _outer_progress = self._progress
                if _outer_progress is not None:
                    def _scaled_progress(
                        pct: float,
                        _idx: int = batch_idx,
                        _n: int = n_batches,
                        _outer=_outer_progress,
                    ) -> None:
                        _outer((_idx + pct) / _n)
                    inner_progress: Callable[[float], None] | None = _scaled_progress
                else:
                    inner_progress = None
                sent_bytes, _elapsed, rc = self._runner._cache.upload_subset(
                    batch_zips,
                    self._runner._run.remote_input_dir,
                    log_fn=self._log,
                    progress_fn=inner_progress,
                    stop_event=self._stop,
                )
                if rc != 0:
                    self._log(
                        f"[remote] batch {batch_idx + 1} upload failed "
                        f"(rsync rc={rc}); aborting remaining batches\n"
                    )
                    self._returncode = rc
                    return
                self._log(
                    f"[remote] batch {batch_idx + 1}: upload complete "
                    f"({sent_bytes / (1024**2):.1f} MB); launching pipeline\n"
                )

                # Build the remote command for this batch. Input dir
                # is shared across batches; the pipeline scans it
                # once at startup and only sees the zips currently
                # present (i.e. just this batch's, because the
                # Fetcher cleaned out the previous one).
                remote_argv = self._runner._build_remote_argv(
                    self._pipeline_argv,
                    self._runner._run.remote_input_dir,
                    self._runner._run.remote_output_dir,
                    self._output_filter,
                    self._zipped.mode,
                )
                # v5.4.7: suppress the per-batch auto-threshold pass.
                # Each batch sees only its own outputs (the Fetcher
                # downloads + deletes them as soon as they land), so
                # the in-pipeline auto-threshold would compute
                # thresholds from a sliver of the plate and overwrite
                # the previous batch's defaults each time. We run a
                # single auto-threshold pass locally after every
                # batch drains (see ``_run_local_auto_threshold``
                # below).
                if "--no_auto_threshold" not in remote_argv:
                    remote_argv = list(remote_argv) + ["--no_auto_threshold"]
                # Each batch is its own pipeline launch, so on a
                # scheduler-backed host (``compute_launcher``) each batch
                # gets its own fresh allocation on a scheduled compute
                # node. No cache-hit fast path exists in batched mode.
                remote_cmd = _compose_remote_cmd(
                    self._runner.host.compute_launcher,
                    self._runner._run.remote_pidfile,
                    remote_argv,
                    cache_hit=False,
                    env_selector=self._runner.env_selector,
                )
                ssh_argv = (
                    self._runner.host.ssh_argv(allocate_tty=True)
                    + [remote_cmd]
                )
                proc = subprocess.Popen(
                    ssh_argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    # The remote (Linux) pipeline logs UTF-8 — including the
                    # em-dash in "Well X — done." Decode the ssh stream as UTF-8
                    # so those markers survive on a Windows client (whose locale
                    # is cp1252); otherwise they arrive mojibake'd and the
                    # dashboard's per-well Process tracking never matches.
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                    start_new_session=(os.name == "posix"),
                )
                # Publish under the lock so a concurrent Stop (terminate/kill on
                # the GUI thread) reads a consistent handle, not a half-updated
                # attribute or a stale value at a batch boundary (audit E20).
                with self._current_proc_lock:
                    self._current_proc = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    if self._stop.is_set():
                        proc.terminate()
                        break
                    self._fetcher.observe(line)
                    self._line_q.put(line)
                rc = proc.wait()
                with self._current_proc_lock:
                    self._current_proc = None

                if rc != 0:
                    self._log(
                        f"[remote] batch {batch_idx + 1} pipeline exited "
                        f"{rc}; aborting remaining batches\n"
                    )
                    self._returncode = rc
                    return

                self._log(
                    f"[remote] batch {batch_idx + 1}/{n_batches} complete\n"
                )

            self._returncode = 0
        except BaseException as exc:
            # A crash anywhere in the batch loop (ssh spawn failure, argv
            # build, upload, stdout iteration) must NOT leave ``_returncode``
            # at None — that made ``wait()`` report success and
            # verify_and_cleanup delete remote data though nothing ran
            # (audit E1). Record a non-zero code and surface the error in the
            # GUI log; the ``finally`` below still drains the fetcher and, now
            # that rc != 0, skips the auto-threshold pass.
            import logging as _logging
            self._returncode = 1
            self._log(f"[remote] batched run failed: {exc}\n")
            _logging.getLogger(__name__).exception("batched remote run crashed")
        finally:
            # The stdout sentinel MUST be enqueued no matter what — if
            # flush_and_join (or the auto-threshold pass) raises, the reader
            # loop in pipeline_runner would otherwise block forever waiting on
            # a None that never arrives (audit X10). Keep it in an inner
            # ``finally`` so it's still emitted last on the happy path (after
            # the fetcher's progress lines) but guaranteed on any failure.
            try:
                # Drain the Fetcher first so every well's CSV + zip lands
                # locally BEFORE we attempt auto-threshold against
                # local_output_dir.
                self._fetcher.flush_and_join()
                # v5.4.7: single auto-threshold pass over the FULL downloaded
                # output. The per-batch passes were suppressed via
                # ``--no_auto_threshold`` so a batch wouldn't write thresholds
                # derived from a sliver of the plate. Fires only when the whole
                # run succeeded — a mid-run abort leaves the previous run's
                # pipeline_info.json (if any) untouched.
                if self._returncode == 0:
                    self._run_local_auto_threshold()
            finally:
                self._line_q.put(None)

    def _run_local_auto_threshold(self) -> None:
        """Re-run the pipeline's auto-threshold pass locally against
        the full ``local_output_dir`` after every batch has drained.
        Failures are logged but non-fatal (matches the remote
        pipeline's "informational only" stance on auto-threshold)."""
        try:
            # Late import: ``process_microscopy`` is a heavy module
            # (pulls TensorFlow indirectly). Don't pay that cost on
            # every import of remote_runner.
            sys.path.insert(0, str(repo_root()))
            from process_microscopy import run_auto_threshold_pass
            import logging
            log_pylog = logging.getLogger("allwell.local_auto_threshold")
            self._log(
                "[remote] running auto-threshold pass locally over "
                f"{self._local_output_dir} (full plate, all batches drained)\n"
            )
            args = _auto_threshold_args_from_argv(self._pipeline_argv)
            written = run_auto_threshold_pass(
                args,
                self._local_output_dir,
                [args.nuclear_token, *args.fluor_tokens] if args.nuclear_token
                else list(args.fluor_tokens),
                log_pylog,
            )
            if written:
                self._log(
                    "[remote] local auto-threshold defaults written for "
                    f"{len(written)} channel(s): "
                    + ", ".join(f"{ch.upper()}={v:.4g}" for ch, v in written.items())
                    + "\n"
                )
            else:
                self._log(
                    "[remote] local auto-threshold pass produced no new defaults.\n"
                )
        except Exception as exc:  # informational, never fatal
            self._log(
                f"[remote] warn: local auto-threshold pass failed: {exc}\n"
            )


class _QueueLineIterator:
    """Iterator that pulls lines off a queue; sentinel ``None`` ends
    iteration. Lets the Popen consumer treat us like a file."""

    def __init__(self, q: queue.Queue[str | None]):
        self._q = q

    def __iter__(self) -> _QueueLineIterator:
        return self

    def __next__(self) -> str:
        item = self._q.get()
        if item is None:
            raise StopIteration
        return item


# ────────────────────────────────────────────────────────────────────────────
# Fetcher
# ────────────────────────────────────────────────────────────────────────────


class Fetcher:
    """Per-well output puller. Triggered by stdout markers from the
    running pipeline; runs each pull in a small thread pool so the
    upload of well N+1 doesn't block the download of well N's output.
    """

    def __init__(
        self,
        host: RemoteHost,
        run: _RemoteRun,
        *,
        output_filter: OutputFilter,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
        delete_after_download: bool = False,
    ):
        self.host = host
        self.run = run
        self.output_filter = output_filter
        self._log = log_fn or (lambda _msg: None)
        self._progress = progress_fn
        self._delete_after_download = delete_after_download
        self._stop = threading.Event()
        self._pending: queue.Queue[str | None] = queue.Queue()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def observe(self, stdout_line: str) -> None:
        """Hand a pipeline-stdout line to the Fetcher.

        Looks for the ``— done.`` marker that the existing
        ``ProgressTracker`` already parses, plus the mode banner that
        announces the well total.
        """
        # v5.4.7: lock the well total to whatever set it FIRST.
        # In single-shot mode the pipeline emits exactly one banner
        # and this is a no-op refinement. In batched mode the
        # ``_BatchedRemoteProcess`` pre-sets ``well_count_total`` to
        # the full plate count and injects a synthetic banner before
        # batch 1; without this guard, each per-batch banner would
        # overwrite the lock with that batch's count, and the
        # Fetcher's per-well log lines would read e.g. "10/5: starting
        # download" once batch 2 began.
        m = _RE_MODE_WELLS.search(stdout_line)
        if m and not self.run.well_count_total:
            self.run.well_count_total = int(m.group(1))
        # v5.2.17: use .search not .match — the line carries a
        # ``YYYY-MM-DD HH:MM:SS  INFO`` prefix from the basicConfig
        # formatter in ``process_microscopy.py``. Without .search the
        # regex never matches and per-well rsync never fires.
        m = _RE_WELL_DONE.search(stdout_line)
        if m:
            well = m.group("well").upper()
            self.run.expected_wells.add(well)
            self._pending.put(well)

    def cancel(self) -> None:
        self._stop.set()
        self._pending.put(None)

    def flush_and_join(self, timeout: float = 60.0) -> None:
        """Signal end-of-stream and wait for outstanding pulls.

        Called by the Popen wrapper's ``wait()`` so the run isn't
        reported complete until every well's output has landed.

        If the fetcher was **cancelled** (Stop pressed → :meth:`cancel`), do
        *not* drain: the user asked to abort, not to wait for the wells still
        queued for download. Just reap the worker — it returns promptly on the
        stop event — and exit, so Stop is immediate instead of blocking on the
        remaining queue.
        """
        if self._stop.is_set():
            self._pending.put(None)          # unblock the worker's queue.get()
            self._worker.join(timeout=timeout)
            self._log("[remote] ↓ fetcher cancelled — "
                      "remaining downloads skipped\n")
            return
        pending_count = self._pending.qsize()
        if pending_count:
            self._log(
                f"[remote] ↓ draining fetcher: {pending_count} well(s) "
                f"still queued for download\n"
            )
        self._pending.put(None)
        # Wait for the worker to ACTUALLY finish draining — NOT a fixed 60s cap.
        # The old short timeout let join() return while downloads were still in
        # flight, so wait() returned and verify_and_cleanup deleted the remote
        # mid-fetch: "completed run → deleted cache → then finished fetcher".
        # Each per-well fetch is independently bounded (rsync --timeout + retry
        # cap), so the worker is guaranteed to terminate; block until it does,
        # logging a heartbeat each interval so a long-but-legitimate drain isn't
        # mistaken for a hang (and Stop still breaks out — cancel() sets _stop,
        # the in-flight rsync aborts, the worker exits and this join returns).
        while self._worker.is_alive():
            self._worker.join(timeout=timeout)
            if self._worker.is_alive():
                done = len(self.run.fetched_wells)
                tot = self.run.well_count_total
                self._log(
                    f"[remote] ↓ still downloading… ({done}/{tot} wells)\n"
                    if tot else
                    f"[remote] ↓ still downloading… ({done} well(s))\n"
                )
        # v5.2.18: surface the end-of-stream summary so the user
        # sees a clear "all downloads complete" marker rather than
        # the run silently going quiet.
        total = self.run.well_count_total
        fetched = len(self.run.fetched_wells)
        if total and fetched == total:
            self._log(
                f"[remote] ↓ fetcher: {fetched}/{total} wells downloaded "
                f"to {self.run.local_output_dir}\n"
            )
        elif total:
            self._log(
                f"[remote] ↓ fetcher: {fetched}/{total} wells downloaded "
                f"({total - fetched} missing — verify step will retry)\n"
            )
        else:
            self._log(
                f"[remote] ↓ fetcher: {fetched} well(s) downloaded "
                f"(remote never announced total)\n"
            )

    # Internals ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while True:
            if self._stop.is_set():
                return
            try:
                item = self._pending.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                return
            try:
                self._fetch_one(item)
            except Exception as exc:
                # A crash in one well's fetch must not kill the fetcher thread
                # — that left the remaining wells silently un-downloaded behind
                # a misleading "N/M downloaded" line (audit E12). Log it and
                # move on to the next item; the verify/retry step re-pulls the
                # gap.
                #
                # The cause goes in the run log, not just the Python logger: a
                # bare "error on 'A01'" is what made a storage fault look like a
                # transfer bug. ``volume_hint`` names the protocol behind the
                # errno when it recognises one.
                import logging as _logging
                _logging.getLogger(__name__).exception(
                    "fetcher: error downloading %r; continuing", item)
                hint = volume_hint(exc)
                self._log(
                    f"[remote] ↓ fetcher: error on {item!r}: {exc}"
                    + (f" — {hint}" if hint else "")
                    + "; continuing\n"
                )

    @contextlib.contextmanager
    def _transfer_heartbeat(self, label: str, dest_path: Path, interval: float = 8.0):
        """Emit a periodic "still transferring…" line showing bytes-so-far while a
        per-well rsync runs, so a long download is visibly *moving* rather than
        looking hung. The per-well fetch rsync is silent (capture_output), so
        without this the user has no way to tell a slow transfer from a stuck one.
        Bytes-so-far = the growing rsync temp/partial file(s) in the dest dir."""
        stop = threading.Event()
        t0 = time.monotonic()

        def _bytes_so_far() -> int:
            total = 0
            try:
                # rsync streams into a hidden temp ``.{name}.XXXXXX`` (or the
                # partial file) in the dest dir, renaming to dest on completion.
                for p in dest_path.parent.glob(f".{dest_path.name}.*"):
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
            except OSError:
                pass
            try:
                total += dest_path.stat().st_size
            except OSError:
                pass
            return total

        def _beat() -> None:
            while not stop.wait(interval):
                mb = _bytes_so_far() / (1024 * 1024)
                self._log(
                    f"[remote] ↓ {label}: still transferring… "
                    f"({_fmt_secs(time.monotonic() - t0)}, {mb:.1f} MB so far)\n"
                )

        th = threading.Thread(target=_beat, daemon=True)
        th.start()
        try:
            yield
        finally:
            stop.set()
            th.join(timeout=1.0)

    def _fetch_one(self, well: str) -> None:
        """Pull a single well's output. The remote pipeline produces
        ``{output_dir}/{well}_out.zip`` and a sibling
        ``{csv_prefix}_{well}.csv`` (see report §2).

        v5.2.18: emits visible progress lines so the user can see
        what's being downloaded. Pre-v5.2.18 the Fetcher worked
        silently — a long fetch looked like the GUI had hung.

        v5.2.28: short-circuit if the stop event fired between the
        ``_loop`` check and now (mid-rsync cancellation is handled
        deeper by ``_rsync_with_retry``).
        """
        if self._stop.is_set():
            return
        tmp_zip = _scratch_dir() / f"allwell-{self.run.run_id}-{well}_out.zip"
        zip_src = f"{self.host.ssh_alias}:{self.run.remote_output_dir}/{well}_out.zip"
        csv_glob_src = f"{self.host.ssh_alias}:{self.run.remote_output_dir}/*_{well}.csv"

        total = self.run.well_count_total or 0
        position = len(self.run.fetched_wells) + 1
        prefix = f"({position}/{total}) " if total else ""

        self._log(f"[remote] ↓ {prefix}{well}: starting download from {self.host.ssh_alias}\n")

        # Output zip — exists iff the pipeline finished this well. Pull it
        # even under CSV-only mode: we'll discard it after extracting the
        # files we want.
        t0 = time.monotonic()
        with self._transfer_heartbeat(well, tmp_zip):
            ok = self._rsync(
                # ``--timeout`` is rsync's own I/O-idle timeout: if the link
                # stalls (or rsync hangs after the data is across, waiting on a
                # half-closed connection) rsync aborts in 2 min instead of the
                # whole transfer blocking until the 5-min subprocess deadline —
                # which used to leave the final fetch sitting there for minutes.
                [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
                 "-a", "--partial", "--timeout=120", zip_src, to_rsync_path(tmp_zip)]
            )
        # rsync can report a non-zero exit (timeout / connection reset on close)
        # *after* the zip is fully across. Don't throw away a complete file and
        # re-fetch it forever — if the .zip is intact on disk, proceed.
        if not ok and not _zip_is_complete(tmp_zip):
            self._log(
                f"[remote] ↓ {well}: WARN could not pull {well}_out.zip from "
                f"{self.run.remote_output_dir}; will retry at end of run.\n"
            )
            try:
                tmp_zip.unlink()   # don't leave a partial in scratch (audit X9)
            except OSError:
                pass
            return
        if not ok:
            self._log(
                f"[remote] ↓ {well}: rsync returned an error but the .zip is "
                f"complete on disk — proceeding.\n"
            )

        zip_elapsed = time.monotonic() - t0
        zip_size = tmp_zip.stat().st_size if tmp_zip.exists() else 0
        mb = zip_size / (1024 * 1024)
        mbps = (mb * 8) / zip_elapsed if zip_elapsed > 0 else 0.0
        self._log(
            f"[remote] ↓ {well}: {mb:.1f} MB zip in "
            f"{_fmt_secs(zip_elapsed)} ({mbps:.0f} Mbps)\n"
        )

        # CSVs live at the top of the remote output_dir, not inside the
        # well zip — pull them separately. A failure here isn't fatal:
        # the per-well CSV is the most important file and any retry can
        # re-pull it.
        t1 = time.monotonic()
        if self.run.incremental:
            # Partial CSV (new FOV rows only) — merge into the local full CSV
            # rather than overwriting it.
            tmp_csv_dir = _new_scratch_dir(f"allwell-csv-{well}-")
            try:
                csv_listed, csv_count = self._rsync_glob(csv_glob_src, tmp_csv_dir)
                if csv_count:
                    self._merge_fetched_csvs(tmp_csv_dir)
            finally:
                shutil.rmtree(tmp_csv_dir, ignore_errors=True)
            # The temp dir is gone by now; name the volume it lived on, which is
            # what the user would have to look at.
            csv_dest = _scratch_dir()
        else:
            csv_listed, csv_count = self._rsync_glob(
                csv_glob_src, self.run.local_output_dir)
            csv_dest = self.run.local_output_dir
        csv_elapsed = time.monotonic() - t1
        if csv_count > 0:
            self._log(
                f"[remote] ↓ {well}: {csv_count} CSV file(s) in "
                f"{_fmt_secs(csv_elapsed)}\n"
            )
        elif csv_listed:
            # The remote HAS the CSVs; every pull of them failed. That is a
            # local-side fault (a full or unwritable destination, or a network
            # mount that dropped its session), and saying "no CSV files found"
            # here would point the user at the wrong machine entirely.
            self._log(
                f"[remote] ↓ {well}: WARN the remote has {csv_listed} CSV "
                f"file(s) but none could be written to {csv_dest} — check that "
                f"volume; the verify step will retry.\n"
            )
        else:
            self._log(
                f"[remote] ↓ {well}: WARN no CSV files found at "
                f"{self.run.remote_output_dir}/*_{well}.csv\n"
            )

        # Now materialize the well's output in the user's output_dir
        # according to local input mode.
        materialized = self._materialize(well, tmp_zip)
        try:
            tmp_zip.unlink()
        except OSError:
            pass
        if not materialized:
            # The local artifact isn't safely in place — do NOT mark the well
            # fetched or delete the remote copy (audit X2); leave it for the
            # end-of-run retry to re-pull.
            self._log(
                f"[remote] ↓ {well}: WARN local materialize failed; keeping the "
                f"remote copy and leaving the well for retry.\n"
            )
            return
        self.run.fetched_wells.add(well)
        total_elapsed = time.monotonic() - t0
        position_done = len(self.run.fetched_wells)
        tally = f"{position_done}/{total} wells" if total else f"{position_done} wells"
        self._log(
            f"[remote] ✓ {well}: materialised to {self.run.local_output_dir} "
            f"(total {_fmt_secs(total_elapsed)}, {tally} fetched)\n"
        )
        if self._progress and self.run.well_count_total > 0:
            self._progress(len(self.run.fetched_wells) / self.run.well_count_total)

        # v5.4.6 — per-well remote cleanup. With the data safely landed
        # locally, both the remote input zip and the remote output zip
        # (plus per-well CSVs) become redundant. Deleting them as each
        # well is fetched bounds peak remote disk to roughly
        # ``(uploaded-but-not-yet-processed inputs) + 1 well's output``
        # — independent of how many wells the plate has. Crucial on
        # constrained remotes where the full plate's outputs would
        # otherwise overflow before the run completes. Failures are
        # logged but non-fatal — an end-of-run ``rm -rf`` via
        # ``verify_and_cleanup`` is the backstop.
        if self._delete_after_download:
            self._delete_well_files_on_remote(well)

    def _delete_well_files_on_remote(self, well: str) -> None:
        """rm the remote input + output zips + per-well CSVs for
        ``well``. Fires after the local download is confirmed safe
        (Fetcher.fetched_wells.add). Non-fatal: ssh-delete failure is
        logged but doesn't abort the run — the end-of-run
        ``verify_and_cleanup`` is the backstop. On a disk-constrained
        remote this is the difference between the pipeline completing
        and ENOSPC mid-run.

        v5.4.7: pipe the script via stdin to ``bash -s`` instead of
        passing it as ``bash -c "..."`` arguments. ssh joins multi-
        arg remote commands with spaces, so a ``["bash", "-c",
        "rm -f a b c"]`` argv arrived at the remote bash as
        ``bash -c rm -f a b c`` — bash then took ``rm`` as the
        ``-c`` script and treated ``-f a b c`` as positional args
        to that script. The script ``rm`` (no args) hit ``rm: missing
        operand`` and the user saw the warning on every well. The
        stdin pipe avoids the shell-quoting round-trip entirely.
        """
        script = (
            f"rm -f -- "
            f"{self.run.remote_input_dir}/{well}.zip "
            f"{self.run.remote_output_dir}/{well}_out.zip "
            f"{self.run.remote_output_dir}/*_{well}.csv\n"
        )
        try:
            result = subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
                input=script.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                stderr = (
                    result.stderr.decode("utf-8", errors="replace")
                    if isinstance(result.stderr, (bytes, bytearray))
                    else (result.stderr or "")
                )
                self._log(
                    f"[remote] warn: per-well cleanup of {well} on remote "
                    f"returned {result.returncode}: {stderr.strip()}\n"
                )
        except (subprocess.TimeoutExpired, OSError) as exc:
            self._log(
                f"[remote] warn: per-well cleanup of {well} on remote "
                f"failed: {exc}\n"
            )

    def _merge_fetched_csvs(self, tmp_csv_dir: Path) -> None:
        """Merge each partial per-well CSV pulled into *tmp_csv_dir* into the
        matching full CSV under ``local_output_dir`` (incremental remote runs).
        New rows are appended, duplicates skipped — see
        ``pipeline.io.merge_records_into_csv``."""
        import csv as _csv

        from pipeline.io import merge_records_into_csv

        for partial in sorted(tmp_csv_dir.glob("*.csv")):
            try:
                with open(partial, newline="") as fh:
                    rows = list(_csv.DictReader(fh))
            except OSError as exc:
                self._log(f"[remote] warn: reading fetched CSV {partial.name} failed: {exc}\n")
                continue
            if not rows:
                continue
            added = merge_records_into_csv(self.run.local_output_dir / partial.name, rows)
            self._log(
                f"[remote] merge: {partial.name} → +{added} new row(s) "
                f"into {self.run.local_output_dir.name}/\n"
            )

    def _materialize(self, well: str, well_zip: Path) -> bool:
        """Place the well's outputs in ``local_output_dir`` so the result
        matches what a local run would have produced.

        Returns True iff the local artifact is now in place. The caller gates
        ``fetched_wells.add`` + remote deletion on this (audit X2): the copy
        goes to a temp path then ``os.replace`` (atomic), so an interrupted
        copy can't leave a truncated ``{well}_out.zip`` — and a failed copy
        returns False so the only complete (remote) copy is NOT deleted.
        """
        dest = self.run.local_output_dir
        dest.mkdir(parents=True, exist_ok=True)

        if self.output_filter is OutputFilter.CSV_ONLY:
            # Nothing to extract — the CSV was pulled separately. Drop
            # the well zip on the floor.
            return True

        if self.run.incremental:
            # The fetched zip holds only the new FOVs' images. Merge them into
            # the existing local <well>_out.zip instead of overwriting.
            return self._materialize_incremental(well, well_zip, dest)

        # Output is always a per-well .zip now (compression is mandatory), so
        # the fetched zip IS the expected local artifact for every input mode.
        # Drop it in place — previously FOLDER-mode inputs extracted it loose,
        # which left no <well>_out.zip and no <well>/ dir, so the verify step
        # failed and the post-run cleanup (delete local well folders) was
        # skipped.
        final = dest / f"{well}_out.zip"
        tmp = dest / f".{well}_out.zip.tmp"

        def _cleanup_tmp() -> None:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

        def _publish() -> None:
            shutil.copyfile(well_zip, tmp)
            os.replace(tmp, final)

        # This is the remote run's equivalent of the pipeline's ``<well>_out.zip``
        # write, and it lands on the same volume — the user's output dir — so it
        # carries the same retry shield. Unguarded, a single dropped SMB session
        # failed the well here with a bare errno even though the data was already
        # safely on disk in scratch.
        try:
            retry_io(
                _publish,
                what=f"{final.name} into {dest}",
                cleanup=_cleanup_tmp,
                advice=_TRANSFER_ADVICE,
                log_fn=self._log,
            )
        except OSError as exc:
            self._log(f"[remote] warn: copying {well}_out.zip failed: {exc}\n")
            return False
        return True

    def _materialize_incremental(self, well: str, well_zip: Path, dest: Path) -> bool:
        """Merge a partial fetched well zip into the existing local output.

        Returns True on a successful merge, False if the partial zip couldn't be
        extracted (so the caller won't mark the well fetched / delete the remote
        copy — audit X2)."""
        from pipeline.io import merge_images_into_zip

        tmp_dir = _new_scratch_dir(f"allwell-merge-{well}-")
        try:
            try:
                with zipfile.ZipFile(well_zip, "r") as zf:
                    zf.extractall(tmp_dir)
            except (OSError, zipfile.BadZipFile) as exc:
                self._log(f"[remote] warn: extracting partial {well}_out.zip failed: {exc}\n")
                return False
            n = merge_images_into_zip(dest / f"{well}_out.zip", tmp_dir)
            self._log(f"[remote] merge: {well}_out.zip → +{n} new image(s)\n")
            return True
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _rsync(self, argv: list[str]) -> bool:
        # Routed through the shared retry helper (v5.1.1) so transient
        # network glitches during the Fetcher's per-well pull don't
        # silently lose a well's output. Non-transient rsync errors
        # (permission, bad path) still fail immediately.
        #
        # v5.2.28: pass our own stop event so the Stop button can
        # actually interrupt the per-well rsync. Pre-v5.2.28 each
        # in-flight rsync had to run to completion before the
        # Fetcher noticed the stop_event between queue items —
        # which on a slow link looked like Stop did nothing.
        #
        # timeout=None: the per-well fetch argv carries rsync ``--timeout=120``
        # (I/O-idle) and the ssh transport sets ``ConnectTimeout=15``, so stalls
        # and dead connections are already bounded. The old fixed 5-min
        # subprocess wall-clock killed *healthy, still-progressing* transfers of
        # large final zips mid-flight, and --partial resume couldn't always
        # finish within the next window → endless retry/timeout/fail and a
        # forced rerun. Drop the wall-clock and let rsync's own idle timeout be
        # the stall guard.
        result = _rsync_with_retry(
            argv, log_fn=self._log, stop_event=self._stop, timeout=None,
        )
        return result.returncode == 0

    def _rsync_glob(self, src_glob: str, dest_dir: Path) -> tuple[int, int]:
        """Pull files matching a glob. Wraps the source in a quoted-glob
        rsync so the remote side expands ``*``, not the local shell.

        Returns ``(listed, pulled)`` — how many files the remote's glob matched,
        and how many of them landed locally. The caller needs both to tell the
        two failures apart: the remote produced no CSVs for this well, or it
        produced them and the *local* write failed. Reporting only the pulled
        count made a dropped session on a network-mounted output dir read as
        "no CSV files found" on the remote, which sent every diagnosis in the
        wrong direction.
        """
        # We invoke `ssh host 'ls -1 GLOB'` first; rsync's own glob
        # support requires the source to be quoted in a way that's awkward
        # to spell cross-shell.
        host, path_glob = src_glob.split(":", 1)
        # v5.2.5: the ls-listing ssh and the per-file rsync both go
        # through the runner's ssh policy (key, accept-new, batchmode).
        # v5.2.11: pipe the script via stdin so the remote bash parses
        # any ~ in path_glob normally — wrapping in shlex.quote here
        # would lose tilde expansion and the listing would silently
        # return nothing.
        # v5.x.x: input as bytes so Windows text=True doesn't translate
        # ``\n`` → ``\r\n`` and trip the remote bash with
        # ``$'\r': command not found``.
        listing = subprocess.run(
            self.host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
            input=f"ls -1 {path_glob} 2>/dev/null || true".encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
        listing_stdout = (
            listing.stdout.decode("utf-8", errors="replace")
            if listing.stdout else ""
        )
        files = [ln for ln in listing_stdout.splitlines() if ln.strip()]
        if not files:
            return 0, 0
        pulled = 0
        dest_str = to_rsync_path(dest_dir).rstrip("/") + "/"
        for f in files:
            if self._rsync(
                [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
                 "-a", f"{host}:{f}", dest_str]
            ):
                pulled += 1
        return len(files), pulled


# ────────────────────────────────────────────────────────────────────────────
# RemoteRunner — orchestrator
# ────────────────────────────────────────────────────────────────────────────


class RemoteRunner:
    """High-level entry point. Drives a single remote run end-to-end.

    Lifecycle::

        runner = RemoteRunner(host)
        zipped = stage_local_zips(input_dir, …)
        proc = runner.spawn(zipped, output_dir, args, output_filter=...)
        for line in proc.stdout:
            ...  # GUI consumes lines exactly like spawn_pipeline
        proc.wait()
        runner.verify_and_cleanup(proc.returncode == 0, cleanup_mode=...)
    """

    def __init__(self, host: RemoteHost):
        self.host = host
        # Which conda env the pipeline runs in, resolved once from the host's
        # "Remote python" setting. Both the argv builder and the shell composer
        # read it, so they can never disagree about which env is being used.
        self.env_selector, self._env_warning = _conda_env_selector(
            host.remote_python)
        self._run: _RemoteRun | None = None
        self._cache: DatasetCache = DatasetCache(host)
        # The Fetcher created in spawn(), kept so cancel() can interrupt an
        # in-flight per-well download, and a stop flag the end-of-run retry
        # backstop polls. Without these, Stop did nothing while the final
        # per-well rsync was in flight / retry-looping.
        self._fetcher: Fetcher | None = None
        self._cancelled: threading.Event = threading.Event()

    @property
    def run(self) -> _RemoteRun | None:
        return self._run

    # ── Required-file sync ──────────────────────────────────────────────

    def sync_required_files(
        self,
        *,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        """Push the minimal set of files the pipeline needs to the remote.

        Idempotent and cheap: rsync's mtime+size skip-pass means a no-op
        run is a single round-trip with no bytes transferred. Called
        automatically by :meth:`spawn` before every run so the user
        never accidentally runs against stale remote code.

        Files synced: see :data:`_REQUIRED_REMOTE_FILES`. The relative
        layout is preserved on the remote (so ``_Docs/requirements.txt``
        lands at ``{remote_project_dir}/_Docs/requirements.txt``).
        """
        log = log_fn or (lambda _m: None)
        root = repo_root()
        missing_local = [f for f in _REQUIRED_REMOTE_FILES if not (root / f).is_file()]
        if missing_local:
            raise FileNotFoundError(
                "Required pipeline files missing locally: "
                + ", ".join(missing_local)
                + f"  (looked under {root})"
            )

        # Make sure subdirectories exist on the remote — rsync with
        # individual file sources doesn't create intermediate dirs on
        # the target. One mkdir -p covers everything.
        remote_subdirs = sorted({
            str(Path(f).parent) for f in _REQUIRED_REMOTE_FILES
            if str(Path(f).parent) not in (".", "")
        })
        mk_target = f"{self.host.remote_project_dir}"
        mkdirs = [f"{mk_target}/{d}" for d in remote_subdirs]
        self._cache._ssh_run(
            ["mkdir", "-p", mk_target, *mkdirs],
            check=True,
        )

        # v5.2.12: use bare relative source paths with cwd=root so
        # rsync's -R preserves only the path BELOW the repo root.
        #
        # Previously we used the absolute-path-with-`/./`-pivot trick:
        #   sources = ["/Users/me/allwell/./process_microscopy.py", ...]
        # which is supposed to make rsync use everything after /./ as
        # the relative path. Older rsync (macOS bundles 2.6.9) doesn't
        # honor that pivot reliably and mirrors the WHOLE local path
        # on the remote, so the user saw:
        #   ~/allwell/Users/me/allwell/_Docs/requirements-remote.txt
        # instead of the expected:
        #   ~/allwell/_Docs/requirements-remote.txt
        #
        # Using cwd + bare relative sources works on every rsync
        # version we've seen. rsync's -R then preserves just the
        # relative path from cwd (`_Docs/requirements-remote.txt`)
        # rather than the absolute prefix.
        sources = list(_REQUIRED_REMOTE_FILES)
        dest = f"{self.host.ssh_alias}:{self.host.remote_project_dir}/"
        cmd = [
            resolve_rsync_binary(),
            *_rsync_ssh_transport(self.host),
            "-aR",
        ]
        # v5.2.10: --info=stats0 (suppresses summary footer) needs
        # rsync ≥ 3.1.0. Old rsync (macOS 2.6.9, older HPC images)
        # rejects with exit 1. Omitting it is purely cosmetic — rsync
        # just prints its stats summary to stdout, which we don't
        # parse or display anyway.
        if _rsync_supports_info_flag():
            cmd.append("--info=stats0")
        cmd += [*sources, dest]
        result = _rsync_with_retry(
            cmd, log_fn=log_fn, timeout=60, cwd=str(root),
        )
        if result.returncode != 0:
            err = (result.stderr or "").strip() or f"rsync exited {result.returncode}"
            raise RuntimeError(
                f"Failed to sync pipeline files to remote "
                f"({self.host.ssh_alias}): {err}"
            )
        log(
            f"[remote] code sync: {len(_REQUIRED_REMOTE_FILES)} file(s) "
            f"verified on {self.host.ssh_alias}:{self.host.remote_project_dir}\n"
        )

    # ── Estimation ──────────────────────────────────────────────────────

    def estimate(
        self,
        zipped: ZippedInput,
        *,
        is_cache_hit: bool = False,
        local_compute_s: float | None = None,
        remote_compute_speedup: float = 4.0,
    ) -> RunEstimate:
        """Rough break-even estimate. The compute-time numbers come from a
        tiny local cache of past runs — first run has no history, so we
        assume a 4× CPU speedup as a placeholder.
        """
        total_bytes = sum(p.stat().st_size for p in zipped.well_zips)
        bw_mbps = max(self.host.measured_bandwidth_mbps, 1.0)
        bps = bw_mbps * 1_000_000 / 8
        transfer_s = (total_bytes / bps) if not is_cache_hit else 0.0
        # Default local-compute fallback: very rough — ~5s per well zip
        # on a laptop. The Phase-3 timings.json refinement lands later.
        if local_compute_s is None:
            local_compute_s = max(60.0, len(zipped.well_zips) * 5.0)
        remote_compute_s = local_compute_s / max(remote_compute_speedup, 1.0)
        return RunEstimate(
            bytes_to_transfer=total_bytes,
            transfer_s=transfer_s,
            remote_compute_s=remote_compute_s,
            local_compute_s=local_compute_s,
        )

    # ── Spawn ───────────────────────────────────────────────────────────

    def spawn(
        self,
        zipped: ZippedInput,
        local_output_dir: Path,
        pipeline_argv: list[str],
        *,
        output_filter: OutputFilter,
        log_fn: Callable[[str], None] | None = None,
        progress_fn: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
        batch_size: int = 0,
    ):
        """Stage inputs, build the remote command, launch ssh.

        ``pipeline_argv`` is the result of
        :func:`services.pipeline_service.build_pipeline_args` — i.e. the
        same argv we'd pass locally. We rewrite the two path args and
        append the filter flags before launching remotely.

        ``batch_size`` controls remote-disk peak usage:

        * ``0`` (default) — upload every well-zip up front, run the
          pipeline once against the whole plate. Lowest total runtime
          (StarDist loads once, full parallelism inside the pipeline).
          Peak remote disk = ``(all inputs) + (all outputs accumulated
          before fetch)``.
        * ``> 0`` — split into batches of this many wells. Each batch:
          upload → spawn pipeline → wait → next batch. The Fetcher
          deletes each well's input + output zip on the remote as
          soon as the local download lands, so peak disk ≈
          ``batch_size`` wells of input + ~1 well of output. Tradeoff:
          StarDist reloads once per batch (~5–10 s overhead per
          batch). Pick ``batch_size`` based on your remote's free
          disk; the smaller it is, the more loads and the lower the
          peak.

        Returns a Popen-compatible handle the GUI's stdout reader can
        consume unchanged.
        """
        log = log_fn or (lambda _m: None)

        # Pipeline code sync runs for either path.
        self.sync_required_files(log_fn=log)

        if batch_size > 0:
            log(
                f"[remote] batched mode enabled: batch_size={batch_size}\n"
            )
            return _BatchedRemoteProcess(
                self,
                zipped,
                local_output_dir,
                pipeline_argv,
                batch_size,
                output_filter=output_filter,
                log_fn=log_fn,
                progress_fn=progress_fn,
                stop_event=stop_event,
            )

        # Single-shot path (the v5.4.5 and earlier behavior, byte-
        # identical apart from the moved sync_required_files() call).
        # Cache budget enforcement before each run.
        self._cache.gc(self.host.cache_budget_gb * (1024**3), log_fn=log)

        # ``sync_required_files`` ran above (covers both single-shot
        # and batched paths). Cheap — ~300 KB of source plus rsync's
        # delta-skip when nothing changed.

        # Upload.
        key, sent, elapsed = self._cache.ensure_uploaded(
            zipped, log_fn=log, progress_fn=progress_fn, stop_event=stop_event
        )

        # v5.2.25: deterministic run_id keyed on the input cache_key
        # + pipeline-args hash, so re-running the same input with the
        # same config lands in the same remote output dir. Combined
        # with ``cleanup_mode=none`` (the default), the pipeline's
        # own ``output_exists_for_well`` logic then skips wells whose
        # ``{well}_out.zip`` already exists from a prior run — making
        # re-runs cheap. The ``[run-]`` prefix marks them visually
        # different from the timestamp-style dirs older builds left
        # on the remote.
        run_id = "run-" + _compute_run_id(key, pipeline_argv)
        remote_input = self._cache.remote_dataset_dir(key)
        remote_output = f"{self.host.remote_staging_dir}/runs/{run_id}/out"
        remote_pidfile = f"{self.host.remote_staging_dir}/runs/{run_id}/run.pid"

        self._cache._ssh_run(  # short-circuit reuse of ssh helper
            ["mkdir", "-p", remote_output],
            check=True,
        )

        self._run = _RemoteRun(
            run_id=run_id,
            cache_key=key,
            remote_input_dir=remote_input,
            remote_output_dir=remote_output,
            remote_pidfile=remote_pidfile,
            local_output_dir=local_output_dir,
            local_input_mode=zipped.mode,
            output_filter=output_filter,
            incremental=zipped.incremental,
        )

        # v5.2.27: pre-flight cache-hit check. Before launching the
        # pipeline, ssh in and count the ``*_out.zip`` files already
        # present in the remote output dir. If every well in the
        # input is already represented there, the previous run
        # finished its compute phase — skip launching the pipeline
        # entirely and let the verify-backstop + retry loop pull
        # whatever the user is missing locally. This handles the
        # case the user reported in v5.2.26+: pipeline completed,
        # local fetch was interrupted (network drop or Stop button),
        # re-run should NOT re-analyze.
        #
        # The replacement remote command is ``true`` — a no-op shell
        # builtin that exits 0 immediately. Downstream flow
        # (PipelineRunner reading stdout to EOF, then
        # verify_and_cleanup → backstop → _retry_missing_wells) is
        # unchanged.
        expected_well_count = len(zipped.well_zips)
        cache_hit = False
        if expected_well_count > 0:
            # v5.2.28: surface the path + raw listing so when a user
            # expects a cache hit but doesn't get one, we can see
            # whether (a) the run_id drifted between runs (different
            # remote dir than the prior run wrote to), or (b) the
            # dir exists but is empty / missing the right files.
            log(
                f"[remote] pre-flight: checking {self.host.ssh_alias}:"
                f"{remote_output} for prior outputs\n"
            )
            completed_remote = self._list_remote_completed_wells()
            log(
                f"[remote] pre-flight: remote output dir has "
                f"{len(completed_remote)}/{expected_well_count} well "
                f"output zip(s) "
                f"(found wells: "
                f"{sorted(completed_remote)[:5]}"
                f"{'…' if len(completed_remote) > 5 else ''})\n"
            )
            if len(completed_remote) >= expected_well_count:
                cache_hit = True
                log(
                    "[remote] FULL CACHE HIT — every well has output "
                    "already; skipping pipeline launch and proceeding "
                    "straight to verify + fetch.\n"
                )

        if cache_hit:
            # Replace the pipeline with a no-op. The shell still runs
            # ``echo $$ > pidfile`` (so cancel() can find the PID if
            # the user clicks Stop during the trivial ssh), then exec
            # ``true``. The ``true`` exits 0 immediately, downstream
            # verify-and-cleanup runs the v5.2.25 backstop, and the
            # existing _retry_missing_wells re-pulls anything the
            # user is missing locally.
            remote_argv = ["true"]
        else:
            remote_argv = self._build_remote_argv(
                pipeline_argv,
                remote_input,
                remote_output,
                output_filter,
                zipped.mode,
            )

        # Compose the remote shell command. PID echoed first so the
        # cancel path can target the pipeline directly. v5.2.11: tilde-
        # aware quoting so the pidfile (under ~/.allwell-staging) and
        # the pipeline argv (with --output_dir ~/.allwell-staging/...)
        # don't end up creating a literal ~ directory on the remote.
        # v5.2.13: source conda.sh and exec via ``conda run`` so the
        # pipeline runs inside the ``allwell`` env regardless of where
        # the user's conda is installed.
        #
        # When the host has a ``compute_launcher`` (e.g. an ``srun …``
        # line), the pipeline is launched into a scheduler allocation so
        # it runs on a compute node the remote schedules rather than on
        # the login/"lobby" node we ssh into — see _compose_remote_cmd.
        # A cache hit short-circuits to a ``true`` no-op, which must NOT
        # consume a compute allocation, so the launcher is skipped there.
        remote_cmd = _compose_remote_cmd(
            self.host.compute_launcher,
            remote_pidfile,
            remote_argv,
            cache_hit=cache_hit,
            env_selector=self.env_selector,
        )
        ssh_argv = self.host.ssh_argv(allocate_tty=True) + [remote_cmd]

        # Name the environment before the run starts. When it turns out not to
        # exist, the guard's error names the same thing, so the two lines read
        # as one story instead of a raw conda traceback out of nowhere.
        kind, value = self.env_selector
        log(f"[remote] conda env: {kind} {value}\n")
        if self._env_warning:
            log(f"[remote] warn: {self._env_warning}\n")
        log(
            f"[remote] run_id={run_id}  cache_key={key[:8]}  "
            f"(upload {sent / (1024**2):.1f} MB in {_fmt_secs(elapsed)})\n"
            f"[remote] $ {_remote_join(remote_argv)}\n"
        )
        # Surface the scheduler hand-off so a queue wait reads as "waiting",
        # not a hang. The ``[remote] scheduler: allocation granted …`` line
        # (emitted from inside the srun step) follows once a node is granted.
        if self.host.compute_launcher.strip() and not cache_hit:
            log(
                f"[remote] scheduler: requesting allocation via: "
                f"{self.host.compute_launcher.strip()}\n"
                f"[remote] scheduler: waiting for the scheduler to grant a "
                f"node (this can queue)…\n"
            )

        popen = subprocess.Popen(
            ssh_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # Decode the remote (Linux, UTF-8) pipeline output as UTF-8 so the
            # em-dash in per-well markers ("Well X — done.") isn't mojibake'd on
            # a cp1252 Windows client — otherwise the dashboard's Process stage
            # stays stuck at 0/N while ASCII-only markers (threshold) reach N/N.
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            start_new_session=(os.name == "posix"),
        )

        fetcher = Fetcher(
            self.host,
            self._run,
            output_filter=output_filter,
            log_fn=log,
            progress_fn=progress_fn,
        )
        # Keep a handle so cancel() can stop an in-flight per-well download.
        self._fetcher = fetcher
        self._cancelled.clear()
        return _RemoteProcess(popen, self._run, fetcher)

    # ── Verify + cleanup ────────────────────────────────────────────────

    def verify_and_cleanup(
        self,
        ok: bool,
        *,
        cleanup_mode: str = "none",
        dry_run: bool = False,
        log_fn: Callable[[str], None] | None = None,
    ) -> bool:
        """Pull top-level artefacts, then verify and clean up.

        Returns True iff the local manifest checks pass. The remote
        cleanup is only fired on success — a failed manifest leaves the
        remote state in place for retry.

        v5.2.23: when ``dry_run`` is True, the ``rm -rf`` paths are
        replaced with an ssh probe that surfaces the path, file count,
        and total bytes that WOULD be removed. The remote files are
        left in place. Useful for shared machines where the user
        wants to confirm what gets deleted before turning auto-delete
        on for real.
        """
        log = log_fn or (lambda _m: None)
        if self._run is None:
            return False
        run = self._run

        # Pull pipeline_info.json + any top-level CSVs we may have missed
        # (well CSVs are pulled by the Fetcher, but a top-level summary
        # written at end-of-run wouldn't be).
        for top in ("pipeline_info.json",):
            self._rsync_optional(
                f"{self.host.ssh_alias}:{run.remote_output_dir}/{top}",
                run.local_output_dir / top,
                log_fn=log,
            )

        # The fetched pipeline_info.json reflects only the (partial) input the
        # remote saw — for an incremental run that's just the uploaded new-FOV
        # members, so its available_timepoints/available_fovs miss the rest of
        # the dataset and any dropdown built from them goes stale. Recompute both
        # from the *merged* local outputs (the <well>_out.zip members keep the
        # input stem), which hold the complete picture after the fetch.
        refresh_pipeline_info_available_from_outputs(run.local_output_dir, log_fn=log)

        if not ok:
            log("[remote] pipeline returned non-zero — skipping cleanup, leaving remote state.\n")
            return False

        # Verify each expected well's output is present locally.
        # v5.1.1: before declaring the manifest incomplete, give the
        # Fetcher one more pass at any well whose CSV didn't make it
        # back. A transient connection blip during a per-well rsync no
        # longer requires a full pipeline re-run — we re-pull the
        # missing files using the same retry-aware helpers.
        missing = self._verify_local_manifest(log=log)
        if missing:
            self._retry_missing_wells(missing, log=log)
            missing = self._verify_local_manifest(log=log)
        if missing:
            log(
                f"[remote] keeping remote files for retry — verification failed: "
                f"{len(missing)} file(s) missing.\n"
            )
            return False

        # Cleanup according to the user's flag. Fires only after verify
        # passes — there's no path that can lose remote state if a fetch
        # was incomplete.
        if cleanup_mode == "none":
            log("[remote] --cleanup-remote=none — leaving everything in place.\n")
            return True

        run_dir = f"{self.host.remote_staging_dir}/runs/{run.run_id}"
        if dry_run:
            self._log_would_delete(run_dir, label=f"run dir {run.run_id}", log=log)
        else:
            self._cache._ssh_run(
                ["rm", "-rf", run_dir],
                check=False,
            )
            log(f"[remote] removed run dir {run.run_id}.\n")
        if cleanup_mode == "all":
            cache_dir = self._cache.remote_dataset_dir(run.cache_key)
            if dry_run:
                self._log_would_delete(
                    cache_dir,
                    label=f"dataset cache {run.cache_key[:8]}",
                    log=log,
                )
            else:
                self._cache._ssh_run(
                    ["rm", "-rf", cache_dir],
                    check=False,
                )
                log(f"[remote] removed dataset cache {run.cache_key[:8]}.\n")
        if dry_run:
            log(
                "[remote] [DRY-RUN] no files were actually deleted. "
                "Re-run without --dry-run-cleanup to commit.\n"
            )
        return True

    def _log_would_delete(
        self, remote_path: str, *, label: str, log: Callable[[str], None]
    ) -> None:
        """v5.2.23 dry-run probe: ssh in, count files + bytes at
        ``remote_path``, log the result without deleting anything.

        Uses ``_ssh_script`` (stdin-piped to remote bash) so the
        ``~/.allwell-staging`` default expands correctly on the
        remote — the v5.2.16 lesson, generalised."""
        script = (
            f"if [ ! -e {remote_path} ]; then echo MISSING; exit 0; fi; "
            f"_b=$(du -sb {remote_path} 2>/dev/null | awk '{{print $1}}'); "
            f"_f=$(find {remote_path} -type f 2>/dev/null | wc -l | tr -d ' '); "
            f"echo BYTES $_b; "
            f"echo FILES $_f"
        )
        result = self._cache._ssh_script(script, check=False)
        out = (result.stdout if result is not None else "") or ""
        if "MISSING" in out:
            log(
                f"[remote] [DRY-RUN] would delete {label} at {remote_path} "
                f"— but path doesn't exist on remote (already cleaned up?).\n"
            )
            return
        bytes_match = re.search(r"BYTES\s+(\d+)", out)
        files_match = re.search(r"FILES\s+(\d+)", out)
        bytes_n = int(bytes_match.group(1)) if bytes_match else 0
        files_n = int(files_match.group(1)) if files_match else 0
        mb = bytes_n / (1024 * 1024)
        log(
            f"[remote] [DRY-RUN] would delete {label} at {remote_path} "
            f"({files_n} file(s), {mb:.1f} MB)\n"
        )

    def _rsync_optional(
        self, src: str, dest: Path, *, log_fn: Callable[[str], None]
    ) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        result = _rsync_with_retry(
            [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
             "-a", src, to_rsync_path(dest)],
            log_fn=log_fn, timeout=60,
        )
        if result.returncode != 0:
            log_fn(
                f"[remote] note: {src} not present on remote "
                f"(rc={result.returncode}).\n"
            )

    def _retry_missing_wells(
        self, missing_messages: list[str], *, log: Callable[[str], None],
    ) -> None:
        """Re-pull the per-well outputs for wells whose CSV is missing.

        Closes the "transient drop ate one well's output" hole that
        existed in v5.0.0–v5.1.0: the in-stream Fetcher already gave up
        on a failed pull with a "will retry at end" comment that wasn't
        actually implemented. This is that retry.
        """
        run = self._run
        if run is None:
            return
        # Extract well IDs from the manifest messages. _verify_local_manifest
        # emits several forms: "missing CSV for A01", "empty CSV for A01",
        # "missing A01_out.zip locally", "missing A01/ output folder locally".
        # A trailing \b fails on the zip form ("A01_out" — '_' is a word char,
        # so no boundary after A01), which silently dropped zip/folder-only
        # misses from the retry. Anchor on a leading boundary and forbid only a
        # trailing digit, so all four forms parse without over-matching A012…
        well_re = re.compile(r"\b([A-H]\d{2})(?![0-9])")
        wells: set[str] = set()
        for m in missing_messages:
            match = well_re.search(m)
            if match:
                wells.add(match.group(1))
        if not wells:
            return

        log(f"[remote] end-of-run retry: re-pulling {len(wells)} well(s)…\n")
        host = self.host
        # The Fetcher's per-well rsync got the v6.0.1 wall-clock fix; this
        # backstop path didn't, and was the remaining source of the
        # "7/7 fetched, then endless rsync retry" hang: it re-pulled with the
        # default 5-min subprocess wall-clock (which kills a large, still-
        # progressing {well}_out.zip mid-transfer → --partial resume → killed
        # again → loop) and with no stop event (so Stop couldn't break it).
        # rsync --timeout=120 (I/O-idle) + ssh ConnectTimeout bound real stalls,
        # so timeout=None is safe; stop_event makes Stop interrupt an in-flight
        # re-pull.
        # FOLDER must be included: _verify_local_manifest requires the
        # {well}_out.zip for *any* FULL run (including FOLDER input), so the
        # retry re-pull has to be able to fetch it too — otherwise a
        # FOLDER+FULL run whose out-zip failed to land loops verify→retry
        # forever and remote cleanup never happens (audit G2).
        want_zip = (
            run.output_filter is OutputFilter.FULL
            and run.local_input_mode in (
                LocalInputMode.ZIP, LocalInputMode.FLAT,
                LocalInputMode.MIXED, LocalInputMode.FOLDER)
        )

        def _pull(remote_src: str) -> None:
            _rsync_with_retry(
                [resolve_rsync_binary(), *_rsync_ssh_transport(host),
                 "-a", "--partial", "--timeout=120",
                 f"{host.ssh_alias}:{remote_src}",
                 to_rsync_path(run.local_output_dir).rstrip("/") + "/"],
                log_fn=log, timeout=None, stop_event=self._cancelled,
            )

        for well in sorted(wells):
            if self._cancelled.is_set():
                log("[remote] end-of-run retry cancelled by Stop.\n")
                return
            # Re-pull the per-well CSV(s) only if they're actually missing or
            # empty locally — a present CSV needs no refetch.
            local_csvs = list(run.local_output_dir.glob(f"*_{well}.csv"))
            csv_ok = bool(local_csvs) and all(p.stat().st_size > 0 for p in local_csvs)
            if not csv_ok:
                try:
                    listing = subprocess.run(
                        host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
                        # v5.x.x: bytes input — see _ssh_script for the
                        # Windows CRLF rationale.
                        input=f"ls -1 {run.remote_output_dir}/*_{well}.csv 2>/dev/null || true".encode("utf-8"),
                        capture_output=True, timeout=30,
                    )
                except (subprocess.TimeoutExpired, OSError) as exc:
                    log(f"[remote] retry: listing {well} failed: {exc}\n")
                    continue
                files = [
                    ln for ln in (
                        listing.stdout.decode("utf-8", errors="replace")
                        if listing.stdout else ""
                    ).splitlines() if ln.strip()
                ]
                for f in files:
                    _pull(f)
            # Re-pull {well}_out.zip ONLY when the user's output mode wants it
            # AND it isn't already present locally. Pulling a multi-hundred-MB
            # zip just because a CSV was missing is what dragged this backstop
            # into the wall-clock retry loop in the first place.
            if want_zip:
                out_zip = run.local_output_dir / f"{well}_out.zip"
                if not out_zip.exists() or out_zip.stat().st_size == 0:
                    _pull(f"{run.remote_output_dir}/{well}_out.zip")

    def _verify_local_manifest(
        self, *, log: Callable[[str], None]
    ) -> list[str]:
        """Check that every well with output on the remote has its
        full local artifact set.

        Returns the list of missing paths (empty list = all good).

        v5.2.25: also discovers wells from the remote output dir's
        ``*_out.zip`` listing — not just the ``Well X — done.`` stdout
        markers. This catches the cache-hit case where the pipeline
        skipped already-complete wells (no ``done`` marker emitted)
        but their outputs still need to land locally.

        v5.2.29: verify the OUT_ZIP / extracted folder too, not just
        the CSV. Pre-v5.2.29 a resumed run that had pulled the CSV
        but failed mid-zip-rsync would silently leave the well
        flagged "complete" — the user would have the measurements
        but never the QC zip the GUI's Review tab needs.

        The expected local artifacts depend on the user's output
        filter and input mode:
        - Always: ``{csv_prefix}_{well}.csv``
        - FULL filter + ZIP/FLAT mode: also ``{well}_out.zip``
        - FULL filter + FOLDER mode: also ``{well}/`` directory
        - CSV_ONLY filter: just the CSV (zip explicitly discarded)
        """
        run = self._run
        assert run is not None
        all_wells = set(run.expected_wells)
        # Backstop: ask the remote what wells it has ``{well}_out.zip``
        # for. Pipeline-skipped wells aren't in expected_wells (no
        # stdout marker fires for them) but their outputs are still
        # what the user wants locally.
        for w in self._list_remote_completed_wells():
            all_wells.add(w)
        missing: list[str] = []
        # Output is always materialised as <well>_out.zip now (compression is
        # mandatory and the Fetcher keeps the zip for every input mode), so the
        # FULL filter expects that zip regardless of input mode. (Pre-always-zip
        # this branched on FOLDER mode and expected a <well>/ dir, which no
        # longer exists — that mismatch failed verify and skipped cleanup.)
        want_zip_artifact = run.output_filter is OutputFilter.FULL
        for well in sorted(all_wells):
            csvs = list(run.local_output_dir.glob(f"*_{well}.csv"))
            if not csvs:
                missing.append(f"missing CSV for {well}")
                continue
            if any(p.stat().st_size == 0 for p in csvs):
                missing.append(f"empty CSV for {well}")
                continue
            # v5.2.29: also verify the QC artifact for the user's
            # output mode. A previous run could have pulled the CSV
            # but failed mid-zip-rsync; without this check the well
            # is silently flagged complete and the zip stays remote.
            if want_zip_artifact:
                out_zip = run.local_output_dir / f"{well}_out.zip"
                if not out_zip.exists() or out_zip.stat().st_size == 0:
                    missing.append(f"missing {well}_out.zip locally")
        if missing:
            for m in missing[:10]:
                log(f"[remote] verify: {m}\n")
            if len(missing) > 10:
                log(f"[remote] verify: … and {len(missing) - 10} more\n")
        else:
            log(
                f"[remote] verify: all {len(all_wells)} wells "
                f"present locally.\n"
            )
        return missing

    def _list_remote_completed_wells(self) -> set[str]:
        """ssh into the remote and list every ``{well}_out.zip`` in
        the run's output dir; return the set of well labels. Used by
        ``_verify_local_manifest`` to discover wells the pipeline
        skipped (no ``Well X — done.`` marker fires when the
        ``output_exists_for_well`` early-exit kicks in).

        Uses ``_ssh_script`` so ``~/.allwell-staging`` expands
        properly on the remote (the v5.2.16 lesson). Any error
        (ssh missing, network drop, transient timeout) degrades
        gracefully to an empty set — verify then falls back to the
        ``expected_wells``-only check just like pre-v5.2.25."""
        run = self._run
        if run is None:
            return set()
        try:
            listing = self._cache._ssh_script(
                f"ls -1 {run.remote_output_dir}/*_out.zip 2>/dev/null || true",
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return set()
        wells: set[str] = set()
        if listing is None or not listing.stdout:
            return wells
        for line in listing.stdout.splitlines():
            stem = Path(line.strip()).name.removesuffix(".zip")
            if stem.endswith("_out"):
                well = stem[:-len("_out")]
                label = _canonical_well_from_path(well)
                if label is not None:
                    wells.add(label)
        return wells

    # ── Cancel ──────────────────────────────────────────────────────────

    def close_remote_job(
        self, *, log_fn: Callable[[str], None] | None = None
    ) -> None:
        """Guaranteed-teardown backstop: ensure the scheduler job this run
        launched is closed.

        Idempotent and best-effort — a no-op on direct-execution hosts (no
        ``compute_launcher``), when no run has started, or when the job has
        already exited (``scancel`` on a finished id is harmless). Reads the
        ``$SLURM_JOB_ID`` the run wrote to ``<pidfile>.slurm`` on the shared
        filesystem and ``scancel``s it via a fresh ssh.

        ``srun`` already exits when the pipeline finishes *or* fails, so in
        the common case the job is gone before we get here and this does
        nothing. Its job is to make sure an *orphaned* allocation — ssh /
        srun client died, connection dropped, AllWell crashed mid-run —
        never lingers in the queue waiting on the user. Called from the
        PipelineRunner's ``finally`` (covers complete / fail / exception)
        and from :meth:`cancel` (covers user Stop)."""
        if not self.host.compute_launcher.strip():
            return
        run = self._run
        if run is None:
            return
        log = log_fn or (lambda _m: None)
        slurm_sidecar_q = _remote_quote(f"{run.remote_pidfile}.slurm")
        cmd = (
            f"_jid=$(cat {slurm_sidecar_q} 2>/dev/null); "
            '[ -n "$_jid" ] && command -v scancel >/dev/null 2>&1 '
            '&& scancel "$_jid" 2>/dev/null && echo "AW_SCANCELLED $_jid" || true'
        )
        try:
            result = subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + [cmd],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            # A failed teardown ssh must not be silent — an orphaned SLURM
            # allocation could keep running (and billing) unnoticed (audit E13).
            log(f"[remote] WARN scheduler: could not confirm scancel of the "
                f"remote job ({exc}); a SLURM allocation may still be running.\n")
            return
        out = (result.stdout or "")
        if "AW_SCANCELLED" in out:
            jid = out.split("AW_SCANCELLED", 1)[1].strip().split()[0:1]
            log(
                f"[remote] scheduler: cancelled lingering job "
                f"{jid[0] if jid else ''}.\n"
            )
        else:
            # The common case: ``srun`` already exited with the pipeline, so
            # there was no allocation left to cancel. Confirm the clean
            # teardown so the log has a definite end-of-job marker rather
            # than silence.
            log("[remote] scheduler: job already finished — nothing to cancel.\n")

    def cancel(self, ssh_proc: subprocess.Popen) -> None:
        """Best-effort remote kill. The local pgid kill (handled by
        PipelineRunner) is the primary mechanism; this is the belt-and-
        braces second channel via a fresh ssh that ``kill``s the PID we
        wrote on startup.

        On a scheduler-backed host this also calls :meth:`close_remote_job`
        to ``scancel`` the allocation — killing the login-side ``srun``
        client usually tears it down, but a queued/odd state can leave the
        job alive.

        Also interrupts the local download side: the per-well Fetcher runs
        rsync on a background thread in THIS process (separate from the ssh
        process group), and the end-of-run retry backstop polls ``_cancelled``.
        Without these, clicking Stop while the final per-well rsync was in
        flight / retry-looping did nothing — the GUI only ever tore down the
        ssh process, never the download that was actually blocking."""
        self._cancelled.set()
        if self._fetcher is not None:
            try:
                self._fetcher.cancel()
            except Exception:  # best-effort
                swallowed()  # was: pass (silent except)
        if self._run is None:
            return
        run = self._run
        pidfile_q = _remote_quote(run.remote_pidfile)
        try:
            subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + [
                    f"kill -TERM $(cat {pidfile_q} 2>/dev/null) 2>/dev/null || true"
                ],
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            # Best-effort (the local process-group kill is primary), but don't
            # swallow it silently — record that this teardown channel failed
            # (audit E13); close_remote_job() below is the scheduler backstop.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "remote cancel: ssh kill failed (%s); relying on the local "
                "process-group kill + scancel backstop", exc)
        # Belt-and-braces: also cancel the scheduler job (no-op on
        # direct-execution hosts / when no job id was recorded).
        self.close_remote_job()

    # ── Argv building ───────────────────────────────────────────────────

    def _build_remote_argv(
        self,
        local_argv: list[str],
        remote_input: str,
        remote_output: str,
        output_filter: OutputFilter,
        mode: LocalInputMode,
    ) -> list[str]:
        """Rewrite the local pipeline argv for remote execution.

        Replaces ``--input_dir`` / ``--output_dir`` with the remote-side
        paths, swaps the local python path for ``conda run`` inside the
        ``allwell`` env, and appends the filter flags.
        """
        # The local argv is shaped like:
        #   [sys.executable, /path/to/process_microscopy.py, --input_dir, X, ...]
        # We replace the interpreter and script path. v5.2.13: instead
        # of pinning ``self.host.remote_python`` (which broke on hosts
        # whose conda lives outside ``~/miniforge3``), we wrap with
        # ``conda run --no-capture-output -n allwell python`` — the
        # surrounding shell prefix in ``spawn`` sources conda.sh first.
        remote_script = f"{self.host.remote_project_dir}/process_microscopy.py"
        out = [*_conda_run_prefix_for(self.env_selector), "python", remote_script]
        i = 2  # skip the interpreter + script slots
        while i < len(local_argv):
            arg = local_argv[i]
            if arg == "--input_dir":
                out.extend([arg, remote_input])
                i += 2
            elif arg == "--output_dir":
                out.extend([arg, remote_output])
                # Pin the remote scratch to the remote output dir. The pipeline's
                # default moved to ``~/.allwell/scratch`` to keep a local run's
                # bulk I/O off a network-mounted output volume; on a cluster that
                # default would be wrong — the login/compute home is typically a
                # small-quota NFS share, while ``remote_output`` already lives in
                # the host's configured staging area. Passing it explicitly keeps
                # remote runs byte-identical to their previous behaviour.
                out.append(f"--scratch_dir={remote_output}")
                i += 2
            elif arg.startswith("--scratch_dir"):
                # Never forward a local scratch path to the remote host.
                if arg == "--scratch_dir":
                    i += 2
                else:
                    i += 1
            elif arg == "--incremental":
                # Incremental is a client-side concern on remote runs: the
                # client uploads only the new-FOV files and merges the partial
                # outputs back on fetch. The remote just processes what it
                # received, so the flag (and its prior-dir) are dropped here.
                i += 1
            elif arg == "--incremental_prior_dir":
                i += 2
            else:
                out.append(arg)
                i += 1
        # Apply the output filter.
        if output_filter is OutputFilter.CSV_ONLY and "--no_save_tophat" not in out:
            out.append("--no_save_tophat")
        # Host-level override args.
        out.extend(self.host.extra_args)
        if self.host.cpu_only and "--cpu_only" not in out:
            out.append("--cpu_only")
        return out
