"""The per-FOV resume record: where one well's intermediates live, and whose.

Split out of `process_microscopy` because it is a self-contained cluster with
one job — deciding which staging directory a run owns and whether a checkpoint
inside it is this run's to read — and because the module it came from is under a
line budget that only moves down.

The keying is the whole point. `scratch_base_for` returns one directory for the
*machine*; the staging directory under it used to be named `_tmp_images_<WELL>`
with checkpoints keyed on the input filename stem, so nothing named the dataset,
the output directory, the run or the settings. Acquisition software writes the
same filename template for every plate, so the stems collided by default rather
than by accident, and one plate resumed another's per-cell measurements.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import logging

from pipeline.io import (
    _sweep_stale_scratch_dirs_all, remove_directory, scratch_base_for)
from util.aw_safe import swallow
from util.settings_digest import primary_settings_key, settings_key_aliases

log = logging.getLogger(__name__)

#: Bumped when the checkpoint payload's shape changes, so a run never reads a
#: record it would misinterpret.
#:
#: 1 → 2: the payload gained ``quality``, the FOV-group's field-quality
#: mapping. A version-1 checkpoint has the records but no quality, and a
#: resume that accepted it would produce a well whose quality block silently
#: covered fewer fields than the well holds — the plate-wide robust z is then
#: computed against a biased subset with nothing in the artifact saying so.
#: Refusing it costs one FOV of reprocessing and is the only answer that keeps
#: the block honest.
_FOV_CHECKPOINT_SCHEMA = 2


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

def _staging_dir_for(output_dir: Path, well_label: str,
                    settings_key: str) -> Path:
    """Where one well's intermediates live, keyed on *this* run.

    `scratch_base_for` returns one directory for the whole machine —
    `$ALLWELL_SCRATCH_DIR` or `~/.allwell/scratch` — and the staging dir under
    it used to be named `_tmp_images_<WELL>` with the checkpoints inside keyed
    on the input filename stem. Nothing in either named the dataset, the output
    directory, the run or the settings, and acquisition software writes the
    same filename template for every plate, so the stems collided by default
    rather than by accident: plate B resumed plate A's checkpoints and wrote
    plate A's per-cell measurements into plate B's CSV under plate B's well
    labels. Stop is SIGKILL, so the `finally` that removes this directory does
    not run and a stopped plate leaves the mine armed.

    The output directory and the settings both go into the name, because either
    changing means the intermediates are not this run's to reuse — as two
    *separate* components rather than one combined hash, so that
    :func:`prune_superseded_staging` can recognise this plate's own
    superseded directories without having to guess at another plate's.
    """
    base = scratch_base_for(output_dir)
    aliases = settings_key_aliases(settings_key) or ("",)
    here = base / f"_tmp_images_{_output_stamp(output_dir)}_{_settings_stamp(aliases[0])}_{well_label}"
    if not here.exists():
        # A directory an earlier build named after its own digest for these same options
        # (`util.settings_digest.legacy_settings_digests`) holds this well's resumable groups: take it over, so a
        # well half-finished on that build continues here rather than starting again.
        for alias in aliases[1:]:
            earlier = base / f"_tmp_images_{_output_stamp(output_dir)}_{_settings_stamp(alias)}_{well_label}"
            if earlier.is_dir():
                with swallow(f"taking over {earlier.name}"):
                    os.replace(earlier, here)
                    log.info("Resuming %s from the checkpoints an earlier build left in %s",
                             well_label, earlier.name)
                break
    return here


def _output_stamp(output_dir: Path) -> str:
    return hashlib.blake2b(
        str(Path(output_dir).resolve()).encode(), digest_size=6).hexdigest()


def _settings_stamp(settings_key: str) -> str:
    return hashlib.blake2b(
        primary_settings_key(settings_key).encode(), digest_size=4).hexdigest()


def prune_superseded_staging(output_dir: Path, settings_key: str) -> int:
    """Remove this plate's staging dirs left behind by *other* settings.

    Keying the staging directory on the run closed a collision and opened a
    leak. The name used to be ``_tmp_images_<WELL>``, so the next run of that
    well reused and eventually consumed it; now a run under different settings
    gets a different directory and the old one is orphaned. Stop is SIGKILL, so
    the ``finally`` that would have removed it never runs, and
    ``_sweep_stale_scratch_dirs`` deliberately *keeps* any directory holding
    ``.fov_*.done`` markers — which is exactly what a killed run leaves. A
    96-well plate stopped mid-run therefore stranded a plate's worth of
    intermediates in the shared scratch base per settings variation, for ever.

    Scoped to directories carrying *this output directory's* stamp, so it can
    never touch another plate's resumable work, and skipped entirely when the
    settings are unknown — an empty key names no run, and deleting on the
    strength of it would be guessing.

    Returns how many directories were removed.
    """
    if not settings_key:
        return 0
    # Every digest the key accepts: a directory an earlier build named for these same options is this run's.
    keep = tuple(_settings_stamp(alias) for alias in settings_key_aliases(settings_key))
    prefix = f"_tmp_images_{_output_stamp(output_dir)}_"
    removed = 0
    base = scratch_base_for(output_dir)
    with swallow(f"pruning superseded staging under {base}"):
        for stale in sorted(base.glob(f"{prefix}*")):
            if not stale.is_dir() or any(stale.name.startswith(f"{prefix}{k}_") for k in keep):
                continue
            log.info("Removing staging left by a previous settings "
                     "combination: %s", stale.name)
            remove_directory(stale)
            removed += 1
    return removed

def _checkpoint_paths(tmp_images: Path, fov_id: str) -> tuple[Path, Path]:
    """Return ``(records_json, done_marker)`` for a given FOV id."""
    return (
        tmp_images / f".fov_{fov_id}.records.json",
        tmp_images / f".fov_{fov_id}.done",
    )

def _load_fov_checkpoint(tmp_images: Path, fov_id: str,
                         settings_key: str = "") -> tuple[list[dict], dict] | None:
    """Return ``(records, quality)`` for ``fov_id``, or None if no valid
    checkpoint exists.

    Treats a missing marker, a missing records file, an unreadable
    records file, or a schema-version mismatch all as "no checkpoint" —
    in every case the caller will reprocess the FOV and overwrite the
    on-disk state.

    ``quality`` is the FOV-group's field-quality mapping, restored so a
    resumed well is measured as completely as one that ran straight through.
    It is a plain ``dict`` and may be empty, which is how a checkpoint written
    by a run whose group produced no quality reads; it is never ``None``, so a
    caller cannot confuse "no checkpoint" with "checkpoint with no quality".
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
    # Belt as well as braces. The staging directory's name already keys on the
    # run, so a mismatch here should be unreachable — but this is the file whose
    # contents become CSV rows, and a stale one is not a slow run, it is another
    # plate's numbers published under this plate's well labels. An empty
    # recorded value is a checkpoint written before this existed and is
    # accepted, matching how the per-well settings marker grandfathers.
    recorded = str(payload.get("settings") or "")
    if recorded and settings_key and recorded not in settings_key_aliases(settings_key):
        return None
    records = payload.get("records")
    if not isinstance(records, list):
        return None
    quality = payload.get("quality")
    if not isinstance(quality, dict):
        # The schema check above already refuses a version-1 payload, so this
        # is a version-2 file whose quality is malformed rather than absent.
        # An empty mapping keeps the tuple's shape; the well's ``measured``
        # count then falls short of ``measured_of`` and the block says partial,
        # which is exactly right — this field was not measured.
        quality = {}
    return records, quality

def _save_fov_checkpoint(
    tmp_images: Path, fov_id: str, records: list[dict],
    settings_key: str = "", quality: dict | None = None,
) -> None:
    """Atomically persist ``records`` + ``quality`` and create the ``.done``
    marker.

    Writes records.json first, fsync-then-rename, then touches the marker
    — so a process killed between the two writes leaves NO marker and
    the next run reprocesses the FOV (rather than reading half-written
    records).

    *quality* rides along rather than living in its own file because the two
    describe the same FOV-group and must be resumed or reprocessed together:
    stored apart, a kill between the two writes leaves records whose quality
    is missing, and the well then reports a field count it cannot back.
    """
    records_file, marker = _checkpoint_paths(tmp_images, fov_id)
    tmp = records_file.with_suffix(".records.json.tmp")
    payload = {"schema": _FOV_CHECKPOINT_SCHEMA,
               "settings": primary_settings_key(settings_key),
               "quality": _json_safe([dict(quality or {})])[0],
               "records": _json_safe(records)}
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
            # pipeline/io.py and review/persistence/_io.py.
            pass
    tmp.replace(records_file)
    # The marker is the atomicity boundary — present iff records_file
    # is on disk and complete.
    marker.touch()


def sweep_run_scratch(output_dir: Path, settings_key: str, *,
                      extra_extract: bool) -> None:
    """The pre-run scratch sweep, plus this plate's superseded staging.

    :func:`pipeline.io._sweep_stale_scratch_dirs_all` deliberately keeps any
    staging directory holding ``.fov_*.done`` markers, so that a killed run
    resumes from them. Once the directory name carries the run's settings, a
    run under different settings never reaches the old one again — so what
    protected a resume became a leak. This removes the ones this plate can no
    longer use, and leaves every other plate's alone.
    """
    _sweep_stale_scratch_dirs_all(output_dir, extra_extract=extra_extract)
    prune_superseded_staging(output_dir, settings_key)
