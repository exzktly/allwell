"""Consolidated viewer-state document (``<data_dir>/persistence.json``).

Holds one section per viewer-owned persistence domain so the data directory
carries a single file instead of a pile of small sidecars. Houses ``ratios``,
``heatmap_layouts``, ``line_order``, ``plot_groups``, ``export_groups`` (the
Batch Export tab's per-panel groups), ``view_state`` (added by ``view_state.py``
for live UI selections), and ``qc_params``.
``cell_overrides`` stays in its own file (size + write-frequency);
``pipeline_info.json`` is owned by the batch pipeline and is left alone.

Migration: on first read, any raw-format legacy sidecars (see ``LEGACY_SIDECARS``)
present in the data directory are absorbed into a fresh ``persistence.json`` and
then deleted. ``qc_params`` migrates its own legacy ``qc_params.json`` (which
wraps its data) separately, in ``qc_params.py``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from well_viewer.persistence._io import atomic_write_json

_logger = logging.getLogger("well_viewer")

PERSISTENCE_FILENAME = "persistence.json"
SCHEMA_VERSION = 1

# Legacy sidecar filename → section key.
LEGACY_SIDECARS: dict[str, str] = {
    "ratios.json": "ratios",
    "heatmap_layouts.json": "heatmap_layouts",
    "line_order.json": "line_order",
}


def path_for(app) -> Optional[Path]:
    if app._app_state.dataset.data_dir:
        return Path(app._app_state.dataset.data_dir) / PERSISTENCE_FILENAME
    return None


def delete(app) -> bool:
    """Delete the dataset's ``persistence.json`` and drop the in-memory cache.

    This wipes every persistence.json-backed domain (view state, per-row LUTs,
    crop, plot groups, heatmap layouts, cell overrides, line order, ratios, QC
    params) for the active data dir. The dataset's own files — including
    ``pipeline_info.json`` (sample-definition labels) — are left untouched.
    Returns ``True`` when a file was removed. Caller reloads the dataset.
    """
    p = path_for(app)
    app._app_state.dataset.persistence_doc = None
    if p is None or not p.exists():
        return False
    try:
        p.unlink()
        return True
    except OSError:
        _logger.warning("Failed to delete %s", p, exc_info=True)
        return False


def read(app) -> dict:
    """Return the cached persistence doc, populating from disk on first call.

    When ``persistence.json`` is missing but legacy sidecars are present, the
    legacy files are migrated into a freshly written doc and then deleted.
    Returns an empty dict when there's no data directory at all.
    """
    cached = app._app_state.dataset.persistence_doc
    if cached is not None:
        return cached
    p = path_for(app)
    if p is None:
        return {}
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            _logger.warning("Failed to read %s: %s", p, exc)
            doc = {}
        if not isinstance(doc, dict):
            _logger.warning("%s is not a JSON object; ignoring.", p)
            doc = {}
        sv = doc.get("schema_version")
        if sv is not None and sv != SCHEMA_VERSION:
            _logger.warning(
                "%s schema_version=%r (expected %d); applying defaults.",
                p, sv, SCHEMA_VERSION,
            )
        # Legacy sidecars alongside an existing persistence.json shouldn't
        # happen, but if they do, prefer the consolidated doc.
        _warn_about_orphan_legacy(p.parent)
    else:
        doc = _migrate_from_legacy(p)
    doc.setdefault("schema_version", SCHEMA_VERSION)
    app._app_state.dataset.persistence_doc = doc
    return doc


def write(app, doc: dict) -> None:
    """Atomically replace the on-disk doc and refresh the cache."""
    p = path_for(app)
    if p is None:
        return
    doc.setdefault("schema_version", SCHEMA_VERSION)
    try:
        atomic_write_json(p, doc)
    except OSError as exc:
        _logger.warning("Failed to write %s: %s", p, exc)
        return
    app._app_state.dataset.persistence_doc = doc


def get_section(app, key: str) -> Any:
    return read(app).get(key)


def set_section(app, key: str, value: Any) -> None:
    """Mutate one section and flush the whole doc to disk."""
    doc = read(app)
    if value is None:
        doc.pop(key, None)
    else:
        doc[key] = value
    write(app, doc)


def _migrate_from_legacy(persistence_path: Path) -> dict:
    """Roll any legacy sidecars in *persistence_path.parent* into a fresh doc.

    Successfully migrated legacy files are deleted only after the consolidated
    doc has been written, so a crash mid-migration leaves the legacy files
    intact for a retry next time.
    """
    data_dir = persistence_path.parent
    doc: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    migrated: list[Path] = []
    for filename, section in LEGACY_SIDECARS.items():
        legacy = data_dir / filename
        if not legacy.exists():
            continue
        try:
            with open(legacy, "r", encoding="utf-8") as fh:
                doc[section] = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            _logger.warning("Skipping legacy %s during migration: %s", legacy, exc)
            continue
        migrated.append(legacy)
    if not migrated:
        return doc
    try:
        atomic_write_json(persistence_path, doc)
    except OSError as exc:
        _logger.warning(
            "Failed to write %s during migration: %s (legacy files left in place)",
            persistence_path, exc,
        )
        return doc
    for legacy in migrated:
        try:
            legacy.unlink()
        except OSError as exc:
            _logger.warning("Could not remove legacy %s: %s", legacy, exc)
    _logger.info(
        "Migrated %d legacy sidecar(s) into %s",
        len(migrated), persistence_path.name,
    )
    return doc


def _warn_about_orphan_legacy(data_dir: Path) -> None:
    for filename in LEGACY_SIDECARS:
        if (data_dir / filename).exists():
            _logger.warning(
                "%s exists alongside %s; the legacy file will be ignored.",
                filename, PERSISTENCE_FILENAME,
            )
