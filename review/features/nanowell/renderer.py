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
from review.features.nanowell.extremes_compute import caption, format_value
from review.features.nanowell.starting_cells_compute import occupancy_in_filter
from review.features.nanowell.full_montage_compute import fit_region
from review.features.nanowell._identity_marks import mark_timepoints, split_segments
from review.features.nanowell.identity_compute import marks_legend
from review.features.nanowell.series_compute import baseline_text

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


def _mark_colours(muted: str) -> dict:
    """The ink of each identity mark's band and word: the theme's warning and danger, and the secondary ink."""
    from ui.theme import Colors

    return {"inconclusive": Colors.warn, "partial": Colors.warn, "contradicted": Colors.danger, "not_checked": muted}


def _inks(ax) -> tuple[str, str, str]:
    """``(ground, ink, secondary ink)`` for *ax*'s figure, screen or publication, from the plot tokens.

    Every text and line drawn with these is tagged :func:`follows_theme`, so a capture — which flips the card
    to publication without a redraw — repaints them.
    """
    from review.core.plot_style import tokens_for

    bg, fg, muted, _grid, _spine = tokens_for(ax)
    return bg, fg, muted


def _theme_axes(ax) -> None:
    """Give *ax* the card's own ground, spines and tick ink, as ``PlotCard.setPlotTheme`` gives the axes it finds.

    An axes added after the card was themed is matplotlib's white otherwise, and on the dark screen card the light
    occupancy shades and the light legend text drawn for that ground vanished into it."""
    from widgets.plot_card import apply_axes_style

    apply_axes_style(ax, "publication" if _is_publication(ax) else "screen")


def _y_locator(integer: bool):
    """Ticks for a y axis: whole numbers only for a count, where 2.5 cells is no value a reader can have."""
    from matplotlib.ticker import MaxNLocator

    return MaxNLocator(nbins=5, integer=integer, min_n_ticks=2)


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
    """``(rows, columns)`` for *n* panels: as few rows as ``MAX_COLUMNS`` allows, the panels spread evenly over them
    (six wells are two rows of three, not a row of four over a row of two beside two empty cells)."""
    n = max(n, 1)
    rows = int(math.ceil(n / MAX_COLUMNS))
    return rows, int(math.ceil(n / rows))


#: The share of the Properties font sizes this page's figures are drawn at (``plot_style`` reads it from each axes
#: as ``_aw_type_scale``). Its defaults, 18 pt, are sized for one plot filling its card; a panel of a grid of wells
#: drew its ticks larger than its own title and ran neighbouring tick labels into one another. At the defaults a
#: grid's ticks and labels come out at 9 pt and a single plot's at 11 pt, and raising a size in Properties still
#: raises it here.
GRID_TYPE_SCALE = 0.5
SINGLE_TYPE_SCALE = 0.62
#: The Properties default tick size the scales above are measured against, for drawing before the style pass runs.
_PREF_PT = 18.0
#: The page's own annotation sizes — counts, keys, footers — in points: below the axes' type, above legibility.
ANNOTATION_PT = 7.0
CROWDED_PT = 6.0
FOOTER_PT = 8.0


def _prepare_axes(ax, scale: float) -> None:
    """Theme *ax* and give it the page's type scale, before anything is drawn on it."""
    _theme_axes(ax)
    ax._aw_type_scale = scale
    ax.tick_params(labelsize=_PREF_PT * scale)
    ax.yaxis.label.set_fontsize(_PREF_PT * scale)
    ax.xaxis.label.set_fontsize(_PREF_PT * scale)


def _no_offset(ax) -> None:
    """Plain tick numbers on y: matplotlib's offset ("1e−6+1" over the axis) overprinted the counts above a panel and
    hid the values a reader came for."""
    from matplotlib.ticker import ScalarFormatter

    formatter = ScalarFormatter(useOffset=False)
    formatter.set_scientific(False)
    ax.yaxis.set_major_formatter(formatter)


def _below(ax, points: float):
    """A transform for text by *ax*'s x axis: x in data, y in axes fractions moved *points* down (up when negative),
    so text keeps its distance from the axis whatever size the panel is drawn at."""
    from matplotlib.transforms import offset_copy

    return offset_copy(ax.get_xaxis_transform(), fig=ax.figure, x=0, y=-points, units="points")


def _reference_line(ax, metric: str, muted) -> None:
    """The no-change line on a fold change: 1× on a ratio, 0 on a log₂ ratio."""
    if not metric.endswith("_fc"):
        return
    _ink(ax.axhline(0.0 if "log2" in metric else 1.0, color=muted, lw=0.8, ls=":", zorder=0.5, gid="nw-no-change"),
         muted=True)


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
    # Only a centre that carries a comparison is drawn (as text), so only that one widens the axis.
    stats = [st.centre for st in panel.stats if math.isfinite(st.centre) and math.isfinite(st.vs_control)]
    if model.responding_above is not None and math.isfinite(model.responding_above):
        stats.append(model.responding_above)          # the cut stays in view, or the line would not be seen
    every = np.concatenate([values, np.asarray(stats, dtype=float)])
    if not len(every):
        return 0.0, 1.0
    lo, hi = float(np.min(every)), float(np.max(every))
    # A near-constant metric (a fold change of 1 everywhere) still gets a readable range: padded by a share of its
    # magnitude, it is drawn against ticks of two decimals rather than seven, which pushed the axis label off the
    # figure.
    pad = 0.6 if model.integer else max((hi - lo) * 0.06, 0.05 * max(abs(lo), abs(hi)), 1e-6)
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
    fig.subplots_adjust(left=0.07, right=0.98, top=0.86, bottom=bottom, wspace=0.12 if rows * cols > 1 else 0.2,
                        hspace=0.62)
    axes = [fig.add_subplot(rows, cols, i + 1) for i in range(len(panels))] if panels else []
    rings = {tuple(site): colour for site, colour in zip(map(tuple, selected), selection_colours())}
    inks = _inks(axes[0]) if axes else _inks(fig.add_subplot(1, 1, 1))
    if not axes:
        fig.axes[0].set_axis_off()
    scale = GRID_TYPE_SCALE if len(panels) > 1 else SINGLE_TYPE_SCALE
    # One y scale for every panel, so wells read against each other and a tick label in the gap between two panels
    # belongs to the one on its right; the labels are written on the left column only.
    limits = [_y_limits(model, panel) for panel in panels]
    shared = (min(lo for lo, _hi in limits), max(hi for _lo, hi in limits)) if limits else None
    densities = hidden = 0
    for i, (ax, panel) in enumerate(zip(axes, panels)):
        _prepare_axes(ax, scale)
        d, u = _draw_panel(fig, ax, model, panel, hits, rings, inks, ylim=shared)
        densities, hidden = densities + d, hidden + u
        if i % cols:
            ax.tick_params(labelleft=False)
        else:
            _ink(ax.set_ylabel(_wrap_label(model.label, rows), color=inks[1]))
        if i + cols >= len(panels) and model.x_label:
            _ink(ax.set_xlabel(model.x_label, color=inks[1], labelpad=ax._aw_gutter_pt))
    _footer(fig, model, inks, panels, densities, hidden,
            crowded=any(getattr(ax, "_aw_crowded", False) for ax in axes))
    return hits


def _wrap_label(label: str, rows: int) -> str:
    """A y label short enough for its panel: on a grid of more than one row it is broken onto two lines."""
    import textwrap

    return label if rows < 2 or len(label) <= 26 else "\n".join(textwrap.wrap(label, 26)[:2])


def _members_per_tick(model, panel) -> int:
    if model.axis == TIME:
        return max(len({s.well for s in panel.swarms}), 1)
    return max((len({s.well for s in panel.swarms if round(s.x) == round(t[0])}) for t in panel.ticks), default=1)


def _draw_panel(fig, ax, model, panel, hits, rings, inks, *, ylim=None) -> tuple[int, int]:
    """Draw one panel; return how many of its sub-swarms were drawn as densities (U2), and how many dots with a
    value did not fit their slot (each swarm's are counted under it; the footer gives the total).

    The counts under a swarm are placed in points below the axis — under the tick labels, whatever size those are
    drawn at — and the depth they take is kept on the axes (``_aw_gutter_pt``) for the axis label beneath them."""
    _bg, ink, muted = inks
    xs_all = [t[0] for t in panel.ticks] or [0.0]
    ax.set_xlim(min(xs_all) - 0.6, max(xs_all) + 0.6)
    ax.set_ylim(*(ylim or _y_limits(model, panel)))
    ax.yaxis.set_major_locator(_y_locator(model.integer))
    _no_offset(ax)
    _reference_line(ax, model.metric, muted)
    if model.responding_above is not None and math.isfinite(model.responding_above):
        # The cut the fraction responding is counted above (tracker I9), in the muted ink behind the dots.
        _ink(ax.axhline(model.responding_above, color=muted, lw=0.8, ls="--", zorder=0.5, gid="nw-responding"),
             muted=True)
    ax.set_xticks([t[0] for t in panel.ticks])
    ax.set_xticklabels([t[1] for t in panel.ticks])
    dot_w, dot_h = _dot_units(ax, fig)
    k = _members_per_tick(model, panel)
    half = (0.9 if model.axis == TIME else 0.7) / (2 * k)
    density = k > DENSITY_ABOVE
    # How wide one swarm's slot is on the page decides how its counts are written: each swarm's used, excluded and
    # hidden counts when there is room; when there is not, only the bare number used above it, and the footer says
    # the rest are in its totals — written rotated, they ran into the next row's title and into the footer.
    slot_pt = 2 * half / dot_w * DOT_PT
    crowded = slot_pt < _ROOM_FOR_LABEL_PT
    size = CROWDED_PT if crowded else ANNOTATION_PT
    # The title sits above the counts, higher when they stand rotated.
    ax._aw_crowded = crowded
    _ink(ax.set_title(panel.title, fontsize=10, fontweight="bold", loc="left", color=ink, pad=14))
    subwells = model.axis != TIME and any(
        sum(1 for o in panel.swarms if round(o.x) == round(s.x)) > 1 for s in panel.swarms)
    tick_pt = ax.xaxis.get_tick_params(which="major").get("labelsize", _PREF_PT * GRID_TYPE_SCALE)
    tick_pt = float(tick_pt) if isinstance(tick_pt, (int, float)) else _PREF_PT * GRID_TYPE_SCALE
    if subwells:
        # The member wells' names sit directly under the axis and the group names below them.
        ax.tick_params(axis="x", pad=4 + ANNOTATION_PT + 2)
    gutter = 4 + (ANNOTATION_PT + 2 if subwells else 0) + 4 + tick_pt + 3     # under the tick labels
    line = (size + 2) if not crowded else 0
    hidden = 0
    lines_used = 0
    for swarm in panel.swarms:
        rows_here = 0
        if density:
            _draw_density(ax, swarm, half, ink)
        else:
            layout = _draw_swarm(ax, model, swarm, half, dot_w, dot_h, hits, rings, ink)
            hidden += layout.n_hidden
            if layout.n_hidden and not crowded:
                # Under the swarm, below its exclusions: one line per swarm, so it can never overprint a
                # neighbour, and it says which values the hidden dots have.
                values = sorted(layout.overflow_range or layout.overflow)
                where = f"{values[0]:.3g}" if values[0] == values[-1] else f"{values[0]:.3g}–{values[-1]:.3g}"
                _ink(ax.text(swarm.x, 0, f"+{layout.n_hidden:,} at {where}",
                             transform=_below(ax, gutter + (line if swarm.n_excluded else 0)), ha="center",
                             va="top", fontsize=size, color=ink, gid="nw-hidden"))
                rows_here += 1
        _ink(ax.text(swarm.x, 1.0, f"{len(swarm.values):,}" if crowded else f"n = {len(swarm.values):,}",
                     transform=_below(ax, -2), ha="center", va="bottom",       # 2 pt above the panel
                     fontsize=size, color=muted, gid="nw-used"), muted=True)
        if swarm.n_excluded and not crowded:
            _ink(ax.text(swarm.x, 0, f"excl. {swarm.n_excluded:,}", transform=_below(ax, gutter), ha="center",
                         va="top", fontsize=size, color=muted, gid="nw-excluded"), muted=True)
            rows_here += 1
        lines_used = max(lines_used, rows_here)
        if subwells and sum(1 for s in panel.swarms if round(s.x) == round(swarm.x)) > 1:
            _ink(ax.text(swarm.x, 0, swarm.well, transform=_below(ax, 4), ha="center", va="top",
                         fontsize=ANNOTATION_PT, color=muted, gid="nw-subwell"), muted=True)
    # The axis label goes under whatever the swarms wrote below the ticks.
    ax._aw_gutter_pt = lines_used * line + (ANNOTATION_PT + 4 if subwells else 0) + 4
    marks = model.identity_marks if model.axis == TIME else {}
    for swarm in panel.swarms:
        if (swarm.well, swarm.label) in marks:
            mark_timepoints(ax, [swarm.x], {swarm.x: marks[(swarm.well, swarm.label)]}, _mark_colours(muted),
                            width=2 * half, fontsize=size, ground=inks[0])
    _draw_centres(ax, model, panel, ink)
    ax.grid(axis="y", alpha=0.3)
    return (len(panel.swarms) if density else 0), hidden


def _draw_centres(ax, model, panel, ink) -> None:
    """Each well's median joined over time, and each group's comparison with the control.

    No summary marker or interval is drawn beside a swarm: the swarm is the figure. The comparison with the
    control (only when **vs** names a group) is written as text at the height of the group's centre."""
    marks = model.identity_marks if model.axis == TIME else {}
    for well, points in panel.medians.items():
        pts = [(px, py) for px, py in points if math.isfinite(py)]
        if not pts:
            continue
        # A segment into or out of a timepoint whose nanowell identity is not confirmed is dashed.
        unverified = {s.x for s in panel.swarms if s.well == well and (s.well, s.label) in marks}
        xs, ys = zip(*pts)
        for line_ in ax.plot(xs, ys, linestyle="none", color=ink, marker="D", markersize=3.5, zorder=4):
            _ink(line_)
        for pieces, style in zip(split_segments(xs, ys, unverified), ("-", "--")):
            for (x0, y0), (x1, y1) in pieces:
                for line_ in ax.plot([x0, x1], [y0, y1], color=ink, linewidth=1.2, linestyle=style, zorder=4):
                    _ink(line_)
    for stat in panel.stats:
        if not math.isfinite(stat.centre):
            continue
        sx = stat.x + 0.44
        if math.isfinite(stat.vs_control):
            text = (f" {stat.vs_control:+.3g}" if "log2" in model.metric else f" ×{stat.vs_control:.3g}")
            _ink(ax.text(sx, stat.centre, text, fontsize=ANNOTATION_PT, va="center", color=ink, gid="nw-vs-control"))


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


def _footer(fig, model: DistributionModel, inks, panels, densities: int, hidden: int, *,
            crowded: bool = False) -> None:
    """The sums the figure owes the reader, over the panels drawn; what was left out is stated beside them."""

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
    if crowded:
        lines.append("Too narrow to write each swarm's exclusions and undrawn dots under it; the totals above count "
                     "them.")
    if hidden and crowded:
        lines.append(f"{hidden:,} values in all are used but not drawn as dots, for lack of room.")
    elif hidden:
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
        _ink(fig.text(0.07, _FOOTER_BASE + i * _LINE, text, fontsize=FOOTER_PT, color=ink if text is line1 else muted,
                      gid="nw-footer-used" if text is line1 else "nw-footer"), muted=text is not line1)
    # The occupancy key (U5): the colours decode only with it, and its title names the baseline in use.
    _occupancy_key(fig, "Cells at baseline",
                   baseline=baseline_text(model.baseline),
                   starting=model.starting)


def _occupancy_key(fig, title: str, *, baseline: str = "", starting=(None, None)) -> None:
    """The occupancy colours' key at the figure's top right: every figure coloured by occupancy class carries one,
    or its colours decode to nothing. Its title reads ``title (baseline; faint: filtered out):``, each part in the
    brackets only when it applies; *starting* is the ``(minimum, maximum)`` count at the baseline the *Subset*
    holds every kept nanowell to (``Subset.start_bounds``), whose excluded classes are drawn faint."""
    from widgets.plot_card import theme_paired_colours

    key = fig.add_axes([0.6, 0.955, 0.38, _LINE * 0.9])        # top right, above the panels' titles
    key.set_axis_off()
    key.set_xlim(0, 10)
    key.set_ylim(0, 1)
    classes = ["0", "1", "2", "3+", None]
    # The classes the Subset removes whole are drawn faint and named so, or a key would decode
    # colours the figure cannot contain.
    lo, hi = starting
    kept = [occupancy_in_filter(cls, lo, hi) for cls in classes]
    screen, publication = _occupancy_colours(classes)
    faces, edges = publication if _is_publication(key) else screen
    coll = key.scatter([4.2, 5.4, 6.6, 7.8, 9.0], [0.5] * 5, s=(DOT_PT * 1.3) ** 2, facecolors=faces,
                       edgecolors=edges, linewidths=0.8)
    coll.set_alpha([1.0 if k else 0.2 for k in kept])
    theme_paired_colours(coll, screen=screen, publication=publication)
    muted = _inks(key)[2]
    said = [part for part in (baseline, "" if all(kept) else "faint: filtered out") if part]
    _ink(key.text(4.0, 0.5, title + (f" ({'; '.join(said)})" if said else "") + ":", ha="right",
                  va="center", fontsize=ANNOTATION_PT + 0.5, color=muted, gid="nw-legend"), muted=True)
    for xk, label, k in zip([4.2, 5.4, 6.6, 7.8, 9.0], ["0", "1", "2", "3+", "?"], kept):
        _ink(key.text(xk + 0.2, 0.5, label, ha="left", va="center", fontsize=ANNOTATION_PT + 0.5, color=muted,
                      gid="nw-legend", alpha=1.0 if k else 0.35), muted=True)


# ── site detail ──────────────────────────────────────────────────────────────
#: The site detail's rows: (column, label). Channel rows are added for the shared channel.
DETAIL_ROWS = (("n", "Cell count"), ("c", "Confluence"))


def render_site_detail(fig, series: list, *, channel: str | None = None, time_source: str | None = None,
                       outside: dict | None = None) -> None:
    """One row per measurement, one line per selected nanowell, against recorded time (tracker V3).

    *series* is ``[(site, frame)]`` with ``frame`` from :func:`series_compute.site_series`. A timepoint
    without a value is a gap in the line, never a zero; a timepoint whose state carries no number is a
    hatched band naming the state. The rows share one time axis, labelled once under the bottom row. The crops
    are drawn on a figure of their own under the **Pop out** button (:func:`render_crop_strip`). *outside*
    (``site -> sentence``) marks a selected nanowell that the *Subset* leaves out of the page's
    figures: the detail still draws it, and says so.
    """
    fig.clear()
    outside = outside or {}
    rows = list(DETAIL_ROWS) + ([(f"brightness_{channel}", "Brightness"), (f"signal_{channel}", "Nanowell fluorescence")]
                                if channel else [])
    if not series:
        return
    header = 0.035 * len(series)
    grid = fig.add_gridspec(len(rows), 1, left=0.24, right=0.96, top=0.96 - header, bottom=0.1, hspace=0.18)
    axes = [fig.add_subplot(grid[0, 0])]
    axes += [fig.add_subplot(grid[i, 0], sharex=axes[0]) for i in range(1, len(rows))]
    _bg, ink, faint = _inks(axes[0])
    for ax in axes:
        _theme_axes(ax)
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
        badge = f" · {outside[tuple(site)]}" if tuple(site) in outside else ""
        fig.text(0.03, 0.985 - 0.035 * k, f"{site[0]} · q {site[1]}, r {site[2]} — {_states_line(frame)}{badge}",
                 fontsize=7, color=colour, va="top", gid="nw-detail-states")     # a selection colour: data ink
    for ax, (column, label) in zip(axes, rows):
        _ink(ax.set_ylabel(label + (f"\n{channel}" if channel and column.endswith(f"_{channel}") else ""),
                           fontsize=7.5, color=faint), muted=True)
        ax.tick_params(labelsize=7)
        ax.yaxis.set_major_locator(_y_locator(column == "n"))
        if column == "n":
            lo, hi = ax.get_ylim()
            if hi - lo < 2:                       # a flat count still gets whole-number ticks around it
                mid = round((lo + hi) / 2)
                ax.set_ylim(mid - 1.5, mid + 1.5)
        ax.grid(alpha=0.3)
    for ax in axes[:-1]:
        ax.tick_params(labelbottom=False)
    axes[-1].set_xlabel("Hours since the first timepoint" + (" (from labels)" if time_source == "label" else "")
                        if use_time else "Timepoint (order; times unknown)", fontsize=8, color=ink)
    _ink(axes[-1].xaxis.label)
    fig.align_ylabels(axes)


def _draw_crop_cells(ax, crop, tally: dict, size: float | None = None) -> None:
    """The counted cells of *crop*'s own nanowell, as the Array view's Cells layer draws them: a light dot with a dark
    rim at each centre (the run keeps no outlines); a neighbour's cells in the crop's margin are not drawn. *tally* adds up what was drawn and which crops had no cell table, for
    the caption."""
    from ui.nanowell_sites import CELL_COLOUR, CELL_EDGE_COLOUR

    if crop is None or crop.image is None:
        return
    if crop.cells is None:
        tally["no_table"] = tally.get("no_table", 0) + 1
        return
    tally["drawn"] = tally.get("drawn", 0) + len(crop.cells)
    for key in ("gated", "in_fields"):
        if getattr(crop, key, 0):
            tally[key] = tally.get(key, 0) + getattr(crop, key)
    if len(crop.cells):
        ax.scatter(crop.cells[:, 0], crop.cells[:, 1], s=size or CELL_DOT_PT2, c=CELL_COLOUR,
                   edgecolors=CELL_EDGE_COLOUR, linewidths=0.4, zorder=5, gid="nw-crop-cells")
        h, w = crop.image.shape[:2]
        ax.set_xlim(-0.5, w - 0.5)                      # the dots must not widen the image's own extent
        ax.set_ylim(h - 0.5, -0.5)


def _cells_clause(tally: dict) -> str:
    """What the cell dots on a set of crops are, for its caption: how many were drawn, how many the page left out
    (Cell Gating's, and an excluded field's), and that a crop without a cell table has none drawn because none could
    be read — never that it holds no cells."""
    if not tally:
        return ""
    said = [f"dots: the {tally.get('drawn', 0):,} cells the run counted in this nanowell"] if "drawn" in tally else []
    if tally.get("gated"):
        said.append(f"{tally['gated']:,} more Cell Gating leaves out, not drawn")
    if tally.get("in_fields"):
        said.append(f"{tally['in_fields']:,} more in an excluded field, not drawn")
    if tally.get("no_table"):
        said.append(f"{tally['no_table']} crop(s) of a well without a cell table, so no cells drawn there")
    return "; ".join(said)


def _of_channel(image_channel: str) -> str:
    """What a set of crops is an image of, for its caption: the fluorescence channel, or the phase image."""
    return f"{image_channel} image" if image_channel else "phase image"


def render_crop_strip(fig, crops: dict | None, site, *, cells: bool = False, image_channel: str = "",
                      display_range=None) -> None:
    """The first selected nanowell's crops, one per timepoint, on the strip under the site detail's **Pop out**
    button: each titled with its timepoint, a timepoint without a crop saying why in its place, and where the crops
    were cut from said once in the caption. *crops* is ``None`` while they are being cut."""
    fig.clear()
    _bg, ink, faint = _inks(fig.add_subplot(111))
    fig.clear()
    if not crops:
        _ink(fig.text(0.5, 0.5, "Cutting the crops…" if crops is None else "No crop could be cut.", ha="center",
                      va="center", fontsize=7.5, color=faint, gid="nw-crop-pending"), muted=True)
        return
    items = list(crops.items())
    scale = crop_scale((c for _tp, c in items), display_range)
    notes: set = set()
    mixed = len({c.source for _tp, c in items if c.image is not None}) > 1
    sub = fig.add_gridspec(1, len(items), left=0.03, right=0.97, top=0.84, bottom=0.36, wspace=0.08)
    tally: dict = {}
    for k, (tp, crop) in enumerate(items):
        ax = fig.add_subplot(sub[0, k])
        ax._aw_image_panel = True
        ax.set_xticks([])
        ax.set_yticks([])
        if crop.image is not None:
            lo, hi = scale.of(crop)
            ax.imshow(crop.image, cmap="gray", interpolation="nearest", vmin=lo, vmax=hi, gid="nw-crop-image")
            if cells:
                _draw_crop_cells(ax, crop, tally, size=6.0)
            if crop.source == "montage":
                notes.add(crop.note)
        else:
            _ink(ax.text(0.5, 0.5, crop.note, ha="center", va="center", wrap=True, fontsize=5.5,
                         transform=ax.transAxes, color=faint, gid="nw-crop-missing"), muted=True)
        _ink(ax.set_title(tp + (" (montage)" if mixed and crop.source == "montage" else ""), fontsize=7, color=ink,
                          pad=2, gid="nw-crop-title"))
    sources = {crop.source for _tp, crop in items if crop.image is not None}
    origin = ("from its owner tile at full resolution" if sources == {"tile"} else
              "from the montage, at its lower resolution" if sources == {"montage"} else
              "from its owner tile, or the montage where marked" if sources else "none could be cut")
    caption = (f"Crops ({_of_channel(image_channel)}): {site[0]} · q {site[1]}, r {site[2]}, {origin}; "
               + scale.said(numbers=scale.chosen)) + "".join(
        f"; {note}" for note in sorted(notes)) + (f"; {_cells_clause(tally)}" if tally else "")
    _ink(fig.text(0.03, 0.03, caption, fontsize=6, color=faint, gid="nw-crop-caption", wrap=True), muted=True)


def render_crop_grid(fig, crops: dict | None, site, *, cells: bool = False, metrics: dict | None = None,
                     image_channel: str = "", display_range=None, labels: bool = True) -> None:
    """The selected nanowell at every timepoint, large: one panel per timepoint, filling the page's figure.

    Asked for: a larger view of a selected nanowell over time than the site detail's strip of small crops. Every
    panel is drawn on **one** brightness scale — the 1st to 99th percentile of all the crops together — so a cell
    that appears, divides or fades reads as a change between panels, not as each crop stretched to its own range.
    *crops* is ``timepoint -> image_compute.Crop`` as the strip takes it, or ``None`` while they are cut; a
    timepoint without a crop says why in its panel, and the caption says where the crops were cut from. A
    *display_range* chosen on the page replaces the percentile scale (:func:`crop_scale`). *labels* off leaves out
    each panel's timepoint and the caption; the title stays."""
    fig.clear()
    _bg, ink, faint = _inks(fig.add_subplot(111))
    fig.clear()
    if not crops:
        _ink(fig.text(0.5, 0.5, "Cutting the crops…" if crops is None else "No crop could be cut.", ha="center",
                      va="center", color=faint, gid="nw-crop-grid-pending"), muted=True)
        return
    items = list(crops.items())
    scale = crop_scale((c for _tp, c in items), display_range)
    cols = int(min(len(items), max(1, np.ceil(np.sqrt(len(items) * 1.6)))))
    rows = int(np.ceil(len(items) / cols))
    # *metrics* (``timepoint -> [items]``) are written under each panel, so the rows leave room for them.
    grid = fig.add_gridspec(rows, cols, left=0.02, right=0.98, top=0.87, bottom=0.15 if metrics else 0.08, wspace=0.05,
                            hspace=0.36 if metrics else 0.18)
    notes: set = set()
    mixed = len({c.source for _tp, c in items if c.image is not None}) > 1
    tally: dict = {}
    for k, (tp, crop) in enumerate(items):
        ax = fig.add_subplot(grid[k // cols, k % cols])
        # An image, pixel for pixel: the style pass must not put the card's typed axis range on it.
        ax._aw_image_panel = True
        ax.set_xticks([])
        ax.set_yticks([])
        if crop.image is not None:
            lo, hi = scale.of(crop)
            ax.imshow(crop.image, cmap="gray", interpolation="nearest", vmin=lo, vmax=hi, gid="nw-crop-grid-image")
            if cells:
                _draw_crop_cells(ax, crop, tally, size=CELL_DOT_PT2 * 2)
            if crop.source == "montage":
                notes.add(crop.note)
        else:
            _ink(ax.text(0.5, 0.5, crop.note, ha="center", va="center", wrap=True, fontsize=8,
                         transform=ax.transAxes, color=faint, gid="nw-crop-missing"), muted=True)
        if labels:
            _ink(ax.set_title(tp + (" (montage)" if mixed and crop.source == "montage" else ""), fontsize=9,
                              color=ink, gid="nw-crop-title"))
        said = (metrics or {}).get(tp, [])
        if said:
            width_pt = ax.get_position().width * fig.get_size_inches()[0] * 72
            _ink(ax.text(0.5, -0.03, "\n".join(_pack(said, width_pt, 8.0)), ha="center", va="top", fontsize=8,
                         transform=ax.transAxes, color=faint, gid="nw-crop-grid-metrics"), muted=True)
    _ink(fig.text(0.02, 0.955, f"{site[0]} · q {site[1]}, r {site[2]} over time · {_of_channel(image_channel)}",
                  fontsize=11, color=ink,
                  gid="nw-crop-grid-title"))
    sources = {crop.source for _tp, crop in items if crop.image is not None}
    origin = ("cut from its owner tile at full resolution" if sources == {"tile"} else
              "cut from the montage, at its lower resolution" if sources == {"montage"} else
              "cut from its owner tile, or the montage where marked" if sources else "none could be cut")
    caption = (f"{origin}; every panel on {scale.said()}"
               + "".join(f"; {note}" for note in sorted(notes)) + (f"; {_cells_clause(tally)}" if tally else ""))
    if labels:
        _ink(fig.text(0.02, 0.015, caption, fontsize=8, color=faint, gid="nw-crop-grid-caption", wrap=True),
             muted=True)


@dataclass(frozen=True)
class CropScale:
    """How a set of crops is put on screen: one brightness scale for all of them, so a change between crops reads as
    a change in the nanowell rather than each crop stretched to its own range.

    Without a chosen range the scale is the 1st to 99th percentile of every crop together (:func:`_one_scale`). With
    the page's **Display range** (``chosen``) it is that range, in the raw image's units — which the crops cut from an
    owner tile are in. A crop cut from the run's montage is an 8-bit picture whose numbers mean something else, so
    under a chosen range those crops keep a percentile scale of their own (``montage``), and :meth:`said` says so."""

    lo: float
    hi: float
    chosen: bool = False
    montage: tuple | None = None

    def of(self, crop) -> tuple[float, float]:
        """The ``(vmin, vmax)`` *crop* is drawn with."""
        if self.chosen and self.montage is not None and getattr(crop, "source", "") == "montage":
            return self.montage
        return self.lo, self.hi

    def said(self, numbers: bool = True) -> str:
        """The scale as a caption names it."""
        if not self.chosen:
            return f"one brightness scale ({self.lo:.4g} to {self.hi:.4g})" if numbers else "one brightness scale"
        out = f"the display range {self.lo:.4g} to {self.hi:.4g}"
        if self.montage is not None:
            out += (f" (the crops cut from the montage, an 8-bit picture, on a scale of their own: "
                    f"{self.montage[0]:.4g} to {self.montage[1]:.4g})")
        return out


def crop_scale(crops, display_range=None) -> CropScale:
    """The :class:`CropScale` for *crops* (``image_compute.Crop`` objects; one without an image is skipped):
    *display_range* ``(low, high)`` when the page has one, else one percentile scale over them all."""
    crops = [c for c in crops if c is not None and c.image is not None]
    if display_range is None:
        return CropScale(*_one_scale(c.image for c in crops))
    lo, hi = (float(v) for v in display_range)
    montage = [c.image for c in crops if c.source == "montage"]
    return CropScale(lo, hi, True, _one_scale(montage) if montage else None)


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


#: The Extremes montage's text sizes, in points: a row's label, the fold change under a crop, its other metrics.
_EXT_LABEL_PT, _EXT_VALUE_PT, _EXT_METRIC_PT = 8.0, 7.5, 6.5


def _one_line(items, width_pt: float, size_pt: float) -> tuple[str, int]:
    """As many of *items*, in order, as fit one line of *width_pt* at *size_pt* (the glyph width :func:`_pack`
    takes), and how many were left out."""
    kept = 0
    while kept < len(items) and len(" · ".join(items[:kept + 1])) * 0.55 * size_pt <= width_pt:
        kept += 1
    return " · ".join(items[:kept]), len(items) - kept


def _pack(items, width_pt: float, size_pt: float) -> list[str]:
    """*items* joined with `` · `` into as few lines as fit *width_pt* at *size_pt* (an average glyph is taken as
    0.55 of the size, which errs wide for the digits and lower case these are)."""
    lines: list[str] = []
    for item in items:
        if lines and (len(lines[-1]) + 3 + len(item)) * 0.55 * size_pt <= width_pt:
            lines[-1] += " · " + item
        else:
            lines.append(item)
    return lines


@dataclass
class _ExtremesLayout:
    """Where the Extremes montage goes, in inches: the crop's side and whether the two blocks sit side by side."""
    side: float
    beside: bool
    #: Whether the crops are large enough for their metrics, written inside each crop along its foot.
    metrics_shown: bool = True


#: The smallest crop side (inches) that holds its metrics legibly: below it, only the fold change is written on the
#: crop, and the caption says so.
_EXT_MIN_SIDE_WITH_METRICS = 0.6
#: Inches: the row labels' column, the space between crops, the room above the montage and the caption below it, a
#: block's title and timepoint headers, and the gap between two blocks side by side.
_EXT_LABELS_W, _EXT_GAP, _EXT_TOP, _EXT_BOTTOM, _EXT_HEADER, _EXT_BLOCK_GAP = 0.8, 0.05, 0.5, 0.45, 0.38, 0.25


def _extremes_layout(fig, blocks, cols: int) -> _ExtremesLayout:
    """The largest square crop the figure holds, with the Largest and Smallest blocks side by side or one above the
    other, whichever gives the larger crops.

    Every word a crop carries — its fold change, its metrics — is written **inside** it, over its corners and foot,
    so the crops take the whole figure. Text under or beside each crop took more room than the crops did: the
    montage had to choose between legible metrics and crops large enough to see a cell in."""
    w, h = fig.get_size_inches()
    rows = [len(r) for _t, r in blocks]
    best = None
    for beside in ((False, True) if len(blocks) > 1 else (False,)):
        n_blocks = len(blocks) if beside else 1
        per_col = (w - 0.2 - n_blocks * _EXT_LABELS_W - (n_blocks - 1) * _EXT_BLOCK_GAP) / (cols * n_blocks)
        n_rows = max(rows) if beside else sum(rows)
        per_row = (h - _EXT_TOP - _EXT_BOTTOM - (1 if beside else len(blocks)) * _EXT_HEADER) / n_rows
        side = max(min(per_col, per_row) - _EXT_GAP, 0.2)
        if best is None or side > best.side + 1e-6:
            best = _ExtremesLayout(side, beside, side >= _EXT_MIN_SIDE_WITH_METRICS)
    return best


def render_extremes(fig, model, crops: dict, *, label: str, note: str = "", cells: bool = False,
                    timepoints=None, metrics: dict | None = None, numbers: bool = True,
                    image_channel: str = "", display_range=None) -> dict:
    """The *N* nanowells with the largest fold change and the *N* with the smallest, each a row of its crops at the
    timepoints shown; returns ``{axes: (well, q, r)}`` so a click on a row selects its nanowell.

    *model* is ``extremes_compute.ExtremesModel``; *crops* is ``(well, q, r) -> {timepoint: Crop}`` for the
    nanowells cut so far — a row not yet cut says so in each panel. *timepoints* are the columns (the well's every
    timepoint when ``None``). Every crop is drawn on **one** brightness scale, as the enlarged crops are, so a
    nanowell that fills with cells reads brighter than one that empties. Each row is labelled with its rank, address
    and the fold change it was ranked by; under each crop is the fold change at its timepoint and *metrics*
    (``site -> {timepoint: [items]}``, ``series_compute.crop_metric_lines``) packed to the crop's width, and the
    ranking timepoint's column is marked.

    The montage is laid out in inches to fill the figure (:func:`_extremes_layout`): square crops as large as the
    figure holds, the Largest and Smallest blocks side by side whenever that gives larger crops than one above the
    other, the whole centred."""
    fig.clear()
    _bg, ink, faint = _inks(fig.add_subplot(111))
    fig.clear()
    blocks = [(f"Largest {len(model.largest)}", model.largest), (f"Smallest {len(model.smallest)}", model.smallest)]
    blocks = [(title, rows) for title, rows in blocks if rows]
    columns = [tp for tp in (timepoints if timepoints is not None else model.timepoints) if tp in model.timepoints]
    if not blocks or not columns:
        said = f"No nanowell in {model.well} has a {label.lower()} at {model.timepoint}, so none can be ranked."
        if model.excluded:
            said += " " + _sentence(caption(model, label).split("; ", 1)[-1]) + "."
        render_message(fig, said, gid="nw-extremes-missing")
        return {}
    metrics = metrics or {}
    every = [crop for row in model.largest + model.smallest
             for tp, crop in (crops.get(row.site) or {}).items() if tp in columns or tp == ""]
    layout = _extremes_layout(fig, blocks, len(columns))
    draw = _ExtremesDrawing(fig, layout, model, metrics, crop_scale(every, display_range), cells, ink, faint)
    draw.numbers = numbers
    hits: dict = {}
    for k, (title, rows) in enumerate(blocks):
        bx, by = draw.block_origin(k, blocks, len(columns))
        draw.block_header(bx, by, f"{title} · {label} at {model.timepoint}", columns)
        for i, row in enumerate(rows):
            y_crop_top = by - _EXT_HEADER - i * draw.row_h
            draw.row_label(bx, y_crop_top, row)
            for j, tp in enumerate(columns):
                x0 = bx + _EXT_LABELS_W + j * draw.cell_w
                ax = draw.crop(x0, y_crop_top, crops.get(row.site), tp)
                hits[ax] = row.site
                if numbers:
                    draw.crop_text(ax, row, tp)
    _ink(fig.text(0.02, 1 - 0.2 / draw.h_in,
                  f"{model.well} · the nanowells with the largest and smallest {label.lower()}",
                  fontsize=10, color=ink, va="top", gid="nw-extremes-title"))
    _ink(fig.text(0.02, 0.012, draw.caption(label, columns, note, image_channel), fontsize=7, color=faint,
                  gid="nw-extremes-caption", wrap=True), muted=True)
    return hits


class _ExtremesDrawing:
    """One Extremes montage being drawn: its sizes in inches from the layout, the figure's inks, and what the crops
    drawn so far said about themselves (their sources, montage notes, cells), which the caption reports."""

    def __init__(self, fig, layout: _ExtremesLayout, model, metrics: dict, scale: CropScale, cells: bool,
                 ink, faint) -> None:
        self.fig, self.layout, self.model, self.metrics = fig, layout, model, metrics
        self.scale, self.cells, self.ink, self.faint = scale, cells, ink, faint
        self.w_in, self.h_in = fig.get_size_inches()
        self.cell_w = self.row_h = layout.side + _EXT_GAP
        self.notes: set = set()
        self.sources: set = set()
        self.pending = False
        self.metrics_cut = False                         # some crop had more metrics than its line holds
        self.numbers = True                              # the numbers are written on the crops (Metrics box)
        self.tally: dict = {}

    def text(self, x_in: float, y_in: float, said: str, *, color, **kw):
        """Figure text at a position in inches."""
        return _ink(self.fig.text(x_in / self.w_in, y_in / self.h_in, said, color=color, **kw),
                    muted=color == self.faint)

    def block_origin(self, k: int, blocks, n_columns: int) -> tuple[float, float]:
        """The top-left corner of block *k*, the montage centred both ways in the room between title and caption."""
        rows = [len(r) for _t, r in blocks]
        block_w = _EXT_LABELS_W + n_columns * self.cell_w
        n_beside = len(blocks) if self.layout.beside else 1
        total_w = n_beside * block_w + (n_beside - 1) * _EXT_BLOCK_GAP
        total_h = ((1 if self.layout.beside else len(blocks)) * _EXT_HEADER
                   + (max(rows) if self.layout.beside else sum(rows)) * self.row_h)
        x_start = max((self.w_in - total_w) / 2, 0.1)
        y_top = self.h_in - _EXT_TOP - max((self.h_in - _EXT_TOP - _EXT_BOTTOM - total_h) / 2, 0.0)
        if self.layout.beside:
            return x_start + k * (block_w + _EXT_BLOCK_GAP), y_top
        return x_start, y_top - sum(_EXT_HEADER + n * self.row_h for n in rows[:k])

    def block_header(self, bx: float, by: float, title: str, columns) -> None:
        self.text(bx, by - 0.16, title, color=self.ink, fontsize=8.5, fontweight="bold", gid="nw-extremes-block")
        for j, tp in enumerate(columns):
            self.text(bx + _EXT_LABELS_W + j * self.cell_w + self.layout.side / 2, by - _EXT_HEADER + 0.04,
                      tp + (" · ranked" if tp == self.model.timepoint else ""), color=self.ink, ha="center",
                      va="bottom", fontsize=_EXT_VALUE_PT, gid="nw-extremes-tp")

    def row_label(self, bx: float, y_crop_top: float, row) -> None:
        self.text(bx + _EXT_LABELS_W - 0.08, y_crop_top - self.layout.side / 2,
                  f"#{row.rank}  q {row.site[1]}, r {row.site[2]}\n{format_value(self.model.column, row.value)}",
                  color=self.ink, ha="right", va="center", fontsize=_EXT_LABEL_PT, gid="nw-extremes-row")

    def crop(self, x0: float, y_crop_top: float, site_crops, tp: str):
        """One crop's panel, or why it has none; returns the axes, which a click on selects its nanowell."""
        s = self.layout.side
        ax = self.fig.add_axes([x0 / self.w_in, (y_crop_top - s) / self.h_in, s / self.w_in, s / self.h_in])
        ax._aw_image_panel = True                         # pixel for pixel: no typed axis range on it
        ax.set_xticks([])
        ax.set_yticks([])
        # A read that failed whole stores its reason under "" (``controller._fetch``).
        crop = (site_crops or {}).get(tp) or (site_crops or {}).get("")
        if crop is not None and crop.image is not None:
            lo, hi = self.scale.of(crop)
            ax.imshow(crop.image, cmap="gray", interpolation="nearest", vmin=lo, vmax=hi, gid="nw-extremes-image")
            if self.cells:
                _draw_crop_cells(ax, crop, self.tally, size=max(5.0, 9.0 * s))
            self.sources.add(crop.source)
            if crop.source == "montage":
                self.notes.add(crop.note)
        else:
            self.pending = self.pending or site_crops is None
            said = "Cutting…" if site_crops is None else (crop.note if crop is not None else f"No row at {tp}.")
            _ink(ax.text(0.5, 0.5, said, ha="center", va="center", wrap=True, fontsize=5.5, transform=ax.transAxes,
                         color=self.faint, gid="nw-extremes-missing"), muted=True)
        if tp == self.model.timepoint:
            for spine in ax.spines.values():
                spine.set_linewidth(1.6)
        return ax

    def crop_text(self, ax, row, tp: str) -> None:
        """The crop's fold change in its top-left corner and its metrics along its foot, written inside it on a
        translucent band of the ground so they read over any image and take no room of their own."""
        ranked = tp == self.model.timepoint
        side_pt = self.layout.side * 72
        band = {"boxstyle": "square,pad=0.15", "facecolor": _inks(ax)[0], "alpha": 0.62, "linewidth": 0}
        _ink(ax.text(0.03, 0.97, format_value(self.model.column, row.values.get(tp, math.nan)),
                     transform=ax.transAxes, ha="left", va="top", fontsize=_EXT_VALUE_PT,
                     fontweight="bold" if ranked else "normal", color=self.ink, bbox=band, zorder=6,
                     gid="nw-extremes-value"))
        if not self.layout.metrics_shown:
            return
        # One line along the foot, the measurements in their order of importance as far as they fit: three lines
        # covered the lower third of the nanowell, where the cells are.
        line, dropped = _one_line(self.metrics.get(row.site, {}).get(tp, []), side_pt * 0.94, _EXT_METRIC_PT)
        self.metrics_cut = self.metrics_cut or dropped > 0
        if line:
            _ink(ax.text(0.5, 0.02, line, transform=ax.transAxes, ha="center", va="bottom", fontsize=_EXT_METRIC_PT,
                         color=self.ink, bbox=band, zorder=6, gid="nw-extremes-metrics"))

    def caption(self, label: str, columns, note: str, image_channel: str = "") -> str:
        """What the montage says about itself: the ranking, where the crops came from, the cells, the timepoints left
        out, and the metrics when they had to be."""
        sources = self.sources
        origin = ("cut from each owner tile at full resolution" if sources == {"tile"} else
                  "cut from the montage, at its lower resolution" if sources == {"montage"} else
                  "cut from each owner tile, or the montage where it had to be" if sources else
                  "still being cut" if self.pending else "none could be cut")
        hidden = [tp for tp in self.model.timepoints if tp not in columns]
        said = [caption(self.model, label) + ".",
                f"Crops of the {_of_channel(image_channel)}, {origin}; every crop on {self.scale.said()}."
                + "".join(f" {_sentence(n_)}." for n_ in sorted(self.notes))
                + (f" {_sentence(_cells_clause(self.tally))}." if self.tally else "")
                + (f" Timepoints not shown: {', '.join(hidden)}." if hidden else "")
                + (" The crops are too small at this size to write their metrics on: show fewer timepoints or "
                   "nanowells, widen the window, or pop out a nanowell to read them."
                   if not self.layout.metrics_shown and self.numbers else
                   " Each crop writes the metrics that fit one line along its foot; pop out a nanowell for all of them."
                   if self.metrics_cut else "")]
        return " ".join(([note] if note else []) + said)


def _states_line(frame) -> str:
    """What a nanowell's state was over time, short enough for one line of the detail's header: the timepoints whose
    state was not plainly ``measured`` by name, or that it was measured at every one. A line listing every timepoint
    wrapped onto the next nanowell's line once two were selected."""
    odd = [f"{tp}: {_state_text(row)}" for tp, row in zip(frame["timepoint"], frame.to_dict("records"))
           if _state_text(row) != "measured"]
    return "; ".join(odd) if odd else "measured at every timepoint"


def _band(x) -> float:
    finite = np.sort(np.asarray(x, dtype=float)[np.isfinite(x)])
    gaps = np.diff(finite)
    return float(np.min(gaps[gaps > 0]) * 0.3) if (gaps > 0).any() else 0.3


def _state_text(row: dict) -> str:
    flags = [name for name in ("fused", "partially_visible") if row.get(name) is True]
    state = str(row.get("state", ""))
    if state == "out_of_focus":
        state = "rim out of focus"
    return state + (f" ({', '.join(f.replace('_', ' ') for f in flags)})" if flags else "")


# ── the Array view (V2, V7) ──────────────────────────────────────────────────
#: The colour map a metric is drawn in. Never a grey one: grey is how a nanowell without a value is drawn.
MAP_CMAP = "viridis"
#: How a nanowell without a value is outlined, by its state (fill colouring by the metric).
_STATE_STYLE = {"occluded": ("////", "solid"), "clipped": ("", "dashed"), "absent": ("", "dotted"),
                "not_located": ("", "dotted"), "unaddressed": ("", "dotted"),
                "identity_contradicted": ("xx", "solid"), "field_excluded": ("++", "solid"),
                "out_of_focus": ("....", "solid")}
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
                 region=None, note: str = "", dimmed=frozenset()) -> ArrayDrawn:
    """The Array view: one well at one timepoint, drawn with the *layers* that are on (``state.ArrayLayers``).

    *image* is what is drawn under the nanowells in the well frame: a run montage (``image_compute.Montage``) or a
    tile store (``full_montage_compute.TileStore``), rendered for exactly the region and pixels the axes show.
    *region* ``(left, top, right, bottom)`` is where the view looks, widened to the axes' shape; without one the
    view fits everything drawn. The nanowells are coloured as ``ui/nanowell_sites.py`` colours them wherever one is
    drawn, so a state reads the same here as in Analyze's Test detection. The nanowells in *dimmed* (``(well, q,
    r)``: outside the *Subset*) are drawn under a veil of the ground, still where they are and still
    clickable, so where the kept ones sit on the array stays visible.
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
        _dim_nanowells(ax, scene.sites, dimmed)
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
    # A map in the well frame, with its own zoom: an axis range, log scale or tick choice typed in Properties is the
    # Distribution's (the page's views share one figure, and its typed prefs with it), and applied here it squeezed
    # the array into a band. The style pass leaves an image panel's limits and scales alone (``plot_style``).
    ax._aw_image_panel = True
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


#: How opaque the veil over a nanowell outside the *Subset* is: enough to read as set aside, little
#: enough that its state and fill still show through.
DIM_ALPHA = 0.65


def _dim_nanowells(ax, sites, dimmed) -> None:
    """A veil of the figure's ground over each nanowell in *dimmed*, in the ground of either theme (a capture flips
    the theme without a redraw, so the veil is a paired colour like the occupancy dots)."""
    from matplotlib.colors import to_rgba

    from widgets.plot_card import plot_tokens, theme_paired_colours

    if not dimmed or not len(sites.x):
        return
    mask = np.array([tuple(site) in dimmed for site in sites.sites], dtype=bool)
    if not mask.any():
        return
    screen = to_rgba(plot_tokens("screen")[0], DIM_ALPHA)
    publication = to_rgba(plot_tokens("publication")[0], DIM_ALPHA)
    coll = _ellipses(ax, sites, mask, facecolors=publication if _is_publication(ax) else screen,
                     edgecolors="none", zorder=4.5)
    coll.set_gid("nw-array-dimmed")
    theme_paired_colours(coll, screen=(screen, "none"), publication=(publication, "none"))


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
        cax._aw_colorbar = True                           # a key for the fill, not a plot axis (plot_style)
        cax._aw_type_scale = GRID_TYPE_SCALE              # sized beside one map, not as a plot of its own
        _ink(bar.ax.set_ylabel(label, fontsize=_PREF_PT * GRID_TYPE_SCALE, color=ink))
        bar.ax.tick_params(labelsize=_PREF_PT * GRID_TYPE_SCALE, colors=muted)
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
    """One panel per well: a thin line per nanowell, a thick median per occupancy class at baseline.

    The panels share one y scale, labelled on the left column, and one time axis: recorded hours as numbers, the
    axis labelled once per column under the bottom row — the timepoint names at their true hours ran into each
    other wherever two were imaged close together — and the occupancy key at the top right."""
    from matplotlib.collections import LineCollection
    from matplotlib.ticker import MaxNLocator

    from widgets.plot_card import theme_paired_colours

    fig.clear()
    shown = panels[:MAX_PANELS]
    rows, cols = _grid(len(shown))
    fig.subplots_adjust(left=0.07, right=0.98, top=0.88, bottom=0.10 + _FOOTER_BASE + 2 * _LINE, wspace=0.12,
                        hspace=0.42)
    axes = [fig.add_subplot(rows, cols, i + 1) for i in range(len(shown))]
    inks = _inks(axes[0] if axes else fig.add_subplot(1, 1, 1))
    _bg, ink, muted = inks
    scale = GRID_TYPE_SCALE if len(shown) > 1 else SINGLE_TYPE_SCALE
    in_hours = bool(shown) and all(p.in_hours for p in shown)
    drawn = without = 0
    marked_kinds: set = set()
    for ax, panel in zip(axes, shown):
        _prepare_axes(ax, scale)
        # Each nanowell as two-point segments, so a segment into or out of a timepoint whose nanowell identity is
        # not confirmed can be dashed (``_identity_marks``); the colours stay paired per segment.
        unverified = set(panel.marks)
        oks = panel.line_ok or [np.ones(len(panel.times), dtype=bool)] * len(panel.lines)
        for dashed in (False, True):
            segments, occupancy = [], []
            for (_site, occ, y), ok in zip(panel.lines, oks):
                # A value that may belong to another nanowell is joined to nothing (drawn as a point below).
                pieces = split_segments(panel.times, np.where(ok, y, np.nan), unverified)[int(dashed)]
                segments += pieces
                occupancy += [occ] * len(pieces)
            if segments:
                screen, publication = _occupancy_colours(occupancy)
                edges = (publication if _is_publication(ax) else screen)[1]
                coll = LineCollection(segments, colors=edges, linewidths=0.5, alpha=0.45,
                                      linestyles="--" if dashed else "-")
                ax.add_collection(coll)
                theme_paired_colours(coll, screen=("none", screen[1]), publication=("none", publication[1]))
        for cls, median in panel.class_medians.items():
            # A paired collection, like the thin lines, so a capture repaints it: drawn as a plain line it kept its
            # screen shade on publication white, where the 3+ median nearly vanished (roll-up review X14).
            screen, publication = _occupancy_colours([cls])
            colour = (publication if _is_publication(ax) else screen)[1]
            solid, dashed = split_segments(panel.times, median, unverified)
            for pieces, style in ((solid, "-"), (dashed, "--")):
                if not pieces:
                    continue
                thick = LineCollection(pieces, colors=colour, linewidths=2.4, zorder=4, linestyles=style,
                                       label=f"{cls} at baseline" if style == "-" else None)
                ax.add_collection(thick)
                theme_paired_colours(thick, screen=("none", screen[1]), publication=("none", publication[1]))
        _unjoined_points(ax, panel, oks)
        span = float(np.ptp(panel.times)) if len(panel.times) > 1 else 1.0
        marked_kinds.update(panel.marks.values())
        mark_timepoints(ax, panel.times, panel.marks, _mark_colours(muted), width=0.04 * span or 0.3,
                        fontsize=ANNOTATION_PT, ground=inks[0])
        if in_hours:
            ax.xaxis.set_major_locator(MaxNLocator(nbins=6, integer=True, min_n_ticks=3))
        else:
            ax.set_xticks(panel.times)
            ax.set_xticklabels(panel.labels)
        _no_offset(ax)
        _ink(ax.set_title(panel.well, fontsize=10, fontweight="bold", loc="left", color=ink))
        ax.grid(alpha=0.3)
        drawn += len(panel.lines)
        without += panel.n_without_value
    _share_limits(axes)
    for i, ax in enumerate(axes):
        if i % cols:
            ax.tick_params(labelleft=False)
        else:
            _ink(ax.set_ylabel(_wrap_label(label, rows), color=ink))
        if i + cols >= len(axes):
            _ink(ax.set_xlabel("Time (h)" if in_hours else "Timepoint (order; times unknown)", color=ink))
    if axes:
        _occupancy_key(fig, "Thick lines · cells at baseline")
    lines = [(f"Fixed cohort: {drawn:,} nanowells with a value at every timepoint drawn, one line each; {without:,} "
              "without one at some timepoint not drawn." if cohort else
              f"{drawn:,} nanowells drawn, one line each; {without:,} with no value at any timepoint not drawn. "
              "A gap in a line is a timepoint without a value."),
             "Thick lines: the median of each occupancy class at baseline, over the nanowells with a value at each "
             "timepoint." + (" Times read from the timepoint labels." if time_source == "label" else "")]
    if in_hours and shown:
        names = dict(zip(shown[0].labels, shown[0].times))
        if any(tp not in (f"{h:g} h", f"{h:g}h", f"{h:g}") for tp, h in names.items()):
            lines.append("Timepoints: " + " · ".join(f"{h:g} h {tp}" for tp, h in names.items()) + ".")
    if len(panels) > len(shown):
        lines.append(f"{len(panels) - len(shown)} more well(s) not drawn; select fewer wells.")
    if marked_kinds:
        lines.append(marks_legend(marked_kinds))
    if note:
        lines.insert(0, note)
    # The panels sit above however many lines the footer holds (two were reserved, and four or five ran into the
    # time axis's label), as the Distribution view's footer does.
    fig.subplots_adjust(bottom=0.10 + _FOOTER_BASE + _LINE * max(2, len(lines)))
    for i, text in enumerate(reversed(lines)):
        _ink(fig.text(0.07, _FOOTER_BASE + i * _LINE, text, fontsize=FOOTER_PT, color=muted, gid="nw-traj-footer"),
             muted=True)


def _unjoined_points(ax, panel, oks) -> None:
    """Each value whose nanowell identity is contradicted, as a point of its occupancy colour joined to nothing: it
    is a measurement of *some* nanowell, so it stays on the figure over the hatching, but not on a line."""
    xs, ys, occupancy = [], [], []
    for (_site, occ, y), ok in zip(panel.lines, oks):
        loose = ~np.asarray(ok, dtype=bool) & np.isfinite(y)
        xs += list(panel.times[loose])
        ys += list(y[loose])
        occupancy += [occ] * int(loose.sum())
    if xs:
        screen, publication = _occupancy_colours(occupancy)
        colours = (publication if _is_publication(ax) else screen)[1]
        ax.scatter(xs, ys, s=6, c=colours, alpha=0.6, linewidths=0, zorder=3, gid="nw-traj-unjoined")


def _share_limits(axes) -> None:
    """One x and one y range over *axes*, each the union of what their data asked for."""
    if not axes:
        return
    for ax in axes:
        ax.autoscale_view()
    xs = [ax.get_xlim() for ax in axes]
    ys = [ax.get_ylim() for ax in axes]
    for ax in axes:
        ax.set_xlim(min(x[0] for x in xs), max(x[1] for x in xs))
        ax.set_ylim(min(y[0] for y in ys), max(y[1] for y in ys))


# ── position effects (V6) ────────────────────────────────────────────────────
def render_position(fig, series, *, label: str, timepoint: str, note: str = "") -> None:
    """Metric against ring, per well (median and interquartile band), with each ring's excluded share below.

    The two panels share the ring axis, so a ring's bars stand under its points, and the wells' key sits outside the
    plot on the right, on the card's ground."""
    from matplotlib.ticker import MaxNLocator, PercentFormatter

    from review.domain.selections_model import color_for_well

    fig.clear()
    grid = fig.add_gridspec(2, 1, height_ratios=(3, 1), hspace=0.12)
    ax = fig.add_subplot(grid[0])
    share = fig.add_subplot(grid[1], sharex=ax)
    fig.subplots_adjust(left=0.08, right=0.88, top=0.90, bottom=0.10 + _FOOTER_BASE + _LINE)
    for panel in (ax, share):
        _prepare_axes(panel, SINGLE_TYPE_SCALE)
    _bg, ink, muted = _inks(ax)
    width = 0.8 / max(len(series), 1)
    for k, s in enumerate(series):
        # The well's plate-position colour, as the plate selector and every plot tab colour it: picked by the
        # well's place in this list, A03 drawn alone took A01's colour and no well matched its swatch on the plate.
        colour = color_for_well(s.well)
        ax.fill_between(s.rings, s.q1, s.q3, color=colour, alpha=0.18, linewidth=0)
        ax.plot(s.rings, s.median, color=colour, marker="o", markersize=3, linewidth=1.4, label=s.well)
        share.bar(s.rings + (k - (len(series) - 1) / 2) * width, s.excluded_share, width=width, color=colour)
    ax.set_ylabel(label, color=ink)
    _no_offset(ax)
    _ink(ax.set_title(f"By ring from the central nanowell · {timepoint}", fontsize=10, fontweight="bold", loc="left",
                      color=ink))
    ax.tick_params(labelbottom=False)
    ax.grid(alpha=0.3)
    if series:
        legend = ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False, fontsize=_PREF_PT * 0.5,
                           title="Well")
        legend.get_title().set_color(ink)
        for text in legend.get_texts():
            text.set_color(ink)
    share.set_ylabel("Excluded", color=muted)
    share.set_ylim(0, 1)
    share.yaxis.set_major_locator(MaxNLocator(nbins=2))
    share.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    share.xaxis.set_major_locator(MaxNLocator(integer=True))
    share.set_xlabel("Ring (lattice steps from the central nanowell)", color=ink)
    share.grid(alpha=0.3)
    fig.align_ylabels([ax, share])
    lines = ["Median and interquartile range over the nanowells with a value in each ring; the bars are the share "
             "of each ring's nanowells without one. Descriptive: no test is made."]
    if note:
        lines.insert(0, note)
    for i, text in enumerate(reversed(lines)):
        _ink(fig.text(0.08, _FOOTER_BASE + i * _LINE, text, fontsize=FOOTER_PT, color=muted, gid="nw-position-footer"),
             muted=True)
