"""Consolidated viewer-state document (``<data_dir>/persistence.json``).

Holds one section per viewer-owned persistence domain so the data directory
carries a single file instead of a pile of small sidecars. Houses ``ratios``,
``heatmap_layouts``, ``line_order``, ``plot_groups``, ``export_groups`` (the
Batch Export tab's per-panel groups), ``view_state`` (added by ``view_state.py``
for live UI selections), and ``qc_params``.
``cell_overrides`` stays in its own file (size + write-frequency);
``pipeline_info.json`` is owned by the batch pipeline and is left alone.

There is deliberately no "delete the whole document" helper. Resets clear one
domain at a time (``persistence/reset_scopes.py``), writing each domain's
cleared value through its own saver. Deleting the shared file instead was how a
single wrong argument at one call site — ``delete(app)`` where the function
wanted ``app._app_state`` — could silently spare *every* section at once, and a
per-domain clear cannot fail that way for domains it does not name.

Migration: on first read, any raw-format legacy sidecars (see ``LEGACY_SIDECARS``)
present in the data directory are absorbed into a fresh ``persistence.json`` and
then deleted. ``qc_params`` migrates its own legacy ``qc_params.json`` (which
wraps its data) separately, in ``qc_params.py``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from review.persistence._io import atomic_write_json

_logger = logging.getLogger("review")

PERSISTENCE_FILENAME = "persistence.json"
SCHEMA_VERSION = 1

# Legacy sidecar filename → section key.
LEGACY_SIDECARS: dict[str, str] = {
    "ratios.json": "ratios",
    "heatmap_layouts.json": "heatmap_layouts",
    "line_order.json": "line_order",
}


def path_for(state) -> Path | None:
    if state.dataset.data_dir:
        return Path(state.dataset.data_dir) / PERSISTENCE_FILENAME
    return None


def read(state) -> dict:
    """Return the cached persistence doc, populating from disk on first call.

    When ``persistence.json`` is missing but legacy sidecars are present, the
    legacy files are migrated into a freshly written doc and then deleted.
    Returns an empty dict when there's no data directory at all.
    """
    cached = state.dataset.persistence_doc
    if cached is not None:
        return cached
    p = path_for(state)
    if p is None:
        return {}
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            _logger.warning("Failed to read %s: %s", p, exc)
            doc = {}
            state.dataset.persistence_doc_degraded = True
        if not isinstance(doc, dict):
            _logger.warning("%s is not a JSON object; ignoring.", p)
            doc = {}
            state.dataset.persistence_doc_degraded = True
        sv = doc.get("schema_version")
        if sv is not None and sv != SCHEMA_VERSION:
            # "Applying defaults" is now what actually happens. It used to be
            # only what the message said: the doc was then used unchanged, so a
            # file written by a different schema was read section by section as
            # if this build understood it.
            _logger.warning(
                "%s schema_version=%r (expected %d); this build will read it "
                "as empty and will not write over it.", p, sv, SCHEMA_VERSION,
            )
            doc = {}
            # Not ``persistence_doc_degraded``: that flag renames the file
            # aside, and this file is perfectly legible — it was written by a
            # different build of the app. A user who alternates builds would
            # lose the other one's state on every launch, under a filename
            # claiming it was unreadable. Refusing to *write* is enough to keep
            # both safe, and it is what the section-by-section read should have
            # done instead of using the document anyway.
            state.dataset.persistence_doc_foreign = True
        # Legacy sidecars alongside an existing persistence.json shouldn't
        # happen, but if they do, prefer the consolidated doc.
        _warn_about_orphan_legacy(p.parent)
    else:
        doc = _migrate_from_legacy(p)
    doc.setdefault("schema_version", SCHEMA_VERSION)
    state.dataset.persistence_doc = doc
    return doc


def _set_aside_unreadable(p) -> bool:
    """Move an unreadable ``persistence.json`` out of the way, keeping it.

    Overwriting it would destroy the only copy of everything the user saved —
    their ratios, layouts, curation, notes and sample definitions — because one
    section failed to parse. Renaming keeps that file recoverable by hand while
    letting the app start a clean one.
    """
    candidates = [p.with_name(f"{p.stem}.unreadable-{n}{p.suffix}")
                  for n in range(1, 100)]
    # A name that cannot already be taken, so running out of the tidy numbered
    # ones never turns into "abandon every save from here on" — the only thing
    # worse than an ugly filename is a session that silently stops persisting.
    candidates.append(p.with_name(
        f"{p.stem}.unreadable-{os.getpid()}-{time.time_ns()}{p.suffix}"))
    for alt in candidates:
        if alt.exists():
            continue
        try:
            p.replace(alt)
        except OSError as exc:
            _logger.warning("Could not set aside %s: %s", p, exc)
            return False
        _logger.warning(
            "Kept the unreadable %s as %s rather than overwriting it.",
            p.name, alt.name,
        )
        return True
    return False


def write(state, doc: dict) -> None:
    """Atomically replace the on-disk doc and refresh the cache."""
    p = path_for(state)
    if p is None:
        return
    if getattr(state.dataset, "persistence_doc_foreign", False):
        _logger.warning(
            "Not writing %s: it was written by a different schema version and "
            "this build would overwrite state it cannot read.", p)
        return
    if getattr(state.dataset, "persistence_doc_degraded", False):
        # The doc in memory is empty because the read failed, not because the
        # dataset has nothing saved. Writing it back would erase every section
        # the file still holds.
        if p.exists() and not _set_aside_unreadable(p):
            # The copy could not be made — a read-only directory, a permission
            # problem. Writing anyway would destroy the very file the rename
            # exists to preserve, which is the outcome this whole path is here
            # to avoid, so the write is abandoned instead.
            _logger.warning("Not writing %s: its unreadable predecessor could "
                            "not be preserved.", p)
            return
        state.dataset.persistence_doc_degraded = False
    doc.setdefault("schema_version", SCHEMA_VERSION)
    try:
        # ``deferrable``: inside a debounced autosave the file operations go to
        # the background writer, because this is nine persistence domains'
        # write path and every one of them was paying it on the GUI thread.
        # The JSON is serialised before this returns, which is what makes it
        # safe to hand over a dict that *is* the live cache assigned below.
        # Outside an autosave — an explicit save, a test — nothing is deferred
        # and this raises as it always did.
        atomic_write_json(p, doc, deferrable=True)
    except OSError as exc:
        _logger.warning("Failed to write %s: %s", p, exc)
        return
    # A deferred write has not landed yet, so this is the optimistic half: the
    # cache takes the doc the user's edit produced, and a write that later
    # fails is reported by the writer rather than by rolling the cache back.
    # Rolling back would be worse — ``read`` would then hand the *pre-edit* doc
    # to the next ``set_section``, so one unwritable moment would erase the
    # edit from every subsequent write too.
    state.dataset.persistence_doc = doc


def get_section(ctx, key: str) -> Any:
    return read(ctx.state).get(key)


def set_section(ctx, key: str, value: Any) -> None:
    """Mutate one section and flush the whole doc to disk."""
    doc = read(ctx.state)
    if value is None:
        doc.pop(key, None)
    else:
        doc[key] = value
    write(ctx.state, doc)


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
