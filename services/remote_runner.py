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
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
from functools import lru_cache
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Optional

from services.remote_config import (
    RemoteHost,
    resolve_rsync_binary,
    resolve_ssh_binary,
    resolve_ssh_for_rsync,
    to_rsync_path,
    update_bandwidth,
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
_RE_WELL_DONE = re.compile(r"(?:\bWell\s+)?(?P<well>[A-Ha-h]\d{2})\s+—\s+done\.")
_RE_MODE_WELLS = re.compile(r"(?:Zip mode|Flat mode|Folder mode):\s+(\d+)\s+well")

# Default output-filter glob set: just CSVs and the top-level metadata
# sidecar. The remote also gets --no_save_tophat under this filter so QC
# files aren't even produced.
_CSV_ONLY_GLOBS: tuple[str, ...] = ("*.csv", "pipeline_info.json")


# Files this framework needs on the remote in order to run the pipeline.
# Kept minimal on purpose: process_microscopy.py plus its two local-module
# imports, plus the requirements list (so the bootstrap script knows what
# pip-installs to do). Paths are relative to the repo root.
#
# Synced before every run by :meth:`RemoteRunner.spawn` so a user who
# edits any of these files locally doesn't end up running stale code on
# the remote — the per-run cost is a few hundred KB and a quick rsync
# stat-pass when nothing changed.
_REQUIRED_REMOTE_FILES: tuple[str, ...] = (
    "process_microscopy.py",
    "auto_threshold_core.py",
    "well_token.py",
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
    mode = _detect_local_input_mode(input_dir)

    if mode is LocalInputMode.ZIP:
        well_zips = tuple(sorted(input_dir.glob("[A-H][0-9]*.zip")))
        if not well_zips:
            raise RuntimeError(
                f"Input dir reports zip-mode but contains no A01.zip-style files: {input_dir}"
            )
        if fluor_tokens is None:
            # No filtering — use the user's zips directly (read-only).
            return ZippedInput(zip_dir=input_dir, mode=mode, well_zips=well_zips, cleanup=None)
        # v5.2.18: copy the user's zips into a staging dir BEFORE
        # filtering. Pre-v5.2.18 the filter rewrote zips in place at
        # ``input_dir`` — if the user mis-typed a fluor channel name
        # (e.g. "GP" for "GFP"), every fluor image got dropped from
        # the user's original dataset on disk. The user's input dir
        # must be treated as read-only.
        staging = Path(tempfile.mkdtemp(prefix="allwell-stage-"))
        try:
            staged: list[Path] = []
            for wz in well_zips:
                dest = staging / wz.name
                shutil.copy2(wz, dest)
                staged.append(dest)
            staged_zips = tuple(staged)
            zin = _filter_channels_in_staging(
                staging, staged_zips, mode,
                keep_tokens=_required_tokens(nuclear_token, fluor_tokens),
                log_fn=log_fn,
            )
            zin.cleanup = lambda d=staging: shutil.rmtree(d, ignore_errors=True)
            return zin
        except Exception:
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
    try:
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
    except OSError:
        # Drive yanked? Re-raise as a clearer error upstream.
        raise

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
    from well_token import canonical_well_label
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

    Uses the same ``__aw_tmp_*.pidNNN`` rename pattern as
    ``process_microscopy.compress_folder_images_to_zip_and_remove`` so
    a future inspection can tell a partial v5.2.21 mixed-mode resume
    apart from a partial local-pipeline compression. Returns the
    number of TIFs zipped (0 → folder had no images, caller should
    skip).
    """
    tifs = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in (".tif", ".tiff")
    )
    if not tifs:
        return 0
    tmp = out_zip.with_name(f"__aw_tmp_{out_zip.name}.pid{os.getpid()}")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for t in tifs:
                zf.write(t, arcname=t.name)
        tmp.replace(out_zip)
    except BaseException:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
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
        # v5.2.18 invariant: the channel filter must NOT mutate the
        # user's input dir. Copy ALL zips (existing + new) into a
        # temp staging dir and filter there. The originals in
        # input_dir stay pristine.
        staging = Path(tempfile.mkdtemp(prefix="allwell-stage-"))
        try:
            staged: list[Path] = []
            for z in all_zips:
                dest = staging / z.name
                shutil.copy2(z, dest)
                staged.append(dest)
            zin = _filter_channels_in_staging(
                staging, tuple(staged), LocalInputMode.MIXED,
                keep_tokens=_required_tokens(nuclear_token, fluor_tokens),
                log_fn=log_fn,
            )
            zin.cleanup = lambda d=staging: shutil.rmtree(d, ignore_errors=True)
            zin.on_pipeline_success = _on_success
            return zin
        except Exception:
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
        tmp = wz.with_suffix(".zip.tmp")
        with zipfile.ZipFile(wz, "r") as src, zipfile.ZipFile(
            tmp, "w", compression=zipfile.ZIP_DEFLATED
        ) as dst:
            for member, keep in zip(members, keep_mask):
                if keep:
                    dst.writestr(src.getinfo(member), src.read(member))
        # Read the size from the temp before replace — one stat
        # syscall instead of two, and avoids any race where another
        # process might mutate `wz` between rename and stat.
        size_after = tmp.stat().st_size
        tmp.replace(wz)
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


def _conda_run_prefix(env_name: str = "allwell") -> list[str]:
    """Argv prefix that runs the following command inside conda env
    ``env_name``. ``--no-capture-output`` makes stdout/stderr stream
    through unchanged so the existing progress parser keeps working."""
    return ["conda", "run", "--no-capture-output", "-n", env_name]


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
        # break out of the loop on the first attempt.
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

        if log_fn:
            mb = sent_bytes / (1024**2)
            log_fn(
                f"[remote] upload: {mb:.1f} MB in {_fmt_secs(elapsed)}"
                f"{' (cache hit)' if mb < 1 else ''}\n"
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
        t0 = time.monotonic()
        sent_bytes = 0
        rc = -1
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
            sent_bytes += attempt_bytes
            if rc == 0 or rc not in _RSYNC_RETRY_CODES:
                break
        elapsed = max(time.monotonic() - t0, 1e-6)
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


def _iter_rsync_lines(stdout) -> "Iterable[str]":
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
    PROGRESS_LOG_MIN_INTERVAL = 2.0

    try:
        assert proc.stdout is not None
        for line in _iter_rsync_lines(proc.stdout):
            if stop_event and stop_event.is_set():
                proc.terminate()
                break
            if not line:
                continue
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
#   12: error in rsync protocol data stream
#   30: timeout in data send/receive
#   35: timeout waiting for daemon connection
_RSYNC_RETRY_CODES = {10, 12, 30, 35}

# Backoff schedule for rsync retries — mirrors the git push/pull policy
# documented at the top of the runner's invocation env. Total wait
# across the three retries is 14 s, an upper bound the user can sit
# through without thinking the run is hung.
_RSYNC_RETRY_DELAYS = (2.0, 4.0, 8.0)


def _rsync_with_retry(
    cmd: list[str],
    *,
    log_fn: Callable[[str], None] | None = None,
    timeout: int = 300,
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
    timeout: int,
    capture_output: bool,
    cwd: str | None,
    stop_event: threading.Event | None,
) -> subprocess.CompletedProcess:
    """``subprocess.run`` work-alike that polls ``stop_event`` while
    the child runs and terminates it on cancellation. v5.2.28.

    When ``stop_event`` is None this is equivalent to ``subprocess.run``
    — the only path that runs without cancellation is the bootstrap
    ``sync_required_files`` call, which is small and short-lived."""
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
    deadline = time.monotonic() + timeout
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
                if time.monotonic() > deadline:
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
        fetcher: "Fetcher",
    ):
        self._ssh = ssh_proc
        self._run = run
        self._fetcher = fetcher
        self._stdout_pipe: Optional[io.TextIOBase] = None
        # Tee the ssh stdout into the fetcher AND back out to the caller.
        # The caller (PipelineRunner._run_pipeline_subprocess) reads from
        # self.stdout one line at a time; the fetcher also receives
        # every line via the same tee.
        self._tee_thread: Optional[threading.Thread] = None
        self._line_q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._start_tee()

    # Popen-compatible attributes ───────────────────────────────────────

    @property
    def pid(self) -> int:
        return self._ssh.pid

    @property
    def returncode(self) -> Optional[int]:
        return self._ssh.returncode

    @property
    def stdout(self) -> "_QueueLineIterator":
        return self._lines

    def poll(self) -> Optional[int]:
        return self._ssh.poll()

    def wait(self, timeout: Optional[float] = None) -> int:
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

        def _pump(src: io.TextIOBase, q: "queue.Queue[Optional[str]]") -> None:
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
        runner: "RemoteRunner",
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
        self._returncode: Optional[int] = None
        self._line_q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._lines = _QueueLineIterator(self._line_q)
        self._current_proc: Optional[subprocess.Popen] = None

        # Set up the shared run identifiers and remote dirs.
        # v5.4.7: use a per-run input dir (``runs/<run_id>/in``)
        # instead of the persistent cache dir (``datasets/<key>``).
        # The cache dir is shared across runs with the same input
        # set and can hold stale well-zips from a prior aborted run —
        # the per-batch pipeline would then SEE those stale zips
        # alongside the batch's freshly-uploaded ones, process some,
        # and log "Zip mode: N to process, M skipped" with bogus
        # numbers (the user saw "processed 5, skipped 5" on batch
        # one of a fresh run). A run-private input dir is clean by
        # construction, and the per-well Fetcher cleanup keeps it
        # bounded to ~``batch_size`` zips at any time.
        key = runner._cache.cache_key(zipped)  # noqa: SLF001
        run_id = "run-" + _compute_run_id(key, pipeline_argv)
        remote_input = (
            f"{runner.host.remote_staging_dir}/runs/{run_id}/in"
        )
        remote_output = (
            f"{runner.host.remote_staging_dir}/runs/{run_id}/out"
        )
        remote_pidfile = (
            f"{runner.host.remote_staging_dir}/runs/{run_id}/run.pid"
        )
        # Clean any leftover zips from a prior batched run that
        # crashed mid-batch — they'd otherwise be visible to this
        # batch's pipeline scan. Then (re)create the input + output
        # dirs.
        runner._cache._ssh_script(  # noqa: SLF001
            f"rm -f -- {remote_input}/*.zip 2>/dev/null || true\n"
            f"mkdir -p {remote_input} {remote_output}\n",
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
        self._fetcher.run.well_count_total = len(zipped.well_zips)

        self._worker = threading.Thread(
            target=self._run_batches, daemon=True,
        )
        self._worker.start()

    # Popen-compatible surface ─────────────────────────────────────────

    @property
    def pid(self) -> int:
        return self._current_proc.pid if self._current_proc else -1

    @property
    def returncode(self) -> Optional[int]:
        return self._returncode

    @property
    def stdout(self) -> "_QueueLineIterator":
        return self._lines

    def poll(self) -> Optional[int]:
        if self._worker.is_alive():
            return None
        return self._returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        self._worker.join(timeout=timeout)
        return self._returncode if self._returncode is not None else 0

    def terminate(self) -> None:
        self._stop.set()
        if self._current_proc is not None:
            self._current_proc.terminate()
        self._fetcher.cancel()

    def kill(self) -> None:
        self._stop.set()
        if self._current_proc is not None:
            self._current_proc.kill()
        self._fetcher.cancel()

    def send_signal(self, sig: int) -> None:
        if self._current_proc is not None:
            self._current_proc.send_signal(sig)

    # Internals ────────────────────────────────────────────────────────

    def _run_batches(self) -> None:
        try:
            all_zips = list(self._zipped.well_zips)
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

            # v5.4.7: inject a synthetic "Zip mode: <total> wells"
            # banner BEFORE batch 1's stdout. The GUI's
            # ``ProgressTracker`` locks on the first such banner via
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
                    inner_progress: "Callable[[float], None] | None" = _scaled_progress
                else:
                    inner_progress = None
                sent_bytes, _elapsed, rc = self._runner._cache.upload_subset(  # noqa: SLF001
                    batch_zips,
                    self._runner._run.remote_input_dir,  # noqa: SLF001
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
                remote_argv = self._runner._build_remote_argv(  # noqa: SLF001
                    self._pipeline_argv,
                    self._runner._run.remote_input_dir,  # noqa: SLF001
                    self._runner._run.remote_output_dir,  # noqa: SLF001
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
                remote_cmd = (
                    f"echo $$ > {_remote_quote(self._runner._run.remote_pidfile)}; "  # noqa: SLF001
                    f"{_CONDA_ACTIVATE_SNIPPET}; "
                    f"exec {_remote_join(remote_argv)}"
                )
                ssh_argv = (
                    self._runner.host.ssh_argv(allocate_tty=True)
                    + [remote_cmd]
                )
                self._current_proc = subprocess.Popen(
                    ssh_argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                    start_new_session=(os.name == "posix"),
                )
                assert self._current_proc.stdout is not None
                for line in self._current_proc.stdout:
                    if self._stop.is_set():
                        self._current_proc.terminate()
                        break
                    self._fetcher.observe(line)
                    self._line_q.put(line)
                rc = self._current_proc.wait()
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
        finally:
            # Drain the Fetcher first so every well's CSV + zip lands
            # locally BEFORE we attempt auto-threshold against
            # local_output_dir.
            self._fetcher.flush_and_join()
            # v5.4.7: single auto-threshold pass over the FULL
            # downloaded output. The per-batch passes were suppressed
            # via ``--no_auto_threshold`` so that a batch wouldn't
            # write thresholds derived from a sliver of the plate.
            # Fires only when the whole run succeeded — a mid-run
            # abort leaves the previous run's pipeline_info.json (if
            # any) untouched.
            if self._returncode == 0:
                self._run_local_auto_threshold()
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
            from process_microscopy import (
                _estimate_thresholds_standalone,
                _apply_thresholds_to_pipeline_info,
            )
            # Parse the channel tokens out of the pipeline argv so we
            # don't have to re-thread them through __init__.
            fluor_tokens: list[str] = []
            schema = ""
            sep = "_"
            it = iter(self._pipeline_argv)
            for arg in it:
                if arg == "--fluor_tokens":
                    fluor_tokens = next(it, "").split()
                elif arg == "--filename_schema":
                    schema = next(it, "")
                elif arg == "--filename_sep":
                    sep = next(it, "_")
            import logging
            log_pylog = logging.getLogger("allwell.local_auto_threshold")
            self._log(
                "[remote] running auto-threshold pass locally over "
                f"{self._local_output_dir} (full plate, all batches drained)\n"
            )
            thresholds = _estimate_thresholds_standalone(
                self._local_output_dir,
                fluor_channels=fluor_tokens,
                filename_schema=schema,
                filename_sep=sep,
                log=log_pylog,
            )
            written = _apply_thresholds_to_pipeline_info(
                self._local_output_dir, thresholds, log_pylog,
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
        except Exception as exc:  # noqa: BLE001 — informational, never fatal
            self._log(
                f"[remote] warn: local auto-threshold pass failed: {exc}\n"
            )


class _QueueLineIterator:
    """Iterator that pulls lines off a queue; sentinel ``None`` ends
    iteration. Lets the Popen consumer treat us like a file."""

    def __init__(self, q: "queue.Queue[Optional[str]]"):
        self._q = q

    def __iter__(self) -> "_QueueLineIterator":
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
        self._pending: queue.Queue[Optional[str]] = queue.Queue()
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
        """
        pending_count = self._pending.qsize()
        if pending_count:
            self._log(
                f"[remote] ↓ draining fetcher: {pending_count} well(s) "
                f"still queued for download\n"
            )
        self._pending.put(None)
        self._worker.join(timeout=timeout)
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
            self._fetch_one(item)

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
        tmp_zip = Path(tempfile.gettempdir()) / f"allwell-{self.run.run_id}-{well}_out.zip"
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
        if not self._rsync(
            [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
             "-a", zip_src, to_rsync_path(tmp_zip)]
        ):
            self._log(
                f"[remote] ↓ {well}: WARN could not pull {well}_out.zip from "
                f"{self.run.remote_output_dir}; will retry at end of run.\n"
            )
            return

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
        csv_count = self._rsync_glob(csv_glob_src, self.run.local_output_dir)
        csv_elapsed = time.monotonic() - t1
        if csv_count > 0:
            self._log(
                f"[remote] ↓ {well}: {csv_count} CSV file(s) in "
                f"{_fmt_secs(csv_elapsed)}\n"
            )
        else:
            self._log(
                f"[remote] ↓ {well}: WARN no CSV files found at "
                f"{self.run.remote_output_dir}/*_{well}.csv\n"
            )

        # Now materialize the well's output in the user's output_dir
        # according to local input mode.
        self._materialize(well, tmp_zip)
        try:
            tmp_zip.unlink()
        except OSError:
            pass
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

    def _materialize(self, well: str, well_zip: Path) -> None:
        """Place the well's outputs in ``local_output_dir`` so the result
        matches what a local run would have produced."""
        dest = self.run.local_output_dir
        dest.mkdir(parents=True, exist_ok=True)

        if self.output_filter is OutputFilter.CSV_ONLY:
            # Nothing to extract — the CSV was pulled separately. Drop
            # the well zip on the floor.
            return

        if self.run.local_input_mode is LocalInputMode.FOLDER:
            # Local folder-mode runs produce loose QC files (the pipeline
            # decompresses on its own when --compress_output_well_folders
            # is off). For the remote case we always run in
            # compress-output mode (it's the simplest fetch unit), so
            # extract here to match.
            try:
                with zipfile.ZipFile(well_zip, "r") as zf:
                    zf.extractall(dest)
            except (OSError, zipfile.BadZipFile) as exc:
                self._log(f"[remote] warn: extracting {well}_out.zip failed: {exc}\n")
            return

        # Zip- or flat-mode local run: the zip itself IS the expected
        # output unit. Drop it in place.
        try:
            shutil.copyfile(well_zip, dest / f"{well}_out.zip")
        except OSError as exc:
            self._log(f"[remote] warn: copying {well}_out.zip failed: {exc}\n")

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
        result = _rsync_with_retry(argv, log_fn=self._log, stop_event=self._stop)
        return result.returncode == 0

    def _rsync_glob(self, src_glob: str, dest_dir: Path) -> int:
        """Pull files matching a glob. Wraps the source in a quoted-glob
        rsync so the remote side expands ``*``, not the local shell.

        v5.2.18: returns the count of files successfully pulled
        (was: bool). The caller logs the count so the user can see
        how many CSVs landed per well.
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
            return 0
        pulled = 0
        dest_str = to_rsync_path(dest_dir).rstrip("/") + "/"
        for f in files:
            if self._rsync(
                [resolve_rsync_binary(), *_rsync_ssh_transport(self.host),
                 "-a", f"{host}:{f}", dest_str]
            ):
                pulled += 1
        return pulled


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
        self._run: Optional[_RemoteRun] = None
        self._cache: DatasetCache = DatasetCache(host)

    @property
    def run(self) -> Optional[_RemoteRun]:
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
        self._cache._ssh_run(  # noqa: SLF001
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

        self._cache._ssh_run(  # noqa: SLF001 — short-circuit reuse of ssh helper
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
        remote_cmd = (
            f"echo $$ > {_remote_quote(remote_pidfile)}; "
            f"{_CONDA_ACTIVATE_SNIPPET}; "
            f"exec {_remote_join(remote_argv)}"
        )
        ssh_argv = self.host.ssh_argv(allocate_tty=True) + [remote_cmd]

        log(
            f"[remote] run_id={run_id}  cache_key={key[:8]}  "
            f"(upload {sent / (1024**2):.1f} MB in {_fmt_secs(elapsed)})\n"
            f"[remote] $ {_remote_join(remote_argv)}\n"
        )

        popen = subprocess.Popen(
            ssh_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
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
            self._cache._ssh_run(  # noqa: SLF001
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
                self._cache._ssh_run(  # noqa: SLF001
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
        result = self._cache._ssh_script(script, check=False)  # noqa: SLF001
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
        # Extract well IDs from the manifest messages. Format from
        # _verify_local_manifest: "missing CSV for A01" / "empty CSV
        # for A01". Match a two-letter+two-digit token.
        well_re = re.compile(r"\b([A-H]\d{2})\b")
        wells: set[str] = set()
        for m in missing_messages:
            match = well_re.search(m)
            if match:
                wells.add(match.group(1))
        if not wells:
            return

        log(f"[remote] end-of-run retry: re-pulling {len(wells)} well(s)…\n")
        host = self.host
        for well in sorted(wells):
            # Pull the per-well CSV (top-level on remote out dir).
            csv_glob_src = (
                f"{host.ssh_alias}:{run.remote_output_dir}/*_{well}.csv"
            )
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
                _rsync_with_retry(
                    [resolve_rsync_binary(), *_rsync_ssh_transport(host),
                     "-a", f"{host.ssh_alias}:{f}",
                     to_rsync_path(run.local_output_dir).rstrip("/") + "/"],
                    log_fn=log,
                )
            # Also re-pull the {well}_out.zip in case the user has a
            # full output filter — it's harmless under csv-only mode
            # since the temp zip gets discarded.
            _rsync_with_retry(
                [resolve_rsync_binary(), *_rsync_ssh_transport(host),
                 "-a",
                 f"{host.ssh_alias}:{run.remote_output_dir}/{well}_out.zip",
                 to_rsync_path(run.local_output_dir).rstrip("/") + "/"],
                log_fn=log,
            )

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
        want_zip_artifact = (
            run.output_filter is OutputFilter.FULL
            and run.local_input_mode in (LocalInputMode.ZIP, LocalInputMode.FLAT,
                                          LocalInputMode.MIXED)
        )
        want_folder_artifact = (
            run.output_filter is OutputFilter.FULL
            and run.local_input_mode is LocalInputMode.FOLDER
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
            elif want_folder_artifact:
                out_folder = run.local_output_dir / well
                if not out_folder.is_dir() or not any(out_folder.iterdir()):
                    missing.append(f"missing {well}/ output folder locally")
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
            listing = self._cache._ssh_script(  # noqa: SLF001
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

    def cancel(self, ssh_proc: subprocess.Popen) -> None:
        """Best-effort remote kill. The local pgid kill (handled by
        PipelineRunner) is the primary mechanism; this is the belt-and-
        braces second channel via a fresh ssh that ``kill``s the PID we
        wrote on startup."""
        if self._run is None:
            return
        run = self._run
        try:
            subprocess.run(
                self.host.ssh_argv(allocate_tty=False) + [
                    f"kill -TERM $(cat {_remote_quote(run.remote_pidfile)} 2>/dev/null) "
                    "2>/dev/null || true"
                ],
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

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
        out = [*_conda_run_prefix(), "python", remote_script]
        i = 2  # skip the interpreter + script slots
        while i < len(local_argv):
            arg = local_argv[i]
            if arg == "--input_dir":
                out.extend([arg, remote_input])
                i += 2
            elif arg == "--output_dir":
                out.extend([arg, remote_output])
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
