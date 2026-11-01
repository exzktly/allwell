"""Bar Plots under **Observations: Nanowells** (ARCHITECTURE §6.2l).

Three panels, as for cells, each saying what it is:

* the band's nanowell metric — a bar per member (well or replicate set) and selected timepoint at the mean over
  its wells of each well's **median over its nanowells**, ``± SEM over wells``, with each well's median as a dot;
* the **occupied share** — the same, of each well's share of nanowells holding a cell (a different number from
  any "fraction above a threshold", and titled so);
* how many nanowells had a value — the sample behind each bar, summed over its wells.

Wells are the replicates: a bar's error is over wells and is absent for a single well. The rows are the Nanowells
feature's, asked for through ``review/data/observations``; nothing here imports it.
"""

from __future__ import annotations

from widgets.plot_card import follows_theme

from review.domain.observation import NANOWELLS

LEAF = "Bar Plots"


def draw(ctx, ax_mean, ax_frac, ax_n, target_ts, tp_label: str) -> bool:
    """Draw the three panels under Nanowells; ``False`` (nothing drawn) when the leaf draws cells."""
    from review.core.observation_band import (
        FOLD_CHANGE_NOT_APPLIED, FOOTER_GID, chosen_metric, effective_observations, nanowell_table_for,
    )
    from review.core.plot_style import tokens_for
    from review.data.observations import observation_groups
    from review.domain import fold_change as _fc
    from review.domain.observation import footer_line, group_points, scoped_counts, well_medians, wells_without_value

    if effective_observations(ctx, LEAF) != NANOWELLS:
        return False
    groups = observation_groups(ctx)
    wells = [w for _l, ws, _c in groups for w in ws]
    table = nanowell_table_for(ctx, wells)
    fig = ax_mean.figure
    for text in [t for t in fig.texts if t.get_gid() == FOOTER_GID]:
        text.remove()
    _bg, title_fg, muted, _grid, _spine = tokens_for(ax_mean)
    if table is None:
        follows_theme(ax_mean.text(0.5, 0.5, "No nanowell tables for the selected wells.", transform=ax_mean.transAxes,
                     ha="center", va="center", color=muted), muted=True)
        return True
    key = chosen_metric(ctx, table)
    label = table.metric(key).label if table.metric(key) else key
    medians = well_medians(table, key, wells, target_ts)
    occupied = well_medians(table, "occupied", wells, target_ts)
    pairs = [(lbl, ws) for lbl, ws, _c in groups]
    colors = {lbl: c for lbl, _ws, c in groups}
    # One slot per member and timepoint, fixed before anything is drawn, so each panel's bar for a member sits under
    # that member's label even when another panel has no value for it (an empty control has a share, no brightness).
    hours = sorted({float(h) for h in medians["timepoint_hours"].dropna()}) if len(medians) else []
    slots = [(lbl, h) for lbl, _ws in pairs for h in hours]
    at = {slot: i for i, slot in enumerate(slots)}
    for ax, points in ((ax_mean, group_points(medians, pairs)), (ax_frac, group_points(occupied, pairs))):
        for point in points:
            i = at[(point.label, point.timepoint_hours)]
            ax.bar(i, point.mean, color=colors.get(point.label), alpha=0.85)
            if point.n_wells >= 2:
                ax.errorbar(i, point.mean, yerr=point.sem, color=title_fg, capsize=3, lw=1)
            if ax is ax_mean:
                ax.scatter([i] * point.n_wells, point.well_values, s=14, color=title_fg, zorder=3, linewidths=0)
    if ax_n is not None:
        for (lbl, h), i in at.items():
            ws = dict(pairs)[lbl]
            here = medians[medians["well"].isin(ws) & (medians["timepoint_hours"] == h)]
            ax_n.bar(i, float(here["n"].sum()), color=colors.get(lbl), alpha=0.85)
    ticks = [lbl if len(hours) == 1 else f"{lbl}\nt={h:g}h" for lbl, h in slots]
    for ax in (ax_mean, ax_frac, ax_n):
        if ax is not None:
            ax.set_xticks(range(len(ticks)))
            ax.set_xticklabels(ticks, fontsize=8)
    ax_mean.set_title(f"{label} — well median over nanowells, mean ± SEM over wells — {tp_label}",
                      color=title_fg, fontsize=9, fontweight="bold")
    ax_mean.set_ylabel(label)
    ax_frac.set_title(f"Occupied share (nanowells holding ≥ 1 cell) — {tp_label}", color=title_fg, fontsize=9,
                      fontweight="bold")
    ax_frac.set_ylabel("Share of nanowells")
    ax_frac.set_ylim(-0.05, 1.05)
    if ax_n is not None:
        ax_n.set_title(f"Nanowells with a value (N) — {tp_label}", color=title_fg, fontsize=9, fontweight="bold")
        ax_n.set_ylabel("N nanowells")
    used, total, excluded = scoped_counts(table, key, wells, target_ts)
    lines = [footer_line(NANOWELLS, used, total, excluded), *table.notes]
    missing = wells_without_value(medians)
    if missing:
        lines.append("No value, so not in their member's mean or spread: " + ", ".join(missing))
    if _fc.fold_change_state(ctx).active:
        lines.append(FOLD_CHANGE_NOT_APPLIED)
    follows_theme(fig.text(0.01, 0.002, "\n".join(lines), ha="left", va="bottom", fontsize=7, color=muted,
                           gid=FOOTER_GID), muted=True)
    return True
