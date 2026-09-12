"""The on-disk ``sample_definitions`` format: read it, write it, repair it.

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
        "name_is_verbatim": True,           # optional; only ever present as True
    }

``name_is_verbatim`` marks a name that stands on its own — one composed from the
sample variables the group's wells hold — so ``replicate_display_label`` leaves
it alone instead of reading a `Dose` group called ``10`` as an auto-generated
name and redrawing it as its well tokens. Absent on every selection that has not
claimed it, which is most of them.

The model proper is ``selections: list[Selection]`` — the array order *is* the
bar-plot order. ``well_labels`` (a ``dict[token, str]``) stays a separate sibling.

Why this is shared rather than Review's
=======================================

The block is written by the pipeline, read by Review, and merged by Datasets:
``datasets.merge_plates.merge_plates_compute.merge_sample_definitions`` runs both
plates' blocks through :func:`from_block` and writes the result back through
:func:`to_block`, because they are the only things in the codebase that know how
to read a saved block — a v2 one is validated and a legacy v1 one is migrated on
the way in. Two panes that disagreed about the format would disagree about the
same file.

What is *not* here is what a selection means once it reaches a figure: its
colour on a plate, the label it draws under a bar, which of its members the
replicate machinery treats as a set. That is Review's plotting model and it
stays in :mod:`review.domain.selections_model`, which imports the two helpers it
shares with this module (:func:`well_rank`, :func:`_clean_wells`) from here.

This module is Qt-free and GUI-free. It reads the 96 rank colours from
:mod:`domain.well_palette`, which is where they live precisely so that both
panes bake the same ``color`` value for the same well.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any
from collections.abc import Callable, Iterable, Sequence

from util.well_token import canonical_well_label
from domain.well_palette import WELL_COLORS_TUPLE

_logger = logging.getLogger("domain.selections_format")

SCHEMA_VERSION = 2

_PLATE_ROWS = "ABCDEFGH"
_N_COLS = 12


def well_colors() -> list[str]:
    """The 96 rank colours a selection's ``color`` field is baked from.

    This used to borrow ``review.domain.plate_layout.WELL_COLORS`` behind a
    ``try``/``except``, falling back to a ten-colour list when the pane was not
    importable. Both halves of that are gone: the palette is
    :mod:`domain.well_palette` now, which imports nothing and cannot fail to
    load, and a ten-colour fallback silently repeating every eleventh well was
    never a good answer for a value that gets written to disk.
    """
    return [str(c) for c in WELL_COLORS_TUPLE]


# ─────────────────────────────────────────────────────────── token helpers ──
def normalize_token(raw: Any) -> str | None:
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


def _clean_token(w: Any) -> str | None:
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
def _hex6(c: Any) -> str | None:
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
               palette: Sequence[str] | None = None,
               used_colors: set | None = None) -> str:
    """Decision-#1 colour: by the position rank of the selection's *lowest* well.

    Falls back to the positional palette entry when ``wells`` has no parseable
    token (e.g. an empty, not-yet-populated selection).

    The plate has 96 wells and the palette has ``len(theme.WELL_COLORS_TUPLE)``
    entries, so two selections whose lowest-rank wells differ by exactly that
    many map to the same palette index. When ``used_colors`` is supplied, the index is
    incremented (mod len(palette)) until a free slot is found — preserving
    the "same lowest-rank well → same colour" intent in the common case
    while keeping distinct selections distinguishable.
    """
    # No empty-palette guard: a truthy ``palette`` lists at least one colour and
    # a falsy one falls through to the 96 of :func:`well_colors`, so ``pal`` can
    # no longer be empty. It could when this read a ten-colour fallback through
    # a ``try``/``except`` that a missing GUI could take.
    pal = list(palette) if palette else well_colors()
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


# ─────────────────────────────────────────────────────── building Selections ──
def make_selection(*, name: Any, wells: Any, replicates: Any = None,
                   hidden: bool = False, color: Any = None, source: Any = "user",
                   labels: Any = None, sel_id: Any = None,
                   name_is_verbatim: bool = False,
                   used_names: set | None = None, used_ids: set | None = None,
                   used_colors: set | None = None,
                   fallback_color_idx: int = 0,
                   palette: Sequence[str] | None = None,
                   id_factory: Callable[[], str] = _default_id_factory) -> dict:
    """Build one normalised, invariant-respecting ``Selection`` dict.

    ``used_names`` / ``used_ids`` / ``used_colors`` (if given) are updated
    in place so a sequence of calls produces globally-unique names / ids /
    colours.

    ``name_is_verbatim`` marks a name that means something on its own — a name
    composed from the sample variables the group's wells hold, rather than one
    the app made up from their tokens. It is stored **only when true**, so a
    selection that has never claimed it keeps exactly the keys it always had.

    It survives save and load because :func:`validate_repair` reads it
    **explicitly** and passes it back in here — *not*, as this said until a
    reviewer measured it, on that function's unknown-key passthrough, which
    excludes this key by name. The exclusion is the point of the pair: the
    passthrough copies a key back only when it is absent from the rebuilt dict,
    so a stored ``false`` would be copied in as a key that is present and
    falsy, and this field's whole contract is that it is present only when
    true. Deleting the explicit parameter on the strength of the old sentence
    would silently drop the flag on every reload; the check is one line, and it
    is in ``tests/test_group_names_from_variables.py``.
    :func:`active_replicate_sets` hands it to the ``ReplicateSet``,
    which is where :func:`replicate_display_label` reads it: without it a `Dose`
    group named ``10`` matches the auto-generated-name pattern and is redrawn on
    every axis as the tokens of the wells that received that dose.
    """
    used_names = used_names if used_names is not None else set()
    used_ids = used_ids if used_ids is not None else set()

    wl = _clean_wells(wells)
    # A group is exactly one condition: its wells are implicitly its replicate
    # set. We no longer store a ``replicates`` sub-partition (multiple sub-sets /
    # solo wells mixed with replicates) — the field is reserved and always
    # ``None``. Any incoming partition (e.g. from a legacy on-disk block) is
    # collapsed here; the group's flat ``wells`` are preserved. Nothing
    # downstream reads the partition (every plot/stat/export flattens a group to
    # its ``wells`` via ``ReplicateSet``), so this is behaviour-preserving.
    reps = None

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
    if name_is_verbatim:
        sel["name_is_verbatim"] = True
    if isinstance(labels, dict) and labels:
        sel["labels"] = {str(k): str(v) for k, v in labels.items() if str(v).strip()}
    # preserve unknown keys for forward-compat
    return sel


def validate_repair(selections: Any, *, palette: Sequence[str] | None = None,
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
            name_is_verbatim=bool(raw.get("name_is_verbatim")),
            used_names=used_names, used_ids=used_ids,
            used_colors=used_colors,
            fallback_color_idx=i, palette=palette, id_factory=id_factory,
        )
        # carry through any unknown keys (forward-compat)
        for k, v in raw.items():
            if k not in sel and k not in ("id", "name", "color", "hidden", "wells",
                                          "replicates", "source", "labels",
                                          "name_is_verbatim"):
                sel[k] = v
        out.append(sel)
    return out


def _pick_current(selections: Sequence[dict], current_id: Any) -> str | None:
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


def migrate_v1(block: Any, *, rep_hidden: Iterable[int] | None = None,
               bar_active_grp: int = -1, active_rep_idx: int = -1,
               loaded_tokens: Iterable[str] | None = None,
               palette: Sequence[str] | None = None,
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
        # A migrated group flattens to one condition — its wells are its
        # replicate set; the legacy member/solo sub-partition is not carried
        # over (it was never read downstream).
        selections.append(make_selection(
            name=g["name"], wells=gw, replicates=None, hidden=g["hidden"],
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
                          rset_order: Sequence[str] | None = None,
                          well_order: Sequence[str] | None = None) -> list[dict]:
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


def from_block(block: Any, *, tok_to_label: Any = None,
               line_order_rsets: Sequence[str] | None = None,
               line_order_wells: Sequence[str] | None = None,
               palette: Sequence[str] | None = None,
               id_factory: Callable[[], str] = _default_id_factory):
    """Parse a ``sample_definitions`` / ``bar_groups.json`` block → unified state.

    Returns ``(selections, current_id, well_labels, notes, plot_groups)`` —
    five values, so a caller unpacking four raises ``ValueError``. v1 blocks are
    migrated; v2 blocks are validated/repaired; non-dict / empty → empty state.
    Honours a saved line-graph order (§3.5) when one is supplied.
    """
    if not isinstance(block, dict):
        # Five values, like the return at the end of this function. It returned
        # four here, and every one of the four callers unpacks five — so a
        # ``sample_definitions`` key holding anything but a dict (a list from a
        # hand-edited sidecar, a null) raised ValueError instead of the empty
        # state this branch exists to supply.
        return [], None, {}, "", []

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
    from domain.plot_groups import validate_repair_plot_groups
    plot_groups = validate_repair_plot_groups(block.get("plot_groups"))
    return selections, current_id, well_labels, notes, plot_groups


def to_block(selections: Sequence[dict], well_labels: Any = None, notes: Any = "",
             current_id: Any = None, plot_groups: Any = None) -> dict:
    """Serialise unified state to a v2 ``sample_definitions`` block.

    Drops the legacy ``rep_sets`` / ``groups`` keys; adds ``schema_version: 2``.
    The caller is responsible for merging this under the ``sample_definitions``
    key of ``pipeline_info.json`` while preserving every other key verbatim.
    """
    sels = validate_repair(list(selections or []))
    from domain.plot_groups import validate_repair_plot_groups
    return {
        "schema_version": SCHEMA_VERSION,
        "selections": sels,
        "current_id": (current_id if isinstance(current_id, str) and
                       any(s["id"] == current_id for s in sels)
                       else (sels[0]["id"] if sels else "")),
        "well_labels": _clean_labels(well_labels),
        "notes": str(notes or ""),
        "plot_groups": validate_repair_plot_groups(plot_groups),
    }
