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

from pipeline.io import scratch_base_for

#: Bumped when the checkpoint payload's shape changes, so a run never reads a
#: record it would misinterpret.
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
    changing means the intermediates are not this run's to reuse.
    """
    stamp = hashlib.blake2b(
        f"{Path(output_dir).resolve()}|{settings_key}".encode(),
        digest_size=6).hexdigest()
    return scratch_base_for(output_dir) / f"_tmp_images_{stamp}_{well_label}"

def _checkpoint_paths(tmp_images: Path, fov_id: str) -> tuple[Path, Path]:
    """Return ``(records_json, done_marker)`` for a given FOV id."""
    return (
        tmp_images / f".fov_{fov_id}.records.json",
        tmp_images / f".fov_{fov_id}.done",
    )

def _load_fov_checkpoint(tmp_images: Path, fov_id: str,
                         settings_key: str = "") -> list[dict] | None:
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
    # Belt as well as braces. The staging directory's name already keys on the
    # run, so a mismatch here should be unreachable — but this is the file whose
    # contents become CSV rows, and a stale one is not a slow run, it is another
    # plate's numbers published under this plate's well labels. An empty
    # recorded value is a checkpoint written before this existed and is
    # accepted, matching how the per-well settings marker grandfathers.
    recorded = str(payload.get("settings") or "")
    if recorded and settings_key and recorded != settings_key:
        return None
    records = payload.get("records")
    if not isinstance(records, list):
        return None
    return records

def _save_fov_checkpoint(
    tmp_images: Path, fov_id: str, records: list[dict],
    settings_key: str = "",
) -> None:
    """Atomically persist ``records`` and create the ``.done`` marker.

    Writes records.json first, fsync-then-rename, then touches the marker
    — so a process killed between the two writes leaves NO marker and
    the next run reprocesses the FOV (rather than reading half-written
    records).
    """
    records_file, marker = _checkpoint_paths(tmp_images, fov_id)
    tmp = records_file.with_suffix(".records.json.tmp")
    payload = {"schema": _FOV_CHECKPOINT_SCHEMA,
               "settings": str(settings_key or ""),
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
            # pipeline/io.py and well_viewer/persistence/_io.py.
            pass
    tmp.replace(records_file)
    # The marker is the atomicity boundary — present iff records_file
    # is on disk and complete.
    marker.touch()
