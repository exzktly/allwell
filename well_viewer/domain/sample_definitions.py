"""Save / load Sample Definitions (well labels + replicate sets + groups)
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
from typing import Any, Dict, Iterable, Optional

from well_viewer.domain.bar_groups import bar_groups_from_data, bar_groups_to_dict
from well_viewer.domain.batch_models import BarGroup, ReplicateSet
from well_viewer.domain import selections_model as _sel_model


_logger = logging.getLogger("well_viewer.domain.sample_definitions")

PIPELINE_INFO_FILENAME = "pipeline_info.json"
SAMPLE_DEFINITIONS_KEY = "sample_definitions"
PRE_V2_BACKUP_SUFFIX = ".pre-v2-backup"


def _resolve_pipeline_info_path(out_dir: Path) -> Path:
    """Return the pipeline_info.json path, falling back to parent dir.

    Mirrors ``viewer_state.read_pipeline_info`` so save and load look in
    the same place.
    """
    primary = out_dir / PIPELINE_INFO_FILENAME
    if primary.exists():
        return primary
    parent = out_dir.parent / PIPELINE_INFO_FILENAME
    if parent.exists():
        return parent
    return primary  # may not exist; caller decides


def build_sample_definitions_v2(
    well_labels: Dict[str, str],
    selections: Iterable[Dict[str, Any]],
    current_id: Optional[str] = None,
    *,
    notes: str = "",
) -> Dict[str, Any]:
    """Snapshot the unified ``selections`` state as a JSON-friendly v2 block.

    (Schema v2 — ``schema_version: 2`` + ``selections`` + ``current_id``; the
    legacy ``rep_sets`` / ``groups`` keys are intentionally absent.)
    """
    return _sel_model.to_block(list(selections or []), well_labels, notes, current_id)


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
    sample_definitions: Dict[str, Any],
) -> Path:
    """Merge ``sample_definitions`` into the pipeline_info.json sidecar.

    Existing keys (schema, fov_index, fluor_tokens, …) are preserved
    verbatim. The ``sample_definitions`` key is replaced. When the *new* block
    is schema-v2 and the *old* one on disk was v1, a one-time
    ``pipeline_info.json.pre-v2-backup`` is written first; if that backup can't
    be written the save is aborted (OSError).

    Raises FileNotFoundError when the pipeline file does not yet exist —
    the caller should run the pipeline first so the file gets created.
    """
    info_path = _resolve_pipeline_info_path(out_dir)
    if not info_path.exists():
        raise FileNotFoundError(
            f"{PIPELINE_INFO_FILENAME} was not found in {out_dir} "
            f"(or its parent). Run the pipeline first to create it."
        )
    try:
        existing = json.loads(info_path.read_text())
        if not isinstance(existing, dict):
            raise ValueError(f"{info_path} is not a JSON object.")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise OSError(f"Could not read {info_path}: {exc}") from exc

    old_block = existing.get(SAMPLE_DEFINITIONS_KEY)
    writing_v2 = _sel_model.block_is_v2(sample_definitions)
    old_was_v1 = isinstance(old_block, dict) and not _sel_model.block_is_v2(old_block)
    if writing_v2 and old_was_v1:
        try:
            _backup_pre_v2(info_path)
        except OSError as exc:
            raise OSError(
                f"Refusing to save: couldn't write a pre-upgrade backup of "
                f"{info_path.name} ({exc})."
            ) from exc

    existing[SAMPLE_DEFINITIONS_KEY] = sample_definitions
    tmp = info_path.with_suffix(info_path.suffix + ".tmp")
    tmp.write_text(json.dumps(existing, indent=2))
    tmp.replace(info_path)
    return info_path


def read_sample_definitions(out_dir: Path) -> Optional[Dict[str, Any]]:
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


