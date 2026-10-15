"""Draw the Nanowells page's figures: the Distribution beeswarm and the site detail (tracker V3b, V3d, V3e, V3).

Pure matplotlib onto a figure it is handed; everything a reader can count comes from
:class:`distribution_compute.DistributionModel` and is only drawn here. The text the figure carries — the
per-swarm counts, the "+N more" overflow labels, the excluded totals and the footer — is drawn as text
artists, so a test can read the figure back rather than the model (CLAUDE.md §7.4 #1).

**Colours.** Dots are coloured by occupancy class at baseline with ``occupancy_ramp`` (decision U7), the
screen or publication shade by the figure's ground; class 0 is hollow. A nanowell with no count at baseline
is ``occupancy_unknown``. Selected nanowells are ringed in a fixed order: the app's selection colour, then
the well-rank colours.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from review.features.nanowell.beeswarm_compute import swarm_layout
from review.features.nanowell.distribution_compute import TIME, DistributionModel

#: A dot's diameter, in points.
DOT_PT = 3.4
#: How many panels sit side by side before wrapping.
MAX_COLUMNS = 4
#: Panels drawn at most; the footer names how many were left out.
MAX_PANELS = 12
_OCCUPANCY_INDEX = {"0": 0, "1": 1, "2": 2, "3+": 3}


@dataclass
class HitMap:
    """Where each drawn dot is, for turning a click into a nanowell: per axes, display-space points and sites."""
    entries: list = field(default_factory=list)   # (ax, x_data, y_data, sites)

    def nearest(self, ax, x_px: float, y_px: float, radius_px: float):
        """The site of the drawn dot nearest the display point on *ax*, within *radius_px*, or ``None``."""
        best, best_d = None, radius_px
        for entry_ax, xs, ys, sites in self.entries:
            if entry_ax is not ax or not len(xs):
                continue
            pts = entry_ax.transData.transform(np.column_stack([xs, ys]))
            d = np.hypot(pts[:, 0] - x_px, pts[:, 1] - y_px)
            k = int(np.argmin(d))
            if d[k] <= best_d:
                best, best_d = sites[k], float(d[k])
        return best


def _is_publication(ax) -> bool:
    from review.core.plot_style import tokens_for
    from ui.theme import CPub

    return str(tokens_for(ax)[0]).lower() == CPub.bg.lower()


def _inks(ax) -> tuple[str, str, str]:
    """``(ground, ink, faint ink)`` for *ax*'s figure, screen or publication, from the plot tokens."""
    from review.core.plot_style import tokens_for

    bg, _title, ink, grid, spine = tokens_for(ax)
    return bg, ink, spine


def _ramp(ax):
    from ui.theme import Colors, CPub

    return CPub.occupancy_ramp if _is_publication(ax) else Colors.occupancy_ramp


def selection_colours() -> list[str]:
    """The ring colour of the 1st…6th selected nanowell, in a fixed order (decision U7)."""
    from ui.theme import Colors, WELL_COLORS_TUPLE

    return [Colors.heatmap_selection] + list(WELL_COLORS_TUPLE[:5])


def _grid(n: int) -> tuple[int, int]:
    cols = min(max(n, 1), MAX_COLUMNS)
    return int(math.ceil(max(n, 1) / cols)), cols


def _dot_units(ax, fig) -> tuple[float, float]:
    """A dot's width and height in *ax*'s data units."""
    dpi = fig.dpi
    w_in, h_in = fig.get_size_inches()
    box = ax.get_position()
    d_px = DOT_PT * dpi / 72.0
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    return (d_px / (box.width * w_in * dpi) * (x1 - x0), d_px / (box.height * h_in * dpi) * (y1 - y0))


def _y_limits(model: DistributionModel, panel) -> tuple[float, float]:
    values = np.concatenate([s.values for s in panel.swarms] or [np.zeros(0)])
    stats = [v for st in panel.stats for v in (st.ci_low, st.ci_high, st.centre) if math.isfinite(v)]
    every = np.concatenate([values, np.asarray(stats, dtype=float)])
    if not len(every):
        return 0.0, 1.0
    lo, hi = float(np.min(every)), float(np.max(every))
    pad = 0.6 if model.integer else max((hi - lo) * 0.06, 1e-6)
    return lo - pad, hi + pad


def render_distribution(fig, model: DistributionModel, *, selected=()) -> HitMap:
    """Draw *model* on *fig* (cleared first) and return where every dot went."""
    fig.clear()
    hits = HitMap()
    panels = model.panels[:MAX_PANELS]
    rows, cols = _grid(len(panels))
    footer_lines = 3 + (1 if len(model.panels) > MAX_PANELS else 0)
    bottom = 0.10 + 0.035 * footer_lines
    fig.subplots_adjust(left=0.07, right=0.98, top=0.90, bottom=bottom, wspace=0.18, hspace=0.42)
    axes = [fig.add_subplot(rows, cols, i + 1) for i in range(len(panels))] if panels else []
    rings = {tuple(site): colour for site, colour in zip(map(tuple, selected), selection_colours())}
    probe = axes[0] if axes else fig.add_subplot(1, 1, 1)
    inks = _inks(probe)
    if not axes:
        probe.set_axis_off()
    for ax, panel in zip(axes, panels):
        _draw_panel(fig, ax, model, panel, hits, rings, inks)
    if axes:
        axes[0].set_ylabel(model.label, color=inks[1])
    _footer(fig, model, inks, len(panels))
    return hits


def _draw_panel(fig, ax, model, panel, hits, rings, inks) -> None:
    from ui.theme import Colors

    bg, ink, faint = inks
    xs_all = [t[0] for t in panel.ticks] or [0.0]
    ax.set_xlim(min(xs_all) - 0.6, max(xs_all) + 0.6)
    ax.set_ylim(*_y_limits(model, panel))
    ax.set_xticks([t[0] for t in panel.ticks])
    ax.set_xticklabels([t[1] for t in panel.ticks], fontsize=8)
    ax.set_title(panel.title, fontsize=9, loc="left", color=ink)
    ramp = _ramp(ax)
    dot_w, dot_h = _dot_units(ax, fig)
    # Each tick holds one sub-swarm per well of its group; a sub-swarm's half-width is its share of the slot.
    k_per_slot = max(len({s.well for s in panel.swarms}), 1) if model.axis == TIME else max(
        (len({s.well for s in panel.swarms if round(s.x) == round(t[0])}) for t in panel.ticks), default=1)
    half = (0.9 if model.axis == TIME else 0.7) / (2 * max(k_per_slot, 1))
    y_lo, _y_hi = ax.get_ylim()
    for swarm in panel.swarms:
        layout = swarm_layout(swarm.values, half_width=half, dot_w=dot_w, dot_h=dot_h, integer=model.integer)
        idx = layout.index
        x = layout.x + swarm.x
        occupancy = [swarm.occupancy[i] for i in idx]
        faces, edges = [], []
        for occ in occupancy:
            colour = ramp[_OCCUPANCY_INDEX[occ]] if occ in _OCCUPANCY_INDEX else Colors.occupancy_unknown
            edges.append(colour)
            faces.append("none" if occ == "0" else colour)
        ax.scatter(x, layout.y, s=(DOT_PT * 0.9) ** 2, facecolors=faces, edgecolors=edges, linewidths=0.6,
                   zorder=3)
        sites = [swarm.sites[i] for i in idx]
        hits.entries.append((ax, x, layout.y, sites))
        ringed = [(xi, yi, rings[tuple(s)]) for xi, yi, s in zip(x, layout.y, sites) if tuple(s) in rings]
        for xi, yi, colour in ringed:
            ax.scatter([xi], [yi], s=(DOT_PT * 2.6) ** 2, facecolors="none", edgecolors=colour, linewidths=1.6,
                       zorder=5)
        ax.text(swarm.x, 1.0, f"n = {len(swarm.values):,}", transform=ax.get_xaxis_transform(), ha="center",
                va="bottom", fontsize=6.5, color=faint, gid="nw-used")
        if swarm.n_excluded:
            ax.text(swarm.x, -0.13 if model.axis == TIME else -0.10, f"excl. {swarm.n_excluded:,}",
                    transform=ax.get_xaxis_transform(), ha="center", va="top", fontsize=6.5,
                    color=faint, gid="nw-excluded")
        if model.axis != TIME and sum(1 for s in panel.swarms if round(s.x) == round(swarm.x)) > 1:
            ax.text(swarm.x, -0.055, swarm.well, transform=ax.get_xaxis_transform(), ha="center", va="top",
                    fontsize=6.5, color=faint, gid="nw-subwell")
        for value, count in sorted(layout.overflow.items()):
            ax.text(swarm.x, value if model.integer else max(value, y_lo), f"+{count:,} more at {value:g}",
                    ha="center", va="center", fontsize=6, color=ink, zorder=6, gid="nw-hidden",
                    bbox={"boxstyle": "round,pad=0.15", "fc": bg, "ec": "none", "alpha": 0.85})
    for _well, points in panel.medians.items():
        pts = [(px, py) for px, py in points if math.isfinite(py)]
        if pts:
            ax.plot(*zip(*pts), color=ink, linewidth=1.2, marker="D",
                    markersize=3.5, zorder=4)
    for stat in panel.stats:
        if not math.isfinite(stat.centre):
            continue
        sx = stat.x + 0.44
        if math.isfinite(stat.ci_low):
            ax.plot([sx, sx], [stat.ci_low, stat.ci_high], color=ink, linewidth=1.4, zorder=4)
        ax.plot([sx], [stat.centre], marker="D", markersize=5, color=ink, zorder=4)
        if math.isfinite(stat.vs_control):
            ax.text(sx, stat.centre, f" ×{stat.vs_control:.2g}" if "log2" not in model.metric
                    else f" {stat.vs_control:+.2g}", fontsize=6.5, va="center", color=ink, gid="nw-vs-control")
    ax.grid(axis="y", alpha=0.3)


def _footer(fig, model: DistributionModel, inks, n_drawn: int) -> None:
    _bg, ink, faint = inks
    excluded = model.excluded()
    n_excluded = sum(excluded.values())
    line1 = (f"Nanowells used {model.n_used:,} of {model.n_roster:,} · excluded {n_excluded:,}"
             + (": " + " · ".join(f"{k} {v:,}" for k, v in excluded.items()) if excluded else ""))
    lines = [line1] + list(model.notes)
    if len(model.panels) > n_drawn:
        lines.append(f"{len(model.panels) - n_drawn} more panel(s) not drawn; select fewer wells to see them.")
    y = 0.012
    for i, text in enumerate(reversed(lines)):
        fig.text(0.07, y + i * 0.032, text, fontsize=7.5, color=ink if text is line1 else faint,
                 gid="nw-footer-used" if text is line1 else "nw-footer")


# ── site detail ──────────────────────────────────────────────────────────────
#: The site detail's rows: (column, label). Channel rows are added for the shared channel.
DETAIL_ROWS = (("n", "Cell count"), ("c", "Confluence"))


def render_site_detail(fig, series: list, *, channel: str | None = None, time_source: str | None = None) -> None:
    """One row per measurement, one line per selected nanowell, against recorded time (tracker V3).

    *series* is ``[(site, frame)]`` with ``frame`` from :func:`series_compute.site_series`. A timepoint
    without a value is a gap in the line, never a zero; a timepoint whose state carries no number is a
    hatched band naming the state.
    """
    fig.clear()
    rows = list(DETAIL_ROWS) + ([(f"brightness_{channel}", f"Brightness · {channel}"),
                                 (f"signal_{channel}", f"Site signal · {channel}")] if channel else [])
    if not series:
        return
    axes = fig.subplots(len(rows), 1, sharex=True)
    axes = np.atleast_1d(axes)
    fig.subplots_adjust(left=0.16, right=0.97, top=0.94 - 0.03 * len(series), bottom=0.12, hspace=0.25)
    _bg, ink, faint = _inks(axes[0])
    colours = selection_colours()
    use_time = all(frame["time_h"].notna().all() for _site, frame in series)
    for k, (site, frame) in enumerate(series):
        colour = colours[k % len(colours)]
        x = frame["time_h"].to_numpy(dtype=float) if use_time else frame["tp_order"].to_numpy(dtype=float)
        for ax, (column, _label) in zip(axes, rows):
            y = frame[column].to_numpy(dtype=float) if column in frame else np.full(len(frame), np.nan)
            ax.plot(x, y, color=colour, marker="o", markersize=3.5, linewidth=1.3, zorder=3)
            for xi, state in zip(x, frame["state"]):
                if state not in ("measured", "empty"):
                    ax.axvspan(xi - _band(x), xi + _band(x), facecolor="none", edgecolor=colour, hatch="///",
                               alpha=0.35, linewidth=0, zorder=1)
        states = ", ".join(f"{tp}: {_state_text(row)}" for tp, row in zip(frame["timepoint"], frame.to_dict("records")))
        fig.text(0.16, 0.975 - 0.03 * k, f"{site[0]} · q {site[1]}, r {site[2]} — {states}", fontsize=7,
                 color=colour, gid="nw-detail-states")
    for ax, (_column, label) in zip(axes, rows):
        ax.set_ylabel(label, fontsize=7.5, color=faint)
        ax.grid(alpha=0.3)
    axes[-1].set_xlabel("Hours since the first timepoint" + (" (from labels)" if time_source == "label" else "")
                        if use_time else "Timepoint (order; times unknown)", fontsize=8, color=ink)


def _band(x) -> float:
    finite = np.sort(np.asarray(x, dtype=float)[np.isfinite(x)])
    gaps = np.diff(finite)
    return float(np.min(gaps[gaps > 0]) * 0.3) if (gaps > 0).any() else 0.3


def _state_text(row: dict) -> str:
    flags = [name for name in ("fused", "partially_visible") if row.get(name) is True]
    state = str(row.get("state", ""))
    return state + (f" ({', '.join(f.replace('_', ' ') for f in flags)})" if flags else "")
