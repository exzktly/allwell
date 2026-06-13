"""Unified saved-selections data model (schema v2) + the v1→v2 migration.

A ``Selection`` is a plain ``dict`` (JSON-friendly):

    {
        "id":         "ab12cd34",          # uuid4().hex[:8], unique, never reused
        "name":       "Control",           # unique within the list ("_v2" on clash)
        "color":      "#5B9BF8",           # #RRGGBB; baked from well-position rank
        "hidden":     False,
        "wells":      ["A01", "A02", ...],  # zero-padded tokens; draw order
        "replicates": [["A01","A02"], ...] | None,   # partition of `wells`
        "labels":     {...} | None,         # per-well overrides; reserved/ignored v1
        "source":     "bar_group" | "rep_set" | "user" | "import",
    }

The model proper is ``selections: list[Selection]`` — the array order *is* the
bar-plot order. ``well_labels`` (a ``dict[token, str]``) stays a separate sibling.

This module is GUI-free (it lazily borrows ``WELL_COLORS`` from
``well_viewer.plate_layout`` with a pure-Python fallback) so it — and its
self-test — import without Qt.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Callable, Iterable, Optional, Sequence

from core.well_token import canonical_well_label
from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer.domain.selections_model")

SCHEMA_VERSION = 2

_PLATE_ROWS = "ABCDEFGH"
_N_COLS = 12
_DEFAULT_COLOR = "#888888"
_FALLBACK_PALETTE = [
    "#5B9BF8", "#F26B6B", "#4ADE80", "#F5A524", "#A78BFA",
    "#22D3EE", "#FB7185", "#84CC16", "#E879F9", "#FACC15",
]


def well_colors() -> list[str]:
    """The bar-plot colour palette — real one if available, fallback otherwise."""
    try:
        from well_viewer.domain.plate_layout import WELL_COLORS  # noqa: WPS433
        out = [str(c) for c in WELL_COLORS if isinstance(c, str) and c.strip()]
        if out:
            return out
    except Exception:  # pragma: no cover - GUI/theme not importable
        swallowed()  # was: pass (silent except)
    return list(_FALLBACK_PALETTE)


# ─────────────────────────────────────────────────────────── token helpers ──
def normalize_token(raw: Any) -> Optional[str]:
    """``"a1"`` / ``"A01"`` / ``" h12 "`` → ``"A01"`` / ``"H12"``; else ``None``.

    Delegates to the canonical 96-well parser in :mod:`well_token`.
    """
    return canonical_well_label(raw) if isinstance(raw, str) else None


def well_rank(token: Any) -> int:
    """Row-major rank: A01=0, A02=1, …, A12=11, B01=12, …, H12=95.

    Un-parseable tokens sort *last* (a large sentinel).
    """
    t = normalize_token(token)
    if t is None:
        return 1 << 30
    try:
        return _PLATE_ROWS.index(t[0]) * _N_COLS + (int(t[1:]) - 1)
    except ValueError:
        return 1 << 30


def _clean_token(w: Any) -> Optional[str]:
    """Normalise a 96-well token (``"a1"`` → ``"A01"``); drop anything that isn't
    a structurally-valid well token. (We do *not* filter against the currently
    loaded dataset — stored ≠ loaded is allowed — but junk like ``"ZZZ"`` / ints
    is dropped, matching what the legacy loader effectively did.)"""
    return normalize_token(w)


def _dedup_keep_order(seq: Iterable[Any]) -> list:
    seen: set = set()
    out: list = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _clean_wells(seq: Any) -> list[str]:
    if not isinstance(seq, (list, tuple)):
        return []
    return _dedup_keep_order(t for t in (_clean_token(w) for w in seq) if t)


# ─────────────────────────────────────────────────────── colour / id / name ──
def _hex6(c: Any) -> Optional[str]:
    if not isinstance(c, str):
        return None
    s = c.strip()
    m = re.fullmatch(r"#?([0-9A-Fa-f]{6})", s)
    if m:
        return "#" + m.group(1).upper()
    m = re.fullmatch(r"#?([0-9A-Fa-f]{3})", s)
    if m:
        r, g, b = m.group(1)
        return f"#{r}{r}{g}{g}{b}{b}".upper()
    return None


def rank_color(wells: Sequence[str], fallback_idx: int = 0,
               palette: Optional[Sequence[str]] = None,
               used_colors: Optional[set] = None) -> str:
    """Decision-#1 colour: by the position rank of the selection's *lowest* well.

    Falls back to the positional palette entry when ``wells`` has no parseable
    token (e.g. an empty, not-yet-populated selection).

    The plate has 96 wells but the palette has 48 colours, so two selections
    whose lowest-rank wells differ by exactly 48 (e.g. A01 / E01) would map
    to the same palette index. When ``used_colors`` is supplied, the index is
    incremented (mod len(palette)) until a free slot is found — preserving
    the "same lowest-rank well → same colour" intent in the common case
    while keeping distinct selections distinguishable.
    """
    pal = list(palette) if palette else well_colors()
    if not pal:
        pal = list(_FALLBACK_PALETTE)
    ranks = [well_rank(w) for w in wells]
    ranks = [r for r in ranks if r < (1 << 30)]
    if not ranks:
        base_idx = fallback_idx % len(pal)
    else:
        base_idx = min(ranks) % len(pal)
    if not used_colors:
        return pal[base_idx]
    # Step to the next free palette slot, in palette order. After a full
    # cycle every slot is taken; fall back to the base index (a colour
    # *will* repeat, but at that point the user has more selections than
    # the palette can distinguish).
    for offset in range(len(pal)):
        candidate = pal[(base_idx + offset) % len(pal)]
        if candidate not in used_colors:
            return candidate
    return pal[base_idx]


def _default_id_factory() -> str:
    return uuid.uuid4().hex[:8]


def _mint_id(used: set, factory: Callable[[], str]) -> str:
    for _ in range(10_000):
        try:
            i = factory()
        except Exception:  # pragma: no cover - a misbehaving factory
            break
        if isinstance(i, str) and i and i not in used:
            return i
    # pathological / exhausted factory — fall back to uuid
    while True:
        i = uuid.uuid4().hex[:8]
        if i not in used:
            return i


def _unique_name(name: Any, used: set) -> str:
    name = (str(name).strip() if name is not None else "") or "Selection"
    if name not in used:
        return name
    base = f"{name}_v2"
    if base not in used:
        return base
    i = 2
    while f"{base} {i}" in used:
        i += 1
    return f"{base} {i}"


# ───────────────────────────────────────────────────────── replicate hygiene ──
def _deoverlap_replicates(replicates: Any, *, allowed: Optional[set] = None) -> Optional[list[list[str]]]:
    """Make ``replicates`` a partition (a well in ≤ 1 sub-list) ⊆ ``allowed``.

    Returns ``None`` for an empty/absent input or when nothing survives.
    """
    if not replicates:
        return None
    seen: set = set()
    out: list[list[str]] = []
    for sub in replicates:
        if not isinstance(sub, (list, tuple)):
            continue
        kept: list[str] = []
        for w in sub:
            t = _clean_token(w)
            if t is None or t in seen:
                continue
            if allowed is not None and t not in allowed:
                continue
            seen.add(t)
            kept.append(t)
        if kept:
            out.append(kept)
    return out or None


# ─────────────────────────────────────────────────────── building Selections ──
def make_selection(*, name: Any, wells: Any, replicates: Any = None,
                   hidden: bool = False, color: Any = None, source: Any = "user",
                   labels: Any = None, sel_id: Any = None,
                   used_names: Optional[set] = None, used_ids: Optional[set] = None,
                   used_colors: Optional[set] = None,
                   fallback_color_idx: int = 0,
                   palette: Optional[Sequence[str]] = None,
                   id_factory: Callable[[], str] = _default_id_factory) -> dict:
    """Build one normalised, invariant-respecting ``Selection`` dict.

    ``used_names`` / ``used_ids`` / ``used_colors`` (if given) are updated
    in place so a sequence of calls produces globally-unique names / ids /
    colours.
    """
    used_names = used_names if used_names is not None else set()
    used_ids = used_ids if used_ids is not None else set()

    wl = _clean_wells(wells)
    reps = _deoverlap_replicates(replicates, allowed=set(wl))

    sid = sel_id if (isinstance(sel_id, str) and sel_id and sel_id not in used_ids) \
        else _mint_id(used_ids, id_factory)
    used_ids.add(sid)

    nm = _unique_name(name, used_names)
    used_names.add(nm)

    col = _hex6(color) or rank_color(wl, fallback_color_idx, palette, used_colors)
    if used_colors is not None:
        used_colors.add(col)

    sel: dict = {
        "id": sid,
        "name": nm,
        "color": col,
        "hidden": bool(hidden) if not isinstance(hidden, str)
        else hidden.strip().lower() in ("1", "true", "yes", "on"),
        "wells": wl,
        "replicates": reps,
        "source": str(source) if source else "user",
    }
    if isinstance(labels, dict) and labels:
        sel["labels"] = {str(k): str(v) for k, v in labels.items() if str(v).strip()}
    # preserve unknown keys for forward-compat
    return sel


def validate_repair(selections: Any, *, palette: Optional[Sequence[str]] = None,
                    id_factory: Callable[[], str] = _default_id_factory) -> list[dict]:
    """Coerce an arbitrary list into a well-formed ``selections`` list.

    Re-mints empty/duplicate ids, resolves name collisions (``_v2`` …), coerces
    ``color`` to ``#RRGGBB`` (re-baking from rank when missing/bad), defaults
    missing ``hidden`` / ``wells`` / ``replicates`` / ``source``, de-overlaps
    ``replicates``. Unknown keys are preserved.
    """
    if not isinstance(selections, (list, tuple)):
        return []
    used_names: set = set()
    used_ids: set = set()
    used_colors: set = set()
    out: list[dict] = []
    for i, raw in enumerate(selections):
        if not isinstance(raw, dict):
            continue
        sel = make_selection(
            name=raw.get("name"),
            wells=raw.get("wells", []),
            replicates=raw.get("replicates"),
            hidden=raw.get("hidden", False),
            color=raw.get("color"),
            source=raw.get("source", "user"),
            labels=raw.get("labels"),
            sel_id=raw.get("id"),
            used_names=used_names, used_ids=used_ids,
            used_colors=used_colors,
            fallback_color_idx=i, palette=palette, id_factory=id_factory,
        )
        # carry through any unknown keys (forward-compat)
        for k, v in raw.items():
            if k not in sel and k not in ("id", "name", "color", "hidden", "wells",
                                          "replicates", "source", "labels"):
                sel[k] = v
        out.append(sel)
    return out


def _pick_current(selections: Sequence[dict], current_id: Any) -> Optional[str]:
    ids = {s["id"] for s in selections}
    if isinstance(current_id, str) and current_id in ids:
        return current_id
    return selections[0]["id"] if selections else None


# ─────────────────────────────────────────────────────────────── v1 → v2 ────
def _coerce_bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _resolve_v1(block: Any):
    """From a raw v1 ``{rep_sets, groups}`` payload → (rep_dicts, name→dict, group_dicts).

    ``group_dicts[*]['members']`` holds *references* to the rep-dicts (so the
    "is this rep-set in a group?" identity test works).
    """
    if not isinstance(block, dict):
        return [], {}, []
    rep_dicts: list[dict] = []
    rep_by_name: dict[str, dict] = {}
    for it in (block.get("rep_sets") or []):
        if not isinstance(it, dict):
            continue
        d = {"name": str(it.get("name", "R")), "wells": _clean_wells(it.get("wells", []))}
        rep_dicts.append(d)
        rep_by_name.setdefault(d["name"], d)
    group_dicts: list[dict] = []
    for it in (block.get("groups") or []):
        if not isinstance(it, dict):
            continue
        members: list[dict] = []
        for rn in (it.get("members") or []):
            d = rep_by_name.get(rn if isinstance(rn, str) else None)
            if d is not None:
                members.append(d)
            else:
                _logger.warning("group %r references unknown rep-set %r — dropped",
                                it.get("name"), rn)
        group_dicts.append({
            "name": str(it.get("name", "Group")),
            "hidden": _coerce_bool(it.get("hidden", False)),
            "members": members,
            "solo_wells": _clean_wells(it.get("solo_wells", [])),
        })
    return rep_dicts, rep_by_name, group_dicts


def migrate_v1(block: Any, *, rep_hidden: Optional[Iterable[int]] = None,
               bar_active_grp: int = -1, active_rep_idx: int = -1,
               loaded_tokens: Optional[Iterable[str]] = None,
               palette: Optional[Sequence[str]] = None,
               id_factory: Callable[[], str] = _default_id_factory):
    """A v1 ``{rep_sets, groups[, well_labels, notes]}`` block → (selections, current_id).

    ``rep_hidden`` / ``loaded_tokens`` are only meaningful for an *in-session*
    migration (a from-disk block has no ``_rep_hidden`` and persists nothing
    about visibility, so all rep-set-derived entries migrate ``hidden=False``).
    """
    rep_dicts, _rep_by_name, group_dicts = _resolve_v1(block)
    used_names: set = set()
    used_ids: set = set()
    used_colors: set = set()
    selections: list[dict] = []

    # (1) bar groups, in order — group order wins
    for gi, g in enumerate(group_dicts):
        gw = _dedup_keep_order(
            [w for m in g["members"] for w in m["wells"]] + list(g["solo_wells"])
        )
        reps = [list(m["wells"]) for m in g["members"] if m["wells"]] \
            + [[w] for w in g["solo_wells"]]
        selections.append(make_selection(
            name=g["name"], wells=gw, replicates=(reps or None), hidden=g["hidden"],
            color=None, source="bar_group", used_names=used_names, used_ids=used_ids,
            used_colors=used_colors,
            fallback_color_idx=gi, palette=palette, id_factory=id_factory,
        ))

    # (2) "free" rep-sets — not a member of any group (dict identity)
    in_group = {id(m) for g in group_dicts for m in g["members"]}
    rep_hidden_set = set(int(i) for i in (rep_hidden or set()))
    if loaded_tokens is not None:
        loaded = set(loaded_tokens)
        loaded_order = [rd for rd in rep_dicts if any(w in loaded for w in rd["wells"])]
    else:
        loaded_order = list(rep_dicts)
    loaded_index = {id(rd): i for i, rd in enumerate(loaded_order)}
    free = [rd for rd in rep_dicts if id(rd) not in in_group]
    rep_dict_index = {id(rd): k for k, rd in enumerate(rep_dicts)}   # original _rep_sets order
    free_sel_index_by_rep_idx: dict[int, int] = {}
    for j, rd in enumerate(free):
        hidden = (id(rd) in loaded_index) and (loaded_index[id(rd)] in rep_hidden_set)
        selections.append(make_selection(
            name=rd["name"], wells=list(rd["wells"]),
            replicates=([list(rd["wells"])] if rd["wells"] else None),
            hidden=hidden, color=None, source="rep_set",
            used_names=used_names, used_ids=used_ids,
            used_colors=used_colors,
            fallback_color_idx=len(group_dicts) + j, palette=palette, id_factory=id_factory,
        ))
        if id(rd) in rep_dict_index:
            free_sel_index_by_rep_idx[rep_dict_index[id(rd)]] = len(group_dicts) + j

    # (3) current — bar-active group wins; else an active (free) rep-set; else first
    current_id = None
    if 0 <= bar_active_grp < len(group_dicts) and bar_active_grp < len(selections):
        current_id = selections[bar_active_grp]["id"]
    elif int(active_rep_idx) in free_sel_index_by_rep_idx:
        current_id = selections[free_sel_index_by_rep_idx[int(active_rep_idx)]]["id"]
    if current_id is None and selections:
        current_id = selections[0]["id"]
    return selections, current_id


# ──────────────────────────────────────────────────── §3.5 line-order honour ──
def reorder_by_line_order(selections: Sequence[dict], *,
                          rset_order: Optional[Sequence[str]] = None,
                          well_order: Optional[Sequence[str]] = None) -> list[dict]:
    """Stable-sort ``selections`` to match a saved line-graph order; unknowns last.

    A selection matches by ``name`` (the legacy rep-set/group name) first, then
    by the lowest position of any of its wells within ``well_order``.
    """
    rset_order = list(rset_order or [])
    well_order = list(well_order or [])
    if not rset_order and not well_order:
        return list(selections)
    rpos = {n: i for i, n in enumerate(rset_order)}
    wpos = {w: i for i, w in enumerate(well_order)}
    big = 1 << 30

    def _key(item):
        idx, s = item
        k = rpos.get(s.get("name"))
        if k is None:
            ws = [wpos[w] for w in (s.get("wells") or []) if w in wpos]
            k = min(ws) if ws else None
        return (big if k is None else k, idx)

    return [s for _, s in sorted(enumerate(selections), key=_key)]


# ─────────────────────────────────────────────────────── block (de)serialise ──
def _clean_labels(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and str(v).strip():
            out[k] = str(v).strip()
    return out


def block_is_v2(block: Any) -> bool:
    """A block is v2 only when *all three* hold:

      1. ``schema_version`` is present and >= 2 (or absent + ``selections``
         is a list, which the v2 builder always sets).
      2. No legacy v1 keys (``rep_sets`` / ``groups``) are populated.

    A mixed-shape file (carrying both v1 *and* v2 keys with content) is
    rejected so the caller can refuse to migrate, leaving the v1 data
    untouched rather than silently dropping it.
    """
    if not isinstance(block, dict):
        return False
    has_legacy = bool(block.get("rep_sets")) or bool(block.get("groups"))
    try:
        is_v2_marker = int(block.get("schema_version", 1)) >= 2
    except (TypeError, ValueError):
        is_v2_marker = False
    if not is_v2_marker:
        is_v2_marker = isinstance(block.get("selections"), list)
    if is_v2_marker and has_legacy:
        # Mixed payload — fail closed. Caller surfaces a warning and
        # disables v2 writes for the session.
        return False
    return is_v2_marker


def block_is_mixed(block: Any) -> bool:
    """``True`` when *block* carries both v1 and v2 keys with content.

    Lets callers distinguish "use v1 path" from "refuse to migrate"
    after ``block_is_v2`` returns ``False``.
    """
    if not isinstance(block, dict):
        return False
    has_legacy = bool(block.get("rep_sets")) or bool(block.get("groups"))
    try:
        is_v2_marker = int(block.get("schema_version", 1)) >= 2
    except (TypeError, ValueError):
        is_v2_marker = False
    if not is_v2_marker:
        is_v2_marker = isinstance(block.get("selections"), list)
    return is_v2_marker and has_legacy


def from_block(block: Any, *, tok_to_label: Any = None,
               line_order_rsets: Optional[Sequence[str]] = None,
               line_order_wells: Optional[Sequence[str]] = None,
               palette: Optional[Sequence[str]] = None,
               id_factory: Callable[[], str] = _default_id_factory):
    """Parse a ``sample_definitions`` / ``bar_groups.json`` block → unified state.

    Returns ``(selections, current_id, well_labels, notes)``. v1 blocks are
    migrated; v2 blocks are validated/repaired; non-dict / empty → empty state.
    Honours a saved line-graph order (§3.5) when one is supplied.
    """
    if not isinstance(block, dict):
        return [], None, {}, ""

    if block_is_v2(block):
        selections = validate_repair(block.get("selections"), palette=palette, id_factory=id_factory)
        current_id = _pick_current(selections, block.get("current_id"))
    else:
        # v1: from disk there is no active-group / rep-hidden info persisted —
        # mirror today's default (active = first group when groups exist).
        groups = block.get("groups") or []
        selections, current_id = migrate_v1(
            block, bar_active_grp=(0 if groups else -1),
            palette=palette, id_factory=id_factory,
        )

    if line_order_rsets or line_order_wells:
        selections = reorder_by_line_order(
            selections, rset_order=line_order_rsets, well_order=line_order_wells)
        current_id = _pick_current(selections, current_id)

    well_labels = _clean_labels(block.get("well_labels"))
    notes_raw = block.get("notes", "")
    notes = notes_raw if isinstance(notes_raw, str) else ""
    return selections, current_id, well_labels, notes


def to_block(selections: Sequence[dict], well_labels: Any = None, notes: Any = "",
             current_id: Any = None) -> dict:
    """Serialise unified state to a v2 ``sample_definitions`` block.

    Drops the legacy ``rep_sets`` / ``groups`` keys; adds ``schema_version: 2``.
    The caller is responsible for merging this under the ``sample_definitions``
    key of ``pipeline_info.json`` while preserving every other key verbatim.
    """
    sels = validate_repair(list(selections or []))
    return {
        "schema_version": SCHEMA_VERSION,
        "selections": sels,
        "current_id": (current_id if isinstance(current_id, str) and
                       any(s["id"] == current_id for s in sels)
                       else (sels[0]["id"] if sels else "")),
        "well_labels": _clean_labels(well_labels),
        "notes": str(notes or ""),
    }


def to_bar_groups_payload(selections: Sequence[dict], current_id: Any = None) -> dict:
    """v2 payload for the standalone ``bar_groups.json`` (no labels/notes)."""
    sels = validate_repair(list(selections or []))
    return {
        "schema_version": SCHEMA_VERSION,
        "selections": sels,
        "current_id": (current_id if isinstance(current_id, str) and
                       any(s["id"] == current_id for s in sels)
                       else (sels[0]["id"] if sels else "")),
    }
