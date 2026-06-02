"""Persistence for the cell-tracking sidecar (``tracks.json``).

One ``tracks.json`` per data dir holds a ``{well: payload}`` map (each payload
from :func:`features.tracking.model.tracks_json_payload`). Each well's payload
carries the ``<well>_out.zip`` fingerprint it was computed against; on load a
well whose fingerprint no longer matches the file on disk is dropped (a pipeline
re-run invalidates stale tracks) — the ``cell_overrides`` staleness pattern.

The Qt-free :func:`save_tracks` / :func:`load_tracks` are the testable core;
:func:`save_to_data_dir` / :func:`load_from_data_dir` are the app wrappers
registered with the persistence registry.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

from ._io import atomic_write_json

_logger = logging.getLogger("well_viewer")

TRACKS_FILENAME = "tracks.json"
SCHEMA_VERSION = 1


def path_for_data_dir(data_dir) -> "Optional[Path]":
    if not data_dir:
        return None
    return Path(data_dir) / TRACKS_FILENAME


# ── Qt-free core ─────────────────────────────────────────────────────────────

def save_tracks(data_dir, wells: Dict[str, dict]) -> "Optional[Path]":
    """Write ``{well: payload}`` to ``<data_dir>/tracks.json`` atomically."""
    path = path_for_data_dir(data_dir)
    if path is None:
        return None
    payload = {"version": SCHEMA_VERSION, "wells": dict(wells or {})}
    try:
        atomic_write_json(path, payload)
    except OSError as exc:
        _logger.warning("Failed to save tracks to %s: %s", path, exc)
        return None
    return path


def load_tracks(data_dir, *, current_fingerprints: "Optional[Dict[str, str]]" = None) -> Dict[str, dict]:
    """Load the ``{well: payload}`` map, dropping wells whose stored
    ``<well>_out.zip`` fingerprint no longer matches *current_fingerprints*.

    A missing/unreadable file yields ``{}``. When *current_fingerprints* is
    ``None`` no staleness check is applied (every stored well is returned).
    """
    path = path_for_data_dir(data_dir)
    if path is None or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("Failed to load tracks from %s: %s", path, exc)
        return {}
    wells = data.get("wells") if isinstance(data, dict) else None
    if not isinstance(wells, dict):
        return {}
    if current_fingerprints is None:
        return wells
    fresh: Dict[str, dict] = {}
    for well, payload in wells.items():
        stored = (payload.get("fingerprints", {}) or {}).get(well, "") if isinstance(payload, dict) else ""
        current = current_fingerprints.get(well, "")
        # Keep when no fingerprint was recorded (best-effort) or it still matches.
        if not stored or stored == current:
            fresh[well] = payload
        else:
            _logger.info("Dropping stale tracks for well %s (segmentation changed).", well)
    return fresh


# ── App wrappers (registered with the persistence registry) ──────────────────

def _data_dir(app):
    return getattr(getattr(getattr(app, "_app_state", None), "dataset", None), "data_dir", None)


def _well_zip_fingerprints(app) -> Dict[str, str]:
    out: Dict[str, str] = {}
    data_dir = _data_dir(app)
    if not data_dir:
        return out
    for zip_path in Path(data_dir).glob("*_out.zip"):
        well = zip_path.stem.replace("_out", "")
        try:
            out[well] = f"{zip_path.stat().st_mtime_ns}"
        except OSError:
            pass
    return out


def save_to_data_dir(app) -> None:
    wells = getattr(app, "_tracks_by_well", None)
    if not wells:
        return
    save_tracks(_data_dir(app), wells)


def load_from_data_dir(app) -> None:
    fresh = load_tracks(_data_dir(app), current_fingerprints=_well_zip_fingerprints(app))
    # PR5b populates the GUI from this; storing it is always safe.
    app._tracks_by_well = fresh
