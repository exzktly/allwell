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

from review.features.nanowell._array_key import (KEY_COLUMNS, draw_key, key_entries, no_value_marks, reason_tag,
                                                  state_word)
from review.features.nanowell.array_compute import padded
from review.features.nanowell import array_hover_compute as ahc
from review.features.nanowell.background_compute import short_clause
from review.features.nanowell.crop_grid_compute import grid_shape, shown_timepoints
from review.features.nanowell.beeswarm_compute import swarm_layout
from review.features.nanowell.distribution_compute import TIME, DistributionModel
from review.features.nanowell import distribution_style_compute as dsc
from review.features.nanowell.extremes_compute import caption, format_value
from review.features.nanowell.starting_cells_compute import occupancy_in_filter
from review.features.nanowell.full_montage_compute import CentreZoom, fit_region
from review.features.nanowell._identity_marks import mark_timepoints, split_segments
from review.features.nanowell.identity_compute import marks_legend
from review.features.nanowell.series_compute import baseline_text
from review.features.nanowell.summary_compute import well_statistic, well_value

#: A dot's diameter, in points.
DOT_PT = 3.4
#: How many panels sit side by side before wrapping.
MAX_COLUMNS = 4
#: Panels drawn at most; the footer names how many were left out.
MAX_PANELS = 12
#: A slot narrower than this (points) writes its counts rotated and bare.
_ROOM_FOR_LABEL_PT = 34.0
_OCCUPANCY_INDEX = {"0": 0, "1": 1, "2": 2, "3+": 3}


@dataclass
class HitMap:
    """Where each drawn dot is, for turning a click into a nanowell: per axes, display-space points and sites."""
    entries: list = field(default_factory=list)   # (ax, x_data, y_data, sites)
    panels: list = field(default_factory=list)    # (axes, index into model.panels): which axes drew which panel
    values: list = field(default_factory=list)    # per entry, the values of the dots it holds (the window's rings)
    #: The footer the page shows under the figure instead of in it (``render_distribution(footer_out=True)``).
    footer: object = None

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


def site_name(site) -> str:
    """How a nanowell is named wherever the page writes one: its well and its address as the Array view draws it and
    **Go to nanowell** takes it — ``A01 · nanowell 1,0``. It read ``q 1, r 0`` before, which beside a statistics
    report reads as a q-value."""
    return f"{site[0]} · nanowell {site[1]},{site[2]}"


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
#: A footer line's height in units of its type size, so two lines never touch: ``_LINE`` of a figure is 8 pt on a
#: short (sideways) figure, where an 8 pt line has no room.
_LINE_LEADING = 1.4
#: The occupancy key's one row, in points: its type (:data:`ANNOTATION_PT` + 0.5) and a little leading.
_KEY_PT = (ANNOTATION_PT + 0.5) * 1.5
#: What stands over a panel's top edge that the key must clear, in points: a title's 6 pt pad and its 10 pt type, and
#: with the wells' own titles (``pad=14``, room for the counts above the swarms) that pad.
_TITLE_PT = 6 + 10 * 1.3
_PANEL_TITLE_PT = 14 + 10 * 1.3
#: The occupancy key's dots and their class names, in points: what its five entries need not to touch.
_KEY_WIDTH_PT = 217.0
#: One average glyph's width in units of its size, which errs wide for the digits and lower case a footer is.
_GLYPH_EM = 0.55


def _figure_pt(fig) -> tuple[float, float]:
    """The figure's ``(width, height)`` in points: what a fixed size in points is measured against, so a size that
    reads well on a wide figure is not taken from a narrow or short one as a share of it."""
    w, h = fig.get_size_inches()
    return float(w) * 72.0, float(h) * 72.0


def _line_step(fig, size_pt: float = FOOTER_PT) -> float:
    """The distance between two footer lines of *size_pt* type, as a share of the figure's height: ``_LINE`` wherever
    the figure is tall enough for it, more where it is not."""
    return max(_LINE, _LINE_LEADING * size_pt / max(_figure_pt(fig)[1], 1.0))


def _head_room(fig, *, floor_top: float, title_pt: float = _TITLE_PT) -> tuple[float, float, float]:
    """``(top, key_bottom, key_height)``, as shares of the figure's height, for panels with a *title_pt* title over
    them and the occupancy key above that.

    *floor_top* is where the panels' tops sit on a figure tall enough for them; on a short one they come down just
    far enough that the key, the title and the panels' counts each have their own band instead of the key being
    written over the titles."""
    h_pt = max(_figure_pt(fig)[1], 1.0)
    key_h = max(_LINE * 0.9, _KEY_PT / h_pt)
    top = min(floor_top, 0.995 - key_h - title_pt / h_pt)
    return top, min(0.995 - key_h, max(0.955, top + title_pt / h_pt)), key_h


#: The least height, as a fraction of the figure, the panels keep when a footer grows: on a short figure the lines a
#: footer owes the reader can need more room than there is, and matplotlib refuses a bottom at or above the top.
_MIN_PANEL_SHARE = 0.12


def _adjust(fig, **params) -> None:
    """``fig.subplots_adjust(**params)``, with *bottom* held at least :data:`_MIN_PANEL_SHARE` below the top (the
    one given, else the figure's own). A footer that would need more keeps every line and is drawn over the panels'
    lowest part rather than raising ``bottom cannot be >= top`` and leaving the page undrawn.

    A layout engine left on the figure by an earlier style pass is dropped first. Phone layout sets constrained
    layout on the canvas's figure, and ``fig.clear()`` keeps it, so on every later draw matplotlib ignored this call
    (warning once) and ``fig.subplotpars`` kept the margins of whatever was drawn before the phone was entered. The
    phone pass reads those margins to decide which figure text is a footer to keep the panels clear of
    (``plot_style._figure_text_band``), so a footer whose upper lines sat above the stale bottom was laid over the
    x-axis label. The style pass sets the engine again after the renderer has drawn."""
    engine = fig.get_layout_engine()
    if engine is not None and not engine.adjust_compatible:
        fig.set_layout_engine(None)
    if "bottom" in params:
        top = params.get("top", fig.subplotpars.top)
        params["bottom"] = min(params["bottom"], top - _MIN_PANEL_SHARE)
    fig.subplots_adjust(**params)


def _fit_text(text: str, width_pt: float, size_pt: float, *, em: float = _GLYPH_EM) -> str:
    """*text* broken into lines that fit *width_pt* at *size_pt*; every word kept."""
    import textwrap

    chars = max(8, int(width_pt / (em * size_pt)))
    return "\n".join(textwrap.wrap(text, chars)) or text


def render_distribution(fig, model: DistributionModel, *, selected=(), spacing: float = 1.0,
                        footer_out: bool = False, style: str = dsc.AUTO,
                        smooth: int = dsc.SMOOTH_DEFAULT, bins: int = dsc.BINS_DEFAULT,
                        log: bool = False) -> HitMap:
    """Draw *model* on *fig* (cleared first) and return where every dot went.

    *footer_out* is the page: the footer — the counts, what the figure leaves out or marks, how to read it — is not
    written in the figure but returned as ``HitMap.footer`` (a :class:`Footer`), which the page shows in its **Details**
    field under the figure, and the panels take the room it took. Off (Batch Export, a figure saved on its own), the
    footer is written in the figure, every line of it.

    *style* is the page's **Show** (``distribution_style_compute.STYLES``): Auto draws a column's wells as dots up
    to ``DENSITY_ABOVE`` wells and as violins past it, Beeswarm and Violin draw every column one way. *smooth* is
    the violins' bandwidth as a percentage of the automatic one (``distribution_style_compute.bandwidth``). Under
    Histogram, *bins* is the bin count and *log* spaces the bins for a log value axis; the bins and the width scale
    are the whole figure's (every panel's wells), since the panels share one y axis.
"""
    style = dsc.style_or_default(style)
    spacing = spacing if spacing > 0 else 1.0
    fig.clear()
    hits = HitMap()
    panels = model.panels[:MAX_PANELS]
    rows, cols = _grid(len(panels))
    bottom = 0.10 if footer_out else 0.10 + _FOOTER_BASE + _line_step(fig) * _FOOTER_LINES
    top, key_bottom, key_h = _head_room(fig, floor_top=0.86, title_pt=_PANEL_TITLE_PT)
    _adjust(fig, left=0.07, right=0.98, top=top, bottom=bottom, wspace=0.12 if rows * cols > 1 else 0.2,
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
    hist = _figure_histograms(model, panels, bins, log) if style == dsc.HISTOGRAM else None
    for i, (ax, panel) in enumerate(zip(axes, panels)):
        _prepare_axes(ax, scale)
        hits.panels.append((ax, i))
        d, u = _draw_panel(fig, ax, model, panel, hits, rings, inks, ylim=shared, spacing=spacing, style=style,
                           smooth=smooth, hist=hist)
        densities, hidden = densities + d, hidden + u
        if i % cols:
            ax.tick_params(labelleft=False)
        else:
            _ink(ax.set_ylabel(_wrap_label(model.label, rows), color=inks[1]))
        if i + cols >= len(panels) and model.x_label:
            _ink(ax.set_xlabel(model.x_label, color=inks[1], labelpad=ax._aw_gutter_pt))
    footer = _footer(fig, model, inks, panels, densities, hidden, style=style, smooth=smooth,
            hist=hist[0] if hist else None,
            crowded=any(getattr(ax, "_aw_crowded", False) for ax in axes),
            counts_hidden=any(getattr(ax, "_aw_counts_hidden", False) for ax in axes),
            key_at=(key_bottom, key_h), footer_out=footer_out)
    if footer_out:
        hits.footer = footer
    return hits


def _wrapped_to_figure(fig, lines: list, *, left: float = 0.07, size_pt: float = FOOTER_PT) -> tuple[list, int]:
    """*lines* broken to the figure's width, and how many of them the first line became.

    A footer line written from *left* (a share of the figure's width) at *size_pt* runs off the right edge of a narrow
    figure ("Timepoints: 0 h t00 · 1 h t01 · …"), so every footer is broken to what the figure holds. Each piece
    becomes a line of its own, so the footer's spacing and the room it reserves follow. The width is the figure's own,
    in points, so no view needs to be told it is on a phone: a figure wide enough for a line leaves it whole."""
    import textwrap

    width_pt = _figure_pt(fig)[0] * (1 - left - 0.02)
    chars = max(20, int(width_pt / (_GLYPH_EM * size_pt)))
    out, first = [], 1
    for k, line in enumerate(lines):
        parts = textwrap.wrap(line, chars) or [line]
        if k == 0:
            first = len(parts)
        out.extend(parts)
    return out, first


#: The most of a figure's height its footer may take, as a share: past it the panels would be a sliver, or — the footer
#: reserving room it cannot have — written over them.
_FOOTER_MAX_SHARE = 0.38


def _footer_that_fits(fig, lines: list, share: float = _FOOTER_MAX_SHARE) -> list:
    """*lines* (already broken to the figure's width) cut to what *share* of the figure's height holds: the leading
    lines, then one saying how many were left out and how to read them. A phone's figure is a few hundred points tall
    and its footer is many lines of its narrow width, which was drawn over the panels' lowest part, the time axis
    included; a figure with room for every line is returned whole."""
    fit = max(2, int(share / _line_step(fig)))
    if len(lines) <= fit:
        return lines
    left_out = len(lines) - (fit - 1)
    return lines[:fit - 1] + [f"… {left_out} more line{'s' if left_out != 1 else ''}: enlarge the card to read them."]


def _wrap_label(label: str, rows: int) -> str:
    """A y label short enough for its panel: on a grid of more than one row it is broken onto at most three lines.

    Every word is kept. Cutting the label after two lines dropped its end, which is where the channel and whether
    the background was subtracted are written, so two different metrics could read the same on the axis."""
    import textwrap

    if rows < 2 or len(label) <= 26:
        return label
    width = max(26, -(-len(label) // 3))
    while len(lines := textwrap.wrap(label, width)) > 3:      # words break unevenly: widen until three lines hold it
        width += 2
    return "\n".join(lines)


def _members_per_tick(model, panel) -> int:
    if model.axis == TIME:
        return max(len({s.well for s in panel.swarms}), 1)
    return max((len({s.well for s in panel.swarms if round(s.x) == round(t[0])}) for t in panel.ticks), default=1)


def _draw_panel(fig, ax, model, panel, hits, rings, inks, *, ylim=None, spacing: float = 1.0, style: str = dsc.AUTO,
                smooth: int = dsc.SMOOTH_DEFAULT, hist=None) -> tuple[int, int]:
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
    from review.core.plot_style import mark_position_axes

    mark_position_axes(ax, y=False)                     # one slot per well or timepoint, not a value: no X log
    dot_w, dot_h = _dot_units(ax, fig)
    k = _members_per_tick(model, panel)
    half = (0.9 if model.axis == TIME else 0.7) / (2 * k)
    density = k > dsc.density_above(style)
    # How wide one swarm's slot is on the page decides how its counts are written: each swarm's used, excluded and
    # hidden counts when there is room; when there is not, only the bare number used above it, and the footer says
    # the rest are in its totals — written rotated, they ran into the next row's title and into the footer.
    slot_pt = 2 * half / dot_w * DOT_PT
    crowded = slot_pt < _ROOM_FOR_LABEL_PT
    size = CROWDED_PT if crowded else ANNOTATION_PT
    # Even the bare number needs its own width: at phone width twelve slots of a few points each ran "2,025" into
    # "2,025" across the panel's top. Where the widest of them does not fit its slot, none is written, and the footer
    # says the totals hold them. A digit is about 0.6 of its type size wide.
    widest = max((len(f"{len(s.values):,}") for s in panel.swarms), default=0)
    counts_fit = slot_pt >= 0.6 * size * widest + 2
    # The title sits above the counts, higher when they stand rotated.
    ax._aw_crowded = crowded
    ax._aw_counts_hidden = not counts_fit
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
        if density and hist is not None:
            _draw_histogram(ax, swarm, hist, ink, fill=model.decoration.inks.get(swarm.group), column=model.column)
        elif density:
            _draw_density(ax, swarm, half, ink, fill=model.decoration.inks.get(swarm.group), column=model.column,
                          smooth=smooth)
        else:
            layout = _draw_swarm(ax, model, swarm, half, dot_w * spacing, dot_h * spacing, hits, rings, ink)
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
        if counts_fit:
            _ink(ax.text(swarm.x, 1.0, f"{len(swarm.values):,}" if crowded else f"n = {len(swarm.values):,}",
                         transform=_below(ax, -2), ha="center", va="bottom",       # 2 pt above the panel
                         fontsize=size, color=muted, gid="nw-used"), muted=True)
        if swarm.n_excluded and not crowded:
            _ink(ax.text(swarm.x, 0, f"excl. {swarm.n_excluded:,}", transform=_below(ax, gutter), ha="center",
                         va="top", fontsize=size, color=muted, gid="nw-excluded"), muted=True)
            rows_here += 1
        lines_used = max(lines_used, rows_here)
        if subwells and sum(1 for s in panel.swarms if round(s.x) == round(swarm.x)) > 1:
            # The bare well: the group's own name is on the tick above, and the slot is one well wide.
            _ink(ax.text(swarm.x, 0, swarm.well, transform=_below(ax, 4), ha="center", va="top",
                         fontsize=ANNOTATION_PT, color=muted, gid="nw-subwell"), muted=True)
    # The axis label goes under whatever the swarms wrote below the ticks, and under the bands' headers.
    ax._aw_gutter_pt = lines_used * line + (ANNOTATION_PT + 4 if subwells else 0) + 4
    ax._aw_gutter_pt += _draw_bands(ax, panel, gutter + lines_used * line, ink, muted)
    marks = model.identity_marks if model.axis == TIME else {}
    for swarm in panel.swarms:
        if (swarm.well, swarm.label) in marks:
            mark_timepoints(ax, [swarm.x], {swarm.x: marks[(swarm.well, swarm.label)]}, _mark_colours(muted),
                            width=2 * half, fontsize=size, ground=inks[0])
    _draw_centres(ax, model, panel, ink)
    ax.grid(axis="y", alpha=0.3)
    return (len(panel.swarms) if density else 0), hidden


#: The depth one row of band headers takes under the axis, in points.
_BAND_ROW_PT = ANNOTATION_PT + 5


def _draw_bands(ax, panel, below_pt: float, ink, muted) -> float:
    """*Split by*'s bands (and *Then by*'s sub-bands) along the wells axis: each a header centred under its groups,
    *below_pt* under the axis, with a faint divider between neighbouring bands. Returns the depth drawn, in points.

    Under the group names rather than above the swarms, so a header can never sit on a dot, and the sub-bands
    first (nearer their groups) with the bands beneath them, which reads outward the way the nesting does.
    """
    if not panel.bands:
        return 0.0
    depth = 2.0
    rows = [(panel.sub_bands, "nw-subband", muted, False)] if panel.sub_bands else []
    rows.append((panel.bands, "nw-band", ink, True))
    for spans, gid, colour, bold in rows:
        for first, last, label in spans:
            _ink(ax.text((first + last) / 2, 0, label, transform=_below(ax, below_pt + depth), ha="center",
                         va="top", fontsize=ANNOTATION_PT + (0.5 if bold else 0),
                         fontweight="bold" if bold else "normal", color=colour, gid=gid),
                 muted=not bold)
        depth += _BAND_ROW_PT
    for first, _last, _label in panel.bands[1:]:
        _ink(ax.axvline(first - 0.5, color=muted, lw=0.8, ls="-", alpha=0.6, zorder=0.4, gid="nw-band-divider"),
             muted=True)
    return depth


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
            line_._aw_marker_is_data = True              # the diamond is the page's mark for this; Marker style keeps off it
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
    """Draw one swarm's dots; return its :class:`SwarmLayout` (what was drawn, and what did not fit).

    *dot_w* and *dot_h* are the pitch the dots are laid out at — a dot's size times the Point spacing — not the size
    they are drawn at, which is always ``DOT_PT``."""
    from widgets.plot_card import theme_paired_colours

    layout = swarm_layout(swarm.values, half_width=half, dot_w=dot_w, dot_h=dot_h, integer=model.integer)
    idx = layout.index
    x = layout.x + swarm.x
    occupancy = [swarm.occupancy[i] for i in idx]
    value_ink = model.decoration.inks.get(swarm.group)
    if value_ink is not None:
        # *Colour by*: the group's value is the dot's colour, the same ink on either ground (data ink does not
        # follow the theme). Solid for every class — a hollow dot would be decoding occupancy with no key for it.
        pair = ([value_ink] * len(idx), [value_ink] * len(idx))
        screen = publication = pair
    else:
        screen, publication = _occupancy_colours(occupancy)
    faces, edges = publication if _is_publication(ax) else screen
    coll = ax.scatter(x, layout.y, s=(DOT_PT * 0.9) ** 2, facecolors=faces, edgecolors=edges, linewidths=0.6,
                      zorder=3)
    theme_paired_colours(coll, screen=screen, publication=publication)
    coll.set_gid("nw-dots")
    sites = [swarm.sites[i] for i in idx]
    hits.entries.append((ax, x, layout.y, sites))
    hits.values.append(swarm.values[idx])
    _ring_selected(ax, x, layout.y, sites, rings)
    return layout


#: The gid of the ring drawn round a selected nanowell's dot on the Distribution view.
RING_GID = "nw-selected-ring"


def _ring_selected(ax, xs, ys, sites, rings) -> None:
    for xi, yi, s in zip(xs, ys, sites):
        colour = rings.get(tuple(s))
        if colour is not None:
            ax.scatter([xi], [yi], s=(DOT_PT * 2.6) ** 2, facecolors="none", edgecolors=colour, linewidths=1.6,
                       zorder=5).set_gid(RING_GID)


def redraw_selection_rings(hits: HitMap, selected=()) -> None:
    """Move the Distribution view's selection rings to *selected* on the figure as drawn, leaving every other artist
    alone. The rings are the only thing on that figure the selection changes, so selecting a nanowell or closing
    the site detail need not rebuild the model and draw every dot again — on a sixteen-well plate over ten
    timepoints that full redraw took seconds. Draws exactly what :func:`render_distribution` would, through the
    same :func:`_ring_selected`, at the positions the hit map recorded."""
    rings = {tuple(site): colour for site, colour in zip(map(tuple, selected), selection_colours())}
    for ax in {id(entry[0]): entry[0] for entry in hits.entries}.values():
        for coll in [c for c in ax.collections if c.get_gid() == RING_GID]:
            coll.remove()
    if rings:
        for ax, xs, ys, sites in hits.entries:
            _ring_selected(ax, xs, ys, sites, rings)


def _draw_density(ax, swarm, half, ink, *, fill: str | None = None, column: str = "",
                  smooth: int = dsc.SMOOTH_DEFAULT) -> None:
    """One well's nanowells as a violin with its value, not dots — under Auto for a set of more than four wells
    (U2), or every well under Show: Violin. The value is the median, or the mean for a metric whose well value is a
    mean (``summary_compute.well_value`` of *column*). *fill* is the group's *Colour by* ink, which the density
    takes in place of the plot's ink; *smooth* is the bandwidth as a percentage of the automatic one."""

    if len(swarm.values) >= 2 and np.ptp(swarm.values) > 0:
        parts = ax.violinplot([swarm.values], positions=[swarm.x], widths=[2 * half], showextrema=False,
                              bw_method=dsc.bandwidth(smooth))
        for body in parts["bodies"]:
            body.set_facecolor(fill or ink)
            body.set_alpha(0.35)
            body.set_edgecolor("none")
            body.set_gid("nw-density")
    if len(swarm.values):
        median_tick = ax.plot([swarm.x - half, swarm.x + half], [well_value(swarm.values, column)] * 2, color=ink,
                              linewidth=1.2, zorder=4)[0]
        median_tick._aw_no_style_override = True         # a mark of the well's value, not a series
        _ink(median_tick)


def _slot_half(model, panel) -> float:
    """The half-width of one swarm's slot in *panel*, as :func:`_draw_panel` lays it out."""
    return (0.9 if model.axis == TIME else 0.7) / (2 * _members_per_tick(model, panel))


def _figure_histograms(model, panels, bins: int, log: bool) -> tuple:
    """``(ColumnHistograms, {id(swarm): index}, half)`` over every swarm the figure draws: one set of bins and one
    width scale for the whole figure, and the narrowest panel's slot half-width, so a bar of the same probability is
    the same width in every panel."""
    from review.domain.column_histogram import column_histograms

    swarms = [s for p in panels for s in p.swarms]
    hist = column_histograms([s.values for s in swarms], bins, integer=bool(model.integer), log=log)
    half = min((_slot_half(model, p) for p in panels if p.swarms), default=0.35)
    return hist, {id(s): i for i, s in enumerate(swarms)}, half


#: The gid of the Distribution histogram's bars.
HISTOGRAM_GID = "nw-histogram"


def _draw_histogram(ax, swarm, hist, ink, *, fill: str | None = None, column: str = "") -> None:
    """One well's nanowells as a histogram mirrored about its slot, on the figure's bins and width scale, with its
    value's tick (the median, or the mean — as :func:`_draw_density`). *hist* is :func:`_figure_histograms`'s."""
    histograms, index, half = hist
    i = index.get(id(swarm))
    if i is not None:
        bottoms, heights, lefts, widths = histograms.bars(i, swarm.x, half)
        if len(bottoms):
            for patch in ax.barh(bottoms, widths, height=heights, left=lefts, align="edge", color=fill or ink,
                                 alpha=0.35, linewidth=0, zorder=2):
                patch.set_gid(HISTOGRAM_GID)
    if len(swarm.values):
        tick = ax.plot([swarm.x - half, swarm.x + half], [well_value(swarm.values, column)] * 2, color=ink,
                       linewidth=1.2, zorder=4)[0]
        tick._aw_no_style_override = True                # a mark of the well's value, not a series
        _ink(tick)


def _shape_alerts(model, style: str, smooth: int, hist) -> list:
    """What the footer says when wells are drawn as shapes rather than dots: which shape and why, how to get the dots
    back, and — for a violin smoothed away from the automatic bandwidth, or a histogram — how to read it."""
    statistic = well_statistic(getattr(model, "column", ""))
    if style == dsc.HISTOGRAM:
        note = hist.note("well") if hist is not None else ""
        return [f"Show: Histogram — each well is drawn as a histogram of its nanowells with its {statistic}"
                + (f" ({note})" if note else "") + "; every well's bars sum to 1 and share one set of bins and one "
                "width scale. Choose Beeswarm or Auto to see and click single nanowells."]
    if style == dsc.VIOLIN:
        out = [f"Show: Violin — each well is drawn as a violin with its {statistic}; choose Beeswarm or Auto to see "
               "and click single nanowells."]
    else:
        out = [f"Sets of more than four wells are drawn as violins with each well's {statistic}; select fewer wells, "
               "or choose Show: Beeswarm, to see and click single nanowells."]
    if dsc.clamp_smooth(smooth) != dsc.SMOOTH_DEFAULT:
        out.append(f"Violin smoothing {dsc.smooth_text(smooth)} the automatic (Scott's rule) bandwidth.")
    return out


@dataclass
class Footer:
    """The Distribution footer as data: the counts line, what changes, leaves out or marks what is drawn (``alerts``,
    the model's), how to read it (``notes``), and what this drawing could not fit (``warnings``: panels not drawn,
    dots not drawn, densities)."""
    counts: str = ""
    alerts: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def lines(self) -> list:
        """In the figure's order: the counts, the alerts, the notes, and last what this drawing left out."""
        return [self.counts, *self.alerts, *self.notes, *self.warnings]


def _footer(fig, model: DistributionModel, inks, panels, densities: int, hidden: int, *,
            style: str = dsc.AUTO, smooth: int = dsc.SMOOTH_DEFAULT, hist=None, crowded: bool = False, counts_hidden: bool = False, key_at: tuple[float, float] | None = None,
            footer_out: bool = False) -> Footer:
    """The sums the figure owes the reader, over the panels drawn, with what was left out stated beside them — as a
    :class:`Footer`, written in the figure unless *footer_out*; the figure's key is drawn either way."""

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
    alerts = []                                          # what this drawing could not fit, written last
    left_out = model.panels[len(panels):]
    if left_out:
        n = sum(s.n_roster for p in left_out for s in p.swarms)
        alerts.append(f"{len(left_out)} more panel(s) not drawn ({n:,} values, not in the counts above); "
                      "select fewer wells to see them.")
    if counts_hidden:
        alerts.append("Too narrow to write each swarm's count, exclusions and undrawn dots beside it; the totals above "
                      "count them.")
    elif crowded:
        alerts.append("Too narrow to write each swarm's exclusions and undrawn dots under it; the totals above count "
                      "them.")
    if hidden and crowded:
        alerts.append(f"{hidden:,} values in all are used but not drawn as dots, for lack of room.")
    elif hidden:
        alerts.append(f"+N under a swarm: {hidden:,} values in all are used but not drawn as dots, for lack of "
                      "room; widen the window or select fewer wells to see them.")
    if densities:
        alerts += _shape_alerts(model, style, smooth, hist)
    footer = Footer(line1, list(getattr(model, "alerts", ()) or ()), list(model.notes), alerts)
    if footer_out:
        _figure_key(fig, model, key_at)
        return footer
    lines = footer.lines()
    lines, line1_parts = _wrapped_to_figure(fig, lines)
    step = _line_step(fig)
    if len(lines) > _FOOTER_LINES:
        # Every line here is owed to the reader — a panel not drawn, values not drawn as dots — so the footer
        # grows to hold them rather than cutting the last (roll-up review X11).
        _adjust(fig, bottom=0.10 + _FOOTER_BASE + step * len(lines))
    for i, text in enumerate(reversed(lines)):
        first = (len(lines) - 1 - i) < line1_parts
        _ink(fig.text(0.07, _FOOTER_BASE + i * step, text, fontsize=FOOTER_PT, color=ink if first else muted,
                      gid="nw-footer-used" if first else "nw-footer"), muted=not first)
    _figure_key(fig, model, key_at)
    return footer


def _figure_key(fig, model: DistributionModel, key_at) -> None:
    """The key the dots' colours decode by, at the figure's top right: it stays in the figure wherever the footer is."""
    if model.decoration.colours:
        # Coloured by a sample variable: the dots decode by its values, and the occupancy key would decode colours
        # the figure no longer contains.
        _value_key(fig, model.decoration)
        return
    # The occupancy key (U5): the colours decode only with it, and its title names the baseline in use.
    _occupancy_key(fig, "Cells at baseline",
                   baseline=baseline_text(model.baseline),
                   starting=model.starting, at=key_at)


def _value_key(fig, decoration) -> None:
    """*Colour by*'s key at the figure's top right, where the occupancy key otherwise sits: one dot per value
    drawn, in the variable's order, titled with the variable's name. Only the values drawn — the reader counts the
    key (``variable_series.legend_entries``)."""
    from matplotlib.lines import Line2D

    if not decoration.legend:
        return
    handles = [Line2D([], [], linestyle="none", marker="o", markersize=DOT_PT * 1.3, markerfacecolor=colour,
                      markeredgecolor=colour, label=label) for label, colour in decoration.legend]
    legend = fig.legend(handles=handles, loc="upper right", bbox_to_anchor=(0.98, 0.995), frameon=False,
                        ncol=min(len(handles), 6), fontsize=ANNOTATION_PT + 0.5, handletextpad=0.3,
                        columnspacing=1.0, borderaxespad=0.0, title=f"{decoration.colour_name}:" if decoration.colour_name else None,
                        title_fontsize=ANNOTATION_PT + 0.5, alignment="right")
    legend.set_gid("nw-value-legend")
    muted = _inks(fig.axes[0])[2] if fig.axes else None
    for text in [*legend.get_texts(), legend.get_title()]:
        if muted is not None:
            text.set_color(muted)
        _ink(text, muted=True)


def _occupancy_key(fig, title: str, *, baseline: str = "", starting=(None, None),
                   at: tuple[float, float] | None = None) -> None:
    """The occupancy colours' key at the figure's top right: every figure coloured by occupancy class carries one,
    or its colours decode to nothing. Its title reads ``title (baseline; faint: filtered out):``, each part in the
    brackets only when it applies; *starting* is the ``(minimum, maximum)`` count at the baseline the *Subset*
    holds every kept nanowell to (``Subset.start_bounds``), whose excluded classes are drawn faint.

    *at* is the key's ``(bottom, height)`` as shares of the figure's height (:func:`_head_room`): above the panels'
    titles whatever the figure's height. Without one the key sits where a tall figure puts it."""
    from widgets.plot_card import theme_paired_colours

    bottom, height = at if at is not None else _head_room(fig, floor_top=0.88)[1:]
    # Five dots with their class beside each, and the title ending where the first begins: 217 pt holds them at the key's
    # type with a dot's room between, so a narrow figure's key takes more of its width than 38% rather than touching.
    width = min(0.96, max(0.38, _KEY_WIDTH_PT / max(_figure_pt(fig)[0], 1.0)))
    key = fig.add_axes([0.98 - width, bottom, width, height])  # top right, above the panels' titles
    # Phone layout hands the panels to constrained layout, which keeps clear of text written on the figure and knows
    # nothing of an axes added by hand (``plot_style._figure_text_band``). A blank line of the key's own height at
    # the key's own place tells it the band is taken, so the panels' titles start below the key rather than under it.
    fig.text(0.98, bottom + height / 2, " ", fontsize=ANNOTATION_PT + 0.5, ha="right", va="center", alpha=0.0,
             color=_inks(key)[1], gid="nw-legend-band")
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
                       outside: dict | None = None, backgrounds=()) -> None:
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
    # Each fluorescence row's second line says what its background was (*backgrounds*: the wells' modes).
    rows = list(DETAIL_ROWS) + ([(f"brightness_{channel}", f"Cell fluor.\n{short_clause('brightness', backgrounds)}"),
                                (f"signal_{channel}", f"Nanowell fluor.\n{short_clause('signal', backgrounds)}")]
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
        fig.text(0.03, 0.985 - 0.035 * k, f"{site_name(site)} — {_states_line(frame)}{badge}",
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
    """The counted cells of *crop*'s own nanowell: each cell's outline, traced from the pixels the run counted it on
    (``Crop.outlines``), in the Array view's Cells colour — or, when Cell Gating's size classes are set, in its
    class's colour (:func:`_cell_inks`); a cell the run kept no outline of is a dot with a dark rim at its centre,
    as the Cells layer draws it, coloured the same way. A neighbour's cells in the crop's margin are not drawn.
    *tally* adds up what was drawn, how many as outlines, and which crops had no cell table or no outlines, for the
    caption."""
    from matplotlib.collections import LineCollection
    from ui.nanowell_sites import CELL_EDGE_COLOUR

    if crop is None or crop.image is None:
        return
    if crop.cells is None:
        tally["no_table"] = tally.get("no_table", 0) + 1
        return
    tally["drawn"] = tally.get("drawn", 0) + len(crop.cells)
    for key in ("gated", "in_fields"):
        if getattr(crop, key, 0):
            tally[key] = tally.get(key, 0) + getattr(crop, key)
    outlines = crop.outlines if crop.outlines is not None and len(crop.outlines) == len(crop.cells) else None
    if outlines is None:
        tally["no_outlines"] = tally.get("no_outlines", 0) + (1 if len(crop.cells) else 0)
        dotted = np.ones(len(crop.cells), dtype=bool)
    else:
        dotted = np.array([not o for o in outlines], dtype=bool)
    inks = _cell_inks(crop, tally)
    if outlines is not None:
        lines = [line for o in outlines if o for line in o]
        colours = [ink for o, ink in zip(outlines, inks) if o for _line in o]
        tally["outlined"] = tally.get("outlined", 0) + int((~dotted).sum())
        tally["unoutlined"] = tally.get("unoutlined", 0) + int(dotted.sum())
        if lines:
            # The rim colour under the fill colour, so an outline reads on a bright cell and a dark floor alike.
            ax.add_collection(LineCollection(lines, colors=CELL_EDGE_COLOUR, linewidths=1.6, zorder=5,
                                             gid="nw-crop-outlines-rim"))
            ax.add_collection(LineCollection(lines, colors=colours, linewidths=0.8, zorder=5,
                                             gid="nw-crop-outlines"))
    if len(crop.cells):
        if dotted.any():
            ax.scatter(crop.cells[dotted, 0], crop.cells[dotted, 1], s=size or CELL_DOT_PT2,
                       c=[ink for ink, dot in zip(inks, dotted) if dot],
                       edgecolors=CELL_EDGE_COLOUR, linewidths=0.4, zorder=5, gid="nw-crop-cells")
        h, w = crop.image.shape[:2]
        ax.set_xlim(-0.5, w - 0.5)                      # the dots must not widen the image's own extent
        ax.set_ylim(h - 0.5, -0.5)


def _cell_inks(crop, tally: dict) -> list:
    """Each of *crop*'s cells' colour: its size class's (``theme.size_class_inks``) when the crop carries classes
    (``Crop.size_class``, from Cell Gating's rule), else — and for a cell in neither class — the Cells colour. Counts
    each class into *tally* (``"classes"``: label → cells, ``"unclassed"``) for the caption."""
    from ui import theme
    from ui.nanowell_sites import CELL_COLOUR

    n = len(crop.cells)
    classes = getattr(crop, "size_class", None)
    if classes is None or len(classes) != n or not crop.size_labels:
        return [CELL_COLOUR] * n
    inks = theme.size_class_inks()
    counts = tally.setdefault("classes", {})
    out = []
    for k in classes:
        k = int(k)
        if 0 <= k < len(inks) and k < len(crop.size_labels):
            label = crop.size_labels[k]
            counts[label] = counts.get(label, 0) + 1
            out.append(inks[k][0])
        else:
            tally["unclassed"] = tally.get("unclassed", 0) + 1
            out.append(CELL_COLOUR)
    tally["class_labels"] = tuple(crop.size_labels)
    return out


def _classes_clause(tally: dict) -> str:
    """The size classes' colour key for a caption — ``T cells in green (12), DCs in blue (5), white in neither
    (3)`` — or ``""`` when the crops were not coloured by class."""
    labels = tally.get("class_labels")
    if not labels:
        return ""
    from ui import theme

    counts = tally.get("classes", {})
    parts = [f"{label} in {name} ({counts.get(label, 0):,})"
             for label, (_ink, name) in zip(labels, theme.size_class_inks())]
    parts.append(f"white in neither ({tally.get('unclassed', 0):,})")
    return "size classes: " + ", ".join(parts)


def _cells_clause(tally: dict) -> str:
    """What the cells drawn on a set of crops are, for its caption: how many were drawn, whether as outlines or as
    dots (and why a cell is a dot: the run kept no outline of it), how many the page left out (Cell Gating's, and an
    excluded field's), and that a crop without a cell table has none drawn because none could be read — never that
    it holds no cells."""
    if not tally:
        return ""
    said = []
    if "drawn" in tally:
        outlined = tally.get("outlined", 0)
        what = ("outlines" if outlined and outlined == tally["drawn"] else
                "outlines and dots" if outlined else "dots")
        said.append(f"{what}: the {tally.get('drawn', 0):,} cells the run counted in this nanowell")
    if tally.get("gated"):
        said.append(f"{tally['gated']:,} more Cell Gating leaves out, not drawn")
    if tally.get("in_fields"):
        said.append(f"{tally['in_fields']:,} more in an excluded field, not drawn")
    if tally.get("unoutlined") and tally.get("outlined"):
        said.append(f"{tally['unoutlined']:,} of them dots, the run kept no outline of them")
    if tally.get("no_outlines"):
        said.append(f"{tally['no_outlines']} crop(s) of a run that kept no cell outlines, so dots there")
    if tally.get("no_table"):
        said.append(f"{tally['no_table']} crop(s) of a well without a cell table, so no cells drawn there")
    classes = _classes_clause(tally)
    if classes:
        said.append(classes)
    return "; ".join(said)


def _of_channel(image_channel: str) -> str:
    """What a set of crops is an image of, for its caption: the fluorescence channel, or the phase image."""
    return f"{image_channel} image" if image_channel else "phase image"


def render_crop_strip(fig, crops: dict | None, site, *, cells: bool = False, image_channel: str = "",
                      display_range=None, cols: int = 0) -> None:
    """The selected nanowell's crops, one per timepoint, on the strip under the site detail's **Pop out**
    button: each titled with its timepoint, a timepoint without a crop saying why in its place, and where the crops
    were cut from said once in the caption. *crops* is ``None`` while they are being cut.

    *cols* wraps the crops into rows of that many (``crop_grid_compute.strip_columns``, which phone layout asks for);
    ``0`` keeps them in one row, as at a desk."""
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
    missing: dict = {}                   # why no crop -> the timepoints it is why for, said once in the caption
    mixed = len({c.source for _tp, c in items if c.image is not None}) > 1
    n_cols = len(items) if cols <= 0 else min(cols, len(items))
    n_rows = -(-len(items) // n_cols)
    if n_rows == 1:
        sub = fig.add_gridspec(1, n_cols, left=0.03, right=0.97, top=0.84, bottom=0.36, wspace=0.08)
    else:
        sub = fig.add_gridspec(n_rows, n_cols, left=0.03, right=0.97, top=0.94, bottom=0.16, wspace=0.08,
                               hspace=0.3)
    tally: dict = {}
    for k, (tp, crop) in enumerate(items):
        ax = fig.add_subplot(sub[k // n_cols, k % n_cols])
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
            # The reason is said once, in the caption: written in each panel it wrapped to the figure's width, not
            # the panel's, and ran across every neighbour.
            # A note names its own timepoint ("… for A02 at t03 (ok)"); without it, the twelve notes of a well with no
            # montage are one reason, listed against the timepoints it is for.
            why = (crop.note or "no crop could be cut").replace(f" at {tp}", "")
            missing.setdefault(why, []).append(tp)
            _ink(ax.text(0.5, 0.5, "no crop", ha="center", va="center", fontsize=5.5,
                         transform=ax.transAxes, color=faint, gid="nw-crop-missing"), muted=True)
        _ink(ax.set_title(tp + (" (montage)" if mixed and crop.source == "montage" else ""), fontsize=7, color=ink,
                          pad=2, gid="nw-crop-title"))
    sources = {crop.source for _tp, crop in items if crop.image is not None}
    origin = ("from its owner tile at full resolution" if sources == {"tile"} else
              "from the montage, at its lower resolution" if sources == {"montage"} else
              "from its owner tile, or the montage where marked" if sources else "none could be cut")
    caption = (f"Crops ({_of_channel(image_channel)}): {site_name(site)}, {origin}; "
               + scale.said(numbers=scale.chosen)) + "".join(
        f"; {note}" for note in sorted(notes)) + (f"; {_cells_clause(tally)}" if tally else "") + "".join(
        f"; no crop at {_timepoint_list(tps, len(items))}: {why}" for why, tps in missing.items())
    _ink(fig.text(0.03, 0.03, caption, fontsize=6, color=faint, gid="nw-crop-caption", wrap=True), muted=True)


def _timepoint_list(tps: list, of: int) -> str:
    """The timepoints a crop is missing at, short: "every timepoint", or the list."""
    return "every timepoint" if len(tps) == of and of > 1 else ", ".join(tps)


def render_crop_grid(fig, crops: dict | None, site, *, cells: bool = False, metrics: dict | None = None,
                     image_channel: str = "", display_range=None, labels: bool = True, more=(),
                     timepoints=None, grid: str = "auto", cols: int = 0) -> None:
    """The selected nanowell over time, large: one panel per timepoint, filling the pop-out's figure.

    Asked for: a larger view of a selected nanowell over time than the site detail's strip of small crops. Every
    panel of one channel is drawn on **one** brightness scale — the 1st to 99th percentile of all that channel's crops
    together — so a cell that appears, divides or fades reads as a change between panels, not as each crop stretched
    to its own range. *crops* is ``timepoint -> image_compute.Crop`` of *image_channel* as the strip takes it, or
    ``None`` while they are cut; a timepoint without a crop says why in its panel, and the caption says where the crops
    were cut from. A *display_range* chosen on the page replaces the percentile scale (:func:`crop_scale`).

    *more* adds channels: ``(channel, crops or None, display_range)`` each, drawn as a row (or column) of its own on a
    scale of its own. *timepoints* chooses which of the nanowell's timepoints are shown (every one when empty;
    ``crop_grid_compute.shown_timepoints``), and *grid* / *cols* the shape (``crop_grid_compute.grid_shape``).
    *labels* off leaves out each panel's timepoint and the caption; the title stays."""
    fig.clear()
    _bg, ink, faint = _inks(fig.add_subplot(111))
    fig.clear()
    if not crops:
        _ink(fig.text(0.5, 0.5, "Cutting the crops…" if crops is None else "No crop could be cut.", ha="center",
                      va="center", color=faint, gid="nw-crop-grid-pending"), muted=True)
        return
    channels = [(image_channel, crops, display_range)] + [tuple(row) for row in more]
    tps = shown_timepoints(list(crops), timepoints)
    shown = [[c for tp in tps if (c := (row or {}).get(tp)) is not None] for _ch, row, _dr in channels]
    scales = [crop_scale(row, dr) for row, (_ch, _row, dr) in zip(shown, channels)]
    shape = grid_shape(len(channels), len(tps), grid, cols)
    # *metrics* (``timepoint -> [items]``) are written under the first channel's panels, so the rows leave room.
    spec = fig.add_gridspec(shape.rows, shape.cols, left=0.02, right=0.98, top=0.87, bottom=0.15 if metrics else 0.08,
                            wspace=0.05, hspace=0.36 if metrics else 0.3 if shape.rows > 1 else 0.18)
    every = [c for row in shown for c in row]
    mixed = len({c.source for c in every if c.image is not None}) > 1
    notes: set = set()
    tally: dict = {}
    several = len(channels) > 1
    for c, (channel, row, _dr) in enumerate(channels):
        for k, tp in enumerate(tps):
            r, col = shape.place(c, k)
            ax = fig.add_subplot(spec[r, col])
            crop = None if row is None else row.get(tp)
            _crop_grid_panel(ax, crop, scales[c], cells=cells, tally=tally, notes=notes, faint=faint,
                             pending=row is None)
            if labels:
                title = (f"{channel or 'phase'} · {tp}" if several else tp) + (
                    " (montage)" if mixed and crop is not None and crop.source == "montage" else "")
                _ink(ax.set_title(title, fontsize=9, color=ink, gid="nw-crop-title"))
            said = (metrics or {}).get(tp, []) if c == 0 else []
            if said:
                width_pt = ax.get_position().width * fig.get_size_inches()[0] * 72
                _ink(ax.text(0.5, -0.03, "\n".join(_pack(said, width_pt, 8.0)), ha="center", va="top", fontsize=8,
                             transform=ax.transAxes, color=faint, gid="nw-crop-grid-metrics"), muted=True)
    names = " + ".join(_of_channel(channel) for channel, _row, _dr in channels)
    _ink(fig.text(0.02, 0.955, f"{site_name(site)} over time · {names}", fontsize=11, color=ink,
                  gid="nw-crop-grid-title"))
    if labels:
        _ink(fig.text(0.02, 0.015, _crop_grid_caption(channels, every, scales, notes, tally), fontsize=8,
                      color=faint, gid="nw-crop-grid-caption", wrap=True), muted=True)


def _crop_grid_panel(ax, crop, scale, *, cells: bool, tally: dict, notes: set, faint, pending: bool) -> None:
    """One panel of the pop-out's grid: *crop* on *scale*, or a sentence saying why there is none."""
    # An image, pixel for pixel: the style pass must not put the card's typed axis range on it.
    ax._aw_image_panel = True
    ax.set_xticks([])
    ax.set_yticks([])
    if crop is not None and crop.image is not None:
        lo, hi = scale.of(crop)
        ax.imshow(crop.image, cmap="gray", interpolation="nearest", vmin=lo, vmax=hi, gid="nw-crop-grid-image")
        if cells:
            _draw_crop_cells(ax, crop, tally, size=CELL_DOT_PT2 * 2)
        if crop.source == "montage":
            notes.add(crop.note)
        return
    text = ("Cutting the crops…" if pending else crop.note if crop is not None
            else "No crop of this channel at this timepoint.")
    _ink(ax.text(0.5, 0.5, text, ha="center", va="center", wrap=True, fontsize=8, transform=ax.transAxes,
                 color=faint, gid="nw-crop-missing"), muted=True)


def _crop_grid_caption(channels, every, scales, notes, tally) -> str:
    """The pop-out's caption: where the crops were cut from, the brightness scale each channel is drawn on, the
    montage crops' notes and the counted cells."""
    sources = {crop.source for crop in every if crop.image is not None}
    origin = ("cut from its owner tile at full resolution" if sources == {"tile"} else
              "cut from the montage, at its lower resolution" if sources == {"montage"} else
              "cut from its owner tile, or the montage where marked" if sources else "none could be cut")
    if len(channels) == 1:
        scale = f"every panel on {scales[0].said()}"
    else:
        scale = "each channel's panels on a scale of their own: " + "; ".join(
            f"{channel or 'phase'} " + ("not cut yet" if row is None else f"on {s.said()}")
            for (channel, row, _dr), s in zip(channels, scales))
    return (f"{origin}; {scale}" + "".join(f"; {note}" for note in sorted(notes))
            + (f"; {_cells_clause(tally)}" if tally else ""))


@dataclass(frozen=True)
class CropScale:
    """How a set of crops is put on screen: one brightness scale for all of them, so a change between crops reads as
    a change in the nanowell rather than each crop stretched to its own range.

    Without a chosen range the scale is the 1st to 99th percentile of every crop together (:func:`_one_scale`). With
    the page's **Display range** (``chosen``) it is that range, in the raw image's units — which the crops cut from an
    owner tile are in. A crop cut from the run's montage is an 8-bit picture whose numbers mean something else, so
    whenever montage crops sit beside crops of another source — under a chosen range, and under the percentile scale
    too — those crops keep a percentile scale of their own (``montage``), and :meth:`said` says so. Put on the tiles'
    percentile scale, an 8-bit crop beside 12- or 16-bit tile crops filled the bottom few percent of the ramp and
    read as a dark, empty nanowell."""

    lo: float
    hi: float
    chosen: bool = False
    montage: tuple | None = None

    def of(self, crop) -> tuple[float, float]:
        """The ``(vmin, vmax)`` *crop* is drawn with."""
        if self.montage is not None and getattr(crop, "source", "") == "montage":
            return self.montage
        return self.lo, self.hi

    def said(self, numbers: bool = True) -> str:
        """The scale as a caption names it."""
        if not self.chosen:
            out = f"one brightness scale ({self.lo:.4g} to {self.hi:.4g})" if numbers else "one brightness scale"
        else:
            out = f"the display range {self.lo:.4g} to {self.hi:.4g}"
        if self.montage is not None:
            out += (f" (the crops cut from the montage, an 8-bit picture, on a scale of their own: "
                    f"{self.montage[0]:.4g} to {self.montage[1]:.4g})")
        return out


def crop_scale(crops, display_range=None) -> CropScale:
    """The :class:`CropScale` for *crops* (``image_compute.Crop`` objects; one without an image is skipped):
    *display_range* ``(low, high)`` when the page has one, else one percentile scale over them all — except that
    crops cut from the 8-bit montage beside crops of another source get a percentile scale of their own."""
    crops = [c for c in crops if c is not None and c.image is not None]
    montage = [c.image for c in crops if c.source == "montage"]
    if display_range is None:
        others = [c.image for c in crops if c.source != "montage"]
        if montage and others:
            return CropScale(*_one_scale(others), False, _one_scale(montage))
        return CropScale(*_one_scale(c.image for c in crops))
    lo, hi = (float(v) for v in display_range)
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


_EXT_TOP_DEFAULT = 0.5
_EXT_BOTTOM_DEFAULT = 0.45


@dataclass
class _ExtremesLayout:
    """Where the Extremes montage goes, in inches: the crop's side and whether the two blocks sit side by side."""
    side: float
    beside: bool
    #: Whether the crops are large enough for their metrics, written inside each crop along its foot.
    metrics_shown: bool = True
    #: The room above the montage, in inches: the figure's title, which is as many lines as the figure's width needs.
    top: float = _EXT_TOP_DEFAULT
    #: The room under the montage, in inches: the caption, which is as many lines as the figure's width needs.
    bottom: float = _EXT_BOTTOM_DEFAULT


#: The smallest crop side (inches) that holds its metrics legibly: below it, only the fold change is written on the
#: crop, and the caption says so.
_EXT_MIN_SIDE_WITH_METRICS = 0.6
#: Inches: the row labels' column, the space between crops, the room above the montage and the caption below it, a
#: block's title and timepoint headers, and the gap between two blocks side by side.
_EXT_LABELS_W, _EXT_GAP, _EXT_TOP, _EXT_BOTTOM, _EXT_HEADER, _EXT_BLOCK_GAP = 0.8, 0.05, 0.5, 0.45, 0.38, 0.25
_EXT_TITLE_PT = 10.0
#: The caption under the montage: its type size, its line spacing (in units of that size), and where its last line
#: sits, as a share of the figure's height from the foot.
_EXT_CAPTION_PT, _EXT_CAPTION_LEADING, _EXT_CAPTION_Y = 7.0, 1.25, 0.012


def _extremes_layout(fig, blocks, cols: int, top: float = _EXT_TOP, bottom: float = _EXT_BOTTOM) -> _ExtremesLayout:
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
        per_row = (h - top - bottom - (1 if beside else len(blocks)) * _EXT_HEADER) / n_rows
        side = max(min(per_col, per_row) - _EXT_GAP, 0.2)
        if best is None or side > best.side + 1e-6:
            best = _ExtremesLayout(side, beside, side >= _EXT_MIN_SIDE_WITH_METRICS, top, bottom)
    return best


def render_extremes(fig, model, crops: dict, *, label: str, note: str = "", cells: bool = False,
                    timepoints=None, metrics: dict | None = None, numbers: bool = True,
                    image_channel: str = "", display_range=None, tp_names: dict | None = None) -> dict:
    """The *N* nanowells with the largest fold change and the *N* with the smallest, each a row of its crops at the
    timepoints shown; returns ``{axes: (well, q, r)}`` so a click on a row selects its nanowell.

    *model* is ``extremes_compute.ExtremesModel``. Drawn by :func:`render_site_rows`, which the Distribution
    window's pop-out draws through as well; this supplies the two blocks, the title and the sentences about the
    ranking."""
    blocks = [(f"Largest {len(model.largest)}", model.largest), (f"Smallest {len(model.smallest)}", model.smallest)]
    empty = f"No nanowell in {model.well} has a {label.lower()} at {model.timepoint}, so none can be ranked."
    if model.excluded:
        empty += " " + _sentence(caption(model, label).split("; ", 1)[-1]) + "."
    return render_site_rows(
        fig, model, blocks, crops, label=label, empty=empty,
        title=f"{model.name or model.well} · the nanowells with the largest and smallest {label.lower()}",
        head=caption(model, label), note=note, cells=cells, timepoints=timepoints, metrics=metrics, numbers=numbers,
        image_channel=image_channel, display_range=display_range, tp_names=tp_names)


def render_site_rows(fig, model, blocks, crops: dict, *, label: str, empty: str, title: str, head: str,
                     note: str = "", cells: bool = False, timepoints=None, metrics: dict | None = None,
                     numbers: bool = True, image_channel: str = "", display_range=None,
                     with_well: bool = False, bottom: float = _EXT_BOTTOM, tp_names: dict | None = None) -> dict:
    """Blocks of nanowells, each a row of its crops at the timepoints shown; returns ``{axes: (well, q, r)}`` so a
    click on a row selects its nanowell. Extremes draws two blocks (the largest and the smallest of one well); the
    Distribution window draws one (the nanowells inside it, from any number of wells, *with_well* naming each row's).

    *model* gives the montage its ``timepoint`` (the column the rows are ranked at, marked), ``column`` (what a
    value is formatted as) and ``timepoints`` (the columns when none are chosen); *blocks* is
    ``[(title, rows)]``, a row having ``site``, ``rank``, ``value`` and ``values`` (``timepoint -> value``);
    *crops* is ``(well, q, r) -> {timepoint: Crop}`` for the nanowells cut so far — a row not yet cut says so in each
    panel. *empty* is what the figure says when there is nothing to draw, *title* its title and *head* the first
    sentence of its caption. Every crop is drawn on **one** brightness scale, as the enlarged crops are, so a
    nanowell that fills with cells reads brighter than one that empties. Each row is labelled with its rank, address
    and the value it was ranked by; under each crop is the value at its timepoint and *metrics*
    (``site -> {timepoint: [items]}``, ``series_compute.crop_metric_lines``) packed to the crop's width, and the
    ranking timepoint's column is marked. *tp_names* names each column as the band's Timepoint control names it
    (``5 h``, ``summary_compute.timepoint_short``); a timepoint missing from it prints its label.

    The montage is laid out in inches to fill the figure (:func:`_extremes_layout`): square crops as large as the
    figure holds, blocks side by side whenever that gives larger crops than one above the other, the whole
    centred."""
    fig.clear()
    _bg, ink, faint = _inks(fig.add_subplot(111))
    fig.clear()
    blocks = [(block_title, rows) for block_title, rows in blocks if rows]
    columns = [tp for tp in (timepoints if timepoints is not None else model.timepoints) if tp in model.timepoints]
    if not blocks or not columns:
        render_message(fig, empty, gid="nw-extremes-missing")
        return {}
    metrics = metrics or {}
    every = [crop for _t, rows in blocks for row in rows
             for tp, crop in (crops.get(row.site) or {}).items() if tp in columns or tp == ""]
    # The title is broken to the figure's width, and the montage starts below however many lines that is.
    heading = _fit_text(title, fig.get_size_inches()[0] * 72 * 0.96, _EXT_TITLE_PT)
    heading_h = 0.2 + (heading.count("\n") + 1) * _EXT_TITLE_PT * 1.25 / 72
    layout = _extremes_layout(fig, blocks, len(columns), max(_EXT_TOP, heading_h + 0.05), bottom)
    draw = _ExtremesDrawing(fig, layout, model, metrics, crop_scale(every, display_range), cells, ink, faint)
    draw.numbers, draw.with_well, draw.tp_names = numbers, with_well, dict(tp_names or {})
    hits: dict = {}
    for k, (block_title, rows) in enumerate(blocks):
        bx, by = draw.block_origin(k, blocks, len(columns))
        ranked_at = draw.tp_names.get(model.timepoint, model.timepoint)
        draw.block_header(bx, by, f"{block_title} · {label}" + (f" at {ranked_at}" if model.timepoint else ""),
                          columns)
        for i, row in enumerate(rows):
            y_crop_top = by - _EXT_HEADER - i * draw.row_h
            draw.row_label(bx, y_crop_top, row)
            for j, tp in enumerate(columns):
                x0 = bx + _EXT_LABELS_W + j * draw.cell_w
                ax = draw.crop(x0, y_crop_top, crops.get(row.site), tp)
                hits[ax] = row.site
                if numbers:
                    draw.crop_text(ax, row, tp)
    # The caption is written last, since it reports what the crops found, and broken to the figure's width as the
    # title is. A phone's narrow figure breaks it into five or six lines, and the room under the montage was fixed:
    # a line more ("Excluded on this page: …") ran the caption over the lowest crops and their metrics. When it needs
    # more room than the montage left, the montage is laid out again with that room under it.
    width_pt = draw.w_in * 72 * 0.96
    text = "\n".join(_fit_text(part, width_pt, _EXT_CAPTION_PT)
                     for part in draw.caption(head, columns, note, image_channel).split("\n"))
    need = _EXT_CAPTION_Y * draw.h_in + (text.count("\n") + 1) * _EXT_CAPTION_PT * _EXT_CAPTION_LEADING / 72 + 0.06
    if need > bottom + 0.01 and bottom == _EXT_BOTTOM:
        return render_site_rows(fig, model, blocks, crops, label=label, empty=empty, title=title, head=head,
                                note=note, cells=cells, timepoints=timepoints, metrics=metrics, numbers=numbers,
                                image_channel=image_channel, display_range=display_range, with_well=with_well,
                                bottom=need, tp_names=tp_names)
    _ink(fig.text(0.02, 1 - 0.2 / draw.h_in, heading, fontsize=_EXT_TITLE_PT, color=ink, va="top",
                  gid="nw-extremes-title"))
    _ink(fig.text(0.02, _EXT_CAPTION_Y, text, fontsize=_EXT_CAPTION_PT, color=faint, va="bottom",
                  linespacing=_EXT_CAPTION_LEADING, gid="nw-extremes-caption"), muted=True)
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
        self.tp_names: dict = {}
        self.metrics_cut = False                         # some crop had more metrics than its line holds
        self.numbers = True                              # the numbers are written on the crops (Metrics box)
        self.with_well = False                           # the row labels name the well (rows from several wells)
        self.tally: dict = {}

    def text(self, x_in: float, y_in: float, said: str, *, color, **kw):
        """Figure text at a position in inches."""
        return _ink(self.fig.text(x_in / self.w_in, y_in / self.h_in, said, color=color, **kw),
                    muted=color == self.faint)

    def value_text(self, value: float) -> str:
        """A row's value as the figure writes it: the model's own ``format`` when it has one (the window's rows are
        any metric, not only a fold change), else a fold change (``extremes_compute.format_value``)."""
        own = getattr(self.model, "format", None)
        return own(value) if own is not None else format_value(self.model.column, value)

    def block_origin(self, k: int, blocks, n_columns: int) -> tuple[float, float]:
        """The top-left corner of block *k*, the montage centred both ways in the room between title and caption."""
        rows = [len(r) for _t, r in blocks]
        block_w = _EXT_LABELS_W + n_columns * self.cell_w
        n_beside = len(blocks) if self.layout.beside else 1
        total_w = n_beside * block_w + (n_beside - 1) * _EXT_BLOCK_GAP
        total_h = ((1 if self.layout.beside else len(blocks)) * _EXT_HEADER
                   + (max(rows) if self.layout.beside else sum(rows)) * self.row_h)
        x_start = max((self.w_in - total_w) / 2, 0.1)
        top = self.layout.top
        y_top = self.h_in - top - max((self.h_in - top - self.layout.bottom - total_h) / 2, 0.0)
        if self.layout.beside:
            return x_start + k * (block_w + _EXT_BLOCK_GAP), y_top
        return x_start, y_top - sum(_EXT_HEADER + n * self.row_h for n in rows[:k])

    def block_header(self, bx: float, by: float, title: str, columns) -> None:
        self.text(bx, by - 0.16, title, color=self.ink, fontsize=8.5, fontweight="bold", gid="nw-extremes-block")
        for j, tp in enumerate(columns):
            self.text(bx + _EXT_LABELS_W + j * self.cell_w + self.layout.side / 2, by - _EXT_HEADER + 0.04,
                      self.tp_names.get(tp, tp) + (" · ranked" if tp == self.model.timepoint else ""),
                      color=self.ink, ha="center",
                      va="bottom", fontsize=_EXT_VALUE_PT, gid="nw-extremes-tp")

    def row_label(self, bx: float, y_crop_top: float, row) -> None:
        self.text(bx + _EXT_LABELS_W - 0.08, y_crop_top - self.layout.side / 2,
                  (f"#{row.rank}  {row.site[0]} " if self.with_well else f"#{row.rank}  ")
                  + f"nanowell {row.site[1]},{row.site[2]}\n{self.value_text(row.value)}",
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
            # Broken to the crop's own width: ``wrap=True`` breaks at the figure's, and the sentence ran across its
            # neighbours.
            _ink(ax.text(0.5, 0.5, _fit_text(said, s * 72 * 0.9, 5.5), ha="center", va="center", fontsize=5.5,
                         transform=ax.transAxes, color=self.faint, gid="nw-extremes-missing"), muted=True)
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
        _ink(ax.text(0.03, 0.97, self.value_text(row.values.get(tp, math.nan)),
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

    def caption(self, head: str, columns, note: str, image_channel: str = "") -> str:
        """What the montage says about itself: the ranking, where the crops came from, the cells, the timepoints left
        out, and the metrics when they had to be."""
        sources = self.sources
        origin = ("cut from each owner tile at full resolution" if sources == {"tile"} else
                  "cut from the montage, at its lower resolution" if sources == {"montage"} else
                  "cut from each owner tile, or the montage where it had to be" if sources else
                  "still being cut" if self.pending else "none could be cut")
        hidden = [tp for tp in self.model.timepoints if tp not in columns]
        said = [head + ".",
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
                "not_located": ("", "dashdot"), "unaddressed": ("--", "dotted"),
                "identity_contradicted": ("xx", "solid"), "field_excluded": ("++", "solid"),
                "out_of_focus": ("....", "solid")}
#: The height of one row of the Array view's key, as a share of the figure: the axes give up one per row past the first.
_KEY_ROW = 0.035
#: The rule ``_STATE_STYLE`` keeps: every state a nanowell can have without a value draws a mark no other state
#: draws, under either colouring (filled by the metric, it is the mark alone in grey; outlined by state, the mark in
#: the state's colour), so each cause of a nanowell's exclusion can be read off the array.
#: ``tests/test_nanowell_array_marks_are_distinct.py`` renders every state and holds the marks apart.
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
    scene: object = None             # the scene drawn (``array_compute.ArrayScene``): what a hover reads
    label: str = ""                  # what the nanowells are coloured by, as the figure names it
    #: ``(q, r) -> row`` of the nanowell table at the scene's timepoint, built at the first hover
    #: (``array_hover_compute.rows_by_address``).
    rows: dict | None = None


def _image_extent(image) -> tuple[float, float, float, float]:
    """``(left, top, right, bottom)`` of an image layer: a run montage, or a tile store's whole well."""
    if hasattr(image, "render"):
        return image.source.extent_um()
    left, right, bottom, top = image.extent()
    return left, top, right, bottom


def _centre(box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _union(a, b):
    if a is None or b is None:
        return a or b
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _head(scene, layers, dimmed: int = 0) -> str:
    """The counts line, in the key's own groups so it adds up to the roster: ``19 nanowells: 13 with a value · 4 no
    value · 2 rim out of focus``, one without a measured position as not placed. A fused nanowell is counted under its
    state like any other, and the fused and the ones outside the *Subset* (*dimmed*) are said after it, since they are
    marks over a state rather than states: ``…; 1 fused (on a tile seam); 3 outside the Subset``."""
    sites = scene.sites
    states = np.array([str(s) for s in sites.state], dtype=object)
    parts: list[tuple[str, int]] = []
    if layers.colour == "metric":
        has = np.isfinite(np.asarray(sites.values, dtype=float))
        measurable = np.isin(states, ["measured", "empty"])
        parts += [("with a value", int(has.sum())), ("no value", int((~has & measurable).sum()))]
        rest = ~has & ~measurable
    else:
        rest = np.ones(len(states), dtype=bool)
    tally: dict[str, int] = {}
    for state in states[rest]:
        tally[state] = tally.get(state, 0) + 1
    parts += [(state_word(k), v) for k, v in sorted(tally.items(), key=lambda kv: -kv[1])]
    parts.append(("not placed (no measured position)", int(scene.unplaced)))
    undrawn = int(scene.roster) - len(sites) - int(scene.unplaced)
    parts.append(("not drawn", max(undrawn, 0)))
    said = " · ".join(f"{n:,} {what}" for what, n in parts if n)
    head = f"{scene.roster:,} nanowells: {said}" if said else f"{scene.roster:,} nanowells"
    fused = int(np.asarray(sites.fused, dtype=bool).sum())
    return head + (f"; {fused:,} fused (on a tile seam)" if fused else "") + (
        f"; {dimmed:,} outside the Subset" if dimmed else "")


def array_footer(scene, layers, *, label: str, image_note: str = "", note: str = "", dimmed: int = 0) -> list[str]:
    """The lines under the Array view: the page's note, the counts, and what went wrong or is missing — no
    addresses, nothing placed, the image's state, what the Cells layer could not draw, and the anchor's status when it
    is not a plain ``ok`` (``_anchor_owed``). How to read the figure is :func:`array_notes`, drawn only with *Notes*
    on and over the foot of the array rather than here, so turning it on never moves or shrinks the array."""
    sites, in_well = scene.sites, scene.frame == "well"
    lines = [note] if note else []
    if scene.reason:
        lines.append(f"No addresses at this timepoint: {scene.reason}")
    if not len(sites):
        lines.append(f"{scene.well} at {scene.timepoint}: {scene.no_places}, so no nanowell is drawn.")
    lines.append(_head(scene, layers, dimmed))
    if layers.cells and scene.cells_note:
        lines.append(scene.cells_note)
    if in_well and layers.image != "off" and image_note:
        lines.append(image_note)
    if in_well and layers.anchor and scene.anchor_text and _anchor_owed(scene):
        lines += scene.anchor_text.split("\n")
    return lines


def array_notes(scene, layers, *, label: str) -> list[str]:
    """How to read what the Array view draws (the *Notes* layer): each line only when what it explains is on
    screen — the fused sentence only with a fused nanowell drawn, the tile colours only with tiles drawn."""
    sites, in_well = scene.sites, scene.frame == "well"
    lines = [f"Lattice positions at the measured pitch {scene.pitch_um:.1f} µm, so a nanowell keeps its place from "
             "one timepoint to the next (the square is the central nanowell)." if not in_well and scene.pitch_um
             else "Measured positions in the well frame (the square is the central nanowell)."]
    if layers.nanowells and len(sites):
        fused = " Fused (dashed purple): on a tile seam, measured from a blended crop." \
            if np.asarray(sites.fused, dtype=bool).any() else ""
        lines.append(f"Fill: {label}. Grey outline: no value — the words inside say why; hatches and dashes mark "
                     "states (see key)." + fused
                     if layers.colour == "metric" else
                     "Outlined by state, as the key above the array shows." + fused)
    if in_well and layers.tiles and scene.tiles:
        lines.append("Tiles: green placed on the lattice, blue by overlap correlation, amber where the acquisition "
                     "record put them; each labelled with its registration residual.")
    if not in_well and (layers.image != "off" or layers.tiles or layers.anchor):
        lines.append("The image, the tiles and the anchor are in the well frame, not on the lattice: switch Positions "
                     "to Measured to draw them.")
    return lines


def _anchor_owed(scene) -> bool:
    """Whether the anchor's lines say something the reader must see with *Notes* off: a grid status other than
    ``ok``, or a second line (an anchor set by hand, a file that cannot be used, how to set one). A plain
    "A01 at t_c: ok." says only that nothing is wrong."""
    return scene.status != "ok" or "\n" in scene.anchor_text


#: The Array view's footer: where it starts across the figure and its type size in points.
_ARRAY_LEFT, _ARRAY_FOOT_PT = 0.03, 7.5
#: What the colour bar takes to the axes' right besides its own width, in points: the tick numbers ("2.00"), their
#: pads and the label's line, at the type the bar is drawn in.
_BAR_PT = _PREF_PT * GRID_TYPE_SCALE * (1 + 5 * 0.6) + 12.0


def _label_lines(fig) -> int:
    """The footer lines the Array's labels sentence can take, at this figure's width: the longest sentence
    :func:`_draw_labels` writes, broken as the footer breaks it."""
    longest = ("Nanowell addresses and values are written in a nanowell drawn at least 99 px across: zoom in to read "
               "them.")
    return len(_wrapped_to_figure(fig, [longest], left=_ARRAY_LEFT, size_pt=_ARRAY_FOOT_PT)[0])


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
    veiled = sum(1 for site in scene.sites.sites if tuple(site) in dimmed) if dimmed else 0
    # Every footer line is broken to the figure's width (a long one ran off a narrow figure's edge), and the room the
    # lines take follows from the pieces.
    lines, head_parts = _wrapped_to_figure(
        fig, array_footer(scene, layers, label=label, image_note=image_note, note=note, dimmed=veiled),
        left=_ARRAY_LEFT, size_pt=_ARRAY_FOOT_PT)
    # The Notes lines' room is reserved whether Notes is on or off, so turning it on writes into space that was
    # already there and the array neither moves nor shrinks (nor is covered: a box over its foot hid a row).
    notes, _n = _wrapped_to_figure(fig, array_notes(scene, layers, label=label), left=_ARRAY_LEFT,
                                   size_pt=_ARRAY_FOOT_PT)
    step = _line_step(fig, _ARRAY_FOOT_PT)
    # The colour bar's tick numbers and label sit to the axes' right, and take the room they need in points: a share
    # of the figure's width left them, on a narrow figure, with the label on the figure's edge.
    bar_room = 0.024 + _BAR_PT / max(_figure_pt(fig)[0], 1.0) + 0.01
    right = 0.62 if layers.record else min(0.86, 0.99 - bar_room)
    # The key sits above the axes, one row per KEY_COLUMNS entries: the axes start lower for each row past the first,
    # or a key naming many causes ran off the top of the figure.
    entries = (key_entries(scene.sites, colour=layers.colour, styles=_STATE_STYLE, why=scene.why, dimmed=veiled)
               if layers.nanowells and len(scene.sites) else [])
    key_rows = -(-len(entries) // KEY_COLUMNS)
    drawn = _array_axes(fig, scene, image, region, right=right, top=0.92 - max(key_rows - 1, 0) * _KEY_ROW,
                        bottom=_FOOTER_BASE + (len(lines) + len(notes)
                                               + (_label_lines(fig) if layers.addresses or layers.values else 0)
                                               + 0.5) * step)
    ax = drawn.ax
    drawn.sites, drawn.scene, drawn.label = scene.sites, scene, label
    ax._aw_hover_label = _hover_label(scene)
    _bg, ink, muted = _inks(ax)
    if image is not None:
        _draw_image(ax, drawn, image)
    if in_well and layers.tiles:
        _draw_tiles(ax, scene.tiles, muted)
    if layers.nanowells and len(scene.sites):
        _draw_nanowells(fig, ax, drawn, scene, layers, label=label, selected=selected)
        _dim_nanowells(ax, scene.sites, dimmed)
        draw_key(ax, entries, linewidth=1.4, fontsize=7.0, ink=_inks(ax)[1])
    if layers.cells and scene.cells is not None and len(scene.cells[0]):
        _draw_cells(ax, *scene.cells)
    if (layers.addresses or layers.values) and len(scene.sites):
        filled = (np.isfinite(np.asarray(scene.sites.values, dtype=float))     # as _fill_by_metric fills them
                  if layers.nanowells and layers.colour == "metric" else None)
        lines += _wrapped_to_figure(
            fig, _draw_labels(fig, ax, drawn, scene.sites, addresses=layers.addresses, values=layers.values,
                              why=scene.why, filled=filled), left=_ARRAY_LEFT, size_pt=_ARRAY_FOOT_PT)[0]
    if in_well and layers.anchor and scene.anchor is not None:
        _draw_anchor(ax, drawn, scene.anchor)
    _ink(ax.set_title(f"{scene.name or scene.well} · {scene.timepoint}", fontsize=9, loc="left", color=ink))
    # The footer's lines start straight under the array, where they are with Notes off; the notes go under them, in
    # the reserved room at the foot, so turning Notes on never moves a line being read (UX review, round 3).
    top = len(lines) + len(notes) - 1
    for i, text in enumerate(lines):
        _ink(fig.text(_ARRAY_LEFT, _FOOTER_BASE + (top - i) * step, text, fontsize=_ARRAY_FOOT_PT,
                      color=ink if i < head_parts else muted, gid="nw-array-footer"), muted=i >= head_parts)
    if layers.notes:
        for j, text in enumerate(notes):
            _ink(fig.text(_ARRAY_LEFT, _FOOTER_BASE + (top - len(lines) - j) * step, text, fontsize=_ARRAY_FOOT_PT,
                          color=muted, gid="nw-array-notes"), muted=True)
    if layers.record:
        _draw_record(fig, scene.record, right + max(0.08, bar_room), ink, muted)
    return drawn


def _hover_label(scene):
    """What the canvas's readout chip leads with over a nanowell: its address, state and value
    (``array_hover_compute.short_text``); nothing off the nanowells."""
    def label(x, y) -> str | None:
        k = ahc.site_at(scene.sites, x, y)
        if k is None:
            return None
        site = scene.sites.sites[k]
        return ahc.short_text(site, scene.sites.values[k], str(scene.sites.state[k]),
                              scene.why.get((site[1], site[2]), ""))
    return label


def _array_axes(fig, scene, image, region, *, right: float, bottom: float, top: float = 0.92) -> ArrayDrawn:
    """The axes, looking at *region* (or at everything drawn) widened to their shape, so pixels stay square."""
    ax = fig.add_axes([0.03, bottom, right - 0.03, top - bottom])
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
    if isinstance(region, CentreZoom):
        # Centred on the whole-well montage itself, not on the padded union with the nanowells drawn around it, or
        # on the point the Resolve walk was told the square is near.
        cx, cy = region.at if region.at is not None else _centre(_image_extent(image) if image is not None else fit)
        half_w, half_h = (fit[2] - fit[0]) / (2 * region.factor), (fit[3] - fit[1]) / (2 * region.factor)
        x0, y0, x1, y1 = cx - half_w, cy - half_h, cx + half_w, cy + half_h
    else:
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
        _fill_by_metric(fig, ax, sites, fused, outline, label, why=scene.why)
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


def _fill_by_metric(fig, ax, sites, fused, outline: float, label: str, *, why: dict | None = None) -> None:
    """Filled by the metric with a colour bar; one without a value a grey outline styled by its state, or — measured
    with no value — by why it has none (*why*, ``_array_key.no_value_marks``), so each reason the key names is a
    mark of its own on the array."""
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
    grey = Colors.plot_undefined_ink
    bare = ~has & np.isin(states, ["measured", "empty"])
    if bare.any():
        for _reason, _n, mask, hatch in no_value_marks(sites, bare, why or {}):
            _state_marks(ax, sites, fused, outline, mask, hatch, "solid", grey)
    for state, (hatch, style) in _STATE_STYLE.items():
        _state_marks(ax, sites, fused, outline, ~has & (states == state), hatch, style, grey)


def _state_marks(ax, sites, fused, outline: float, mask, hatch: str, style: str, colour) -> None:
    """The nanowells in *mask* drawn with one state's mark: a ring in *style* with *hatch* inside, in *colour*. A fused nanowell's ring is the fused one, dashed in purple, drawn by the
    caller; it still carries its state's hatch here, so a fused nanowell excluded as out of focus, occluded or in an
    excluded field still says so."""
    for part, ring in ((mask & ~fused, True), (mask & fused, False)):
        if not part.any() or (not ring and not hatch):
            continue
        coll = _ellipses(ax, sites, part, facecolors="none", edgecolors=colour, linewidths=outline if ring else 0.0,
                         zorder=3)
        coll.set_linestyle(style)
        if hatch:
            coll.set_hatch(hatch)


def _outline_by_state(ax, sites, fused, outline: float) -> None:
    """Each nanowell outlined in its state's colour, with its state's mark (``_STATE_STYLE``) — so two states that
    share the muted colour (absent, not located, unaddressed, identity contradicted, field excluded, rim out of
    focus) are still told apart."""
    from ui.nanowell_sites import state_colour

    states = np.array([str(s) for s in sites.state], dtype=object)
    for state in sorted(set(states)):
        hatch, style = _STATE_STYLE.get(state, ("", "solid"))
        _state_marks(ax, sites, fused, outline, states == state, hatch, style, state_colour(state))

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


def site_label_texts(sites, *, addresses: bool, values: bool, why: dict | None = None) -> list[str]:
    """What each nanowell's label says: its ``q,r`` (the *Addresses* layer) over its value of the page's metric
    (the *Values* layer), whichever of the two it has; ``""`` where it has neither. A nanowell without a value writes,
    with *Values* on, the tag of why it has none (*why*, ``array_compute.no_value_reasons``; ``_array_key.reason_tag``)
    — never a number — so two no-value nanowells for different reasons can be told apart on the array."""
    why = why or {}
    out = []
    for site, address, value in zip(sites.sites, sites.labels, sites.values):
        said = value_text(value) or reason_tag(why.get((site[1], site[2]), ""))
        parts = ([address] if addresses and address else []) + ([said] if values else [])
        out.append("\n".join(p for p in parts if p))
    return out


def _draw_labels(fig, ax, drawn, sites, *, addresses: bool, values: bool, why: dict | None = None,
                 filled=None) -> list[str]:
    """Each nanowell's label (its address, its value, or both) in view inside a nanowell drawn large enough to
    hold it; a footer line when none is, or when too many are to write."""
    from ui.nanowell_sites import LABEL_COLOUR, MIN_LABEL_DIAMETER_PX

    x0, y0, x1, y1 = drawn.region
    diameter = 2 * sites.radius * drawn.size_px[0] / max(x1 - x0, 1e-9)
    from matplotlib import patheffects

    labels = np.array(site_label_texts(sites, addresses=addresses, values=values, why=why), dtype=object)
    # A halo of the ground under a label in a nanowell drawn without a fill: over its hatch (the out-of-focus dots, the
    # occluded lines) the bare label could not be read. *filled* (one bool per site) marks the nanowells filled by the
    # metric, whose labels read on the fill and are left plain — a dark stroke there only roughened them.
    halo = [patheffects.withStroke(linewidth=3.5, foreground=_inks(ax)[0])]
    filled = np.zeros(len(labels), dtype=bool) if filled is None else np.asarray(filled, dtype=bool)
    # Over a hatch a halo still loses to the pattern (UX review, round 3): a hatched nanowell's label sits on a small
    # solid patch of the ground instead.
    hatched = np.array([bool(_STATE_STYLE.get(str(state), ("", ""))[0]) for state in sites.state], dtype=bool)
    backing = {"boxstyle": "round,pad=0.15", "facecolor": _inks(ax)[0], "edgecolor": "none", "alpha": 0.9}
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
    shown = np.nonzero(show)[0]
    sizes = np.array([max(5.0, min(8.0, diameter[k] * (0.22 if labels[k].count("\n") == 0 else 0.17) * 72.0 / fig.dpi))
                      for k in shown])
    # Two labels written over one another read as neither: where a label would overlap one already written, it is
    # left out, and the footer says how many (the nanowell is still there, and a click still finds it).
    px_per_pt = fig.dpi / 72.0
    scale_px = drawn.size_px[0] / max(x1 - x0, 1e-9)
    widths = np.array([max(len(part) for part in labels[k].split("\n")) for k in shown]) * _LABEL_EM * sizes * px_per_pt
    heights = np.array([labels[k].count("\n") + 1 for k in shown]) * _LABEL_LEADING * sizes * px_per_pt
    kept = _spaced(sites.x[shown] * scale_px, sites.y[shown] * scale_px, widths, heights, pad=2.0)
    for k, size, keep in zip(shown, sizes, kept):
        if not keep:
            continue
        backed = hatched[k] and not filled[k]
        ax.text(sites.x[k], sites.y[k], labels[k], ha="center", va="center", clip_on=True, zorder=6,
                fontsize=size, color=LABEL_COLOUR, linespacing=1.0, gid="nw-array-address",
                path_effects=[] if filled[k] or backed else halo, bbox=backing if backed else None)
    if not kept.all():
        left_out = int((~kept).sum())
        return [f"{left_out:,} nanowell {what} left out where they would overlap their neighbours: zoom in to read "
                "them."]
    return []


#: A label's glyph width and a line's height in units of its type size, for placing labels without drawing them.
_LABEL_EM, _LABEL_LEADING = 0.6, 1.15


def _spaced(x, y, w, h, *, pad: float = 0.0):
    """Which of the boxes (centres *x*, *y*; sizes *w*, *h*, all in pixels) are kept, taken in order: a box is kept
    unless, with *pad* pixels added round it, it overlaps one already kept."""
    keep = np.zeros(len(x), dtype=bool)
    for i in range(len(x)):
        if not keep.any() or not (
                (np.abs(x[keep] - x[i]) < (w[keep] + w[i]) / 2 + pad)
                & (np.abs(y[keep] - y[i]) < (h[keep] + h[i]) / 2 + pad)).any():
            keep[i] = True
    return keep


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


#: The share of the figure's width the subset label may take at its top left, clear of the key at its top right.
_SUBSET_LABEL_SHARE = 0.5


def stamp_subset(fig, text: str) -> None:
    """Write *text* (``subset_compute.figure_label``) at the figure's top left, in the secondary ink, after the view is
    drawn: every view clears the figure first, so the label goes on last. Nothing with no subset (``""``).

    It is on the figure rather than only in the page's Details field, because a figure saved, exported or put on a
    slide leaves Details behind, and a figure of a subset that does not say so reads as one of every nanowell."""
    if not text:
        return
    from types import SimpleNamespace

    _bg, _fg, muted = _inks(SimpleNamespace(figure=fig))     # the tokens are the figure's, read through any axes
    width_pt = _figure_pt(fig)[0] * _SUBSET_LABEL_SHARE
    _ink(fig.text(0.01, 0.995, _fit_text(text, width_pt, FOOTER_PT), ha="left", va="top", fontsize=FOOTER_PT,
                  color=muted, gid="nw-subset-label"), muted=True)


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
def _named(item) -> str:
    """What a figure calls a panel's or a line's well: the name the page gave it, else the well itself."""
    return item.name or item.well


#: The gid of the line drawn over a selected nanowell's own trajectory.
TRAJECTORY_SELECTED_GID = "nw-selected-trajectory"


def render_trajectories(fig, panels, *, label: str, time_source: str | None = None, note: str = "",
                        cohort: bool = False, selected=()) -> None:
    """One panel per well: a thin line per nanowell, a thick median per occupancy class at baseline, and each
    *selected* nanowell's own line drawn over them in its selection colour (``selection_colours``, the colour its
    ring has on the Distribution and Array views), so a nanowell gone to is found among the thousands.

    The panels share one y scale, labelled on the left column, and one time axis: recorded hours as numbers, the
    axis labelled once per column under the bottom row — the timepoint names at their true hours ran into each
    other wherever two were imaged close together — and the occupancy key at the top right."""
    from matplotlib.collections import LineCollection

    from widgets.plot_card import theme_paired_colours

    fig.clear()
    shown = panels[:MAX_PANELS]
    rows, cols = _grid(len(shown))
    top, key_bottom, key_h = _head_room(fig, floor_top=0.88)
    _adjust(fig, left=0.07, right=0.98, top=top, bottom=0.10 + _FOOTER_BASE + 2 * _line_step(fig), wspace=0.12,
                        hspace=0.42)
    axes = [fig.add_subplot(rows, cols, i + 1) for i in range(len(shown))]
    inks = _inks(axes[0] if axes else fig.add_subplot(1, 1, 1))
    _bg, ink, muted = inks
    scale = GRID_TYPE_SCALE if len(shown) > 1 else SINGLE_TYPE_SCALE
    in_hours = bool(shown) and all(p.in_hours for p in shown)
    # Hours or order is decided per well (from that well's own timepoints), so a page can hold both: each panel is
    # then ticked and labelled by its own axis, and the x range is shared only among panels on the same one — one
    # range over both squeezed the order panel into a few per cent of its width.
    mixed = len({p.in_hours for p in shown}) > 1
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
        _draw_selected_trajectories(ax, panel, selected)
        span = float(np.ptp(panel.times)) if len(panel.times) > 1 else 1.0
        marked_kinds.update(panel.marks.values())
        mark_timepoints(ax, panel.times, panel.marks, _mark_colours(muted), width=0.04 * span or 0.3,
                        fontsize=ANNOTATION_PT, ground=inks[0])
        _trajectory_ticks(ax, panel)
        _no_offset(ax)
        _ink(ax.set_title(_named(panel), fontsize=10, fontweight="bold", loc="left", color=ink))
        ax.grid(alpha=0.3)
        drawn += len(panel.lines)
        without += panel.n_without_value
    _share_limits(axes, x_groups=[[ax for ax, p in zip(axes, shown) if p.in_hours == kind] for kind in (True, False)])
    for i, (ax, panel) in enumerate(zip(axes, shown)):
        if i % cols:
            ax.tick_params(labelleft=False)
        else:
            _ink(ax.set_ylabel(_wrap_label(label, rows), color=ink))
        if i + cols >= len(axes) or mixed:
            _ink(ax.set_xlabel("Time (h)" if panel.in_hours else "Timepoint (order; times unknown)", color=ink))
    if axes:
        _occupancy_key(fig, "Thick lines · cells at baseline", at=(key_bottom, key_h))
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
    # time axis's label), as the Distribution view's footer does; each line is broken to the figure's width.
    lines, _first = _wrapped_to_figure(fig, lines)
    lines = _footer_that_fits(fig, lines)
    step = _line_step(fig)
    _adjust(fig, bottom=0.10 + _FOOTER_BASE + step * max(2, len(lines)))
    for i, text in enumerate(reversed(lines)):
        _ink(fig.text(0.07, _FOOTER_BASE + i * step, text, fontsize=FOOTER_PT, color=muted, gid="nw-traj-footer"),
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


def _trajectory_ticks(ax, panel) -> None:
    """A Trajectories panel's x ticks: whole hours when the panel is drawn in hours, else one per timepoint, named."""
    from matplotlib.ticker import MaxNLocator

    if panel.in_hours:
        ax.xaxis.set_major_locator(MaxNLocator(nbins=6, integer=True, min_n_ticks=3))
    else:
        ax.set_xticks(panel.times)
        ax.set_xticklabels(panel.labels)


def _share_limits(axes, x_groups=None) -> None:
    """One y range over *axes*, and one x range over each of *x_groups* (lists of those axes; all of them when
    ``None``), each the union of what their data asked for."""
    if not axes:
        return
    for ax in axes:
        ax.autoscale_view()
    ys = [ax.get_ylim() for ax in axes]
    for group in (x_groups if x_groups is not None else [axes]):
        xs = [ax.get_xlim() for ax in group]
        for ax in group:
            ax.set_xlim(min(x[0] for x in xs), max(x[1] for x in xs))
    for ax in axes:
        ax.set_ylim(min(y[0] for y in ys), max(y[1] for y in ys))


# ── position effects (V6) ────────────────────────────────────────────────────
def _draw_selected_trajectories(ax, panel, selected) -> None:
    """Each selected nanowell of *panel*'s well over its thin line: a line through every value it has (a gap where it
    has none), with a marker at each, in the nanowell's selection colour, above the class medians."""
    colours = {tuple(site): colour for site, colour in zip(map(tuple, selected), selection_colours())}
    for site, _occ, y in panel.lines:
        colour = colours.get(tuple(site))
        if colour is None:
            continue
        ax.plot(panel.times, y, color=colour, linewidth=2.0, marker="o", markersize=3.5, zorder=6,
                gid=TRAJECTORY_SELECTED_GID)


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
    lines = ["Median and interquartile range over the nanowells with a value in each ring; the bars are the share "
             "of each ring's nanowells without one. Descriptive: no test is made."]
    if note:
        lines.insert(0, note)
    lines, _first = _wrapped_to_figure(fig, lines, left=0.08)
    lines = _footer_that_fits(fig, lines)
    step = _line_step(fig)
    # The ring label is broken to the width of the axes it sits under, and the second line is room the axes give up.
    ring_label = _fit_text("Ring (lattice steps from the central nanowell)", _figure_pt(fig)[0] * (0.88 - 0.08),
                           _PREF_PT * SINGLE_TYPE_SCALE)
    extra = (ring_label.count("\n") * 1.2 * _PREF_PT * SINGLE_TYPE_SCALE) / max(_figure_pt(fig)[1], 1.0)
    _adjust(fig, left=0.08, right=0.88, top=0.90,
                        bottom=0.10 + extra + _FOOTER_BASE + step * max(1, len(lines)))
    for panel in (ax, share):
        _prepare_axes(panel, SINGLE_TYPE_SCALE)
    _bg, ink, muted = _inks(ax)
    width = 0.8 / max(len(series), 1)
    for k, s in enumerate(series):
        # The well's plate-position colour, as the plate selector and every plot tab colour it: picked by the
        # well's place in this list, A03 drawn alone took A01's colour and no well matched its swatch on the plate.
        colour = color_for_well(s.well)
        ax.fill_between(s.rings, s.q1, s.q3, color=colour, alpha=0.18, linewidth=0)
        ax.plot(s.rings, s.median, color=colour, marker="o", markersize=3, linewidth=1.4, label=_named(s))
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
    share.set_xlabel(ring_label, color=ink)
    share.grid(alpha=0.3)
    fig.align_ylabels([ax, share])
    for i, text in enumerate(reversed(lines)):
        _ink(fig.text(0.08, _FOOTER_BASE + i * step, text, fontsize=FOOTER_PT, color=muted, gid="nw-position-footer"),
             muted=True)
