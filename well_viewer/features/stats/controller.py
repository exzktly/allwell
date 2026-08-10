"""Statistics-tab computation helpers for WellViewerApp."""

from __future__ import annotations

import statistics as _pystats

import numpy as np
import pandas as pd

from well_viewer.data.data_loading import (
    df_included_mask,
    get_rows,
    is_ratio_key,
    resolve_value_series,
)
from well_viewer.domain.stats_compute import group_replicate_stats
from well_viewer.domain.tp_match import tp_close_mask


_STAT_LABELS = {
    "mean": "Mean (above threshold)",
    "mean_all": "Mean (all cells)",
    "median": "Median (above threshold)",
    "fraction": "Fraction above threshold",
}


def collect_group_values(
    ctx,
    grp,
    target_t: float,
    *,
    val_col: str | None = None,
    threshold: float | None = None,
    statistic: str = "mean",
    use_fov: bool = True,
) -> list[float]:
    """Return one summary value per replicate for the group's test.

    The replicate unit is decided by the SHARED :func:`replicate_unit` rule and
    the values come from the SHARED :func:`group_replicate_stats` producer — the
    exact same per-replicate values the bar/line error bars reduce — so the test's
    ``n`` and the plotted spread can never disagree. Returns ``[]`` when there are
    no loaded wells; a single-well group with FOV mode off yields a single value
    (``n=1``), which the caller declines as untestable.
    """
    val_col = val_col or ctx.state.channel.active_val_col
    if threshold is None:
        try:
            threshold = float(on_cutoff_for_column(
                ctx, val_col, channel=ctx.state.channel.active_channel))
        except Exception:
            threshold = float(ctx.state.channel.threshold)
    threshold = float(threshold)

    reps = group_replicate_stats(
        ctx, grp.wells, target_t, use_fov=use_fov,
        val_col=val_col, threshold=threshold,
    )
    out: list[float] = []
    for r in reps:
        s = r.summary(statistic)
        if s is not None and np.isfinite(s):
            out.append(float(s))
    return out


def collect_group_per_cell_values(
    ctx, grp, target_t: float, *, val_col: str | None = None,
    threshold: float | None = None,
) -> list[float]:
    """Return per-cell values above threshold for KS-test (distribution comparison)."""
    val_col = val_col or ctx.state.channel.active_val_col
    if threshold is None:
        try:
            threshold = float(on_cutoff_for_column(
                ctx, val_col, channel=ctx.state.channel.active_channel))
        except Exception:
            threshold = float(ctx.state.channel.threshold)
    threshold = float(threshold)
    ratios = ctx.state.channel.ratio_index
    chunks: list[np.ndarray] = []
    for lbl in grp.wells:
        if lbl not in ctx.state.dataset.well_paths:
            continue
        df = get_rows(ctx, lbl)
        if df is None or df.empty or "timepoint_hours" not in df.columns:
            continue
        mask = df_included_mask(df).to_numpy(copy=True)
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").to_numpy()
        with np.errstate(invalid="ignore"):
            mask &= np.isfinite(tp) & tp_close_mask(tp, target_t)
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


def draw_ks_cdf(ctx, group_vals: list[tuple[str, list[float]]], tp_str: str, well_colors: list[str]) -> None:
    """Draw empirical CDFs for the two KS-test groups."""
    app = ctx.window  # transitional: widget-heavy body still reads app._stats_*
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


def compute_pairwise(ctx, *, groups, target_t, val_col, statistic, threshold, test,
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
    group_vals: list[tuple[str, list[float]]] = []
    for g in groups:
        if is_ks:
            vals = collect_group_per_cell_values(ctx, g, target_t,
                                                 val_col=val_col, threshold=threshold)
        else:
            vals = collect_group_values(ctx, g, target_t, val_col=val_col,
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


def _sig_marker(p: float) -> str:
    """Significance stars for a p-value (audit A5): ``***``/``**``/``*``/``ns``."""
    return "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"


def _resolve_sample_units(is_ks: bool, use_fov: bool) -> str:
    """The 'Samples:' descriptor for the report header (audit A5)."""
    if is_ks:
        return "per-cell values"
    if use_fov:
        return "per-FOV (FOVs pooled across each group's wells)"
    return "per-well samples"


def _build_header_lines(
    *, test: str, channel_lbl: str, stat_lbl: str, threshold: float,
    tp_str: str, is_ks: bool, use_fov: bool,
) -> list[str]:
    """Assemble the report header block (audit A5). Pure — takes resolved
    labels and returns the lines, including the FOV-mode note."""
    sample_units = _resolve_sample_units(is_ks, use_fov)
    header_lines: list[str] = [
        f"Test:       {test}",
        f"Channel:    {channel_lbl}",
        f"Statistic:  {stat_lbl}" if not is_ks else "Statistic:  per-cell distribution",
        f"Threshold:  {threshold:.2f}",
        f"Timepoint:  {tp_str} h",
        f"Samples:    {sample_units}",
        "",
    ]
    if use_fov and not is_ks:
        header_lines.insert(-1,
            "Note:       FOV mode is ON — each field of view is one replicate, "
            "pooled across all of a group's wells (n = total FOVs per group).")
        header_lines.insert(-1, "")
    return header_lines


def _format_group_summary_lines(group_vals: list[tuple[str, list[float]]]) -> list[str]:
    """Per-group n/mean/median/sd summary block (audit A5). Pure."""
    lines: list[str] = []
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
    return lines


def _format_pairwise_lines(pair_results, adjusted: dict, show_adjusted: bool) -> list[str]:
    """Pairwise test results + BH-adjusted p-values (audit A5). Pure."""
    lines: list[str] = []
    for idx, (name_a, name_b, stat_name, stat_value, p, err) in enumerate(pair_results):
        lines.append(f"── {name_a}  vs  {name_b} ──")
        if err is not None:
            lines.append(f"  Error: {err}")
        elif p is not None and stat_value is not None:
            line = f"  {stat_name} = {stat_value:.4f}   p = {p:.4g}   {_sig_marker(p)}"
            if show_adjusted and idx in adjusted:
                ap = adjusted[idx]
                line += f"   |   p_BH = {ap:.4g}   {_sig_marker(ap)}"
            lines.append(line)
        lines.append("")
    if show_adjusted:
        lines.append(
            f"p_BH: Benjamini-Hochberg FDR-adjusted p-value across "
            f"{len(adjusted)} pairwise comparison(s). Report p_BH alongside "
            f"raw p when quoting significance with 3+ groups."
        )
        lines.append("")
    return lines


def run_stats(ctx, *, collect_group_values_fn, draw_ks_cdf_fn) -> None:
    """Run the selected statistical test across all group pairs."""
    app = ctx.window  # transitional: widget-heavy body still reads app._stats_*
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

    group_vals: list[tuple[str, list[float]]] = []
    for g in groups:
        if is_ks:
            vals = collect_group_per_cell_values(
                app._ctx, g, target_t, val_col=val_col, threshold=threshold,
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
            # A test needs >=2 replicate values. The replicate unit is per-FOV
            # (FOVs pooled across the group's wells) when FOV mode is on, else
            # per-well.
            if len(vals) < 2:
                if use_fov:
                    app._stats_write_result(
                        f"Group '{g.name}' (per-FOV) has fewer than 2 FOV samples "
                        f"across its well(s) at t={tp_str} h (threshold {threshold:.2f})."
                    )
                elif n_wells <= 1:
                    app._stats_write_result(
                        f"Group '{g.name}' is a single well and FOV mode is off "
                        f"— enable FOV mode (FOVs become replicates) or add "
                        f"replicate wells to run a test."
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

    # Replicate unit is per-FOV (FOVs pooled across each group's wells) when FOV
    # mode is on, else per-well. Note it in the header.
    lines: list[str] = _build_header_lines(
        test=test, channel_lbl=channel_lbl, stat_lbl=stat_lbl,
        threshold=threshold, tp_str=tp_str, is_ks=is_ks, use_fov=use_fov,
    )
    lines += _format_group_summary_lines(group_vals)

    # Run every pair, then apply Benjamini-Hochberg FDR across all valid
    # p-values (shared with ``compute_pairwise`` so the live results and a
    # Saved-Plot re-run are computed by the exact same code).
    pair_results = _run_pair_tests(group_vals, test, is_ks)
    adjusted = _bh_adjust(pair_results)
    show_adjusted = len(adjusted) >= 2
    lines += _format_pairwise_lines(pair_results, adjusted, show_adjusted)

    app._stats_write_result("\n".join(lines))

    # N7: store structured pairwise results so the Bar plot can draw significance
    # brackets from the same comparison (text output above is unchanged). Built by
    # the shared ``compute_pairwise`` so the live results are byte-identical to a
    # later Saved-Plot re-run of the same config. Each dict carries the compared
    # groups' WELLS (bar matching by membership), the tested measure (``stat`` →
    # which panel), and the run CONTEXT (``val_col`` / ``target_t``).
    app._app_state.stats.pairwise = compute_pairwise(
        app._ctx, groups=app._app_state.stats.groups, target_t=target_t,
        val_col=val_col, statistic=statistic, threshold=threshold, test=test,
        use_fov=use_fov)
    stats_refresh_fov_indicator(app._ctx)

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
from well_viewer.domain.batch_models import BarGroup
from well_viewer.core.ui_helpers import ask_name_dialog as _ask_name_dialog, clear_layout as _clear_layout
from well_viewer.core.channel_state_controller import on_cutoff_for_column, refresh_stats_property_combo, set_active_metric
from well_viewer.core.safe import swallowed
# A8 teardown: the stats group-editor controllers now take ``ctx`` and derive
# the loaded / visible rep-sets and their rank colours through the pure DOMAIN
# enumerators (a lower layer, so no cross-feature module import and no
# god-object reach-through — the same rule ``deserialize_stats_groups`` follows).
from well_viewer.domain.selections_model import (
    active_replicate_sets as _active_replicate_sets,
    color_for_replicate_set as _color_for_replicate_set,
    replicate_sets_loaded as _replicate_sets_loaded,
)


def _rank_color_rset(ctx, rset) -> str:
    """Rank colour for a ReplicateSet / BarGroup off ``ctx.state.selection``."""
    return _color_for_replicate_set(rset, ctx.state.selection.selections)


def _rep_sets_loaded(ctx):
    """All rep-sets with ≥1 loaded well (incl. hidden), from typed state."""
    return _replicate_sets_loaded(
        ctx.state.selection.selections, ctx.state.dataset.well_paths)


def _stats_groups_from_rep_sets(ctx) -> list[BarGroup]:
    """One BarGroup per loaded selection (each wrapping its ReplicateSet)."""
    return [BarGroup(r.name, members=[r]) for r in _rep_sets_loaded(ctx)]


def _stats_groups_from_visible_rep_sets(ctx) -> list[BarGroup]:
    """One BarGroup per *visible* (non-hidden) rep-set — the traces actually
    drawn on the plots."""
    rsets = _active_replicate_sets(
        ctx.state.selection.selections, ctx.state.dataset.well_paths)
    return [BarGroup(r.name, members=[r]) for r in rsets]


def stats_active_group(ctx):
    return ctx.state.stats.active_group()


def stats_apply_drag(ctx, tok: str) -> None:
    if tok in ctx.state.stats.drag_visited:
        return
    ctx.state.stats.drag_visited.add(tok)
    grp = stats_active_group(ctx)
    if grp is None or tok not in ctx.state.dataset.well_paths:
        return
    rset = next((r for r in _rep_sets_loaded(ctx) if tok in r.wells), None)
    if rset is not None:
        if ctx.state.stats.drag_adding:
            if rset not in grp.members:
                grp.members.append(rset)
        else:
            if rset in grp.members:
                grp.members.remove(rset)
    else:
        if ctx.state.stats.drag_adding:
            if tok not in grp.solo_wells:
                grp.solo_wells.append(tok)
        else:
            if tok in grp.solo_wells:
                grp.solo_wells.remove(tok)
    stats_refresh_map(ctx)


def stats_refresh_map(ctx) -> None:
    """Push group state onto the Statistics plate (a WellPlateSelector): each
    group's wells take its rank colour; the active group's wells are the plate's
    selection (sunken), which is also what a drag on the plate edits."""
    plate = ctx.widgets.get_or_none("_stats_map_plate")
    if plate is None:
        return
    avail = list(ctx.state.dataset.well_paths.keys())
    plate.setEnabledWells(avail)
    tok_color: dict = {}
    for grp in ctx.state.stats.groups:
        c = _rank_color_rset(ctx, grp)  # decision #1: colour by well-position rank
        for w in grp.wells:
            if w in ctx.state.dataset.well_paths:
                tok_color[w] = c   # last group wins — matches the other plates
    plate.clearWellColors()
    plate.setWellColors(tok_color)
    grp = stats_active_group(ctx)
    active = [w for w in grp.wells if w in ctx.state.dataset.well_paths] if grp else []
    plate.setSelectedWellIds(active)


def stats_refresh_group_list(ctx) -> None:
    # Every group mutation (add / delete / rename / clear / sync) funnels through
    # here, and this tab's empty state is gated on the group count rather than on
    # the plate selection — so this is the one place that has to tell the
    # empty-state stacks a group changed. Nothing else would: Statistics is not
    # on the selection_changed path.
    try:
        from well_viewer.core.ui_helpers import refresh_empty_state_pages
        refresh_empty_state_pages(ctx.window)
    except Exception:
        swallowed()
    container = ctx.widgets.get("_stats_grp_inner")
    layout = container.layout()
    if layout is None:
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
    _clear_layout(layout)
    if not ctx.state.stats.groups:
        lbl = QLabel("No groups.  Click + Add to create one.")
        lbl.setObjectName("Muted")
        layout.addWidget(lbl)
        stats_refresh_map(ctx)
        return
    for gi, grp in enumerate(ctx.state.stats.groups):
        is_sel = (gi == ctx.state.stats.active_grp)
        color = _rank_color_rset(ctx, grp)  # decision #1: colour by well-position rank
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
        ren_btn.clicked.connect(lambda _=False, i=idx: stats_grp_rename(ctx, i))
        hl.addWidget(ren_btn)
        del_btn = QPushButton("✕")
        del_btn.setFlat(True)
        del_btn.clicked.connect(lambda _=False, i=idx: stats_grp_delete(ctx, i))
        hl.addWidget(del_btn)

        def _click_select(ev, i=idx):
            stats_select_grp(ctx, i)
        card.mousePressEvent = _click_select
        layout.addWidget(card)
    layout.addStretch(1)
    stats_refresh_map(ctx)


def stats_select_grp(ctx, idx: int) -> None:
    ctx.state.stats.select_group(idx)
    stats_refresh_group_list(ctx)


def stats_grp_add(ctx) -> None:
    ctx.state.stats.add_group()
    stats_refresh_group_list(ctx)


def stats_grp_delete(ctx, idx: int) -> None:
    if ctx.state.stats.delete_group(idx):
        stats_refresh_group_list(ctx)


def stats_grp_rename(ctx, idx: int) -> None:
    stats = ctx.state.stats
    if not (0 <= idx < len(stats.groups)):
        return
    old = stats.groups[idx].name
    name = _ask_name_dialog(ctx.window, title="Rename group", prompt="Group name:", default=old)
    if name and stats.rename_group(idx, name):
        stats_refresh_group_list(ctx)


def stats_grp_clear_all(ctx) -> None:
    ctx.state.stats.clear_groups()
    stats_refresh_group_list(ctx)


def stats_sync_from_app(ctx, *, visible_only_in_split: bool = True) -> None:
    # On *auto-populate* (first visit) in Split View, seed the comparison groups
    # from only the rep-sets currently *visible* on the plots (non-hidden) so the
    # comparison matches what the user is looking at. The explicit "Load from
    # Home → Groups" action passes visible_only_in_split=False to load every
    # defined grouping regardless of visibility. Falls back to all groups outside
    # Split View, or when nothing is visible.
    if visible_only_in_split and ctx.runtime.split.active:
        groups = _stats_groups_from_visible_rep_sets(ctx) or _stats_groups_from_rep_sets(ctx)
    else:
        groups = _stats_groups_from_rep_sets(ctx)
    ctx.state.stats.set_groups(_copy.deepcopy(groups))
    stats_refresh_group_list(ctx)


def stats_resync_from_sample_defs(ctx) -> None:
    """Re-pull the comparison groups from the shared Sample-Definition sets.

    Statistics auto-seeds its groups from the Sample-Definition replicate sets
    on first visit (tab_controller); this is the explicit "do it again" so a
    user who edited the sample definitions can refresh without rebuilding them by
    hand. Confirm first when existing groups would be discarded.
    """
    from PySide6.QtWidgets import QMessageBox

    rsets = _stats_groups_from_rep_sets(ctx)
    if not rsets:
        ctx.notify.set_status("No sample-definition groups to load — define them on Home → Groups.")
        return
    existing = [g for g in ctx.state.stats.groups if g.wells]
    if existing:
        resp = QMessageBox.question(
            ctx.window,
            "Load from Home → Groups",
            f"Replace the current {len(existing)} comparison group(s) with the "
            f"{len(rsets)} Sample-Definition group(s)?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
    # Explicit "load all from Home → Groups" — never filter to the
    # split-visible subset (the dialog above promised all rsets groups).
    stats_sync_from_app(ctx, visible_only_in_split=False)
    ctx.notify.set_status(f"Loaded {len(rsets)} comparison group(s) from Home → Groups.")


def stats_on_test_change(ctx) -> None:
    """Clear results when test changes; update UI hints."""
    _stats_write_result(ctx, "")
    # Keep the stats Property combo in sync with the channel (ratio
    # channels collapse the metric set to "Calculated Val").
    try:
        refresh_stats_property_combo(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)


def _stats_write_result(ctx, text: str) -> None:
    """Write *text* into the registered stats result panel (no-op if absent)."""
    txt = ctx.widgets.get_or_none("_stats_result_text")
    if txt is None:
        return
    txt.setReadOnly(False)
    txt.setPlainText(text or "")
    txt.setReadOnly(True)


def stats_update_tp_menu(ctx) -> None:
    """Populate the timepoint, channel, and statistic dropdowns."""
    from well_viewer.core.ui_helpers import set_combo_values
    from well_viewer.data.data_loading import get_rows
    from well_viewer.core.channel_state_controller import ratio_label_for

    tp_cb = ctx.widgets.get_or_none("_stats_tp_cb")
    all_tps: set = set()
    for label in ctx.state.dataset.well_paths:
        df = get_rows(ctx, label)
        if df is None or "timepoint_hours" not in df.columns:
            continue
        tp = pd.to_numeric(df["timepoint_hours"], errors="coerce").dropna().unique()
        all_tps.update(float(t) for t in tp)
    sorted_tps = sorted(all_tps)
    tp_strs = [f"{t:.4g}" for t in sorted_tps]
    if tp_cb is not None:
        set_combo_values(tp_cb, tp_strs or ["—"])
        if hasattr(tp_cb, "setCurrentText"):
            tp_cb.setCurrentText(tp_strs[0] if tp_strs else "—")

    # Channel dropdown -- list every measurement column so the user can
    # pick any value to compare. Mirrors the scatter-tab dropdown so the
    # _col_for_scatter_entry mapping is reused.
    channels = list(ctx.state.channel.fluor_channels) if ctx.state.channel.fluor_channels else []
    ch_options: list[str] = []
    for ch in channels:
        # Display fluor tokens upper-case (GFP) to match the rest of the app;
        # col_for_scatter_entry resolves the column case-insensitively and the
        # chart titles already upper-case (_channel_label_for_val_col). ``ch``
        # stays lower-case for the smFISH-channel membership test.
        ch_options.append(ch.upper())
        if ch in ctx.state.channel.smfish_channels:
            ch_options.append(f"{ch.upper()} (spots)")
    # Ratio "channels" (computed per-cell values) and shape-class features are
    # selectable too — they resolve via _label_to_channel_key in
    # col_for_scatter_entry, and the Property combo collapses to a single
    # Calculated Val for them (see populate_metric_combo).
    ch_options.extend(ratio_label_for(ctx, r) for r in ctx.state.channel.ratio_metrics)
    from pipeline.shape_features import label_for as _shape_label_for
    for _col in (ctx.state.channel.shape_features or []):
        ch_options.append(_shape_label_for(_col) or _col)
    if not ch_options:
        ch_options = ["—"]
    channel_cb = ctx.widgets.get_or_none("_stats_channel_cb")
    if channel_cb is not None:
        current_ch = channel_cb.currentText()
        set_combo_values(channel_cb, ch_options)
        if current_ch in ch_options:
            channel_cb.setCurrentText(current_ch)
        else:
            # Default to the active channel when possible to match the
            # bar/line plots' current selection (compare upper-case since the
            # options now display upper-case while the token is lower-case).
            active_disp = (ctx.state.channel.active_channel or "").upper()
            active = active_disp if active_disp in ch_options else ch_options[0]
            channel_cb.setCurrentText(active)
    # Channel may now resolve to a ratio key -- refresh the Property
    # combo's item set accordingly.
    try:
        refresh_stats_property_combo(ctx)
    except Exception:
        swallowed()  # was: pass (silent except)


def stats_refresh_colors(ctx) -> None:
    """Refresh matplotlib figure facecolor on theme change.

    Widget colours are driven by the app-level QSS stylesheet; only the
    embedded matplotlib figure needs an explicit repaint.
    """
    import theme
    app = ctx.window  # transitional: figure handles still on app._stats_*

    if hasattr(app, "_stats_fig"):
        app._stats_fig.set_facecolor(theme.Colors.surface)
    if hasattr(app, "_stats_canvas_widget"):
        app._stats_canvas_widget.draw()


def threshold_for_val_col(ctx, val_col: str) -> float:
    """The ON cutoff for a value column (the per-channel Cell-Gating cut).

    Ratio columns have no per-channel cut → the global threshold. Everything
    else defers to ``on_cutoff_for_column``, which picks ThreshFracOn or
    ThreshCountOn from what the column measures. Keyed off the passed
    ``val_col`` (not the live widget) so it's reusable at Saved-Plot re-run
    time."""
    if is_ratio_key(val_col):
        return float(ctx.state.channel.threshold)
    return float(on_cutoff_for_column(ctx, val_col))


def stats_active_threshold(ctx) -> float:
    """The per-channel ON cutoff for the currently-selected stats value column."""
    return threshold_for_val_col(ctx, stats_active_val_col(ctx))


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


def deserialize_stats_groups(ctx, data) -> list:
    """Rebuild stats ``BarGroup`` objects from :func:`serialize_stats_groups`
    output.

    Each member is reconstructed by name when a live replicate set still matches,
    otherwise from the wells stored alongside it (so a renamed/deleted set is
    reproduced by composition, not dropped). Only wells that still load are kept;
    a member with no loaded wells is dropped. Accepts the legacy name-only member
    format (``members: [name, ...]``) for plots saved before wells were stored."""
    from well_viewer.domain.batch_models import BarGroup, ReplicateSet
    paths = ctx.state.dataset.well_paths
    # One ReplicateSet per selection with >=1 loaded well — the same rule as
    # ``domain.selections_model.replicate_sets_loaded``, inlined off
    # ``ctx.state`` so this stays free of the app god-object.
    rep_by_name: dict = {}
    for s in ctx.state.selection.selections:
        s_wells = s.get("wells") or []
        if any(w in paths for w in s_wells):
            rs = ReplicateSet(s.get("name") or "", list(s_wells))
            rep_by_name[rs.name] = rs
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



def stats_refresh_fov_indicator(ctx) -> None:
    """Update the Statistics tab's replicate-mode label from the FOV toggle, so
    the tab acknowledges which unit of replication the test will use."""
    lbl = ctx.widgets.get_or_none("_stats_fov_indicator")
    if lbl is None:
        return
    try:
        on = bool(ctx.state.plot.use_fov_replicates)
    except Exception:
        return
    lbl.setText(
        "Replicates: per-FOV — FOVs pooled across each group's wells (FOV mode ON)"
        if on else
        "Replicates: per-well (enable FOV mode to use FOVs as replicates)"
    )


def stats_collect_group_values_shim(
    ctx, grp: BarGroup, target_t: float
) -> list[float]:
    return collect_group_values(
        ctx, grp, target_t,
        val_col=stats_active_val_col(ctx),
        threshold=stats_active_threshold(ctx),
        statistic=stats_active_statistic(ctx),
        use_fov=ctx.window._use_fov_spread_active(),
    )



def stats_active_val_col(ctx) -> str:
    """Resolve the selected channel-dropdown entry to a value-column key."""
    entry = stats_active_channel_entry(ctx)
    if not entry:
        return ctx.state.channel.active_val_col
    try:
        return ctx.window._col_for_scatter_entry(entry)
    except Exception:
        return ctx.state.channel.active_val_col



def stats_active_channel_entry(ctx) -> str:
    cb = getattr(ctx.window, "_stats_channel_cb", None)
    if cb is None:
        return ctx.state.channel.active_channel or ""
    text = cb.currentText().strip()
    return text if text and text != "—" else (ctx.state.channel.active_channel or "")



def stats_active_statistic(ctx) -> str:
    cb = getattr(ctx.window, "_stats_statistic_cb", None)
    if cb is None:
        return "mean"
    return ctx.window._STATS_STATISTIC_KEYS.get(cb.currentText(), "mean")



def stats_run_shim(ctx) -> None:
    app = ctx.window
    run_stats(
        ctx,
        collect_group_values_fn=app._stats_collect_group_values,
        draw_ks_cdf_fn=app._stats_draw_ks_cdf,
    )



def stats_draw_ks_cdf_shim(
    ctx,
    group_vals: list[tuple[str, list[float]]],
    tp_str: str,
) -> None:
    draw_ks_cdf(ctx, group_vals, tp_str, WELL_COLORS)



def on_stats_property_change(ctx) -> None:
    """Stats tab's per-tab Property combo → propagate to global state."""
    from well_viewer.data.metric_labels import METRIC_LABEL_TO_KEY
    cb = getattr(ctx.window, "_stats_property_cb", None)
    if cb is None:
        return
    metric = METRIC_LABEL_TO_KEY.get(str(cb.currentText() or ""), "mean_intensity")
    if metric == "smfish_count" and ctx.state.channel.active_channel not in ctx.state.channel.smfish_channels:
        metric = "mean_intensity"
    set_active_metric(ctx, metric)

