"""Export/save service helpers extracted from runtime_app."""

from __future__ import annotations

from well_viewer.data.metric_labels import spread_label

import csv
import math
from pathlib import Path

from PySide6.QtWidgets import QMessageBox
from well_viewer.domain.numeric import is_number
from well_viewer.domain.stats_compute import aggregate_well
from well_viewer.domain.viewer_state import parse_rc
from well_viewer.domain.plot_members import plot_replicate_sets
from well_viewer.domain import fold_change_pairing as _pairing
from well_viewer.domain import fold_change_series_cache as _fc_cache
from well_viewer.domain import plot_members as _members
from well_viewer.core.channel_state_controller import (
    get_all_fluor_gates,
    on_cutoff_for_column,
)
from well_viewer.core.csv_export import ask_save_csv
from well_viewer.core.safe import swallow, swallowed
from well_viewer.core.lazy import lazy as _lazy
from well_viewer.features.export import cell_scope_compute as csc
from well_viewer.features.export.worker import DONE, CellExportWriter
# Bound lazily so export imports without scatter / barplot present — keeps those
# features deletable. Each proxy resolves its target on first call.
_scatter_collect_agg_data = _lazy("well_viewer.features.scatter.collect", "collect_scatter_agg_data")
_scatter_collect_data = _lazy("well_viewer.features.scatter.collect", "collect_scatter_data")
_scatter_points_gated = _lazy("well_viewer.features.scatter.collect", "scatter_points_gated")
_scatter_gates_for_axes = _lazy("well_viewer.features.scatter.collect", "gates_for_axes")
_scatter_axis_metric_key = _lazy("well_viewer.features.scatter.controller", "scatter_axis_metric_key")
_scatter_col_for_axis = _lazy("well_viewer.features.scatter.controller", "col_for_scatter_axis")
_scatter_sync_agg_stat_combos = _lazy("well_viewer.features.scatter.controller", "sync_scatter_agg_stat_combos")
resolve_bar_timepoints = _lazy("well_viewer.features.barplot.renderer", "resolve_bar_timepoints")
_collect_bar_items_for_app = _lazy("well_viewer.features.barplot.collect", "collect_bar_items_for_app")




def _well_labels_map(ctx) -> dict:
    """Snapshot of the Home → Well Labels map (token → name)."""
    return dict(ctx.state.dataset.well_labels or {})


def _well_paths_keys(ctx) -> set:
    """Set of valid well tokens for the loaded project."""
    paths = ctx.state.dataset.well_paths or {}
    return set(paths)


def well_name_for(token, well_labels: dict, *, well_paths: set | None = None,
                  strict: bool = False) -> str:
    """Look up the Home → Well Labels display name for a well token.

    Falls back to the token itself when no custom name is set, matching
    the editor convention "blank = use well token". When ``strict`` is
    True the lookup returns "" for tokens not present in ``well_paths``,
    so columns that mix well tokens with non-well identifiers (group
    names, replicate-set names) leave non-well rows blank.
    """
    if token is None:
        return ""
    s = str(token).strip()
    if not s:
        return ""
    if strict and well_paths is not None and s not in well_paths and s not in well_labels:
        return ""
    return well_labels.get(s, s)


def well_names_joined(tokens, well_labels: dict, *,
                      well_paths: set | None = None,
                      sep: str = ";", strict: bool = False) -> str:
    """Map a separator-joined string of well tokens to display names."""
    if tokens is None:
        return ""
    text = str(tokens).strip()
    if not text:
        return ""
    parts = [p.strip() for p in text.split(sep)]
    names = [well_name_for(p, well_labels, well_paths=well_paths, strict=strict)
             for p in parts]
    if strict and not any(names):
        return ""
    return sep.join(names)


# ── Shared metric-row builders ───────────────────────────────────────────────
#
# These helpers define the canonical CSV schema for line- and bar-plot
# exports. Both the per-tab "Export CSV" buttons and the Batch Export tab
# call these so the metric columns (mean / spread / fraction / counts /
# threshold / metric) stay byte-identical across paths. Identifying columns
# (well vs group/member/...) are added by the caller, since those legitimately
# differ between per-tab and batch exports.

def line_metric_fieldnames(ch: str, metric: str, band_lbl: str) -> list:
    """CSV columns for a single line-plot timepoint row, in order."""
    return [
        "time_h",
        f"mean_{ch}_{metric}",
        f"{band_lbl.lower()}_{ch}_{metric}",
        "n_above_threshold",
        "n_above_replicate_mean",
        f"err_n_above_replicate_{band_lbl}",
        "n_above_replicate_unit",
        "fraction_above",
        "n_total",
        "threshold",
        "metric",
    ]


def line_metric_row(pt, *, ch: str, metric: str, threshold: float,
                    band_lbl: str, per_fov: bool = False) -> dict:
    """Metric-column dict for one line-plot AggPoint.

    *pt* is an aggregation tuple ``(t, mean, spread, frac, n_above, n_total, ...)``
    as returned by ``_aggregate_well`` / ``_aggregate_group``. The ``spread``
    field is SEM when the aggregator was called with ``use_sem=True``, SD
    otherwise; *band_lbl* must be set accordingly so the column header
    reflects what the data represents.

    The three Events columns are the bar CSV's, for the same reason: the
    integer ``n_above_threshold`` and the fractional
    ``n_above_replicate_mean`` are different numbers, and without
    ``n_above_replicate_unit`` naming what the mean is over, nothing else in
    the row records whether Aggregate-FOVs was on. *per_fov* supplies that —
    the point itself cannot say.
    """
    t, mean, spread, frac, n_above, n_total, *_rest = pt
    # Index 10, when present, is the cross-well total for a replicate set; the
    # drawn value at index 4 is that set's per-well mean and is fractional, so
    # the integer column cannot be derived from it.
    n_above_total = _rest[4] if len(_rest) > 4 and _rest[4] is not None else None
    # Index 9 is the cross-well spread behind that mean. Only a replicate
    # aggregation has one, which is the same condition the unit column reports:
    # a single well's row leaves both empty rather than writing 0.000000, which
    # would read as "the replicates agreed" on a row that had no replicates.
    n_above_spread = _rest[3] if len(_rest) > 3 and _rest[3] is not None else None
    is_replicate = n_above_total is not None
    return {
        "time_h": f"{t:.4f}",
        f"mean_{ch}_{metric}": f"{mean:.6f}" if not math.isnan(mean) else "",
        # Empty, not the string "nan". The bar CSV's own writer blanks a
        # non-finite spread here, and a spreadsheet reads a literal ``nan``
        # cell as text — so the column stops being numeric for every row below
        # it, and a chart built on it silently drops the series.
        f"{band_lbl.lower()}_{ch}_{metric}": (
            f"{spread:.6f}" if is_number(spread) else ""),
        "n_above_threshold": (
            int(n_above_total) if n_above_total is not None else n_above),
        "n_above_replicate_mean": (
            f"{float(n_above):.6f}"
            if is_number(n_above)
            else ""),
        f"err_n_above_replicate_{band_lbl}": (
            f"{float(n_above_spread):.6f}"
            if is_replicate and n_above_spread is not None
            and math.isfinite(float(n_above_spread)) else ""),
        "n_above_replicate_unit": (
            ("fov" if per_fov else "well") if is_replicate else ""),
        "fraction_above": f"{frac:.6f}" if not math.isnan(frac) else "",
        "n_total": n_total,
        "threshold": f"{threshold:.4f}",
        "metric": metric,
    }


def _replicate_unit_for(item, *, per_fov: bool) -> str:
    """What ``n_above_replicate_mean`` averages over, for the CSV.

    ``fov`` when Aggregate-FOVs is on, ``well`` for a pooled replicate set,
    and empty for a single well — where the drawn value is the count itself
    and there is nothing to average. Recorded because no other column in the
    row says whether the FOV toggle was on, which would leave the mean and its
    spread ambiguous.
    """
    if item.n_above_total is None:
        return ""
    return "fov" if per_fov else "well"


def bar_metric_fieldnames(ch: str, metric: str, band_lbl: str) -> list:
    """CSV columns for a single bar-plot timepoint row, in order."""
    return [
        "timepoint_h",
        f"mean_{ch}_{metric}",
        f"err_{band_lbl}_{ch}_{metric}",
        "fraction_above",
        f"err_frac_{band_lbl}",
        "n_above_threshold",
        f"err_n_above_{band_lbl}",
        "n_above_replicate_mean",
        f"err_n_above_replicate_{band_lbl}",
        "n_above_replicate_unit",
        "threshold",
        "metric",
    ]


def bar_metric_row(
    *,
    mean: float,
    spread: float,
    frac: float,
    frac_spread: float,
    has: bool,
    n_above: float,
    n_above_spread: float,
    ch: str,
    metric: str,
    tp_str: str,
    threshold: float,
    band_lbl: str,
    n_above_total: int | None = None,
    replicate_unit: str = "",
) -> dict:
    """Metric-column dict for one bar-plot bar at a single timepoint.

    The arguments mirror the per-bar values used by both ``_collect_bar_items``
    (per-tab) and direct ``_aggregate_*`` calls (batch). ``n_above_spread > 0``
    indicates per-FOV spread is in use, which switches *n_above* to a float
    and exposes the per-FOV error column.

    Two quantities are reported for Events, because they are different
    numbers. ``n_above_threshold`` is the integer count this column has always
    carried — for a replicate set, the total across its wells, supplied as
    *n_above_total*. ``n_above_replicate_mean`` is what the panel actually
    draws: the mean of the per-well counts, with its cross-well spread. A
    two-well set holding 7 and 8 events reports 15 and 7.5 respectively, and
    neither is a rounding of the other.

    *replicate_unit* names what the mean is over (``well`` / ``fov``, empty for
    a single well). Without it the two new columns are ambiguous, since nothing
    else in the row records whether Aggregate-FOVs was on.
    """
    # An undefined spread is written as an EMPTY cell, never as 0.000000 — the
    # zero reads as "the replicates agreed exactly" when in fact there was only
    # one replicate (or none, when the FOV labels were unusable) and nothing was
    # computed. Anything downstream that averages these columns would otherwise
    # take a fabricated zero as data.
    def _num(v) -> str:
        return f"{float(v):.6f}" if (v is not None and math.isfinite(float(v))) else ""

    return {
        "timepoint_h": tp_str,
        f"mean_{ch}_{metric}": _num(mean) if has else "",
        f"err_{band_lbl}_{ch}_{metric}": _num(spread) if has else "",
        "fraction_above": _num(frac) if has else "",
        f"err_frac_{band_lbl}": _num(frac_spread) if has and not math.isnan(frac) else "",
        "n_above_threshold": (
            "" if not has or not math.isfinite(float(n_above))
            # A replicate set reports its cross-well total, which is what this
            # column has always meant. A single well has no total — the
            # collectors pass ``None`` — so it falls through to ``n_above``,
            # which is the count itself with Aggregate-FOVs off and the
            # per-FOV mean, in the historic float form, with it on.
            else f"{int(n_above_total)}" if n_above_total is not None
            else f"{n_above:.6f}" if math.isfinite(float(n_above_spread)) and n_above_spread > 0
            else f"{int(n_above)}"
        ),
        f"err_n_above_{band_lbl}": f"{n_above_spread:.6f}" if n_above_spread > 0 else "",
        "n_above_replicate_mean": _num(n_above) if has else "",
        # A two-well set holding 5 and 5 events has a spread of exactly 0.0,
        # which is a measurement. Blanking it here conflated "the replicates
        # agreed" with "there was one replicate and nothing could be computed"
        # — the second is what the header row's own documentation promises the
        # blank means. The unit column beside it says which case it is: empty
        # there, and only there, means there was nothing to spread over.
        f"err_n_above_replicate_{band_lbl}": (
            _num(n_above_spread) if replicate_unit and math.isfinite(
                float(n_above_spread)) else ""),
        "n_above_replicate_unit": replicate_unit,
        "threshold": f"{threshold:.4f}",
        "metric": metric,
    }


def _control_cell(ref) -> str:
    """What a ``fold_change_control`` cell says for one row.

    The plain token. The combo's ``" (well)"`` disambiguation is chrome and has
    no business in an exported column — it was part of the stored value once,
    and it leaked into exactly here.
    """
    return getattr(ref, "display_name", "") if ref is not None else ""


def _fc_mode_str(vs_control: bool, vs_t0: bool) -> str:
    """Compact textual mode for the fold-change CSV columns."""
    parts: list = []
    if vs_control:
        parts.append("control")
    if vs_t0:
        parts.append("t0")
    return "+".join(parts) or "off"


def bar_fold_change_fieldnames(band_lbl: str) -> list:
    """Additive fold-change CSV columns appended after the raw bar columns."""
    return [
        "fold_change_mean",
        f"fold_change_{band_lbl.lower()}",
        "fold_change_mode",
        "fold_change_control",
        "fold_change_outcome",
    ]


def attach_bar_fold_change_columns(
    row: dict,
    *,
    fc_mean: float,
    fc_spread: float,
    fc_has: bool,
    band_lbl: str,
    vs_control: bool,
    vs_t0: bool,
    control_label: str,
    outcome: str = "",
) -> None:
    """Append the fold-change columns to a bar-plot CSV row in place.

    The raw ``mean_*`` / ``err_*`` columns stay as written by
    :func:`bar_metric_row`; this helper carries the post-normalization
    values plus mode / control annotations on the right of the row. Used
    by both the on-tab CSV (``export_bar_plot_data``) and the batch CSV.

    ``control_label`` is **this row's** control, not the figure's: under
    per-sample controls the rows differ, and a single value repeated down the
    column would misstate every denominator but one. ``outcome`` is the only
    thing separating "nobody assigned a control" from "the control is gone"
    from "the control has no data at this timepoint" — all three leave
    ``fold_change_mean`` empty, and a reader with only that column cannot tell
    a normalization that failed from one that was never asked for.
    """
    row["fold_change_mean"] = (
        f"{fc_mean:.6f}" if fc_has and not math.isnan(fc_mean) else ""
    )
    row[f"fold_change_{band_lbl.lower()}"] = (
        f"{fc_spread:.6f}" if fc_has else ""
    )
    row["fold_change_mode"] = _fc_mode_str(vs_control, vs_t0)
    row["fold_change_control"] = control_label if vs_control else ""
    row["fold_change_outcome"] = str(outcome or "") if vs_control else ""


def _warn(parent, title: str, msg: str) -> None:
    QMessageBox.warning(parent, title, msg)


def _error(parent, title: str, msg: str) -> None:
    QMessageBox.critical(parent, title, msg)


def export_plot_data(ctx) -> None:
    from well_viewer.domain import fold_change as _fc

    selected = sorted(ctx.state.selection.selected_wells, key=lambda lbl: parse_rc(lbl))
    well_to_repset: dict = {}
    if not selected:
        # The line plot falls back to active replicate sets when nothing is
        # manually selected; the CSV export must do the same instead of
        # refusing — otherwise "Export CSV" reports "no wells selected" while
        # the plot is clearly showing rep-set data. Emit per-well rows for the
        # rep-set members (deduped, order preserved) plus a replicate_set
        # column so the grouping isn't lost.
        seen: set = set()
        rep_sets = plot_replicate_sets(ctx)
        for rset in (rep_sets or []):
            for w in rset.wells:
                if w in ctx.state.dataset.well_paths and w not in seen:
                    seen.add(w)
                    selected.append(w)
                    well_to_repset[w] = getattr(rset, "name", "")
    if not selected:
        _warn(ctx.window, "Export", "No wells selected. Select wells in the picker, or define a replicate set.")
        return
    ch = ctx.state.channel.active_channel
    metric = ctx.state.channel.active_metric
    threshold = on_cutoff_for_column(
        ctx, ctx.state.channel.active_val_col, channel=ch)
    use_sem = bool(ctx.state.plot.use_sem)
    band_lbl = spread_label(use_sem)
    fluor_gates = get_all_fluor_gates(ctx)
    well_labels = _well_labels_map(ctx)
    include_repset_col = bool(well_to_repset)

    fc = _fc.fold_change_state(ctx)
    fc_vs_ctrl = fc.vs_control_on
    fc_vs_t0 = fc.vs_t0_on
    fc_active = fc.active
    # The same resolver the tab's collector uses. This path used to build one
    # pooled control series by hand and hand it to ``normalize_pts``, which
    # gave it three separate ways to disagree with the figure it claims to
    # describe: no per-sample mapping, no blanking when the control did not
    # resolve, and a control column that printed the reference's repr.
    _ctrl_cache = _fc_cache.ControlSeriesCache(
        ctx, threshold=threshold, val_col=ctx.state.channel.active_val_col,
        use_sem=use_sem, per_fov_spread=bool(ctx.state.plot.use_fov_replicates),
        fluor_gates=fluor_gates,
    )
    _denoms = _pairing.control_denominators(
        ctx, _ctrl_cache, fc,
        _members.line_member_keys(ctx) if fc.per_member else (),
        scope_wells=_members.member_wells(ctx, "line") if fc.per_member else None,
        member_rsets=_members.member_replicate_sets(ctx) if fc.per_member else None,
    )
    _norm_kw = dict(fc=fc, n_per_fov=bool(ctx.state.plot.use_fov_replicates),
                    baseline_tp=_fc.parse_baseline_tp(ctx),
                    op_ctrl=_fc.parse_fc_op_ctrl(ctx),
                    op_t0=_fc.parse_fc_op_t0(ctx))

    rows_out = []
    for label in selected:
        pts = aggregate_well(ctx,
            label, threshold=threshold, use_sem=use_sem,
            val_col=ctx.state.channel.active_val_col,
            fluor_gates=fluor_gates,
        )
        # A rep-set member exports as its own wells here, so the mapping is
        # asked about the set that owns the well when one does; falling back to
        # the well token covers the per-well case.
        _member_key = well_to_repset.get(label) or label
        _ctrl_ref, _outcome = _denoms.control_for(_member_key)
        norm_pts = (
            _pairing.normalize_member_pts(
                list(pts), _denoms, _member_key, **_norm_kw)
            if fc_active else None
        )
        for i, pt in enumerate(pts):
            row = {
                "well": label,
                "well_name": well_name_for(label, well_labels),
            }
            if include_repset_col:
                row["replicate_set"] = well_to_repset.get(label, "")
            row.update(line_metric_row(
                pt, ch=ch, metric=metric, threshold=threshold,
                band_lbl=band_lbl,
                per_fov=bool(ctx.state.plot.use_fov_replicates)))
            if fc_active and norm_pts is not None and i < len(norm_pts):
                npt = norm_pts[i]
                fmean = npt[1] if not (isinstance(npt[1], float) and math.isnan(npt[1])) else float("nan")
                row["fold_change_mean"] = (
                    f"{fmean:.6f}" if is_number(fmean) else ""
                )
                fspread = npt[2]
                row[f"fold_change_{band_lbl.lower()}"] = (
                    f"{fspread:.6f}" if is_number(fspread) else ""
                )
                row["fold_change_mode"] = _fc_mode_str(fc_vs_ctrl, fc_vs_t0)
                row["fold_change_control"] = (
                    _control_cell(_ctrl_ref) if fc_vs_ctrl else "")
                row["fold_change_outcome"] = (
                    str(_outcome or "") if fc_vs_ctrl else "")
            rows_out.append(row)
    if not rows_out:
        _warn(ctx.window, "Export", "No data to export for the current selection.")
        return
    out_path = ask_save_csv(ctx, "Export plot data", f"{ch}_{metric}_plot_export.csv")
    if not out_path:
        return
    base_cols = ["well", "well_name"] + (["replicate_set"] if include_repset_col else [])
    fieldnames = base_cols + line_metric_fieldnames(ch, metric, band_lbl)
    if fc_active:
        fieldnames += [
            "fold_change_mean",
            f"fold_change_{band_lbl.lower()}",
            "fold_change_mode",
            "fold_change_control",
            "fold_change_outcome",
        ]
    try:
        with open(out_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows_out)
        ctx.notify.set_status(f"Exported {len(rows_out)} row(s) to {Path(out_path).name}")
    except OSError as exc:
        _error(ctx.window, "Export failed", str(exc))


def export_bar_plot_data(ctx) -> None:
    from well_viewer.domain import fold_change as _fc
    from well_viewer.features.barplot.controller import FC_STATE_OFF

    # Pull EVERY selected timepoint from the multi-select popup, falling
    # back to the hidden single-tp combo only when the popup hasn't been
    # populated yet (legacy single-tp behaviour). Previously the export
    # read ``_bar_tp_cb.currentText()`` directly — that carries just the
    # first checked timepoint, so a multi-tp selection silently dropped
    # every additional one from the CSV.
    tp_list = resolve_bar_timepoints(ctx)
    if not tp_list:
        _warn(ctx.window, "Export", "Select a timepoint first.")
        return
    target_ts = [t for t, _ in tp_list]
    target_t_arg = target_ts[0] if len(target_ts) == 1 else target_ts
    fc = _fc.fold_change_state(ctx)
    fc_vs_ctrl = fc.vs_control_on
    fc_vs_t0 = fc.vs_t0_on
    fc_active = fc_vs_ctrl or fc_vs_t0
    # Additive CSV: raw values in the standard ``mean_*`` / ``err_*``
    # columns, fold-change values in their own columns. The aggregation
    # helpers are cached so the second call to collect_bar_items is cheap.
    use_groups, items_raw, band_lbl = _collect_bar_items_for_app(
        ctx, target_t_arg, fc_state=FC_STATE_OFF,
    )
    if fc_active:
        _, items_fc, _ = _collect_bar_items_for_app(ctx, target_t_arg)
        # Match raw items by (key, tp) — same key can appear at multiple tps
        # in a multi-tp export, so a single ``key → fc_item`` map would
        # collapse them all onto the last timepoint's FC values.
        fc_by_key_tp = {(it.key, float(it.tp)): it for it in items_fc}
    else:
        fc_by_key_tp = {}
    ch = ctx.state.channel.active_channel
    metric = ctx.state.channel.active_metric
    threshold = on_cutoff_for_column(
        ctx, ctx.state.channel.active_val_col, channel=ch)
    well_labels = _well_labels_map(ctx)
    well_paths = _well_paths_keys(ctx)
    rows_out = []
    id_cols = (["name", "well_name"] if use_groups else ["well", "well_name"])
    for item in items_raw:
        if use_groups:
            row = {
                "name": item.key,
                "well_name": well_name_for(item.key, well_labels,
                                           well_paths=well_paths, strict=True),
            }
        else:
            row = {
                "well": item.key,
                "well_name": well_name_for(item.key, well_labels),
            }
        # Use each item's own timepoint string so multi-tp rows differ
        # in the ``timepoint_h`` column. Format matches ``BarItem.tp``
        # convention from ``collect_bar_items`` (``f"{tp:.4g}"``).
        item_tp_str = f"{float(item.tp):.4g}"
        row.update(bar_metric_row(
            mean=item.mean, spread=item.spread,
            frac=item.frac, frac_spread=item.frac_spread,
            has=item.has_mean,
            n_above=float(item.n_above),
            n_above_spread=float(item.n_above_spread),
            n_above_total=item.n_above_total,
            replicate_unit=_replicate_unit_for(
                item, per_fov=bool(ctx.state.plot.use_fov_replicates)),
            ch=ch, metric=metric, tp_str=item_tp_str,
            threshold=threshold, band_lbl=band_lbl,
        ))
        if fc_active:
            fc_item = fc_by_key_tp.get((item.key, float(item.tp)))
            if fc_item is not None:
                attach_bar_fold_change_columns(
                    row, fc_mean=fc_item.mean, fc_spread=fc_item.spread,
                    fc_has=fc_item.has_mean, band_lbl=band_lbl,
                    vs_control=fc_vs_ctrl, vs_t0=fc_vs_t0,
                    control_label=_control_cell(fc_item.fc_control),
                    outcome=fc_item.fc_outcome,
                )
        rows_out.append(row)
    if not rows_out:
        _warn(ctx.window, "Export", "No data to export.")
        return
    tp_label_for_filename = (
        tp_list[0][1] if len(tp_list) == 1
        else f"{tp_list[0][1]}-{tp_list[-1][1]}"
    )
    out_path = ask_save_csv(
        ctx, "Export bar plot data",
        f"bar_t{tp_label_for_filename}.csv",
    )
    if not out_path:
        return
    fieldnames = id_cols + bar_metric_fieldnames(ch, metric, band_lbl)
    if fc_active:
        fieldnames += bar_fold_change_fieldnames(band_lbl)
    try:
        with open(out_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows_out)
        ctx.notify.set_status(f"Exported {len(rows_out)} row(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(ctx.window, "Export failed", str(exc))


def _axis_column_names(name_x: str, name_y: str) -> tuple[str, str]:
    """Column names for the two scatter axes, disambiguated only if they clash.

    A scatter with the same statistic (or the same column) on both axes is a
    legitimate plot — mean-vs-mean across two timepoints, a metric against
    itself under different gating. The row dicts and the ``DictWriter``
    fieldnames were keyed by the statistic's own name, so in that case the two
    axes wrote to one key: Y overwrote X, and the CSV carried Y twice under one
    header while claiming to hold both.

    Names that differ are left exactly as they were, so the ordinary export's
    headers are unchanged and downstream scripts keep working; only the
    colliding case gains an ``_x`` / ``_y`` suffix.
    """
    if name_x != name_y:
        # The aggregate exporter derives an ``_error`` column from each of
        # these, so two names that differ can still collide once suffixed:
        # ``("A", "A_error")`` yields ``A_error`` twice. Disambiguate on the
        # derived names too.
        if f"{name_x}_error" != name_y and f"{name_y}_error" != name_x:
            return name_x, name_y
    return f"{name_x}_x", f"{name_y}_y"


def export_scatter_data(ctx) -> None:
    """Export scatter plot data to CSV."""
    from well_viewer.core.channel_state_controller import get_fluor_gate
    ch_x_cb = ctx.widgets.get_or_none("_scatter_ch_x_cb")
    ch_y_cb = ctx.widgets.get_or_none("_scatter_ch_y_cb")
    try:
        ch_x_entry = ch_x_cb.currentText()
        ch_y_entry = ch_y_cb.currentText()
    except (ValueError, AttributeError):
        _warn(ctx.window, "Export", "Select channels and timepoint first.")
        return

    # Pick every active timepoint from the multi-tp selector, falling
    # back to the legacy single-tp combo when the picker hasn't been
    # populated (very first build).
    selections = ctx.runtime.scatter.tp_selections or {}
    timepoints_h: list[float] = []
    for tp_str, on in selections.items():
        if not on:
            continue
        try:
            timepoints_h.append(float(tp_str))
        except (TypeError, ValueError):
            continue
    if not timepoints_h:
        try:
            tp_str = ctx.widgets.get_or_none("_scatter_tp_cb").currentText()
            if tp_str:
                timepoints_h = [float(tp_str)]
        except (TypeError, ValueError, AttributeError):
            pass
    if not timepoints_h:
        _warn(ctx.window, "Export", "Select at least one timepoint first.")
        return

    ch_x_base = ch_x_entry.split(" ")[0]
    ch_y_base = ch_y_entry.split(" ")[0]
    # Per-axis Property combos drive column selection.
    metric_x = _scatter_axis_metric_key(ctx, "x")
    metric_y = _scatter_axis_metric_key(ctx, "y")
    col_x = _scatter_col_for_axis(ctx, ch_x_entry, metric_x)
    col_y = _scatter_col_for_axis(ctx, ch_y_entry, metric_y)

    fluor_gate_x = get_fluor_gate(ctx, ch_x_base)
    fluor_gate_y = get_fluor_gate(ctx, ch_y_base)

    # This is the *tab's* Export CSV button, so it exports what the tab is
    # drawing. With "Apply gates" on, the plot shows only the cells inside the
    # gate boxes and the CSV used to carry the whole cloud — the two disagreed
    # about which cells the figure is about, which is the one thing an export
    # beside a plot must not do. (Batch export deliberately does not inherit
    # this live toggle; see
    # ``batch_export.scatter_panel._warn_if_live_gates_ignored`` and
    # ARCHITECTURE §5.6.)
    scatter_data = _scatter_collect_data(
        ctx, col_x, col_y, timepoints_h,
        well_colors=[],
        fluor_gate_x=fluor_gate_x,
        fluor_gate_y=fluor_gate_y,
        gates=(_scatter_gates_for_axes(ctx, col_x, col_y)
               if _scatter_points_gated(ctx) else None),
    )

    name_x, name_y = _axis_column_names(col_x, col_y)
    well_labels = dict(ctx.state.dataset.well_labels or {})
    well_paths = set(ctx.state.dataset.well_paths or {})
    rows_out = []
    for label, data in scatter_data.items():
        x_vals = data['x']
        y_vals = data['y']
        tp_vals = data.get('tp') or [float('nan')] * len(x_vals)
        well_name = well_name_for(label, well_labels,
                                  well_paths=well_paths, strict=True)
        for x, y, tp in zip(x_vals, y_vals, tp_vals):
            rows_out.append({
                "group_well": label,
                "well_name": well_name,
                name_x: f"{x:.6f}",
                name_y: f"{y:.6f}",
                "timepoint_h": f"{tp:.4f}",
            })

    if not rows_out:
        _warn(ctx.window, "Export", "No data to export for the current selection.")
        return

    tp_tag = (f"t{timepoints_h[0]:g}" if len(timepoints_h) == 1
              else f"t{min(timepoints_h):g}-{max(timepoints_h):g}")
    out_path = ask_save_csv(
        ctx, "Export scatter plot data",
        f"scatter_{ch_x_base}_vs_{ch_y_base}_{tp_tag}.csv",
    )
    if not out_path:
        return

    try:
        with open(out_path, "w", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["group_well", "well_name", name_x, name_y, "timepoint_h"])
            writer.writeheader()
            writer.writerows(rows_out)
        ctx.notify.set_status(f"Exported {len(rows_out)} datapoint(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(ctx.window, "Export failed", str(exc))


def export_scatter_agg_data(ctx) -> None:
    """Export aggregate scatter plot data to CSV."""
    # Keep the legacy stat combos in sync with the new per-axis
    # (channel, property) state — the export reads from those.
    try:
        _scatter_sync_agg_stat_combos(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)

    try:
        stat_x = ctx.widgets.get_or_none("_scatter_agg_stat_x_cb").currentText()
        stat_y = ctx.widgets.get_or_none("_scatter_agg_stat_y_cb").currentText()

        selected_timepoints: list[float] = []
        tp_checks = ctx.runtime.scatter.agg_tp_checks
        if tp_checks:
            for tp_str, widget in tp_checks.items():
                try:
                    if widget.isChecked():
                        selected_timepoints.append(float(tp_str))
                except Exception:
                    swallowed()  # was: pass (silent except)
            selected_timepoints.sort()

        if not selected_timepoints:
            _warn(ctx.window, "Export", "Please select at least one timepoint.")
            return

    except (ValueError, AttributeError, IndexError):
        _warn(ctx.window, "Export", "Select statistics and timepoints first.")
        return

    scatter_data = _scatter_collect_agg_data(
        ctx, stat_x, stat_y, selected_timepoints,
        well_colors=[],
    )

    name_x, name_y = _axis_column_names(stat_x, stat_y)
    well_labels = dict(ctx.state.dataset.well_labels or {})
    well_paths = set(ctx.state.dataset.well_paths or {})
    rows_out = []
    for label, data in scatter_data.items():
        x_val = data['x'][0]
        y_val = data['y'][0]
        x_err = data['x_err'][0]
        y_err = data['y_err'][0]
        tp = data['timepoint']
        rep_well = label.split("_tp")[0]

        rows_out.append({
            "replicate_well": rep_well,
            "well_name": well_name_for(rep_well, well_labels,
                                       well_paths=well_paths, strict=True),
            "timepoint_h": f"{tp:.4f}",
            name_x: f"{x_val:.6f}",
            f"{name_x}_error": f"{x_err:.6f}",
            name_y: f"{y_val:.6f}",
            f"{name_y}_error": f"{y_err:.6f}",
        })

    if not rows_out:
        _warn(ctx.window, "Export", "No data to export for the current selection.")
        return

    tp_range = f"t{min(selected_timepoints):.1f}-{max(selected_timepoints):.1f}"
    stat_x_safe = stat_x.replace(" ", "_").lower()
    stat_y_safe = stat_y.replace(" ", "_").lower()

    out_path = ask_save_csv(
        ctx, "Export aggregate scatter plot data",
        f"scatter_agg_{stat_x_safe}_vs_{stat_y_safe}_{tp_range}.csv",
    )
    if not out_path:
        return

    try:
        with open(out_path, "w", newline="") as fh:
            fieldnames = ["replicate_well", "well_name", "timepoint_h",
                          name_x, f"{name_x}_error", name_y, f"{name_y}_error"]
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows_out)
        ctx.notify.set_status(f"Exported {len(rows_out)} datapoint(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(ctx.window, "Export failed", str(exc))


def export_heatmap_data(ctx) -> None:
    """Export the currently displayed heatmap grid values to CSV."""
    import math as _math
    arr = ctx.runtime.heatmap.array
    layout = ctx.runtime.heatmap.layout_active
    cell_index = ctx.runtime.heatmap.cell_well_index
    if arr is None or layout is None:
        _warn(ctx.window, "Export", "No heatmap data — draw the heatmap first.")
        return
    tp_values = list(ctx.state.heatmap.tp_values or [])
    tp_cb = ctx.widgets.get_or_none("_heatmap_tp_cb")
    tp = None
    if tp_cb is not None and tp_values:
        idx = max(0, min(tp_cb.currentIndex(), len(tp_values) - 1))
        tp = tp_values[idx]
    ch = ctx.state.channel.active_channel
    metric_cb = ctx.widgets.get_or_none("_heatmap_metric_cb")
    metric = str(metric_cb.currentText()) if metric_cb else "metric"
    col_name = f"{ch}_{metric.lower().replace(' ', '_')}"
    well_labels = _well_labels_map(ctx)
    rows_out = []
    for r in range(layout.rows):
        for c in range(layout.cols):
            val = float(arr[r, c]) if not _math.isnan(arr[r, c]) else ""
            wells = sorted((cell_index or {}).get((r, c), []))
            wells_str = ";".join(wells)
            rows_out.append({
                "row": r + 1,
                "col": c + 1,
                "wells": wells_str,
                "well_names": well_names_joined(wells_str, well_labels),
                "timepoint_h": f"{tp:g}" if tp is not None else "",
                col_name: f"{val:.6f}" if val != "" else "",
            })
    if not rows_out:
        _warn(ctx.window, "Export", "No heatmap cells to export.")
        return
    tp_tag = f"_t{tp:g}h" if tp is not None else ""
    out_path = ask_save_csv(
        ctx, "Export heatmap data",
        f"heatmap_{ch}_{metric.replace(' ', '_')}{tp_tag}.csv",
    )
    if not out_path:
        return
    fieldnames = ["row", "col", "wells", "well_names", "timepoint_h", col_name]
    try:
        with open(out_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows_out)
        ctx.notify.set_status(f"Exported {len(rows_out)} heatmap cell(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(ctx.window, "Export failed", str(exc))


def export_distribution_data(ctx) -> None:
    """Export the per-cell values currently shown in the Distribution tab to CSV."""
    from well_viewer.data.data_loading import _all_fluor_values_filtered, iter_plot_groups
    import math as _math

    val_col = ctx.state.channel.active_val_col
    fluor_gates = get_all_fluor_gates(ctx)
    ratios = ctx.state.channel.ratio_index

    tp_var = ctx.widgets.get_or_none("_distribution_tp_var")
    tp_str = tp_var.currentText() if tp_var is not None else ""
    try:
        tp_h = float(tp_str) if tp_str not in ("", "—", None) else float("nan")
    except (ValueError, TypeError):
        tp_h = float("nan")
    tp_filter = tp_h if _math.isfinite(tp_h) else None

    ch = ctx.state.channel.active_channel
    well_labels = _well_labels_map(ctx)
    well_paths = _well_paths_keys(ctx)
    import pandas as pd
    chunks = []
    tp_tag_str = f"{tp_h:g}" if _math.isfinite(tp_h) else ""
    for name, _color, df in iter_plot_groups(ctx):
        vals = _all_fluor_values_filtered(
            df, val_col=val_col,
            fluor_gates=fluor_gates,
            ratios=ratios,
            tp_filter=tp_filter,
        )
        if vals.size == 0:
            continue
        well_name = well_name_for(name, well_labels,
                                  well_paths=well_paths, strict=True)
        chunks.append(pd.DataFrame({
            "group": name,
            "well_name": well_name,
            "timepoint_h": tp_tag_str,
            val_col: [f"{v:.6f}" for v in vals],
        }))

    if not chunks:
        _warn(ctx.window, "Export", "No distribution data — select wells and a timepoint first.")
        return

    combined = pd.concat(chunks, ignore_index=True)
    tp_tag = f"_t{tp_h:g}h" if _math.isfinite(tp_h) else ""
    out_path = ask_save_csv(
        ctx, "Export distribution data",
        f"distribution_{ch}_{val_col}{tp_tag}.csv",
    )
    if not out_path:
        return
    try:
        combined.to_csv(out_path, index=False)
        ctx.notify.set_status(f"Exported {len(combined)} cell(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(ctx.window, "Export failed", str(exc))




# ── Raw per-cell export ──────────────────────────────────────────────────────
#
# The "Save cell data…" entry every plot tab's Save menu carries. Each tab
# contributes a ``CellScope`` from its own ``collect.py``; everything below is
# shared. See ``export/cell_scope_compute.py`` for the rule about which rows a
# scope names and why narrowing is a marker column rather than a filter.


def _dataset_signature(ctx) -> tuple:
    """What has to hold still for the file to describe one state of the data.

    ``rows_version`` alone is not enough, and the gap is documented at its own
    site: ``data/cache_projectors`` says "No ``note_rows_changed`` here,
    deliberately", so rewriting ``QC_Pass`` or ``Crop_Pass`` across every cached
    frame — which is what moving a QC threshold or editing the crop region does
    — leaves the counter untouched. The QC params and the crop region are
    therefore folded in beside it, along with the dataset directory (which
    covers a swap, since ``cache.clear()`` does not bump either) and the gate
    signature the stats cache already uses.
    """
    ds = ctx.state.dataset
    qc = getattr(ctx.state, "qc", None)
    params = getattr(qc, "params", None) if qc is not None else None
    try:
        qc_sig = tuple(sorted(params.to_dict().items())) if params is not None else None
    except Exception:
        swallowed("cell export: QC params signature")
        qc_sig = None
    from well_viewer.domain.stats_compute import _gate_cache_sig
    return (
        int(getattr(ds, "rows_version", 0) or 0),
        str(getattr(ds, "data_dir", "") or ""),
        qc_sig,
        getattr(ds, "crop_region", None),
        _gate_cache_sig(ctx),
    )


def _scope_block(ctx, scope, *, wells, missing_verdicts, header_len: int,
                 unreadable=()) -> list[str]:
    """The ``#`` lines above the header — what produced this file.

    They travel *with* the data rather than in a sidecar, because a sidecar is
    separated from its file the first time someone emails one of the two. The
    last line names the row the table starts on, so a reader that would rather
    not treat ``#`` as a comment can skip to it instead.
    """
    from well_viewer.domain.gating_state import active_fluor_gates

    tps = ", ".join(f"{t:g}" for t in scope.timepoints) or "all"
    on_rule = (f"{scope.on_col} > 0.5" if scope.on_col
               else f"{scope.val_col} > {scope.threshold:g}")
    gates = active_fluor_gates(scope.fluor_gates or {})
    lines = [
        "# All-Well per-cell export",
        f"# plot: {scope.plot}",
        f"# value column: {scope.val_col}",
        f"# timepoints (h): {tps}",
        f"# above-threshold rule: {on_rule}",
        "# inclusion: Included AND QC_Pass AND Crop_Pass"
        + (" AND Gate_Pass" if scope.include_gate else " (Gate_Pass not applied)"),
        "# channel cutoffs: " + (", ".join(f"{c}>{g:g}" for c, g in sorted(gates.items()))
                                 or "none"),
        f"# rows are this plot's cell population; the figure drew {scope.drawn_subset}=1",
        f"# wells ({len(wells)}): " + ", ".join(w for _g, w in wells),
    ]
    if missing_verdicts:
        lines.append("# verdict layers not computed for every well (written as 1): "
                     + ", ".join(missing_verdicts))
    if unreadable:
        # Otherwise a well whose rows would not load is indistinguishable from
        # one that had no passing cells — both simply contribute nothing.
        lines.append("# wells that could not be read (no rows in this file): "
                     + ", ".join(unreadable))
    for key, value in scope.notes:
        lines.append(f"# {key}: {value}")
    lines.append(f"# dataset signature: {_dataset_signature(ctx)}")
    lines.append(f"# columns: {header_len}")
    # The header row, which is what a reader skipping to it wants: pandas
    # takes ``skiprows=N - 1`` and gets its column names. Naming the first
    # *data* row instead would cost the header.
    lines.append(f"# table starts on line {len(lines) + 2}")
    return lines


def _gating_busy(ctx) -> bool:
    """True while the cell-gating ``QThread`` is rewriting cached frames.

    It replaces ``Included`` / ``Gate_Pass`` / ``Gate_On`` well by well with a
    short sleep between them so the GUI repaints, which makes the window where a
    slice would straddle two gating generations wide rather than theoretical.
    Refusing to start is cheaper than detecting it afterwards.
    """
    worker = getattr(ctx.runtime.cell_gating, "worker", None)
    try:
        return bool(worker is not None and worker.isRunning())
    except (RuntimeError, AttributeError):
        swallowed("cell export: gating worker liveness")
        return False


def export_cell_data(ctx, scope) -> None:
    """Write every cell behind the current plot to a CSV, off the GUI thread.

    The slicing runs on the GUI thread, one well per event-loop tick, and the
    serialising runs on a writer thread — so the worker never holds a reference
    to a cached frame that another thread rewrites in place. See
    ``export/worker.py``.
    """
    import queue
    import threading

    from PySide6.QtCore import QTimer

    from well_viewer.core.worker_bridge import run_off_thread
    from well_viewer.data.data_loading import get_rows

    if scope is None:
        return
    if getattr(ctx.runtime.export, "cell_cancel", None) is not None:
        _warn(ctx.window, "Export", "A cell-data export is already running.")
        return
    if _gating_busy(ctx):
        _warn(ctx.window, "Export",
              "Cell gating is still recomputing. Wait for it to finish, then "
              "export — otherwise the file would mix two gating states.")
        return

    wells = [(g, w) for g, w in scope.wells
             if w in (ctx.state.dataset.well_paths or {})]
    if not wells:
        _warn(ctx.window, "Export",
              "This plot is not drawing any wells — select wells first.")
        return

    frames = {}
    unreadable: list[str] = []
    for _group, well in wells:
        try:
            frames[well] = get_rows(ctx, well)
        except Exception:
            swallowed(f"cell export: rows for {well!r}")
            unreadable.append(well)
    frames_columns = [list(f.columns) for f in frames.values() if f is not None]
    if not frames_columns:
        _warn(ctx.window, "Export", "No rows to export for the current plot.")
        return

    header = csc.header_for(frames_columns, scope)
    missing = csc.missing_verdict_layers(frames_columns)

    tp_tag = (f"_t{scope.timepoints[0]:g}" if len(scope.timepoints) == 1 else "")
    out_path = ask_save_csv(
        ctx, "Save cell data", f"{scope.plot}_cells{tp_tag}.csv")
    if not out_path:
        return

    scope_lines = _scope_block(ctx, scope, wells=wells,
                               missing_verdicts=missing, header_len=len(header),
                               unreadable=unreadable)

    well_labels = _well_labels_map(ctx)
    well_paths = _well_paths_keys(ctx)
    chunks: queue.Queue = queue.Queue(maxsize=2)
    cancel = threading.Event()
    baseline = _dataset_signature(ctx)
    ctx.runtime.export.cell_cancel = cancel
    ctx.runtime.export.cell_queue = chunks

    writer = CellExportWriter(out_path=Path(out_path), header=header,
                              scope_lines=scope_lines, chunks=chunks,
                              cancel=cancel, total_wells=len(wells))

    state = {"i": 0, "failed": False}

    def _finish_ui() -> None:
        ctx.runtime.export.cell_cancel = None
        ctx.runtime.export.cell_queue = None
        ctx.runtime.export.cell_bridge = None
        ctx.runtime.export.cell_writer = None
        ctx.runtime.export.cell_stop = None

    def _abort(message: str) -> None:
        cancel.set()
        with swallow():
            chunks.put_nowait(DONE)
        state["failed"] = True
        ctx.notify.set_status(f"Cell export cancelled — {message}")

    def _tick() -> None:
        if cancel.is_set() or state["failed"]:
            return
        if _dataset_signature(ctx) != baseline:
            _abort("the data changed while it was running "
                   "(gating, QC, crop or the dataset itself)")
            return
        if chunks.full():
            # The writer is behind. Come back rather than blocking the GUI on
            # ``put`` — a full queue must never stall the event loop. A dead
            # writer leaves the queue permanently full, which is why
            # ``state["failed"]`` is checked above rather than only here: with
            # no terminal flag this branch reschedules itself at 100 Hz for the
            # life of the process, pinning every well's frame as it goes.
            QTimer.singleShot(10, _tick)
            return
        if state["i"] >= len(wells):
            with swallow():
                chunks.put_nowait(DONE)
            return
        group, well = wells[state["i"]]
        state["i"] += 1
        df = frames.get(well)
        if df is None or df.empty:
            QTimer.singleShot(0, _tick)
            return
        try:
            chunk = csc.build_chunk(
                df, scope, group=group, well_label=well,
                well_name=well_name_for(well, well_labels,
                                        well_paths=well_paths, strict=True),
            )
        except Exception as exc:
            _abort(f"could not assemble {well}: {exc}")
            return
        # Re-check after the slice as well as before it: check-then-slice is not
        # atomic, and a rewrite landing between the two is exactly what would
        # put two gating generations in one file.
        if _dataset_signature(ctx) != baseline:
            _abort("the data changed while it was running "
                   "(gating, QC, crop or the dataset itself)")
            return
        # An empty well still counts as handled, or the status line stalls
        # short of the total for the whole run — "stuck at 92%" with nothing
        # wrong. The writer counts what it writes; this counts what was offered.
        if len(chunk):
            chunks.put_nowait(chunk)
        else:
            _on_progress(state["i"], len(wells))
        QTimer.singleShot(0, _tick)

    def _on_done(result) -> None:
        state["failed"] = True      # terminal either way — stop slicing
        _finish_ui()
        if not isinstance(result, dict) or result.get("cancelled"):
            return
        rows = result.get("rows", 0)
        ctx.notify.set_status(
            f"Saved {rows:,} cell(s) from {result.get('wells', 0)} well(s) "
            f"→ {Path(out_path).name}")

    def _on_failed(message: str) -> None:
        # Stop the slicing chain first. ``_finish_ui`` only clears the handles;
        # without the flag the chain never reaches a terminal condition, and
        # clearing ``cell_cancel`` would let the user start a second export
        # alongside the first one still ticking.
        state["failed"] = True
        cancel.set()
        _finish_ui()
        _error(ctx.window, "Export failed", str(message))

    def _on_progress(done: int, total: int) -> None:
        ctx.notify.set_status(f"Saving cell data… {done}/{total} well(s)")

    ctx.runtime.export.cell_bridge = run_off_thread(
        writer, on_done=_on_done, on_failed=_on_failed, on_progress=_on_progress)
    ctx.runtime.export.cell_writer = writer
    # The Review pane's teardown reaches the cancel through this rather than by
    # importing the service — core must not name a feature, and a stopper the
    # feature registers while it runs is the seam that keeps it from having to.
    ctx.runtime.export.cell_stop = lambda: cancel_cell_export(ctx)
    ctx.notify.set_status(f"Saving cell data for {len(wells)} well(s)…")
    QTimer.singleShot(0, _tick)


def cancel_cell_export(ctx) -> None:
    """Stop a running per-cell export and leave no partial file behind.

    Called from the Review pane's teardown as well as by the user. Both the
    event *and* the sentinel are needed: setting the event does not wake a
    writer already blocked on ``Queue.get()``, and the sentinel is what lets it
    return through its own cleanup rather than being killed mid-write.
    """
    cancel = getattr(ctx.runtime.export, "cell_cancel", None)
    if cancel is None:
        return
    cancel.set()
    chunks = getattr(ctx.runtime.export, "cell_queue", None)
    if chunks is not None:
        with swallow():
            chunks.put_nowait(DONE)
    # Give the writer its own unwinding rather than letting the interpreter kill
    # it: its ``finally`` is what removes the temp file, and a daemon thread
    # killed at exit never runs one. One queue timeout plus a margin is enough
    # for it to notice the sentinel and return.
    writer = getattr(ctx.runtime.export, "cell_writer", None)
    if writer is not None:
        with swallow():
            writer.finished.wait(timeout=1.0)
    ctx.runtime.export.cell_cancel = None
    ctx.runtime.export.cell_queue = None
    ctx.runtime.export.cell_writer = None
    ctx.runtime.export.cell_stop = None


def export_cell_data_from(ctx, scope_fn, *, unavailable: str = "") -> None:
    """Resolve a tab's scope and export it, or say why there is nothing to write.

    The menu entry is wired to this rather than to :func:`export_cell_data` so
    that a tab whose scope depends on a drawn figure — the Heat Map needs its
    grid, Spatial its frame, Multivariate its feature picker — refuses with a
    sentence instead of appearing to work and producing nothing.
    """
    try:
        scope = scope_fn(ctx)
    except Exception as exc:
        _error(ctx.window, "Save cell data", str(exc))
        return
    if scope is None:
        _warn(ctx.window, "Save cell data",
              unavailable or "This plot has nothing to export yet.")
        return
    export_cell_data(ctx, scope)
