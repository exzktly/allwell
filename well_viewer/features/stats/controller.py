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
from well_viewer.domain.stats_compute import group_replicate_stats


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
    use_fov: bool = True,
) -> List[float]:
    """Return one summary value per replicate for the group's test.

    The replicate unit is decided by the SHARED :func:`replicate_unit` rule and
    the values come from the SHARED :func:`group_replicate_stats` producer — the
    exact same per-replicate values the bar/line error bars reduce — so the test's
    ``n`` and the plotted spread can never disagree. Returns ``[]`` when there are
    no loaded wells; a single-well group with FOV mode off yields a single value
    (``n=1``), which the caller declines as untestable.
    """
    val_col = val_col or app._app_state.channel.active_val_col
    if threshold is None:
        try:
            threshold = float(app._get_thresh_frac_on(app._app_state.channel.active_channel))
        except Exception:
            threshold = float(app._app_state.channel.threshold)
    threshold = float(threshold)

    reps = group_replicate_stats(
        app, grp.wells, target_t, use_fov=use_fov,
        val_col=val_col, threshold=threshold,
    )
    out: List[float] = []
    for r in reps:
        s = r.summary(statistic)
        if s is not None and np.isfinite(s):
            out.append(float(s))
    return out


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
    from well_viewer.core.plot_style import style_legend
    style_legend(ax.legend(fontsize=8, framealpha=0.0, facecolor="none"), ax)
    ax.tick_params(labelsize=7)
    ax.set_ylim(-0.02, 1.05)
    try:
        app._stats_fig.tight_layout(pad=0.8)
    except Exception:
        swallowed()  # was: pass (silent except)
    app._stats_canvas_widget.draw_idle()


def _bh_adjust(pair_results) -> dict:
    """Benjamini-Hochberg FDR-adjusted p-values keyed by pair index.

    ``pair_results`` items are ``(name_a, name_b, stat_name, stat_value, p, err)``
    (``p`` at index 4). Returns ``{}`` when fewer than two valid p-values exist."""
    valid_idx = [i for i, r in enumerate(pair_results) if r[4] is not None]
    adjusted: dict = {}
    if len(valid_idx) >= 2:
        ranked = sorted(valid_idx, key=lambda i: pair_results[i][4])
        m = len(ranked)
        prev = 1.0
        for rank_from_top, idx in enumerate(reversed(ranked)):
            k = m - rank_from_top  # 1-based rank, largest p first
            raw_p = pair_results[idx][4]
            cand = raw_p * m / k
            prev = min(prev, cand)
            adjusted[idx] = min(1.0, prev)
    return adjusted


def _run_pair_tests(group_vals, test, is_ks):
    """Run the chosen scipy test over every group pair.

    Returns ``[(name_a, name_b, stat_name, stat_value, p, err), ...]``. Pure /
    deterministic — no widgets, no app state. ``group_vals`` is ``[(name, [values]), ...]``."""
    import scipy.stats as _st
    pairs = [(group_vals[i], group_vals[j])
             for i in range(len(group_vals)) for j in range(i + 1, len(group_vals))]
    out = []
    for (name_a, vals_a), (name_b, vals_b) in pairs:
        stat_name = "?"
        stat_value = None
        p = None
        err = None
        try:
            if test.startswith("t-test"):
                pooled = ("pooled" in test.lower()) or ("student" in test.lower())
                res = _st.ttest_ind(vals_a, vals_b, equal_var=pooled)
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
                out.append((name_a, name_b, stat_name, stat_value, p, "Unknown test."))
                continue
            p = float(res.pvalue)
            stat_value = float(res.statistic)
        except Exception as exc:
            err = str(exc)
        out.append((name_a, name_b, stat_name, stat_value, p, err))
    return out


def compute_pairwise(app, *, groups, target_t, val_col, statistic, threshold, test,
                     use_fov: bool = True) -> list:
    """Widget-free computation of the structured pairwise results that drive the
    Bar-plot significance brackets (``StatsState.pairwise``).

    Deterministic given the same config + underlying data (scipy tests + BH-FDR;
    no randomness), so it reproduces the exact brackets. Returns ``[]`` when a
    result can't be produced (fewer than 2 non-empty groups, a group with no
    samples, invalid KS config, or scipy missing). Used by both ``run_stats``
    (live) and Saved-Plot restore (re-run)."""
    groups = [g for g in (groups or []) if g.wells]
    if len(groups) < 2:
        return []
    test = str(test)
    is_ks = test.startswith("KS")
    if is_ks and (len(groups) != 2 or any(len(g.wells) != 1 for g in groups)):
        return []
    try:
        import scipy.stats  # noqa: F401  (availability probe)
    except ImportError:
        return []
    group_vals: List[Tuple[str, List[float]]] = []
    for g in groups:
        if is_ks:
            vals = collect_group_per_cell_values(app, g, target_t,
                                                 val_col=val_col, threshold=threshold)
        else:
            vals = collect_group_values(app, g, target_t, val_col=val_col,
                                        threshold=threshold, statistic=statistic,
                                        use_fov=use_fov)
        # A meaningful test needs >=2 replicate values; a single-well group with
        # FOV mode off yields only 1 (no replicates) and is declined here.
        if not vals or (not is_ks and len(vals) < 2):
            return []
        group_vals.append((g.name, vals))
    pair_results = _run_pair_tests(group_vals, test, is_ks)
    adjusted = _bh_adjust(pair_results)
    group_wells = {g.name: sorted(g.wells) for g in groups}
    out: list[dict] = []
    for idx, (name_a, name_b, _sname, _sval, p, err) in enumerate(pair_results):
        if err is not None or p is None:
            continue
        out.append({
            "a": name_a,
            "b": name_b,
            "a_wells": group_wells.get(name_a, []),
            "b_wells": group_wells.get(name_b, []),
            "p": float(p),
            "p_adj": float(adjusted.get(idx, p)),
            "test": test,
            "stat": str(statistic),
            "val_col": str(val_col),
            "target_t": float(target_t),
        })
    return out


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
    use_fov = bool(app._use_fov_spread_active())
    channel_lbl = _channel_label_for_val_col(val_col)
    stat_lbl = _STAT_LABELS.get(statistic, statistic)

    group_vals: List[Tuple[str, List[float]]] = []
    for g in groups:
        if is_ks:
            vals = collect_group_per_cell_values(
                app, g, target_t, val_col=val_col, threshold=threshold,
            )
            if not vals:
                app._stats_write_result(
                    f"Group '{g.name}' has no per-cell {channel_lbl} data "
                    f"at t={tp_str} h above threshold ({threshold:.2f})."
                )
                return
        else:
            vals = collect_group_values_fn(g, target_t)
            n_wells = len([w for w in g.wells if w in app._app_state.dataset.well_paths])
            # A test needs >=2 replicate values. The replicate unit is per-well
            # (replicate sets) or per-FOV (single-well + FOV mode on).
            if len(vals) < 2:
                if n_wells <= 1 and not use_fov:
                    app._stats_write_result(
                        f"Group '{g.name}' is a single well and FOV mode is off "
                        f"— enable FOV mode (FOVs become replicates) or add "
                        f"replicate wells to run a test."
                    )
                elif n_wells <= 1:
                    app._stats_write_result(
                        f"Group '{g.name}' (single well, per-FOV) has fewer than "
                        f"2 FOV samples at t={tp_str} h (threshold {threshold:.2f})."
                    )
                else:
                    app._stats_write_result(
                        f"Group '{g.name}' has fewer than 2 {channel_lbl} per-well "
                        f"samples at t={tp_str} h (threshold {threshold:.2f})."
                    )
                return
        group_vals.append((g.name, vals))

    try:
        import scipy.stats  # noqa: F401  (availability probe; tests run in _run_pair_tests)
    except ImportError:
        app._stats_write_result("scipy is required.\nInstall with: pip install scipy")
        return

    # Single-well groups test across FOVs (FOV mode is on, else they'd have been
    # declined above); replicate sets test across wells. Note the replicate unit.
    solo_groups = [
        g.name for g in groups
        if len([w for w in g.wells if w in app._app_state.dataset.well_paths]) == 1
    ]
    if is_ks:
        sample_units = "per-cell values"
    elif solo_groups:
        sample_units = ("per-well (replicate sets) · per-FOV (single-well "
                        "groups, FOV mode on)")
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
            "Note:       single-well group(s) " + ", ".join(solo_groups)
            + " — tested across FOVs (one sample per FOV) because FOV mode is "
            "ON and no replicate wells are defined for them.")
        header_lines.insert(-1, "")
    lines: List[str] = list(header_lines)
    for name_a, vals_a in group_vals:
        if not vals_a:
            continue
        med = _pystats.median(vals_a)
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

    # Run every pair, then apply Benjamini-Hochberg FDR across all valid
    # p-values (shared with ``compute_pairwise`` so the live results and a
    # Saved-Plot re-run are computed by the exact same code).
    pair_results = _run_pair_tests(group_vals, test, is_ks)
    adjusted = _bh_adjust(pair_results)

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

    # N7: store structured pairwise results so the Bar plot can draw significance
    # brackets from the same comparison (text output above is unchanged). Built by
    # the shared ``compute_pairwise`` so the live results are byte-identical to a
    # later Saved-Plot re-run of the same config. Each dict carries the compared
    # groups' WELLS (bar matching by membership), the tested measure (``stat`` →
    # which panel), and the run CONTEXT (``val_col`` / ``target_t``).
    app._app_state.stats.pairwise = compute_pairwise(
        app, groups=app._app_state.stats.groups, target_t=target_t,
        val_col=val_col, statistic=statistic, threshold=threshold, test=test,
        use_fov=use_fov)
    stats_refresh_fov_indicator(app)

    if is_ks:
        draw_ks_cdf_fn(group_vals, tp_str)
        app._stats_fig_frame.setVisible(True)
    else:
        app._stats_fig_frame.setVisible(False)

    # If significance brackets are enabled, nudge the Bar plot to repaint with
    # the fresh results (live in a Stats+Bar Split View; otherwise the bar scope
    # is marked dirty and repaints when next shown). Bus-only — no feature import
    # from this controller (keeps the deferred-import budget intact, G5/§5.9).
    if app._app_state.plot.show_significance:
        try:
            app._event_bus.plot_options_changed.emit(app._app_state.plot)
        except Exception:
            swallowed()


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


def stats_sync_from_app(app, *, visible_only_in_split: bool = True) -> None:
    # On *auto-populate* (first visit) in Split View, seed the comparison groups
    # from only the rep-sets currently *visible* on the plots (non-hidden) so the
    # comparison matches what the user is looking at. The explicit "Load from
    # Sample Definitions" action passes visible_only_in_split=False to load every
    # defined grouping regardless of visibility. Falls back to all groups outside
    # Split View, or when nothing is visible.
    if visible_only_in_split and getattr(app, "_split_active", False):
        groups = app._groups_from_visible_rep_sets() or app._groups_from_rep_sets()
    else:
        groups = app._groups_from_rep_sets()
    app._app_state.stats.set_groups(_copy.deepcopy(groups))
    stats_refresh_group_list(app)


def stats_resync_from_sample_defs(app) -> None:
    """Re-pull the comparison groups from the shared Sample-Definition sets.

    Statistics auto-seeds its groups from the Sample-Definition replicate sets
    on first visit (tab_controller); this is the explicit "do it again" so a
    user who edited Sample Definitions can refresh without rebuilding groups by
    hand. Confirm first when existing groups would be discarded.
    """
    from PySide6.QtWidgets import QMessageBox

    rsets = app._groups_from_rep_sets()
    if not rsets:
        app._set_status("No Sample-Definition groups to load — define them on the Sample Definitions tab.")
        return
    existing = [g for g in app._app_state.stats.groups if g.wells]
    if existing:
        resp = QMessageBox.question(
            app if hasattr(app, "window") else None,
            "Load from Sample Definitions",
            f"Replace the current {len(existing)} comparison group(s) with the "
            f"{len(rsets)} Sample-Definition group(s)?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
    # Explicit "load all from Sample Definitions" — never filter to the
    # split-visible subset (the dialog above promised all rsets groups).
    stats_sync_from_app(app, visible_only_in_split=False)
    app._set_status(f"Loaded {len(rsets)} comparison group(s) from Sample Definitions.")


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
        # Display fluor tokens upper-case (GFP) to match the rest of the app;
        # col_for_scatter_entry resolves the column case-insensitively and the
        # chart titles already upper-case (_channel_label_for_val_col). ``ch``
        # stays lower-case for the smFISH-channel membership test.
        ch_options.append(ch.upper())
        if ch in app._app_state.channel.smfish_channels:
            ch_options.append(f"{ch.upper()} (spots)")
    # Ratio "channels" (computed per-cell values) and shape-class features are
    # selectable too — they resolve via _label_to_channel_key in
    # col_for_scatter_entry, and the Property combo collapses to a single
    # Calculated Val for them (see populate_metric_combo).
    ch_options.extend(app._ratio_dropdown_labels())
    from pipeline.shape_features import label_for as _shape_label_for
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
            # bar/line plots' current selection (compare upper-case since the
            # options now display upper-case while the token is lower-case).
            active_disp = (app._app_state.channel.active_channel or "").upper()
            active = active_disp if active_disp in ch_options else ch_options[0]
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


def threshold_for_val_col(app, val_col: str) -> float:
    """The ThreshFracOn cutoff for a value column (per-channel Cell-Gating cut).

    Ratio columns have no per-channel cut → the global threshold. Keyed off the
    passed ``val_col`` (not the live widget) so it's reusable at Saved-Plot
    re-run time."""
    if is_ratio_key(val_col):
        return float(app._app_state.channel.threshold)
    # val_col is "<channel>_mean_intensity" or "<channel>_smfish_count".
    if "_" in val_col:
        channel = val_col.split("_", 1)[0]
        return float(app._get_thresh_frac_on(channel))
    return float(app._get_thresh_frac_on(app._app_state.channel.active_channel))


def stats_active_threshold(app) -> float:
    """The per-channel ThreshFracOn for the currently-selected stats value column."""
    return threshold_for_val_col(app, app._stats_active_val_col())


def serialize_stats_groups(groups) -> list:
    """Serialise a list of stats ``BarGroup`` for a saved plot.

    Each member replicate set stores BOTH its name and its wells so the group is
    self-contained: a restored plot reproduces the exact same well composition
    even if the live replicate set was later renamed or deleted (matching by name
    alone would silently drop it). Solo wells are stored directly."""
    out = []
    for g in groups or []:
        out.append({
            "name": g.name,
            "members": [
                {"name": rs.name, "wells": list(getattr(rs, "wells", []) or [])}
                for rs in getattr(g, "members", []) or []
            ],
            "solo_wells": list(getattr(g, "solo_wells", []) or []),
        })
    return out


def deserialize_stats_groups(app, data) -> list:
    """Rebuild stats ``BarGroup`` objects from :func:`serialize_stats_groups`
    output.

    Each member is reconstructed by name when a live replicate set still matches,
    otherwise from the wells stored alongside it (so a renamed/deleted set is
    reproduced by composition, not dropped). Only wells that still load are kept;
    a member with no loaded wells is dropped. Accepts the legacy name-only member
    format (``members: [name, ...]``) for plots saved before wells were stored."""
    from well_viewer.domain.batch_models import BarGroup, ReplicateSet
    rep_by_name = {r.name: r for r in app._rep_sets_loaded()}
    paths = app._app_state.dataset.well_paths
    out = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        members = []
        for m in item.get("members", []) or []:
            if isinstance(m, dict):
                name = str(m.get("name", "") or "")
                wells = [w for w in (m.get("wells") or []) if w in paths]
            else:  # legacy: bare replicate-set name, resolved against live sets
                name, wells = str(m), None
            live = rep_by_name.get(name)
            if live is not None:
                members.append(live)
            elif wells:
                members.append(ReplicateSet(name, wells))
        solos = [w for w in item.get("solo_wells", []) if w in paths]
        out.append(BarGroup(str(item.get("name", "Group")),
                            members=members, solo_wells=solos))
    return out



def stats_refresh_fov_indicator(app) -> None:
    """Update the Statistics tab's replicate-mode label from the FOV toggle, so
    the tab acknowledges which unit of replication the test will use."""
    lbl = getattr(app, "_stats_fov_indicator", None)
    if lbl is None:
        return
    try:
        on = bool(app._use_fov_spread_active())
    except Exception:
        return
    lbl.setText(
        "Replicates: per-well · single-well groups use FOVs (FOV mode ON)"
        if on else
        "Replicates: per-well · single-well groups need FOV mode (OFF)"
    )


def stats_collect_group_values_shim(
    app, grp: BarGroup, target_t: float
) -> List[float]:
    return collect_group_values(
        app, grp, target_t,
        val_col=app._stats_active_val_col(),
        threshold=app._stats_active_threshold(),
        statistic=app._stats_active_statistic(),
        use_fov=app._use_fov_spread_active(),
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

