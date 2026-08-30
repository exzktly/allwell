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


def response_member_keys(ctx) -> list[str]:
    """The members Response Curves draws, which are always replicate sets.

    No fallback to the selected wells, unlike the bar and line lists. A point
    on a response curve is a member at a dose, and a loose well has no dose
    until it is in something the plate annotates; the tab says so in its empty
    state rather than drawing a curve through wells nobody grouped.
    """
    return [rset.name for rset in plot_replicate_sets(ctx)]


def plotted_member_keys(ctx, scope: str) -> list[str]:
    """Dispatch on the fold-change scope name (``"bar"`` / ``"line"`` / ``"response"``)."""
    if scope == "bar":
        return bar_member_keys(ctx)
    if scope == "line":
        return line_member_keys(ctx)
    if scope == "response":
        return response_member_keys(ctx)
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


#: The scopes whose keys are replicate-set names whenever any set is active.
#: Named rather than tested with ``scope == "line"``: that spelling answered
#: *False* for every scope it had not heard of, so a third pooled tab would
#: have had its controls resolved against well tokens — silently, since a key
#: that resolves to no well simply loses its denominator.
_ALWAYS_POOLED_SCOPES = frozenset({"line", "response"})


def _keys_are_replicate_sets(ctx, scope: str) -> bool:
    """Whether *scope*'s keys name replicate sets rather than wells.

    Bar is the only scope that can flip: ``bar_pool_replicates`` turns its bars
    into wells. Line and Response always pool — Response because a dose is a
    property of a condition, and an un-pooled response curve would draw one
    point per well at the same x, which is a scatter of replicates rather than
    a curve.
    """
    if not plot_replicate_sets(ctx):
        return False
    if scope == "bar":
        return bool(getattr(ctx.state.plot, "bar_pool_replicates", True))
    return scope in _ALWAYS_POOLED_SCOPES


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


# ``member_variable_values`` and ``split_members_by_variable`` lived here and
# were removed: they answered "what value does this member hold, in whatever key
# space the plot is in" and nothing in the application asked. They were written
# for row 32's Colour-by and Split-into-panels, which are still owed — and a
# seam for an implementation that has not arrived is the thing workstream D's
# review gate had the ``columns`` parameter deleted for. The behaviour they
# expressed is not lost: ``sample_variables.roll_up`` is where the "every well
# must agree" rule lives and is tested there, and ``collect_response`` applies
# it per member. Bring them back when Colour-by needs them, not before.


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
            # ``name_is_verbatim``: these labels are the user's own, so the
            # auto-generated-name heuristic must not rewrite a dose of "10"
            # into the tokens of the wells that received it.
            out.append(ReplicateSet(variable.display_label(value.id), wells,
                                    name_is_verbatim=True))
    return out


def member_variable_values(ctx, scope: str, variable_id: str) -> dict:
    """``member key -> value id``, or ``MIXED``, or ``None``, for one scope.

    The single answer to "what is this member, in terms of this variable" — the
    question *Colour by* and *Split by* both ask, and the one a renderer must
    not answer for itself. It is built on :func:`member_wells`, so the key space
    is whatever that scope actually draws: replicate-set names where the scope
    pools, well tokens where it does not, and the two are never guessed at from
    the key's spelling (``A01`` is both a well and a set someone named ``A01``).

    Three outcomes, not two, because :func:`sample_variables.roll_up` has three
    and collapsing them here would undo the reason it has them. ``MIXED`` is a
    member whose wells disagree — a real state, reached by adding a well to a
    group after filling — and ``None`` is a member holding a well with no value
    at all. A caller that painted both the same colour would say two different
    things with one ink, and one of them ("you filled these inconsistently") is
    a mistake the user can fix while the other is not.
    """
    from well_viewer.domain.sample_variables import roll_up

    vset = getattr(ctx.state.selection, "variables", None)
    if vset is None or not variable_id or vset.by_id(variable_id) is None:
        return {}
    return {key: roll_up(vset, variable_id, wells) if wells else None
            for key, wells in member_wells(ctx, scope).items()}


def wells_without_variable(ctx, variable_id: str) -> list[str]:
    """The dataset's wells that :func:`variable_replicate_sets` leaves out."""
    from well_viewer.domain.sample_variables import unassigned_wells

    vset = getattr(ctx.state.selection, "variables", None)
    if vset is None or vset.by_id(variable_id) is None:
        return []
    return unassigned_wells(vset, variable_id,
                            (ctx.state.dataset.well_paths or {}))
