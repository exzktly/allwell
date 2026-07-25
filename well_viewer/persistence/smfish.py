"""Per-channel smFISH threshold persistence (within ``pipeline_info.json``).

The smFISH spot-detection threshold is the value that was last applied
globally to the dataset (Apply Global Threshold). Persisting it makes
the spinbox's reading meaningful across sessions — the value the user
sees matches the spot counts already baked into the per-well CSVs.

Schema written under the ``smfish_thresholds`` key:

    {"w3cy5": 1500.0, "w4gfp": 1200.0}

Only channels with a saved value appear in the block. The block is
removed entirely when no values remain.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional  # Any used in save_threshold

from well_viewer.persistence._io import atomic_write_json

_logger = logging.getLogger("well_viewer")

_PIPELINE_INFO_FILENAME = "pipeline_info.json"
_SMFISH_THRESHOLDS_KEY = "smfish_thresholds"


def _resolve_pipeline_info_path(out_dir: Path) -> Path:
    primary = out_dir / _PIPELINE_INFO_FILENAME
    if primary.exists():
        return primary
    parent = out_dir.parent / _PIPELINE_INFO_FILENAME
    if parent.exists():
        return parent
    return primary


def _read_block(out_dir: Path) -> Dict[str, float]:
    info_path = _resolve_pipeline_info_path(out_dir)
    if not info_path.exists():
        return {}
    try:
        data = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Could not read %s: %s", info_path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get(_SMFISH_THRESHOLDS_KEY)
    if not isinstance(block, dict):
        return {}
    out: Dict[str, float] = {}
    for ch, val in block.items():
        try:
            out[str(ch).strip().lower()] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def save_threshold(ctx, channel: str, threshold: float) -> None:
    """Persist the last-applied threshold for *channel* under
    ``smfish_thresholds`` in pipeline_info.json. Called from
    ``_smfish_apply_to_all``'s after-csv callback so the saved value is
    always the one the CSVs on disk reflect.

    When ``pipeline_info.json`` is absent (e.g. legacy datasets that
    weren't processed through the current pipeline, or datasets the
    user opened raw), the sidecar is CREATED with just the
    ``smfish_thresholds`` block — without this, every threshold the
    user applied during the session was silently dropped on close and
    the spinbox reverted to its hardcoded "1500" default on reopen.
    The pipeline merges with any pre-existing block on its next run
    so creating-on-missing is safe.

    Silent no-op when no data directory is loaded or the channel
    string is empty.
    """
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if not data_dir:
        _logger.info(
            "smFISH save_threshold: no data_dir loaded, skipping persistence",
        )
        return
    ch = str(channel).strip().lower()
    if not ch:
        _logger.info(
            "smFISH save_threshold: empty channel, skipping persistence",
        )
        return
    info_path = _resolve_pipeline_info_path(Path(data_dir))
    existing: Dict[str, Any]
    if info_path.exists():
        try:
            existing = json.loads(info_path.read_text())
            if not isinstance(existing, dict):
                _logger.warning("%s is not a JSON object; not updating.", info_path)
                return
        except (OSError, json.JSONDecodeError) as exc:
            _logger.warning("Could not read %s: %s", info_path, exc)
            return
    else:
        # Sidecar is missing — create a minimal one. The pipeline's next
        # run will merge with these keys, so this is non-destructive.
        existing = {}
        _logger.info(
            "smFISH save_threshold: %s did not exist; creating it with "
            "only the smfish_thresholds block (the pipeline will merge "
            "with this on its next run).",
            info_path,
        )

    block = existing.get(_SMFISH_THRESHOLDS_KEY)
    if not isinstance(block, dict):
        block = {}
    block[ch] = float(threshold)
    existing[_SMFISH_THRESHOLDS_KEY] = block

    # Mirror onto the in-memory cache so a subsequent channel switch
    # restores the just-saved value without a round-trip to disk.
    saved = ctx.runtime.smfish.thresholds_saved
    if saved is None:
        saved = {}
        ctx.runtime.smfish.thresholds_saved = saved
    saved[ch] = float(threshold)

    try:
        atomic_write_json(info_path, existing)
        _logger.info(
            "smFISH save_threshold: wrote %s=%g to %s",
            ch, float(threshold), info_path,
        )
    except OSError as exc:
        _logger.warning("Could not write %s: %s", info_path, exc)


def load_from_pipeline_info(ctx) -> bool:
    """Read saved per-channel thresholds into
    ``app._smfish_thresholds_saved`` and, if the smFISH spinbox exists
    and the currently-selected channel has a saved value, write that
    value into the spinbox. Returns True when any value was applied to
    the UI.
    """
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if not data_dir:
        return False
    block = _read_block(Path(data_dir))
    ctx.runtime.smfish.thresholds_saved = dict(block)
    if not block:
        return False
    return apply_saved_to_spinbox(ctx)


def apply_saved_to_spinbox(ctx) -> bool:
    """If the smFISH tab is built, restore the spinbox to the saved
    threshold for the currently-selected channel. Returns True when a
    value was applied. Safe to call from the channel-change handler."""
    cb = ctx.runtime.smfish.channel_cb
    edit = ctx.runtime.smfish.threshold_edit
    if cb is None or edit is None:
        return False
    saved: Dict[str, float] = ctx.runtime.smfish.thresholds_saved or {}
    if not saved:
        return False
    ch = str(cb.currentText() or "").strip().lower()
    val = saved.get(ch)
    if val is None:
        return False
    # Block signals so re-emitting editingFinished doesn't re-trigger
    # _smfish_redraw before the FOV/TP combos finish updating.
    blocked = edit.blockSignals(True)
    try:
        edit.setText(str(float(val)))
    finally:
        edit.blockSignals(blocked)
    return True


def get_saved_threshold(ctx, channel: str) -> Optional[float]:
    """Look up the saved threshold for *channel* without touching the UI."""
    saved: Dict[str, float] = ctx.runtime.smfish.thresholds_saved or {}
    return saved.get(str(channel).strip().lower())
