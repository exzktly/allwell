"""Draw the Nanowells page's figures: the Distribution beeswarm and the site detail (tracker V3b, V3d, V3e, V3).

Pure matplotlib onto a figure it is handed; everything a reader can count comes from
:class:`distribution_compute.DistributionModel` and is only drawn here. The text the figure carries — the
per-swarm counts, the "+N more" overflow labels, the excluded totals and the footer — is drawn as text
artists, so a test can read the figure back rather than the model (CLAUDE.md §7.4 #1).

**Colours.** Dots are coloured by occupancy class at baseline with ``occupancy_ramp`` (decision U7), the
screen or publication shade by the figure's ground, and marked with
:func:`widgets.plot_card.theme_paired_colours` so a capture repaints them; class 0 is hollow. Texts and lines
are in the plot's ink and tagged :func:`widgets.plot_card.follows_theme`. A nanowell with no count at baseline
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
#: A slot narrower than this (points) writes its counts rotated and bare.
_ROOM_FOR_LABEL_PT = 34.0
#: Wells per set beyond which a set's wells are drawn as densities rather than dots (decision U2).
DENSITY_ABOVE = 4
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


def _inks(ax) -> tuple[str, str, str]:
    """``(ground, ink, secondary ink)`` for *ax*'s figure, screen or publication, from the plot tokens.

    Every text and line drawn with these is tagged :func:`follows_theme`, so a capture — which flips the card
    to publication without a redraw — repaints them.
    """
    from review.core.plot_style import tokens_for

    bg, fg, muted, _grid, _spine = tokens_for(ax)
    return bg, fg, muted


def _ink(artist, *, muted: bool = False):
    from widgets.plot_card import follows_theme

    return follows_theme(artist, muted=muted)


def _occupancy_colours(occupancy) -> tuple:
    """``(screen, publication)`` pairs of ``(faces, edges)`` for dots of these occupancy classes (U7)."""
    from ui.theme import Colors, CPub

    out = []
    for ramp in (Colors.occupancy_ramp, CPub.occupancy_ramp):
        edges = [ramp[_OCCUPANCY_INDEX[o]] if o in _OCCUPANCY_INDEX else Colors.occupancy_unknown for o in occupancy]
        faces = ["none" if o == "0" else e for o, e in zip(occupancy, edges)]
        out.append((faces, edges))
    return tuple(out)


def _is_publication(ax) -> bool:
    from ui.theme import CPub

    return str(_inks(ax)[0]).lower() == CPub.bg.lower()


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


#: Lines of footer text the figure reserves room for at the bottom.
_FOOTER_LINES = 5
#: Where the footer starts, above the Group-by notice ``plot_style`` writes at the figure's foot.
_FOOTER_BASE = 0.035
_LINE = 0.03


def render_distribution(fig, model: DistributionModel, *, selected=()) -> HitMap:
    """Draw *model* on *fig* (cleared first) and return where every dot went."""
    fig.clear()
    hits = HitMap()
    panels = model.panels[:MAX_PANELS]
    rows, cols = _grid(len(panels))
    bottom = 0.10 + _FOOTER_BASE + _LINE * _FOOTER_LINES
    fig.subplots_adjust(left=0.07, right=0.98, top=0.88, bottom=bottom, wspace=0.18, hspace=0.42)
    axes = [fig.add_subplot(rows, cols, i + 1) for i in range(len(panels))] if panels else []
    rings = {tuple(site): colour for site, colour in zip(map(tuple, selected), selection_colours())}
    inks = _inks(axes[0]) if axes else _inks(fig.add_subplot(1, 1, 1))
    if not axes:
        fig.axes[0].set_axis_off()
    densities = hidden = 0
    for ax, panel in zip(axes, panels):
        d, u = _draw_panel(fig, ax, model, panel, hits, rings, inks)
        densities, hidden = densities + d, hidden + u
    if axes:
        _ink(axes[0].set_ylabel(model.label, color=inks[1]))
    _footer(fig, model, inks, panels, densities, hidden)
    return hits


def _members_per_tick(model, panel) -> int:
    if model.axis == TIME:
        return max(len({s.well for s in panel.swarms}), 1)
    return max((len({s.well for s in panel.swarms if round(s.x) == round(t[0])}) for t in panel.ticks), default=1)


def _draw_panel(fig, ax, model, panel, hits, rings, inks) -> tuple[int, int]:
    """Draw one panel; return how many of its sub-swarms were drawn as densities (U2), and how many dots with a
    value did not fit their slot (each swarm's are counted under it; the footer gives the total)."""
    _bg, ink, muted = inks
    xs_all = [t[0] for t in panel.ticks] or [0.0]
    ax.set_xlim(min(xs_all) - 0.6, max(xs_all) + 0.6)
    ax.set_ylim(*_y_limits(model, panel))
    ax.set_xticks([t[0] for t in panel.ticks])
    ax.set_xticklabels([t[1] for t in panel.ticks], fontsize=8)
    dot_w, dot_h = _dot_units(ax, fig)
    k = _members_per_tick(model, panel)
    half = (0.9 if model.axis == TIME else 0.7) / (2 * k)
    density = k > DENSITY_ABOVE
    # How wide one swarm's slot is on the page decides how its counts are written: level and labelled when
    # there is room, rotated and bare when there is not, so neighbouring counts never overprint.
    slot_pt = 2 * half / dot_w * DOT_PT
    crowded = slot_pt < _ROOM_FOR_LABEL_PT
    # The title sits above the counts, higher when they stand rotated.
    _ink(ax.set_title(panel.title, fontsize=9, loc="left", color=ink, pad=22 if crowded else 12))
    hidden = 0
    gutter = -0.13 if model.axis == TIME else -0.10
    for swarm in panel.swarms:
        if density:
            _draw_density(ax, swarm, half, ink)
        else:
            layout = _draw_swarm(ax, model, swarm, half, dot_w, dot_h, hits, rings, ink)
            if layout.n_hidden:
                # Under the swarm, in the gutter beside its exclusions: one line per swarm, so it can never
                # overprint a neighbour, and it says which values the hidden dots have.
                values = sorted(layout.overflow_range or layout.overflow)
                where = f"{values[0]:.3g}" if values[0] == values[-1] else f"{values[0]:.3g}–{values[-1]:.3g}"
                _ink(ax.text(swarm.x, gutter - (0.035 if swarm.n_excluded else 0.0),
                             f"+{layout.n_hidden:,}" if crowded else f"+{layout.n_hidden:,} at {where}",
                             transform=ax.get_xaxis_transform(), ha="center", va="top",
                             rotation=90 if crowded else 0, fontsize=5.5 if crowded else 6.5, color=ink,
                             gid="nw-hidden"))
                hidden += layout.n_hidden
        _ink(ax.text(swarm.x, 1.01, f"{len(swarm.values):,}" if crowded else f"n = {len(swarm.values):,}",
                     transform=ax.get_xaxis_transform(), ha="center", va="bottom", rotation=90 if crowded else 0,
                     fontsize=5.5 if crowded else 6.5, color=muted, gid="nw-used"), muted=True)
        if swarm.n_excluded:
            _ink(ax.text(swarm.x, gutter,
                         f"−{swarm.n_excluded:,}" if crowded else f"excl. {swarm.n_excluded:,}",
                         transform=ax.get_xaxis_transform(), ha="center", va="top", rotation=90 if crowded else 0,
                         fontsize=5.5 if crowded else 6.5, color=muted, gid="nw-excluded"), muted=True)
        if model.axis != TIME and sum(1 for s in panel.swarms if round(s.x) == round(swarm.x)) > 1:
            _ink(ax.text(swarm.x, -0.055, swarm.well, transform=ax.get_xaxis_transform(), ha="center", va="top",
                         fontsize=6.5, color=muted, gid="nw-subwell"), muted=True)
    for _well, points in panel.medians.items():
        pts = [(px, py) for px, py in points if math.isfinite(py)]
        if pts:
            for line in ax.plot(*zip(*pts), color=ink, linewidth=1.2, marker="D", markersize=3.5, zorder=4):
                _ink(line)
    for stat in panel.stats:
        if not math.isfinite(stat.centre):
            continue
        sx = stat.x + 0.44
        if math.isfinite(stat.ci_low):
            _ink(ax.plot([sx, sx], [stat.ci_low, stat.ci_high], color=ink, linewidth=1.4, zorder=4)[0])
        _ink(ax.plot([sx], [stat.centre], marker="D", markersize=5, color=ink, zorder=4)[0])
        if math.isfinite(stat.vs_control):
            text = (f" {stat.vs_control:+.3g}" if "log2" in model.metric else f" ×{stat.vs_control:.3g}")
            _ink(ax.text(sx, stat.centre, text, fontsize=6.5, va="center", color=ink, gid="nw-vs-control"))
    ax.grid(axis="y", alpha=0.3)
    return (len(panel.swarms) if density else 0), hidden


def _draw_swarm(ax, model, swarm, half, dot_w, dot_h, hits, rings, ink):
    """Draw one swarm's dots; return its :class:`SwarmLayout` (what was drawn, and what did not fit)."""
    from widgets.plot_card import theme_paired_colours

    layout = swarm_layout(swarm.values, half_width=half, dot_w=dot_w, dot_h=dot_h, integer=model.integer)
    idx = layout.index
    x = layout.x + swarm.x
    occupancy = [swarm.occupancy[i] for i in idx]
    screen, publication = _occupancy_colours(occupancy)
    faces, edges = publication if _is_publication(ax) else screen
    coll = ax.scatter(x, layout.y, s=(DOT_PT * 0.9) ** 2, facecolors=faces, edgecolors=edges, linewidths=0.6,
                      zorder=3)
    theme_paired_colours(coll, screen=screen, publication=publication)
    coll.set_gid("nw-dots")
    sites = [swarm.sites[i] for i in idx]
    hits.entries.append((ax, x, layout.y, sites))
    for xi, yi, s in zip(x, layout.y, sites):
        colour = rings.get(tuple(s))
        if colour is not None:
            ax.scatter([xi], [yi], s=(DOT_PT * 2.6) ** 2, facecolors="none", edgecolors=colour, linewidths=1.6,
                       zorder=5)
    return layout


def _draw_density(ax, swarm, half, ink) -> None:
    """A set of more than four wells: each well's nanowells as a density with its median, not dots (U2)."""
    if len(swarm.values) >= 2 and np.ptp(swarm.values) > 0:
        parts = ax.violinplot([swarm.values], positions=[swarm.x], widths=[2 * half], showextrema=False)
        for body in parts["bodies"]:
            body.set_facecolor(ink)
            body.set_alpha(0.35)
            body.set_edgecolor("none")
            body.set_gid("nw-density")
    if len(swarm.values):
        _ink(ax.plot([swarm.x - half, swarm.x + half], [np.median(swarm.values)] * 2, color=ink, linewidth=1.2,
                     zorder=4)[0])


def _footer(fig, model: DistributionModel, inks, panels, densities: int, hidden: int) -> None:
    """The sums the figure owes the reader, over the panels drawn; what was left out is stated beside them."""
    from widgets.plot_card import theme_paired_colours

    _bg, ink, muted = inks
    drawn = [s for p in panels for s in p.swarms]
    used = sum(len(s.values) for s in drawn)
    roster = sum(s.n_roster for s in drawn)
    excluded: dict[str, int] = {}
    for s in drawn:
        for k, v in s.excluded.items():
            excluded[k] = excluded.get(k, 0) + v
    excluded = dict(sorted(excluded.items(), key=lambda kv: (-kv[1], kv[0])))
    wells = sorted({s.well for s in drawn if len(s.values)})
    unit = "Nanowell × timepoint values" if model.axis == TIME else "Nanowells"
    line1 = (f"{unit} used {used:,} of {roster:,} · excluded {sum(excluded.values()):,}"
             + (": " + " · ".join(f"{k} {v:,}" for k, v in excluded.items()) if excluded else "")
             + f" · wells {len(wells)}")
    lines = [line1] + list(model.notes)
    left_out = model.panels[len(panels):]
    if left_out:
        n = sum(s.n_roster for p in left_out for s in p.swarms)
        lines.append(f"{len(left_out)} more panel(s) not drawn ({n:,} values, not in the counts above); "
                     "select fewer wells to see them.")
    if hidden:
        lines.append(f"+N under a swarm: {hidden:,} values in all are used but not drawn as dots, for lack of "
                     "room; widen the window or select fewer wells to see them.")
    if densities:
        lines.append("Sets of more than four wells are drawn as densities with each well's median; select fewer "
                     "wells to see and click single nanowells.")
    for i, text in enumerate(reversed(lines[:_FOOTER_LINES])):
        _ink(fig.text(0.07, _FOOTER_BASE + i * _LINE, text, fontsize=7.5, color=ink if text is line1 else muted,
                      gid="nw-footer-used" if text is line1 else "nw-footer"), muted=text is not line1)
    # The occupancy key (U5): the colours decode only with it, and its title names the baseline in use.
    key = fig.add_axes([0.62, 0.955, 0.36, _LINE * 0.9])        # top right, above the panels' titles
    key.set_axis_off()
    key.set_xlim(0, 10)
    key.set_ylim(0, 1)
    classes = ["0", "1", "2", "3+", None]
    screen, publication = _occupancy_colours(classes)
    faces, edges = publication if _is_publication(key) else screen
    coll = key.scatter([4.2, 5.4, 6.6, 7.8, 9.0], [0.5] * 5, s=(DOT_PT * 1.1) ** 2, facecolors=faces,
                       edgecolors=edges, linewidths=0.8)
    theme_paired_colours(coll, screen=screen, publication=publication)
    title = ("Cells at baseline (" + ("first measurable" if model.baseline == "first_measurable" else "earliest")
             + "):")
    _ink(key.text(4.0, 0.5, title, ha="right", va="center", fontsize=7, color=muted, gid="nw-legend"), muted=True)
    for xk, label in zip([4.2, 5.4, 6.6, 7.8, 9.0], ["0", "1", "2", "3+", "?"]):
        _ink(key.text(xk + 0.2, 0.5, label, ha="left", va="center", fontsize=7, color=muted, gid="nw-legend"),
             muted=True)


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
                 color=colour, gid="nw-detail-states")          # a selection colour: data ink, not tagged
    for ax, (_column, label) in zip(axes, rows):
        _ink(ax.set_ylabel(label, fontsize=7.5, color=faint), muted=True)
        ax.grid(alpha=0.3)
    axes[-1].set_xlabel("Hours since the first timepoint" + (" (from labels)" if time_source == "label" else "")
                        if use_time else "Timepoint (order; times unknown)", fontsize=8, color=ink)
    _ink(axes[-1].xaxis.label)


def _band(x) -> float:
    finite = np.sort(np.asarray(x, dtype=float)[np.isfinite(x)])
    gaps = np.diff(finite)
    return float(np.min(gaps[gaps > 0]) * 0.3) if (gaps > 0).any() else 0.3


def _state_text(row: dict) -> str:
    flags = [name for name in ("fused", "partially_visible") if row.get(name) is True]
    state = str(row.get("state", ""))
    return state + (f" ({', '.join(f.replace('_', ' ') for f in flags)})" if flags else "")
