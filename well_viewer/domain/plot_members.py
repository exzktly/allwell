"""Which members a plot draws, in the order it draws them.

One question, one answer. The per-sample fold-change editor lists a row per
plotted member and the collectors normalize per plotted member, and if those
two disagree the table describes a plot that does not exist — a member listed
but never normalized, or normalized but never listed.

The obvious source, ``ctx.runtime.barplot.current_keys()``, is **not** that
answer. It returns replicate-set names whenever any set is active, but
``collect_bar_items`` emits **well tokens** when sets are active and the
pool-replicates toggle is off. An editor built on it would show two rows while
the renderer normalized four members, which is the case this module exists to
prevent.

Deliberately narrow: it answers "which members, in what order", not what
colour they are or how their labels are formatted. Those depend on the
timepoint being drawn and on the group-by mode, and belong where they are.
``tests/test_plot_members.py`` asserts these keys **equal** the keys the
collectors emit, which is what binds the two together — the equality is the
guard, not the extraction.

Qt-free (``tests/test_qt_free_layers.py``) and free of module-level
``features/`` imports (``tests/test_layering.py``). It reaches the bar tab's
drag order through ``ctx.runtime.barplot.current_keys``, a callable the app
registers on the runtime namespace — an attribute lookup, not an import, the
same indirection ``core/scope_registry.py`` uses for redraw callables.
"""

from __future__ import annotations

from well_viewer.domain.plot_order import apply_order
from well_viewer.domain.selections_model import active_replicate_sets
from well_viewer.domain.viewer_state import parse_rc


def bar_member_keys(ctx) -> list[str]:
    """The ``BarItem.key`` values ``collect_bar_items`` will emit, in order.

    Three shapes, matching the collector's own branches:

    * replicate sets active **and** pooling on — one key per set, in the drag
      order the tab keeps;
    * replicate sets active **and** pooling off — each set expanded into its
      loaded wells, de-duplicated, in set order;
    * no replicate sets — the selected wells, in drag order.
    """
    well_paths = ctx.state.dataset.well_paths or {}
    active_rsets = active_replicate_sets(
        ctx.state.selection.selections, well_paths)
    pool_reps = bool(getattr(ctx.state.plot, "bar_pool_replicates", True))
    ordered_keys = list(ctx.runtime.barplot.current_keys() or [])

    if active_rsets and pool_reps:
        by_name = {r.name: r for r in active_rsets}
        return [k for k in ordered_keys if k in by_name]

    if active_rsets:
        # Pooling off: the bars are the sets' wells, so the key space is well
        # tokens even though the plot is "in group mode". This is the flip the
        # runtime's key list does not make.
        out: list[str] = []
        seen: set[str] = set()
        for rset in active_rsets:
            for w in rset.wells:
                if w in seen or w not in well_paths:
                    continue
                seen.add(w)
                out.append(w)
        return out

    return [k for k in ordered_keys if k in well_paths]


def line_member_keys(ctx) -> list[str]:
    """The ``LineSeries.key`` values ``collect_line_series`` will emit."""
    well_paths = ctx.state.dataset.well_paths or {}
    active_rsets = active_replicate_sets(
        ctx.state.selection.selections, well_paths)
    if active_rsets:
        ordered = apply_order(
            active_rsets,
            list(ctx.state.plot.line_order_rsets or []),
            key=lambda r: getattr(r, "name", ""),
        )
        return [r.name for r in ordered]

    selected = sorted(
        (lbl for lbl in ctx.state.selection.selected_wells if lbl in well_paths),
        key=lambda lbl: parse_rc(lbl),
    )
    return apply_order(
        selected, list(ctx.state.plot.line_order_wells or []), key=lambda x: x)


def plotted_member_keys(ctx, scope: str) -> list[str]:
    """Dispatch on the fold-change scope name (``"bar"`` / ``"line"``)."""
    if scope == "bar":
        return bar_member_keys(ctx)
    if scope == "line":
        return line_member_keys(ctx)
    return []


def member_wells(ctx, scope: str) -> dict[str, list[str]]:
    """``member key -> the loaded wells it is drawn from``, same keys as above.

    A key alone cannot say what it is made of: ``A01`` is a well token in
    per-well mode and a replicate set someone named ``A01`` in grouped mode,
    and the two have different wells. Asking a resolver to guess reintroduces
    exactly the ambiguity ``ControlRef`` exists to remove, so the mode decides
    here — where it is already known — and the answer travels with the keys.

    This is what lets "is this member its own control?" be decided by comparing
    resolved wells rather than comparing names, which is the only comparison
    that survives a well and a set sharing a token.
    """
    keys = plotted_member_keys(ctx, scope)
    well_paths = ctx.state.dataset.well_paths or {}
    by_name = {
        r.name: r
        for r in active_replicate_sets(ctx.state.selection.selections, well_paths)
    }
    grouped = _keys_are_replicate_sets(ctx, scope)
    out: dict[str, list[str]] = {}
    for key in keys:
        rset = by_name.get(key) if grouped else None
        if rset is not None:
            out[key] = [w for w in rset.wells if w in well_paths]
        else:
            out[key] = [key] if key in well_paths else []
    return out


def _keys_are_replicate_sets(ctx, scope: str) -> bool:
    """Whether *scope*'s keys name replicate sets rather than wells."""
    well_paths = ctx.state.dataset.well_paths or {}
    if not active_replicate_sets(ctx.state.selection.selections, well_paths):
        return False
    if scope == "bar":
        return bool(getattr(ctx.state.plot, "bar_pool_replicates", True))
    return scope == "line"


def member_replicate_sets(ctx) -> dict[str, str]:
    """``well token -> the replicate set that owns it``.

    What lets a well inherit its set's control when pooling is switched off:
    the key space changes from set names to well tokens, and a mapping written
    against the sets would otherwise be silently orphaned by a toggle that has
    nothing to do with fold change.
    """
    well_paths = ctx.state.dataset.well_paths or {}
    out: dict[str, str] = {}
    for rset in active_replicate_sets(ctx.state.selection.selections, well_paths):
        for w in rset.wells:
            out.setdefault(w, rset.name)
    return out
