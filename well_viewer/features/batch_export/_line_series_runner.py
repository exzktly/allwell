"""Batch-export line collector — analogue of ``collect_bar_items_for_group``.

The on-tab line plot iterates ``app._rep_sets_active()`` + selected wells;
the batch line exporter iterates a ``BarGroup`` (members + solo wells).
This module bridges the two — it builds :class:`LineSeries` records for
a single batch group using the same per-trace stats the on-tab plot
uses, with one important deviation: rep-set members in the batch path
aggregate via ``_aggregate_group`` (pool-of-cells), matching the
existing batch line CSV's prior behaviour. The plot-tab side uses
``_compute_rep_stats`` (mean-of-per-well-means). The cross-path
numerator difference is a pre-existing issue acknowledged in
ARCHITECTURE.md §9.5 and outside this refactor's scope.

Fold-change is applied via the same helpers
(``control_pts_for_line`` → ``control_means``-style normalization),
threaded through the panel-local ``fc_state`` so a batch job is
isolated from whatever the plot-tab ctxbar has picked.
"""

from __future__ import annotations

from typing import List, Tuple

from well_viewer.domain import fold_change as _fc
from well_viewer.domain.batch_models import BarGroup
from well_viewer.features.lineplot.controller import (
    LinePoint, LineSeries, _aggpoint_to_linepoint,
)
from well_viewer.domain.stats_compute import aggregate_well
from well_viewer.domain.stats_compute import aggregate_group


def collect_line_series_for_group(
    app,
    group: BarGroup,
    *,
    threshold: float,
    use_sem: bool,
    val_col: str,
    fluor_gates,
    fc_state: Tuple[bool, str, bool] = (False, "", False),
    include_cdf: bool = False,
) -> List[LineSeries]:
    """Build :class:`LineSeries` for every member of a batch ``BarGroup``.

    Rep-set members aggregate via ``_aggregate_group`` (pool-of-cells,
    the batch-path convention); solo wells use ``_aggregate_well``.
    Fold-change is applied here using the legacy
    ``control_pts_for_line`` path so the denominator matches the batch
    numerator (also pool-of-cells). Pass ``fc_state=(False, "", False)``
    to skip normalization and emit raw series — the additive CSV
    writer makes that call alongside the active-state call.
    """
    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state
    fc_active = fc_vs_ctrl or fc_vs_t0

    # Pool-of-cells control series — matches the batch numerator's stat
    # (each rep-set is aggregated via ``_aggregate_group``).
    fc_control_means: dict = {}
    if fc_vs_ctrl and fc_ctrl_lbl:
        fc_control_means = _fc.pts_to_mean_by_t(
            _fc.control_pts_for_line(
                app, fc_ctrl_lbl, threshold=threshold,
                val_col=val_col,
                fluor_gates=fluor_gates,
            )
        )

    def _cdf_for(wells: List[str]) -> List[float]:
        if not include_cdf:
            return []
        from well_viewer.data.data_loading import all_fluor_values as _all_fluor_values
        vals: List[float] = []
        for lbl in wells:
            df = app._get_rows(lbl)
            if df is None or df.empty:
                continue
            vals.extend(_all_fluor_values(df, val_col=val_col).tolist())
        return vals

    series_out: List[LineSeries] = []
    for rset in group.members:
        valid_wells = [w for w in rset.wells if w in app._app_state.dataset.well_paths]
        if not valid_wells:
            continue
        pts = aggregate_group(app, 
            valid_wells, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
        )
        if fc_active and pts:
            pts = _fc.normalize_pts(
                pts, control_means=fc_control_means or None,
                use_t0=fc_vs_t0,
            )
        points = [_aggpoint_to_linepoint(pt) for pt in (pts or [])]
        display = app._replicate_display_label(rset)
        color = app._rank_color_rset(rset)
        series_out.append(LineSeries(
            key=rset.name, display=display, color=color,
            kind="repset", wells=valid_wells,
            points=points, cdf_vals=_cdf_for(valid_wells),
        ))
    for w in group.solo_wells:
        if w not in app._app_state.dataset.well_paths:
            continue
        pts = aggregate_well(app, 
            w, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            fluor_gates=fluor_gates,
        )
        if fc_active and pts:
            pts = _fc.normalize_pts(
                pts, control_means=fc_control_means or None,
                use_t0=fc_vs_t0,
            )
        points = [_aggpoint_to_linepoint(pt) for pt in (pts or [])]
        color = app._rank_color_well(w)
        series_out.append(LineSeries(
            key=w, display=w, color=color,
            kind="well", wells=[w],
            points=points, cdf_vals=_cdf_for([w]),
        ))
    return series_out
