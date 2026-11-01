"""Line Graphs under **Observations: Nanowells** (NANOWELL_OBSERVATIONS_TRACKER #14).

Four panels, as for cells: the band's nanowell metric over time — each member's mean over its wells of each well's
median over its nanowells, with a ``± SEM over wells`` band when it has two or more wells; the occupied share (the
same, of each well's share of nanowells holding a cell); how many nanowells had a value at each timepoint; and the
cumulative distribution of the metric over every selected nanowell (variation over nanowells is described, never
used as replicates). Per-nanowell trajectories stay on the Nanowells page, where a nanowell can be picked.
"""

from __future__ import annotations

from widgets.plot_card import follows_theme

import numpy as np

from review.domain.observation import NANOWELLS

LEAF = "Line Graphs"


def draw(ctx, ax_mean, ax_frac, ax_n, ax_cdf, legend_kw: dict) -> bool:
    """Draw the panels under Nanowells; ``False`` (nothing drawn) when the leaf draws cells."""
    from review.core.observation_band import (
        FOLD_CHANGE_NOT_APPLIED, FOOTER_GID, chosen_metric, effective_observations, nanowell_table_for,
    )
    from review.core.plot_style import tokens_for
    from review.data.observations import observation_groups
    from review.domain import fold_change as _fc
    from review.domain.observation import footer_line, group_points, well_medians

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
    medians = well_medians(table, key, wells)
    occupied = well_medians(table, "occupied", wells, how="mean")
    for (lbl, ws, color) in groups:
        for ax, frame in ((ax_mean, medians), (ax_frac, occupied)):
            pts = [p for p in group_points(frame, [(lbl, ws)])]
            if not pts:
                continue
            t = np.array([p.timepoint_hours for p in pts])
            m = np.array([p.mean for p in pts])
            e = np.array([p.sem for p in pts])
            ax.plot(t, m, marker="o", color=color, label=lbl)
            if np.isfinite(e).any():
                ax.fill_between(t, m - np.nan_to_num(e), m + np.nan_to_num(e), color=color, alpha=0.15, lw=0)
        mine = medians[medians["well"].isin(ws)].groupby("timepoint_hours")["n"].sum()
        if len(mine):
            ax_n.plot(mine.index.to_numpy(float), mine.to_numpy(float), marker="o", color=color, label=lbl)
        values = table.values(key)[table.frame["well"].isin(ws)].dropna().sort_values().to_numpy(float)
        if len(values):
            ax_cdf.step(values, np.arange(1, len(values) + 1) / len(values), where="post", color=color, label=lbl)
    ax_mean.set_title(f"{label} — well median over nanowells, mean ± SEM over wells", color=title_fg)
    ax_mean.set_ylabel(label)
    ax_frac.set_title("Occupied share (nanowells holding ≥ 1 cell)", color=title_fg)
    ax_frac.set_ylabel("Share of nanowells")
    ax_frac.set_ylim(-0.05, 1.05)
    ax_n.set_title("Nanowells with a value (N)", color=title_fg)
    ax_n.set_ylabel("N nanowells")
    ax_cdf.set_title(f"{label} CDF over nanowells (all timepoints)", color=title_fg)
    ax_cdf.set_xlabel(label)
    for ax in (ax_mean, ax_frac, ax_n, ax_cdf):
        if ax.get_legend_handles_labels()[0]:
            ax.legend(**legend_kw)
    lines = [footer_line(NANOWELLS, int(medians["n"].sum()) if len(medians) else 0,
                         int(medians["total"].sum()) if len(medians) else 0, table.exclusions(key)),
             *table.notes]
    if _fc.fold_change_state(ctx).active:
        lines.append(FOLD_CHANGE_NOT_APPLIED)
    follows_theme(fig.text(0.01, 0.002, "\n".join(lines), ha="left", va="bottom", fontsize=7, color=muted, gid=FOOTER_GID), muted=True)
    return True
