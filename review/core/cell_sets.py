"""Create, rename, delete and read saved cells (``review/core/cell_sets_state.py``).

The rules every user of a set shares live here, so a set cannot mean one thing
in the Cell Browser and another in the analysis:

* :func:`usable_cells` is a set minus its cells in wells re-segmented since it
  was saved; :func:`stale_summary` is the sentence saying how many those are.
* :func:`set_excluded` chooses the one set left out of every analysis, which
  ``review/data/saved_cells_gate.py`` projects as the ``Saved_Cells_Pass``
  verdict. Saving a set never changes a number; only this does.
"""

from __future__ import annotations

from datetime import datetime

from review.core.cell_sets_state import CellSet


def _state(ctx):
    return ctx.state.cell_sets


def sets(ctx) -> list:
    return list(_state(ctx).sets)


def find(ctx, name: str) -> CellSet | None:
    return next((s for s in _state(ctx).sets if s.name == name), None)


def unique_name(ctx, base: str = "Cells") -> str:
    taken = {s.name for s in _state(ctx).sets}
    n = 1
    while f"{base} {n}" in taken:
        n += 1
    return f"{base} {n}"


def usable_cells(ctx, cell_set: CellSet) -> frozenset:
    """The set's cells outside the wells re-segmented since it was saved."""
    stale_wells = set((_state(ctx).stale.get(cell_set.name) or {}))
    if not stale_wells:
        return cell_set.cells
    return frozenset(c for c in cell_set.cells if c[0] not in stale_wells)


def stale_summary(ctx, cell_set: CellSet) -> str:
    """``""``, or why some of the set's cells are left out."""
    stale = _state(ctx).stale.get(cell_set.name) or {}
    n = sum(stale.values())
    if not n:
        return ""
    wells = ", ".join(sorted(stale))
    return (f"stale: {n:,} of {len(cell_set.cells):,} cells are in wells segmented again "
            f"since the set was saved ({wells}) and are left out")


def describe(ctx, cell_set: CellSet) -> str:
    """One line for a list of sets: name, size, origin, staleness."""
    parts = [f"{cell_set.name} — {len(cell_set.cells):,} cells"]
    if cell_set.source:
        parts.append(cell_set.source)
    if cell_set.note:
        parts.append(cell_set.note)
    stale = stale_summary(ctx, cell_set)
    if stale:
        parts.append(stale)
    return " · ".join(parts)


def _changed(ctx) -> None:
    """Save, and refresh the Saved cells panel when this build has one."""
    persistence = getattr(ctx, "persistence", None)
    if persistence is not None:
        persistence.schedule_save("cell_sets")
    panel = ctx.widgets.get_or_none("_saved_cells_panel") if getattr(ctx, "widgets", None) else None
    refresh = getattr(panel, "refresh", None)
    if callable(refresh):
        refresh()


def add_set(ctx, cells, *, source: str = "", note: str = "", name: str = "") -> CellSet | None:
    """Save *cells* (``(well, fov, tp, nucleus_id)`` in any spelling) as a new set.

    Returns the set, or ``None`` for no cells. Each well's archive fingerprint
    is taken now, so a later re-segmentation is noticed.
    """
    from domain.image_identity import CellKey
    from review.persistence.cell_overrides import well_zip_fingerprints

    keys = frozenset(CellKey.from_row({"fov": f, "timepoint": t, "nucleus_id": n}, well=w).as_tuple()
                     for w, f, t, n in cells)
    if not keys:
        return None
    prints = well_zip_fingerprints(ctx)
    cell_set = CellSet(
        name=name or unique_name(ctx), cells=keys,
        created=datetime.now().isoformat(timespec="seconds"), source=source, note=note,
        fingerprints={w: prints.get(w, "") for w in sorted({k[0] for k in keys})})
    _state(ctx).sets.append(cell_set)
    _changed(ctx)
    return cell_set


def rename_set(ctx, old: str, new: str) -> bool:
    new = str(new or "").strip()
    st = _state(ctx)
    if not new or new == old or any(s.name == new for s in st.sets):
        return False
    for i, s in enumerate(st.sets):
        if s.name == old:
            st.sets[i] = CellSet(name=new, cells=s.cells, created=s.created, source=s.source,
                                 note=s.note, fingerprints=dict(s.fingerprints))
    if old in st.stale:
        st.stale[new] = st.stale.pop(old)
    if st.exclude == old:
        st.exclude = new
    _changed(ctx)
    return True


def delete_set(ctx, name: str) -> None:
    st = _state(ctx)
    st.sets = [s for s in st.sets if s.name != name]
    st.stale.pop(name, None)
    if st.exclude == name:
        set_excluded(ctx, None)
    _changed(ctx)


def set_excluded(ctx, name: str | None) -> None:
    """Leave *name*'s usable cells out of every analysis (``None`` for no set)."""
    st = _state(ctx)
    name = name if name and find(ctx, name) is not None else None
    if st.exclude == name:
        return
    st.exclude = name
    from review.data.saved_cells_gate import apply_saved_cells_gate_to_cache
    apply_saved_cells_gate_to_cache(ctx)
    from review.core.selection_controller import invalidate_stats_cache
    invalidate_stats_cache(ctx)
    _changed(ctx)
    ctx.bus.plot_options_changed.emit(ctx.state.plot)


def excluded_cells(ctx) -> frozenset:
    """The usable cells of the excluded set, or an empty set."""
    st = getattr(ctx.state, "cell_sets", None)
    if st is None or not st.exclude:
        return frozenset()
    s = find(ctx, st.exclude)
    return usable_cells(ctx, s) if s is not None else frozenset()
