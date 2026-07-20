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

Fold-change is resolved once for the whole group via the same
``member_stats_series`` helpers the tab uses and threaded through
``normalize_pts`` — identical error propagation. The caller runs this inside
``BatchExportPanel._app_group_scope`` so a replicate-set control label resolves
against the group's selections.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from well_viewer.domain import fold_change as _fc
from well_viewer.domain.batch_models import BarGroup
from well_viewer.core.lazy import lazy as _lazy
# Lazy so batch export imports without the lineplot tab present (keeps it
# deletable). LineSeries is a class — the proxy is callable so constructor
# calls work; annotations are strings under ``from __future__`` above.
LineSeries = _lazy("well_viewer.features.lineplot.controller", "LineSeries")
_aggpoint_to_linepoint = _lazy("well_viewer.features.lineplot.controller", "_aggpoint_to_linepoint")
from well_viewer.domain.stats_compute import (
    aggregate_well,
    compute_rep_n_above,
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

_rank_color_rset = _lazy("well_viewer.features.grouping.controller", "rank_color_rset")


def collect_line_series_for_group(
    ctx,
    group: BarGroup,
    *,
    threshold: float,
    use_sem: bool,
    val_col: str,
    fluor_gates,
    fc_state: Tuple[bool, str, bool] = (False, "", False),
    fc_baseline_tp: Optional[float] = None,
    include_cdf: bool = False,
) -> List["LineSeries"]:
    """Build :class:`LineSeries` for every member of a batch ``BarGroup``.

    Replicate-set members use the tab's ``compute_rep_stats`` /
    ``compute_rep_per_fov_stats`` (mean-of-per-well-means, per-FOV when FOV mode
    is on); solo wells use ``aggregate_well`` (within-well). Pass
    ``fc_state=(False, "", False)`` to skip normalization and emit raw series —
    the additive CSV writer makes that call alongside the active-state call.
    """
    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state
    fc_active = fc_vs_ctrl or fc_vs_t0

    rep_per_fov = bool(ctx.state.plot.use_fov_replicates)
    fc_n_per_fov = rep_per_fov
    if fc_baseline_tp is None:
        fc_baseline_tp = _fc.parse_baseline_tp(ctx)
    fc_op_ctrl = _fc.parse_fc_op_ctrl(ctx)
    fc_op_t0 = _fc.parse_fc_op_t0(ctx)

    # Control series — same helpers the on-tab collector uses, so the
    # denominator statistic matches each member's numerator.
    fc_control_stats: dict = {}
    fc_control_frac_stats: dict = {}
    fc_control_n_stats: dict = {}
    if fc_vs_ctrl and fc_ctrl_lbl:
        fc_control_stats = _fc.member_stats_series(
            ctx, fc_ctrl_lbl, threshold=threshold, val_col=val_col,
            use_sem=use_sem, per_fov_spread=fc_n_per_fov, fluor_gates=fluor_gates,
        )
        fc_control_frac_stats = _fc.member_frac_stats_series(
            ctx, fc_ctrl_lbl, threshold=threshold, val_col=val_col,
            use_sem=use_sem, per_fov_spread=fc_n_per_fov, fluor_gates=fluor_gates,
        )
        fc_control_n_stats = _fc.member_n_stats_series(
            ctx, fc_ctrl_lbl, threshold=threshold, val_col=val_col,
            per_fov_spread=fc_n_per_fov, fluor_gates=fluor_gates,
        )

    def _normalize(pts: list) -> list:
        if not (pts and fc_active):
            return pts
        return _fc.normalize_pts(
            pts,
            control_stats=fc_control_stats or None,
            control_frac_stats=fc_control_frac_stats or None,
            control_n_stats=fc_control_n_stats or None,
            normalize_frac_n=True, n_per_fov=fc_n_per_fov,
            use_t0=fc_vs_t0, baseline_tp=fc_baseline_tp,
            op_ctrl=fc_op_ctrl, op_t0=fc_op_t0,
        )

    def _cdf_for(wells: List[str]) -> List[float]:
        if not (include_cdf and wells):
            return []
        from well_viewer.data.data_loading import _all_fluor_values_filtered
        vals: List[float] = []
        for lbl in wells:
            df = _get_rows(ctx, lbl)
            if df is None or df.empty:
                continue
            vals.extend(_all_fluor_values_filtered(
                df, val_col=val_col, fluor_gates=fluor_gates,
                ratios=ctx.state.channel.ratio_index,
            ).tolist())
        return vals

    series_out: List["LineSeries"] = []

    # ── Replicate-set members: the tab's rep-set statistic ───────────────────
    for rset in group.members:
        valid_wells = [w for w in rset.wells if w in ctx.state.dataset.well_paths]
        if not valid_wells:
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
            if rset_per_fov:
                gm, gerr, gf, gfe, gnpf_m, gnpf_s = compute_rep_per_fov_stats(
                    ctx, rset, t, threshold, use_sem)
                n_above_val = 0
            else:
                gm, gerr, gf, _ = compute_rep_stats(ctx, rset, t, threshold, use_sem)
                n_above_val = compute_rep_n_above(ctx, rset, t)
                gfe, gnpf_m, gnpf_s = 0.0, 0.0, 0.0
            if not (isinstance(gm, float) and math.isnan(gm)):
                raw_pts.append((t, gm, gerr, gf, n_above_val, 0, gfe, gnpf_m, gnpf_s))
        points = [_aggpoint_to_linepoint(pt) for pt in _normalize(raw_pts)]
        series_out.append(LineSeries(
            key=rset.name, display=_replicate_display_label(ctx, rset),
            color=_rank_color_rset(ctx, rset), kind="repset",
            wells=valid_wells, points=points, cdf_vals=_cdf_for(valid_wells),
        ))

    # ── Solo wells: the tab's per-well (within-well) statistic ───────────────
    for w in group.solo_wells:
        if w not in ctx.state.dataset.well_paths:
            continue
        pts = aggregate_well(ctx, w, threshold=threshold, use_sem=use_sem,
                             val_col=val_col, fluor_gates=fluor_gates,
                             per_fov_spread=rep_per_fov)
        points = [_aggpoint_to_linepoint(pt) for pt in _normalize(list(pts or []))]
        series_out.append(LineSeries(
            key=w, display=_well_display_label(ctx, w), color=_color_for_well(w),
            kind="well", wells=[w], points=points, cdf_vals=_cdf_for([w]),
        ))

    return series_out
