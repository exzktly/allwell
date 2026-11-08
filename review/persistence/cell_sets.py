"""Saved cells on disk (``<data_dir>/cell_sets.json``), and which one is excluded.

One row per set: its name, cells (``[well, fov, tp, nucleus_id]``), where and
how it was drawn, and each well's ``<well>_out.zip`` fingerprint when it was
saved — the same fingerprint ``cell_overrides.json`` stamps, for the same
reason: nucleus ids are re-allocated when a well is segmented again, so a set
saved before that names other cells after it. On load, a well whose
fingerprint changed, or cannot be read, makes that set's cells there *stale*
(``CellSetsState.stale``); they are kept on disk and left out of every use, and
the set says so. A set is never silently emptied.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from review.core.cell_sets_state import CellSet
from review.persistence._io import atomic_write_json
from review.persistence.cell_overrides import fingerprints_for_data_dir

_logger = logging.getLogger("review")

FILENAME = "cell_sets.json"
_SCHEMA_VERSION = 1


def path_for(ctx) -> Path | None:
    data_dir = ctx.state.dataset.data_dir
    return Path(data_dir) / FILENAME if data_dir else None


def to_payload(state) -> dict:
    return {
        "version": _SCHEMA_VERSION,
        "exclude": state.exclude,
        "highlight": state.highlight,
        "sets": [{"name": s.name, "created": s.created, "source": s.source, "note": s.note,
                  "fingerprints": dict(s.fingerprints),
                  "cells": [list(c) for c in sorted(s.cells)]} for s in state.sets],
    }


def from_payload(payload: dict, current_prints: dict) -> tuple[list, dict, str | None]:
    """``(sets, stale, exclude)`` from a parsed file, staleness judged against *current_prints*."""
    sets, stale = [], {}
    for row in (payload or {}).get("sets") or ():
        try:
            cells = frozenset(tuple(str(v) for v in c) for c in row.get("cells") or () if len(c) == 4)
            cell_set = CellSet(name=str(row["name"]), cells=cells, created=str(row.get("created", "")),
                               source=str(row.get("source", "")), note=str(row.get("note", "")),
                               fingerprints={str(k): str(v) for k, v in (row.get("fingerprints") or {}).items()})
        except (KeyError, TypeError, ValueError):
            _logger.warning("cell_sets.json: skipped an unreadable set %r", row)
            continue
        per_well: dict[str, int] = {}
        for well, *_rest in cell_set.cells:
            saved, now = cell_set.fingerprints.get(well, ""), current_prints.get(well, "")
            if not saved or not now or saved != now:
                per_well[well] = per_well.get(well, 0) + 1
        sets.append(cell_set)
        if per_well:
            stale[cell_set.name] = per_well
    names = {s.name for s in sets}
    exclude = (payload or {}).get("exclude")
    return sets, stale, (exclude if exclude in names else None)


def save_to_data_dir(ctx) -> None:
    path = path_for(ctx)
    if path is None:
        return
    try:
        atomic_write_json(path, to_payload(ctx.state.cell_sets), deferrable=True)
    except OSError as exc:
        _logger.warning("Failed to save saved cells to %s: %s", path, exc)


def load_from_data_dir(ctx) -> None:
    state = ctx.state.cell_sets
    state.sets, state.stale, state.exclude = [], {}, None
    state.highlight = state.missing_highlight = None
    path = path_for(ctx)
    if path is None or not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _logger.warning("Could not read saved cells from %s (%s); none loaded", path, exc)
        return
    state.sets, state.stale, state.exclude = from_payload(
        payload, fingerprints_for_data_dir(ctx.state.dataset.data_dir))
    highlight = payload.get("highlight") if isinstance(payload, dict) else None
    state.highlight = highlight if highlight in {s.name for s in state.sets} else None
