"""Statistics-tab computation helpers for WellViewerApp."""

from __future__ import annotations

import statistics as _pystats
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from well_viewer.data.data_loading import (
    df_included_mask,
    is_ratio_key,
    resolve_value_series,
)


_STAT_LABELS = {
    "mean": "Mean (above threshold)",
    "mean_all": "Mean (all cells)",
    "median": "Median (above threshold)",
    "fraction": "Fraction above threshold",
}


def _compute_statistic_arr(
    arr: np.ndarray, statistic: str, threshold: float
) -> Optional[float]:
    """Compute the per-bucket summary on a numpy array; ``arr`` may be empty."""
    if arr.size == 0:
        return None
    if statistic == "fraction":
        return float(np.mean(arr > threshold))
    if statistic == "mean_all":
        # No per-channel threshold filter — pool every included cell.
        return float(arr.mean())
    above = arr[arr > threshold]
    if above.size == 0:
        return None
    if statistic == "median":
        return float(np.median(above))
    return float(above.mean())


def collect_group_values(
    app,
    grp,
    target_t: float,
    *,
    val_col: Optional[str] = None,
    threshold: Optional[float] = None,
    statistic: str = "mean",
) -> List[float]:
    """Return one summary value per well (or per-FOV when the group has 1 well)."""
    val_col = val_col or app._app_state.channel.active_val_col
    if threshold is None:
        try:
            threshold = float(app._get_thresh_frac_on(app._app_state.channel.active_channel))
        except Exception:
            threshold = float(app._app_state.channel.threshold)
    threshold = float(threshold)
    ratios = app._app_state.channel.ratio_index

    wells = [w for w in grp.wells if w in app._app_state.dataset.well_paths]
    if not wells:
        return []
    aggregate_per_fov = (len(wells) == 1)

    samples: List[float] = []
    for lbl in wells:
        df = app._get_rows(lbl)
        if df is None or df.empty:
            continue
        mask = df_included_mask(df).to_numpy(copy=True)
        if "timepoint_hours" not in df.columns:
            continue
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(tp) & (np.abs(tp - target_t) <= 1e-6)
        val = resolve_value_series(df, val_col, ratios).to_numpy()
        mask &= np.isfinite(val)
        if not mask.any():
            continue

        if aggregate_per_fov:
            fov_raw = (df["fov"].fillna("").astype(str).str.strip()
                       if "fov" in df.columns
                       else pd.Series([""] * len(df), index=df.index))
            # Empty FOV → "1" to match aggregate_with_threshold_df. The
            # earlier "_" sentinel bucketed missing-FOV cells differently
            # from the bar / line aggregator on the same dataset.
            fov = fov_raw.where(fov_raw != "", "1").to_numpy()
            sub = pd.DataFrame({"key": fov[mask], "v": val[mask]})
            for _, group_vals in sub.groupby("key", sort=False)["v"]:
                stat = _compute_statistic_arr(group_vals.to_numpy(), statistic, threshold)
                if stat is not None:
                    samples.append(stat)
        else:
            stat = _compute_statistic_arr(val[mask], statistic, threshold)
            if stat is not None:
                samples.append(stat)
    return samples


def collect_group_per_cell_values(
    app, grp, target_t: float, *, val_col: Optional[str] = None,
    threshold: Optional[float] = None,
) -> List[float]:
    """Return per-cell values above threshold for KS-test (distribution comparison)."""
    val_col = val_col or app._app_state.channel.active_val_col
    if threshold is None:
        try:
            threshold = float(app._get_thresh_frac_on(app._app_state.channel.active_channel))
        except Exception:
            threshold = float(app._app_state.channel.threshold)
    threshold = float(threshold)
    ratios = app._app_state.channel.ratio_index
    chunks: List[np.ndarray] = []
    for lbl in grp.wells:
        if lbl not in app._app_state.dataset.well_paths:
            continue
        df = app._get_rows(lbl)
        if df is None or df.empty or "timepoint_hours" not in df.columns:
            continue
        mask = df_included_mask(df).to_numpy(copy=True)
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(tp) & (np.abs(tp - target_t) <= 1e-6)
        val = resolve_value_series(df, val_col, ratios).to_numpy()
        mask &= np.isfinite(val) & (val > threshold)
        if mask.any():
            chunks.append(val[mask])
    return np.concatenate(chunks).tolist() if chunks else []


def _channel_label_for_val_col(val_col: str) -> str:
    """Human-readable channel label for the chart title / status string."""
    if is_ratio_key(val_col):
        return val_col.split(":", 1)[-1]
    from well_viewer.data.metric_labels import split_metric_col
    parts = split_metric_col(val_col)
    if parts is None:
        return val_col
    ch, metric_key, label = parts
    if metric_key == "smfish_count":
        return f"{ch.upper()} (spots)"
    if metric_key == "mean_intensity":
        # Preserve the legacy bare-channel label for mean intensity so existing
        # chart titles ("GFP") stay unchanged when the user hasn't picked a
        # non-default property.
        return ch.upper()
    return label


def draw_ks_cdf(app, group_vals: List[Tuple[str, List[float]]], tp_str: str, well_colors: list[str]) -> None:
    """Draw empirical CDFs for the two KS-test groups."""
    ax = app._stats_ax
    ax.cla()
    colors = [well_colors[0], well_colors[1]]
    val_col = ""
    try:
        val_col = app._stats_active_val_col()
    except Exception:
        val_col = app._app_state.channel.active_val_col
    for (name, vals), color in zip(group_vals, colors):
        if not vals:
            continue
        sv = sorted(vals)
        n = len(sv)
        xs = [sv[0]] + sv + [sv[-1]]
        ys = [0.0] + [(i + 1) / n for i in range(n)] + [1.0]
        ax.step(xs, ys, where="post", label=name, color=color, linewidth=1.5)
    ax.set_xlabel(_channel_label_for_val_col(val_col) or "value", fontsize=8)
    ax.set_ylabel("Cumulative fraction", fontsize=8)
    ax.set_title(f"Empirical CDF  —  t = {tp_str} h", fontsize=9)
    ax.legend(fontsize=8, framealpha=0.0, facecolor="none")
    ax.tick_params(labelsize=7)
    ax.set_ylim(-0.02, 1.05)
    try:
        app._stats_fig.tight_layout(pad=0.8)
    except Exception:
        swallowed()  # was: pass (silent except)
    app._stats_canvas_widget.draw_idle()


def run_stats(app, *, collect_group_values_fn, draw_ks_cdf_fn) -> None:
    """Run the selected statistical test across all group pairs."""
    tp_str = app._stats_tp_cb.currentText()
    if tp_str in ("—", ""):
        app._stats_write_result("No timepoint selected.")
        return
    try:
        target_t = float(tp_str)
    except ValueError:
        app._stats_write_result("Invalid timepoint.")
        return

    groups = [g for g in app._app_state.stats.groups if g.wells]
    if len(groups) < 2:
        app._stats_write_result("Define at least 2 non-empty groups to run a test.")
        return

    test = app._stats_test_cb.currentText()
    is_ks = test.startswith("KS")

    if is_ks:
        if len(groups) != 2:
            app._stats_write_result("KS test requires exactly 2 groups.")
            return
        for g in groups:
            if len(g.wells) != 1:
                app._stats_write_result(
                    "KS test requires each group to contain exactly 1 well.\n"
                    f"Group '{g.name}' has {len(g.wells)} well(s)."
                )
                return

    try:
        threshold = float(app._stats_active_threshold())
    except Exception:
        threshold = float(app._app_state.channel.threshold)
    try:
        val_col = app._stats_active_val_col()
    except Exception:
        val_col = app._app_state.channel.active_val_col
    try:
        statistic = app._stats_active_statistic()
    except Exception:
        statistic = "mean"
    channel_lbl = _channel_label_for_val_col(val_col)
    stat_lbl = _STAT_LABELS.get(statistic, statistic)

    group_vals: List[Tuple[str, List[float]]] = []
    for g in groups:
        if is_ks:
            vals = collect_group_per_cell_values(
                app, g, target_t, val_col=val_col, threshold=threshold,
            )
            empty_msg = (
                f"Group '{g.name}' has no per-cell {channel_lbl} data "
                f"at t={tp_str} h above threshold ({threshold:.2f})."
            )
        else:
            vals = collect_group_values_fn(g, target_t)
            n_wells = len([w for w in g.wells if w in app._app_state.dataset.well_paths])
            if n_wells == 1:
                empty_msg = (
                    f"Group '{g.name}' (1 well, FOV-aggregated) has no "
                    f"{channel_lbl} samples at t={tp_str} h "
                    f"(threshold {threshold:.2f})."
                )
            else:
                empty_msg = (
                    f"Group '{g.name}' has no {channel_lbl} per-well "
                    f"samples at t={tp_str} h (threshold {threshold:.2f})."
                )
        if not vals:
            app._stats_write_result(empty_msg)
            return
        group_vals.append((g.name, vals))

    try:
        import scipy.stats as _st
    except ImportError:
        app._stats_write_result("scipy is required.\nInstall with: pip install scipy")
        return

    # Detect solo-well groups (variance computed across FOVs because there's
    # only one well per "group") so the output can note the change in unit
    # of replication.
    solo_groups = [
        g.name for g in groups
        if len([w for w in g.wells if w in app._app_state.dataset.well_paths]) == 1
    ]
    if is_ks:
        sample_units = "per-cell values"
    elif solo_groups:
        sample_units = "per-FOV samples (solo-well groups — see note below)"
    else:
        sample_units = "per-well samples"
    header_lines: List[str] = [
        f"Test:       {test}",
        f"Channel:    {channel_lbl}",
        f"Statistic:  {stat_lbl}" if not is_ks else "Statistic:  per-cell distribution",
        f"Threshold:  {threshold:.2f}",
        f"Timepoint:  {tp_str} h",
        f"Samples:    {sample_units}",
        "",
    ]
    if solo_groups and not is_ks:
        header_lines.insert(-1,
            "Note:       solo-well group(s) " + ", ".join(solo_groups)
            + " — variance computed across FOVs (one sample per FOV) since "
            "no replicate wells were defined for them.")
        header_lines.insert(-1, "")
    lines: List[str] = list(header_lines)
    for name_a, vals_a in group_vals:
        if not vals_a:
            continue
        sv = sorted(vals_a)
        med = sv[len(sv) // 2]
        try:
            sd_val = _pystats.stdev(vals_a) if len(vals_a) > 1 else 0.0
        except _pystats.StatisticsError:
            sd_val = 0.0
        lines.append(
            f"{name_a}:  n={len(vals_a)}  "
            f"mean={sum(vals_a)/len(vals_a):.4g}  "
            f"median={med:.4g}  "
            f"sd={sd_val:.4g}"
        )
    lines.append("")

    pairs = [(group_vals[i], group_vals[j]) for i in range(len(group_vals)) for j in range(i + 1, len(group_vals))]

    # Two-pass: collect all raw p-values first so we can compute
    # multiple-comparison adjustments (Benjamini-Hochberg) before
    # emitting per-pair lines. With ≥3 groups (≥3 pairs) reporting
    # raw p alone overstates significance — BH-FDR is the most common
    # control for that.
    pair_results: list = []  # [(name_a, name_b, stat_name, stat_value, p, err)]
    for (name_a, vals_a), (name_b, vals_b) in pairs:
        stat_name = "?"
        stat_value: Optional[float] = None
        p: Optional[float] = None
        err: Optional[str] = None
        try:
            if test.startswith("t-test"):
                res = _st.ttest_ind(vals_a, vals_b, equal_var=False)
                stat_name = "t"
            elif test.startswith("Wilcoxon"):
                res = _st.ranksums(vals_a, vals_b)
                stat_name = "W"
            elif test.startswith("Mann"):
                res = _st.mannwhitneyu(vals_a, vals_b, alternative="two-sided")
                stat_name = "U"
            elif is_ks:
                res = _st.ks_2samp(vals_a, vals_b)
                stat_name = "D"
            else:
                err = "Unknown test."
                pair_results.append((name_a, name_b, stat_name, stat_value, p, err))
                continue
            p = float(res.pvalue)
            stat_value = float(res.statistic)
        except Exception as exc:
            err = str(exc)
        pair_results.append((name_a, name_b, stat_name, stat_value, p, err))

    # Benjamini-Hochberg adjusted p-values (FDR control). Applied across
    # all valid p-values in this report.
    valid_idx = [i for i, r in enumerate(pair_results) if r[4] is not None]
    adjusted: dict[int, float] = {}
    if len(valid_idx) >= 2:
        ranked = sorted(valid_idx, key=lambda i: pair_results[i][4])
        m = len(ranked)
        # Walk in descending order so the monotonic-decreasing step
        # function for BH-FDR can be propagated forward.
        prev = 1.0
        for rank_from_top, idx in enumerate(reversed(ranked)):
            k = m - rank_from_top  # 1-based rank, largest p first
            raw_p = pair_results[idx][4]
            assert raw_p is not None
            cand = raw_p * m / k
            prev = min(prev, cand)
            adjusted[idx] = min(1.0, prev)

    show_adjusted = len(adjusted) >= 2

    for idx, (name_a, name_b, stat_name, stat_value, p, err) in enumerate(pair_results):
        lines.append(f"── {name_a}  vs  {name_b} ──")
        if err is not None:
            lines.append(f"  Error: {err}")
        elif p is not None and stat_value is not None:
            sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"
            line = f"  {stat_name} = {stat_value:.4f}   p = {p:.4g}   {sig}"
            if show_adjusted and idx in adjusted:
                ap = adjusted[idx]
                asig = "***" if ap < 0.001 else "**" if ap < 0.01 else "*" if ap < 0.05 else "ns"
                line += f"   |   p_BH = {ap:.4g}   {asig}"
            lines.append(line)
        lines.append("")
    if show_adjusted:
        lines.append(
            f"p_BH: Benjamini-Hochberg FDR-adjusted p-value across "
            f"{len(adjusted)} pairwise comparison(s). Report p_BH alongside "
            f"raw p when quoting significance with 3+ groups."
        )
        lines.append("")

    app._stats_write_result("\n".join(lines))

    if is_ks:
        draw_ks_cdf_fn(group_vals, tp_str)
        app._stats_fig_frame.setVisible(True)
    else:
        app._stats_fig_frame.setVisible(False)


# ── Stats tab group-editor helpers (extracted from runtime_app) ──────────────

import copy as _copy

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from well_viewer.domain.plate_layout import WELL_COLORS
from well_viewer.core.ui_helpers import ask_name_dialog as _ask_name_dialog, clear_layout as _clear_layout
from well_viewer.core.channel_state_controller import refresh_stats_property_combo, set_active_metric
from well_viewer.core.safe import swallowed


def stats_active_group(app):
    return app._app_state.stats.active_group()


def stats_apply_drag(app, tok: str) -> None:
    if tok in app._app_state.stats.drag_visited:
        return
    app._app_state.stats.drag_visited.add(tok)
    grp = stats_active_group(app)
    if grp is None or tok not in app._app_state.dataset.well_paths:
        return
    rset = next((r for r in app._rep_sets_loaded() if tok in r.wells), None)
    if rset is not None:
        if app._app_state.stats.drag_adding:
            if rset not in grp.members:
                grp.members.append(rset)
        else:
            if rset in grp.members:
                grp.members.remove(rset)
    else:
        if app._app_state.stats.drag_adding:
            if tok not in grp.solo_wells:
                grp.solo_wells.append(tok)
        else:
            if tok in grp.solo_wells:
                grp.solo_wells.remove(tok)
    stats_refresh_map(app)


def stats_refresh_map(app) -> None:
    """Push group state onto the Statistics plate (a WellPlateSelector): each
    group's wells take its rank colour; the active group's wells are the plate's
    selection (sunken), which is also what a drag on the plate edits."""
    plate = getattr(app, "_stats_map_plate", None)
    if plate is None:
        return
    avail = list(app._app_state.dataset.well_paths.keys())
    plate.setEnabledWells(avail)
    tok_color: dict = {}
    for grp in app._app_state.stats.groups:
        c = app._rank_color_rset(grp)  # decision #1: colour by well-position rank
        for w in grp.wells:
            if w in app._app_state.dataset.well_paths:
                tok_color[w] = c   # last group wins — matches the other plates
    plate.clearWellColors()
    plate.setWellColors(tok_color)
    grp = stats_active_group(app)
    active = [w for w in grp.wells if w in app._app_state.dataset.well_paths] if grp else []
    plate.setSelectedWellIds(active)


def stats_refresh_group_list(app) -> None:
    container = app._stats_grp_inner
    layout = container.layout()
    if layout is None:
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
    _clear_layout(layout)
    if not app._app_state.stats.groups:
        lbl = QLabel("No groups.  Click + Add to create one.")
        lbl.setObjectName("Muted")
        layout.addWidget(lbl)
        stats_refresh_map(app)
        return
    for gi, grp in enumerate(app._app_state.stats.groups):
        is_sel = (gi == app._app_state.stats.active_grp)
        color = app._rank_color_rset(grp)  # decision #1: colour by well-position rank
        card = QFrame()
        card.setObjectName("StatsGroupCard")
        if is_sel:
            card.setProperty("state", "selected")
        hl = QHBoxLayout(card)
        hl.setContentsMargins(6, 4, 6, 4)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color};")
        hl.addWidget(dot)
        hl.addWidget(QLabel(grp.name))
        n_mem = len(grp.members)
        n_sol = len(grp.solo_wells)
        parts = []
        if n_mem: parts.append(f"{n_mem} set{'s' if n_mem!=1 else ''}")
        if n_sol: parts.append(f"{n_sol} solo well{'s' if n_sol!=1 else ''}")
        if not parts: parts = ["empty"]
        meta = QLabel(f"  ({', '.join(parts)})")
        meta.setObjectName("Muted")
        hl.addWidget(meta)
        hl.addStretch(1)
        idx = gi
        ren_btn = QPushButton("✎")
        ren_btn.setFlat(True)
        ren_btn.clicked.connect(lambda _=False, i=idx: stats_grp_rename(app, i))
        hl.addWidget(ren_btn)
        del_btn = QPushButton("✕")
        del_btn.setFlat(True)
        del_btn.clicked.connect(lambda _=False, i=idx: stats_grp_delete(app, i))
        hl.addWidget(del_btn)

        def _click_select(ev, i=idx):
            stats_select_grp(app, i)
        card.mousePressEvent = _click_select
        layout.addWidget(card)
    layout.addStretch(1)
    stats_refresh_map(app)


def stats_select_grp(app, idx: int) -> None:
    app._app_state.stats.select_group(idx)
    stats_refresh_group_list(app)


def stats_grp_add(app) -> None:
    app._app_state.stats.add_group()
    stats_refresh_group_list(app)


def stats_grp_delete(app, idx: int) -> None:
    if app._app_state.stats.delete_group(idx):
        stats_refresh_group_list(app)


def stats_grp_rename(app, idx: int) -> None:
    stats = app._app_state.stats
    if not (0 <= idx < len(stats.groups)):
        return
    old = stats.groups[idx].name
    name = _ask_name_dialog(app, title="Rename group", prompt="Group name:", default=old)
    if name and stats.rename_group(idx, name):
        stats_refresh_group_list(app)


def stats_grp_clear_all(app) -> None:
    app._app_state.stats.clear_groups()
    stats_refresh_group_list(app)


def stats_sync_from_app(app) -> None:
    app._app_state.stats.set_groups(_copy.deepcopy(app._groups_from_rep_sets()))
    stats_refresh_group_list(app)


def stats_on_test_change(app) -> None:
    """Clear results when test changes; update UI hints."""
    app._stats_write_result("")
    # Keep the stats Property combo in sync with the channel (ratio
    # channels collapse the metric set to "Calculated Val").
    try:
        refresh_stats_property_combo(app)
    except Exception:
        swallowed()  # was: pass (silent except)


def stats_update_tp_menu(app) -> None:
    """Populate the timepoint, channel, and statistic dropdowns."""
    from well_viewer.core.ui_helpers import set_combo_values

    all_tps: set = set()
    for label in app._app_state.dataset.well_paths:
        df = app._get_rows(label)
        if df is None or "timepoint_hours" not in df.columns:
            continue
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").dropna().unique()
        all_tps.update(float(t) for t in tp)
    sorted_tps = sorted(all_tps)
    tp_strs = [f"{t:.4g}" for t in sorted_tps]
    set_combo_values(app._stats_tp_cb, tp_strs or ["—"])
    if hasattr(app._stats_tp_cb, "setCurrentText"):
        app._stats_tp_cb.setCurrentText(tp_strs[0] if tp_strs else "—")

    # Channel dropdown -- list every measurement column so the user can
    # pick any value to compare. Mirrors the scatter-tab dropdown so the
    # _col_for_scatter_entry mapping is reused.
    channels = list(app._app_state.channel.fluor_channels) if app._app_state.channel.fluor_channels else []
    ch_options: List[str] = []
    for ch in channels:
        ch_options.append(ch)
        if ch in app._app_state.channel.smfish_channels:
            ch_options.append(f"{ch} (spots)")
    # Ratio "channels" (computed per-cell values) and shape-class features are
    # selectable too — they resolve via _label_to_channel_key in
    # col_for_scatter_entry, and the Property combo collapses to a single
    # Calculated Val for them (see populate_metric_combo).
    ch_options.extend(app._ratio_dropdown_labels())
    from shape_features_core import label_for as _shape_label_for
    for _col in (app._app_state.channel.shape_features or []):
        ch_options.append(_shape_label_for(_col) or _col)
    if not ch_options:
        ch_options = ["—"]
    if hasattr(app, "_stats_channel_cb") and app._stats_channel_cb is not None:
        current_ch = app._stats_channel_cb.currentText()
        set_combo_values(app._stats_channel_cb, ch_options)
        if current_ch in ch_options:
            app._stats_channel_cb.setCurrentText(current_ch)
        else:
            # Default to the active channel when possible to match the
            # bar/line plots' current selection.
            active = app._app_state.channel.active_channel if app._app_state.channel.active_channel in ch_options else ch_options[0]
            app._stats_channel_cb.setCurrentText(active)
    # Channel may now resolve to a ratio key -- refresh the Property
    # combo's item set accordingly.
    try:
        refresh_stats_property_combo(app)
    except Exception:
        swallowed()  # was: pass (silent except)


def stats_refresh_colors(app) -> None:
    """Refresh matplotlib figure facecolor on theme change.

    Widget colours are driven by the app-level QSS stylesheet; only the
    embedded matplotlib figure needs an explicit repaint.
    """
    from ui.theme import get_color

    if hasattr(app, "_stats_fig"):
        app._stats_fig.set_facecolor(get_color("BG_APP"))
    if hasattr(app, "_stats_canvas_widget"):
        app._stats_canvas_widget.draw()


def stats_active_threshold(app) -> float:
    """Use the per-channel ThreshFracOn from the Cell Gating tab.

    For ratio columns the channel-specific threshold doesn't apply; fall
    back to the global threshold so a sensible cutoff still exists.
    """
    val_col = app._stats_active_val_col()
    if is_ratio_key(val_col):
        return float(app._app_state.channel.threshold)
    # val_col is "<channel>_mean_intensity" or "<channel>_smfish_count".
    if "_" in val_col:
        channel = val_col.split("_", 1)[0]
        return float(app._get_thresh_frac_on(channel))
    return float(app._get_thresh_frac_on(app._app_state.channel.active_channel))



def stats_collect_group_values_shim(
    app, grp: BarGroup, target_t: float
) -> List[float]:
    return collect_group_values(
        app, grp, target_t,
        val_col=app._stats_active_val_col(),
        threshold=app._stats_active_threshold(),
        statistic=app._stats_active_statistic(),
    )



def stats_active_val_col(app) -> str:
    """Resolve the selected channel-dropdown entry to a value-column key."""
    entry = app._stats_active_channel_entry()
    if not entry:
        return app._app_state.channel.active_val_col
    try:
        return app._col_for_scatter_entry(entry)
    except Exception:
        return app._app_state.channel.active_val_col



def stats_active_channel_entry(app) -> str:
    cb = getattr(app, "_stats_channel_cb", None)
    if cb is None:
        return app._app_state.channel.active_channel or ""
    text = cb.currentText().strip()
    return text if text and text != "—" else (app._app_state.channel.active_channel or "")



def stats_active_statistic(app) -> str:
    cb = getattr(app, "_stats_statistic_cb", None)
    if cb is None:
        return "mean"
    return app._STATS_STATISTIC_KEYS.get(cb.currentText(), "mean")



def stats_run_shim(app) -> None:
    run_stats(
        app,
        collect_group_values_fn=app._stats_collect_group_values,
        draw_ks_cdf_fn=app._stats_draw_ks_cdf,
    )



def stats_draw_ks_cdf_shim(
    app,
    group_vals: List[Tuple[str, List[float]]],
    tp_str: str,
) -> None:
    draw_ks_cdf(app, group_vals, tp_str, WELL_COLORS)



def on_stats_property_change(app) -> None:
    """Stats tab's per-tab Property combo → propagate to global state."""
    from well_viewer.data.metric_labels import METRIC_LABEL_TO_KEY
    cb = getattr(app, "_stats_property_cb", None)
    if cb is None:
        return
    metric = METRIC_LABEL_TO_KEY.get(str(cb.currentText() or ""), "mean_intensity")
    if metric == "smfish_count" and app._app_state.channel.active_channel not in app._app_state.channel.smfish_channels:
        metric = "mean_intensity"
    set_active_metric(app, metric)

