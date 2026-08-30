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
from well_viewer.domain.selections_model import (
    active_replicate_sets, replicate_sets_loaded,
)
from well_viewer.domain.viewer_state import parse_rc


def plot_replicate_sets(ctx):
    """The sets a plot pools its wells into — **the one answer for every tab**.

    Normally the user's own visible replicate sets. When
    ``ctx.state.plot.group_by_variable`` names a sample variable, one set per
    value of it instead, pooling the wells that hold that value, in the
    variable's own order.

    **Why every tab goes through here.** Grouping by a variable is not a Bar-tab
    setting: if the Bar tab pooled by dose while Statistics still pooled by the
    user's groups, the two would describe the same plate differently and neither
    would say so. So the substitution happens once, at the point every view asks
    the question, and `tests/test_group_by_variable.py` fails on a caller that
    reaches past it to ``active_replicate_sets`` with a ctx in hand.

    Grouping by a variable does **not** invent a third member-key shape.
    Pooling the wells that share a dose is structurally what a replicate set
    already is, so every collector's existing grouped branch is reused — which
    is what keeps fifty-odd call sites and every saved per-sample control
    reference working.
    """
    variable_id = _group_by_variable(ctx)
    if variable_id:
        sets = variable_replicate_sets(ctx, variable_id)
        if sets:
            return sets
        # A variable that groups nothing — every well unassigned, or the
        # variable deleted while it was selected — falls back to the user's own
        # sets rather than emptying every plot in the app. The Variables tab is
        # where that shortfall is reported; a blank Bar tab three clicks away is
        # not an explanation.
    return active_replicate_sets(
        ctx.state.selection.selections, ctx.state.dataset.well_paths or {})


def plot_replicate_sets_loaded(ctx):
    """:func:`plot_replicate_sets`, but including sets the user has hidden.

    The Heat Map's rep-set average and Batch Export's group roster need the
    whole pool rather than the visible traces. **Under a variable grouping the
    two are the same list** — a value has no hidden flag — which is exactly why
    this must exist rather than callers reaching for ``replicate_sets_loaded``:
    left unconverted, four sites kept pooling by the user's groups while their
    neighbours regrouped, and the Heat Map averaged the whole plate into every
    dose cell while labelling them all with the first group's name.
    """
    variable_id = _group_by_variable(ctx)
    if variable_id:
        sets = variable_replicate_sets(ctx, variable_id)
        if sets:
            return sets
    return replicate_sets_loaded(
        ctx.state.selection.selections, ctx.state.dataset.well_paths or {})


def _group_by_variable(ctx) -> str:
    """The chosen grouping variable, tolerating a context with no plot slice.

    ``ctx.state.plot`` is absent on the headless export and CSV paths, which
    build a context holding only the slices they read. A missing slice means
    "no grouping chosen"; ``tests/test_group_by_variable.py`` pins that the real
    ``PlotState`` always carries the field, so the tolerance cannot hide a
    production context that silently stopped honouring it.
    """
    return str(getattr(getattr(ctx.state, "plot", None),
                       "group_by_variable", "") or "")


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
    active_rsets = plot_replicate_sets(ctx)
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
    active_rsets = plot_replicate_sets(ctx)
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
    by_name = {r.name: r for r in plot_replicate_sets(ctx)}
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
    if not plot_replicate_sets(ctx):
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
    out: dict[str, str] = {}
    # Through the seam, not ``active_replicate_sets``: with a variable grouping
    # active and pooling switched off, a well was inheriting its *user group's*
    # control while the plot pooled by dose — two definitions of "the set this
    # well belongs to" in one figure, and the outcome vocabulary reported OK.
    for rset in plot_replicate_sets(ctx):
        for w in rset.wells:
            out.setdefault(w, rset.name)
    return out


def member_variable_values(ctx, scope: str, variable_id: str) -> dict[str, str | None]:
    """``member key -> the value id its wells share``, for one sample variable.

    Built on :func:`member_wells` so it answers for whatever key space the plot
    is currently in — well tokens, replicate-set names, or the value labels of a
    grouped-by-variable plot — without any caller having to know which.

    **A member gets a value only when every well in it has the same one.** A
    member whose wells disagree, or that contains a single well with no value at
    all, maps to ``None``. It is not resolved to its first well, its commonest
    value, or the value of the wells that do have one: a bar built that way
    carries a dose those wells were never given and is indistinguishable from a
    correct one.

    ``None`` is a member the caller must **exclude and count**, not draw at the
    end of the axis and not silently drop. :func:`split_members_by_variable`
    does that split so every consumer reports the shortfall the same way.
    """
    from well_viewer.domain.sample_variables import MIXED, roll_up

    vset = getattr(ctx.state.selection, "variables", None)
    if vset is None or vset.by_id(variable_id) is None:
        return {}
    out: dict[str, str | None] = {}
    for key, wells in member_wells(ctx, scope).items():
        value_id = roll_up(vset, variable_id, wells) if wells else None
        out[key] = None if value_id is MIXED else value_id
    return out


def split_members_by_variable(ctx, scope: str, variable_id: str):
    """``(by_value, undefined)`` — members grouped by their value, and the rest.

    *by_value* is ``{value_id: [member keys]}`` in the **variable's** value
    order, which is the user's and is what a legend and a numeric axis read.
    *undefined* is every member with no single value, in plot order.

    Two returns rather than one dict with a ``None`` key, because the caller
    must do two different things with them: draw the first, and *say* how many
    are in the second. A shortfall that reaches only a log line is the failure
    §7 names — the plot has to carry it.
    """
    vset = getattr(ctx.state.selection, "variables", None)
    variable = vset.by_id(variable_id) if vset is not None else None
    if variable is None:
        return {}, list(plotted_member_keys(ctx, scope))

    values = member_variable_values(ctx, scope, variable_id)
    by_value: dict[str, list[str]] = {v.id: [] for v in variable.values}
    undefined: list[str] = []
    for key in plotted_member_keys(ctx, scope):
        value_id = values.get(key)
        if value_id is None:
            undefined.append(key)
        else:
            by_value.setdefault(value_id, []).append(key)
    # Values nobody holds are dropped rather than drawn empty: a declared dose
    # that no well was given is a gap in the fill, and an empty bar at its
    # position reads as a measured zero.
    return {k: v for k, v in by_value.items() if v}, undefined


def variable_replicate_sets(ctx, variable_id: str):
    """One ``ReplicateSet`` per value of *variable_id*, in the value order.

    **Grouping a plot by a variable is not a new key space.** Pooling the wells
    that share a dose into one member is structurally what a replicate set
    already is, so the plot's grouped branch is reused rather than a third
    member shape being introduced into every collector — which would have
    touched fifty-odd call sites and orphaned every saved per-sample control
    reference.

    A well with no value for the variable joins no set, so it is not drawn. That
    is the same refusal :func:`member_variable_values` makes and for the same
    reason; the count of what was left out is the caller's to report.
    """
    from well_viewer.domain.batch_models import ReplicateSet

    vset = getattr(ctx.state.selection, "variables", None)
    variable = vset.by_id(variable_id) if vset is not None else None
    if variable is None:
        return []
    well_paths = ctx.state.dataset.well_paths or {}
    out = []
    for value in variable.values:
        wells = sorted(
            (w for w in well_paths
             if vset.value_id_for(variable_id, w) == value.id),
            key=parse_rc,
        )
        if wells:
            out.append(ReplicateSet(variable.display_label(value.id), wells))
    return out


def wells_without_variable(ctx, variable_id: str) -> list[str]:
    """The dataset's wells that :func:`variable_replicate_sets` leaves out."""
    from well_viewer.domain.sample_variables import unassigned_wells

    vset = getattr(ctx.state.selection, "variables", None)
    if vset is None or vset.by_id(variable_id) is None:
        return []
    return unassigned_wells(vset, variable_id,
                            (ctx.state.dataset.well_paths or {}))
