"""Draw the Nanowells page's figures: the Distribution beeswarm, the site detail, the Array view, Trajectories and
Position (tracker V2, V3b, V3d, V3e, V3, V4, V6, V7).

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

**The Array view** (:func:`render_array`) is one renderer for one well at one timepoint, every element of it a
layer that is on or off (``state.ArrayLayers``): the image under it, the nanowells, their addresses, the tiles,
the anchor and the grid record. What the page once drew as the Array map, Array QC and the Full montage window
are settings of it, so a nanowell is drawn one way whichever of them is on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from review.features.nanowell.array_compute import padded
from review.features.nanowell.beeswarm_compute import swarm_layout
from review.features.nanowell.distribution_compute import TIME, DistributionModel
from review.features.nanowell.full_montage_compute import fit_region

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
    if model.responding_above is not None and math.isfinite(model.responding_above):
        stats.append(model.responding_above)          # the cut stays in view, or the line would not be seen
    every = np.concatenate([values, np.asarray(stats, dtype=float)])
    if not len(every):
        return 0.0, 1.0
    lo, hi = float(np.min(every)), float(np.max(every))
    pad = 0.6 if model.integer else max((hi - lo) * 0.06, 1e-6)
    return lo - pad, hi + pad


#: Lines of footer text the figure reserves room for at the bottom; more push the panels up.
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
    if model.responding_above is not None and math.isfinite(model.responding_above):
        # The cut the fraction responding is counted above (tracker I9), in the muted ink behind the dots.
        _ink(ax.axhline(model.responding_above, color=muted, lw=0.8, ls="--", zorder=0.5, gid="nw-responding"),
             muted=True)
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
    if len(lines) > _FOOTER_LINES:
        # Every line here is owed to the reader — a panel not drawn, values not drawn as dots — so the footer
        # grows to hold them rather than cutting the last (roll-up review X11).
        fig.subplots_adjust(bottom=0.10 + _FOOTER_BASE + _LINE * len(lines))
    for i, text in enumerate(reversed(lines)):
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


def render_site_detail(fig, series: list, *, channel: str | None = None, time_source: str | None = None,
                       crops: dict | None = None) -> None:
    """One row per measurement, one line per selected nanowell, against recorded time (tracker V3).

    *series* is ``[(site, frame)]`` with ``frame`` from :func:`series_compute.site_series`. A timepoint
    without a value is a gap in the line, never a zero; a timepoint whose state carries no number is a
    hatched band naming the state. *crops* (``timepoint -> image_compute.Crop`` for the first nanowell) adds a
    row of its crops at every timepoint, each titled with its timepoint and where it was cut from (V3c); a
    timepoint without one says why in its place.
    """
    fig.clear()
    rows = list(DETAIL_ROWS) + ([(f"brightness_{channel}", f"Brightness · {channel}"),
                                 (f"signal_{channel}", f"Site signal · {channel}")] if channel else [])
    if not series:
        return
    crop_row = bool(crops)
    grid = fig.add_gridspec(len(rows) + (2 if crop_row else 0), 1, hspace=0.25)
    axes = [fig.add_subplot(grid[0, 0])]
    axes += [fig.add_subplot(grid[i, 0], sharex=axes[0]) for i in range(1, len(rows))]
    axes = np.array(axes)
    fig.subplots_adjust(left=0.16, right=0.97, top=0.94 - 0.03 * len(series), bottom=0.06, hspace=0.25)
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
    if crop_row:
        _crop_strip(fig, grid[len(rows):, 0], crops, series[0][0], ink, faint)


def _crop_strip(fig, cell, crops: dict, site, ink, faint) -> None:
    items = list(crops.items())
    notes: set = set()
    sub = cell.subgridspec(1, len(items), wspace=0.08)
    for k, (tp, crop) in enumerate(items):
        ax = fig.add_subplot(sub[0, k])
        ax.set_xticks([])
        ax.set_yticks([])
        if crop.image is not None:
            ax.imshow(crop.image, cmap="gray", interpolation="nearest")
            if crop.source == "montage":
                notes.add(crop.note)
        else:
            _ink(ax.text(0.5, 0.5, crop.note, ha="center", va="center", wrap=True, fontsize=5.5,
                         transform=ax.transAxes, color=faint, gid="nw-crop-missing"), muted=True)
        _ink(ax.set_title(tp + ("" if crop.source != "montage" else " (montage)"), fontsize=6.5, color=ink,
                          gid="nw-crop-title"))
    sources = {crop.source for _tp, crop in items if crop.image is not None}
    origin = ("from its owner tile at full resolution" if sources == {"tile"} else
              "from the montage, at its lower resolution" if sources == {"montage"} else
              "from its owner tile, or the montage where marked" if sources else "none could be cut")
    caption = f"Crops: {site[0]} · q {site[1]}, r {site[2]}, {origin}" + "".join(
        f"; {note}" for note in sorted(notes))
    _ink(fig.text(0.16, 0.012, caption, fontsize=6.5, color=faint, gid="nw-crop-caption", wrap=True), muted=True)


def render_crop_grid(fig, crops: dict | None, site) -> None:
    """The selected nanowell at every timepoint, large: one panel per timepoint, filling the page's figure.

    Asked for: a larger view of a selected nanowell over time than the site detail's strip of small crops. Every
    panel is drawn on **one** brightness scale — the 1st to 99th percentile of all the crops together — so a cell
    that appears, divides or fades reads as a change between panels, not as each crop stretched to its own range.
    *crops* is ``timepoint -> image_compute.Crop`` as the strip takes it, or ``None`` while they are cut; a
    timepoint without a crop says why in its panel, and the caption says where the crops were cut from."""
    fig.clear()
    _bg, ink, faint = _inks(fig.add_subplot(111))
    fig.clear()
    if not crops:
        _ink(fig.text(0.5, 0.5, "Cutting the crops…" if crops is None else "No crop could be cut.", ha="center",
                      va="center", color=faint, gid="nw-crop-grid-pending"), muted=True)
        return
    items = list(crops.items())
    lo, hi = _one_scale(c.image for _tp, c in items)
    cols = int(min(len(items), max(1, np.ceil(np.sqrt(len(items) * 1.6)))))
    rows = int(np.ceil(len(items) / cols))
    grid = fig.add_gridspec(rows, cols, left=0.02, right=0.98, top=0.93, bottom=0.07, wspace=0.05, hspace=0.18)
    notes: set = set()
    for k, (tp, crop) in enumerate(items):
        ax = fig.add_subplot(grid[k // cols, k % cols])
        # An image, pixel for pixel: the style pass must not put the card's typed axis range on it.
        ax._aw_image_panel = True
        ax.set_xticks([])
        ax.set_yticks([])
        if crop.image is not None:
            ax.imshow(crop.image, cmap="gray", interpolation="nearest", vmin=lo, vmax=hi, gid="nw-crop-grid-image")
            if crop.source == "montage":
                notes.add(crop.note)
        else:
            _ink(ax.text(0.5, 0.5, crop.note, ha="center", va="center", wrap=True, fontsize=8,
                         transform=ax.transAxes, color=faint, gid="nw-crop-missing"), muted=True)
        _ink(ax.set_title(tp + ("" if crop.source != "montage" else " (montage)"), fontsize=9, color=ink,
                          gid="nw-crop-title"))
    _ink(fig.text(0.02, 0.965, f"{site[0]} · q {site[1]}, r {site[2]} over time", fontsize=10, color=ink,
                  gid="nw-crop-grid-title"))
    sources = {crop.source for _tp, crop in items if crop.image is not None}
    origin = ("cut from its owner tile at full resolution" if sources == {"tile"} else
              "cut from the montage, at its lower resolution" if sources == {"montage"} else
              "cut from its owner tile, or the montage where marked" if sources else "none could be cut")
    caption = (f"{origin}; every panel on one brightness scale ({lo:.4g} to {hi:.4g})"
               + "".join(f"; {note}" for note in sorted(notes)))
    _ink(fig.text(0.02, 0.015, caption, fontsize=8, color=faint, gid="nw-crop-grid-caption", wrap=True), muted=True)


def _one_scale(images) -> tuple[float, float]:
    """``(low, high)``: the 1st and 99th percentiles of every finite pixel of *images* together (``None`` skipped),
    so a set of crops is drawn on one brightness scale rather than each stretched to its own range."""
    arrays = [np.asarray(im, dtype=float) for im in images if im is not None]
    finite = np.concatenate([a[np.isfinite(a)].ravel() for a in arrays]) if arrays else np.empty(0)
    lo, hi = (np.percentile(finite, [1, 99]) if finite.size else (0.0, 1.0))
    return float(lo), float(hi if hi > lo else lo + 1.0)


# ── the Extremes view ────────────────────────────────────────────────────────
def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def render_extremes(fig, model, crops: dict, *, label: str, note: str = "") -> dict:
    """The *N* nanowells with the largest fold change and the *N* with the smallest, each a row of its crops at
    every timepoint of the well; returns ``{axes: (well, q, r)}`` so a click on a row selects its nanowell.

    *model* is ``extremes_compute.ExtremesModel``; *crops* is ``(well, q, r) -> {timepoint: Crop}`` for the
    nanowells cut so far — a row not yet cut says so in each panel. Every crop is drawn on **one** brightness
    scale, as the enlarged crops are, so a nanowell that fills with cells reads brighter than one that empties.
    Each row is labelled with its rank, address and the fold change it was ranked by; each crop carries the fold
    change at its own timepoint, and the ranking timepoint's column is marked.
    """
    from review.features.nanowell.extremes_compute import caption, format_value

    fig.clear()
    _bg, ink, faint = _inks(fig.add_subplot(111))
    fig.clear()
    blocks = [(f"Largest {len(model.largest)}", model.largest), (f"Smallest {len(model.smallest)}", model.smallest)]
    blocks = [(title, rows) for title, rows in blocks if rows]
    if not blocks or not model.timepoints:
        said = f"No nanowell in {model.well} has a {label.lower()} at {model.timepoint}, so none can be ranked."
        if model.excluded:
            said += " " + _sentence(caption(model, label).split("; ", 1)[-1]) + "."
        render_message(fig, said, gid="nw-extremes-missing")
        return {}
    every = [crop.image for row in model.largest + model.smallest
             for crop in (crops.get(row.site) or {}).values()]
    lo, hi = _one_scale(every)
    heights = []
    for k, (_title, rows) in enumerate(blocks):
        heights += ([0.45] if k else []) + [1.0] * len(rows)          # a gap between the two blocks
    cols = len(model.timepoints)
    grid = fig.add_gridspec(len(heights), cols, height_ratios=heights, left=0.17, right=0.98, top=0.9,
                            bottom=0.1, wspace=0.06, hspace=0.32)
    hits: dict = {}
    notes: set = set()
    sources: set = set()
    pending = False
    line = 0
    for k, (title, rows) in enumerate(blocks):
        line += 1 if k else 0
        for i, row in enumerate(rows):
            site_crops = crops.get(row.site)
            for j, tp in enumerate(model.timepoints):
                ax = fig.add_subplot(grid[line, j])
                ax._aw_image_panel = True                         # pixel for pixel: no typed axis range on it
                ax.set_box_aspect(1)                              # a panel without a crop keeps the crops' shape
                ax.set_xticks([])
                ax.set_yticks([])
                hits[ax] = row.site
                # A read that failed whole stores its reason under "" (``controller._fetch``).
                crop = (site_crops or {}).get(tp) or (site_crops or {}).get("")
                if crop is not None and crop.image is not None:
                    ax.imshow(crop.image, cmap="gray", interpolation="nearest", vmin=lo, vmax=hi,
                              gid="nw-extremes-image")
                    sources.add(crop.source)
                    if crop.source == "montage":
                        notes.add(crop.note)
                else:
                    pending = pending or site_crops is None
                    said = "Cutting…" if site_crops is None else (crop.note if crop is not None else
                                                                  f"No row at {tp}.")
                    _ink(ax.text(0.5, 0.5, said, ha="center", va="center", wrap=True, fontsize=5.5,
                                 transform=ax.transAxes, color=faint, gid="nw-extremes-missing"), muted=True)
                if tp == model.timepoint:
                    for spine in ax.spines.values():
                        spine.set_linewidth(1.6)
                _ink(ax.set_xlabel(format_value(model.column, row.values.get(tp, math.nan)), fontsize=6.5,
                                   color=ink if tp == model.timepoint else faint, labelpad=1,
                                   gid="nw-extremes-value"), muted=tp != model.timepoint)
                if i == 0:
                    _ink(ax.set_title(tp + (" · ranked" if tp == model.timepoint else ""), fontsize=7.5,
                                      color=ink, gid="nw-extremes-tp"))
                if j == 0:
                    _ink(ax.set_ylabel(f"#{row.rank}  q {row.site[1]}, r {row.site[2]}\n"
                                       f"{format_value(model.column, row.value)}", rotation=0, ha="right",
                                       va="center", fontsize=7, color=ink, labelpad=6, gid="nw-extremes-row"))
                if i == 0 and j == 0:
                    top = ax.get_position().y1
                    _ink(fig.text(0.02, top + 0.035, f"{title} · {label} at {model.timepoint}", fontsize=8.5,
                                  color=ink, fontweight="bold", gid="nw-extremes-block"))
            line += 1
    _ink(fig.text(0.02, 0.965, f"{model.well} · the nanowells with the largest and smallest {label.lower()}",
                  fontsize=10, color=ink, gid="nw-extremes-title"))
    origin = ("cut from each owner tile at full resolution" if sources == {"tile"} else
              "cut from the montage, at its lower resolution" if sources == {"montage"} else
              "cut from each owner tile, or the montage where it had to be" if sources else
              "still being cut" if pending else "none could be cut")
    said = [caption(model, label) + ".",
            f"Crops {origin}; every crop on one brightness scale ({lo:.4g} to {hi:.4g})."
            + "".join(f" {_sentence(n_)}." for n_ in sorted(notes))]
    if note:
        said.insert(0, note)
    _ink(fig.text(0.02, 0.012, " ".join(said), fontsize=7, color=faint, gid="nw-extremes-caption", wrap=True),
         muted=True)
    return hits


def _band(x) -> float:
    finite = np.sort(np.asarray(x, dtype=float)[np.isfinite(x)])
    gaps = np.diff(finite)
    return float(np.min(gaps[gaps > 0]) * 0.3) if (gaps > 0).any() else 0.3


def _state_text(row: dict) -> str:
    flags = [name for name in ("fused", "partially_visible") if row.get(name) is True]
    state = str(row.get("state", ""))
    return state + (f" ({', '.join(f.replace('_', ' ') for f in flags)})" if flags else "")


# ── the Array view (V2, V7) ──────────────────────────────────────────────────
#: The colour map a metric is drawn in. Never a grey one: grey is how a nanowell without a value is drawn.
MAP_CMAP = "viridis"
#: How a nanowell without a value is outlined, by its state (fill colouring by the metric).
_STATE_STYLE = {"occluded": ("////", "solid"), "clipped": ("", "dashed"), "absent": ("", "dotted"),
                "not_located": ("", "dotted"), "unaddressed": ("", "dotted")}
#: How each tile was placed (``pipeline/tile_register.py``), as the ``Colors`` token it is drawn in: on the lattice
#: phase, by overlap correlation, or where the acquisition record put it when neither could be measured.
_SOURCE_TOKENS = {"lattice": "success", "overlap": "accent", "recorded": "warn"}
#: Labels written at most: with more than this many large enough in view, the figure asks for a zoom instead.
MAX_ADDRESSES = 600
#: The anchor's crosshair arm, in figure pixels.
ANCHOR_ARM_PX = 14
#: A counted cell's dot on the Array view, in points squared (matplotlib's ``scatter`` size): about 4 px across.
CELL_DOT_PT2 = 9.0
_RECORD_LINE = 0.032


@dataclass
class ArrayDrawn:
    """What :func:`render_array` put on screen: where a click lands, and what a pan, a zoom or the next read needs."""
    hits: HitMap
    ax: object = None
    region: tuple | None = None      # (left, top, right, bottom) the axes show, in the scene's frame
    size_px: tuple = (0, 0)          # the axes' size in figure pixels
    frame: str = "well"
    rendered: object = None          # full_montage_compute.Rendered, when the image is the raw tiles
    fit: tuple | None = None         # the region *Fit* shows, at the axes' shape: what the zoom readout is against
    sites: object = None             # the scene's nanowells (``ArraySites``), drawn or not: what a double-click finds


def _image_extent(image) -> tuple[float, float, float, float]:
    """``(left, top, right, bottom)`` of an image layer: a run montage, or a tile store's whole well."""
    if hasattr(image, "render"):
        return image.source.extent_um()
    left, right, bottom, top = image.extent()
    return left, top, right, bottom


def _union(a, b):
    if a is None or b is None:
        return a or b
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def array_footer(scene, layers, *, label: str, image_note: str = "", note: str = "") -> list[str]:
    """The lines under the Array view: what is drawn, how to read it, and what could not be drawn and why."""
    sites, in_well = scene.sites, scene.frame == "well"
    lines = [note] if note else []
    if scene.reason:
        lines.append(f"No addresses at this timepoint: {scene.reason}")
    if not len(sites):
        lines.append(f"{scene.well} at {scene.timepoint}: {scene.no_places}, so no nanowell is drawn.")
    counts = " · ".join(f"{k.replace('_', ' ')} {v:,}" for k, v in sorted(scene.counts.items(), key=lambda kv: -kv[1]))
    head = f"{scene.roster:,} nanowells in the roster"
    if layers.colour == "metric":
        head += f" · with a value {int(np.isfinite(sites.values).sum()):,}"
    head += (f" · {counts}" if counts else "")
    if scene.unplaced:
        head += f" · {scene.unplaced:,} without a measured position, not drawn"
    lines.append(head)
    lines.append(f"Lattice positions at the measured pitch {scene.pitch_um:.1f} µm, so a nanowell keeps its place from "
                 "one timepoint to the next (the square is the central nanowell)." if not in_well and scene.pitch_um
                 else "Measured positions in the well frame (the square is the central nanowell).")
    if layers.nanowells and len(sites):
        lines.append(f"Filled by {label}; grey outlines have no value: hatched occluded, dashed clipped, dotted not "
                     "seen. Dashed in the fused colour: on a tile seam, measured from a blended crop."
                     if layers.colour == "metric" else
                     "Outlined by state: blue measured, green empty, amber occluded, red clipped, grey not seen; dashed "
                     "in the fused colour: on a tile seam, measured from a blended crop.")
    if layers.cells and scene.cells_note:
        lines.append(scene.cells_note)
    if in_well and layers.tiles and scene.tiles:
        lines.append("Tiles: green placed on the lattice, blue by overlap correlation, amber where the acquisition "
                     "record put them; each labelled with its registration residual.")
    if in_well and layers.image != "off" and image_note:
        lines.append(image_note)
    if in_well and layers.anchor and scene.anchor_text:
        lines += scene.anchor_text.split("\n")
    if not in_well and (layers.image != "off" or layers.tiles or layers.anchor):
        lines.append("The image, the tiles and the anchor are in the well frame, not on the lattice: switch Positions "
                     "to Measured to draw them.")
    return lines


def render_array(fig, scene, layers, *, label: str, image=None, image_note: str = "", selected=(),
                 region=None, note: str = "") -> ArrayDrawn:
    """The Array view: one well at one timepoint, drawn with the *layers* that are on (``state.ArrayLayers``).

    *image* is what is drawn under the nanowells in the well frame: a run montage (``image_compute.Montage``) or a
    tile store (``full_montage_compute.TileStore``), rendered for exactly the region and pixels the axes show.
    *region* ``(left, top, right, bottom)`` is where the view looks, widened to the axes' shape; without one the
    view fits everything drawn. The nanowells are coloured as ``ui/nanowell_sites.py`` colours them wherever one is
    drawn, so a state reads the same here as in Analyze's Test detection.
    """
    fig.clear()
    in_well = scene.frame == "well"
    image = image if in_well and layers.image != "off" else None
    lines = array_footer(scene, layers, label=label, image_note=image_note, note=note)
    right = 0.62 if layers.record else 0.86
    drawn = _array_axes(fig, scene, image, region, right=right,
                        bottom=_FOOTER_BASE + (len(lines) + (1 if layers.addresses or layers.values else 0) + 0.5)
                        * _LINE)
    ax = drawn.ax
    drawn.sites = scene.sites
    _bg, ink, muted = _inks(ax)
    if image is not None:
        _draw_image(ax, drawn, image)
    if in_well and layers.tiles:
        _draw_tiles(ax, scene.tiles, muted)
    if layers.nanowells and len(scene.sites):
        _draw_nanowells(fig, ax, drawn, scene, layers, label=label, selected=selected)
    if layers.cells and scene.cells is not None and len(scene.cells[0]):
        _draw_cells(ax, *scene.cells)
    if (layers.addresses or layers.values) and len(scene.sites):
        lines += _draw_labels(fig, ax, drawn, scene.sites, addresses=layers.addresses, values=layers.values)
    if in_well and layers.anchor and scene.anchor is not None:
        _draw_anchor(ax, drawn, scene.anchor)
    _ink(ax.set_title(f"{scene.well} · {scene.timepoint}", fontsize=9, loc="left", color=ink))
    for i, text in enumerate(lines):
        y = _FOOTER_BASE + (len(lines) - 1 - i) * _LINE
        _ink(fig.text(0.03, y, text, fontsize=7.5, color=ink if i == 0 else muted, gid="nw-array-footer"),
             muted=i != 0)
    if layers.record:
        _draw_record(fig, scene.record, right + 0.08, ink, muted)
    return drawn


def _array_axes(fig, scene, image, region, *, right: float, bottom: float) -> ArrayDrawn:
    """The axes, looking at *region* (or at everything drawn) widened to their shape, so pixels stay square."""
    ax = fig.add_axes([0.03, bottom, right - 0.03, 0.92 - bottom])
    ax.set_axis_off()
    extent = scene.extent()
    if image is not None:
        extent = _union(extent, _image_extent(image))
    extent = extent or (-1.0, -1.0, 1.0, 1.0)
    whole = padded(extent, scene.pitch_um or 0.02 * max(extent[2] - extent[0], extent[3] - extent[1], 1.0))
    box = ax.get_window_extent()
    w_px, h_px = max(int(round(box.width)), 1), max(int(round(box.height)), 1)
    fit = fit_region(whole, w_px, h_px, margin=0.0)
    x0, y0, x1, y1 = fit if region is None else fit_region(region, w_px, h_px, margin=0.0)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)                                                 # y down
    return ArrayDrawn(HitMap(), ax, (x0, y0, x1, y1), (w_px, h_px), scene.frame, fit=fit)


def _draw_image(ax, drawn, image) -> None:
    """The run montage at its own place, or the raw tiles rendered for exactly the region and pixels shown."""
    x0, y0, x1, y1 = drawn.region
    if hasattr(image, "render"):
        drawn.rendered = image.render(drawn.region, *drawn.size_px)
        ax.imshow(drawn.rendered.image, cmap="gray", vmin=0, vmax=255, extent=(x0, x1, y1, y0),
                  interpolation="nearest", zorder=0)
    else:
        ax.imshow(image.image, cmap="gray", vmin=0, vmax=255, extent=image.extent(), interpolation="nearest",
                  zorder=0)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)


def _draw_tiles(ax, tiles, muted) -> None:
    """Each tile's placement coloured by how the run placed it, labelled with its residual."""
    from matplotlib.patches import Rectangle

    from ui.theme import Colors

    unsized = 0
    for tile in tiles:
        colour = getattr(Colors, _SOURCE_TOKENS.get(tile.source, "danger"))
        if tile.width_um and tile.height_um:
            ax.add_patch(Rectangle((tile.left_um, tile.top_um), tile.width_um, tile.height_um, fill=False,
                                   edgecolor=colour, linewidth=1.2, zorder=2, gid="nw-array-tile-box"))
            at = (tile.left_um + 0.02 * tile.width_um, tile.top_um + 0.06 * tile.height_um)
        else:
            # The acquisition record gives no tile size: mark the corner the grid record placed, never a guessed
            # outline.
            unsized += 1
            ax.plot([tile.left_um], [tile.top_um], marker="+", markersize=9, color=colour, zorder=2)
            at = (tile.left_um, tile.top_um)
        residual = f" · {tile.residual_um:.1f} µm" if tile.residual_um is not None else ""
        _ink(ax.text(*at, f"{tile.tile} · {tile.source}{residual}", fontsize=6.5, color=colour,
                     gid="nw-array-tile", clip_on=True))
    if unsized:
        _ink(ax.text(0.01, 0.01, f"{unsized} tile(s) without a size in the acquisition record: their placed "
                     "corner is marked (+), not an outline.", transform=ax.transAxes, fontsize=6.5,
                     color=muted, gid="nw-array-unsized"), muted=True)


def _ellipses(ax, sites, mask, **kw):
    """The nanowells of *mask* as one collection at their radii."""
    from matplotlib.collections import EllipseCollection

    width = 2 * sites.radius[mask]
    coll = EllipseCollection(width, width, np.zeros(int(mask.sum())), units="xy",
                             offsets=np.column_stack([sites.x[mask], sites.y[mask]]), offset_transform=ax.transData,
                             **kw)
    ax.add_collection(coll)
    return coll


def _draw_nanowells(fig, ax, drawn, scene, layers, *, label: str, selected) -> None:
    """The nanowells filled by the metric or outlined by state, a fused one dashed, the central one boxed, the
    selection ringed; and where a click finds them."""
    from matplotlib.patches import Rectangle

    from ui.nanowell_sites import FUSED_COLOUR, outline_width_px

    sites = scene.sites
    # One width for the view, from the nanowells' median drawn diameter (they are one molded size), in points.
    x0, _y0, x1, _y1 = drawn.region
    diameter_px = 2 * float(np.median(sites.radius)) * drawn.size_px[0] / max(x1 - x0, 1e-9)
    outline = outline_width_px(diameter_px) * 72.0 / fig.dpi
    fused = np.asarray(sites.fused, dtype=bool)
    if layers.colour == "metric":
        _fill_by_metric(fig, ax, sites, fused, outline, label)
    else:
        _outline_by_state(ax, sites, fused, outline)
    if fused.any():                                      # dashed in the fused colour whatever else it is
        _ellipses(ax, sites, fused, facecolors="none", edgecolors=FUSED_COLOUR, linewidths=outline,
                  zorder=4).set_linestyle("dashed")
    _bg, ink, _muted = _inks(ax)
    centre = scene.centre()
    if centre is not None:
        side = sites.radius[centre] * 1.6
        ax.add_patch(Rectangle((sites.x[centre] - side / 2, sites.y[centre] - side / 2), side, side, fill=False,
                               edgecolor=ink, linewidth=1.0, zorder=5))
    addressed = [k for k, s in enumerate(sites.sites) if s[1] is not None and s[2] is not None]
    drawn.hits.entries.append((ax, sites.x[addressed], sites.y[addressed], [sites.sites[k] for k in addressed]))
    for site, colour in zip(map(tuple, selected), selection_colours()):
        if site in sites.sites:
            k = sites.sites.index(site)
            ax.add_patch(_ring(sites.x[k], sites.y[k], sites.radius[k] * 1.35, colour))


def _fill_by_metric(fig, ax, sites, fused, outline: float, label: str) -> None:
    """Filled by the metric with a colour bar; one without a value a grey outline styled by its state."""
    from ui.theme import Colors

    _bg, ink, muted = _inks(ax)
    has = np.isfinite(sites.values)
    if has.any():
        coll = _ellipses(ax, sites, has, cmap=MAP_CMAP, edgecolors="none", zorder=3)
        coll.set_array(sites.values[has])
        place = ax.get_position()
        cax = fig.add_axes([place.x1 + 0.012, place.y0 + 0.1 * place.height, 0.012, 0.8 * place.height])
        bar = fig.colorbar(coll, cax=cax)
        _ink(bar.ax.set_ylabel(label, fontsize=8, color=ink))
        bar.ax.tick_params(labelsize=7, colors=muted)
    states = np.array(sites.state, dtype=object)
    groups = [(~has & np.isin(states, ["measured", "empty"]), "", "solid")] + [
        (~has & (states == s), hatch, style) for s, (hatch, style) in _STATE_STYLE.items()]
    for mask, hatch, style in groups:
        mask = mask & ~fused
        if mask.any():
            coll = _ellipses(ax, sites, mask, facecolors="none", edgecolors=Colors.plot_undefined_ink,
                             linewidths=outline, zorder=3)
            coll.set_linestyle(style)
            if hatch:
                coll.set_hatch(hatch)


def _outline_by_state(ax, sites, fused, outline: float) -> None:
    from ui.nanowell_sites import state_colour

    plain = ~fused
    if plain.any():
        _ellipses(ax, sites, plain, facecolors="none", linewidths=outline, zorder=3,
                  edgecolors=[state_colour(s) for s, p in zip(sites.state, plain) if p])


def _draw_cells(ax, x, y) -> None:
    """A dot at each counted cell's centre, a fixed size on screen whatever the zoom, over the nanowells and under
    their addresses."""
    from ui.nanowell_sites import CELL_COLOUR, CELL_EDGE_COLOUR

    ax.scatter(x, y, s=CELL_DOT_PT2, c=CELL_COLOUR, edgecolors=CELL_EDGE_COLOUR, linewidths=0.4, zorder=5.5,
               gid="nw-array-cells")


def value_text(value) -> str:
    """A nanowell's metric value as its label writes it: three significant figures; ``""`` for no value, which is
    not a zero and is never written as one."""
    return f"{value:.3g}" if value is not None and np.isfinite(value) else ""


def site_label_texts(sites, *, addresses: bool, values: bool) -> list[str]:
    """What each nanowell's label says: its ``q,r`` (the *Addresses* layer) over its value of the page's metric
    (the *Values* layer), whichever of the two it has; ``""`` where it has neither."""
    out = []
    for address, value in zip(sites.labels, sites.values):
        parts = ([address] if addresses and address else []) + ([value_text(value)] if values else [])
        out.append("\n".join(p for p in parts if p))
    return out


def _draw_labels(fig, ax, drawn, sites, *, addresses: bool, values: bool) -> list[str]:
    """Each nanowell's label (its address, its value, or both) in view inside a nanowell drawn large enough to
    hold it; a footer line when none is, or when too many are to write."""
    from ui.nanowell_sites import LABEL_COLOUR, MIN_LABEL_DIAMETER_PX

    x0, y0, x1, y1 = drawn.region
    diameter = 2 * sites.radius * drawn.size_px[0] / max(x1 - x0, 1e-9)
    labels = np.array(site_label_texts(sites, addresses=addresses, values=values), dtype=object)
    show = ((labels != "") & (diameter >= MIN_LABEL_DIAMETER_PX) & (sites.x >= x0) & (sites.x <= x1)
            & (sites.y >= y0) & (sites.y <= y1))
    what = "addresses and values" if addresses and values else "addresses" if addresses else "values"
    if show.sum() > MAX_ADDRESSES:
        return [f"{int(show.sum()):,} nanowell {what} in view: zoom in to read them."]
    if not (labels != "").any():
        # Nothing to write at any size: zooming in cannot help, so the footer must not say it would.
        return [f"No nanowell here has {what} to write."]
    if not show.any():
        return [f"Nanowell {what} are written in a nanowell drawn at least {MIN_LABEL_DIAMETER_PX} px across: zoom "
                "in to read them."]
    for k in np.nonzero(show)[0]:
        lines = labels[k].count("\n") + 1
        # Two lines share the height one line had, so each is a little smaller.
        size = diameter[k] * (0.22 if lines == 1 else 0.17) * 72.0 / fig.dpi
        ax.text(sites.x[k], sites.y[k], labels[k], ha="center", va="center", clip_on=True, zorder=6,
                fontsize=max(5.0, min(8.0, size)), color=LABEL_COLOUR, linespacing=1.0, gid="nw-array-address")
    return []


def _draw_anchor(ax, drawn, anchor) -> None:
    from ui.theme import Colors

    x, y = anchor
    arm = ANCHOR_ARM_PX * (drawn.region[2] - drawn.region[0]) / drawn.size_px[0]
    for xs, ys in (([x - arm, x + arm], [y, y]), ([x, x], [y - arm, y + arm])):
        ax.plot(xs, ys, color=Colors.accent2, linewidth=2, zorder=7, gid="nw-array-anchor")


def _draw_record(fig, record, left: float, ink, muted) -> None:
    """The grid record beside the axes; a line that warns keeps the warning colour, the rest follow the theme."""
    from ui.theme import Colors

    for i, (text, kind) in enumerate(record):
        item = fig.text(left, 0.92 - i * _RECORD_LINE, text, fontsize=7, wrap=True, gid="nw-array-record",
                        color=Colors.warn if kind == "flag" else ink if kind == "head" else muted)
        if kind != "flag":
            _ink(item, muted=kind != "head")


def render_message(fig, text: str, *, gid: str) -> None:
    """A figure that says why there is nothing to draw, rather than an empty canvas."""
    fig.clear()
    ax = fig.add_subplot(1, 1, 1)
    ax.set_axis_off()
    _bg, _ink_colour, muted = _inks(ax)
    _ink(ax.text(0.5, 0.5, text, ha="center", va="center", wrap=True, transform=ax.transAxes, fontsize=9,
                 color=muted, gid=gid), muted=True)


def _ring(x, y, radius, colour):
    from matplotlib.patches import Circle

    return Circle((x, y), radius, fill=False, edgecolor=colour, linewidth=1.8, zorder=6)


# ── trajectories (V4) ────────────────────────────────────────────────────────
def render_trajectories(fig, panels, *, label: str, time_source: str | None = None, note: str = "",
                        cohort: bool = False) -> None:
    """One panel per well: a thin line per nanowell, a thick median per occupancy class at baseline."""
    from matplotlib.collections import LineCollection

    from widgets.plot_card import theme_paired_colours

    fig.clear()
    shown = panels[:MAX_PANELS]
    rows, cols = _grid(len(shown))
    fig.subplots_adjust(left=0.07, right=0.98, top=0.88, bottom=0.10 + _FOOTER_BASE + 2 * _LINE, wspace=0.2,
                        hspace=0.45)
    axes = [fig.add_subplot(rows, cols, i + 1) for i in range(len(shown))]
    inks = _inks(axes[0] if axes else fig.add_subplot(1, 1, 1))
    _bg, ink, muted = inks
    drawn = without = 0
    for ax, panel in zip(axes, shown):
        segments, occupancy = [], []
        for _site, occ, y in panel.lines:
            segments.append(np.column_stack([panel.times, y]))
            occupancy.append(occ)
        if segments:
            screen, publication = _occupancy_colours(occupancy)
            edges = (publication if _is_publication(ax) else screen)[1]
            coll = LineCollection(segments, colors=edges, linewidths=0.5, alpha=0.45)
            ax.add_collection(coll)
            theme_paired_colours(coll, screen=("none", screen[1]), publication=("none", publication[1]))
            ax.autoscale_view()
        for cls, median in panel.class_medians.items():
            # A paired collection, like the thin lines, so a capture repaints it: drawn as a plain line it kept its
            # screen shade on publication white, where the 3+ median nearly vanished (roll-up review X14).
            screen, publication = _occupancy_colours([cls])
            colour = (publication if _is_publication(ax) else screen)[1]
            thick = LineCollection([np.column_stack([panel.times, median])], colors=colour, linewidths=2.4,
                                   zorder=4, label=f"{cls} at baseline")
            ax.add_collection(thick)
            theme_paired_colours(thick, screen=("none", screen[1]), publication=("none", publication[1]))
        ax.set_xticks(panel.times)
        ax.set_xticklabels(panel.labels, fontsize=7, rotation=0)
        _ink(ax.set_title(panel.well, fontsize=9, loc="left", color=ink))
        ax.grid(alpha=0.3)
        drawn += len(panel.lines)
        without += panel.n_without_value
    if axes:
        _ink(axes[0].set_ylabel(label, color=ink))
    lines = [(f"Fixed cohort: {drawn:,} nanowells with a value at every timepoint drawn, one line each; {without:,} "
              "without one at some timepoint not drawn." if cohort else
              f"{drawn:,} nanowells drawn, one line each; {without:,} with no value at any timepoint not drawn. "
              "A gap in a line is a timepoint without a value."),
             "Thick lines: the median of each occupancy class at baseline, over the nanowells with a value at each "
             "timepoint." + (" Times read from the timepoint labels." if time_source == "label" else "")]
    if len(panels) > len(shown):
        lines.append(f"{len(panels) - len(shown)} more well(s) not drawn; select fewer wells.")
    if note:
        lines.insert(0, note)
    for i, text in enumerate(reversed(lines)):
        _ink(fig.text(0.07, _FOOTER_BASE + i * _LINE, text, fontsize=7.5, color=muted, gid="nw-traj-footer"),
             muted=True)


# ── position effects (V6) ────────────────────────────────────────────────────
def render_position(fig, series, *, label: str, timepoint: str, note: str = "") -> None:
    """Metric against ring, per well (median and interquartile band), with each ring's excluded share below."""
    from ui.theme import WELL_COLORS_TUPLE

    fig.clear()
    grid = fig.add_gridspec(2, 1, height_ratios=(3, 1), hspace=0.15)
    ax, share = fig.add_subplot(grid[0]), fig.add_subplot(grid[1])
    fig.subplots_adjust(left=0.09, right=0.97, top=0.90, bottom=0.10 + _FOOTER_BASE + _LINE)
    _bg, ink, muted = _inks(ax)
    width = 0.8 / max(len(series), 1)
    for k, s in enumerate(series):
        colour = WELL_COLORS_TUPLE[k % len(WELL_COLORS_TUPLE)]
        ax.fill_between(s.rings, s.q1, s.q3, color=colour, alpha=0.18, linewidth=0)
        ax.plot(s.rings, s.median, color=colour, marker="o", markersize=3, linewidth=1.4, label=s.well)
        share.bar(s.rings + (k - (len(series) - 1) / 2) * width, s.excluded_share, width=width, color=colour)
    ax.set_ylabel(label, color=ink)
    _ink(ax.set_title(f"By ring from the central nanowell · {timepoint}", fontsize=9, loc="left", color=ink))
    ax.tick_params(labelbottom=False)
    ax.grid(alpha=0.3)
    if series:
        ax.legend(fontsize=7, frameon=False)
    share.set_ylabel("excluded", fontsize=7.5, color=muted)
    share.set_ylim(0, 1)
    share.set_xlabel("Ring (lattice steps from the central nanowell)", color=ink)
    share.grid(alpha=0.3)
    lines = ["Median and interquartile range over the nanowells with a value in each ring; the bars are the share "
             "of each ring's nanowells without one. Descriptive: no test is made."]
    if note:
        lines.insert(0, note)
    for i, text in enumerate(reversed(lines)):
        _ink(fig.text(0.09, _FOOTER_BASE + i * _LINE, text, fontsize=7.5, color=muted, gid="nw-position-footer"),
             muted=True)
