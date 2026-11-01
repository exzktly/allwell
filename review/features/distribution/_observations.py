"""Distribution under **Observations: Nanowells**: how the band's nanowell metric varies over nanowells
(ARCHITECTURE §6.2l).

Each member (well or replicate set) contributes every nanowell value of the metric at the tab's timepoint — or
every timepoint when none is chosen — to its histogram, KDE or violin, drawn by the tab's own renderer. This is
the one view that describes variation over nanowells; nothing here compares conditions, so no nanowell is used as
a replicate. Rows come from the Nanowells feature through ``review/data/observations``.
"""

from __future__ import annotations

from widgets.plot_card import follows_theme

from domain.tp_match import tp_close

from review.domain.observation import NANOWELLS

LEAF = "Distribution"


def nanowell_groups(ctx, tp_filter) -> tuple[list, str, str] | None:
    """``(groups, axis label, footer)`` under Nanowells, ``None`` when the tab draws cells. *groups* are the
    renderer's ``(label, colour, values)``."""
    from review.core.observation_band import chosen_metric, effective_observations, nanowell_table_for
    from review.data.observations import observation_groups
    from review.domain.observation import footer_line

    if effective_observations(ctx, LEAF) != NANOWELLS:
        return None
    members = observation_groups(ctx)
    wells = [w for _l, ws, _c in members for w in ws]
    table = nanowell_table_for(ctx, wells)
    if table is None:
        return [], "", "Observations: nanowells · no nanowell tables for the selected wells"
    key = chosen_metric(ctx, table)
    frame = table.frame
    scope = frame["well"].isin(wells)
    if tp_filter is not None:
        hours = frame["timepoint_hours"].astype(float)
        scope &= hours.map(lambda h: h == h and tp_close(h, float(tp_filter)))
    values = table.values(key)
    groups = []
    for label, ws, color in members:
        mine = values[scope & frame["well"].isin(ws)].dropna().to_numpy(float)
        if mine.size:
            groups.append((label, color, mine))
    used = int(values[scope].notna().sum())
    metric = table.metric(key)
    sub = scope[scope].index
    excluded = {}
    if table.why_missing is not None:
        why = table.why_missing(key).loc[sub]
        undefined = values.loc[sub].isna()
        excluded = {str(k): int(v) for k, v in why[undefined].replace("", "no value").value_counts().items()}
    footer = footer_line(NANOWELLS, used, int(scope.sum()), excluded) + " · each point is one nanowell"
    return groups, (metric.label if metric else key), "\n".join([footer, *table.notes])


def label_and_footer(fig, label: str, footer: str) -> None:
    """Name the metric on every value axis and write the footer, after the tab's renderer has drawn."""
    from review.core.observation_band import FOOTER_GID
    from review.core.plot_style import tokens_for

    for text in [t for t in fig.texts if t.get_gid() == FOOTER_GID]:
        text.remove()
    for ax in fig.axes:
        if getattr(ax, "_aw_colorbar", False):
            continue
        if ax.get_xlabel():
            ax.set_xlabel(label)
    muted = tokens_for(fig.axes[0])[2] if fig.axes else None
    follows_theme(fig.text(0.01, 0.002, footer, ha="left", va="bottom", fontsize=7, color=muted, gid=FOOTER_GID), muted=True)

