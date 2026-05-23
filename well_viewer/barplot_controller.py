"""Bar-plot group state serialization helpers extracted from well_viewer3."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from .batch_models import BarGroup, ReplicateSet
from . import debug_flags
from . import fold_change as _fc
from well_viewer.stats_compute import compute_rep_stats
from well_viewer.stats_compute import compute_rep_per_fov_stats
from well_viewer.stats_compute import compute_rep_n_above
from well_viewer.stats_compute import aggregate_well
from well_viewer.stats_compute import aggregate_group
from well_viewer.ui_helpers import set_combo_values as _set_combo_values



@dataclass
class BarItem:
    """One bar in the bar-plot view, post-normalization.

    A single shape consumed by every downstream renderer (on-screen,
    off-screen export, CSV emitter). Replaces the loose-tuple-with-
    backwards-compat-positional-unpacking pattern that grew during the
    fold-change PR.

    Fields:
      * ``key``       — drag-order identity. Replicate-set name for
        grouped mode, well token ("A01") for per-well mode.
      * ``display``   — x-tick label shown on the chart.
      * ``color``     — bar fill colour, already resolved per item
        (rank colour for rep-set, well-rank colour for per-well).
      * ``mean`` / ``spread`` — post-normalization values. NaN /
        ``has_mean=False`` means render as the dashed placeholder.
      * ``frac`` / ``frac_spread`` / ``has_frac`` — fraction-above-
        threshold panel. Always raw (fractions don't get fold-changed).
      * ``n_above`` / ``n_above_spread`` — events-above-threshold panel.
        ``n_above_spread > 0`` indicates per-FOV spread mode; in that
        case ``n_above`` is the per-FOV mean (float), otherwise it's
        the total count (kept as float for storage uniformity, cast on
        display).
    """
    key: str
    display: str
    color: str
    mean: float
    spread: float
    has_mean: bool
    frac: float
    frac_spread: float
    has_frac: bool
    n_above: float
    n_above_spread: float


def _bar_debug(msg: str) -> None:
    """Emit opt-in bar-plot diagnostics for draw-time label debugging."""
    if debug_flags.review_bar_debug_enabled():
        print(f"DEBUG barplot_controller: {msg}")


def bar_groups_to_dict(
    rep_sets: List[ReplicateSet],
    bar_groups: List[BarGroup],
    *,
    extract_well_token: Callable[[str], Optional[str]],
) -> dict:
    rep_list = [{"name": r.name, "wells": list(r.wells)} for r in rep_sets]
    grp_list = []
    for grp in bar_groups:
        grp_list.append(
            {
                "name": grp.name,
                "hidden": grp.hidden,
                "members": [r.name for r in grp.members],
                "solo_wells": list(grp.solo_wells),
            }
        )
    return {"rep_sets": rep_list, "groups": grp_list}


def bar_groups_from_data(data, *, tok_to_label: dict[str, str]) -> Tuple[List[ReplicateSet], List[BarGroup]]:
    def _norm(tok: str) -> str:
        tok = tok.strip().upper()
        m = re.match(r"^([A-H])(\d{1,2})$", tok, re.I)
        return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else tok

    def _valid_tok(raw: str) -> Optional[str]:
        n = _norm(raw)
        return n if n in tok_to_label else None

    rep_sets: List[ReplicateSet] = []
    bar_groups: List[BarGroup] = []

    if not isinstance(data, dict):
        return rep_sets, bar_groups
    for item in data.get("rep_sets", []):
        wells = [t for t in (_valid_tok(x) for x in item.get("wells", [])) if t is not None]
        rep_sets.append(ReplicateSet(item.get("name", "R"), wells))
    rep_by_name = {r.name: r for r in rep_sets}
    for item in data.get("groups", []):
        grp = BarGroup(item.get("name", "Group"), hidden=bool(item.get("hidden", False)))
        for rname in item.get("members", []):
            if rname in rep_by_name:
                grp.members.append(rep_by_name[rname])
        for raw_tok in item.get("solo_wells", []):
            n = _valid_tok(raw_tok)
            if n:
                grp.solo_wells.append(n)
        bar_groups.append(grp)

    return rep_sets, bar_groups


#: Fold-change "off" state — pass to ``collect_bar_items`` or
#: ``collect_bar_items_for_group`` when you want the raw (unscaled) values,
#: e.g. to emit the un-normalized columns of an additive CSV alongside the
#: fold-change columns.
FC_STATE_OFF: Tuple[bool, str, bool] = (False, "", False)


def collect_bar_items(
    app, target_t: float, *,
    fc_state: Optional[Tuple[bool, str, bool]] = None,
    miss_sink: Optional[set] = None,
) -> Tuple[bool, List[BarItem], str]:
    """Compute the bar-plot ``BarItem``s for the active mode (rep-set or per-well).

    Single source of truth — every consumer of bar data (on-screen renderer,
    off-screen figure exporter, CSV emitter) receives the same ``BarItem``
    list in the same order. Fold-change normalization is applied exactly
    once, here. The order follows ``app._bar_current_keys()`` so drag-
    reorder state is honoured by every consumer without a rebuild.

    When *fc_state* is None (default) the app's current state is used.
    Callers that want raw values can pass :data:`FC_STATE_OFF` — the
    aggregation helpers are cached so a second call is cheap, which is how
    the on-tab CSV emits the additive (raw + fold-change) schema.

    *miss_sink* — optional set the caller passes in; the function adds
    *target_t* to it when fold-change-vs-control is requested but the
    control has no sample at *target_t* (so the caller can surface the
    drop via ``_set_status`` rather than emit silently empty bars).

    Returns ``(use_groups, items, band_lbl)``. ``use_groups`` is True when
    replicate sets are active (drives a slightly wider bar style in the
    renderer); the items themselves carry display labels and colours so
    callers don't need to re-resolve them.
    """
    use_sem = app._app_state.plot.use_sem
    band_lbl = "SEM" if use_sem else "SD"
    threshold = app._get_thresh_frac_on(app._app_state.channel.active_channel)
    active_rsets = app._rep_sets_active()
    use_groups = bool(active_rsets)

    # Fold-change normalization (vs control well/group, vs t0, or both).
    # Resolved once here so we don't re-aggregate the control per bar.
    # Numerator (each bar) and denominator (control / t0) use the SAME
    # stat — see ``fold_change.member_mean_series`` for the rationale.
    if fc_state is None:
        fc_state = _fc.fold_change_state(app)
    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state
    cell_area_threshold = app._get_cell_area_threshold()
    fluor_gates = app._get_all_fluor_gates()
    per_fov_spread = app._use_fov_spread_active()
    fc_control_mean_at_t = None
    fc_control_spread_at_t = None
    if fc_vs_ctrl and fc_ctrl_lbl:
        ctrl_stats = _fc.control_stats_at_for_bar(
            app, fc_ctrl_lbl, target_t,
            threshold=threshold, val_col=app._app_state.channel.active_val_col,
            use_sem=use_sem, per_fov_spread=per_fov_spread,
            cell_area_threshold=cell_area_threshold,
            fluor_gates=fluor_gates,
        )
        if ctrl_stats is None:
            if miss_sink is not None:
                # Control couldn't be resolved at target_t — record the
                # miss so the caller can surface a status warning. Bars
                # below will be forced to NaN by ``fc_force_nan`` so the
                # plot doesn't silently fall back to raw values while
                # the axis title still claims fold-change normalization.
                miss_sink.add(float(target_t))
        else:
            fc_control_mean_at_t, fc_control_spread_at_t = ctrl_stats
    fc_force_nan = fc_vs_ctrl and fc_ctrl_lbl and fc_control_mean_at_t is None

    ordered_keys = app._bar_current_keys()
    items: List[BarItem] = []

    if use_groups:
        rset_by_name = {r.name: r for r in active_rsets}
        for key in ordered_keys:
            rset = rset_by_name.get(key)
            if rset is None:
                continue
            color = app._rank_color_rset(rset)  # decision #1: colour by well-position rank
            display = app._replicate_display_label(rset)
            if per_fov_spread:
                # Pool every FOV across all wells in the rep set; SD/SEM is
                # across that pooled set of per-FOV means/fractions/counts.
                gm, g_err_m, gf, g_err_f, n_above_pf_mean, n_above_pf_spread = (
                    compute_rep_per_fov_stats(app, rset, target_t, threshold, use_sem)
                )
                n_above_val = float(n_above_pf_mean)
                n_above_err = float(n_above_pf_spread)
            else:
                gm, g_err_m, gf, g_err_f = compute_rep_stats(app, rset, target_t, threshold, use_sem)
                n_above_val = float(compute_rep_n_above(app, rset, target_t))
                n_above_err = 0.0
            t0_mean = None
            t0_spread = None
            if fc_vs_t0:
                stats = _fc.member_first_tp_stats(
                    app, rset.name,
                    threshold=threshold, val_col=app._app_state.channel.active_val_col,
                    use_sem=use_sem, per_fov_spread=per_fov_spread,
                    cell_area_threshold=cell_area_threshold,
                    fluor_gates=fluor_gates,
                )
                if stats is not None:
                    t0_mean, t0_spread = stats
            if fc_force_nan:
                gm = float("nan")
                g_err_m = 0.0
            elif fc_vs_ctrl or fc_vs_t0:
                gm, g_err_m = _fc.scale_bar_value(
                    gm, g_err_m,
                    control_mean=fc_control_mean_at_t if fc_vs_ctrl else None,
                    control_spread=fc_control_spread_at_t if fc_vs_ctrl else None,
                    t0_mean=t0_mean if fc_vs_t0 else None,
                    t0_spread=t0_spread if fc_vs_t0 else None,
                )
            has_mean = not math.isnan(gm)
            # Preserve the legacy grouped-mode behaviour: when the mean is
            # NaN, the fraction panel also renders as a placeholder. The
            # per-well branch below evaluates frac independently.
            has_frac = has_mean and not math.isnan(gf)
            items.append(BarItem(
                key=rset.name, display=display, color=color,
                mean=gm, spread=g_err_m, has_mean=has_mean,
                frac=gf, frac_spread=g_err_f, has_frac=has_frac,
                n_above=n_above_val, n_above_spread=n_above_err,
            ))
        return True, items, band_lbl

    # Per-well branch — iterate the same drag order, look up cell data.
    for label in ordered_keys:
        if label not in app._app_state.dataset.well_paths:
            continue
        color = app._rank_color_well(label)
        display = app._bar_well_display_label(label)
        pts = aggregate_well(app, 
            label, threshold=threshold, use_sem=use_sem,
            val_col=app._app_state.channel.active_val_col,
            cell_area_threshold=cell_area_threshold,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        # AggPoint: (t, mean, mean_spread, frac, n_above, n_total, frac_spread,
        #            n_above_per_fov_mean, n_above_per_fov_spread).
        # When the Aggregate FOVs toggle is on, the events panel reports
        # mean ± SD/SEM across the per-FOV above-threshold counts so the
        # column stays consistent with the per-FOV stats already shown in
        # rows 1 and 2; otherwise it reports the well's total count with no
        # error bar.
        matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
        if matched:
            pt = matched[0]
            m, s, f = pt[1], pt[2], pt[3]
            n_above_total = int(pt[4])
            fs = float(pt[6]) if len(pt) >= 7 else 0.0
            n_above_pf_mean = float(pt[7]) if len(pt) >= 8 else 0.0
            n_above_pf_spread = float(pt[8]) if len(pt) >= 9 else 0.0
            if fc_force_nan:
                m = float("nan")
                s = 0.0
            elif fc_vs_ctrl or fc_vs_t0:
                # t0 baseline = the well's own (mean, spread) at its
                # earliest tp. ``pts`` is the well's full time series,
                # so the first finite mean there is the baseline; the
                # spread at the same row feeds the error propagation.
                t0_mean = None
                t0_spread = None
                if fc_vs_t0:
                    for _pt in sorted(pts, key=lambda p: p[0]):
                        _m = _pt[1]
                        if (isinstance(_m, (int, float))
                                and math.isfinite(_m) and _m != 0):
                            t0_mean = float(_m)
                            _sp = _pt[2] if len(_pt) > 2 else 0.0
                            t0_spread = (
                                float(_sp) if (
                                    isinstance(_sp, (int, float))
                                    and math.isfinite(_sp)
                                ) else 0.0
                            )
                            break
                m, s = _fc.scale_bar_value(
                    m, s,
                    control_mean=fc_control_mean_at_t if fc_vs_ctrl else None,
                    control_spread=fc_control_spread_at_t if fc_vs_ctrl else None,
                    t0_mean=t0_mean if fc_vs_t0 else None,
                    t0_spread=t0_spread if fc_vs_t0 else None,
                )
            has_mean = not math.isnan(m)
            has_frac = not math.isnan(f)
            if per_fov_spread:
                n_above_val = n_above_pf_mean
                n_above_err = n_above_pf_spread
            else:
                n_above_val = float(n_above_total)
                n_above_err = 0.0
            items.append(BarItem(
                key=label, display=display, color=color,
                mean=m, spread=s, has_mean=has_mean,
                frac=f, frac_spread=fs, has_frac=has_frac,
                n_above=n_above_val, n_above_spread=n_above_err,
            ))
        else:
            items.append(BarItem(
                key=label, display=display, color=color,
                mean=float("nan"), spread=0.0, has_mean=False,
                frac=float("nan"), frac_spread=0.0, has_frac=False,
                n_above=0.0, n_above_spread=0.0,
            ))
    return False, items, band_lbl


def collect_bar_items_for_group(
    app,
    group: BarGroup,
    target_t: float,
    *,
    val_col: str,
    threshold: float,
    use_sem: bool,
    per_fov_spread: bool,
    fc_state: Tuple[bool, str, bool],
    cell_area_threshold: Optional[float] = None,
    fluor_gates: Optional[dict] = None,
) -> List[BarItem]:
    """Build ``BarItem``s for a batch-export ``BarGroup`` (members + solo wells).

    This is the batch-side analogue of :func:`collect_bar_items`. The plot
    tab's collector reads from ``app._rep_sets_active()`` / ``_selected_wells``
    and ``app._fc_*`` state; the batch path needs to operate on arbitrary
    ``BarGroup`` instances with a panel-local fold-change state — so
    everything is passed in explicitly. The batch panel maintains its own
    ``_fc_*`` mirror (intentionally decoupled from the plot tab) and
    forwards it via ``fc_state``.

    Pass :data:`FC_STATE_OFF` to skip fold-change scaling and get raw
    aggregated values (used by the additive CSV emitter).
    """
    if cell_area_threshold is None:
        cell_area_threshold = app._get_cell_area_threshold()
    if fluor_gates is None:
        fluor_gates = app._get_all_fluor_gates()

    fc_vs_ctrl, fc_ctrl_lbl, fc_vs_t0 = fc_state
    fc_control_mean_at_t = None
    fc_control_spread_at_t = None
    if fc_vs_ctrl and fc_ctrl_lbl:
        # Batch path uses ``_aggregate_group`` (pool-of-cells) for the
        # bar numerator, so the control denominator stays on the
        # pool-of-cells stat too — keeps the ratio internally
        # consistent. (The plot tab uses ``_compute_rep_stats``
        # mean-of-means and pairs with ``control_stats_at_for_bar`` to
        # match. The cross-path numerator difference is a pre-existing
        # issue out of scope here.)
        ctrl_stats = _fc.control_stats_at(
            app, fc_ctrl_lbl, target_t,
            threshold=threshold, val_col=val_col,
            cell_area_threshold=cell_area_threshold,
            fluor_gates=fluor_gates,
        )
        if ctrl_stats is not None:
            fc_control_mean_at_t, fc_control_spread_at_t = ctrl_stats

    items: List[BarItem] = []
    members: List[Tuple[str, str, list[str]]] = []  # (key, display, wells)
    for rset in group.members:
        valid = [w for w in rset.wells if w in app._app_state.dataset.well_paths]
        if not valid:
            continue
        members.append((rset.name, rset.name, valid))
    for w in group.solo_wells:
        if w not in app._app_state.dataset.well_paths:
            continue
        members.append((w, w, [w]))

    # Use the batch-export palette by position rank — matches the prior
    # behaviour of ``_render_bar_group_figure`` before consolidation. Each
    # caller may override the colour by mutating ``item.color`` after the
    # call if needed.
    from well_viewer.plate_layout import WELL_COLORS as _WELL_COLORS

    for i, (key, display, wells) in enumerate(members):
        color = _WELL_COLORS[i % len(_WELL_COLORS)]
        pts = aggregate_group(app, 
            wells, threshold=threshold, use_sem=use_sem,
            val_col=val_col,
            cell_area_threshold=cell_area_threshold,
            fluor_gates=fluor_gates,
            per_fov_spread=per_fov_spread,
        )
        matched = [pt for pt in pts if abs(pt[0] - target_t) < 1e-6]
        if matched:
            pt = matched[0]
            m, s, f = pt[1], pt[2], pt[3]
            n_above_total = int(pt[4]) if len(pt) >= 5 else 0
            frac_spread = float(pt[6]) if len(pt) >= 7 else 0.0
            n_above_pf_mean = float(pt[7]) if len(pt) >= 8 else 0.0
            n_above_pf_spread = float(pt[8]) if len(pt) >= 9 else 0.0
            if per_fov_spread:
                n_above_val = n_above_pf_mean
                n_above_err = n_above_pf_spread
            else:
                n_above_val = float(n_above_total)
                n_above_err = 0.0
            if fc_vs_ctrl or fc_vs_t0:
                # Batch t0 baseline: pool-of-cells stat (matches the
                # batch numerator). Spread comes from the same AggPoint
                # at the earliest tp so error propagation has the right
                # uncertainty for the baseline.
                t0_mean = None
                t0_spread = None
                if fc_vs_t0:
                    for _pt in sorted(pts, key=lambda p: p[0]):
                        _m = _pt[1]
                        if (isinstance(_m, (int, float))
                                and math.isfinite(_m) and _m != 0):
                            t0_mean = float(_m)
                            _sp = _pt[2] if len(_pt) > 2 else 0.0
                            t0_spread = (
                                float(_sp) if (
                                    isinstance(_sp, (int, float))
                                    and math.isfinite(_sp)
                                ) else 0.0
                            )
                            break
                m, s = _fc.scale_bar_value(
                    m, s,
                    control_mean=fc_control_mean_at_t if fc_vs_ctrl else None,
                    control_spread=fc_control_spread_at_t if fc_vs_ctrl else None,
                    t0_mean=t0_mean if fc_vs_t0 else None,
                    t0_spread=t0_spread if fc_vs_t0 else None,
                )
            has_mean = not math.isnan(m)
            has_frac = has_mean and not math.isnan(f)
            items.append(BarItem(
                key=key, display=display, color=color,
                mean=m, spread=s, has_mean=has_mean,
                frac=f, frac_spread=frac_spread, has_frac=has_frac,
                n_above=n_above_val, n_above_spread=n_above_err,
            ))
        else:
            items.append(BarItem(
                key=key, display=display, color=color,
                mean=float("nan"), spread=0.0, has_mean=False,
                frac=float("nan"), frac_spread=0.0, has_frac=False,
                n_above=0.0, n_above_spread=0.0,
            ))
    return items


def ordered_bar_keys(app) -> list:
    """Return ordered bar keys for current mode, respecting custom drag order."""
    active_rsets = app._rep_sets_active()
    if active_rsets:
        keys = [r.name for r in active_rsets]
    else:
        keys = sorted(
            (lbl for lbl in app._app_state.selection.selected_wells if lbl in app._app_state.dataset.well_paths),
            key=lambda lbl: app._parse_rc(lbl),
        )
    if app._app_state.plot.bar_order is None:
        return keys
    ordered = [k for k in app._app_state.plot.bar_order if k in keys]
    seen = set(ordered)
    ordered.extend(k for k in keys if k not in seen)
    return ordered


def render_bar_items(
    *,
    ax_mean,
    ax_frac,
    use_groups: bool,
    items: List[BarItem],
    threshold: float,
    warn_color: str,
    border_color: str,
    placeholder_color: str,
    disabled_well_color: str,
    err_bar_color: str,
    ax_n=None,
) -> None:
    """Draw mean / fraction / events-above-threshold bar panels.

    Consumes ``BarItem`` records emitted by :func:`collect_bar_items`. All
    per-bar state (display label, colour, value, spread, has-flags) lives on
    the item, so the renderer doesn't second-guess what to draw. ``use_groups``
    only governs the bar-width preset — grouped mode draws slightly wider
    bars to differentiate visually from the per-well view.
    """
    n = len(items)
    xlabels = [item.display for item in items]
    keys = [item.key for item in items]
    _bar_debug(
        f"render_bar_items use_groups={use_groups} n={n} keys={keys!r} xlabels={xlabels!r}"
    )

    bar_w = min(0.65 if use_groups else 0.6, 5.0 / max(n, 1))
    for i, item in enumerate(items):
        color = item.color
        # Mean panel.
        if item.has_mean and not math.isnan(item.mean):
            ax_mean.bar(i, item.mean, width=bar_w, color=color, alpha=0.85,
                        zorder=3, linewidth=0)
            if item.spread > 0:
                ax_mean.errorbar(i, item.mean, yerr=item.spread, fmt="none",
                                 ecolor=err_bar_color, elinewidth=1.4,
                                 capsize=4, zorder=4)
        else:
            ax_mean.bar(i, 0, width=bar_w, color=placeholder_color,
                        linewidth=1, edgecolor=disabled_well_color,
                        linestyle="--", zorder=3)
        # Fraction panel.
        if item.has_frac and not math.isnan(item.frac):
            ax_frac.bar(i, item.frac, width=bar_w, color=color, alpha=0.85,
                        zorder=3, linewidth=0)
            if item.frac_spread > 0:
                ax_frac.errorbar(i, item.frac, yerr=item.frac_spread,
                                 fmt="none", ecolor=err_bar_color,
                                 elinewidth=1.4, capsize=4, zorder=4)
        else:
            ax_frac.bar(i, 0, width=bar_w, color=placeholder_color,
                        linewidth=1, edgecolor=disabled_well_color,
                        linestyle="--", zorder=3)
        # Events-above-threshold panel (optional).
        if ax_n is not None:
            if item.has_mean and item.n_above > 0:
                ax_n.bar(i, item.n_above, width=bar_w, color=color,
                         alpha=0.85, zorder=3, linewidth=0)
                if item.n_above_spread > 0:
                    ax_n.errorbar(i, item.n_above, yerr=item.n_above_spread,
                                  fmt="none", ecolor=err_bar_color,
                                  elinewidth=1.4, capsize=4, zorder=4)
            else:
                ax_n.bar(i, 0, width=bar_w, color=placeholder_color,
                         linewidth=1, edgecolor=disabled_well_color,
                         linestyle="--", zorder=3)

    ax_frac.axhline(0.5, color=border_color, lw=0.8, ls="--", alpha=0.5, zorder=1)
    # Add threshold context label to fraction axis
    ax_frac.text(0.02, 0.98, f"threshold={threshold:.2f}", transform=ax_frac.transAxes,
                 fontsize=8, va="top", ha="left", color=warn_color, alpha=0.7)

    xs = list(range(n))
    axes_to_label = [ax_mean, ax_frac]
    if ax_n is not None:
        axes_to_label.append(ax_n)
    for ax in axes_to_label:
        ax.set_xticks(xs)
        ax.set_xticklabels(
            xlabels,
            rotation=45 if n > 8 else 0,
            ha="right" if n > 8 else "center",
            fontsize=7,
        )
        ax.set_xlim(-0.6, n - 0.4)
        # Mark categorical x-axis so downstream styling does not reset tick formatters.
        setattr(ax, "_categorical_xaxis", True)
    if ax_n is not None:
        cur_lo, cur_hi = ax_n.get_ylim()
        ax_n.set_ylim(0, max(cur_hi, 1))
        # Default to integer ticks because N is a count of events; in
        # Aggregate-FOVs mode the bar shows a per-FOV mean which can be
        # fractional, so let matplotlib pick auto ticks then.
        try:
            from matplotlib.ticker import MaxNLocator
            ax_n.yaxis.set_major_locator(MaxNLocator(integer=False))
        except ImportError:
            pass


def apply_bar_ylims(app, ax_mean, ax_frac, *, ax_n=None) -> None:
    """Apply user-entered y-limits to the bar panels (linear scale only)."""
    def _parse(edit) -> Optional[float]:
        txt = edit.text().strip() if hasattr(edit, "text") else str(edit).strip()
        if not txt:
            return None
        try:
            return float(txt)
        except ValueError:
            return None

    mean_lo = _parse(app._bar_ylim_mean_lo_edit)
    mean_hi = _parse(app._bar_ylim_mean_hi_edit)
    if mean_lo is not None or mean_hi is not None:
        cur_lo, cur_hi = ax_mean.get_ylim()
        lo = mean_lo if mean_lo is not None else cur_lo
        hi = mean_hi if mean_hi is not None else cur_hi
        if hi > lo:
            ax_mean.set_ylim(lo, hi)

    frac_lo = _parse(app._bar_ylim_frac_lo_edit)
    frac_hi = _parse(app._bar_ylim_frac_hi_edit)
    if frac_lo is not None or frac_hi is not None:
        cur_lo, cur_hi = ax_frac.get_ylim()
        lo = frac_lo if frac_lo is not None else cur_lo
        hi = frac_hi if frac_hi is not None else cur_hi
        if hi > lo:
            ax_frac.set_ylim(lo, hi)


# ── Drag-and-drop reordering ─────────────────────────────────────────────────
#
# Lightweight helpers that drive the bar drag interaction. The view (matplotlib
# canvas + axes) is owned by ``WellViewerApp``; these helpers only mutate
# ``app._bar_drag_state`` / ``app._app_state.plot.bar_order`` and trigger a redraw.

def bar_event_xdata(app, event) -> Optional[float]:
    """Return data-x for a matplotlib MouseEvent over any bar axis."""
    ax = getattr(event, "inaxes", None)
    if ax is not app._ax_bar_mean and ax is not app._ax_bar_frac and ax is not getattr(app, "_ax_bar_n", None):
        return None
    xdata = getattr(event, "xdata", None)
    if xdata is None:
        return None
    return float(xdata)


def bar_idx_at_x(xdata: float, n: int) -> int:
    """Return the bar index nearest to *xdata*, clamped to [0, n-1]."""
    return max(0, min(n - 1, int(round(xdata))))


def bar_reset_order(app) -> None:
    app._app_state.plot.bar_order = None
    app._bar_reset_order_btn.setProperty("variant", "toggle_muted")
    app._bar_reset_order_btn.style().unpolish(app._bar_reset_order_btn)
    app._bar_reset_order_btn.style().polish(app._bar_reset_order_btn)
    from well_viewer.barplot_renderer import redraw_bars
    redraw_bars(app)


def on_bar_drag_press(app, event) -> None:
    """Begin drag — record which bar was pressed."""
    if getattr(event, "button", None) != 1:
        return
    xdata = bar_event_xdata(app, event)
    if xdata is None:
        return
    keys = app._bar_current_keys()
    n = len(keys)
    if n < 2:
        return
    idx = bar_idx_at_x(xdata, n)
    app._bar_drag_state.update(active=True, src_idx=idx, cur_idx=idx)


def on_bar_drag_motion(app, event, *, accent_color: str) -> None:
    """Update drop-target indicator while dragging."""
    ds = app._bar_drag_state
    if not ds["active"]:
        return
    xdata = bar_event_xdata(app, event)
    if xdata is None:
        return
    keys = app._bar_current_keys()
    n = len(keys)
    if n < 2:
        return
    tgt = bar_idx_at_x(xdata, n)
    if tgt == ds["cur_idx"]:
        return
    ds["cur_idx"] = tgt

    for ax in (app._ax_bar_mean, app._ax_bar_frac, getattr(app, "_ax_bar_n", None)):
        if ax is None:
            continue
        for ln in list(ax.lines):
            if getattr(ln, "_bar_drag_guide", False):
                ln.remove()
        if tgt > ds["src_idx"]:
            guide_x = min(tgt + 0.5, n - 0.5)
        else:
            guide_x = max(tgt - 0.5, -0.5)
        ln = ax.axvline(guide_x, color=accent_color, lw=1.5, ls="--",
                        alpha=0.8, zorder=10)
        ln._bar_drag_guide = True
    app._bar_canvas.draw_idle()


def on_bar_drag_release(app, event) -> None:
    """Finalise drop — reorder and redraw."""
    ds = app._bar_drag_state
    if not ds["active"]:
        return
    ds["active"] = False

    for ax in (app._ax_bar_mean, app._ax_bar_frac, getattr(app, "_ax_bar_n", None)):
        if ax is None:
            continue
        for ln in list(ax.lines):
            if getattr(ln, "_bar_drag_guide", False):
                ln.remove()

    src = ds["src_idx"]
    tgt = ds["cur_idx"]
    if src == tgt:
        app._bar_canvas.draw_idle()
        return

    keys = app._bar_current_keys()
    if not (0 <= src < len(keys) and 0 <= tgt < len(keys)):
        app._bar_canvas.draw_idle()
        return

    item = keys.pop(src)
    keys.insert(tgt, item)
    app._app_state.plot.bar_order = keys
    app._bar_reset_order_btn.setProperty("variant", "toggle_accent")
    app._bar_reset_order_btn.style().unpolish(app._bar_reset_order_btn)
    app._bar_reset_order_btn.style().polish(app._bar_reset_order_btn)
    from well_viewer.barplot_renderer import redraw_bars
    redraw_bars(app)


def update_bar_tp_menu(app) -> None:
    """
    Populate the timepoint dropdown with the union of all timepoints
    present across all loaded wells.

    The available timepoints are always drawn from the full loaded dataset,
    not from the current selection — the selection controls what is plotted,
    but the dropdown must remain usable even when nothing is selected yet.
    """
    if not app._app_state.dataset.well_paths:
        _set_combo_values(app._bar_tp_cb, ["—"])
        app._bar_tp_cb.setCurrentText("—")
        return

    all_tps: set = set(app._app_state.dataset.all_timepoints_cache)

    # If no timepoints were found the schema had no timepoint field.
    # Use 0.0 as a single synthetic timepoint so bar plots remain usable.
    if not all_tps and app._app_state.dataset.well_paths:
        all_tps.add(0.0)

    sorted_tps = sorted(all_tps)
    tp_strs    = [f"{t:.4g}" for t in sorted_tps]

    cur = app._bar_tp_cb.currentText()
    _set_combo_values(app._bar_tp_cb, tp_strs)
    if cur in tp_strs:
        app._bar_tp_cb.setCurrentText(cur)
    elif tp_strs:
        app._bar_tp_cb.setCurrentText(tp_strs[0])
    else:
        app._bar_tp_cb.setCurrentText("—")



def collect_bar_items_for_app(app, target_t: float, *,
                       fc_state=None, miss_sink=None) -> tuple:
    """Return ``(use_groups, list[BarItem], band_lbl)`` for *target_t*.

    Thin wrapper around :func:`barplot_controller.collect_bar_items`.
    ``fc_state`` lets the on-tab CSV exporter request raw items (pass
    ``FC_STATE_OFF``) alongside the normalized items the renderer
    consumes. ``miss_sink`` collects timepoints where a vs-control
    denominator couldn't be resolved so the caller can surface a
    single status warning at the end of the redraw.
    """
    return _bar_collect_items(
        app, target_t, fc_state=fc_state, miss_sink=miss_sink,
    )



def toggle_swarm(app) -> None:
    """Toggle beeswarm / bar mode and update the button appearance."""
    app._bar_swarm = not app._bar_swarm
    on = app._bar_swarm
    app._swarm_btn.setChecked(on)
    if on and app._bar_violin:
        # Swarm and violin are mutually exclusive
        app._bar_violin = False
        app._violin_btn.setChecked(False)
        app._violin_slider.setEnabled(False)
    redraw_bars(app)



def toggle_violin(app) -> None:
    """Toggle violin / bar mode and update the button appearance."""
    app._bar_violin = not app._bar_violin
    on = app._bar_violin
    app._violin_btn.setChecked(on)
    app._violin_slider.setEnabled(on)
    if on and app._bar_swarm:
        # Mutually exclusive with beeswarm
        app._bar_swarm = False
        app._swarm_btn.setChecked(False)
    redraw_bars(app)



def rep_sets_active(app) -> "List[ReplicateSet]":
    """One ReplicateSet per visible (non-hidden) selection with ≥1 loaded
    well — these are exactly the traces drawn on the plots."""
    out: "List[ReplicateSet]" = []
    for s in app._app_state.selection.selections:
        if s.get("hidden"):
            continue
        wells = s.get("wells") or []
        if any(w in app._app_state.dataset.well_paths for w in wells):
            out.append(ReplicateSet(s.get("name") or "", list(wells)))
    return out



def bar_well_display_label(app, lbl: str) -> str:
    """Bar-plot-safe well label that avoids numeric-only tick text."""
    disp = str(app._well_display_label(lbl))
    if re.match(r"^\s*\d+\s*$", disp):
        return _extract_well_token(lbl) or disp
    return disp



def toggle_fov_replicates(app) -> None:
    app._app_state.plot.toggle_fov_replicates()
    app._invalidate_stats_cache()
    app._refresh_fov_btn_state()
    app._redraw()
    if app._current_centre_tab() == "Bar Plots":
        redraw_bars(app)

