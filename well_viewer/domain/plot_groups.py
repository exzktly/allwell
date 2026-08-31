"""Lightweight Plot Groups for quick well-set selection.

Plot Groups are intentionally separate from Sample Definition selections:
they are not replicate sets, do not affect stats/group aggregation, and only
exist to reload a saved subset of wells into the live plate selection.
"""

from __future__ import annotations

import uuid
from typing import Any
from collections.abc import Iterable

from well_viewer.domain.selections_model import _clean_wells


def _mint_id(used: set[str]) -> str:
    while True:
        sid = uuid.uuid4().hex[:8]
        if sid not in used:
            return sid


def _unique_name(name: Any, used: set[str]) -> str:
    base = (str(name).strip() if name is not None else "") or "Selection"
    if base not in used:
        return base
    i = 2
    while f"{base} {i}" in used:
        i += 1
    return f"{base} {i}"


def make_plot_group(
    *,
    name: Any,
    wells: Any,
    group_id: str | None = None,
    used_names: set[str] | None = None,
    used_ids: set[str] | None = None,
) -> dict:
    """Return a normalised Plot Group dictionary."""
    used_names = used_names if used_names is not None else set()
    used_ids = used_ids if used_ids is not None else set()
    sid = (
        group_id
        if isinstance(group_id, str) and group_id and group_id not in used_ids
        else _mint_id(used_ids)
    )
    used_ids.add(sid)
    name_out = _unique_name(name, used_names)
    used_names.add(name_out)
    return {
        "id": sid,
        "name": name_out,
        "wells": _clean_wells(wells),
    }


def validate_repair_plot_groups(groups: Any) -> list[dict]:
    """Coerce arbitrary JSON into a list of valid Plot Groups."""
    if not isinstance(groups, (list, tuple)):
        return []
    used_names: set[str] = set()
    used_ids: set[str] = set()
    out: list[dict] = []
    for raw in groups:
        if not isinstance(raw, dict):
            continue
        group = make_plot_group(
            name=raw.get("name"),
            wells=raw.get("wells", []),
            group_id=raw.get("id"),
            used_names=used_names,
            used_ids=used_ids,
        )
        if group["wells"]:
            out.append(group)
    return out


def filter_loaded_wells(groups: Iterable[dict], loaded_wells: Iterable[str]) -> list[dict]:
    """Return Plot Groups with wells pruned to those currently loaded."""
    loaded = set(loaded_wells or [])
    out: list[dict] = []
    for group in validate_repair_plot_groups(list(groups or [])):
        wells = [w for w in group.get("wells", []) if w in loaded]
        if wells:
            out.append({**group, "wells": wells})
    return out
