"""Batch-export line collector — analogue of ``collect_bar_items_for_group``.

The on-tab line plot is *mode-exclusive* (``collect_line_series`` draws
replicate sets **or** selected wells, never both), but a batch export
``BarGroup`` mixes replicate-set members with solo wells. This module is the
thin batch orchestrator that reuses the plotting tab's OWN statistic and
fold-change source functions per member, so batch curves match the tab:

* **replicate-set members** aggregate via ``compute_rep_stats`` /
  ``compute_rep_per_fov_stats`` — the exact mean-of-per-well-means (per-FOV)
  statistic the on-tab rep-set branch uses (NOT the old pool-of-cells
  ``aggregate_group``);
* **solo wells** aggregate via ``aggregate_well`` — the within-well cell
  statistic the on-tab per-well branch uses, so a solo well's error band is
  the same whether exported on-tab or in batch (byte-for-byte parity).

Fold-change is resolved per member through the same
``fold_change_pairing.ControlDenominators`` the tab uses and threaded through
``normalize_member_pts`` — identical error propagation.

The caller runs this inside ``BatchExportPanel._app_group_scope``, and that is
what a replicate-set control has to survive rather than what makes it work: the
scope **replaces** the app's selections with this group's members, so a control
naming a set in another batch group — the normal layout, one group per
condition — used to resolve to nothing and blank every curve. The scope carries
those sets in hidden for that reason; see its own docstring.
"""

from __future__ import annotations

import math

from well_viewer.domain import fold_change as _fc
from well_viewer.domain.batch_models import BarGroup
from well_viewer.domain.fold_change_pairing import (
    control_denominators,
    normalize_member_pts,
)
from well_viewer.domain.fold_change_series_cache import (
    ControlSeriesCache as _ControlSeriesCache,
)
from well_viewer.core.lazy import lazy as _lazy
# Lazy so batch export imports without the lineplot tab present (keeps it
# deletable). LineSeries is a class — the proxy is callable so constructor
# calls work; annotations are strings under ``from __future__`` above.
LineSeries = _lazy("well_viewer.features.lineplot.controller", "LineSeries")
_aggpoint_to_linepoint = _lazy("well_viewer.features.lineplot.controller", "_aggpoint_to_linepoint")
from well_viewer.domain.stats_compute import (
    aggregate_well,
    compute_rep_n_above_stats,
    compute_rep_n_above_total,
    compute_rep_per_fov_stats,
    compute_rep_stats,
    replicate_unit,
)
from well_viewer.domain.selections_model import (
    color_for_well as _color_for_well,
    replicate_display_label as _replicate_display_label,
    well_display_label as _well_display_label,
)
from well_viewer.data.data_loading import get_rows as _get_rows

from well_viewer.domain.selections_model import color_for_replicate_set


def _rank_color_rset(ctx, rset) -> str:
    """Rank colour for a ReplicateSet / BarGroup off ``ctx.state.selection``."""
    return color_for_replicate_set(rset, ctx.state.selection.selections)




def collect_line_series_for_group(
    ctx,
    group: BarGroup,
    *,
    threshold: float,
    use_sem: bool,
    val_col: str,
    fluor_gates,
    fc_state=_fc.FC_STATE_OFF,
    fc_baseline_tp: float | None = None,
    include_cdf: bool = False,
) -> list[LineSeries]:
    """Build :class:`LineSeries` for every member of a batch ``BarGroup``.

    Replicate-set members use the tab's ``compute_rep_stats`` /
    ``compute_rep_per_fov_stats`` (mean-of-per-well-means, per-FOV when FOV mode
    is on); solo wells use ``aggregate_well`` (within-well). Pass
    ``fc_state=(False, "", False)`` to skip normalization and emit raw series —
    the additive CSV writer makes that call alongside the active-state call.
    """
    fc = _fc.coerce_fc_state(fc_state)

    rep_per_fov = bool(ctx.state.plot.use_fov_replicates)
    fc_n_per_fov = rep_per_fov
    if fc_baseline_tp is None:
        fc_baseline_tp = _fc.parse_baseline_tp(ctx)
    fc_op_ctrl = _fc.parse_fc_op_ctrl(ctx)
    fc_op_t0 = _fc.parse_fc_op_t0(ctx)

    # Control denominators — the same resolver and the same series helpers the
    # on-tab collector uses, so the denominator statistic matches each member's
    # numerator and a per-sample mapping reaches the exported figure too.
    member_keys = ([r.name for r in group.members if any(
        w in ctx.state.dataset.well_paths for w in r.wells)]
        + [w for w in group.solo_wells if w in ctx.state.dataset.well_paths])
    member_well_map = {
        r.name: [w for w in r.wells if w in ctx.state.dataset.well_paths]
        for r in group.members
    }
    member_well_map.update({w: [w] for w in group.solo_wells})
    ctrl_cache = _ControlSeriesCache(
        ctx, threshold=threshold, val_col=val_col, use_sem=use_sem,
        per_fov_spread=fc_n_per_fov, fluor_gates=fluor_gates,
    )
    denoms = control_denominators(
        ctx, ctrl_cache, fc, member_keys, scope_wells=member_well_map,
        member_rsets={w: r.name for r in group.members for w in r.wells},
    )
    hidden_members = denoms.hidden_members

    # Same helper the on-tab collector uses, so the exported figure and the
    # on-screen one are the same plot.
    _norm_kw = dict(fc=fc, n_per_fov=fc_n_per_fov, baseline_tp=fc_baseline_tp,
                    op_ctrl=fc_op_ctrl, op_t0=fc_op_t0)

    def _normalize(pts: list, member_key: str) -> list:
        return normalize_member_pts(pts, denoms, member_key, **_norm_kw)

    def _cdf_for(wells: list[str]) -> list[float]:
        if not (include_cdf and wells):
            return []
        from well_viewer.data.data_loading import _all_fluor_values_filtered
        vals: list[float] = []
        for lbl in wells:
            df = _get_rows(ctx, lbl)
            if df is None or df.empty:
                continue
            vals.extend(_all_fluor_values_filtered(
                df, val_col=val_col, fluor_gates=fluor_gates,
                ratios=ctx.state.channel.ratio_index,
            ).tolist())
        return vals

    series_out: list[LineSeries] = []

    # ── Replicate-set members: the tab's rep-set statistic ───────────────────
    for rset in group.members:
        valid_wells = [w for w in rset.wells if w in ctx.state.dataset.well_paths]
        if not valid_wells or rset.name in hidden_members:
            continue
        rset_per_fov = replicate_unit(len(valid_wells), rep_per_fov) == "fov"
        all_tps: set = set()
        for lbl in valid_wells:
            for t, *_ in aggregate_well(ctx, lbl, threshold=threshold,
                                        use_sem=False, val_col=val_col,
                                        fluor_gates=fluor_gates):
                all_tps.add(t)
        raw_pts: list = []
        for t in sorted(all_tps):
            # Index 10 carries the cross-well total for the CSV's integer
            # column; index 4 stays the drawn per-well mean and index 9 its
            # spread, which used to be discarded here.
            n_above_total = compute_rep_n_above_total(ctx, rset, t)
            n_above_spread = 0.0
            if rset_per_fov:
                gm, gerr, gf, gfe, gnpf_m, gnpf_s = compute_rep_per_fov_stats(
                    ctx, rset, t, threshold, use_sem)
                n_above_val = 0
            else:
                gm, gerr, gf, _ = compute_rep_stats(ctx, rset, t, threshold, use_sem)
                # Per-well mean, matching the Line tab's own numerator.
                n_above_val, n_above_spread = compute_rep_n_above_stats(
                    ctx, rset, t, use_sem)
                gfe, gnpf_m, gnpf_s = 0.0, 0.0, 0.0
            if not (isinstance(gm, float) and math.isnan(gm)):
                raw_pts.append((
                    t, gm, gerr, gf, n_above_val, 0, gfe, gnpf_m, gnpf_s,
                    n_above_spread, n_above_total,
                ))
        points = [_aggpoint_to_linepoint(pt) for pt in _normalize(raw_pts, rset.name)]
        _ctrl_ref, _outcome = denoms.control_for(rset.name)
        series_out.append(LineSeries(
            key=rset.name, display=_replicate_display_label(ctx, rset),
            color=_rank_color_rset(ctx, rset), kind="repset",
            wells=valid_wells, points=points, cdf_vals=_cdf_for(valid_wells),
            fc_control=_ctrl_ref, fc_outcome=str(_outcome or ""),
        ))

    # ── Solo wells: the tab's per-well (within-well) statistic ───────────────
    for w in group.solo_wells:
        if w not in ctx.state.dataset.well_paths or w in hidden_members:
            continue
        pts = aggregate_well(ctx, w, threshold=threshold, use_sem=use_sem,
                             val_col=val_col, fluor_gates=fluor_gates,
                             per_fov_spread=rep_per_fov)
        points = [_aggpoint_to_linepoint(pt) for pt in _normalize(list(pts or []), w)]
        _ctrl_ref, _outcome = denoms.control_for(w)
        series_out.append(LineSeries(
            key=w, display=_well_display_label(ctx, w), color=_color_for_well(w),
            kind="well", wells=[w], points=points, cdf_vals=_cdf_for([w]),
            fc_control=_ctrl_ref, fc_outcome=str(_outcome or ""),
        ))

    return series_out
