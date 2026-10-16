"""The ``pipeline_info.json`` blocks that belong to the user, not to a run.

Most of the sidecar describes how a run was produced — schema, channel tokens,
segmentation parameters — and every run rewrites it. A few blocks describe what
the *user* decided afterwards in Review: cell-gating thresholds, saved sample
definitions, ratios, notes. Those have no author on the pipeline side, so any
code that replaces the sidecar wholesale destroys them unless it carries them
across first.

Two places replace it wholesale, and they used to disagree about this:
``process_microscopy.write_pipeline_info`` (a re-run against the same output dir)
carried the blocks over, while the remote runner's end-of-run fetch (which rsyncs
the remote sidecar over the local one) carried only ``run_report``. A remote run
therefore threw away thresholds the local auto-threshold pass had just written,
along with any sample definitions, ratios and notes the user had saved before
re-running. One list, imported by both, is what keeps them from drifting again.

Qt-free and stdlib-only: the pipeline imports it, so it must stay inside the
pipeline's own dependency set (see the §6.2 self-sufficiency contract), and it is
listed in ``services.remote_runner._REQUIRED_REMOTE_FILES``.
"""

from __future__ import annotations

import json
import shutil
from util.aw_safe import swallow
from util.json_write import write_json_atomic
from pathlib import Path

#: Sidecar keys written by the Review side and owned by the user. Anything that
#: rewrites ``pipeline_info.json`` from run state must preserve these.
#:
#: ``run_report`` and ``run_check`` are deliberately **not** here, and the
#: instinct to add them should be resisted. Both describe one run's output, so
#: carrying them across a rewrite means a run that died before it could describe
#: itself keeps the *previous* run's description — a Home page reporting a plate
#: as 96/96 complete when the run that just finished wrote four wells. Losing a
#: record is recoverable and visible; inheriting a stale one is neither.
USER_SIDE_BLOCKS: tuple[str, ...] = (
    "cell_gating",
    "sample_definitions",
    "ratios",
    "notes",
)


def read_user_blocks(info_path) -> dict:
    """The user-side blocks currently in *info_path*, or ``{}``.

    Missing, unreadable or non-dict sidecars yield an empty mapping — this is a
    best-effort rescue, and failing it must never take a run down with it.
    """
    path = Path(info_path)
    if not path.exists():
        return {}
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(info, dict):
        return {}
    return {k: info[k] for k in USER_SIDE_BLOCKS if k in info}


def restore_user_blocks(info_path, prior: dict | None) -> list[str]:
    """Fold *prior*'s blocks back into a sidecar that was just overwritten.

    Only blocks the new file does **not** already carry are restored, so a run
    that legitimately produced its own (the auto-threshold pass writing
    ``cell_gating``) keeps it. Returns the key names actually restored, so the
    caller can say so rather than doing it silently.
    """
    if not prior:
        return []
    path = Path(info_path)
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(info, dict):
        return []
    restored = [k for k, v in prior.items()
                if k in USER_SIDE_BLOCKS and k not in info and v is not None]
    if not restored:
        return []
    for k in restored:
        info[k] = prior[k]
    try:
        write_json_atomic(path, info)
    except OSError:
        return []
    return restored


def install_provisional_sidecar(tmp: Path, dest: Path, *, run_id: str) -> None:
    """Stamp *tmp* as mid-run and put it at *dest*, unless *dest* appeared.

    A remote run pulls ``pipeline_info.json`` as soon as the worker has written
    one, so the plate can be opened in Review while the wells are still
    arriving. What arrives describes only the input *that* run was handed — for
    a batched run, one batch's split — so its ``available_timepoints`` and
    ``available_fovs`` can be short of the plate and there is no run report at
    all. ``verify_and_cleanup`` replaces the file wholesale at the end.

    The stamp is in the artifact rather than only in the run log because the
    artifact is what outlives the session: a run that is stopped, or whose app
    is closed, leaves this file behind as the dataset's only description of
    itself, and it describes less than a finished one does. A run that never
    reached the end therefore keeps the mark, which is the honest outcome.
    """
    info = json.loads(tmp.read_text())
    if not isinstance(info, dict):
        raise ValueError("pipeline_info.json is not a JSON object")
    info["provisional"] = {
        "source": "mid_run_fetch",
        "run_id": run_id,
        "note": (
            "Fetched while the run was still going, so it describes only "
            "the input this run was handed — for a batched run, one "
            "batch's split. available_timepoints / available_fovs may be "
            "short of the plate and there is no run_report. The finished "
            "sidecar replaces this file at the end of the run."
        ),
    }
    from pipeline.io import _safe_atomic_text_write

    if dest.exists():              # a re-run's own sidecar, or Review's saves
        return
    with _safe_atomic_text_write(dest) as fh:
        json.dump(info, fh, indent=2)
    with swallow("mid-run sidecar temp cleanup"):
        tmp.unlink()


def preserve_existing_user_blocks(p, info, *, logger):
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
            logger.error("existing %s unreadable (%s); backed up to %s",
                      p.name, exc, backup if backup is not None else "(backup failed)")
            existing = None
        if isinstance(existing, dict):
            for preserved_key in USER_SIDE_BLOCKS:
                if preserved_key in existing and preserved_key not in info:
                    info[preserved_key] = existing[preserved_key]


def check_acquisition_inputs(input_dir, schema, sep):
    """Read and check acquisition metadata before local or remote image work."""
    from util.acquisition_record import (
        AcquisitionRecordError, RECORD_FILENAME, read_record_file, validate_record_paths,
    )
    record = read_record_file(Path(input_dir) / RECORD_FILENAME)
    if record is None:
        return
    validation = validate_record_paths(Path(input_dir).glob("*.zip"), record, schema=schema, sep=sep)
    if not validation.ok:
        # Said with the way out: the record was written for the archives a conversion (or a Datasets tool)
        # produced, and archives added or removed by hand since make it describe a different dataset. Running on
        # anyway would place tiles from positions that belong to other images.
        raise AcquisitionRecordError(
            "; ".join(validation.issues) + f". {RECORD_FILENAME} no longer matches the well archives in "
            f"{Path(input_dir).name}/: if they were changed by hand, convert the plate again or use Datasets → "
            f"Subset to drop wells, or delete {RECORD_FILENAME} to run without it")
