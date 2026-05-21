"""Export/save service helpers extracted from runtime_app."""

from __future__ import annotations

import csv
import math
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox


_CSV_FILTER = "CSV files (*.csv);;All files (*.*)"


def _well_labels_map(app) -> dict:
    """Snapshot of the Sample Definitions well-label map (token → name)."""
    return dict(app._app_state.dataset.well_labels or {})


def _well_paths_keys(app) -> set:
    """Set of valid well tokens for the loaded project."""
    paths = app._app_state.dataset.well_paths or {}
    return set(paths)


def well_name_for(token, well_labels: dict, *, well_paths: set | None = None,
                  strict: bool = False) -> str:
    """Look up the Sample Definitions display name for a well token.

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


def _ask_save_csv(app, title: str, default_name: str) -> str:
    initial_dir = str(app._app_state.dataset.data_dir) if app._app_state.dataset.data_dir else ""
    initial_path = str(Path(initial_dir) / default_name) if initial_dir else default_name
    out, _ = QFileDialog.getSaveFileName(app, title, initial_path, _CSV_FILTER)
    return out or ""


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
        "fraction_above",
        "n_total",
        "threshold",
        "metric",
    ]


def line_metric_row(pt, *, ch: str, metric: str, threshold: float, band_lbl: str) -> dict:
    """Metric-column dict for one line-plot AggPoint.

    *pt* is an aggregation tuple ``(t, mean, spread, frac, n_above, n_total, ...)``
    as returned by ``_aggregate_well`` / ``_aggregate_group``. The ``spread``
    field is SEM when the aggregator was called with ``use_sem=True``, SD
    otherwise; *band_lbl* must be set accordingly so the column header
    reflects what the data represents.
    """
    t, mean, spread, frac, n_above, n_total, *_ = pt
    return {
        "time_h": f"{t:.4f}",
        f"mean_{ch}_{metric}": f"{mean:.6f}" if not math.isnan(mean) else "",
        f"{band_lbl.lower()}_{ch}_{metric}": f"{spread:.6f}",
        "n_above_threshold": n_above,
        "fraction_above": f"{frac:.6f}" if not math.isnan(frac) else "",
        "n_total": n_total,
        "threshold": f"{threshold:.4f}",
        "metric": metric,
    }


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
) -> dict:
    """Metric-column dict for one bar-plot bar at a single timepoint.

    The arguments mirror the per-bar values used by both ``_collect_bar_items``
    (per-tab) and direct ``_aggregate_*`` calls (batch). ``n_above_spread > 0``
    indicates per-FOV spread is in use, which switches *n_above* to a float
    and exposes the per-FOV error column.
    """
    return {
        "timepoint_h": tp_str,
        f"mean_{ch}_{metric}": f"{mean:.6f}" if has and not math.isnan(mean) else "",
        f"err_{band_lbl}_{ch}_{metric}": f"{spread:.6f}" if has else "",
        "fraction_above": f"{frac:.6f}" if has and not math.isnan(frac) else "",
        f"err_frac_{band_lbl}": f"{frac_spread:.6f}" if has and not math.isnan(frac) else "",
        "n_above_threshold": (
            f"{n_above:.6f}" if n_above_spread > 0
            else f"{int(n_above)}" if has else ""
        ),
        f"err_n_above_{band_lbl}": f"{n_above_spread:.6f}" if n_above_spread > 0 else "",
        "threshold": f"{threshold:.4f}",
        "metric": metric,
    }


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
) -> None:
    """Append the four fold-change columns to a bar-plot CSV row in place.

    The raw ``mean_*`` / ``err_*`` columns stay as written by
    :func:`bar_metric_row`; this helper carries the post-normalization
    values plus mode / control annotations on the right of the row. Used
    by both the on-tab CSV (``export_bar_plot_data``) and the batch CSV.
    """
    row["fold_change_mean"] = (
        f"{fc_mean:.6f}" if fc_has and not math.isnan(fc_mean) else ""
    )
    row[f"fold_change_{band_lbl.lower()}"] = (
        f"{fc_spread:.6f}" if fc_has else ""
    )
    row["fold_change_mode"] = _fc_mode_str(vs_control, vs_t0)
    row["fold_change_control"] = control_label if vs_control else ""


def aggpoint_at(pts, target_t: float, tol: float = 1e-6):
    """Return the AggPoint whose time matches *target_t* within tol, or None."""
    for pt in pts or ():
        if abs(pt[0] - target_t) < tol:
            return pt
    return None


def aggpoint_bar_fields(pt, *, use_fov_spread: bool):
    """Decompose an AggPoint into the per-bar fields used for bar exports.

    Returns a dict with keys ``mean, spread, frac, frac_spread, has, n_above,
    n_above_spread`` matching the kwargs of ``bar_metric_row``. Mirrors the
    n_above selection logic used by the on-screen bar renderer: total
    n_above by default, mean ± SD/SEM per FOV when the Aggregate-FOVs toggle
    is on.
    """
    if pt is None:
        return dict(mean=float("nan"), spread=0.0, frac=float("nan"),
                    frac_spread=0.0, has=False, n_above=0.0, n_above_spread=0.0)
    _t = pt[0]
    mean = float(pt[1])
    spread = float(pt[2])
    frac = float(pt[3])
    n_above_total = int(pt[4]) if len(pt) >= 5 else 0
    frac_spread = float(pt[6]) if len(pt) >= 7 else 0.0
    n_above_pf_mean = float(pt[7]) if len(pt) >= 8 else 0.0
    n_above_pf_spread = float(pt[8]) if len(pt) >= 9 else 0.0
    if use_fov_spread:
        n_above = n_above_pf_mean
        n_above_spread = n_above_pf_spread
    else:
        n_above = float(n_above_total)
        n_above_spread = 0.0
    has = not math.isnan(mean)
    return dict(mean=mean, spread=spread, frac=frac, frac_spread=frac_spread,
                has=has, n_above=n_above, n_above_spread=n_above_spread)


def _warn(app, title: str, msg: str) -> None:
    QMessageBox.warning(app, title, msg)


def _error(app, title: str, msg: str) -> None:
    QMessageBox.critical(app, title, msg)


def export_plot_data(app) -> None:
    from well_viewer import fold_change as _fc

    selected = list(app._selected_labels())
    well_to_repset: dict = {}
    if not selected:
        # The line plot falls back to active replicate sets when nothing is
        # manually selected; the CSV export must do the same instead of
        # refusing — otherwise "Export CSV" reports "no wells selected" while
        # the plot is clearly showing rep-set data. Emit per-well rows for the
        # rep-set members (deduped, order preserved) plus a replicate_set
        # column so the grouping isn't lost.
        seen: set = set()
        for rset in (app._rep_sets_active() or []):
            for w in rset.wells:
                if w in app._app_state.dataset.well_paths and w not in seen:
                    seen.add(w)
                    selected.append(w)
                    well_to_repset[w] = getattr(rset, "name", "")
    if not selected:
        _warn(app, "Export", "No wells selected. Select wells in the picker, or define a replicate set.")
        return
    ch = app._app_state.channel.active_channel
    metric = app._app_state.channel.active_metric
    threshold = app._get_thresh_frac_on(ch)
    use_sem = bool(app._use_sem)
    band_lbl = "SEM" if use_sem else "SD"
    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gates = app._get_all_fluor_gates()
    well_labels = _well_labels_map(app)
    include_repset_col = bool(well_to_repset)

    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = _fc.fold_change_state(app)
    fc_control_means: dict = {}
    if fc_vs_ctrl and fc_ctrl_lbl:
        fc_control_means = _fc.pts_to_mean_by_t(
            _fc.control_pts_for_line(
                app, fc_ctrl_lbl, threshold=threshold,
                val_col=app._app_state.channel.active_val_col,
                cell_area_threshold=cell_area_threshold,
                fluor_gates=fluor_gates,
            )
        )
    fc_active = fc_vs_ctrl or fc_vs_t0

    rows_out = []
    for label in selected:
        pts = app._aggregate_well(
            label, threshold=threshold, use_sem=use_sem,
            val_col=app._app_state.channel.active_val_col,
            cell_area_threshold=cell_area_threshold,
            fluor_gates=fluor_gates,
        )
        norm_pts = (
            _fc.normalize_pts(pts, control_means=fc_control_means or None,
                              use_t0=fc_vs_t0)
            if fc_active else None
        )
        for i, pt in enumerate(pts):
            row = {
                "well": label,
                "well_name": well_name_for(label, well_labels),
            }
            if include_repset_col:
                row["replicate_set"] = well_to_repset.get(label, "")
            row.update(line_metric_row(pt, ch=ch, metric=metric,
                                       threshold=threshold, band_lbl=band_lbl))
            if fc_active and norm_pts is not None and i < len(norm_pts):
                npt = norm_pts[i]
                fmean = npt[1] if not (isinstance(npt[1], float) and math.isnan(npt[1])) else float("nan")
                row["fold_change_mean"] = (
                    f"{fmean:.6f}" if isinstance(fmean, (int, float)) and not math.isnan(fmean) else ""
                )
                fspread = npt[2]
                row[f"fold_change_{band_lbl.lower()}"] = (
                    f"{fspread:.6f}" if isinstance(fspread, (int, float)) and not math.isnan(fspread) else ""
                )
                row["fold_change_mode"] = _fc_mode_str(fc_vs_ctrl, fc_vs_t0)
                row["fold_change_control"] = fc_ctrl_lbl if fc_vs_ctrl else ""
            rows_out.append(row)
    if not rows_out:
        _warn(app, "Export", "No data to export for the current selection.")
        return
    out_path = _ask_save_csv(app, "Export plot data", f"{ch}_{metric}_plot_export.csv")
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
        ]
    try:
        with open(out_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows_out)
        app._set_status(f"Exported {len(rows_out)} row(s) to {Path(out_path).name}")
    except OSError as exc:
        _error(app, "Export failed", str(exc))


def export_bar_plot_data(app) -> None:
    from well_viewer import fold_change as _fc
    from well_viewer.barplot_controller import FC_STATE_OFF

    tp_str = app._bar_tp_cb.currentText()
    if tp_str in ("—", ""):
        _warn(app, "Export", "Select a timepoint first.")
        return
    try:
        target_t = float(tp_str)
    except ValueError:
        return
    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = _fc.fold_change_state(app)
    fc_active = fc_vs_ctrl or fc_vs_t0
    # Additive CSV: raw values in the standard ``mean_*`` / ``err_*``
    # columns, fold-change values in their own columns. The aggregation
    # helpers are cached so the second call to collect_bar_items is cheap.
    use_groups, items_raw, band_lbl = app._collect_bar_items(
        target_t, fc_state=FC_STATE_OFF,
    )
    if fc_active:
        _, items_fc, _ = app._collect_bar_items(target_t)
        fc_by_key = {it.key: it for it in items_fc}
    else:
        fc_by_key = {}
    ch = app._app_state.channel.active_channel
    metric = app._app_state.channel.active_metric
    threshold = app._get_thresh_frac_on(ch)
    well_labels = _well_labels_map(app)
    well_paths = _well_paths_keys(app)
    rows_out = []
    if use_groups:
        id_cols = ["name", "well_name"]
        for item in items_raw:
            row = {
                "name": item.key,
                "well_name": well_name_for(item.key, well_labels,
                                           well_paths=well_paths, strict=True),
            }
            row.update(bar_metric_row(
                mean=item.mean, spread=item.spread,
                frac=item.frac, frac_spread=item.frac_spread,
                has=item.has_mean,
                n_above=float(item.n_above),
                n_above_spread=float(item.n_above_spread),
                ch=ch, metric=metric, tp_str=tp_str,
                threshold=threshold, band_lbl=band_lbl,
            ))
            if fc_active and item.key in fc_by_key:
                fc_item = fc_by_key[item.key]
                attach_bar_fold_change_columns(
                    row, fc_mean=fc_item.mean, fc_spread=fc_item.spread,
                    fc_has=fc_item.has_mean, band_lbl=band_lbl,
                    vs_control=fc_vs_ctrl, vs_t0=fc_vs_t0,
                    control_label=fc_ctrl_lbl,
                )
            rows_out.append(row)
    else:
        id_cols = ["well", "well_name"]
        for item in items_raw:
            row = {
                "well": item.key,
                "well_name": well_name_for(item.key, well_labels),
            }
            row.update(bar_metric_row(
                mean=item.mean, spread=item.spread,
                frac=item.frac, frac_spread=item.frac_spread,
                has=item.has_mean,
                n_above=float(item.n_above),
                n_above_spread=float(item.n_above_spread),
                ch=ch, metric=metric, tp_str=tp_str,
                threshold=threshold, band_lbl=band_lbl,
            ))
            if fc_active and item.key in fc_by_key:
                fc_item = fc_by_key[item.key]
                attach_bar_fold_change_columns(
                    row, fc_mean=fc_item.mean, fc_spread=fc_item.spread,
                    fc_has=fc_item.has_mean, band_lbl=band_lbl,
                    vs_control=fc_vs_ctrl, vs_t0=fc_vs_t0,
                    control_label=fc_ctrl_lbl,
                )
            rows_out.append(row)
    if not rows_out:
        _warn(app, "Export", "No data to export.")
        return
    out_path = _ask_save_csv(app, "Export bar plot data", f"bar_t{tp_str}.csv")
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
        app._set_status(f"Exported {len(rows_out)} row(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(app, "Export failed", str(exc))


def export_raw_data_csv(app) -> None:
    if not app._app_state.dataset.well_paths:
        _warn(app, "Export", "Load data before exporting raw CSV.")
        return

    import numpy as np
    import pandas as pd
    from well_viewer.data_loading import df_included_mask

    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gate_threshold = app._get_fluor_gate(app._app_state.channel.active_channel)
    well_labels = _well_labels_map(app)
    # Gating semantics stay on mean fluorescence intensity even when the
    # global Property selector points at a different column — the gate
    # values entered in the Cell Gating tab are MFI thresholds. The raw
    # dump itself still includes every column from the source CSV, so the
    # selected property is preserved in the output.
    gate_col = f"{app._app_state.channel.active_channel}_mean_intensity"

    frames = []
    for label in sorted(app._app_state.dataset.well_paths):
        df = app._get_rows(label)
        if df is None or df.empty or "area_px" not in df.columns or gate_col not in df.columns:
            continue
        mask = df_included_mask(df).to_numpy(copy=True)
        area = pd.to_numeric(df["area_px"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(area) & (area > cell_area_threshold)
        fluor = pd.to_numeric(df[gate_col], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(fluor) & (fluor > fluor_gate_threshold)
        if not mask.any():
            continue
        sub = df.loc[mask].copy()
        sub.insert(0, "well_name", well_name_for(label, well_labels))
        sub.insert(0, "well", label)
        frames.append(sub)

    if not frames:
        _warn(app, "Export", "No raw rows available to export.")
        return

    combined = pd.concat(frames, ignore_index=True, sort=False)
    other_cols = sorted(c for c in combined.columns if c not in ("well", "well_name"))
    combined = combined[["well", "well_name"] + other_cols]

    out_path = _ask_save_csv(app, "Export raw data", "raw_data_export.csv")
    if not out_path:
        return

    try:
        combined.to_csv(out_path, index=False)
        app._set_status(f"Exported {len(combined)} raw row(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(app, "Export failed", str(exc))


def save_montage_figure(app) -> None:
    from matplotlib.figure import Figure as _Figure

    # Pull theme constants and the well-token helper from their proper
    # homes (ui.theme / data_loading) instead of round-tripping through
    # the runtime_app monolith. Numpy is a hard pipeline dependency, so
    # the optional-import dance in runtime_app's _np / _NP_AVAILABLE is
    # also unnecessary here.
    import numpy as _np
    from ui.theme import PLOT_BG, TXT_PRI, TXT_MUT
    from well_viewer.data_loading import extract_well_token as _extract_well_token

    if not app._montage_fluor_arrays:
        _warn(app, "Nothing to save", "Load a well in the Preview tab first.")
        return
    fov = app._preview_fov_cb.currentText()
    well = _extract_well_token(app._preview_selected_well or "") or "well"
    n = len(app._montage_fluor_arrays)
    try:
        lo = float(app._mon_lmin_edit.text())
    except ValueError:
        lo = None
    try:
        hi = float(app._mon_lmax_edit.text())
    except ValueError:
        hi = None
    ov_lmin_edit = getattr(app, "_mon_ov_lmin_edit", None)
    ov_lmax_edit = getattr(app, "_mon_ov_lmax_edit", None)
    try:
        ov_lo = float(ov_lmin_edit.text()) if ov_lmin_edit is not None else None
    except (ValueError, AttributeError):
        ov_lo = None
    try:
        ov_hi = float(ov_lmax_edit.text()) if ov_lmax_edit is not None else None
    except (ValueError, AttributeError):
        ov_hi = None
    tophat_on = getattr(app, "_mon_tophat_cb", None) is not None and app._mon_tophat_cb.isChecked()
    use_display = tophat_on and hasattr(app, "_montage_fluor_display_arrays") and len(app._montage_fluor_display_arrays) == len(app._montage_fluor_arrays)
    fluor_source = app._montage_fluor_display_arrays if use_display else app._montage_fluor_arrays
    tp_list = [(tp, ref) for (f, tp), ref in sorted(app._preview_fluor.items()) if f == fov]
    crop = getattr(app, "_montage_crop", None)

    def _apply_crop(a):
        """Slice ``a`` to the active montage crop, preserving channel axes."""
        if crop is None or a is None:
            return a
        a = _np.asarray(a)
        ih, iw = a.shape[:2]
        y0, x0, y1, x1 = crop
        y0 = max(0, min(int(y0), ih))
        y1 = max(y0, min(int(y1), ih))
        x0 = max(0, min(int(x0), iw))
        x1 = max(x0, min(int(x1), iw))
        if y1 <= y0 or x1 <= x0:
            return a
        return a[y0:y1, x0:x1]

    fig = _Figure(figsize=(max(4, n * 2.5), 5), dpi=300, facecolor=PLOT_BG)
    for ci, ((tp, _), display_arr, ov_arr) in enumerate(zip(tp_list, fluor_source, app._montage_overlay_arrays)):
        ax_g = fig.add_subplot(2, n, ci + 1)
        ax_o = fig.add_subplot(2, n, n + ci + 1)
        if display_arr is not None and True:
            arr = _np.asarray(_apply_crop(display_arr), dtype=_np.float32)
            alo = lo if lo is not None else float(arr.min())
            ahi = hi if hi is not None else float(arr.max())
            if ahi <= alo:
                ahi = alo + 1.0
            ax_g.imshow(arr, cmap="gray", vmin=alo, vmax=ahi, aspect="auto")
        else:
            ax_g.text(0.5, 0.5, "unavail", ha="center", va="center", transform=ax_g.transAxes, color=TXT_MUT)
        ax_g.set_title(tp, fontsize=6, color=TXT_PRI)
        ax_g.axis("off")
        if ci == 0:
            ax_g.set_ylabel(app._app_state.channel.active_channel.upper(), fontsize=7, color=TXT_PRI)
        if ov_arr is not None and True:
            arr = _np.asarray(_apply_crop(ov_arr))
            if arr.ndim == 2:
                arr_f = arr.astype(_np.float32)
                lo_o = ov_lo if ov_lo is not None else float(arr_f.min())
                hi_o = ov_hi if ov_hi is not None else float(arr_f.max())
                if hi_o <= lo_o:
                    hi_o = lo_o + 1.0
                ax_o.imshow(arr_f, cmap="gray", vmin=lo_o, vmax=hi_o, aspect="auto")
            elif arr.ndim == 3:
                a = arr[:, :, :3].astype(_np.float32)
                lo_o = ov_lo if ov_lo is not None else float(a.min())
                hi_o = ov_hi if ov_hi is not None else float(a.max())
                if hi_o <= lo_o:
                    hi_o = lo_o + 1.0
                a = _np.clip((a - lo_o) / (hi_o - lo_o) * 255.0, 0, 255).astype(_np.uint8)
                ax_o.imshow(a, aspect="auto")
        else:
            ax_o.text(0.5, 0.5, "unavail", ha="center", va="center", transform=ax_o.transAxes, color=TXT_MUT)
        ax_o.axis("off")
        if ci == 0:
            ax_o.set_ylabel("overlay", fontsize=7, color=TXT_PRI)
    fig.suptitle(f"{well}  FOV: {fov}", fontsize=9, fontweight="bold", color=TXT_PRI, y=1.01)
    fig.tight_layout()
    app._save_matplotlib_fig(fig, f"montage_{well}_{fov}.png")


def export_scatter_data(app) -> None:
    """Export scatter plot data to CSV."""
    from well_viewer.scatter_controller import collect_scatter_data as _scatter_collect_data

    try:
        ch_x_entry = app._scatter_ch_x_cb.currentText()
        ch_y_entry = app._scatter_ch_y_cb.currentText()
    except (ValueError, AttributeError):
        _warn(app, "Export", "Select channels and timepoint first.")
        return

    # Pick every active timepoint from the multi-tp selector, falling
    # back to the legacy single-tp combo when the picker hasn't been
    # populated (very first build).
    selections = getattr(app, "_scatter_tp_selections", None) or {}
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
            tp_str = app._scatter_tp_cb.currentText()
            if tp_str:
                timepoints_h = [float(tp_str)]
        except (TypeError, ValueError, AttributeError):
            pass
    if not timepoints_h:
        _warn(app, "Export", "Select at least one timepoint first.")
        return

    ch_x_base = ch_x_entry.split(" ")[0]
    ch_y_base = ch_y_entry.split(" ")[0]
    # Per-axis Property combos drive column selection now; fall back to
    # the legacy single-entry resolver if those combos aren't present.
    if hasattr(app, "_scatter_axis_metric_key"):
        metric_x = app._scatter_axis_metric_key("x")
        metric_y = app._scatter_axis_metric_key("y")
        col_x = app._col_for_scatter_axis(ch_x_entry, metric_x)
        col_y = app._col_for_scatter_axis(ch_y_entry, metric_y)
    else:
        col_x = app._col_for_scatter_entry(ch_x_entry)
        col_y = app._col_for_scatter_entry(ch_y_entry)

    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gate_x = app._get_fluor_gate(ch_x_base)
    fluor_gate_y = app._get_fluor_gate(ch_y_base)

    scatter_data = _scatter_collect_data(
        app, col_x, col_y, timepoints_h,
        well_colors=[],
        cell_area_threshold=cell_area_threshold,
        fluor_gate_x=fluor_gate_x,
        fluor_gate_y=fluor_gate_y,
    )

    well_labels = _well_labels_map(app)
    well_paths = _well_paths_keys(app)
    rows_out = []
    for label, data in scatter_data.items():
        x_vals = data['x']
        y_vals = data['y']
        well_name = well_name_for(label, well_labels,
                                  well_paths=well_paths, strict=True)
        for x, y in zip(x_vals, y_vals):
            rows_out.append({
                "group_well": label,
                "well_name": well_name,
                f"{col_x}": f"{x:.6f}",
                f"{col_y}": f"{y:.6f}",
                "timepoint_h": f"{timepoint_h:.4f}",
            })

    if not rows_out:
        _warn(app, "Export", "No data to export for the current selection.")
        return

    out_path = _ask_save_csv(
        app, "Export scatter plot data",
        f"scatter_{ch_x_base}_vs_{ch_y_base}_t{timepoint_h}.csv",
    )
    if not out_path:
        return

    try:
        with open(out_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["group_well", "well_name", f"{col_x}", f"{col_y}", "timepoint_h"])
            writer.writeheader()
            writer.writerows(rows_out)
        app._set_status(f"Exported {len(rows_out)} datapoint(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(app, "Export failed", str(exc))


def export_scatter_agg_data(app) -> None:
    """Export aggregate scatter plot data to CSV."""
    from well_viewer.scatter_controller import collect_scatter_agg_data as _scatter_collect_agg_data

    try:
        stat_x = app._scatter_agg_stat_x_cb.currentText()
        stat_y = app._scatter_agg_stat_y_cb.currentText()

        selected_timepoints: list[float] = []
        tp_checks = getattr(app, "_scatter_agg_tp_checks", None)
        if tp_checks:
            for tp_str, widget in tp_checks.items():
                try:
                    if widget.isChecked():
                        selected_timepoints.append(float(tp_str))
                except Exception:
                    pass
            selected_timepoints.sort()

        if not selected_timepoints:
            _warn(app, "Export", "Please select at least one timepoint.")
            return

    except (ValueError, AttributeError, IndexError):
        _warn(app, "Export", "Select statistics and timepoints first.")
        return

    scatter_data = _scatter_collect_agg_data(
        app, stat_x, stat_y, selected_timepoints,
        well_colors=[],
    )

    well_labels = _well_labels_map(app)
    well_paths = _well_paths_keys(app)
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
            stat_x: f"{x_val:.6f}",
            f"{stat_x}_error": f"{x_err:.6f}",
            stat_y: f"{y_val:.6f}",
            f"{stat_y}_error": f"{y_err:.6f}",
        })

    if not rows_out:
        _warn(app, "Export", "No data to export for the current selection.")
        return

    tp_range = f"t{min(selected_timepoints):.1f}-{max(selected_timepoints):.1f}"
    stat_x_safe = stat_x.replace(" ", "_").lower()
    stat_y_safe = stat_y.replace(" ", "_").lower()

    out_path = _ask_save_csv(
        app, "Export aggregate scatter plot data",
        f"scatter_agg_{stat_x_safe}_vs_{stat_y_safe}_{tp_range}.csv",
    )
    if not out_path:
        return

    try:
        with open(out_path, "w", newline="") as fh:
            fieldnames = ["replicate_well", "well_name", "timepoint_h", stat_x, f"{stat_x}_error", stat_y, f"{stat_y}_error"]
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows_out)
        app._set_status(f"Exported {len(rows_out)} datapoint(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(app, "Export failed", str(exc))


def export_heatmap_data(app) -> None:
    """Export the currently displayed heatmap grid values to CSV."""
    import math as _math
    arr = getattr(app, "_heatmap_array", None)
    layout = getattr(app, "_heatmap_layout_active", None)
    cell_index = getattr(app, "_heatmap_cell_well_index", None)
    if arr is None or layout is None:
        _warn(app, "Export", "No heatmap data — draw the heatmap first.")
        return
    tp_values = list(getattr(app, "_heatmap_tp_values", []) or [])
    slider = getattr(app, "_heatmap_tp_slider", None)
    tp = None
    if slider is not None and tp_values:
        idx = max(0, min(slider.value(), len(tp_values) - 1))
        tp = tp_values[idx]
    ch = app._app_state.channel.active_channel
    metric_cb = getattr(app, "_heatmap_metric_cb", None)
    metric = str(metric_cb.currentText()) if metric_cb else "metric"
    col_name = f"{ch}_{metric.lower().replace(' ', '_')}"
    well_labels = _well_labels_map(app)
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
        _warn(app, "Export", "No heatmap cells to export.")
        return
    tp_tag = f"_t{tp:g}h" if tp is not None else ""
    out_path = _ask_save_csv(
        app, "Export heatmap data",
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
        app._set_status(f"Exported {len(rows_out)} heatmap cell(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(app, "Export failed", str(exc))


def export_distribution_data(app) -> None:
    """Export the per-cell values currently shown in the Distribution tab to CSV."""
    from well_viewer.data_loading import _all_fluor_values_filtered, iter_plot_groups
    import math as _math

    val_col = app._app_state.channel.active_val_col
    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gates = app._get_all_fluor_gates()
    ratios = app._app_state.channel.ratio_index

    tp_var = getattr(app, "_distribution_tp_var", None)
    tp_str = tp_var.currentText() if tp_var is not None else ""
    try:
        tp_h = float(tp_str) if tp_str not in ("", "—", None) else float("nan")
    except (ValueError, TypeError):
        tp_h = float("nan")
    tp_filter = tp_h if _math.isfinite(tp_h) else None

    ch = app._app_state.channel.active_channel
    well_labels = _well_labels_map(app)
    well_paths = _well_paths_keys(app)
    import pandas as pd
    chunks = []
    tp_tag_str = f"{tp_h:g}" if _math.isfinite(tp_h) else ""
    for name, _color, df in iter_plot_groups(app):
        vals = _all_fluor_values_filtered(
            df, val_col=val_col,
            cell_area_threshold=cell_area_threshold,
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
        _warn(app, "Export", "No distribution data — select wells and a timepoint first.")
        return

    combined = pd.concat(chunks, ignore_index=True)
    tp_tag = f"_t{tp_h:g}h" if _math.isfinite(tp_h) else ""
    out_path = _ask_save_csv(
        app, "Export distribution data",
        f"distribution_{ch}_{val_col}{tp_tag}.csv",
    )
    if not out_path:
        return
    try:
        combined.to_csv(out_path, index=False)
        app._set_status(f"Exported {len(combined)} cell(s) → {Path(out_path).name}")
    except OSError as exc:
        _error(app, "Export failed", str(exc))
