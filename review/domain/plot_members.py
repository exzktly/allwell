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

from review.domain.batch_models import ReplicateSet
from review.domain.plot_order import apply_order
from review.domain.selections_model import active_replicate_sets, replicate_sets_loaded
from review.domain.viewer_state import parse_rc


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

    **Visibility is decided here too, for both kinds of set.** The user's
    groups carry a ``hidden`` flag; a variable's values are hidden through
    ``ctx.state.plot.group_by_hidden_values``, which is what a plate click
    writes while the plots are grouped by a variable. Hiding every value
    empties the plots — it does not fall back to the user's groups, because
    the fallback is for a variable that groups nothing, not for one whose
    groups the user has switched off.

    **A solo well is a set of one.** With groups defined, a click on a well in
    no group adds it to ``active_solo_wells``; it is returned here, after the
    groups, as a :class:`ReplicateSet` with ``solo=True``, keyed by its own
    token. Every plot used to decide for itself whether solo wells existed, and
    Bar Plots and Line Graphs decided they did not — the plate lit the well
    and neither plot changed. Returning them from the one seam is what makes
    "which wells are plotted" one answer again.
    """
    variable_id = _group_by_variable(ctx)
    if variable_id:
        sets = variable_replicate_sets(ctx, variable_id)
        if sets:
            hidden = hidden_values(ctx)
            return [s for s in sets if s.value_id not in hidden]
        # A variable that groups nothing — every well unassigned, or the
        # variable deleted while it was selected — falls back to the user's own
        # sets rather than emptying every plot in the app. The Variables tab is
        # where that shortfall is reported; a blank Bar tab three clicks away is
        # not an explanation.
    well_paths = ctx.state.dataset.well_paths or {}
    sets = active_replicate_sets(ctx.state.selection.selections, well_paths)
    return sets + solo_well_sets(ctx, sets)


def hidden_values(ctx) -> frozenset[str]:
    """The value ids of the Group-by variable that the user has hidden."""
    plot = getattr(ctx.state, "plot", None)
    return frozenset(getattr(plot, "group_by_hidden_values", None) or ())


def solo_well_sets(ctx, rsets) -> list:
    """One ``solo`` :class:`ReplicateSet` per active solo well, in plate order.

    Only while the user has groups defined — solo wells are the per-well
    clicks of group mode and mean nothing outside it. A well already inside
    one of *rsets* is drawn there and is not repeated, and a well whose token
    is also a group's name is skipped rather than given a second member under
    the same key.
    """
    from review.domain.batch_models import ReplicateSet

    sel = ctx.state.selection
    if not getattr(sel, "selections", None):
        return []
    well_paths = ctx.state.dataset.well_paths or {}
    taken_wells = {w for r in rsets for w in r.wells}
    taken_names = {r.name for r in rsets}
    solo = sorted((w for w in (getattr(sel, "active_solo_wells", None) or ())
                   if w in well_paths and w not in taken_wells
                   and w not in taken_names), key=parse_rc)
    return [ReplicateSet(w, [w], solo=True) for w in solo]


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


def _response_slice(ctx):
    """The Response feature slice, or ``None`` when this ctx carries none.

    Two ways it is absent and both are legitimate: the feature is profiled out
    (``AppState.feature_slice`` answers ``None`` by design), and the headless
    export and CSV paths build a state holding only the slices they read, which
    can be a plain namespace with no ``feature_slice`` at all. Either way the
    honest reading is "this tab has chosen nothing", which is the same tolerance
    :func:`_group_by_variable` documents for the plot slice —
    ``tests/test_plot_members.py`` pins that the real ``AppState`` does carry
    the accessor, so the tolerance cannot hide a production context that
    quietly stopped answering.
    """
    getter = getattr(getattr(ctx, "state", None), "feature_slice", None)
    return getter("response") if callable(getter) else None


def response_split_variables(ctx) -> tuple[str, ...]:
    """The variables that split this tab's curves, outermost first.

    *Group by* first, then the tab's own *Series* — which is itself a list, so
    a plate varying cell line and RNA across a dose series is one curve per
    combination without spending the plate-wide grouping on the second one.
    Each is dropped when it is the axis variable (a split on the axis is one
    curve per dose with a single point on it), and a variable named twice
    counts once, in the outermost position it was named.

    Read off typed state, so this stays Qt-free: ``ResponseState`` mirrors the
    tab's controls, which is why ``x_variable_id`` lives there beside
    ``series_variable_ids`` rather than only in the widgets.
    """
    slice_ = _response_slice(ctx)
    on_x = str(getattr(slice_, "x_variable_id", "") or "") if slice_ else ""
    series = list(getattr(slice_, "series_variable_ids", ()) or []) if slice_ else []
    out: list[str] = []
    for variable_id in (_group_by_variable(ctx), *series):
        if variable_id and variable_id != on_x and variable_id not in out:
            out.append(variable_id)
    return tuple(out)


def crossed_response_sets(ctx):
    """Response's crossed members, or ``[]`` when it is not pooling that way.

    Crossing is what *Group by* means on this tab, so it is in force only when
    a grouping is chosen **and** an axis variable is picked to cross it with.
    A *Series* on its own does not cross: it splits the members the rest of the
    app already pools into, which is what it has always done and what a plate
    whose replicate sets are the conditions wants.

    One definition, because two callers need the same answer and would drift:
    :func:`response_members` builds the list, and
    :func:`_keys_are_replicate_sets` decides whether those keys name sets or
    wells. A key space that two functions disagree about is a control resolving
    against wells that do not exist, silently.
    """
    slice_ = _response_slice(ctx)
    axis = str(getattr(slice_, "x_variable_id", "") or "") if slice_ else ""
    if not (axis and _group_by_variable(ctx)):
        return []
    return crossed_replicate_sets(ctx, (axis, *response_split_variables(ctx)))


def response_members(ctx):
    """The members Response Curves draws — one per point on the figure.

    **Under a grouping, the cross of the axis variable with what splits the
    curves.** ``plot_replicate_sets`` pools every well holding a value, so
    *Group by: Cell line* hands this tab one member per cell line holding all
    six doses — and a member holding six doses has no position on a dose axis:
    it rolls up to MIXED and is refused, which emptied the figure and blamed
    the user's fill on Home → Variables. Crossing the grouping with the axis
    gives a member per (dose, cell line): each has one dose, so each is a
    point, and the cell line is what tells the curves apart.

    Otherwise unchanged, and deliberately so: with no grouping in force this is
    the visible replicate sets, and failing those one single-well set per
    selected well, in plate order. Same fallback the bar and line lists make,
    and for the same reason: a well lit on the plate is a member the user has
    asked to see.

    This used to have no fallback, on the reasoning that a loose well has no
    dose until it is in something the plate annotates. That reasoning is wrong
    about where the annotation lives. A sample variable is assigned **per well**
    — ``VariableSet.assignments`` maps a well token to a value id — so a lit
    well carries its dose whether or not anybody grouped it, and ``roll_up``
    over its one well returns that value. The tab drew nothing on a plate that
    was fully annotated, under an empty state telling the user to select wells
    on the plate, which is what they had already done.

    Returns ``ReplicateSet`` objects rather than names because the collector
    needs the wells: ``compute_rep_stats`` pools a set, and a one-well set is
    the shape that makes a lone well go down the same path as a group of four.
    """
    crossed = crossed_response_sets(ctx)
    if crossed:
        return crossed
    # A cross that matched no well — the grouping and the axis filled on
    # disjoint parts of the plate — falls back rather than emptying the figure,
    # the same choice ``plot_replicate_sets`` makes for a grouping that groups
    # nothing. What was left out is reported on the figure.
    rsets = list(plot_replicate_sets(ctx))
    if rsets:
        return rsets
    well_paths = ctx.state.dataset.well_paths or {}
    selected = sorted(
        (w for w in (ctx.state.selection.selected_wells or ()) if w in well_paths),
        key=parse_rc,
    )
    return [ReplicateSet(w, [w]) for w in selected]


def response_member_keys(ctx) -> list[str]:
    """The keys Response Curves draws, in order — set names, or well tokens."""
    return [rset.name for rset in response_members(ctx)]


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
    # **The scope's own member list, not the shared seam.** Response can pool
    # into the cross of the axis variable with what splits its curves, and those
    # names are not in ``plot_replicate_sets`` — a key looked up there would
    # resolve to no wells at all, which is silent: a member with no wells simply
    # loses its fold-change denominator and its per-sample control, with nothing
    # to say the resolution failed rather than the control being unset.
    sets = response_members(ctx) if scope == "response" else plot_replicate_sets(ctx)
    by_name = {r.name: r for r in sets}
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

    With no replicate set active every scope is well-keyed, which is the guard
    above — except under a grouping, where Response pools into crossed sets
    that exist whether or not the user has drawn a group of their own, and
    whose names are what its collector emits. Given sets, bar is the only scope
    that can flip back:
    ``bar_pool_replicates`` turns its bars into wells. Line and Response keep
    pooling — Response because a dose is a property of a condition, and an
    un-pooled response curve would draw one point per well at the same x, which
    is a scatter of replicates rather than a curve.
    """
    if scope == "response" and crossed_response_sets(ctx):
        # Crossed members are sets whether or not the user drew a group, and
        # their names are what this tab's collector emits.
        return True
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


# ``member_variable_values`` answers "what value does this member hold, in
# whatever key space the plot is in". It is defined below and is live —
# ``domain/variable_series.member_inks`` and ``member_bands`` are its callers,
# which is Colour-by and Split-by reading it exactly as planned.
#
# This comment used to say it had been *removed*, alongside a
# ``split_members_by_variable`` that was never written at all; it survived the
# commit that brought the function back sixty lines below it. The rule it
# defers to is unchanged and worth keeping: ``sample_variables.roll_up`` owns
# the "every well must agree" decision and is tested there, so nothing here
# re-derives it.


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
    from review.domain.batch_models import ReplicateSet

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
                                    name_is_verbatim=True, value_id=value.id))
    return out


def crossed_replicate_sets(ctx, variable_ids):
    """One ``ReplicateSet`` per **combination** of values across *variable_ids*.

    The wells of a combination are the wells that hold every one of its values,
    and the name joins the labels in the order the ids were given, so a plate of
    two cell lines across a dose series pools into ``HeLa · 10`` rather than
    into ``HeLa`` (six doses deep) or ``10`` (two cell lines wide).

    That is the shape a response curve needs and the shape a single grouping
    cannot give it. ``variable_replicate_sets`` pools every well holding a
    value, so grouping by `Cell line` puts all six doses of HeLa in one member —
    and a member with six doses has no position on a dose axis at all: it rolls
    up to MIXED and is refused. Crossing the grouping with the axis variable
    gives back a member per point, still a ``ReplicateSet`` and still in the one
    key space every collector and every saved control reference already uses.

    Combinations no well holds are dropped rather than drawn empty — a plate
    where only two cell lines got the top dose is a plate with two members
    there, not four, and an empty member would draw a point at no value.

    Order is each variable's own declared value order, the outer variable
    moving slowest, which is the order ``variable_replicate_sets`` follows for
    one variable and the order the legend and the colours then take.
    """
    from itertools import product

    from review.domain.batch_models import ReplicateSet
    from review.domain.sample_variables import NAME_JOIN

    vset = getattr(ctx.state.selection, "variables", None)
    variables = [vset.by_id(vid) for vid in variable_ids] if vset is not None else []
    variables = [v for v in variables if v is not None]
    if not variables:
        return []
    well_paths = ctx.state.dataset.well_paths or {}
    out = []
    for combination in product(*(v.values for v in variables)):
        wells = sorted(
            (w for w in well_paths
             if all(vset.value_id_for(v.id, w) == value.id
                    for v, value in zip(variables, combination))),
            key=parse_rc,
        )
        if not wells:
            continue
        # ``name_is_verbatim``: the labels are the user's own, so the
        # auto-generated-name heuristic must not rewrite a dose of "10" into
        # the tokens of the wells that received it.
        # ``NAME_JOIN``, not a literal: this is the same question
        # ``sample_variables.name_for_wells`` answers for a replicate group —
        # what these wells *are*, said in the values they hold — and two
        # separators for one idea is how a crossed member and a named group
        # come to read as different kinds of thing on one plate.
        name = NAME_JOIN.join(v.display_label(value.id)
                              for v, value in zip(variables, combination))
        out.append(ReplicateSet(name, wells, name_is_verbatim=True))
    return out


def variable_values_for(vset, variable_id: str, wells_by_key) -> dict:
    """``member key -> value id / MIXED / None``, from an explicit key space.

    The rule, separated from the question of *which* members are drawn. Two
    surfaces ask it over different key spaces — the plotting tabs over
    ``member_wells(ctx, scope)``, Batch Export over a ``BarGroup``'s own
    members, whose keys are the group's and not the tab's — and the one thing
    that must not differ between them is what a member's value *is*. Sharing
    the rule and letting each provide its own key space is what keeps a batch
    figure and the tab it mirrors from disagreeing about a well.
    """
    from review.domain.sample_variables import roll_up

    if vset is None or not variable_id or vset.by_id(variable_id) is None:
        return {}
    return {key: roll_up(vset, variable_id, wells) if wells else None
            for key, wells in (wells_by_key or {}).items()}


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
    return variable_values_for(
        getattr(ctx.state.selection, "variables", None), variable_id,
        member_wells(ctx, scope))


def wells_the_grouping_adds(ctx, variable_id: str) -> list[str]:
    """Wells a variable grouping draws that the plate selection does not hold.

    **The addition, not only the subtraction.** With no replicate sets defined
    the plate's selection is what drives every plot, and grouping by a variable
    ignores it: it pools every *loaded* well holding a value. One well
    selected, four drawn. That is the defensible reading of "group the plate by
    this variable", but it is a substitution the user did not ask for on that
    click, so the sentence over the figure has to name it.

    Empty unless the selection is what would otherwise drive the plot — with
    visible replicate sets defined, ``selected_wells`` is not what is plotted
    and comparing against it would report a difference nobody made. Asking that
    question is why this lives here beside :func:`plot_replicate_sets` rather
    than in the controller that prints the sentence: it has to compare the two
    poolings, so it is one of the few callers entitled to name the one the
    substitution replaced.
    """
    from review.domain.selections_model import active_replicate_sets

    selected = set(getattr(ctx.state.selection, "selected_wells", None) or ())
    if not selected:
        return []
    if active_replicate_sets(ctx.state.selection.selections,
                             ctx.state.dataset.well_paths or {}):
        return []
    drawn: set[str] = set()
    for rset in variable_replicate_sets(ctx, variable_id):
        drawn.update(getattr(rset, "wells", ()) or ())
    return sorted(drawn - selected)


def variable_grouping_in_force(ctx) -> bool:
    """Whether the plots are pooling by a variable rather than by the groups.

    Not "is a variable chosen": :func:`plot_replicate_sets` falls back to the
    user's own sets when the chosen variable groups nothing, so the setting and
    the effect disagree in exactly the case a reader most needs told apart.
    """
    variable_id = _group_by_variable(ctx)
    return bool(variable_id and variable_replicate_sets(ctx, variable_id))


def groups_the_variable_replaced(ctx) -> list:
    """The user's own visible groups, when a variable grouping has displaced them.

    Empty when the substitution is not in force **or** when the user had no
    groups to displace — which is why it is not the predicate. Those are two
    different states and a caller that read this list as "is a variable
    pooling" reported the fallback sentence over a plate that really was
    grouped by dose. Ask :func:`variable_grouping_in_force` for the predicate
    and this for the count.

    Beside :func:`wells_the_grouping_adds` and for the same reason: comparing
    the two poolings is its whole job, so it is one of the few functions
    entitled to name the one the substitution replaced. Asked here rather than
    in the controller that prints the sentence, which would then hold a second
    definition of "the user's groups" (§7.5 #10 — needing an exemption is first
    a claim about where the code lives).
    """
    if not variable_grouping_in_force(ctx):
        return []
    return list(active_replicate_sets(
        ctx.state.selection.selections or [],
        ctx.state.dataset.well_paths or {}))


def wells_without_variable(ctx, variable_id: str) -> list[str]:
    """The dataset's wells that :func:`variable_replicate_sets` leaves out."""
    from review.domain.sample_variables import unassigned_wells

    vset = getattr(ctx.state.selection, "variables", None)
    if vset is None or vset.by_id(variable_id) is None:
        return []
    return unassigned_wells(vset, variable_id,
                            (ctx.state.dataset.well_paths or {}))
