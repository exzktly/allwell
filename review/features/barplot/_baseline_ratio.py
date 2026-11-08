"""*Then relative to*: each bar divided by its baseline bar, and the test of it.

The Bar tab's second fold-change stage (``BarPlotState.rel_*``). Stage 1 has
already made every bar a ratio to its own control — ``Mut·Drug / WT·Drug`` —
and this divides each by the bar that agrees with it on every "within each"
variable but holds the baseline value, so a mutant's ratio under the drug is
read against the same ratio under the vehicle::

    R = (Mut·Drug / WT·Drug) / (Mut·Veh / WT·Veh)

The arithmetic and the vocabulary are ``domain/ratio_of_ratios.py``; which bar
is whose baseline is ``domain/variable_pairing.match_by_variable``, run over the
same ``member_wells`` the per-sample controls resolve through, so the two
stages agree on what a bar is. The test is ``stats_tests.contrast_t_test`` over
the replicate values ``group_replicate_stats`` returns — the same records every
bar's error bar is reduced from, so the test's replicates are the bar's. It
runs on the Mean panel only, and the figure note says so.

**It is applied only where it means what it says.** It needs stage 1 to be a
per-sample control (a single control cancels out of R altogether) and vs t0
off (the replicate values the test reads are not time-normalised). Otherwise
nothing is changed and the note says why — a setting that reads as applied on
a figure it did not change is the failure this module must not produce.

Called from ``collect.collect_bar_items`` on the live pass only. Imports
nothing from ``collect`` (which imports this), so items are rebuilt with
``dataclasses.replace``.
"""

from __future__ import annotations

import math
from dataclasses import replace

from review.domain import plot_members as _members
from review.domain import ratio_of_ratios as rr
from review.domain.fold_change import resolve_control_wells
from review.domain.significance import p_phrase
from review.domain.stats_compute import group_replicate_stats
from review.domain.stats_tests import bh_adjust, contrast_t_test
from review.domain.variable_pairing import match_by_variable

_NAN = float("nan")
#: How many failures the note names before counting the rest.
_NAMED = 4


def _slice(ctx):
    reader = getattr(getattr(ctx, "state", None), "feature_slice", None)
    return reader("bar") if callable(reader) else None


def _rule_text(vset, slice_) -> tuple[str, str, str]:
    """``(variable name, value label, within names)`` for the note and the axis."""
    variable = vset.by_id(slice_.rel_variable_id) if vset is not None else None
    if variable is None:
        return "", "", ""
    within = [vset.by_id(v).name for v in (slice_.rel_within or [])
              if vset.by_id(v) is not None]
    return (variable.name, variable.display_label(slice_.rel_value_id),
            ", ".join(within))


def relative_suffix(ctx) -> str:
    """The axis/title suffix while the stage is applied, else ``""``.

    Read from the notice's own record of whether it applied, so the label and
    the bars cannot disagree: a rule that was declined adds nothing here.
    """
    slice_ = _slice(ctx)
    if slice_ is None or not getattr(slice_, "rel_applied", False):
        return ""
    vset = getattr(ctx.state.selection, "variables", None)
    name, label, _within = _rule_text(vset, slice_)
    if not name:
        return ""
    from review.domain.fold_change import parse_fc_op_ctrl
    return rr.suffix(name, label, parse_fc_op_ctrl(ctx))


def apply_relative_to(ctx, collector, items: list) -> list:
    """*items* with stage 2 applied, or unchanged with the reason recorded."""
    slice_ = _slice(ctx)
    if slice_ is None:
        return items
    slice_.rel_notice = ""
    slice_.rel_applied = False
    if not slice_.relative_to_on():
        return items
    vset = getattr(ctx.state.selection, "variables", None)
    name, label, within_names = _rule_text(vset, slice_)
    if not name or vset.by_id(slice_.rel_variable_id).value_by_id(slice_.rel_value_id) is None:
        slice_.rel_notice = ("Then relative to: not applied — its variable or "
                             "value no longer exists. Choose it again.")
        return items
    req = collector.req
    fc = req.fc
    if not (fc.vs_control_on and getattr(fc, "per_member", False)):
        slice_.rel_notice = ("Then relative to: not applied — it needs Fold change "
                             "vs a per-sample control (Control → Per-sample control…).")
        return items
    if fc.vs_t0_on:
        slice_.rel_notice = ("Then relative to: not applied — Baseline (vs t0) is "
                             "set. Turn it off to compare against "
                             f"{name} = {label}.")
        return items

    wells_for = _members.member_wells(ctx, "bar")
    matched = match_by_variable(vset, wells_for, variable_id=slice_.rel_variable_id,
                                value_id=slice_.rel_value_id,
                                within=slice_.rel_within)
    failed = {u.member: u for u in matched.unpaired}
    by_key_tp = {(it.key, float(it.tp)): it for it in items}
    op = req.fc_op_ctrl if req.fc_op_ctrl in ("ratio", "subtract") else "ratio"
    log = op == "ratio"

    # A cell of the test is a set of wells; it is named by the bar drawn from
    # exactly those wells, or by the control reference that resolved to them,
    # so the note says "WT-drug" rather than listing wells.
    cell_names = {frozenset(w): k for k, w in wells_for.items() if w}

    def control_name(key) -> str:
        ref, _outcome = collector.denoms.control_for(key)
        return str(getattr(ref, "display_name", "") or "") if ref is not None else ""

    def control_wells(key) -> tuple[str, ...] | None:
        ref, _outcome = collector.denoms.control_for(key)
        if ref is None:
            return None
        wells = tuple(resolve_control_wells(ctx, ref))
        cell_names.setdefault(frozenset(wells), getattr(ref, "display_name", "") or ",".join(wells))
        return wells

    def values(wells, tp):
        reps = group_replicate_stats(
            ctx, list(wells), tp, use_fov=req.per_fov_spread, val_col=req.val_col,
            threshold=req.threshold, fluor_gates=req.fluor_gates)
        return [r.mean for r in reps]

    out = []
    for item in items:
        stage1 = (item.mean, item.spread, item.has_mean)
        if item.key in failed:
            u = failed[item.key]
            out.append(_blank(item, stage1, rr.FROM_PAIRING.get(u.reason, rr.NO_BASELINE),
                              u.detail))
            continue
        base_key = matched.mapping[item.key]
        weights = rr.interaction_weights(
            wells_for.get(item.key), control_wells(item.key),
            wells_for.get(base_key), control_wells(base_key))
        if rr.is_reference(weights):
            out.append(replace(item, stage1=stage1, rel_baseline=base_key,
                               rel_outcome=rr.REFERENCE))
            continue
        problem = _not_crossed(vset, slice_, wells_for.get(item.key),
                               control_wells(item.key), control_wells(base_key),
                               control_name(item.key), base_key, control_name(base_key))
        if problem:
            # Without the cross the controls cancel and the "ratio of ratios"
            # is just this bar over its baseline — a plausible number on the
            # same axis, and the wrong one. Left blank and named instead.
            out.append(_blank(item, stage1, rr.NOT_CROSSED, problem, base_key))
            continue
        base = by_key_tp.get((base_key, float(item.tp)))
        if not item.has_mean:
            out.append(_blank(item, stage1, rr.NO_VALUE, "no value at this timepoint"))
            continue
        if base is None or not base.has_mean:
            out.append(_blank(item, stage1, rr.BASELINE_NO_VALUE,
                              f"its baseline {base_key} has no value at this timepoint",
                              base_key))
            continue
        mean, spread = rr.combine(item.mean, item.spread, base.mean, base.spread, op)
        frac, frac_spread = rr.combine(item.frac, item.frac_spread, base.frac,
                                       base.frac_spread, op)
        n, n_spread = rr.combine(item.n_above, item.n_above_spread, base.n_above,
                                 base.n_above_spread, op)
        named = {cell_names.get(cell) or ",".join(sorted(cell)): (cell, w)
                 for cell, w in weights.items()}
        test = contrast_t_test({n: values(c, item.tp) for n, (c, _w) in named.items()},
                               {n: w for n, (_c, w) in named.items()}, log=log)
        out.append(replace(
            item, mean=mean, spread=spread, has_mean=math.isfinite(mean),
            frac=frac, frac_spread=frac_spread, has_frac=math.isfinite(frac),
            n_above=n, n_above_spread=n_spread, stage1=stage1,
            rel_baseline=base_key, rel_outcome=rr.OK, interaction=test,
            rel_tick=f"{base_key} ÷ {control_name(base_key)}",
            interaction_label=rr.contrast_label(weights, log=log)))

    tested = [i for i, it in enumerate(out) if it.interaction is not None and it.interaction.ok]
    adjusted = bh_adjust([out[i].interaction.p for i in tested])
    for i, p_bh in zip(tested, adjusted):
        out[i] = replace(out[i], interaction_p_bh=p_bh)
    slice_.rel_applied = True
    slice_.rel_notice = _notice(out, name, label, within_names, log=log,
                                per_fov=bool(req.per_fov_spread))
    return out


def _not_crossed(vset, slice_, x_wells, x_ctrl, base_ctrl, x_ctrl_name: str,
                 base_key: str, base_ctrl_name: str) -> str:
    """Why stage 1 does not support a ratio of ratios for this bar, or ``""``.

    R is ``(X / control(X)) / (B / control(B))``, and it is the interaction
    only when the two controls are the same comparison made at the bar's own
    value and at the baseline value: control(X) holds X's value of the
    relative-to variable, control(B) holds the baseline value, and the two
    controls agree on every "within each" variable. Any other pairing makes a
    number on the same axis that answers a different question — one control
    shared by every bar cancels out entirely, leaving ``X / B``.
    """
    from review.domain.sample_variables import MIXED, roll_up

    if not x_ctrl or not base_ctrl:
        return "it or its baseline has no per-sample control"
    rel_id = slice_.rel_variable_id
    rel_name = vset.by_id(rel_id).name
    fix = (f"— pair the per-sample controls within each {rel_name} "
           f"(Control → Per-sample control… → Pair by variable)")
    if roll_up(vset, rel_id, list(x_ctrl)) != roll_up(vset, rel_id, list(x_wells or ())):
        return (f"its control {x_ctrl_name} is not at its own {rel_name}, so this "
                f"would not be a ratio of ratios {fix}")
    if roll_up(vset, rel_id, list(base_ctrl)) != slice_.rel_value_id:
        return (f"its baseline {base_key}'s control {base_ctrl_name} is not at the "
                f"baseline {rel_name}, so this would not be a ratio of ratios {fix}")
    for vid in slice_.rel_within or ():
        variable = vset.by_id(vid)
        if variable is None:
            continue
        a, b = roll_up(vset, vid, list(x_ctrl)), roll_up(vset, vid, list(base_ctrl))
        if a is None or a == MIXED or a != b:
            return (f"its control {x_ctrl_name} and its baseline's control "
                    f"{base_ctrl_name} differ on {variable.name}, so this would "
                    f"not be a ratio of ratios {fix}")
    return ""


def _blank(item, stage1, outcome: str, detail: str, base_key: str = ""):
    """A bar with no stage-2 value: drawn as a gap, named in the note."""
    return replace(item, mean=_NAN, spread=_NAN, has_mean=False, frac=_NAN,
                   frac_spread=_NAN, has_frac=False, n_above=_NAN,
                   n_above_spread=_NAN, stage1=stage1, rel_baseline=base_key,
                   rel_outcome=outcome, rel_detail=detail)


def _notice(items, name: str, label: str, within: str, *, log: bool,
            per_fov: bool) -> str:
    """The figure's note: the rule, what was tested, and every bar left out."""
    head = f"Relative to {name} = {label}" + (f" within each {within}" if within else "")
    lines = [head + ". Reference conditions are the dotted line."]
    labels = sorted({it.interaction_label for it in items if it.interaction_label})
    if labels:
        estimate = ("ratio of geometric means" if log else "difference of means")
        lines.append(
            f"p: {'; '.join(labels)}, Mean panel, BH-adjusted across bars. "
            f"The test's estimate is a {estimate}; the bar is a ratio of means.")
    if per_fov:
        lines.append("Replicates are FOVs (FOV mode is on), so a well counts "
                     "several times — the df reflect fields, not wells.")
    problems = [f"{it.display}: {it.rel_detail}" for it in items
                if it.rel_outcome not in ("", rr.OK, rr.REFERENCE) and it.rel_detail]
    problems += [f"{it.display}: no test — {it.interaction.undefined}" for it in items
                 if it.interaction is not None and not it.interaction.ok]
    if problems:
        more = len(problems) - _NAMED
        lines.append("Not computed: " + "; ".join(problems[:_NAMED])
                     + (f"; and {more} more" if more > 0 else "") + ".")
    return "\n".join(lines)


def p_text(item) -> str:
    """The label above a tested bar: ``p_BH = 0.003``, or ``p —`` when untested."""
    test = getattr(item, "interaction", None)
    if test is None:
        return ""
    if not test.ok or item.interaction_p_bh is None:
        return "p —"
    return p_phrase(item.interaction_p_bh, adjusted=True)
