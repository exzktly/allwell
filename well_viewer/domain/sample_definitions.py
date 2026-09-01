"""Save / load the sample definitions (well labels + replicate sets + groups)
inside ``pipeline_info.json``.

The pipeline writes pipeline_info.json as part of every analysis run. This
module lets the viewer overlay user-curated sample metadata onto that file
without disturbing the pipeline-owned keys: it reads the existing JSON,
sets / replaces the ``sample_definitions`` block, and writes the file back.
On load, ``read_sample_definitions`` returns the saved block (or None) so
the runtime app can re-apply it.

Schema written under the ``sample_definitions`` key:

    {
        "well_labels": {"A01": "Treated 0nM", "B05": "Control", ...},
        "rep_sets":    [{"name": "Rep 1", "wells": ["A01", "A02"]}, ...],
        "groups":      [{"name": "Control", "hidden": false,
                          "members": ["Rep 1"],
                          "solo_wells": ["B05"]}, ...],
    }

This mirrors the format already used by the standalone bar_groups.json
loader so the same parser (``barplot_controller.bar_groups_from_data``)
can hydrate the in-memory state.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from well_viewer.domain import selections_model as _sel_model
from well_viewer.domain.viewer_state import resolve_pipeline_info_path
from well_viewer.persistence._io import defer_io, update_json_atomic


_logger = logging.getLogger("well_viewer.domain.sample_definitions")

PIPELINE_INFO_FILENAME = "pipeline_info.json"
SAMPLE_DEFINITIONS_KEY = "sample_definitions"
PRE_V2_BACKUP_SUFFIX = ".pre-v2-backup"


# The sidecar lives at the dataset root even when the data dir is its
# ``out/`` child, so save and load have to agree on the fallback.
_resolve_pipeline_info_path = resolve_pipeline_info_path


def build_sample_definitions_v2(
    well_labels: dict[str, str],
    selections: Iterable[dict[str, Any]],
    current_id: str | None = None,
    *,
    notes: str = "",
    plot_groups: Any = None,
) -> dict[str, Any]:
    """Snapshot the unified ``selections`` state as a JSON-friendly v2 block.

    (Schema v2 — ``schema_version: 2`` + ``selections`` + ``current_id``; the
    legacy ``rep_sets`` / ``groups`` keys are intentionally absent.)
    """
    return _sel_model.to_block(
        list(selections or []), well_labels, notes, current_id, plot_groups,
    )


def _backup_pre_v2(info_path: Path) -> None:
    """Copy ``info_path`` verbatim to ``…pre-v2-backup`` before a v1→v2 write.

    No-clobber: the *first* backup is the precious one; a second migration goes
    to a timestamped sibling. Raises OSError if the backup can't be written
    (the caller must then abort the save rather than destroy the only old copy).

    Implemented with ``os.O_CREAT | os.O_EXCL`` for the timestamped fallback
    so two threads / processes racing through migration can't both think
    they're writing the "first" backup and overwrite each other's.
    """
    primary = info_path.with_name(info_path.name + PRE_V2_BACKUP_SUFFIX)
    if not primary.exists():
        shutil.copy2(info_path, primary)
        _logger.info("sample_definitions: migrated v1→v2; backup written to %s", primary)
        return
    # Timestamped fallback: open with O_EXCL so a race doesn't clobber a
    # backup another process just wrote.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = info_path.with_name(f"{info_path.name}{PRE_V2_BACKUP_SUFFIX}.{ts}")
    try:
        fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        # Another writer beat us to it within the same second — that backup
        # is just as good as the one we would have written.
        return
    try:
        with os.fdopen(fd, "wb") as dst, open(info_path, "rb") as src:
            shutil.copyfileobj(src, dst)
    except Exception:
        # Best-effort cleanup of an incomplete backup file.
        try:
            backup.unlink()
        except OSError:
            pass
        raise
    _logger.info("sample_definitions: migrated v1→v2; backup written to %s", backup)


def save_to_pipeline_info(
    out_dir: Path,
    sample_definitions: dict[str, Any],
    *,
    deferrable: bool = False,
) -> Path:
    """Merge ``sample_definitions`` into the pipeline_info.json sidecar.

    Existing keys (schema, fov_index, fluor_tokens, …) are preserved
    verbatim. The ``sample_definitions`` key is replaced. When the *new* block
    is schema-v2 and the *old* one on disk was v1, a one-time
    ``pipeline_info.json.pre-v2-backup`` is written first; if that backup can't
    be written the save is aborted (OSError).

    Raises FileNotFoundError when the pipeline file does not yet exist —
    the caller should run the pipeline first so the file gets created.

    *deferrable* hands the write, and the two checks that read its outcome, to
    the background writer when one is active (``_io.background_writes``, entered
    only by the debounced autosave path). It then **raises nothing**: the caller
    gets the path back immediately and a failure is reported by the writer —
    logged, and put on the status line — instead. Only the autosave passes it,
    and only because that caller already catches and logs every failure rather
    than showing it, so nothing is lost but the frame the report comes from. The
    explicit Save button leaves it False and keeps the exception its own
    callers key off.

    **The read-modify-write goes through**
    :func:`well_viewer.persistence._io.update_json_atomic`, which re-reads the
    file and compares it byte-for-byte against the copy the mutation was built
    from immediately before the replace, and re-applies the mutation when
    somebody else landed a write in between. ``pipeline_info.json`` has several
    writers, and the pipeline itself — often in another process, on another
    host — rewrites ``fluor_tokens``, ``schema`` and ``available_timepoints`` at
    the end of a run. Analyze and Review share one window, so that run can
    finish between this function's read and its write. A plain read-modify-write
    here reverted the pipeline's keys, and it did so silently: the viewer simply
    stopped being able to see a channel or a timepoint the run had produced.
    """
    info_path = _resolve_pipeline_info_path(out_dir)
    if not info_path.exists():
        raise FileNotFoundError(
            f"{PIPELINE_INFO_FILENAME} was not found in {out_dir} "
            f"(or its parent). Run the pipeline first to create it."
        )

    backup_failure: list[OSError] = []
    backed_up = False

    def _mutate(existing: dict) -> dict | None:
        # Runs against the bytes this attempt actually read, and may run again
        # if another writer lands in between — so the v1 test is made on the
        # content being replaced, not on a copy read earlier, and the backup is
        # taken at most once per call.
        nonlocal backed_up
        old_block = existing.get(SAMPLE_DEFINITIONS_KEY)
        writing_v2 = _sel_model.block_is_v2(sample_definitions)
        old_was_v1 = isinstance(old_block, dict) and not _sel_model.block_is_v2(old_block)
        if writing_v2 and old_was_v1 and not backed_up:
            try:
                _backup_pre_v2(info_path)
            except OSError as exc:
                # Abandon the update rather than destroy the only old copy;
                # the caller is told by the re-raise below.
                backup_failure.append(exc)
                return None
            backed_up = True
        existing[SAMPLE_DEFINITIONS_KEY] = sample_definitions
        return existing

    # ``durable=False``: this is the debounced sample-definitions autosave,
    # called on the GUI thread every time the user stops typing. An fsync on a
    # network share is a visible pause there, and the value is rewritten within
    # seconds anyway — what matters is that a reader never sees a half-written
    # file, which the rename gives regardless.
    def _write() -> tuple[bool, str]:
        # The write *and* the two checks that read its outcome, as one unit.
        # ``backup_failure`` and ``wrote`` are set by the mutation, so they can
        # only be read after it has run — which on the deferred path is on the
        # writer thread, long after this function has returned. Keeping them
        # inside the callable is what lets the whole thing move without the
        # v1→v2 backup's abort quietly becoming a no-op.
        wrote = update_json_atomic(info_path, _mutate, durable=False)
        if backup_failure:
            return False, (
                f"couldn't write a pre-upgrade backup of {info_path.name} "
                f"({backup_failure[0]}), so the save was abandoned rather than "
                f"destroy the only old copy")
        if not wrote:
            # update_json_atomic has already logged which of its refusals this
            # was (unreadable, not a JSON object, unwritable, or a writer it
            # could not get ahead of).
            return False, (
                f"could not save {SAMPLE_DEFINITIONS_KEY} into {info_path} — "
                f"see the log for which read or write step refused")
        return True, ""

    # *deferrable* is passed only by the debounced autosave, whose caller
    # already catches and logs every failure rather than showing it — so
    # handing the seven filesystem round trips to the background writer costs
    # no report, it only moves where the report comes from. The explicit Save
    # button leaves it False and still gets the exception its contract names.
    if deferrable and defer_io(_write):
        return info_path
    ok, detail = _write()
    if not ok:
        raise OSError(detail[0].upper() + detail[1:])
    return info_path


def read_sample_definitions(out_dir: Path) -> dict[str, Any] | None:
    """Return the saved sample_definitions block, or None when absent."""
    info_path = _resolve_pipeline_info_path(out_dir)
    if not info_path.exists():
        return None
    try:
        data = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Could not read %s: %s", info_path, exc)
        return None
    if not isinstance(data, dict):
        return None
    block = data.get(SAMPLE_DEFINITIONS_KEY)
    if not isinstance(block, dict):
        return None
    return block

