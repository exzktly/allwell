"""Cell-override persistence (``<data_dir>/cell_overrides.json``).

Stores Segmentation tab include/exclude patches for individual cells. Writes
are debounced via ``schedule_save`` to coalesce bursts of toggles into a
single disk write.

Schema v2 adds a per-well **segmentation fingerprint** so re-running the
pipeline (which re-allocates nucleus label IDs from 1) doesn't cause
overrides to silently re-bind to unrelated cells. The fingerprint is the
``<well>_out.zip`` mtime captured at the time the override was set; a
stored entry whose fingerprint no longer matches the on-disk zip is
discarded with a warning.

v1 files (no fingerprint) load with a warning telling the user their
overrides may be stale if the pipeline has been re-run since the
overrides were last saved.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from util.well_token import canonical_well_label
from well_viewer.data.image_identity import CellKey
from well_viewer.persistence._io import atomic_write_json

_logger = logging.getLogger("well_viewer")

_SCHEMA_VERSION = 2


def path_for(app) -> Path | None:
    if app._app_state.dataset.data_dir:
        return app._app_state.dataset.data_dir / "cell_overrides.json"
    return None


def _well_zip_fingerprints(app) -> dict[str, str]:
    """Snapshot the ``<well>_out.zip`` mtimes for every loaded well.

    Used both at save time (stored alongside each override) and at load
    time (compared against the file on disk). Keys are canonicalised
    (``a1_out.zip`` -> ``"A01"``) to match ``CellKey.well`` — the override
    map's own keys, which every well is normalised into via
    ``normalize_well``. An unpadded on-disk filename previously left this
    dict keyed by the raw, unpadded stem, so the lookup at both save and
    load time missed on an unpadded dataset; both sides missed the same
    way, so the staleness guard silently never fired instead of failing
    loudly — stale overrides were kept rather than dropped.
    """
    out: dict[str, str] = {}
    data_dir = app._app_state.dataset.data_dir
    if not data_dir:
        return out
    for zip_path in Path(data_dir).glob("*_out.zip"):
        stem = zip_path.stem
        well = canonical_well_label(stem[:-4]) if stem.endswith("_out") else None
        if not well:
            continue
        try:
            out[well] = f"{zip_path.stat().st_mtime_ns}"
        except OSError:
            pass
    return out


def save_to_data_dir(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    path = path_for(app)
    if path is None:
        return
    fingerprints = _well_zip_fingerprints(app)
    overrides = []
    for (well, fov, tp, nid), val in ctx.runtime.review.included_overrides.items():
        overrides.append({
            "well": str(well),
            "fov": str(fov),
            "tp": str(tp),
            "nucleus_id": str(nid),
            "included": str(val),
            # Stamp each row with the fingerprint of the well's zip at
            # the time the override was saved. A later re-run of the
            # pipeline will change this value and the row will be
            # dropped on next load.
            "seg_fp": fingerprints.get(str(well), ""),
        })
    payload = {
        "version": _SCHEMA_VERSION,
        "overrides": overrides,
    }
    try:
        atomic_write_json(path, payload)
    except OSError as exc:
        _logger.warning("Failed to save cell overrides to %s: %s", path, exc)


def load_from_data_dir(ctx) -> None:
    app = ctx.window  # transitional bridge (B4)
    path = path_for(app)
    if path is None or not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Failed to load cell overrides from %s: %s", path, exc)
        return
    overrides = data.get("overrides") if isinstance(data, dict) else None
    if not isinstance(overrides, list):
        return
    file_version = 1
    try:
        file_version = int(data.get("version", 1))
    except (TypeError, ValueError):
        pass

    fingerprints = _well_zip_fingerprints(app)
    # Build the new map locally — only commit to in-memory state on success
    # so a broken / empty file doesn't wipe unsaved edits.
    new_map: dict = {}
    dropped_stale = 0
    legacy_unfingerprinted = 0
    for entry in overrides:
        if not isinstance(entry, dict):
            continue
        well = str(entry.get("well", "")).strip()
        fov = str(entry.get("fov", "")).strip()
        tp = str(entry.get("tp", "")).strip()
        nid = str(entry.get("nucleus_id", "")).strip()
        inc = str(entry.get("included", "1")).strip() or "1"
        stored_fp = str(entry.get("seg_fp", "")).strip()
        if not (well and fov and tp and nid):
            continue
        if stored_fp:
            current_fp = fingerprints.get(well, "")
            if current_fp and stored_fp != current_fp:
                # Pipeline re-run since this override was saved — nucleus
                # IDs no longer match the original segmentation. Drop.
                dropped_stale += 1
                continue
        else:
            legacy_unfingerprinted += 1
        # Build the lookup key via CellKey — the single source of truth.
        # Previously this strip-only path silently disagreed with the
        # smFISH writer's empty-FOV → "1" rule, so on single-FOV datasets
        # the override was unreachable. CellKey applies the same chain
        # both sides use.
        key = CellKey.from_row(
            {"fov": fov, "timepoint": tp, "nucleus_id": nid}, well=well,
        )
        if not (key.fov and key.tp and key.nid):
            continue
        new_map[key.as_tuple()] = inc
    if dropped_stale:
        _logger.warning(
            "cell_overrides: dropped %d override(s) whose segmentation "
            "fingerprint no longer matches the on-disk well zip (likely a "
            "pipeline re-run since the overrides were saved).",
            dropped_stale,
        )
    if legacy_unfingerprinted and file_version < _SCHEMA_VERSION:
        _logger.warning(
            "cell_overrides: %d v1 override(s) loaded without a segmentation "
            "fingerprint — they may bind to unrelated cells if the pipeline "
            "has been re-run since they were saved. Save to upgrade.",
            legacy_unfingerprinted,
        )
    if not new_map:
        # Refuse to wipe a populated in-memory state with a no-op load.
        return
    ctx.runtime.review.included_overrides.clear()
    ctx.runtime.review.included_overrides.update(new_map)
    # Bump the include-map version so Review Image recomputes; skipped when
    # Image Browser is disabled (no review_image slice).
    _review_image = app._app_state.feature_slice("review_image")
    if _review_image is not None:
        _review_image.override_version += 1
