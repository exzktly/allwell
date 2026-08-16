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
    launcher_time_seconds,
    scale_launcher_time,
    resolve_rsync_binary,
    resolve_ssh_for_rsync,
    to_rsync_path,
    update_bandwidth,
    REWRITE_MIN_SAVING,
)
# Stdlib-only (fov, tp) done-key helpers — shared with pipeline.io (audit A2).
# This module import must stay imaging-stack-free; incremental_keys is csv/
# zipfile/pathlib only, so it never drags numpy/tifffile into the remote client.
from util.channel_roles import LABELFREE_METHODS, quantified_channel_tokens
from util.fov import sort_key as fov_sort_key
from util.image_exts import GROUP_IMAGE_EXTS
from util.timepoint import sort_key as tp_sort_key
from pipeline.run_report import (
    merge_fetched_run_report,
    read_run_report_block,
)
from pipeline.sidecar import read_user_blocks, restore_user_blocks
from pipeline.well_skip import _canonical_force_wells
from util.well_token import canonical_well_label as _canonical_well_label
from util.well_token import (
    background_well_key as _bg_well_key,
    split_background_wells,
)
from pipeline.incremental_keys import (
    fov_tp_from_name,
    read_done_fov_keys,
    read_done_fov_keys_from_zip,
)
from util.aw_safe import swallow, swallowed
# The shared "retry an OSError with a backoff, then fail with an actionable
# message" shield. A remote run does as much I/O on the user's own volumes as a
# local one — it reads the whole plate off the input volume to stage the upload
# and writes every fetched well back onto the output volume — so it needs the
# same protection the pipeline's result writes have. ``pipeline.io`` owns the
# pipeline-side wrapper; this module cannot import it (that would drag numpy /
# tifffile into the remote client), which is why the loop lives in ``util``.
from util.retry_io import retry_io, volume_hint

# Lines this module marks as *live state* rather than events: a transfer's
# bytes-so-far, rsync's own percentage stream. ``pipeline_runner`` routes them
# to the dashboard's status line and never writes them to the log.
#
# A log records things that happened, and "still transferring" is a sample of a
# continuous variable. One line per sample also evicted real history: the log
# holds 10 000 blocks, and ``analyze_tab._log_line`` carries a block of
# scroll-compensation whose own comment blames this stream for making that
# trimming common.
#
# Marked at the source with a prefix rather than sniffed by shape downstream —
# the per-file ``[remote] ↑ <well>.zip`` names look similar and are real events
# (they drive the Upload rail), so guessing between them would be fragile. It
# lives here, with the only code that emits it, because ``pipeline_runner``
# already imports this module and the reverse would be a cycle.
TRANSIENT_PREFIX = "[remote] ⋯ "

# Per-well transfer progress, as ``<prefix><direction> <well> <fraction>`` —
# e.g. ``[remote] ⋯ xfer down C02 0.4180``. ``pipeline_runner.ProgressTracker``
# turns these into ``("well_xfer", (well, direction, fraction))`` events that
# fill the well's dial on the run dashboard's plate map.
#
# Deliberately built on ``TRANSIENT_PREFIX`` rather than beside it: a per-well
# transfer emits a sample every second or so, which is the same flood the
# transient marker exists to keep out of the log, and extending the existing
# prefix means the "never write this to the log" rule already covers it. The
# tracker recognises the longer marker first, so these never reach the status
# line as raw text either.
WELL_XFER_PREFIX = TRANSIENT_PREFIX + "xfer "

# How often a transfer writes a durable "still going" line to the log, as
# opposed to the live status updates it emits every ``interval`` seconds.
_BREADCRUMB_INTERVAL_S = 60.0

# How often a per-well transfer emits its dial fraction. Much faster than the
# status-line cadence — a dial that only moves every eight seconds reads as
# stuck — and cheap, since the emission is one formatted string that never
# touches the log.
_WELL_XFER_INTERVAL_S = 1.0
from util.zip_write import atomic_zip, replace_folder_with_archive
from util.well_token import is_valid_well_name
def _well_zips_in(input_dir: Path):
    """Every ``<WELL>.zip`` directly under *input_dir*.

    A filesystem glob cannot judge a well. ``[A-H][0-9]*.zip`` — what this
    replaced — accepted ``A13.zip`` and ``A1_out.zip`` while rejecting a
    lower-case ``a1.zip``, which is the opposite of what the folder-side scan a
    few lines away decides with ``is_valid_well_name``. Two rules for "is this a
    well" inside one function is the §15 defect in miniature: staging would take
    ``a1/`` but not ``a1.zip``, and ``A13.zip`` but not ``A13/``.
    """
    return (p for p in input_dir.glob("*.zip") if is_valid_well_name(p.stem))




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


# Working directory for bytes in transit, created on the volume they are coming
# from or going to. Dot-prefixed so every dataset scan in the app skips it —
# `image_discovery`, `auto_threshold` and `zip_members` all drop a name starting
# with a dot — and removed as soon as the file it held is in place.
TRANSIT_SUBDIR = ".allwell-transit"

# Volumes that have already refused a transit directory this process. Without
# it, a fallback on a read-only output folder would repeat its warning once per
# well for the whole plate.
_transit_refused: set[str] = set()


def _transit_dir(near: Path | str,
                 *, log_fn: Callable[[str], None] | None = None) -> Path:
    """A working directory beside *near*, for a file on its way to or from it.

    A remote run's compute happens on the cluster; locally it only sends inputs
    and receives results. It has no reason to touch the home volume, and it used
    to touch it for every well: each downloaded ``<well>_out.zip`` was written to
    ``~/.allwell/scratch`` and then materialised into the output directory, so
    every byte of a plate's results crossed two volumes, and a full home volume
    stopped a run whose own input and output volumes had room to spare. That is
    the ``ENOSPC`` users hit.

    **Why this does not re-create the problem ``pipeline.io.scratch_base_for``
    warns about.** That docstring is right that putting churn on a
    network-mounted volume is what caused the macOS SMB "Bad file descriptor"
    well failures — but it is describing the *pipeline's* working set: roughly
    six float32 intermediates per FOV-group, each written tmp → fsync → rename,
    from every worker process at once. This is one file per well, written once
    and moved once. And the destination volume already receives exactly these
    bytes today, by copy from the home volume; the number of writes it sees does
    not change. What changes is that the home volume stops seeing them, and the
    move into place becomes a same-volume rename instead of a cross-device copy.

    Falls back to :func:`_scratch_dir` when *near* will not take a directory (a
    read-only or quota'd volume) — and says so, once per volume. A run that
    silently reverted to the volume the user is trying to get off would be the
    original failure with the explanation removed.
    """
    base = Path(near)
    path = base / TRANSIT_SUBDIR
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError as exc:
        key = str(base)
        if key not in _transit_refused:
            _transit_refused.add(key)
            if log_fn is not None:
                log_fn(f"[remote] WARN cannot use {path} for files in transit "
                       f"({exc}); falling back to {_scratch_dir()}. That volume "
                       f"has to hold a well's output on its way through, so a "
                       f"large plate can fill it.\n")
        return _scratch_dir()


def _new_transit_dir(near: Path | str, prefix: str,
                     *, log_fn: Callable[[str], None] | None = None) -> Path:
    """``mkdtemp`` rooted at :func:`_transit_dir`."""
    return Path(tempfile.mkdtemp(
        prefix=prefix, dir=str(_transit_dir(near, log_fn=log_fn))))


def sweep_stale_scratch(*, max_age_h: float = 6.0,
                        also: Iterable[Path | str] = (),
                        log_fn: Callable[[str], None] | None = None) -> int:
    """Best-effort removal of leftover ``allwell-*`` scratch from runs killed
    before their cleanup ran (a hard quit skips the ``finally`` that deletes the
    staging dir, leaking a full input copy onto the scratch volume). Only
    removes entries older than *max_age_h* so a concurrent run's live staging is
    never touched. Returns the number removed.

    *also* names further bases to sweep — the transit directories beside the
    run's own input and output volumes. Without them the leak does not go away
    when transit moves off the scratch volume, it just moves with it.
    """
    cutoff = time.time() - max_age_h * 3600.0
    removed = 0
    seen: set[Path] = set()
    for base in [_scratch_dir(), *[Path(p) / TRANSIT_SUBDIR for p in also]]:
        if base in seen:
            continue
        seen.add(base)
        try:
            entries = list(base.glob("allwell-*"))
        except OSError:
            continue
        here = 0
        for p in entries:
            try:
                if p.stat().st_mtime > cutoff:
                    continue
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink()
                here += 1
            except OSError:
                continue
        if here and log_fn:
            log_fn(f"[remote] cleaned {here} stale scratch item(s) from {base}\n")
        removed += here
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
# One or two digits: the pipeline logs whatever label the input carried, so a
# plate zipped as ``C8.zip`` announces "Well C8 — done." A two-digit-only
# pattern never matched that, the well never entered ``expected_wells``, and
# it was therefore never fetched — the same class of mismatch
# ``_canonical_force_wells`` exists to prevent on the other side of this
# comparison. The label is canonicalised at the match site.
_RE_WELL_DONE = re.compile(r"(?:\bWell\s+)?(?P<well>[A-Ha-h]\d{1,2})\s+\S+\s+done\.")
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
    # Free-space primitives + the single wording for a volume that filled. The
    # retry shield below rewrites an ENOSPC through it, so a remote run that
    # runs its output volume dry says which volume and how much is left.
    "util/disk_space.py",
    # "Would the pipeline group this file as input?" — one set, read by
    # process_microscopy's scanners and by this module's upload planner.
    "util/image_exts.py",
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
    "pipeline/capacity.py",
    "pipeline/cellpose.py",
    # The one definition of "is this well complete", shared by the end-of-run
    # check and the Datasets Validate tool.
    "pipeline/completeness.py",
    "pipeline/flatfield.py",
    "pipeline/incremental_keys.py",
    "pipeline/well_skip.py",
    "pipeline/io.py",
    # Packed OME-TIFF de-interleave. Runs client-side before remote sync (the
    # remote receives ordinary loose TIFs), but listed so the manifest stays a
    # complete mirror of ``pipeline/`` per test_remote_manifest.
    "pipeline/ome_explode.py",
    "pipeline/run_report.py",
    # The sidecar blocks the user owns (cell_gating / sample_definitions /
    # ratios / notes), shared by write_pipeline_info and the end-of-run fetch.
    "pipeline/sidecar.py",
    "pipeline/shape_features.py",
    "pipeline/stardist.py",
    # Cooperative wind-down: the remote pipeline catches SIGTERM and finishes
    # the FOV-groups in flight instead of dying, so a suspended remote run
    # leaves the same resumable output a local one does.
    "pipeline/suspend.py",
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
    """What this run has to move, and how long that should take.

    **Deliberately not a local-vs-remote verdict.** It used to end with
    "remote wins" / "LOCAL would be faster", derived from a *hardcoded*
    ``max(60s, 5s per well)`` local-compute figure with a flat 4x speedup
    assumed for the remote. Neither number measured anything: on a real plate
    that guessed 60s against a run that took eight hours, roughly a
    thousand-fold error, and the banner used it to recommend running locally —
    printed beside a genuinely measured transfer size, which is exactly what
    made it look like a measurement.

    It could not have been right in principle either. The comparison assumes
    local compute pays no I/O, and a dataset that lives on a NAS pays network
    reads for every image whether or not a remote is involved, plus its scratch
    on top. Modelling that is a real piece of work; asserting a winner without
    it is a guess wearing a measurement's clothes.

    So this reports only what it actually knows — bytes, and the transfer time
    those bytes imply at the measured bandwidth. Which side is faster is a
    question this type does not answer.
    """

    bytes_to_transfer: int
    transfer_s: float

    def format(self) -> str:
        gb = self.bytes_to_transfer / (1024**3)
        return f"transfer {gb:.2f} GB ({_fmt_secs(self.transfer_s)}) to the remote"


def _fmt_bytes(n: float) -> str:
    """Bytes at a unit a human reads without counting zeros.

    A fixed MB format renders every archive under a megabyte as "0 MB", which
    reads as a broken measurement rather than a small one — and the planner
    reports sizes for archives that legitimately span kilobytes to gigabytes.
    """
    for unit, scale in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{int(n)} B"


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
    force_wells: Iterable[str] | None = None,
    background_wells: Iterable[str] | str | None = None,
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

    input_dir = input_dir.resolve()
    # Reclaim local scratch leaked by a previously-killed run before we stage a
    # fresh (potentially multi-GB) copy of the input. The input volume's own
    # transit directory is swept too — that is where a rewritten archive is
    # staged now, so sweeping only the home scratch would have moved the leak
    # rather than removed it.
    sweep_stale_scratch(also=[input_dir], log_fn=log_fn)
    # Scanning the input volume to classify the layout is itself slow enough to
    # look like a hang on a spinning external disk, and it is the very first
    # thing that happens after "[remote] target:". Say so before starting.
    if log_fn is not None:
        log_fn(f"[remote] scanning input: {input_dir}\n")
    mode = _detect_local_input_mode(input_dir)
    if log_fn is not None:
        log_fn(f"[remote] input layout: {mode.value}\n")

    if mode is LocalInputMode.ZIP:
        well_zips = tuple(sorted(_well_zips_in(input_dir)))
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
            force_wells=_canonical_force_wells(force_wells),
            background_wells=frozenset(
                _bg_well_key(w) for w in split_background_wells(
                    background_wells or "")),
            log_fn=log_fn,
        )
        if log_fn is not None:
            # Planning emits a line only for a well it drops or rewrites, so a
            # plate whose remaining wells all stage unchanged went silent right
            # after the two or three wells it did report — reading as a stall
            # with most of the plate still to go. Say what the pass concluded.
            log_fn(f"[remote] planned {len(plans)} well archive(s) to upload, "
                   f"{len(up_to_date_list)} already up to date.\n")
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
            if p.is_dir() and is_valid_well_name(p.name)
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
    pre_existing_zips = {p for p in _well_zips_in(input_dir)}

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
            if not (p.is_dir() and is_valid_well_name(p.name)):
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
            for p in _well_zips_in(input_dir):
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
        if p.is_dir() and is_valid_well_name(p.name)
    )
    # The TIFs a folder gave up are deleted the moment its own zip is
    # published, rather than at the end of the run. ``_zip_well_folder`` goes
    # through ``atomic_zip``, which renames a verified archive into place or
    # leaves nothing at all, so nothing is dropped until a complete archive
    # sits beside it. Deferring the deletion left the input dir holding both
    # ``<well>/`` and ``<well>.zip`` for every well for the whole run — two full
    # copies of the plate on the user's volume at peak, on exactly the runs
    # where the input is largest.
    #
    # What is deleted is the archived TIFs, never the folder as a unit: this
    # archive holds ``.tif``/``.tiff`` only, and removing the folder took
    # everything else in it — companion XML, acquisition logs, mask
    # subdirectories, ``.DS_Store`` — with no copy anywhere. A folder that
    # still holds something survives carrying just that remainder, and the log
    # names it rather than reporting the folder gone. A folder with no TIFs is
    # left whole (there is no archive to replace it with), and one whose well
    # already had a user zip is skipped and likewise left.
    preserved: set[Path] = set()
    for folder in folders_after_zipper:
        target = input_dir / f"{folder.name}.zip"
        if target.exists() and target in pre_existing_zips:
            # User-data zip from a prior run; leave it and its folder alone.
            continue
        tifs = _zip_well_folder(folder, target)
        if not tifs:
            continue
        outcome = replace_folder_with_archive(folder, tifs)
        if not outcome.folder_removed:
            preserved.add(folder)
        if log_fn:
            n = len(tifs)
            tail = (
                "folder removed."
                if outcome.folder_removed
                else (f"folder kept, {len(outcome.kept)} file(s) not archived: "
                      f"{', '.join(outcome.kept)}")
            )
            log_fn(
                f"[stage] zipped {folder.name} → {target.name} "
                f"({n} TIF{'s' if n != 1 else ''}); {tail}\n"
            )
    # No rollback of the zips written above. It used to unlink them all if a
    # later well failed to zip — which, now that each folder is consumed as it
    # goes, would delete the only remaining copy of every well that had already
    # succeeded. A part-zipped plate is the correct resume state anyway: the
    # next run reads the finished wells as ZIP mode and picks up the rest.

    well_zips = tuple(sorted(_well_zips_in(input_dir)))
    if not well_zips:
        raise RuntimeError(
            "WellPlateZipper produced no per-well zips; check filename_schema and separator."
        )

    # The post-success hook needs to delete every well folder the zip-up step
    # consumed (both the snapshotted ones for FOLDER mode and the freshly
    # created ones for FLAT mode). Re-scan now rather than using the pre-zipper
    # snapshot because FLAT-mode folders didn't exist when we took the
    # snapshot — but skip the folders the pass deliberately preserved, or this
    # hook would destroy the very remainder that pass kept.
    folders_to_clean = tuple(p for p in folders_after_zipper if p not in preserved)

    def _on_success() -> None:
        """Delete the source artifacts this stage consumed so the
        input dir is now pure zip mode:

        * loose top-level TIFs (snapshotted before the zipper ran —
          in copy mode they're the originals we need to remove; in
          move mode they're already gone and the unlink is a no-op
          caught by the OSError handler);
        * every well folder we zipped up whose contents the archive fully
          covers (whether it pre-existed for FOLDER mode or the zipper just
          created it for FLAT mode). A folder holding files the archive does
          not carry is not in this list at all.
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
    for p in _well_zips_in(input_dir):
        label = _canonical_well_from_path(p.stem)
        if label is not None:
            zip_wells.add(label)

    folder_wells: set[str] = set()
    # A raised OSError from iterdir() (e.g. the drive was yanked) propagates
    # naturally to the caller — no wrapping handler needed.
    for p in input_dir.iterdir():
        if not (p.is_dir() and is_valid_well_name(p.name)):
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


def _snapshot_local_artifacts(local_output_dir: Path) -> dict:
    """Fingerprint the output artefacts already on disk, before the run.

    ``(size, mtime_ns)`` per path. Cheap — one ``stat`` per file, no reads — and
    it is the only way verify can tell "this well's output was replaced" from
    "this well's output is still the one from yesterday", which are otherwise
    the same observation: a file that exists and is non-empty.
    """
    out: dict = {}
    try:
        entries = list(local_output_dir.glob("*"))
    except OSError:
        return out
    for path in entries:
        try:
            if path.is_file():
                st = path.stat()
                out[path.name] = (st.st_size, st.st_mtime_ns)
        except OSError:
            continue
    return out


def _artifact_changed(run, path: Path) -> bool:
    """Whether *path* differs from its pre-run fingerprint.

    A file absent from the snapshot is new, so it changed. Anything that cannot
    be stat'd now is reported as changed rather than failing the run on an
    unreadable file — this check exists to catch output that was silently *not*
    written, not to invent new ways for a good run to fail.
    """
    before = (run.pre_run_artifacts or {}).get(path.name)
    if before is None:
        return True
    try:
        st = path.stat()
    except OSError:
        return True
    return (st.st_size, st.st_mtime_ns) != before


def _forced_from_argv(pipeline_argv: list[str]) -> tuple[bool, frozenset[str]]:
    """The run's re-processing intent, read back off the argv it will run.

    The argv is the only place both spawn paths agree on what the user asked
    for, and the batched runner already reads ``--force`` back out of it the
    same way. Returns ``(force_all, forced_wells)``.
    """
    force_all = "--force" in pipeline_argv
    wells: list[str] = []
    if "--force_wells" in pipeline_argv:
        i = pipeline_argv.index("--force_wells") + 1
        while i < len(pipeline_argv) and not pipeline_argv[i].startswith("--"):
            wells.append(pipeline_argv[i])
            i += 1
    return force_all, _canonical_force_wells(wells)


def _pipeline_launch_is_pointless(
    pipeline_argv: list[str], expected_well_count: int, completed_remote: set[str],
) -> bool:
    """Whether the remote pipeline can be replaced with a no-op.

    True only when the remote already holds output for every well **and the run
    has not been asked to reprocess anything**. That second clause is the whole
    point of this predicate existing.

    ``_compute_run_id`` deliberately leaves ``--force`` out of its hash so a
    forced re-run lands in the *same* remote output dir and overwrites it —
    which is exactly what stocks that dir with a complete set of ``_out.zip``
    files from the previous run. Without a force term here, those two reasonable
    decisions compose into a guarantee: a forced run always resolves to a dir
    that always looks like a cache hit, so the pipeline is replaced by ``true``,
    nothing is recomputed, nothing is fetched, and every local artefact keeps
    the previous run's bytes and mtime while the run reports success. The
    stronger the user's demand to reprocess, the more certain it was to be
    ignored.
    """
    if expected_well_count <= 0:
        return False
    force_all, forced_wells = _forced_from_argv(pipeline_argv)
    if force_all or forced_wells:
        return False
    return len(completed_remote) >= expected_well_count


def _required_tokens(
    nuclear_token: str | None, fluor_tokens: Iterable[str]
) -> set[str]:
    """Keep the nuclear channel + every requested fluor channel."""
    keep = {t.strip() for t in fluor_tokens if t and t.strip()}
    if nuclear_token:
        keep.add(nuclear_token.strip())
    return keep


def _zip_well_folder(folder: Path, out_zip: Path) -> list[Path]:
    """Atomically zip the TIFs in ``folder`` to ``out_zip``.

    Goes through :func:`util.zip_write.atomic_zip`, so it uses the same
    ``__aw_tmp_*.pidNNN`` staging name as
    ``process_microscopy.compress_folder_images_to_zip_and_remove`` and
    publishes only a verified archive.

    Returns the TIFs that went into the archive — the paths, not a count, so
    the caller can hand them to
    :func:`util.zip_write.replace_folder_with_archive` and delete exactly what
    is now in the zip. An empty list means the folder held no images and there
    is nothing for the caller to do.

    Every TIF is read off the user's input volume, so the write carries the
    shared retry shield: on a network mount a dropped session mid-plate would
    otherwise fail the run outright with a bare errno.
    """
    tifs = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in (".tif", ".tiff")
    )
    if not tifs:
        return []

    def _pack() -> None:
        with atomic_zip(out_zip) as zf:
            for t in tifs:
                zf.write(t, arcname=t.name)

    retry_io(_pack, what=out_zip.name, advice=_TRANSFER_ADVICE, path=out_zip)
    return tifs


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
    existing_zip_paths = sorted(_well_zips_in(input_dir))
    existing_wells: dict[str, Path] = {}
    for p in existing_zip_paths:
        label = _canonical_well_from_path(p.stem)
        if label is not None:
            existing_wells[label] = p

    folder_wells: dict[str, Path] = {}
    for p in input_dir.iterdir():
        if not (p.is_dir() and is_valid_well_name(p.name)):
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
    consumed: list[Path] = []
    for label in sorted(folder_wells):
        folder = folder_wells[label]
        target = input_dir / f"{label}.zip"
        tifs = _zip_well_folder(folder, target)
        new_zip_paths.append(target)
        # The archived TIFs go as soon as the archive is published, not at the
        # end of the run — same reasoning as the FOLDER/FLAT zip-up above. The
        # folder itself only goes when the archive covers everything in it: a
        # folder that yielded no TIFs, and one still holding non-image content,
        # both keep what they hold.
        n = len(tifs)
        tail = ""
        if tifs:
            outcome = replace_folder_with_archive(folder, tifs)
            if outcome.folder_removed:
                consumed.append(folder)
                tail = "; folder removed"
            else:
                tail = (f"; folder kept, {len(outcome.kept)} file(s) not "
                        f"archived: {', '.join(outcome.kept)}")
        if log_fn:
            log_fn(
                f"[stage] zipped {folder.name} → {target.name} "
                f"({n} TIF{'s' if n != 1 else ''}) — persisted to "
                f"input dir{tail}.\n"
            )

    all_zips = tuple(sorted(existing_zip_paths + new_zip_paths))

    # The consumed folders are already gone — each was removed as its archive
    # landed — so the input dir is plain ZIP mode from here on rather than
    # MIXED. This hook stays as the backstop for anything the loop above
    # declined to consume. A folder that yielded no TIFs, and one still holding
    # files the archive does not carry, are both deliberately absent from
    # ``consumed`` and so are not removed here either.
    consumed_folders = tuple(consumed)

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


def _drop_reason(dropped_channel: list[str], dropped_done: int,
                 filename_schema: str, filename_sep: str) -> str:
    """Say *what* the planner dropped, in the words the user can act on.

    The log used to report only that an archive was being rewritten. That is the
    one thing the user can already see happening; what they cannot see is which
    channel caused it — and "all my channels are selected, so why is it copying
    my plate?" is not answerable from any other line the run emits.

    Channels are named by the filename's own ``channel`` slot under the run's
    schema. A member whose name does not decompose under that schema is reported
    by its filename instead: "this file's name means nothing to the schema you
    gave" is itself the answer often enough to be worth saying plainly, and a
    guessed channel token would be worse than none.
    """
    parts: list[str] = []
    if dropped_channel:
        fields = [f.strip().lower() for f in (filename_schema or "").split(":")]
        ci = fields.index("channel") if "channel" in fields else -1
        counts: dict[str, int] = {}
        for member in dropped_channel:
            stem = Path(member).stem
            tokens = stem.split(filename_sep)
            token = tokens[ci] if 0 <= ci < len(tokens) else ""
            counts[token or Path(member).name] = counts.get(
                token or Path(member).name, 0) + 1
        named = ", ".join(f"{tok} ({n})" for tok, n in
                          sorted(counts.items(), key=lambda kv: -kv[1]))
        parts.append(f"no channel match: {named}")
    if dropped_done:
        parts.append(f"already processed: {dropped_done} FOV-group(s)")
    return "; ".join(parts) if parts else "reason not recorded"


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
    force_wells: frozenset[str] = frozenset(),
    background_wells: frozenset[str] = frozenset(),
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
                # ``infolist`` over ``namelist``: the compressed size of every
                # member comes from the same central-directory read, at no extra
                # I/O, and it is what decides below whether rewriting this
                # archive is worth more than it costs.
                infos = zf.infolist()
                members = [i.filename for i in infos]
                sizes = {i.filename: i.compress_size for i in infos}
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

        # Only the images are the run's business. A zip written on macOS carries
        # `.DS_Store`; one written by some other tool carries directory entries
        # or metadata sidecars. None of those is a channel, so the channel filter
        # has no opinion on them — and letting it drop them made every archive on
        # the plate look changed (276 of 279 members "kept"), which forced a full
        # rewrite of the whole dataset to remove a few hundred bytes per well.
        # The remote pipeline ignores them anyway: `find_image_groups_in_zip`
        # skips dotfiles and anything without an image extension.
        images = [m for m in members if _is_image_member(m)]
        keep = list(images)
        dropped_channel: list[str] = []
        dropped_done = 0
        if keep_tokens:
            keep = [m for m in keep if _member_matches(m, keep_tokens)]
            dropped_channel = [m for m in images if m not in set(keep)]
            if images and not keep:
                zero_match.append(wz.stem)
                if log_fn:
                    log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                           f"WARN zero channel matches; will fail after pass\n")
                continue

        well = _canonical_well_from_path(wz.stem)
        if _bg_well_key(wz.stem) in background_wells:
            # An empty-well blank is *input to the correction*, not a well whose
            # own output is wanted, so no filter may drop it or thin it out. An
            # incremental re-run finds every one of its FOVs already processed
            # and would otherwise skip it entirely — which starves every well on
            # the plate of the correction the run asked for, and a partial
            # rewrite would be worse still: the field would be estimated from a
            # fraction of the blank while the log reported it as built.
            if log_fn:
                log_fn(f"[remote] ⊙ {wz.stem}: empty-well reference — staged "
                       f"whole, not diffed\n")
            plans.append(_StagePlan(src=wz, keep=None, n_members=len(images)))
            continue
        if well is not None and well in force_wells:
            # Asked for in full, so the incremental diff below is skipped
            # outright. Without this the split drops the well before a single
            # byte is uploaded and the remote never sees it — so
            # ``--force_wells`` on the remote command line has nothing to act
            # on, and the well that most needs reprocessing is the one
            # guaranteed not to be. Logged, because "up to date — not uploaded"
            # for the well the user just forced is exactly the confusion this
            # avoids.
            if log_fn:
                log_fn(f"[remote] ↻ {well}: forced — uploading in full\n")
            plans.append(_StagePlan(src=wz, keep=None, n_members=len(images)))
            continue
        if incremental and prior_output_dir is not None and well is not None:
            # CSV is the primary done-set; fall back to the well's local output
            # zip so a missing / lost CSV doesn't re-upload already-done FOVs.
            done = read_done_fov_keys(prior_output_dir / f"{csv_prefix}_{well}.csv")
            done |= read_done_fov_keys_from_zip(
                prior_output_dir / f"{well}_out.zip",
                schema=filename_schema, sep=filename_sep)
            if done:
                before = len(keep)
                keep = [
                    m for m in keep
                    if fov_tp_from_name(m, schema=filename_schema,
                                        sep=filename_sep) not in done
                ]
                dropped_done = before - len(keep)
                if images and not keep:
                    up_to_date.append(well)
                    if log_fn:
                        log_fn(f"[remote] ✓ {well}: up to date (local) — "
                               f"not uploaded\n")
                    continue

        # "Changed" is decided on the images alone, so a stray `.DS_Store` can
        # never be the reason an archive is rebuilt. When nothing is dropped the
        # archive is used exactly as it is, junk members and all; when something
        # is, the rewrite carries only the images the remote will actually read.
        take_all = len(keep) == len(images)
        if not take_all:
            # Rewriting costs a full local read and write of this archive before
            # a byte is uploaded, and it buys a smaller transfer. Below a
            # worthwhile saving that trade is plainly bad — the remote ignores
            # channels it was not asked for, so sending them costs bandwidth and
            # nothing else. Decided on compressed bytes, which is what crosses
            # the wire, not on member counts: two frames out of twelve can be
            # 40% of an archive or 0.4% of it.
            dropped = [m for m in images if m not in set(keep)]
            drop_bytes = sum(sizes.get(m, 0) for m in dropped)
            total_bytes = sum(sizes.get(m, 0) for m in images)
            saving = (drop_bytes / total_bytes) if total_bytes else 0.0
            if saving < REWRITE_MIN_SAVING:
                take_all = True
                if log_fn:
                    log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                           f"{len(dropped)} of {len(images)} image(s) could be "
                           f"dropped, worth {saving * 100:.1f}% of "
                           f"{_fmt_bytes(total_bytes)} — uploading it as it is "
                           f"rather than rewriting it to save that.\n")
            elif log_fn:
                log_fn(f"[remote] plan ({idx}/{n_total}): {wz.stem} → "
                       f"rewriting, {len(dropped)} of {len(images)} image(s) "
                       f"dropped ({saving * 100:.0f}% of "
                       f"{_fmt_bytes(total_bytes)}): "
                       f"{_drop_reason(dropped_channel, dropped_done, filename_schema, filename_sep)}\n")
        plans.append(_StagePlan(
            src=wz, keep=None if take_all else tuple(keep),
            n_members=len(images),
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

    When it *is* opened it sits beside the input archives rather than on the
    home volume: what it holds is a rewritten copy of those archives, so the
    volume that already has room for the originals is the one likeliest to have
    room for the copies — and the home volume, which a remote run otherwise has
    no reason to touch, stays out of it.
    """
    rewrites = sum(1 for p in plans if p.keep is not None)
    if log_fn is not None and rewrites < len(plans):
        log_fn(f"[remote] {len(plans) - rewrites} archive(s) need no rewrite — "
               f"uploading those straight from the input, nothing copied "
               f"locally\n")
    if not rewrites:
        return None
    near = next((p.src.parent for p in plans if p.keep is not None), None)
    if near is None:                       # unreachable: rewrites > 0
        return _new_scratch_dir("allwell-stage-")
    return _new_transit_dir(near, "allwell-stage-", log_fn=log_fn)


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
            path=dest,
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


# Shared with the pipeline (`process_microscopy._GROUP_IMAGE_EXTS`) rather
# than restated here. These two lists disagreed: this one omitted .png /
# .jpg / .jpeg, so such a member was invisible to the planner and was
# silently dropped from any archive rewritten for another reason — input
# the remote would have processed, gone with nothing said.
_IMAGE_MEMBER_EXTS = tuple(sorted(GROUP_IMAGE_EXTS))


def _is_image_member(member: str) -> bool:
    """Whether a zip member is one of the images the run is here to process.

    Everything else in an archive — `.DS_Store`, directory entries, metadata
    sidecars — is not a channel, so the channel filter must have no opinion on
    it. Deciding "did this archive change?" on the images alone is what keeps a
    single stray file from forcing a rewrite of the whole plate. Mirrors what
    ``process_microscopy.find_image_groups_in_zip`` already skips on the remote
    side: a leading dot, or a suffix that is not an image.
    """
    base = Path(member).name
    if not base or base.startswith("."):
        return False
    return base.lower().endswith(_IMAGE_MEMBER_EXTS)


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
    """**Superseded and unused in production** — see :func:`_plan_staging`.

    The live remote path plans from each archive's central directory and never
    copies an archive it does not have to rewrite; this earlier shape copied
    every archive into scratch first, which dragged a whole NAS-resident plate
    across the network before a byte went to the remote. Only
    ``tests/test_remote_incremental.py`` still calls it. Anything changed about
    the incremental split has to change ``_plan_staging`` to reach a real run —
    fixing this one alone looks right and does nothing.

    Client-side incremental split: rewrite each staged well zip to keep only
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


def _local_input_dir_from_argv(argv) -> "Path | None":
    """The ``--input_dir`` the local argv named, before the remote rewrite.

    This is the user's own plate directory. For an incremental run the *staged*
    upload is a different, partial directory, and a completeness check pointed
    at that one would call a plate finished on the strength of the slice that
    happened to be re-uploaded.
    """
    argv = list(argv or [])
    for i, arg in enumerate(argv):
        if arg == "--input_dir" and i + 1 < len(argv):
            return Path(argv[i + 1])
        if arg.startswith("--input_dir="):
            return Path(arg.split("=", 1)[1])
    return None


def run_client_side_completeness_check(
    local_input_dir: Path | None, local_output_dir: Path, *,
    log_fn: Callable[[str], None] | None = None,
) -> dict:
    """Record completeness for a remote run, on the client, after the fetch.

    The remote never writes this block — ``_build_remote_argv`` appends
    ``--no_run_check`` to every remote launch. Three reasons, and they compound:
    an incremental run's remote output dir holds only the uploaded split, so a
    record written there would inventory the wrong tree; the fetched sidecar
    overwrites the local one, so the record would be destroyed on arrival
    anyway; and a batched run is N separate pipeline launches, each of which
    would describe its own slice as if it were the plate.

    Running here answers a strictly better question. "Did the remote produce
    it" is what a remote-side check could say; "did it get here" is what this
    one says, and a file that failed to transfer is missing locally whether or
    not it ever existed on the far side.

    **Any ``run_check`` the fetch brought back is dropped first.** The remote's
    suppression writes the same "turned off" stub a user's own
    ``--no_run_check`` would, and that stub riding back would tell the user they
    turned the check off when they did not. Clearing it first means the two
    outcomes here are an honest record or no record at all — and no record
    reads as "not checked", which is what actually happened.

    Best-effort throughout: a remote run's output is already on disk and
    verified, and failing to describe it must not fail it.
    """
    log = log_fn or (lambda _m: None)
    out_dir = Path(local_output_dir)
    info_path = out_dir / "pipeline_info.json"
    try:
        from pipeline.completeness import record_run_check
        from pipeline.io import _safe_atomic_text_write

        info = json.loads(info_path.read_text()) if info_path.exists() else {}
        if not isinstance(info, dict):
            info = {}
        if "run_check" in info:
            info.pop("run_check")
            with _safe_atomic_text_write(info_path) as fh:
                json.dump(info, fh, indent=2)

        schema = [f.strip() for f in str(info.get("schema") or "").split(":") if f.strip()]
        if not schema:
            log("[remote] no schema in the sidecar — skipping the completeness check.\n")
            return {}
        sep = str(info.get("separator") or "_")
        csv_prefix = str(
            (info.get("execution_options") or {}).get("csv_prefix") or "measurements")

        containers = {}
        if local_input_dir is not None:
            from util.well_token import canonical_well_label

            in_dir = Path(local_input_dir)
            try:
                for path in sorted(in_dir.iterdir()):
                    if path.name.startswith(".") or path.name.startswith("_"):
                        continue
                    stem = path.stem if path.suffix == ".zip" else path.name
                    label = canonical_well_label(stem)
                    if label and (path.is_dir() or path.suffix == ".zip"):
                        containers[label] = path
            except OSError:
                containers = {}

        return record_run_check(
            info_path=info_path,
            output_dir=out_dir,
            in_containers=containers,
            schema=schema,
            sep=sep,
            csv_prefix=csv_prefix,
            started_at=None,
            mode="remote",
            atomic_writer=_safe_atomic_text_write,
            log=_LogFnLogger(log),
        )
    except Exception as exc:
        log(f"[remote] completeness check failed: {exc}\n")
        return {}


class _LogFnLogger:
    """Adapts the remote runner's ``log(text)`` sink to a logger-shaped object.

    ``record_run_check`` writes through ``logging``-style calls because it is
    normally driven from inside the pipeline; here the destination is the GUI's
    line stream, which takes whole lines with a trailing newline.
    """

    def __init__(self, log_fn):
        self._log = log_fn

    def _emit(self, prefix, msg, args):
        text = (msg % args) if args else str(msg)
        self._log(f"{prefix}{text}\n")

    def info(self, msg, *args):
        self._emit("[remote] ", msg, args)

    def warning(self, msg, *args):
        self._emit("[remote] ", msg, args)

    def error(self, msg, *args):
        self._emit("[remote] ", msg, args)


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
            advice=_TRANSFER_ADVICE, log_fn=log, path=p,
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


# Characters that would end the assignment and start something else. The value
# comes from the user's own remotes.toml, so this is not about a hostile input —
# it is about a typo taking the whole command down with a shell error nobody can
# read back to a config field.
_SCRATCH_FORBIDDEN = ('"', "`", "$(", ";", "&", "|", "<", ">", "\\", "\n", "\r")


def _remote_scratch_export(value: str) -> str:
    """``export ALLWELL_SCRATCH_DIR=…;`` for the remote, or ``""`` when unset.

    This is the one place a path is deliberately **not** run through
    :func:`_remote_quote`. That helper single-quotes, which is right for a path
    the user typed literally and wrong for this one: the values a cluster user
    reaches for are ``$TMPDIR``, ``$SLURM_TMPDIR`` and ``/scratch/$USER``, and
    single quotes would send the literal string ``$TMPDIR`` to the pipeline,
    which would then create a directory named ``$TMPDIR``. (The batch driver hit
    the same class of bug with ``$_aw_i``.)

    So the value is emitted inside double quotes, where ``$VAR`` expands and
    spaces are still safe — and a leading ``~`` is rewritten to ``$HOME`` first,
    because tilde expansion is exactly what double quotes *do* suppress. That
    combination is the whole reason this cannot reuse either existing helper.

    Raises ``ValueError`` on a value carrying shell syntax, rather than
    composing a command that will fail on the remote with an error that names
    nothing the user can act on.
    """
    value = (value or "").strip()
    if not value:
        return ""
    bad = [c for c in _SCRATCH_FORBIDDEN if c in value]
    if bad:
        raise ValueError(
            f"remote_scratch_dir contains shell syntax ({', '.join(bad)!r}) and "
            f"cannot be used as a directory: {value!r}. Use a plain path; "
            f"environment variables such as $TMPDIR or /scratch/$USER are fine."
        )
    if value == "~":
        value = "$HOME"
    elif value.startswith("~/"):
        value = "$HOME/" + value[2:]
    return f'export ALLWELL_SCRATCH_DIR="{value}"; '


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


def _scheduler_handoff_lines(launcher: str, *, batch: int | None = None) -> str:
    """The lines announcing that the run is now waiting on a scheduler.

    Emitted by **every** launch path, which is the point of it being a function.
    A scheduler-backed host queues the pipeline instead of starting it, and a
    queue wait looks exactly like a hang: the upload finishes, the log stops,
    and nothing on screen says what is being waited for. The non-batched path
    said so; the batched one composed the same launcher and said nothing, so a
    batched run went quiet at the precise moment it began waiting.

    Returns ``""`` when the host has no launcher, so a caller can emit it
    unconditionally — a direct-ssh host is not waiting for anything and must not
    be told that it is.

    The job id is deliberately absent here. ``$SLURM_JOB_ID`` is written to the
    ``.slurm`` sidecar from *inside* the allocation, so at this moment it does
    not exist yet; the id first appears in the scheduler's own stderr, which
    ``ProgressTracker`` picks up.
    """
    launcher = (launcher or "").strip()
    if not launcher:
        return ""
    where = f"batch {batch}: " if batch is not None else ""
    return (
        f"[remote] scheduler: {where}requesting allocation via: {launcher}\n"
        f"[remote] scheduler: waiting for the scheduler to grant a node "
        f"(this can queue)…\n"
    )


# Written into the run directory once every byte of the plate has landed. The
# scheduled job waits for it before starting the pipeline, which is what lets
# the allocation be requested *while* the upload is still running instead of
# after it: on a busy cluster the queue wait and the transfer then overlap
# instead of running end to end.
UPLOAD_SENTINEL = ".aw_upload_complete"

# How long a granted allocation will wait for that file before giving up. The
# node is idle and billed for every second of it, so this is a backstop against
# a dead uploader holding a node, not a budget to be spent — a healthy upload
# writes the sentinel as soon as rsync returns. Exit code is distinct so the
# failure reads as "the upload never finished", not as a pipeline crash.
UPLOAD_WAIT_TIMEOUT_S = 7200
UPLOAD_WAIT_POLL_S = 3
_UPLOAD_WAIT_EXIT = 75

# Touched on the remote every ``UPLOAD_HEARTBEAT_S`` for as long as an upload is
# actually in flight. The deadline above is a backstop against a hung uploader;
# this is the answer to a *dead* one — the app being killed, the laptop closing,
# the network going — where waiting out two hours of idle, billed node is the
# wrong behaviour. A heartbeat that stops advancing frees the node in about a
# minute regardless of how long the upload legitimately needed.
#
# The file is touched over ssh, so its mtime is the *remote* clock and the job
# comparing it against its own ``date`` is not measuring clock skew between two
# machines.
UPLOAD_HEARTBEAT = ".aw_upload_alive"
UPLOAD_HEARTBEAT_S = 30
UPLOAD_HEARTBEAT_STALE_S = 120

# Ceiling on one short ssh control command (mkdir, touch, rm -rf). Generous
# enough for an ``rm -rf`` of a run directory on a slow shared filesystem, and
# far short of the hours an unbounded one can park a thread for.
SSH_CONTROL_TIMEOUT_S = 120.0


def _heartbeat_stale_snippet(heartbeat: str, message: str) -> tuple[str, str]:
    """``(init, check)`` shell for "has the app stopped beating?".

    Both waiting loops — the single-shot job waiting for its upload and the
    batch driver waiting for its next batch — ask the same question, so they
    ask it with the same shell rather than each carrying a copy that can drift.

    Two things it deliberately does not do, because the cost of a wrong answer
    is a granted node thrown away:

    * **``stat`` is not assumed to be GNU.** ``stat -c %Y`` is coreutils;
      ``stat -f %m`` is the BSD spelling. A login node with only the latter used
      to yield an empty mtime, which read as "epoch 0" and therefore as
      infinitely stale — every run on such a host aborted the instant it was
      granted a node, blaming the app. When neither spelling works the timestamp
      is unknowable, so the check says so once and stops judging: losing the
      early hand-back is a far smaller harm than never running at all. The ssh
      dying still tears the job down.
    * **A missing file is not instantly fatal.** The app touches the heartbeat
      before the job can look, but the two see it across a shared filesystem
      whose metadata may lag. An absent file is tolerated for the same window as
      a stale one before the node is handed back.
    """
    if not heartbeat:
        return "", ""
    hb_q = _remote_quote(heartbeat)
    misses = max(1, int(UPLOAD_HEARTBEAT_STALE_S // UPLOAD_WAIT_POLL_S))
    init = "_aw_miss=0; _aw_warn=0; "
    check = (
        f'if [ -e {hb_q} ]; then '
        f'_aw_miss=0; '
        f'_aw_hb=$(stat -c %Y {hb_q} 2>/dev/null || '
        f'stat -f %m {hb_q} 2>/dev/null || echo ""); '
        f'_aw_now=$(date +%s); '
        f'if [ -z "$_aw_hb" ]; then '
        f'if [ "$_aw_warn" -eq 0 ]; then _aw_warn=1; '
        f'echo "[remote] WARN this host cannot report a file\'s modification '
        f'time, so the job cannot tell whether the app is still there — it '
        f'will hold the node until the pipeline runs or the walltime ends"; '
        f'fi; '
        f'elif [ "$((_aw_now - $_aw_hb))" -gt {UPLOAD_HEARTBEAT_STALE_S} ]; '
        f'then echo "{message}"; exit {_UPLOAD_WAIT_EXIT}; fi; '
        f'else '
        f'_aw_miss=$((_aw_miss+1)); '
        f'if [ "$_aw_miss" -gt {misses} ]; then '
        f'echo "{message}"; exit {_UPLOAD_WAIT_EXIT}; fi; '
        f'fi; '
    )
    return init, check


def _wait_for_upload_snippet(sentinel: str, heartbeat: str = "") -> str:
    """Shell that blocks until *sentinel* exists, or gives the node back.

    Runs **inside** the allocation, so its output reaches the user on the same
    stdout as the rest of the run. Announcing both edges matters: a job that
    has been granted a node but is waiting on bytes looks exactly like a job
    that has hung, and the whole point of asking for the node early is that
    this window is expected.
    """
    sentinel_q = _remote_quote(sentinel)
    waited = int(UPLOAD_WAIT_TIMEOUT_S // UPLOAD_WAIT_POLL_S)
    # A stale heartbeat means the uploader is gone — killed, disconnected,
    # asleep — and nothing will ever write the sentinel. Checked before the
    # deadline because it is the case that actually happens; the deadline is
    # for an uploader that is alive but wedged.
    init, stale = _heartbeat_stale_snippet(
        heartbeat,
        f"[remote] ERROR the uploader stopped responding "
        f"({UPLOAD_HEARTBEAT_STALE_S}s without a heartbeat) — giving the "
        f"node back")
    return (
        f'if [ ! -f {sentinel_q} ]; then '
        f'echo "[remote] scheduler: node granted; waiting for the upload to '
        f'finish before starting the pipeline…"; fi; '
        f'_aw_w=0; {init}'
        f'while [ ! -f {sentinel_q} ]; do '
        f'_aw_w=$((_aw_w+1)); '
        f'{stale}'
        f'if [ "$_aw_w" -gt {waited} ]; then '
        f'echo "[remote] ERROR the upload never completed — giving the node '
        f'back after {UPLOAD_WAIT_TIMEOUT_S}s"; exit {_UPLOAD_WAIT_EXIT}; fi; '
        f'sleep {UPLOAD_WAIT_POLL_S}; done; '
        f'echo "[remote] scheduler: input is complete — starting the pipeline"; '
    )


def _only_my_job(jid_var: str, marker: str) -> str:
    """Shell that refuses to cancel a job belonging to somebody else.

    Two locks rather than one. The id is read from a file this app wrote, so in
    the ordinary case it is our own job — but ``remote_staging_dir`` can point
    at shared scratch, and ``run_id`` is deterministic in (input, arguments),
    so two people processing the same plate share a run directory and therefore
    a sidecar. Slurm would refuse a non-owner's ``scancel`` anyway; that is the
    scheduler's policy, not this app's promise, and a cluster where operators
    run the GUI would not have it.

    So: ask the scheduler whose job it is and stop if the answer is not us, and
    pass ``-u`` to ``scancel`` as well, so even a job that changed hands between
    the question and the answer cannot be cancelled by us.
    """
    return (
        f'_aw_me=$(id -un 2>/dev/null || echo "$USER"); '
        f'_aw_owner=$(squeue -h -j "${jid_var}" -o %u 2>/dev/null | head -n1); '
        f'if [ -n "$_aw_owner" ] && [ "$_aw_owner" != "$_aw_me" ]; then '
        f'echo "{marker} ${jid_var} $_aw_owner"; exit 0; fi; '
    )


class _UploadHeartbeat:
    """Touches the remote heartbeat for as long as this app owns the run.

    A daemon thread rather than a hook inside the transfer: rsync can be busy
    with one large archive for minutes without returning to any per-file
    callback, and a heartbeat that only advances between files would read as
    dead exactly during the slowest part of the upload.

    Stopping is the point. Whatever ends the run — success, failure, the Stop
    button, the process being killed — takes the beat with it, and a job still
    waiting for its input frees its node about a minute later instead of
    holding it for the full deadline.

    It beats for the whole run, not only the upload, because it is also what
    :meth:`RemoteRunner._sweep_orphaned_allocation` reads to decide whether an
    allocation still has an owner. ``run_id`` is deterministic in the plate, so
    two sessions on the same plate meet at the same sidecar; a beat that
    stopped when the upload finished would make an hour-long compute look
    abandoned and get itself cancelled by the second session.
    """

    def __init__(self, cache: "DatasetCache", path: str,
                 interval_s: float = UPLOAD_HEARTBEAT_S) -> None:
        self._cache = cache
        self._path = path
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        """One touch, now. Called once before the job starts waiting, so the
        first thing it looks at is a heartbeat that already exists.

        Bounded by the beat interval rather than the default: a touch that has
        not landed by the time the next one is due is not going to help, and
        letting them queue up would leave the thread minutes behind while the
        remote reads the silence as an app that died.
        """
        self._cache._ssh_run(["touch", self._path], check=False,
                             timeout_s=self._interval)

    def start(self) -> None:
        """Begin beating. Paired with :meth:`stop` — see the class docstring."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="aw-upload-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop beating, and wait for the thread to notice.

        The whole mechanism is "a beat that stops means the app is gone", so a
        beat that outlives its run is not a leak of one thread — it tells the
        remote a dead run is alive, holds the node for the full deadline, and
        keeps the orphan sweep from ever sweeping.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def __enter__(self) -> "_UploadHeartbeat":
        self.beat()
        self.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.beat()
            except Exception:
                # A missed beat is not fatal on its own — the job tolerates
                # `UPLOAD_HEARTBEAT_STALE_S` of silence — and an ssh blip must
                # not take down an upload that is otherwise fine.
                swallowed("upload heartbeat")

    def __exit__(self, *_exc) -> None:
        self.stop()


# Per-batch handshake files inside the run directory. The local side writes
# ``ready``; the driver writes ``done`` holding that batch's exit code. Names
# are formatted with the 1-based batch number.
BATCH_READY = ".aw_batch_{i}_ready"
BATCH_DONE = ".aw_batch_{i}_done"

# What the driver prints when a batch ends. The local side reads its own
# progress off this rather than polling the remote for the ``done`` file: the
# stream is already being read, so a marker costs no extra ssh and has no poll
# interval to wait out.
BATCH_DONE_MARKER = "[remote] batch-driver: batch {i} finished rc={rc}"
_BATCH_DONE_RE = re.compile(
    r"\[remote\] batch-driver: batch (\d+) finished rc=(-?\d+)")


def _batch_driver_snippet(run_dir: str, n_batches: int, argv: list[str],
                          heartbeat: str = "",
                          per_batch_timeout_s: int = 0,
                          link_blanks: tuple[str, ...] = ()) -> str:
    """A loop that runs the pipeline once per batch **inside one allocation**.

    Batched mode launched every batch separately, so on a scheduler-backed host
    each one queued on its own: an eight-batch plate paid eight queue waits, and
    between them the node was handed back only to be asked for again. An
    allocation is the expensive thing to obtain and the cheap thing to keep.

    So the job holds the node and the batches come to it. For each batch the
    driver waits for that batch's ``ready`` file, runs the pipeline over the
    input directory as it stands, writes the exit code into the matching
    ``done`` file, and prints :data:`BATCH_DONE_MARKER`.

    **A failed batch does not end the run.** The loop carries on and the local
    side collects the codes, so one bad well does not cost the plate its node —
    the end-of-run tally names which batches failed and the run's own exit code
    reflects that any did.

    Two things *do* end it, both meaning "the app is gone rather than slow": a
    heartbeat that stopped advancing, and a batch that never arrived inside
    :data:`UPLOAD_WAIT_TIMEOUT_S`. Both exit :data:`_UPLOAD_WAIT_EXIT` so they
    read as that rather than as a pipeline crash.
    """
    pipeline = _remote_join(argv)
    waits = int(UPLOAD_WAIT_TIMEOUT_S // UPLOAD_WAIT_POLL_S)
    hb_init, stale = _heartbeat_stale_snippet(
        heartbeat,
        "[remote] ERROR the app stopped responding — giving the node back")
    # The batch number is a shell variable, so the filename cannot be quoted
    # whole: `_remote_quote` single-quotes, which would leave the loop watching
    # for a file literally called ".aw_batch_$_aw_i_ready". The directory keeps
    # its quoting (it may contain ~ or spaces) and the leaf is appended in
    # double quotes, which bash concatenates. Braces around the variable are
    # load-bearing too — bare `$_aw_i_ready` names a *different*, unset
    # variable, so every batch would wait on the same empty path.
    in_q = _remote_quote(f"{run_dir}/in")
    staged = _remote_quote(run_dir) + '"/b${_aw_i}"'
    marker_fail = BATCH_DONE_MARKER.format(i="$_aw_i", rc="90")
    # One batch may not outlive the budget the operator sized for one batch.
    # Without this a single wedged well holds the node for the whole run's
    # scaled walltime, which is strictly worse than the per-batch launches this
    # replaces — there, a hang cost one batch's allocation and no more.
    budget = f"timeout {int(per_batch_timeout_s)} " if per_batch_timeout_s else ""
    # The empty-well blank lives in `bg/` for the whole run so every batch can
    # build the same correction from it without it crossing the wire again. A
    # blank that still needs processing is hard-linked into the final batch to
    # be run once for its own output — `ln` on one filesystem moves no bytes,
    # and `cp` is the fallback where it cannot.
    #
    # Named one by one rather than globbing `bg/*.zip`: on a re-run some blanks
    # may already be complete locally, and those are in `bg/` as a reference
    # only. Linking the whole directory would reprocess a well the user's own
    # re-run just declined.
    blank_link = (
        f'if [ "$_aw_i" -eq {int(n_batches)} ]; then '
        + "".join(
            f'_aw_b={_remote_quote(f"{run_dir}/bg/{stem}.zip")}; '
            f'[ -e "$_aw_b" ] && {{ ln "$_aw_b" {in_q}"/" 2>/dev/null '
            f'|| cp "$_aw_b" {in_q}"/"; }}; '
            for stem in link_blanks)
        + 'fi; '
    ) if link_blanks else ""
    ready = (_remote_quote(run_dir)
             + '"/' + BATCH_READY.format(i="${_aw_i}") + '"')
    done = (_remote_quote(run_dir)
            + '"/' + BATCH_DONE.format(i="${_aw_i}") + '"')
    marker = BATCH_DONE_MARKER.format(i="$_aw_i", rc="$_aw_rc")
    return (
        f'_aw_i=1; '
        f'while [ "$_aw_i" -le {int(n_batches)} ]; do '
        f'echo "[remote] batch-driver: waiting for batch $_aw_i of '
        f'{int(n_batches)}"; '
        f'_aw_w=0; {hb_init}'
        f'while [ ! -f {ready} ]; do '
        f'_aw_w=$((_aw_w+1)); '
        f'{stale}'
        f'if [ "$_aw_w" -gt {waits} ]; then '
        f'echo "[remote] ERROR batch $_aw_i never arrived"; '
        f'exit {_UPLOAD_WAIT_EXIT}; fi; '
        f'sleep {UPLOAD_WAIT_POLL_S}; done; '
        # Swap this batch's staged directory into the place the pipeline
        # reads. `mv` within one filesystem is atomic and instant, so the
        # pipeline cannot see a partially-populated input dir, and it cannot
        # see the previous batch's leftovers either — the whole directory is
        # replaced rather than being emptied well by well as downloads land.
        f'rm -rf {in_q} && mv {staged} {in_q} || {{ '
        f'echo "[remote] ERROR batch $_aw_i: could not stage its input"; '
        f'echo "90" > {done}; echo "{marker_fail}"; '
        f'_aw_i=$((_aw_i+1)); continue; }}; '
        f'{blank_link}'
        f'echo "[remote] batch-driver: batch $_aw_i starting"; '
        f'{budget}{pipeline}; _aw_rc=$?; '
        f'echo "$_aw_rc" > {done}; '
        f'echo "{marker}"; '
        f'_aw_i=$((_aw_i+1)); done; '
        f'echo "[remote] batch-driver: all {int(n_batches)} batch(es) done"'
    )


def _compose_driver_cmd(
    launcher: str,
    remote_pidfile: str,
    driver_script: str,
    *,
    env_selector: EnvSelector = DEFAULT_ENV_SELECTOR,
    scratch_dir: str = "",
) -> str:
    """The login-shell command that puts :func:`_batch_driver_snippet` on a node.

    The same shape as :func:`_compose_remote_cmd` — record the login PID, exec
    the launcher, activate conda and record ``$SLURM_JOB_ID`` inside the
    allocation so both cancel paths keep working — but the thing it ends by
    running is the driver loop rather than a single pipeline invocation.

    A separate function rather than another parameter on ``_compose_remote_cmd``
    because that one's exact output is pinned by two test modules, and because
    the driver has no ``cache_hit`` and no upload sentinel: the batches announce
    themselves one at a time, which is what the loop is for.

    With no *launcher* there is no allocation to hold and nothing to save, so
    the driver runs on the login shell — the batched path is then exactly as
    cheap as it was, and the caller keeps its per-batch behaviour either way.
    """
    pidfile_q = _remote_quote(remote_pidfile)
    launcher = launcher.strip()
    guard = f"{_conda_env_guard(env_selector)}; "
    # Both shapes carry it: a batched run on a launcher-less host still writes
    # the pipeline's working set somewhere, and leaving one shape out would put
    # only *some* runs on the configured volume with nothing saying which.
    scratch = _remote_scratch_export(scratch_dir)
    if not launcher:
        return (
            f"echo $$ > {pidfile_q}; "
            f"{_CONDA_ACTIVATE_SNIPPET}; "
            f"{guard}"
            f"{scratch}"
            f"{driver_script}"
        )
    slurm_sidecar_q = _remote_quote(f"{remote_pidfile}.slurm")
    inner = (
        f"{_CONDA_ACTIVATE_SNIPPET}; "
        f"{guard}"
        f"{scratch}"
        f'echo "${{SLURM_JOB_ID:-}}" > {slurm_sidecar_q} 2>/dev/null || true; '
        f'echo "[remote] scheduler: allocation granted — job ${{SLURM_JOB_ID:-?}} '
        f'on ${{SLURMD_NODENAME:-$(hostname)}} '
        f'(partition ${{SLURM_JOB_PARTITION:-?}}) — held for every batch"; '
        f"{driver_script}"
    )
    return (
        f"echo $$ > {pidfile_q}; "
        # `shlex.join`, not `' '.join`: the split alone drops the quoting, so a
        # token carrying a shell metacharacter (`--constraint=a|b`) reaches the
        # login shell as a pipe and the launch dies with "b: command not found".
        # `_compose_remote_cmd` has always done this; this one had not.
        f"exec {shlex.join(shlex.split(launcher))} bash -lc {shlex.quote(inner)}"
    )


def _compose_remote_cmd(
    launcher: str,
    remote_pidfile: str,
    remote_argv: list[str],
    *,
    cache_hit: bool,
    env_selector: EnvSelector = DEFAULT_ENV_SELECTOR,
    upload_sentinel: str = "",
    upload_heartbeat: str = "",
    scratch_dir: str = "",
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
    # Where the pipeline puts its transient working set on the remote. A cache
    # hit execs `true` and writes nothing, so it needs no scratch at all.
    scratch = "" if cache_hit else _remote_scratch_export(scratch_dir)
    if cache_hit or not launcher:
        # PID echoed first so the cancel path can target the pipeline
        # directly; conda activated on the login shell; then exec.
        return (
            f"echo $$ > {pidfile_q}; "
            f"{_CONDA_ACTIVATE_SNIPPET}; "
            f"{guard}"
            f"{scratch}"
            f"exec {_remote_join(remote_argv)}"
        )

    slurm_sidecar_q = _remote_quote(f"{remote_pidfile}.slurm")
    # The ``[remote] scheduler: …`` echo runs *inside* the allocation, so it
    # only prints once the scheduler has granted a node — it streams back over
    # the same ssh stdout and is the user's "queue wait over, now running on
    # <node>" signal, naming the job id and partition for diagnosis.
    # The wait goes first, before conda and before the pipeline: the job may
    # have been granted its node while the plate was still uploading, which is
    # the point of requesting it early.
    wait_snippet = (_wait_for_upload_snippet(upload_sentinel, upload_heartbeat)
                    if upload_sentinel else "")
    inner = (
        f"{_CONDA_ACTIVATE_SNIPPET}; "
        f"{guard}"
        f"{scratch}"
        f'echo "${{SLURM_JOB_ID:-}}" > {slurm_sidecar_q} 2>/dev/null || true; '
        f'echo "[remote] scheduler: allocation granted — job ${{SLURM_JOB_ID:-?}} '
        f'on ${{SLURMD_NODENAME:-$(hostname)}} '
        f'(partition ${{SLURM_JOB_PARTITION:-?}})"; '
        f"{wait_snippet}"
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


class _LogToLineStream(__import__("logging").Handler):
    """Forward a ``logging`` record onto the run's own line stream.

    The GUI reads a run's progress off that stream, so a phase that reports
    itself through ``logging`` is invisible to it unless its records are put
    there. Used for the post-fetch auto-threshold pass, which runs in-process
    rather than as a subprocess and so has no stdout of its own to be read.

    Deliberately format-free: the consumer's regexes match the pipeline's own
    message text, so a prefix or a timestamp here would stop them matching —
    the same class of break as a progress flag that is never passed.
    """

    def __init__(self, emit_line) -> None:
        super().__init__()
        self._emit_line = emit_line

    def emit(self, record) -> None:
        try:
            self._emit_line(record.getMessage() + "\n")
        except Exception:  # a log line must never break the pass it describes
            self.handleError(record)


def _rsync_progress_flags() -> list[str]:
    """Flags that make rsync narrate a transfer, for whatever rsync is here.

    ``--info=name1,stats2,progress2`` needs rsync ≥ 3.1.0. macOS ships 2.6.9 and
    that is the machine most runs are launched from, so on the common case the
    gate around ``--info`` fell through to **nothing at all** — rsync ran
    silently for the entire transfer. Everything downstream is driven off its
    output: ``_looks_like_rsync_filename`` turns per-file lines into
    ``[remote] ↑ <well>.zip`` (the Upload rail and the per-well "uploaded"
    colour), and the progress stream drives the transfer bar and the per-well
    pie slices. With no flags none of it fires, so a multi-hour upload showed
    ``Upload 0/N`` and an unchanged plate from the first second to the last,
    which is indistinguishable from a hang.

    ``-v`` and ``--progress`` both predate 3.0 and are what the old rsync
    understands: ``-v`` names each file as it goes, ``--progress`` emits the
    percentage stream. The parsers already accept both shapes.
    """
    if _rsync_supports_info_flag():
        return ["--info=name1,stats2,progress2"]
    return ["-v", "--progress"]


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
        cmd += _rsync_progress_flags()
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
            well_sizes=_well_zip_sizes(zipped.well_zips),
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
        if log_fn:
            _log_wells_uploaded(zipped.well_zips, log_fn)
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
        cmd += _rsync_progress_flags()
        if log_fn:
            log_fn(
                f"[remote] $ {' '.join(shlex.quote(c) for c in cmd)} … → {dest} "
                f"({len(sources)} file(s))\n"
            )
        cmd += [*sources, dest]
        # Shared streaming retry loop (audit A11).
        sent_bytes, elapsed, rc, _attempts = _stream_rsync_with_retry(
            cmd, log_fn=log_fn, progress_fn=progress_fn, stop_event=stop_event,
            well_sizes=_well_zip_sizes(well_zips),
        )
        if rc == 0 and log_fn:
            _log_wells_uploaded(well_zips, log_fn)
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
        self, remote_argv: list[str], *, check: bool,
        timeout_s: float = SSH_CONTROL_TIMEOUT_S,
    ) -> subprocess.CompletedProcess[str] | None:
        """Run one short control command over ssh.

        Every caller is a ``mkdir`` / ``touch`` / ``rm -rf`` — seconds of work —
        so the call is bounded. Unbounded, a dead network with no keepalive
        parks the calling thread for ever: the batch loop stops on its
        ``touch <ready>`` and never reaches the wait that would notice the run
        is over, and the heartbeat thread stops beating while believing it is
        still beating.

        A timeout is reported as return code 124 (``timeout``'s own
        convention) so a ``check=False`` caller reading ``returncode`` sees a
        failure rather than a success it never had.
        """
        # v5.2.11: use the tilde-aware joiner so ``mkdir -p ~/foo`` reaches
        # the remote shell as ``mkdir -p "$HOME"/foo`` rather than as
        # ``mkdir -p '~/foo'`` (which creates a literal ``~`` directory).
        cmd = self.host.ssh_argv(allocate_tty=False) + [
            _remote_join(remote_argv),
        ]
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, check=check,
                timeout=timeout_s,
            )
        except subprocess.CalledProcessError as exc:
            if check:
                raise
            return exc  # type: ignore[return-value]
        except subprocess.TimeoutExpired as exc:
            if check:
                raise RuntimeError(
                    f"the remote did not answer within {timeout_s:g}s: "
                    f"{' '.join(str(a) for a in remote_argv[:3])}"
                ) from exc
            return subprocess.CompletedProcess(cmd, 124, "", "")

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


def _well_xfer_line(well: str, direction: str, fraction: float) -> str:
    """Format one per-well transfer-progress line (see ``WELL_XFER_PREFIX``).

    ``direction`` is ``"up"`` or ``"down"``; ``fraction`` is clamped to 0..1.
    """
    frac = max(0.0, min(1.0, float(fraction)))
    return f"{WELL_XFER_PREFIX}{direction} {well} {frac:.4f}\n"


def _well_zip_sizes(well_zips) -> dict[str, int]:
    """``{archive basename: bytes}`` for the well zips about to be uploaded.

    The denominator for per-well upload progress. An archive that has gone
    missing between staging and upload is simply left out — the transfer will
    fail on its own and say so; guessing a size here would put a dial on a
    well that is not moving.
    """
    sizes: dict[str, int] = {}
    for p in well_zips or ():
        path = Path(p)
        try:
            sizes[path.name] = path.stat().st_size
        except OSError:
            continue
    return sizes


class _UploadWellProgress:
    """Turn one rsync's ``--info=name1,progress2`` stream into per-well upload
    fractions.

    ``progress2`` reports bytes transferred for the *whole* invocation, not per
    file, so a percentage on its own says nothing about any individual well.
    ``name1`` fills the gap: rsync prints a file's name as it starts sending
    it, so the cumulative byte count at that moment is that well's zero point,
    and everything after it until the next name belongs to that well. With the
    local zip's own size as the denominator that gives a genuine per-well
    fraction rather than a plate-wide one smeared across every well.

    Files rsync skips — a cache hit, an incremental re-send — emit no name line
    and contribute no bytes, so they never open a window at all. That is the
    right answer: nothing about them is in flight.
    """

    def __init__(self, sizes: dict[str, int], emit: Callable[[str], None]) -> None:
        # {basename: size_in_bytes} for the archives this rsync was asked for.
        self._sizes = sizes
        self._emit = emit
        self._well: str | None = None
        self._size = 0
        self._base_bytes = 0
        self._cumulative = 0
        self._last_sent = -1.0

    def note_bytes(self, cumulative: int) -> None:
        """Record rsync's running total, and emit the open well's share of it."""
        self._cumulative = cumulative
        if self._well is None or self._size <= 0:
            return
        frac = (cumulative - self._base_bytes) / self._size
        # Rounded before the compare so a byte-level dribble doesn't emit a
        # line per progress update; the dial cannot show finer than this.
        frac = round(max(0.0, min(1.0, frac)), 3)
        if frac != self._last_sent:
            self._last_sent = frac
            self._emit(_well_xfer_line(self._well, "up", frac))

    def note_file(self, name: str) -> None:
        """A new file just started transferring: close the previous well's
        window at full and open this one at zero."""
        self.finish()
        base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        size = self._sizes.get(base)
        if size is None:
            return
        well = _canonical_well_from_path(Path(base).stem)
        if well is None:
            return
        self._well, self._size = well, int(size)
        self._base_bytes = self._cumulative
        self._last_sent = 0.0
        self._emit(_well_xfer_line(well, "up", 0.0))

    def finish(self) -> None:
        """Close the open well's window at full — it is on the remote now."""
        if self._well is not None:
            self._emit(_well_xfer_line(self._well, "up", 1.0))
            self._well = None
            self._size = 0
            self._last_sent = -1.0


def _run_rsync_streaming(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None,
    progress_fn: Callable[[float], None] | None,
    stop_event: threading.Event | None,
    well_sizes: dict[str, int] | None = None,
) -> tuple[int, int]:
    """Run rsync, stream its stdout, return ``(bytes_sent, returncode)``.

    Honors the stop_event by terminating the rsync subprocess. Parses the
    ``--info=stats2`` summary line at the end for the byte count.

    ``well_sizes`` maps ``{archive basename: bytes}`` for the well zips this
    invocation is uploading. When supplied (and ``log_fn`` is set) the stream
    also yields per-well upload fractions — see :class:`_UploadWellProgress`.

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
    wells = (
        _UploadWellProgress(well_sizes, log_fn)
        if (well_sizes and log_fn is not None) else None
    )

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
            if is_progress and wells is not None:
                try:
                    wells.note_bytes(int(m.group(1).replace(",", "")))
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
                    # rsync's own percentage stream is live state, not events:
                    # marked transient so it drives the status line and the
                    # transfer bar without writing to the log at all. Still
                    # rate-limited, since the status line is repainted per
                    # update and a fast link emits these continuously.
                    now = time.monotonic()
                    if now - last_progress_log >= PROGRESS_LOG_MIN_INTERVAL:
                        log_fn(f"{TRANSIENT_PREFIX}{line}\n")
                        last_progress_log = now
                elif _looks_like_rsync_filename(line):
                    file_count += 1
                    if wells is not None:
                        wells.note_file(line)
                    log_fn(f"[remote] ↑ ({file_count}) {line}\n")
        if wells is not None:
            wells.finish()
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


def _log_wells_uploaded(well_zips, log_fn: Callable[[str], None]) -> None:
    """Say which wells are now on the remote, once their transfer succeeded.

    The dashboard's Upload rail counts wells off ``[remote] ↑ …<well>.zip``
    lines, and those come from rsync's own per-file output — which is a poor
    thing to depend on, because rsync stays silent in three ordinary cases:

    * **a cache hit**, where it moves nothing because the inputs are already
      there;
    * **an incremental re-send**, where it skips the files that match;
    * **an rsync without ``--info=name1``** — macOS still ships 2.6.9, which
      does not have the flag at all (see ``_rsync_supports_info_flag``).

    In every one of those the transfer succeeded and the rail sat at 0/N, which
    reads as a stalled upload. Batched mode hit it hardest: ``upload_subset``
    had no compensator of any kind, so a batched run's rail never moved off
    zero on any of the three.

    Emitted from the success path instead, where the caller knows exactly which
    archives it asked for and that rsync returned 0. The dashboard de-dupes
    into sets, so this is harmless alongside live per-file lines.
    """
    for wz in well_zips:
        log_fn(f"[remote] ↑ {Path(wz).stem}.zip on remote\n")


def _stream_rsync_with_retry(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None = None,
    progress_fn=None,
    stop_event: threading.Event | None = None,
    well_sizes: dict[str, int] | None = None,
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
            well_sizes=well_sizes,
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
    #: The user's own input directory, as the local argv named it — not the
    #: staged upload, which for an incremental run holds only the new-FOV
    #: split. The end-of-run completeness check compares against it, and
    #: comparing against the split would report a plate as finished because
    #: only the part that was re-uploaded was checked.
    local_input_dir: Path | None = None
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
    # What the run was told to reprocess. Carried here because the decisions
    # that act on it are downstream of the staging planner, which is where the
    # intent used to stop: the Fetcher decides whether a local artefact is
    # replaced, and "it already exists locally" is precisely the wrong reason to
    # skip a well the user asked to have replaced.
    force: bool = False
    force_wells: frozenset[str] = frozenset()
    # Pre-run fingerprint of the local output artefacts, ``path -> (size,
    # mtime_ns)``. Verify compares against it so a run that was told to replace
    # a well's output has to prove it did, rather than proving the old file is
    # still there.
    pre_run_artifacts: dict = field(default_factory=dict)
    # Why each well the Fetcher declined to transfer was declined, ``well ->
    # reason``. "Why was this well not fetched?" was answerable only from
    # stdout as it streamed, which is no help once the log window is closed;
    # this is what the end-of-run reconciliation reads.
    skipped_wells: dict = field(default_factory=dict)


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
        *,
        heartbeat: "_UploadHeartbeat | None" = None,
    ):
        self._ssh = ssh_proc
        self._run = run
        self._fetcher = fetcher
        # Beats for as long as this process owns the allocation; every exit
        # from the run goes through wait(), terminate() or kill(), and each of
        # them stops it. See _UploadHeartbeat's docstring for why it may not
        # end with the upload.
        self._heartbeat = heartbeat
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
        self._stop_heartbeat()
        # Drain remaining lines, then close the queue.
        if self._tee_thread is not None:
            self._tee_thread.join(timeout=5)
        self._line_q.put(None)
        self._fetcher.flush_and_join()
        return rc

    def terminate(self) -> None:
        self._stop_heartbeat()
        self._fetcher.cancel()
        self._ssh.terminate()

    def kill(self) -> None:
        self._stop_heartbeat()
        self._fetcher.cancel()
        self._ssh.kill()

    def _stop_heartbeat(self) -> None:
        """End the beat once, on whichever exit path gets here first."""
        hb, self._heartbeat = self._heartbeat, None
        if hb is not None:
            try:
                hb.stop()
            except Exception:
                swallowed("stopping the run heartbeat")

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


def _empty_well_blanks(argv, well_zips) -> tuple[str, ...]:
    """The blank well archives this run needs for empty-well correction.

    Returns their stems, or empty when the run asks for no empty-well
    correction. Batched mode has to know about them because it slices the plate
    into batches and stages one at a time: the pipeline builds its correction
    from whatever wells are in the input directory *at that moment*, so a blank
    sitting in the last batch corrects the last batch and nothing else. Every
    earlier batch produced numbers with the background left in, and the output
    carried no sign of it.
    """
    flags = _parse_argv_flags(argv, multi=set(),
                              single={"--background", "--flatfield",
                                      "--background_well"})
    if "empty_well" not in (flags.get("--background"), flags.get("--flatfield")):
        return ()
    # The pipeline's own parser and match key, not a fourth spelling of them.
    # A local `re.split(r"[,\s]+", ...)` here read "H12 G12" as two wells while
    # `split_background_wells` reads it as one, so batched mode held out and
    # uploaded two archives the pipeline then matched neither of — the run died
    # on the guard in every batch.
    wanted = {_bg_well_key(w) for w in split_background_wells(
        flags.get("--background_well", ""))}
    if not wanted:
        return ()
    return tuple(z.stem for z in well_zips if _bg_well_key(z.stem) in wanted)


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

    *argv* is what the GUI built, so it is **pre-remap**: for a label-free method
    ``process_microscopy.main`` moves ``--phase_token`` onto the nuclear slot, and
    the threshold pass asks ``_segmentation_tokens`` which channel was segmented.
    Applying the same remap here keeps that answer right — without it the pass
    zeroes the channel the form happened to name and leaves the channel actually
    segmented with a threshold derived from a brightfield image.
    """
    from types import SimpleNamespace

    flags = _parse_argv_flags(
        argv,
        multi={"--fluor_tokens"},
        single={
            "--nuclear_token", "--cytoplasm_token", "--segmentation_method",
            "--csv_prefix", "--filename_schema", "--filename_sep",
            "--pos_control_well", "--neg_control_well", "--phase_token",
        },
    )
    nuclear_token = flags.get("--nuclear_token", "")
    phase_token = str(flags.get("--phase_token", "") or "").strip()
    if phase_token and flags.get("--segmentation_method", "") in LABELFREE_METHODS:
        nuclear_token = phase_token
    return SimpleNamespace(
        no_auto_threshold=False,
        fluor_tokens=flags.get("--fluor_tokens", []),
        nuclear_token=nuclear_token,
        phase_token=phase_token,
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

    One process runs for the whole plate: a driver
    (:func:`_batch_driver_snippet`) inside a single allocation, which the
    batches are fed to as they finish uploading. Per batch, this side uploads
    that batch's zips into a staging directory of its own, touches the batch's
    ``ready`` file, and waits for the driver's marker naming the exit code. The
    driver swaps the staged directory into the pipeline's input path whole, so
    a batch's pipeline sees that batch's zips and nothing else.

    **A failed batch does not end the run.** The node is already held and the
    remaining batches still need it, so the loop continues and the end-of-run
    tally names which batches failed; the run's own code carries the first of
    them. A single ``Fetcher`` instance persists across all batches with
    ``delete_after_download=True``, so peak remote disk stays at
    ``(M wells of input) + (~1 well of output)``.

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
        # Set by the reader thread as each batch's marker arrives, and read by
        # the batch loop. One Event per batch rather than a single condition,
        # so a marker that lands early (the driver is ahead of the loop) is not
        # missed — an Event that is already set returns immediately.
        self._batch_events: dict[int, threading.Event] = {}
        self._batch_rc: dict[int, int] = {}
        # The driver's stdout has ended: it finished, the node died, or a Stop
        # killed the ssh. Whichever it was, no further batch will report back.
        self._stream_ended = threading.Event()
        # The stream ended with batches still unaccounted for — so it was not
        # the driver finishing. Read by `_await_batch` to say which of the two
        # happened, because "you stopped it" and "the node went away" call for
        # different words and only one of them is the user's doing.
        self._driver_lost = threading.Event()
        self._n_batches = 0
        # True once this object has launched a driver of its own. Until then
        # there is nothing of ours on the scheduler to cancel.
        self._owns_allocation = False
        self._reader: threading.Thread | None = None
        self._heartbeat: _UploadHeartbeat | None = None

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
        self._run_id = run_id
        # Batched input lives in the *run* directory, not the shared dataset
        # cache. Each batch is staged beside it as ``b<i>`` and moved into
        # place by the driver, so the pipeline for a batch sees exactly that
        # batch's zips. The old shared directory relied on the Fetcher having
        # deleted the previous batch's input in time, which nothing waited for
        # — the next batch's startup scan could pick up leftovers and process
        # them twice, which is what `ProgressTracker.well_repeats` counts. The
        # cache is no warmer for it: the Fetcher deletes each zip as it lands
        # either way.
        remote_input = f"{runner.host.remote_staging_dir}/runs/{run_id}/in"
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

        _force, _forced_wells = _forced_from_argv(pipeline_argv)
        runner._run = _RemoteRun(
            run_id=run_id,
            cache_key=key,
            remote_input_dir=remote_input,
            remote_output_dir=remote_output,
            remote_pidfile=remote_pidfile,
            local_output_dir=local_output_dir,
            local_input_mode=zipped.mode,
            local_input_dir=_local_input_dir_from_argv(pipeline_argv),
            output_filter=output_filter,
            incremental=zipped.incremental,
            force=_force, force_wells=_forced_wells,
            pre_run_artifacts=_snapshot_local_artifacts(local_output_dir),
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
            poll_sidecar=True,
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

    def _batch_flag(self, template: str, index: int) -> str:
        """The remote path of one batch's handshake file."""
        run_dir = f"{self._runner.host.remote_staging_dir}/runs/{self._run_id}"
        return f"{run_dir}/{template.format(i=index)}"

    def _batch_staging_dir(self, index: int) -> str:
        """Where batch *index* is uploaded, before the driver swaps it in."""
        return f"{self._runner.host.remote_staging_dir}/runs/{self._run_id}/b{index}"

    def _start_driver(self, n_batches: int) -> bool:
        """Put one allocation on a node and leave it there for the whole run.

        Every batch used to be its own ``srun``, so a scheduler-backed host
        queued once per batch and handed the node back in between. The driver
        holds it and takes the batches as they arrive
        (:func:`_batch_driver_snippet`).

        The ``--time`` in the launcher is scaled here, because it sized one
        batch and now has to cover the plate. The user's own launcher is never
        rewritten on disk — only the copy this run hands to the scheduler.
        """
        run = self._runner._run
        host = self._runner.host
        run_dir = f"{host.remote_staging_dir}/runs/{self._run_id}"
        heartbeat = f"{run_dir}/{UPLOAD_HEARTBEAT}"

        # Ask *before* clearing anything. Everything below assumes this run
        # directory is ours to empty, and `run_id` is deterministic in the
        # plate and the arguments, so a second session on the same plate lands
        # on the same path — its `rm -rf` would take the first session's staged
        # batches and input directory out from under a running pipeline.
        if self._runner._sweep_orphaned_allocation(
                run.remote_pidfile, heartbeat, log_fn=self._log) == "owned":
            self._log(
                "[remote] ERROR another session is already running this exact "
                "dataset and configuration on this host. Both would use the "
                "same remote run directory, so this run stops here rather "
                "than corrupting the one already going.\n")
            self._returncode = 1
            return False

        # Clear any handshake files a previous run of this same dataset left:
        # `run_id` is deterministic, so the driver would otherwise find batch 1
        # already flagged ready and start against an empty input directory.
        self._runner._cache._ssh_run(["mkdir", "-p", run_dir], check=False)
        self._runner._cache._ssh_run(
            ["rm", "-f"] + [self._batch_flag(t, i)
                            for i in range(1, n_batches + 1)
                            for t in (BATCH_READY, BATCH_DONE)],
            check=False,
        )
        # …and any batch a previous, aborted run of this same dataset staged.
        # `run_id` is deterministic, so those directories are still sitting
        # there and the driver would swap one in as though it were ours.
        self._runner._cache._ssh_run(
            ["rm", "-rf", f"{run_dir}/in", f"{run_dir}/bg"]
            + [self._batch_staging_dir(i) for i in range(1, n_batches + 1)],
            check=False,
        )

        # The empty-well blank(s), uploaded once for the whole run. They sit
        # outside the batch staging so the driver's `rm -rf in && mv b<i> in`
        # never touches them, and every batch builds its correction from the
        # same reference. Unconditional: a blank whose CSV is already complete
        # locally is skipped by the batch filter, which on a re-run would
        # otherwise leave the whole plate uncorrected.
        blanks = [z for z in self._zipped.well_zips
                  if z.stem in getattr(self, "_blank_stems", ())]
        if blanks:
            sent, _elapsed, rc = self._runner._cache.upload_subset(
                blanks, f"{run_dir}/bg", log_fn=self._log,
                # A blank is a full well, and it is the first large transfer of
                # the run — without a progress callback the plate-wide
                # "Transferring…" label sits still for its whole duration.
                progress_fn=self._progress, stop_event=self._stop)
            if rc != 0:
                if self._stop.is_set():
                    # `upload_subset` watches `_stop`, so a Stop comes back here
                    # as a non-zero rsync code. Reporting the user's own Stop as
                    # a fault — with rsync's number rather than 130 — is what
                    # the batch-upload path below already avoids.
                    self._log("[remote] stopped while uploading the empty-well "
                              "reference\n")
                    self._returncode = 130
                    return False
                self._log(
                    f"[remote] ERROR could not upload the empty-well blank(s) "
                    f"(rsync rc={rc}). Every batch would run without the "
                    f"background correction this run asked for, and the output "
                    f"would not show it — stopping instead.\n")
                self._returncode = rc
                return False
            self._log(f"[remote] empty-well reference uploaded once "
                      f"({sent / (1024**2):.1f} MB) → {run_dir}/bg\n")
            # A blank uploaded as a reference only was already marked complete
            # by its SKIP marker; the upload's own per-well line then repaints
            # its cell as "uploading" and nothing ever moves it off, because
            # the well is never processed or downloaded. Settle it again.
            for stem in self._blank_stems:
                if stem not in getattr(self, "_link_blank_stems", ()):
                    self._line_q.put(
                        f"SKIP well {stem.upper()} — empty-well reference only; "
                        f"already complete locally.\n")

        # The heartbeat runs for the whole run, not just one upload: the driver
        # checks it at every batch boundary, so an app that dies between
        # batches gives the node back instead of parking it on a `ready` file
        # nobody will ever write.
        self._heartbeat = _UploadHeartbeat(self._runner._cache, heartbeat)
        self._heartbeat.beat()
        self._heartbeat.start()

        remote_argv = self._runner._build_remote_argv(
            self._pipeline_argv, run.remote_input_dir, run.remote_output_dir,
            self._output_filter, self._zipped.mode,
            background_dir=f"{run_dir}/bg" if blanks else "",
        )
        # Each batch still sees only its own slice, so the in-pipeline
        # auto-threshold pass would write defaults from a fraction of the plate
        # — it is suppressed here and re-run once locally over everything.
        if "--no_auto_threshold" not in remote_argv:
            remote_argv = list(remote_argv) + ["--no_auto_threshold"]

        # The budget for one batch is what the operator wrote before this
        # change: their --time sized a single launch.
        per_batch_s = launcher_time_seconds(host.compute_launcher)
        launcher, note = scale_launcher_time(host.compute_launcher, n_batches)
        if note:
            self._log(note)
        # One hand-off for the run rather than one per batch — there is one
        # queue wait now, and this is the line that says it is happening.
        self._log(_scheduler_handoff_lines(launcher))

        driver = _batch_driver_snippet(run_dir, n_batches, remote_argv,
                                       heartbeat, per_batch_s,
                                       link_blanks=(
                                           getattr(self, "_link_blank_stems", ())
                                           if blanks else ()))
        ssh_argv = host.ssh_argv(allocate_tty=True) + [
            _compose_driver_cmd(launcher, run.remote_pidfile, driver,
                                env_selector=self._runner.env_selector,
                                scratch_dir=host.remote_scratch_dir)
        ]
        proc = subprocess.Popen(
            ssh_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # The remote (Linux) pipeline logs UTF-8 — including the em-dash in
            # "Well X — done." Decode as UTF-8 so those markers survive on a
            # cp1252 Windows client; otherwise the dashboard's per-well Process
            # tracking never matches.
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            start_new_session=(os.name == "posix"),
        )
        with self._current_proc_lock:
            self._current_proc = proc
        self._n_batches = n_batches
        # From here on there is an allocation of ours to hand back, which is
        # what `_release_driver` acts on. Set only after the launch: an abort
        # before it would otherwise `scancel` a sidecar written by a *previous*
        # run of this same dataset, or by the other session that made us stop.
        self._owns_allocation = True
        self._reader = threading.Thread(
            target=self._read_driver, args=(proc,), daemon=True,
            name="aw-batch-driver-reader")
        self._reader.start()
        return True

    def _release_driver(self) -> None:
        """Stop the heartbeat and hand back an allocation nobody is feeding.

        Called from ``_run_batches``' ``finally``, so it covers the ends the
        old per-batch loop never had to think about: an upload that failed
        before the first batch, a driver that died, a Stop. The job may still
        be *queued* at this point, in which case its ``.slurm`` sidecar does
        not exist yet and ``close_remote_job`` has nothing to read — so the ssh
        that is holding the queue position is terminated directly as well.
        """
        # Read through `getattr`: this runs in a ``finally``, so it also runs
        # for a crash that happened before `_start_driver` ever set any of it.
        heartbeat = getattr(self, "_heartbeat", None)
        self._heartbeat = None
        if heartbeat is not None:
            with swallow("stopping the upload heartbeat"):
                heartbeat.stop()
        if not getattr(self, "_owns_allocation", False):
            return          # never launched: nothing here is ours to cancel
        if self._returncode in (0, None):
            return          # a clean run: the driver exited on its own
        lock = getattr(self, "_current_proc_lock", None)
        if lock is None or getattr(self, "_runner", None) is None:
            return          # nothing was ever launched
        with lock:
            proc = self._current_proc
        if proc is not None:
            self._log("[remote] releasing the allocation\n")
            with swallow("terminating the batch driver"):
                if proc.poll() is None:
                    proc.terminate()
        with swallow("cancelling the scheduler job"):
            self._runner.close_remote_job(log_fn=self._log)

    def _read_driver(self, proc: subprocess.Popen) -> None:
        """Tee the driver's output, and note where each batch ended.

        One reader for the whole run, where the old code teed inline inside
        each batch's own loop. It also watches for the driver's per-batch
        marker, which is how the batch loop learns a batch is finished: the
        stream is already being read, so that costs no extra ssh and has no
        poll interval to wait out.
        """
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                m = _BATCH_DONE_RE.search(line)
                if m:
                    # Consumed, not forwarded: this is the handshake between the
                    # driver and this loop, and `ProgressTracker` has no reason
                    # to see it.
                    index, rc = int(m.group(1)), int(m.group(2))
                    self._batch_rc[index] = rc
                    self._batch_events.setdefault(
                        index, threading.Event()).set()
                    continue
                self._fetcher.observe(line)
                self._line_q.put(line)
        except Exception:
            swallowed("batch driver reader")
        finally:
            # Whatever ended the stream — the driver finishing, the node dying,
            # a Stop killing the ssh — every batch still waiting has to be
            # released or the loop would block for ever.
            self._stream_ended.set()
            # Batches still unreported means the driver did not finish. This is
            # the only place that learns of it: a Stop reaches this object as a
            # kill of the ssh's process group (`signal_subprocess` uses
            # `proc.pid`, which is the driver's, so `terminate` never runs),
            # and a walltime expiry or a node failure never touches the app at
            # all. Without this the worker thread would sit inside the next
            # batch's rsync — `upload_subset` takes `_stop` and watches it —
            # pushing wells at a node that no longer exists.
            if not self._stop.is_set() and len(self._batch_rc) < self._n_batches:
                self._driver_lost.set()
                self._stop.set()
                with swallow("cancelling downloads after the driver went away"):
                    self._fetcher.cancel()
            # A snapshot: `_await_batch` may be adding an entry from the worker
            # thread at this instant, and iterating the live dict would raise
            # inside a ``finally``. `_stream_ended` is set first, so a batch
            # whose event is created after this still stops within the poll.
            for event in list(self._batch_events.values()):
                event.set()

    def _await_batch(self, index: int, n_batches: int) -> int | None:
        """This batch's exit code, or ``None`` if the driver went away."""
        event = self._batch_events.setdefault(index, threading.Event())
        while not event.wait(timeout=1.0):
            if self._stop.is_set() or self._stream_ended.is_set():
                break
        if index in self._batch_rc:
            return self._batch_rc[index]
        # No code came back. Either the user stopped the run, or the allocation
        # is gone — a walltime expiry, a node failure, somebody else's scancel.
        # `_driver_lost` is checked first because the reader sets `_stop` in
        # that case too, to get the worker out of whatever it is blocked in.
        if self._driver_lost.is_set():
            self._log(
                f"[remote] the allocation ended during batch {index}/"
                f"{n_batches} — no more batches will run\n")
            self._returncode = self._returncode or 1
        else:
            self._log(f"[remote] stopped during batch {index}/{n_batches}\n")
            self._returncode = 130
        return None

    def _run_batches(self) -> None:
        try:
            all_zips = list(self._zipped.well_zips)
            # The whole staged plate — the denominator the GUI's progress
            # headline is measured against. Wells dropped below because their
            # output is already complete locally stay in this count: they are
            # part of what the user asked for, and a denominator that shrinks
            # to the subset actually uploaded lets the headline read "N / N
            # wells (100%)" over a plate that still has wells with no output.
            staged_total = len(all_zips)
            skipped: list[Path] = []

            # Worked out before the local-completion filter below, from the
            # unfiltered plate. The blank is needed as *input to the
            # correction*, not for its own output, so a re-run that legitimately
            # skips it for processing must still upload it — otherwise the
            # filter silently leaves the whole plate uncorrected, which is the
            # worse half of the defect this path exists to fix.
            self._blank_stems = _empty_well_blanks(self._pipeline_argv, all_zips)
            blank_zips = [z for z in all_zips if z.stem in self._blank_stems]

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

            # Inject a synthetic "Zip mode: <total> wells" banner BEFORE
            # batch 1's stdout. The GUI's ``ProgressTracker`` locks on the
            # first such banner via ``if not self.well_total:``; without this,
            # each batch's own banner reports just THAT batch's count (e.g.
            # 50) and the run's progress is measured against one batch. We
            # always say "Zip mode" because the remote pipeline sees zips on
            # disk regardless of the local input mode the user started from.
            # The "— batched" suffix is informational only; the regex
            # ``\s+well`` matches both "well" and "wells" so the parenthetical
            # doesn't disturb the match.
            #
            # The count is the whole staged plate, including the wells skipped
            # above. Each of those then gets its own terminal marker in the
            # same stream, so it is counted and painted on the plate map
            # instead of sitting pending for the length of the run while the
            # headline counts only the wells that were uploaded.
            self._line_q.put(f"Zip mode: {staged_total} wells — batched\n")
            for p in skipped:
                self._line_q.put(
                    f"SKIP well {p.stem.upper()} — output already exists "
                    f"locally; not uploaded. Use --force to reprocess.\n"
                )

            if not all_zips:
                self._log(
                    "[remote] batched mode: every well already complete "
                    "locally — nothing to do.\n"
                )
                self._fetcher.run.well_count_total = 0
                self._returncode = 0
                return

            self._fetcher.run.well_count_total = len(all_zips)

            # Empty-well correction reads the blank from the input directory at
            # model-build time, and batched mode stages one batch at a time — so
            # a blank left in the ordinary slicing corrects the batch it lands
            # in and nothing else. It is held out here, uploaded once to its own
            # directory beside the batches, and named to the pipeline with
            # `--background_dir`. A blank is a full well of uncompressed frames,
            # the same size as any other, so uploading it per batch is not an
            # option: it crosses the wire exactly once.
            sliceable = [z for z in all_zips if z.stem not in self._blank_stems]
            # Holding the blank out only makes sense when there is something
            # else to run. With nothing but blanks left — every sample well
            # complete locally — an empty batch would be uploaded to a directory
            # that is never created, the driver's `mv` would fail, and the run
            # would end 90 without processing the blank at all. In that case the
            # blank is just an ordinary well: it corrects itself, exactly as a
            # single-shot run of it alone would.
            blanks = blank_zips if sliceable else []
            if not sliceable:
                sliceable = list(all_zips)
                # `_start_driver` reads these to decide the `bg/` upload, so
                # clearing them is what keeps the fallback to a single upload
                # rather than sending the blank to both `bg/` and its batch.
                self._blank_stems = ()
            # Which blanks still need processing — per well, not one flag for
            # all of them. With `--background_well G12,H12` where only G12 is
            # complete locally, linking the whole of `bg/` back in would
            # reprocess the well the user's own re-run just declined.
            self._link_blank_stems = tuple(
                z.stem for z in all_zips if z.stem in self._blank_stems)
            if blanks:
                self._log(
                    f"[remote] empty-well correction: {len(blanks)} blank "
                    f"well(s) ({', '.join(sorted(self._blank_stems))}) upload "
                    f"once, outside the batches, so every batch corrects "
                    f"against the same reference"
                    + ("" if self._link_blank_stems else
                       " (already complete locally, so uploaded as a reference "
                       "only)") + "\n"
                )

            # `sliceable` cannot be empty here: an all-complete plate returned
            # above, and a plate of nothing but blanks fell back to treating them
            # as ordinary wells. The `or [[]]` that used to guard this produced
            # exactly the empty batch that ended a run at 90.
            batches = [
                sliceable[i:i + self._batch_size]
                for i in range(0, len(sliceable), self._batch_size)
            ]
            n_batches = len(batches)
            self._log(
                f"[remote] batched mode: {len(all_zips)} well(s) in "
                f"{n_batches} batch(es) of up to {self._batch_size} "
                f"(peak remote disk ≈ {self._batch_size + len(blanks)} "
                f"well-input)\n"
            )

            if self._stop.is_set():
                # Before anything reaches the remote. A Stop that arrives while
                # the plate is still being planned must not leave an allocation
                # behind, and the tests that drive this method with a pre-set
                # stop event build the object without a `_runner` at all — so
                # this ordering is load-bearing twice over.
                self._log("[remote] stopped before the first batch\n")
                self._returncode = 130
                return

            if not self._start_driver(n_batches):
                return
            failed: dict[int, int] = {}

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
                    self._batch_staging_dir(batch_idx + 1),
                    log_fn=self._log,
                    progress_fn=inner_progress,
                    stop_event=self._stop,
                )
                if rc != 0:
                    # `upload_subset` watches `_stop`, so a Stop mid-transfer
                    # comes back here as a non-zero rsync code. Saying "upload
                    # failed" for a stop the user asked for reads as a fault.
                    if self._stop.is_set():
                        self._log(
                            f"[remote] batch {batch_idx + 1}/{n_batches}: "
                            f"upload interrupted\n")
                        self._returncode = (
                            1 if self._driver_lost.is_set() else 130)
                        return
                    self._log(
                        f"[remote] batch {batch_idx + 1} upload failed "
                        f"(rsync rc={rc}); aborting remaining batches\n"
                    )
                    self._returncode = rc
                    return
                self._log(
                    f"[remote] batch {batch_idx + 1}: upload complete "
                    f"({sent_bytes / (1024**2):.1f} MB); handing it to the "
                    f"driver\n"
                )
                # The pipeline for this batch runs inside the allocation the
                # driver is already holding — there is no second queue wait and
                # no second argv to build. Hand it the batch and wait for the
                # code it reports back.
                self._runner._cache._ssh_run(
                    ["touch", self._batch_flag(BATCH_READY, batch_idx + 1)],
                    check=False,
                )
                rc = self._await_batch(batch_idx + 1, n_batches)
                if rc is None:
                    # The driver is gone. Nothing further will ever be run, and
                    # whether that was a Stop or a dead node the outcome is the
                    # same for the batches left.
                    return
                if rc != 0:
                    # Kept, not fatal: one bad well should not cost the plate
                    # the node it already holds. The tally after the loop names
                    # every batch that did this, and the run's own code carries
                    # the first of them.
                    failed[batch_idx + 1] = rc
                    self._log(
                        f"[remote] batch {batch_idx + 1} pipeline exited {rc}; "
                        f"continuing with the remaining batches\n"
                    )
                else:
                    self._log(
                        f"[remote] batch {batch_idx + 1}/{n_batches} complete\n"
                    )

            if failed:
                names = ", ".join(str(i) for i in sorted(failed))
                self._log(
                    f"[remote] {len(failed)} of {n_batches} batch(es) failed "
                    f"({names}); the rest completed. Wells from a failed batch "
                    f"are missing from the output.\n"
                )
                self._returncode = failed[min(failed)]
            else:
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
            # Whatever ended the run, the beat has to end with it: the driver
            # reads a fresh heartbeat as "the app is alive and the next batch is
            # coming", so one left running holds the node for the full deadline
            # and keeps `_sweep_orphaned_allocation` from ever sweeping.
            self._release_driver()
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
                if self._returncode == 0 and not self._auto_threshold_off_by_user():
                    self._run_local_auto_threshold()
            finally:
                self._line_q.put(None)

    def _auto_threshold_off_by_user(self) -> bool:
        """True when the operator unchecked "Calculate cell-gating thresholds".

        ``--no_auto_threshold`` reaches the remote argv for two unrelated
        reasons: the user's choice, and this class appending it per batch so a
        batch cannot write thresholds derived from a sliver of the plate. Once
        appended the two are indistinguishable — which is why the local re-run
        used to write thresholds for someone who had asked for none. Read from
        ``self._pipeline_argv``, the argv as handed in: ``_run_batches`` builds
        each batch's ``remote_argv`` from a copy and never mutates this one, so
        it still answers only the first question.
        """
        return "--no_auto_threshold" in list(self._pipeline_argv or [])

    def _run_local_auto_threshold(self) -> None:
        """Re-run the pipeline's auto-threshold pass locally against
        the full ``local_output_dir`` after every batch has drained.
        Failures are logged but non-fatal (matches the remote
        pipeline's "informational only" stance on auto-threshold).

        The pass reports itself through the ``logging`` module — "Auto-threshold:
        scanning N well(s)", then "completed well i/N (WELL)" as it goes — and
        the GUI parses exactly those strings off the run's line stream to drive
        the Threshold stage. In a *subprocess* run they reach it because the
        pipeline's stdout is that stream. Here the pass runs in-process, so its
        records went to a logger nobody was reading and the only things on the
        line stream were this method's own two bookends: one line as it started
        and one when it finished. Over a full plate that is a long silence right
        at the end of a run, with the rail sitting still. Forwarding the records
        onto the same stream closes it, and the regexes that were always there
        start matching."""
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
            forwarder = _LogToLineStream(self._log)
            log_pylog.addHandler(forwarder)
            log_pylog.setLevel(logging.INFO)
            log_pylog.propagate = False
            try:
                written = run_auto_threshold_pass(
                    args,
                    self._local_output_dir,
                    quantified_channel_tokens(
                        args.fluor_tokens,
                        nuclear_token=args.nuclear_token,
                        segmentation_method=args.segmentation_method,
                        cytoplasm_token=args.cytoplasm_token,
                    ),
                    log_pylog,
                )
            finally:
                log_pylog.removeHandler(forwarder)
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


#: How often the mid-run sidecar poller retries, and how long it keeps trying.
#: The remote pipeline writes ``pipeline_info.json`` before any image work, but
#: the job it is in can sit in the scheduler's queue first, so the window has to
#: cover a wait rather than a start-up. The poll itself is one rsync of a file
#: measured in kilobytes.
SIDECAR_POLL_S = 15.0
SIDECAR_POLL_MAX_S = 12 * 3600.0


class Fetcher:
    """Per-well output puller. Triggered by stdout markers from the
    running pipeline; runs each pull in a small thread pool so the
    upload of well N+1 doesn't block the download of well N's output.

    It also pulls ``pipeline_info.json`` once, as early as the remote has one.
    That file is not a per-well output and nothing in the stdout stream
    announces it, so it has its own poller rather than a marker: see
    :meth:`_sidecar_loop`.
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
        poll_sidecar: bool = False,
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
        # Ends the sidecar poll on its own, without stopping the well fetches:
        # the file lands once and the poller has nothing left to do.
        self._sidecar_done = threading.Event()
        self._sidecar: threading.Thread | None = None
        # Off unless asked. The poller reaches the network on a timer, which a
        # Fetcher built to exercise one method in a unit test must not do; both
        # production sites turn it on, and
        # `tests/test_remote_midrun_sidecar.py` fails if a third one forgets.
        if poll_sidecar:
            self._sidecar = threading.Thread(target=self._sidecar_loop,
                                             daemon=True)
            self._sidecar.start()

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
            well = _canonical_well_label(m.group("well")) or m.group("well").upper()
            self.run.expected_wells.add(well)
            self._pending.put(well)

    def cancel(self) -> None:
        self._stop.set()
        self._sidecar_done.set()
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
        # Whatever ends the run ends the sidecar poll with it: from here on
        # `verify_and_cleanup` fetches the finished sidecar, and a poller still
        # running would be racing it for the same path.
        self._sidecar_done.set()
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

    def _remote_file_size(self, remote_path: str) -> int | None:
        """Byte size of a file on the remote, or ``None`` if it can't be read.

        The denominator for per-well download progress: the heartbeat below
        already counts the bytes that have landed, and this is what they are a
        fraction *of*. One small ssh per well, alongside the ones the Fetcher
        already makes for the CSV listing and the per-well cleanup — cheap
        beside the multi-megabyte transfer it is measuring.

        The script is piped to ``bash -s`` rather than passed as an argument so
        a ``~`` in the staging path still expands on the remote (the same
        lesson ``_rsync_glob`` and ``_delete_well_files_on_remote`` carry), and
        it tries GNU ``stat``, BSD ``stat`` and ``ls`` in turn so it works on a
        Linux cluster or a macOS host without having to know which it is.

        Returns ``None`` on any failure — a missing size means the caller
        reports bytes-so-far and no fraction, never an invented one.
        """
        script = (
            f"stat -c %s -- {remote_path} 2>/dev/null || "
            f"stat -f %z -- {remote_path} 2>/dev/null || "
            f"ls -ln -- {remote_path} 2>/dev/null | awk '{{print $5}}'\n"
        )
        try:
            result = subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
                input=script.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        out = (
            result.stdout.decode("utf-8", errors="replace")
            if result.stdout else ""
        )
        for token in out.split():
            if token.isdigit():
                size = int(token)
                return size if size > 0 else None
        return None

    @contextlib.contextmanager
    def _transfer_heartbeat(self, label: str, dest_path: Path,
                            interval: float = 8.0,
                            breadcrumb_interval: float = _BREADCRUMB_INTERVAL_S,
                            total_bytes: int | None = None,
                            bytes_so_far=None,
                            base_bytes: int = 0):
        """Emit a periodic "still transferring…" line showing bytes-so-far while a
        per-well rsync runs, so a long download is visibly *moving* rather than
        looking hung. The per-well fetch rsync is silent (capture_output), so
        without this the user has no way to tell a slow transfer from a stuck one.
        Bytes-so-far = the growing rsync temp/partial file(s) in the dest dir.

        When ``total_bytes`` is known (the remote size of the file being
        pulled, from :meth:`_remote_file_size`) the same count also drives the
        well's dial on the dashboard, emitted on its own much faster cadence so
        the dial moves smoothly while the log and the status line keep their
        slower ones. Without a total the status line still reports bytes-so-far
        and no fraction is claimed — an unknown denominator must not become a
        made-up percentage.

        ``bytes_so_far`` overrides the counter for a transfer that does not land
        in a single file — the CSV half of a well's fetch is a directory of
        them. ``base_bytes`` is added to whatever the counter reports, so a
        second phase continues the *same* gauge rather than restarting it: a
        dial that dropped back toward zero partway through a fetch would read as
        a retry. With both phases measured against one combined denominator the
        number only ever goes up.
        """
        stop = threading.Event()
        t0 = time.monotonic()
        _counter = bytes_so_far

        def _bytes_so_far() -> int:
            if _counter is not None:
                return int(base_bytes) + int(_counter())
            total = int(base_bytes)
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
            # Three cadences, because they answer three different questions.
            # The dial tick is the fastest: it is a position on a gauge, so it
            # has to move often enough to look alive, and it costs one string.
            # The status line is live state — it never reaches the log, so a
            # long transfer no longer writes a line every few seconds and
            # pushes real events out of the log's 10 000-block cap. The
            # breadcrumb is the durable record: a status line cannot answer
            # "was it stuck at 3am?" the next morning, and one line a minute is
            # a record rather than a flood.
            last_breadcrumb = t0
            last_frac = -1.0
            # The loop runs at the dial's cadence, or at the caller's if that
            # is finer — a caller asking for a fast status line must not be
            # slowed to the dial's tick. The status line then fires every Nth
            # tick, counted rather than clock-compared, so its cadence cannot
            # drift or skip on a tick that returns a hair early.
            tick = min(_WELL_XFER_INTERVAL_S, interval)
            ticks_per_status = max(1, int(round(interval / tick)))
            ticks = 0
            if total_bytes:
                self._log(_well_xfer_line(label, "down", 0.0))
            while not stop.wait(tick):
                now = time.monotonic()
                seen = _bytes_so_far()
                ticks += 1
                if total_bytes:
                    frac = round(min(1.0, seen / total_bytes), 3)
                    if frac != last_frac:
                        last_frac = frac
                        self._log(_well_xfer_line(label, "down", frac))
                if ticks % ticks_per_status:
                    continue
                elapsed = _fmt_secs(now - t0)
                mb = seen / (1024 * 1024)
                of_total = (
                    f" / {total_bytes / (1024 * 1024):.1f}" if total_bytes else ""
                )
                self._log(
                    f"{TRANSIENT_PREFIX}Downloading {label} — "
                    f"{mb:.1f}{of_total} MB, {elapsed}\n"
                )
                if now - last_breadcrumb >= breadcrumb_interval:
                    last_breadcrumb = now
                    self._log(
                        f"[remote] ↓ {label}: still transferring… "
                        f"({elapsed}, {mb:.1f} MB so far)\n"
                    )

        th = threading.Thread(target=_beat, daemon=True)
        th.start()
        try:
            yield
        finally:
            stop.set()
            th.join(timeout=1.0)
            # Close the dial's window whatever happened. A well whose fetch
            # failed still has to stop showing a part-filled download; the
            # stage rules take over from here, and the well is only marked
            # downloaded once its CSVs have landed too.
            if total_bytes:
                self._log(_well_xfer_line(label, "down", 1.0))

    def _already_local(self, well: str) -> str | None:
        """Why this well needs no transfer at all, or ``None`` to fetch it.

        A well the remote **skipped** — up to date, or never uploaded by the
        incremental split — emits no ``Well X — done.`` marker, so it is not in
        ``expected_wells`` and this run produced nothing new for it. If its
        local artefacts are already complete there is nothing on the remote
        that this fetch could improve, and pulling it anyway costs a full
        per-well transfer: an ssh size probe, then two ``rsync --timeout=120``
        invocations, each retry-wrapped. That is the shape behind a fetch that
        sits on one well for minutes while the file it is pulling is already on
        disk, correct, and untouched by this run.

        Deliberately narrow. A well this run *did* process is always re-fetched,
        however complete the local copy looks, because a local artefact from an
        earlier run is exactly what the new output is meant to replace.
        """
        run = self.run
        if well in run.expected_wells:
            return None                       # this run produced new output
        if run.force or well in run.force_wells:
            # The user asked for this well to be reprocessed, so the local
            # artefact is the thing being replaced — "it already exists" is the
            # one reason that cannot justify keeping it. A forced well missing
            # from ``expected_wells`` means its completion marker was never
            # seen, which is a reason to look harder, not to skip.
            return None
        if run.output_filter is OutputFilter.FULL:
            local_zip = run.local_output_dir / f"{well}_out.zip"
            if not (local_zip.exists() and _zip_is_complete(local_zip)):
                return None
        if not list(run.local_output_dir.glob(f"*_{well}.csv")):
            return None
        return "already complete locally and untouched by this run"

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
        skip_reason = self._already_local(well)
        if skip_reason is not None:
            self._log(f"[remote] ⤼ {well}: {skip_reason} — not re-fetched\n")
            self.run.skipped_wells[well] = skip_reason
            self.run.fetched_wells.add(well)
            return
        # On the output volume, not the home one: this file is the well's
        # output on its way to `local_output_dir`, so the move at the end is a
        # rename rather than a copy across two filesystems, and a full home
        # volume no longer stops a run whose output volume has room.
        tmp_zip = (_transit_dir(self.run.local_output_dir, log_fn=self._log)
                   / f"allwell-{self.run.run_id}-{well}_out.zip")
        zip_src = f"{self.host.ssh_alias}:{self.run.remote_output_dir}/{well}_out.zip"
        csv_glob_src = f"{self.host.ssh_alias}:{self.run.remote_output_dir}/*_{well}.csv"

        total = self.run.well_count_total or 0
        position = len(self.run.fetched_wells) + 1
        prefix = f"({position}/{total}) " if total else ""

        # Say *why* this well is being pulled. A fetch that sits on one well
        # for minutes is only diagnosable if the log distinguishes the three
        # reasons a well reaches this point: it announced completion, the run
        # was told to reprocess it, or its local copy is incomplete. The last
        # one, on a well whose files look present, is the interesting case.
        if well in self.run.expected_wells:
            why = "this run produced new output"
        elif self.run.force or well in self.run.force_wells:
            why = "forced reprocess — the local copy is being replaced"
        else:
            why = "not complete locally"
        self._log(
            f"[remote] ↓ {prefix}{well}: starting download from "
            f"{self.host.ssh_alias} ({why})\n")

        # Ask the remote how big the zip is before pulling it. That number is
        # the denominator for this well's download dial; without it the
        # heartbeat can only say how many bytes have arrived, which cannot be
        # turned into a proportion.
        expected_bytes = self._remote_file_size(
            f"{self.run.remote_output_dir}/{well}_out.zip")

        # A well's output arrives as TWO transfers — the zip, then its CSVs —
        # and the dial has to span both or it stops moving partway through a
        # fetch that is still running. So the CSVs are *listed* here, before the
        # zip is pulled, purely to learn their byte total: both halves then
        # measure against one combined denominator and the gauge only goes up.
        # The listing is the same ssh the pull needs anyway, just moved earlier,
        # so this costs no extra round trip. Either size being unknown drops the
        # combined total to None and the dial simply does not run — an unknown
        # denominator must not become an invented one.
        csv_host, csv_files, csv_bytes = self._list_remote_glob(csv_glob_src)
        combined_bytes = (expected_bytes + csv_bytes
                          if expected_bytes and csv_bytes else expected_bytes)

        # Output zip — exists iff the pipeline finished this well. Pull it
        # even under CSV-only mode: we'll discard it after extracting the
        # files we want.
        t0 = time.monotonic()
        with self._transfer_heartbeat(well, tmp_zip, total_bytes=combined_bytes):
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
        csv_listed = len(csv_files)

        def _csv_bytes_landed(dest_dir: Path) -> int:
            """Bytes of this well's CSVs on disk, including rsync's partials —
            the same accounting the zip half uses, over several files."""
            total = 0
            try:
                paths = [*dest_dir.glob(f"*_{well}.csv"),
                         *dest_dir.glob(f".*_{well}.csv.*")]
            except OSError:
                return 0
            for p in paths:
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
            return total

        def _csv_dir_heartbeat(dest_dir: Path):
            """The dial, continuing across the CSV half of this well's fetch.

            The gauge carries on from the zip's byte count (``base_bytes``)
            against the combined denominator, so the ring keeps climbing rather
            than stopping — or, worse, restarting near zero, which reads as a
            retry. The CSVs land as several files in a directory rather than one
            named file, hence the counter override.
            """
            return self._transfer_heartbeat(
                well, dest_dir, total_bytes=combined_bytes,
                bytes_so_far=lambda: _csv_bytes_landed(dest_dir),
                base_bytes=int(expected_bytes or 0))

        if self.run.incremental:
            # Partial CSV (new FOV rows only) — merge into the local full CSV
            # rather than overwriting it.
            tmp_csv_dir = _new_transit_dir(
                self.run.local_output_dir, f"allwell-csv-{well}-",
                log_fn=self._log)
            try:
                with _csv_dir_heartbeat(tmp_csv_dir):
                    csv_count = self._pull_listed(csv_host, csv_files, tmp_csv_dir)
                if csv_count:
                    self._merge_fetched_csvs(tmp_csv_dir)
            finally:
                shutil.rmtree(tmp_csv_dir, ignore_errors=True)
            # The temp dir is gone by now; name the volume it lived on, which is
            # what the user would have to look at.
            csv_dest = tmp_csv_dir.parent
        else:
            csv_dest = self.run.local_output_dir
            with _csv_dir_heartbeat(csv_dest):
                csv_count = self._pull_listed(csv_host, csv_files, csv_dest)
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
                path=final,
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

        # Beside the destination, for the same reason as the transit zip above:
        # this holds one well's extracted output on its way into `dest`.
        tmp_dir = _new_transit_dir(dest, f"allwell-merge-{well}-",
                                   log_fn=self._log)
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

    # Mid-run sidecar ────────────────────────────────────────────────────

    def _sidecar_loop(self) -> None:
        """Pull ``pipeline_info.json`` as soon as the remote has written one.

        Review reads that sidecar for the filename schema, the channel tokens,
        and ``available_timepoints`` / ``available_fovs``; without it a dataset
        opens with no schema metadata and falls back to detecting channels from
        CSV column names. On a remote run it used to arrive only in
        :meth:`RemoteRunner.verify_and_cleanup`, at the very end — so a plate
        opened mid-run had its per-well CSVs (the Fetcher brings those down as
        each well finishes) and nothing to interpret them with.

        The remote writes it *before any image work*, so it exists within
        seconds of the pipeline starting. What it has to wait out is the
        scheduler: the job can queue for hours before that happens. Hence a
        poll rather than a one-shot, and a window measured in hours.

        Two rules make this safe to run alongside everything else that writes
        this file:

        * **It never overwrites.** A local sidecar already present belongs to a
          previous run of this dataset and carries blocks the remote's does not
          — cell-gating thresholds, sample definitions, ratios, notes, the
          run_report. It is authoritative; the poller stands down.
        * **It stamps what it wrote as provisional.** The sidecar a running job
          has produced describes only the input that job was given, which for a
          batched run is one batch's split — so its ``available_timepoints`` /
          ``available_fovs`` are a subset of the plate's, and the run_report is
          absent entirely. ``verify_and_cleanup`` replaces the file wholesale at
          the end of the run and the mark goes with it; a run that never got
          there leaves the mark in place, which is the honest outcome.
        """
        dest = self.run.local_output_dir / "pipeline_info.json"
        if dest.exists():
            return
        src = (f"{self.host.ssh_alias}:"
               f"{self.run.remote_output_dir}/pipeline_info.json")
        deadline = time.monotonic() + SIDECAR_POLL_MAX_S
        while not (self._stop.is_set() or self._sidecar_done.is_set()):
            # Wait *before* the first attempt, not only between attempts. This
            # thread starts in the same breath as the ssh that launches the
            # pipeline, so at t=0 the remote cannot have written the file yet
            # and an immediate pull is a round trip that is guaranteed to miss.
            self._sidecar_done.wait(SIDECAR_POLL_S)
            if self._stop.is_set() or self._sidecar_done.is_set():
                return
            if time.monotonic() > deadline:
                self._log(
                    "[remote] note: pipeline_info.json never appeared on the "
                    f"remote within {SIDECAR_POLL_MAX_S / 3600:.0f}h; Review "
                    "will get it when the run finishes.\n")
                return
            if self._pull_sidecar_once(src, dest):
                return
            self._sidecar_done.wait(SIDECAR_POLL_S)

    def _pull_sidecar_once(self, src: str, dest: Path) -> bool:
        """One quiet attempt. True once the local sidecar exists.

        Quiet on failure by design: the overwhelmingly common failure is "the
        remote has not written it yet", which is not news, and this runs every
        `SIDECAR_POLL_S` seconds. Only the success is worth a line, and it is
        worth one — it is the moment the plate becomes openable mid-run.
        """
        if dest.exists():
            return True
        tmp = dest.with_name(f".{dest.name}.midrun.tmp")
        result = _rsync_with_retry(
            [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
             "-a", src, to_rsync_path(tmp)],
            log_fn=None, timeout=60, stop_event=self._stop,
        )
        if result.returncode != 0 or not tmp.exists():
            with swallow("mid-run sidecar temp cleanup"):
                tmp.unlink()
            return False
        try:
            self._install_provisional_sidecar(tmp, dest)
        except (OSError, ValueError) as exc:
            self._log(f"[remote] note: could not install the mid-run "
                      f"pipeline_info.json ({exc}); Review will get it when "
                      f"the run finishes.\n")
            with swallow("mid-run sidecar temp cleanup"):
                tmp.unlink()
            return False
        self._log(
            "[remote] ↓ pipeline_info.json (provisional — this run's input "
            "only). The plate can be opened in Review now; wells appear as "
            "they finish.\n")
        return True

    def _install_provisional_sidecar(self, tmp: Path, dest: Path) -> None:
        """Stamp *tmp* as mid-run and put it at *dest*, unless *dest* appeared.

        The stamp is in the artifact rather than only in the run log because
        the artifact is what outlives the session: a run that is stopped, or
        whose app is closed, leaves this file behind as the dataset's only
        description of itself, and it describes less than a finished one does.
        """
        info = json.loads(tmp.read_text())
        if not isinstance(info, dict):
            raise ValueError("pipeline_info.json is not a JSON object")
        info["provisional"] = {
            "source": "mid_run_fetch",
            "run_id": self.run.run_id,
            "note": (
                "Fetched while the run was still going, so it describes only "
                "the input this run was handed — for a batched run, one "
                "batch's split. available_timepoints / available_fovs may be "
                "short of the plate and there is no run_report. The finished "
                "sidecar replaces this file at the end of the run."
            ),
        }
        from pipeline.io import _safe_atomic_text_write

        if dest.exists():          # a re-run's own sidecar, or Review's saves
            return
        with _safe_atomic_text_write(dest) as fh:
            json.dump(info, fh, indent=2)
        with swallow("mid-run sidecar temp cleanup"):
            tmp.unlink()

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

    def _list_remote_glob(self, src_glob: str) -> tuple[str, list[str], int | None]:
        """``(host, remote paths, total bytes)`` for a remote glob, in one ssh.

        Split out of :meth:`_rsync_glob` so a caller can know what it is about
        to pull *before* it pulls it — the CSV half of a well's fetch needs its
        byte total up front, or the well's dial has no denominator for that half
        and has to stop moving at the end of the zip.

        The size is a second command in the *same* script, so this costs no
        extra round trip. It is deliberately separate from the path listing:
        ``ls -1`` gives one clean path per line and is what the pull has always
        parsed, while summing sizes needs ``ls -ln``'s columns, which vary
        between GNU and BSD and break on a filename with a space. If the sum is
        missing or unparseable the paths are still returned and the total is
        ``None`` — an unknown denominator means no dial, never an invented one.
        """
        host, path_glob = src_glob.split(":", 1)
        script = (
            f"ls -1 {path_glob} 2>/dev/null || true\n"
            f"echo '---allwell-bytes---'\n"
            f"ls -ln {path_glob} 2>/dev/null | awk '{{s+=$5}} END {{print s+0}}'\n"
        )
        try:
            listing = subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + ["bash", "-s"],
                input=script.encode("utf-8"),
                capture_output=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError):
            return host, [], None
        out = (listing.stdout.decode("utf-8", errors="replace")
               if listing.stdout else "")
        head, _, tail = out.partition("---allwell-bytes---")
        files = [ln for ln in head.splitlines() if ln.strip()]
        total: int | None = None
        for token in tail.split():
            if token.isdigit():
                total = int(token) or None
                break
        return host, files, total

    def _pull_listed(self, host: str, files: list[str], dest_dir: Path) -> int:
        """Pull already-listed remote paths into *dest_dir*; return how many
        landed. The transfer half of :meth:`_rsync_glob`, split so the listing
        can happen earlier (see :meth:`_list_remote_glob`)."""
        pulled = 0
        dest_str = to_rsync_path(dest_dir).rstrip("/") + "/"
        for f in files:
            if self._rsync(
                [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
                 "-a", f"{host}:{f}", dest_str]
            ):
                pulled += 1
        return pulled

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
        # We list over ssh first; rsync's own glob support requires the source
        # to be quoted in a way that's awkward to spell cross-shell. The listing
        # and the per-file rsync both go through the runner's ssh policy (key,
        # accept-new, batchmode), and the script is piped via stdin so the
        # remote bash expands any ``~`` in the glob normally — shlex.quote here
        # would lose that and the listing would silently return nothing — as
        # bytes, so Windows text mode cannot translate ``\n`` → ``\r\n`` and
        # trip the remote bash with ``$'\r': command not found``.
        host, files, _total = self._list_remote_glob(src_glob)
        if not files:
            return 0, 0
        return len(files), self._pull_listed(host, files, dest_dir)


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
    ) -> RunEstimate:
        """How much this run has to upload, and how long that should take.

        Both are measurable here: the staged archives' sizes, and the host's
        measured bandwidth. Nothing about compute time is — see
        :class:`RunEstimate` for why the local-vs-remote verdict this used to
        end with was removed rather than improved.
        """
        total_bytes = sum(p.stat().st_size for p in zipped.well_zips)
        bw_mbps = max(self.host.measured_bandwidth_mbps, 1.0)
        bps = bw_mbps * 1_000_000 / 8
        transfer_s = (total_bytes / bps) if not is_cache_hit else 0.0
        return RunEstimate(bytes_to_transfer=total_bytes, transfer_s=transfer_s)

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
        * ``> 0`` — split into batches of this many wells, fed one at a
          time to a driver that holds a single allocation for the whole
          run: upload → hand it to the driver → wait for its exit code →
          next batch. The Fetcher deletes each well's input + output zip
          on the remote as soon as the local download lands, so peak disk
          ≈ ``batch_size`` wells of input + ~1 well of output. Tradeoff:
          the pipeline starts once per batch, so StarDist reloads each
          time (~5–10 s per batch). Pick ``batch_size`` based on your
          remote's free disk; the smaller it is, the more loads and the
          lower the peak.

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

        # The cache key is a local computation over the input archives, so the
        # remote paths — and therefore the run directory the scheduled job will
        # wait in — are known before a single byte moves. That is what lets the
        # allocation be asked for first; see the upload below.
        key = self._cache.cache_key(zipped)

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
        remote_sentinel = (
            f"{self.host.remote_staging_dir}/runs/{run_id}/{UPLOAD_SENTINEL}")
        remote_heartbeat = (
            f"{self.host.remote_staging_dir}/runs/{run_id}/{UPLOAD_HEARTBEAT}")

        # The sentinel is cleared with the same call that makes the directory:
        # ``run_id`` is deterministic in (input, arguments), so a re-run lands
        # in the directory the last one used and would otherwise find that
        # run's "upload finished" flag already waiting for it.
        self._cache._ssh_run(  # short-circuit reuse of ssh helper
            ["mkdir", "-p", remote_output],
            check=True,
        )
        # Two calls rather than one shell line: ``_remote_join`` quotes every
        # token, so an ``&&`` between them would reach the remote as a
        # directory named "&&" instead of as an operator.
        self._cache._ssh_run(["rm", "-f", remote_sentinel], check=False)

        _force, _forced_wells = _forced_from_argv(pipeline_argv)
        self._run = _RemoteRun(
            run_id=run_id,
            cache_key=key,
            remote_input_dir=remote_input,
            remote_output_dir=remote_output,
            remote_pidfile=remote_pidfile,
            local_output_dir=local_output_dir,
            local_input_mode=zipped.mode,
            local_input_dir=_local_input_dir_from_argv(pipeline_argv),
            output_filter=output_filter,
            incremental=zipped.incremental,
            force=_force, force_wells=_forced_wells,
            pre_run_artifacts=_snapshot_local_artifacts(local_output_dir),
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
            if _pipeline_launch_is_pointless(
                    pipeline_argv, expected_well_count, completed_remote):
                cache_hit = True
                log(
                    "[remote] FULL CACHE HIT — every well has output "
                    "already; skipping pipeline launch and proceeding "
                    "straight to verify + fetch.\n"
                )
            elif len(completed_remote) >= expected_well_count:
                # The remote has everything, but the run was told to reprocess.
                # Say so: the cache-hit line above is the one a user expects
                # here, and its absence is otherwise unexplained.
                log(
                    "[remote] remote output dir is already complete, but this "
                    "run was asked to reprocess — launching the pipeline "
                    "anyway (it will overwrite).\n"
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
        # **Ask for the node before sending the data**, so a queue wait and the
        # transfer overlap instead of running end to end. Only where there is
        # something to queue *for*: a host with no launcher runs on the login
        # node the moment ssh connects, and a cache hit runs ``true``, so both
        # must keep the old order or the pipeline would start against an input
        # directory that is still filling.
        overlap = bool(self.host.compute_launcher.strip()) and not cache_hit
        remote_cmd = _compose_remote_cmd(
            self.host.compute_launcher,
            remote_pidfile,
            remote_argv,
            cache_hit=cache_hit,
            env_selector=self.env_selector,
            upload_sentinel=remote_sentinel if overlap else "",
            upload_heartbeat=remote_heartbeat if overlap else "",
            scratch_dir=self.host.remote_scratch_dir,
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
            f"[remote] run_id={run_id}  cache_key={key[:8]}\n"
            f"[remote] $ {_remote_join(remote_argv)}\n"
        )
        # Surface the scheduler hand-off so a queue wait reads as "waiting",
        # not a hang. The ``[remote] scheduler: allocation granted …`` line
        # (emitted from inside the srun step) follows once a node is granted.
        if not cache_hit:
            # A cache hit short-circuits to a ``true`` no-op and consumes no
            # allocation, so it is not waiting for one.
            log(_scheduler_handoff_lines(self.host.compute_launcher))

        def _start_ssh() -> subprocess.Popen:
            return subprocess.Popen(
                ssh_argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                # Decode the remote (Linux, UTF-8) pipeline output as UTF-8 so
                # the em-dash in per-well markers ("Well X — done.") isn't
                # mojibake'd on a cp1252 Windows client — otherwise the
                # dashboard's Process stage stays stuck at 0/N while ASCII-only
                # markers (threshold) reach N/N.
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                start_new_session=(os.name == "posix"),
            )

        def _upload() -> tuple[int, float]:
            up_key, sent, elapsed = self._cache.ensure_uploaded(
                zipped, log_fn=log, progress_fn=progress_fn,
                stop_event=stop_event,
            )
            if up_key != key:
                # The key decides the remote input directory, and the job was
                # sent to wait in the one derived above. Two answers here means
                # the bytes went somewhere the pipeline will not look.
                raise RuntimeError(
                    f"upload landed under cache key {up_key[:8]} but the run "
                    f"was prepared for {key[:8]}")
            log(f"[remote] uploaded {sent / (1024**2):.1f} MB in "
                f"{_fmt_secs(elapsed)}\n")
            return sent, elapsed

        heartbeat: _UploadHeartbeat | None = None
        if overlap:
            # Before adding an allocation of our own, hand back one a crashed
            # run of this same dataset may have left holding a node.
            self._sweep_orphaned_allocation(
                remote_pidfile, remote_heartbeat, log_fn=log)
            heartbeat = _UploadHeartbeat(self._cache, remote_heartbeat)
            # One beat before the job can look, so the first thing it sees is a
            # heartbeat that exists rather than one that is missing.
            heartbeat.beat()
            popen = _start_ssh()
            try:
                heartbeat.start()
                _upload()
            except BaseException:
                # An allocation is already in flight and nothing will ever
                # write its sentinel. The stopped heartbeat would free it
                # within the minute anyway; this is the direct route.
                heartbeat.stop()
                log("[remote] upload failed — releasing the allocation\n")
                try:
                    self.cancel(popen)
                except Exception:
                    swallowed("releasing the allocation after a failed upload")
                raise
            # The job is watching for exactly this file.
            self._cache._ssh_run(["touch", remote_sentinel], check=True)
        else:
            _upload()
            popen = _start_ssh()

        fetcher = Fetcher(
            self.host,
            self._run,
            output_filter=output_filter,
            log_fn=log,
            progress_fn=progress_fn,
            poll_sidecar=True,
        )
        # Keep a handle so cancel() can stop an in-flight per-well download.
        self._fetcher = fetcher
        self._cancelled.clear()
        return _RemoteProcess(popen, self._run, fetcher, heartbeat=heartbeat)

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
        # The fetched sidecar OVERWRITES the local one, and a remote output dir
        # starts empty — so its run_report block describes only the wells this
        # run processed (on an incremental run, only the uploaded split). Left
        # alone, that replaces a whole plate's completeness records with a
        # handful, and every skipped well loses the record that said whether its
        # empty output was a measurement or a gap. Capture before, merge after.
        #
        # The user-side blocks have the same problem and a simpler answer. A
        # remote output dir starts empty, so its sidecar never carries the
        # cell-gating thresholds, sample definitions, ratios or notes the user
        # set locally — and a batched run's thresholds are written *locally*, by
        # _run_local_auto_threshold, moments before this fetch overwrites them.
        # Capture before, restore after, for exactly the keys the pipeline's own
        # re-run path preserves.
        info_path = run.local_output_dir / "pipeline_info.json"
        prior_run_report = read_run_report_block(info_path)
        prior_user_blocks = read_user_blocks(info_path)
        for top in ("pipeline_info.json",):
            self._rsync_optional(
                f"{self.host.ssh_alias}:{run.remote_output_dir}/{top}",
                run.local_output_dir / top,
                log_fn=log,
            )
        if merge_fetched_run_report(info_path, prior_run_report):
            log("[remote] merged this run's completeness records into the "
                "existing pipeline_info run_report.\n")
        restored = restore_user_blocks(info_path, prior_user_blocks)
        if restored:
            log(f"[remote] restored local pipeline_info block(s) the fetched "
                f"sidecar did not carry: {', '.join(restored)}\n")

        # Per-failure tracebacks. The pipeline writes them remotely and nothing
        # pulled them back, so on a remote run they stayed on the remote host —
        # and Validate & Repair, which reads out/errors/ to say WHY a well is
        # empty, found an empty directory every time. They are small text files
        # and they are usually the fastest answer to "why did this well fail".
        self._rsync_optional(
            f"{self.host.ssh_alias}:{run.remote_output_dir}/errors/",
            run.local_output_dir / "errors",
            log_fn=log,
            # The pipeline creates errors/ only when a well or FOV-group
            # actually fails, so a clean run has none and this note is the
            # expected outcome, not a problem. Saying so here is the difference
            # between a line that reads as bad news and one that reads as none.
            absent_note="The pipeline only creates errors/ when a well or "
                        "FOV-group fails, so a clean run has none.",
        )

        # The fetched pipeline_info.json reflects only the (partial) input the
        # remote saw — for an incremental run that's just the uploaded new-FOV
        # members, so its available_timepoints/available_fovs miss the rest of
        # the dataset and any dropdown built from them goes stale. Recompute both
        # from the *merged* local outputs (the <well>_out.zip members keep the
        # input stem), which hold the complete picture after the fetch.
        refresh_pipeline_info_available_from_outputs(run.local_output_dir, log_fn=log)

        # Completeness, client-side. Must run AFTER the refresh above: that call
        # rewrites available_timepoints/available_fovs from the merged local
        # outputs, and a check reading the sidecar before it would describe
        # fields the very next line repairs. Runs before the `ok` gate too — a
        # run that came back short is exactly the one whose record is worth
        # having, and the shortfall is what the record is for.
        run_client_side_completeness_check(
            run.local_input_dir, run.local_output_dir, log_fn=log)

        if not ok:
            log("[remote] pipeline returned non-zero — skipping cleanup, leaving remote state.\n")
            return False

        # Verify each expected well's output is present locally.
        # v5.1.1: before declaring the manifest incomplete, give the
        # Fetcher one more pass at any well whose CSV didn't make it
        # back. A transient connection blip during a per-well rsync no
        # longer requires a full pipeline re-run — we re-pull the
        # missing files using the same retry-aware helpers.
        # Record what happened to every well before verify passes judgement on
        # it, so the classification survives whether or not verify fails.
        self._reconcile_fetch(log=log)

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
        self, src: str, dest: Path, *, log_fn: Callable[[str], None],
        absent_note: str = "",
    ) -> None:
        """Pull *src* if the remote has it; say so plainly if it does not.

        ``absent_note`` explains what the absence *means* for this particular
        source, because "not present" alone reads as a fault for a source whose
        absence is the normal, good case. It is deliberately not phrased as a
        verdict on the run: a non-zero rc means the pull did not happen, which
        covers both "there was nothing there" and "there was, and it could not
        be fetched", and this cannot tell those apart.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        result = _rsync_with_retry(
            [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
             "-a", src, to_rsync_path(dest)],
            log_fn=log_fn, timeout=60,
        )
        if result.returncode != 0:
            tail = f" {absent_note}" if absent_note else ""
            log_fn(
                f"[remote] note: {src} not pulled "
                f"(rc={result.returncode}).{tail}\n"
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

        # This backstop reports in the SAME per-well lines the in-stream fetch
        # uses, rather than a form of its own. Those two lines are what the
        # progress tracker pairs to turn a well green, so a well rescued here
        # reaches the same state by the same route — no second vocabulary to
        # keep in step with the first, and nothing for the tracker to learn.
        # It logged nothing at all before, which is why a rescued well stayed
        # painted "computed, awaiting fetch" with its data already on disk.
        def _report_zip(well: str, elapsed: float) -> None:
            """The in-stream fetcher's zip line, for a zip this pass secured.

            Emitted whenever the well's zip is complete locally by the time this
            pass is done with it — including the case where it was already
            there. That is not padding: the pair only completes when *both*
            halves are reported, and a well reaches this backstop having
            registered neither, because the in-stream fetcher returns on a
            failed zip pull before it ever gets to the CSVs.
            """
            out_zip = run.local_output_dir / f"{well}_out.zip"
            try:
                mb = out_zip.stat().st_size / (1024 * 1024)
            except OSError:
                return
            log(f"[remote] ↓ {well}: {mb:.1f} MB zip in {_fmt_secs(elapsed)} "
                f"(end-of-run retry)\n")

        def _report_csvs(well: str, elapsed: float) -> None:
            found = list(run.local_output_dir.glob(f"*_{well}.csv"))
            if found:
                log(f"[remote] ↓ {well}: {len(found)} CSV file(s) in "
                    f"{_fmt_secs(elapsed)} (end-of-run retry)\n")

        for well in sorted(wells):
            well_t0 = time.monotonic()
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
            _report_csvs(well, time.monotonic() - well_t0)
            # Re-pull {well}_out.zip ONLY when the user's output mode wants it
            # AND it isn't already present locally. Pulling a multi-hundred-MB
            # zip just because a CSV was missing is what dragged this backstop
            # into the wall-clock retry loop in the first place.
            if want_zip:
                zip_t0 = time.monotonic()
                out_zip = run.local_output_dir / f"{well}_out.zip"
                # A well the run undertook to reprocess is the exception to
                # "present means nothing to pull": the file sitting there is
                # the one the new output was supposed to replace, so its mere
                # presence is the symptom rather than the all-clear. Re-pull it
                # when it still carries the pre-run size and timestamp — that
                # is the difference between reporting the loss and repairing it.
                stale = (
                    (run.force or well in run.force_wells)
                    and out_zip.exists()
                    and not _artifact_changed(run, out_zip)
                )
                if not out_zip.exists() or out_zip.stat().st_size == 0 or stale:
                    _pull(f"{run.remote_output_dir}/{well}_out.zip")
                _report_zip(well, time.monotonic() - zip_t0)

    def _reconcile_fetch(self, *, log: Callable[[str], None]) -> str:
        """Classify every well the remote holds output for, and record it.

        Three outcomes are possible for a well by the time the fetch is over:
        it was transferred, it was skipped because its local copy already
        looked complete, or the run never saw a completion marker for it. The
        first is the normal case; the other two are the ones a user has to be
        able to ask about afterwards, and until now the answer existed only in
        the run log as it streamed. A report written beside the outputs
        outlives the log drawer.
        """
        run = self._run
        if run is None:
            return ""
        remote_wells = sorted(self._list_remote_completed_wells())
        lines = [
            f"run_id: {run.run_id}",
            f"force: {run.force}",
            f"force_wells: {', '.join(sorted(run.force_wells)) or '(none)'}",
            f"incremental: {run.incremental}",
            f"wells with output on the remote: {len(remote_wells)}",
            "",
        ]
        counts = {"fetched": 0, "skipped": 0, "never-announced": 0}
        for well in remote_wells:
            if well in run.skipped_wells:
                counts["skipped"] += 1
                lines.append(f"{well}  skipped        {run.skipped_wells[well]}")
            elif well in run.expected_wells:
                counts["fetched"] += 1
                lines.append(f"{well}  fetched        this run produced new output")
            else:
                counts["never-announced"] += 1
                lines.append(
                    f"{well}  never-announced no completion marker was seen "
                    f"for this well")
        summary = (
            f"[remote] fetch reconciliation: {counts['fetched']} fetched, "
            f"{counts['skipped']} skipped (already complete locally), "
            f"{counts['never-announced']} never announced completion.\n"
        )
        log(summary)
        report = run.local_output_dir / "remote_fetch_report.txt"
        try:
            report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            swallowed(f"could not write {report}")
            log(f"[remote] could not write the fetch reconciliation: {exc}\n")
        else:
            log(f"[remote] fetch reconciliation written to {report.name}.\n")
        return summary

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
        # Wells this run undertook to rewrite. A forced run promised all of
        # them; otherwise only the wells that announced completion produced
        # anything new, and a well legitimately left alone must not be failed.
        must_be_replaced = (
            set(all_wells) if run.force
            else (set(run.force_wells) | set(run.expected_wells))
        )
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
                elif well in must_be_replaced and not _artifact_changed(
                        run, out_zip):
                    # Existence is not the question for a well the run was told
                    # to reprocess: the file that is present may be the one the
                    # new output was supposed to replace. Byte-size and mtime
                    # both unchanged means nothing was written here, and a run
                    # that reports success over that is the failure mode this
                    # check exists for — hours of remote compute discarded with
                    # nothing on screen saying so.
                    missing.append(
                        f"{well}_out.zip was not replaced (same size and "
                        f"timestamp as before the run)")
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
            '[ -z "$_jid" ] && exit 0; '
            + _only_my_job("_jid", "AW_NOTMINE") +
            'command -v scancel >/dev/null 2>&1 '
            '&& scancel -u "$_aw_me" "$_jid" 2>/dev/null '
            '&& echo "AW_SCANCELLED $_jid" || true'
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
        elif "AW_NOTMINE" in out:
            # Not "already finished": it is running, and it is somebody else's.
            # Reporting it as a clean teardown would say the opposite of what
            # happened, and this is the log a user reads when a node is still
            # busy after they stopped.
            rest = out.split("AW_NOTMINE", 1)[1].strip().split()
            log(f"[remote] scheduler: job {rest[0] if rest else '?'} belongs to "
                f"{rest[1] if len(rest) > 1 else 'another user'} — left "
                f"running.\n")
        else:
            # The common case: ``srun`` already exited with the pipeline, so
            # there was no allocation left to cancel. Confirm the clean
            # teardown so the log has a definite end-of-job marker rather
            # than silence.
            log("[remote] scheduler: job already finished — nothing to cancel.\n")

    def _sweep_orphaned_allocation(
        self, remote_pidfile: str, heartbeat: str,
        *, log_fn: Callable[[str], None] | None = None,
    ) -> str:
        """Cancel an allocation a *previous* run of this app left behind.

        The scancel backstop covers every exit the app survives: a clean quit,
        a finished run, a Stop click. It cannot cover the ones where no Python
        of ours runs at all — SIGKILL, a segfault, a force-quit, the power
        going — because it needs the job id held in memory. The id is on the
        shared filesystem the whole time (`<pidfile>.slurm`), and ``run_id`` is
        deterministic in the input and the arguments, so the next run of the
        same plate can find exactly the job the crashed one left.

        **Guarded, because that determinism cuts both ways.** Two instances
        running the same plate compute the same ``run_id``, so an unconditional
        sweep would have each cancel the other's live job. A run that is still
        owned is beating its heartbeat, and one that started moments ago has a
        fresh sidecar; either is left alone. What gets cancelled is a job whose
        owner has been silent for longer than a stalled upload would be.

        Every step that cannot answer refuses to cancel rather than guessing.
        A job that is somebody else's, or one whose age this host's ``stat``
        cannot report, is left running: the cost of not sweeping is an idle
        node until its walltime, and the cost of sweeping wrongly is somebody's
        half-finished plate.

        Returns which of those it found — ``"swept"``, ``"owned"``,
        ``"notmine"``, ``"unknown"`` or ``"none"`` — because the batched path
        also has to know whether the run directory it is about to empty
        currently belongs to somebody.
        """
        if not self.host.compute_launcher.strip():
            return "none"
        log = log_fn or (lambda _m: None)
        sidecar_q = _remote_quote(f"{remote_pidfile}.slurm")
        hb_q = _remote_quote(heartbeat)
        stale = UPLOAD_HEARTBEAT_STALE_S
        # One round trip: read the id, and the two ages that decide whether it
        # is anyone's. Ages are computed remotely so no clock is compared
        # against another machine's, and both spellings of ``stat`` are tried
        # — GNU's ``-c %Y`` and BSD's ``-f %m`` — because a host with only the
        # second used to report every age as zero, which reads as "long dead"
        # and would cancel a job that had started seconds earlier.
        script = (
            f'_jid=$(cat {sidecar_q} 2>/dev/null); '
            f'[ -z "$_jid" ] && exit 0; '
            f'_now=$(date +%s); '
            f'_sc=$(stat -c %Y {sidecar_q} 2>/dev/null || '
            f'stat -f %m {sidecar_q} 2>/dev/null || echo ""); '
            f'if [ -z "$_sc" ]; then echo "AW_UNKNOWN $_jid"; exit 0; fi; '
            f'_hb=0; if [ -e {hb_q} ]; then '
            f'_hb=$(stat -c %Y {hb_q} 2>/dev/null || '
            f'stat -f %m {hb_q} 2>/dev/null || echo 0); fi; '
            f'if [ "$_hb" -ne 0 ] && '
            f'[ "$((_now - $_hb))" -le {stale} ]; then '
            f'echo "AW_OWNED $_jid"; exit 0; fi; '
            f'if [ "$((_now - $_sc))" -le {stale} ]; then '
            f'echo "AW_OWNED $_jid"; exit 0; fi; '
            + _only_my_job("_jid", "AW_NOTMINE") +
            'command -v scancel >/dev/null 2>&1 '
            '&& scancel -u "$_aw_me" "$_jid" '
            '2>/dev/null && echo "AW_SWEPT $_jid" || true'
        )
        try:
            result = subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + [script],
                capture_output=True, text=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log(f"[remote] WARN scheduler: could not check for a leftover "
                f"allocation ({exc}).\n")
            return "unknown"
        out = result.stdout or ""
        if "AW_SWEPT" in out:
            jid = out.split("AW_SWEPT", 1)[1].strip().split()[:1]
            log(f"[remote] scheduler: cancelled a job left over from an "
                f"earlier run of this dataset ({jid[0] if jid else '?'}) — "
                f"the app it belonged to is gone.\n")
            return "swept"
        if "AW_NOTMINE" in out:
            rest = out.split("AW_NOTMINE", 1)[1].strip().split()
            log(f"[remote] scheduler: the job in this dataset's run directory "
                f"({rest[0] if rest else '?'}) belongs to "
                f"{rest[1] if len(rest) > 1 else 'another user'} — not touching "
                f"it.\n")
            return "notmine"
        if "AW_OWNED" in out:
            jid = out.split("AW_OWNED", 1)[1].strip().split()[:1]
            log(f"[remote] scheduler: another session is running this same "
                f"dataset (job {jid[0] if jid else '?'}); leaving it alone.\n")
            return "owned"
        if "AW_UNKNOWN" in out:
            jid = out.split("AW_UNKNOWN", 1)[1].strip().split()[:1]
            log(f"[remote] WARN scheduler: this host cannot report a file's "
                f"modification time, so there is no way to tell whether job "
                f"{jid[0] if jid else '?'} in this dataset's run directory is "
                f"still in use. Leaving it running.\n")
            return "unknown"
        return "none"

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
        background_dir: str = "",
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
        # Suppress the remote completeness check unconditionally; the client
        # runs it after the fetch instead (see verify_and_cleanup). A record
        # written remotely would describe the wrong tree — an incremental run's
        # remote output dir holds only the uploaded split — and would be
        # destroyed anyway when the fetched sidecar overwrites the local one.
        # The client-side check answers "did it get here", which strictly
        # supersets "did the remote produce it".
        suppress_run_check = "--no_run_check" not in local_argv
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
        if suppress_run_check:
            out.append("--no_run_check")
        # Batched mode stages one batch at a time, so the empty-well blank is
        # not in most batches' input. It is uploaded once to its own directory
        # and named here, which is what lets every batch build the same
        # correction. Empty on every other path, where the whole plate is in
        # `--input_dir` and the pipeline's default already finds it.
        if background_dir:
            out.append(f"--background_dir={background_dir}")
        # Apply the output filter.
        if output_filter is OutputFilter.CSV_ONLY and "--no_save_tophat" not in out:
            out.append("--no_save_tophat")
        # Host-level override args.
        out.extend(self.host.extra_args)
        if self.host.cpu_only and "--cpu_only" not in out:
            out.append("--cpu_only")
        return out
