"""Draw the rare-responder analysis: the ECDFs with the threshold, the exceedance counts over the control null, the
Higher Criticism curves, the report, and the image crops of every flagged nanowell.

Pure matplotlib onto a figure it is handed; every number comes from ``rare_report_compute`` and is only drawn here.
:func:`render_rare_page` is what the Nanowells page's Statistics view draws for the *Rare responders* test: one row
per test, each holding only that test's plots — a plot of one dot per well with every comparison on one axes, a plot
of curves once per comparison, wrapping at the figure's width — and every comparison drawn, the figure as tall as it
needs. The single-panel functions draw one of them onto a whole figure. Each artist that carries a result has a gid
(``rare-…``), so a test reads the figure back rather than the result. Control wells are drawn in the secondary ink,
each treatment well in its own colour on the plate (``selections_model.color_for_well``), every text in the plot
tokens' inks with the theme tag, so it repaints on a theme flip. The loading strata are a magnitude, not an identity,
so they are drawn in the page's occupancy ramp (the colours the Distribution view gives a nanowell's cell count)
rather than in well colours a reader would take for wells: its screen / publication pair, marked so a capture
repaints it.
"""

from __future__ import annotations

import math
import textwrap
from collections.abc import Callable

import numpy as np

from review.core.plot_style import own_xtick_angle
from review.features.nanowell.rare_data_compute import ALL
from review.features.nanowell import rare_export_compute as rx
from review.features.nanowell.renderer import (
    GRID_TYPE_SCALE, _ink, _inks, _is_publication, _prepare_axes, stamp_height_in,
)

#: Crops drawn at most per figure; the rest are counted in its title.
MAX_CROPS = 48
_TITLE_PT = 8.0
_REPORT_PT = 6.5
_LEADING = 1.45


def _well_colour(key: str) -> str:
    """A treatment well's colour: the one the plate draws it in. *key* is ``plate:well``."""
    from review.domain.selections_model import color_for_well

    return color_for_well(str(key).split(":", 1)[-1])


def _ramp_index(i: int, n: int, size: int) -> int:
    """Shade *i* of *n* ordered strata on a ramp of *size* shades: the top *n* shades when they fit, so with the
    default strata above one cell the shades match the occupancy classes 1, 2 and 3+; spread over the ramp when
    there are more strata than shades."""
    if n <= size:
        return size - n + i
    return min(size - 1, i * size // n)


def _stratum_shades(n: int) -> list[tuple[str, str]]:
    """``(screen, publication)`` per stratum, lowest first, from the occupancy ramp."""
    from ui.theme import Colors, CPub

    size = len(Colors.occupancy_ramp)
    return [(Colors.occupancy_ramp[_ramp_index(i, n, size)], CPub.occupancy_ramp[_ramp_index(i, n, size)])
            for i in range(n)]


def _paired(line, pair: tuple[str, str]):
    from widgets.plot_card import theme_paired_colours

    return theme_paired_colours(line, screen=pair[0], publication=pair[1])


def _ecdf(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.sort(np.asarray(values, dtype=float))
    return x, np.arange(1, len(x) + 1) / len(x)


def _title(ax, text: str, size: float = _TITLE_PT) -> None:
    _ink(ax.set_title(text, fontsize=size, color=_inks(ax)[1]))


def _new_axes(fig, n: int):
    fig.clear()
    axes = fig.subplots(1, max(1, n), squeeze=False)[0]
    for ax in axes:
        _prepare_axes(ax, GRID_TYPE_SCALE)
    return axes


def _strata(unit) -> list:
    return sorted({s for w in unit.controls + unit.treated for s in w.values})


def _pooled(group, stratum, log_axis: bool, sign: int = 1) -> tuple[np.ndarray, np.ndarray] | None:
    """The pooled ECDF of *group* in *stratum*, of the original values (``sign`` times the analysed ones, so a
    bottom-tail analysis is drawn as the values were measured); with *log_axis*, its x as log₂ of the value (a value
    of 0 left out).

    The log is taken here, on a linear axis, rather than by ``set_xscale("log")``: the page's style pass sets each
    axis's scale from the Properties panel after the draw, which put a "(log)" label over a linear axis."""
    parts = [w.values[stratum] for w in group if stratum in w.values]
    if not parts:
        return None
    x, y = _ecdf(sign * np.concatenate(parts))
    if not log_axis:
        return x, y
    keep = x > 0
    return np.log2(x[keep]), y[keep]


def _x(value: float, log_axis: bool) -> float:
    """*value* on the panel's x: its log₂ on the log axis, NaN for a value the log axis cannot place (≤ 0), which is
    then not drawn rather than drawn at the wrong place."""
    if log_axis:
        return math.log2(value) if value > 0 else math.nan
    return value


def _is_ratio(value_label: str) -> bool:
    return "fold change" in value_label.lower()


def _x_label(value_label: str, log_axis: bool) -> str:
    if not log_axis:
        return value_label
    return f"{value_label}, log scale" + (" — 0 is left out" if _is_ratio(value_label) else "")


def _fold_ticks(ax, log_axis: bool, value_label: str = "fold change") -> None:
    """On the log axis, ticks at whole powers of two labelled as the values they are — ``2×``, ``4×`` for a fold
    change, the plain number otherwise — not as their logarithms."""
    if not log_axis:
        return
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    suffix = "×" if _is_ratio(value_label) else ""
    ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"{2 ** v:g}{suffix}"))


def log_axis_for(result) -> bool:
    """Whether the first panel's x is logarithmic: only when every value drawn is positive (a fold change, a count,
    a brightness), never for a robust z or a log metric, which can be negative."""
    if result.prepared.params.normalize:
        return False
    values = result.prepared.kept["fc"].to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    return bool(len(values)) and bool((values > 0).all() or (_is_ratio(result.prepared.value_label)
                                                              and (values >= 0).all()))


def _stratum_name(stratum: str) -> str:
    if str(stratum) == ALL:
        return "all nanowells"
    return f"{stratum} cell at start" if str(stratum) == "1" else f"{stratum} cells at start"


def _cut_x(unit, stratum, log_axis: bool) -> float:
    """The cutoff of *stratum* on the panel's x, as the original value (``unit.sign`` times the analysed one)."""
    return _x(unit.sign * unit.thresholds[stratum], log_axis) if stratum in unit.thresholds else math.nan


def draw_ecdf_stratum(ax, unit, stratum: str, value_label: str, log_axis: bool) -> None:
    """One stratum's pooled control ECDF against its pooled treatment ECDF, the threshold marked."""
    _ground, ink, muted = _inks(ax)
    treated_colour = _well_colour(unit.treated[0].key) if unit.treated else ink
    for group, colour, gid, label in ((unit.controls, muted, "rare-ecdf-control", "control"),
                                      (unit.treated, treated_colour, "rare-ecdf-treatment", unit.treatment)):
        xy = _pooled(group, stratum, log_axis, unit.sign)
        if xy is not None:
            ax.step(*xy, where="post", color=colour, label=label, linewidth=1.2)[0].set_gid(f"{gid}:{stratum}")
    if np.isfinite(_cut_x(unit, stratum, log_axis)):
        ax.axvline(_cut_x(unit, stratum, log_axis), color=ink, linestyle="--", linewidth=1).set_gid(
            f"rare-threshold:{stratum}")
    _fold_ticks(ax, log_axis, value_label)
    _title(ax, _stratum_name(stratum))
    ax.set_xlabel(_x_label(value_label, log_axis))
    ax.set_ylim(0, 1.01)


def _ecdf_colours(unit, stratum, strata, i, publication, muted):
    """``(treatment, control, pair or None)`` colours of one stratum's curves. Stratified, both take the stratum's
    occupancy shade (paired for publication), told apart by solid and dotted; unstratified, the treatment curve
    takes its first well's plate colour and the control curve the secondary ink, as the other panels draw them."""
    if strata == [ALL]:
        return (_well_colour(unit.treated[0].key) if unit.treated else muted), muted, None
    pair = _stratum_shades(len(strata))[i]
    colour = pair[1] if publication else pair[0]
    return colour, colour, pair


def draw_ecdf_strata(ax, unit, value_label: str, log_axis: bool, *, cutoff: bool = True) -> None:
    """Every stratum on one axes (one, unstratified): control dotted, treatment solid, of the values as measured
    (``unit.sign`` undoes a bottom-tail analysis's negation). With *cutoff*, the exceedance test's cutoff dashed — the
    panel of the exceedance row."""
    strata = _strata(unit)
    publication = _is_publication(ax)
    _ground, ink, muted = _inks(ax)
    for i, stratum in enumerate(strata):
        treated_c, control_c, pair = _ecdf_colours(unit, stratum, strata, i, publication, muted)
        for group, style, gid, colour in ((unit.controls, ":", "rare-ecdf-control", control_c),
                                          (unit.treated, "-", "rare-ecdf-treatment", treated_c)):
            xy = _pooled(group, stratum, log_axis, unit.sign)
            if xy is not None:
                line = ax.step(*xy, where="post", color=colour, linestyle=style, linewidth=1.1)[0]
                line.set_gid(f"{gid}:{stratum}")
                # Dotted is the control: the title says so, so the page's Line style never restyles it solid.
                line._aw_linestyle_is_data = True
                if pair is not None:
                    _paired(line, pair)
                elif colour == muted:
                    _ink(line, muted=True)
        cut = _cut_x(unit, stratum, log_axis) if cutoff else math.nan
        if np.isfinite(cut):
            line = ax.axvline(cut, color=control_c if pair is not None else ink, linestyle="--", linewidth=0.8)
            line.set_gid(f"rare-threshold:{stratum}")
            _paired(line, pair) if pair is not None else _ink(line)
    ax.set_xlabel(_x_label(value_label, log_axis))
    ax.set_ylabel("share of nanowells at or below")
    _fold_ticks(ax, log_axis, value_label)
    ax.set_ylim(0, 1.01)
    _label_strata(ax, unit, strata, log_axis, publication, cutoff=cutoff)
    by = "" if strata == [ALL] else ", by starting cells"
    keys = [f"solid: {unit.treatment}", "dotted: control", *(["dashed: the cutoff"] if cutoff else [])]
    _title(ax, f"{value_label[:1].upper() + value_label[1:]}{by}\n{' · '.join(keys)}")


def gap_curve(unit, stratum) -> tuple[np.ndarray, np.ndarray] | None:
    """``(x, gap)`` of *stratum*: at every distinct value *x* (as measured, ``unit.sign`` undoing a bottom-tail
    analysis's negation), the pooled control's share of nanowells at or below it minus the pooled treatment group's.
    Above 0, more of the treatment group lies above that value than of the control. It is the difference the pooled
    two-sided Anderson–Darling test (``rare_ad_compute.scholz_stephens``) squares and sums over every value, so its
    row draws this rather than the two cumulative curves again — the exceedance row already draws those. ``None``
    when either side has no nanowell in *stratum*."""
    parts = {side: [w.values[stratum] for w in group if stratum in w.values]
             for side, group in (("control", unit.controls), ("treated", unit.treated))}
    if not parts["control"] or not parts["treated"]:
        return None
    c = np.sort(unit.sign * np.concatenate(parts["control"]))
    t = np.sort(unit.sign * np.concatenate(parts["treated"]))
    x = np.unique(np.concatenate([c, t]))
    return x, np.searchsorted(c, x, side="right") / len(c) - np.searchsorted(t, x, side="right") / len(t)


def draw_gap(ax, unit, value_label: str, log_axis: bool) -> None:
    """The pooled shape test's panel: each stratum's :func:`gap_curve` as a step line, shaded between it and 0 (gids
    ``rare-pooled-gap-line:<stratum>`` and ``rare-pooled-gap:<stratum>``), on the same x as the exceedance row's
    cumulative panel, with symmetric y limits so a gap either way reads at its size."""
    strata = _strata(unit)
    publication = _is_publication(ax)
    _ground, ink, muted = _inks(ax)
    _ink(ax.axhline(0.0, color=muted, linewidth=0.8, gid="rare-pooled-gap-zero"), muted=True)
    reach = 0.0
    for i, stratum in enumerate(strata):
        curve = gap_curve(unit, stratum)
        if curve is None:
            continue
        x, gap = curve
        if log_axis:
            keep = x > 0
            x, gap = np.log2(x[keep]), gap[keep]
        if not len(x):
            continue
        colour, _control_c, pair = _ecdf_colours(unit, stratum, strata, i, publication, muted)
        line = ax.step(x, gap, where="post", color=colour, linewidth=1.2)[0]
        line.set_gid(f"rare-pooled-gap-line:{stratum}")
        poly = ax.fill_between(x, 0.0, gap, step="post", color=colour, alpha=0.3, linewidth=0, zorder=1)
        poly.set_gid(f"rare-pooled-gap:{stratum}")
        if pair is not None:
            _paired(line, pair)
        reach = max(reach, float(np.abs(gap).max()))
    ax.set_xlabel(_x_label(value_label, log_axis))
    ax.set_ylabel("control − group share")
    _fold_ticks(ax, log_axis, value_label)
    limit = max(0.05, reach * 1.15)
    ax.set_ylim(-limit, limit)
    # Two short lines: the x axis already names the value, and the panel is a third of the row wide.
    by = "" if strata == [ALL] else " · one line per starting count"
    _title(ax, f"gap: control − {unit.treatment}{by}\nabove 0: more {unit.treatment} above · shaded: what the test sums")


def _label_strata(ax, unit, strata, log_axis: bool, publication: bool, *, cutoff: bool = True) -> None:
    """Label the lines on the panel itself rather than in a key: each stratum's treatment curve where it crosses its
    own height (staggered, since every cumulative curve ends at the same top right corner, where labels at the ends
    would pile up), with a dot in the stratum's shade — unstratified, the treatment curve named by its group — and
    the dashed line as the control cutoff."""
    _ground, ink, muted = _inks(ax)
    for i, stratum in enumerate(strata):
        xy = _pooled(unit.treated, stratum, log_axis, unit.sign)
        if xy is None or not len(xy[0]):
            continue
        treated_c, _control_c, pair = _ecdf_colours(unit, stratum, strata, i, publication, muted)
        level = 0.78 - 0.56 * i / (len(strata) - 1) if len(strata) > 1 else 0.5   # spread over the panel's height
        x = float(xy[0][min(int(np.searchsorted(xy[1], level)), len(xy[0]) - 1)])
        dot = ax.scatter([x], [level], s=14, color=treated_c, zorder=5, gid=f"rare-ecdf-dot:{stratum}")
        if pair is not None:
            _paired(dot, ((pair[0], pair[0]), (pair[1], pair[1])))
        name = unit.treatment if stratum == ALL else _stratum_name(stratum)
        _ink(ax.annotate(name, (x, level), textcoords="offset points", xytext=(6, 0), va="center",
                         fontsize=_TITLE_PT * 0.75, color=ink, gid=f"rare-ecdf-label:{stratum}"))
    cuts = [c for c in (_cut_x(unit, s, log_axis) for s in strata) if np.isfinite(c)] if cutoff else []
    if cuts:
        # At the foot of the dashed line, clear of the stratum labels, which sit at mid-height.
        _ink(ax.annotate("control cutoff", (max(cuts) if unit.sign > 0 else min(cuts), 0.02), textcoords="offset points",
                         xytext=(-3, 0), rotation=90, ha="right", va="bottom", fontsize=_TITLE_PT * 0.7, color=ink,
                         gid="rare-cutoff-label"))


#: Points a dot's count label takes above the dot: its 6 pt offset and one line of its type, with leading.
_P_LABEL_PT = 6 + _TITLE_PT * 0.75 * 1.4


def _headroom(ax, labelled) -> None:
    """Raise *ax*'s top so the p label over each of the *labelled* y values stays inside the axes rather than running
    into the panel's title. The room is in points, so it is taken as a share of the axes' own height: a margin in data
    units left a short phone panel's label above it."""
    tops = [v for v in labelled if np.isfinite(v)]
    axes_in = ax.get_position().height * ax.figure.get_figheight()
    if not tops or axes_in <= 0:
        return
    share = min(0.6, _P_LABEL_PT / 72 / axes_in)
    low, high = ax.get_ylim()
    need = (max(tops) - share * low) / (1 - share)
    if need > high:
        ax.set_ylim(low, need)


def draw_exceedance(ax, unit) -> None:
    """Each well's share of nanowells past the cutoff, labelled with its count (``4/60``); control wells (each
    scored leave-one-out) as the band chance gives, treatment wells in their plate colours with an ink outline, so
    a dark plate colour still shows on the dark card. Each treatment well's own p is in Details: with three control
    wells its floor is 0.25, and drawn beside a group p of 0.0007 it read as a contradiction."""
    _ground, ink, muted = _inks(ax)
    counts = unit.primary.counts
    controls = [c for c in counts if not c.treated]
    treated = [c for c in counts if c.treated]
    ctrl_rates = np.array([c.rate for c in controls], dtype=float) * 100
    if ctrl_rates.size and np.isfinite(ctrl_rates).any():
        ax.axhspan(np.nanmin(ctrl_rates), np.nanmax(ctrl_rates), color=muted, alpha=0.15).set_gid("rare-null-band")
        ax.axhline(np.nanmean(ctrl_rates), color=muted, linestyle=":", linewidth=1).set_gid("rare-null-mean")
    from widgets.plot_card import plot_tokens

    xs = np.arange(len(counts))
    ax.scatter(xs[:len(controls)], ctrl_rates, color=muted, zorder=3, gid="rare-exceed-control")
    for x, c in zip(xs, controls):
        _ink(ax.annotate(f"{c.k}/{c.n}", (x, c.rate * 100), textcoords="offset points", xytext=(0, 6), ha="center",
                         fontsize=_TITLE_PT * 0.7, color=muted, gid="rare-exceed-count"), muted=True)
    outline = {mode: plot_tokens(mode)[1] for mode in ("screen", "publication")}
    for i, c in enumerate(treated):
        x, colour = len(controls) + i, _well_colour(c.key)
        dot = ax.scatter([x], [c.rate * 100], color=colour, edgecolors=outline[
            "publication" if _is_publication(ax) else "screen"], linewidths=0.8, zorder=4,
            gid=f"rare-exceed-treated:{c.key}")
        _paired(dot, ((colour, outline["screen"]), (colour, outline["publication"])))   # (faces, edges) per ground
        _ink(ax.annotate(f"{c.k}/{c.n}", (x, c.rate * 100), textcoords="offset points", xytext=(0, 6), ha="center",
                         fontsize=_TITLE_PT * 0.75, color=ink, gid="rare-exceed-count"))
    ax.set_xticks(xs, [c.key.split(":", 1)[-1] for c in controls + treated], rotation=60,
                  fontsize=_TITLE_PT * 0.75)
    ax.set_ylabel("% of the well's nanowells")
    rates = [c.rate * 100 for c in counts if np.isfinite(c.rate)]
    ax.set_ylim(0, max(1.0, max(rates, default=0) * 1.05))       # a share is never negative, and an all-zero plate
    _headroom(ax, rates)                                           # keeps its dots on the floor, not mid-panel
    _title(ax, "Exceedance test: share of each well's nanowells past the cutoff")


def draw_hc(ax, unit, *, heading: bool = True) -> None:
    """The HC curve over each well's nanowells, most extreme first: control wells (leave-one-out) in the secondary
    ink, treatment wells in their plate colours, a dot at each curve's peak. A treatment well with no curve (HC
    undefined for it) is named in the title with why, rather than silently missing. *heading* off leaves the test's
    name and p out of the title, for the page, whose row heading and caption say them."""
    _ground, _ink_colour, muted = _inks(ax)
    for w in unit.controls:
        h = unit.hc[w.key]
        if len(h.curve_x):
            ax.plot(h.curve_x, h.curve_y, color=muted, linewidth=0.8, gid=f"rare-hc-control:{w.key}")
    missing = []
    for w in unit.treated:
        h, colour = unit.hc[w.key], _well_colour(w.key)
        if len(h.curve_x):
            ax.plot(h.curve_x, h.curve_y, color=colour, linewidth=1.3, gid=f"rare-hc-treated:{w.key}")
            ax.scatter([h.i_star / h.n], [h.value], color=colour, zorder=4, gid=f"rare-hc-istar:{w.key}")
        else:
            missing.append(f"{w.key.split(':', 1)[-1]} ({h.reason.removeprefix('no HC score: ')})" if h.reason
                           else w.key.split(":", 1)[-1])
    ax.set_xlabel("share of the well's nanowells, most extreme first")
    ax.set_ylabel("excess score")
    perm = unit.hc_perm
    p = perm.p if perm is not None else math.nan
    if np.isfinite(p):
        floor = " (lowest possible)" if np.isfinite(perm.min_p) and p <= perm.min_p * (1 + 1e-9) else ""
        head = f"Higher Criticism: p = {p:.3g}{floor}"
    else:
        head = "Higher Criticism: no group p"
    sub = f"colour: {unit.treatment} wells · grey: control"
    lines = [*([head] if heading else []), sub,
             *(textwrap.wrap(f"no HC score: {'; '.join(missing)}", 70) if missing else [])]
    _title(ax, "\n".join(lines))


def render_ecdf(fig, unit, value_label: str, *, log_axis: bool = True) -> None:
    """Per loading stratum, one panel each."""
    strata = _strata(unit)
    axes = _new_axes(fig, len(strata))
    for ax, s in zip(axes, strata):
        draw_ecdf_stratum(ax, unit, s, value_label, log_axis)
    axes[0].set_ylabel("cumulative fraction")
    _ink(fig.suptitle(f"{unit.label}: ECDF by loading stratum, threshold = {unit.primary.percentile:g}th "
                      "percentile of control", fontsize=_TITLE_PT, color=_inks(axes[0])[1]))


def render_exceedance(fig, unit) -> None:
    draw_exceedance(_new_axes(fig, 1)[0], unit)


def render_hc(fig, unit) -> None:
    draw_hc(_new_axes(fig, 1)[0], unit)


def _wrapped(fig, lines, width_frac: float, pt: float = None, indent: str = "   ", *, bold: bool = False) -> list[str]:
    """*lines* broken between words so each drawn line fits *width_frac* of the figure's width at *pt* (the report's
    size), measured in the face the figure draws with. Measured rather than estimated from an average character: the
    estimate was right for the bundled Inter and short by a fifth for DejaVu Sans, matplotlib's fallback where the
    bundled face is not registered, and the figure's widest lines ran off its right edge. A single word wider than the
    room keeps its own line rather than being cut."""
    from matplotlib.backends.backend_agg import RendererAgg
    from matplotlib.font_manager import FontProperties

    pt = pt or _REPORT_PT
    room = fig.get_figwidth() * width_frac * 72                       # points
    renderer = RendererAgg(1, 1, 72)                                   # 72 dpi: a width in pixels is one in points
    prop = FontProperties(size=pt, weight="bold" if bold else "normal")

    def width_of(text: str) -> float:
        return renderer.get_text_width_height_descent(text, prop, ismath=False)[0]

    out = []
    for line in lines:
        words, current = line.split(), ""
        if not words:
            out.append("")
            continue
        for word in words:
            trial = f"{current} {word}" if current else word
            if current and width_of(trial) > room:
                out.append(current)
                current = indent + word
            else:
                current = trial
        out.append(current)
    return out


def _shared_controls(units) -> list:
    """The control wells of every comparison in *units*, each once, in the order the comparisons name them."""
    seen = {}
    for unit in units:
        for w in unit.controls:
            seen.setdefault(w.key, (unit, w))
    return list(seen.values())


def draw_well_dots(ax, result, value_of, ylabel: str, gid: str, *, label_of=None, missing: str = "no value",
                   p_of=None, units=None) -> None:
    """One dot per well for every comparison on one axes: the control wells once, as the grey band chance gives, then
    each group's wells in their plate colours, a thin rule between groups and the group's name — with its p, from
    *p_of* — over them. *value_of(unit, well)* is the dot's height; a well it has no value for (NaN) is written in as
    *missing* at the axis' foot rather than left out; *label_of(unit, well)*, when given, labels each dot (the
    exceedance counts). Each dot's gid is ``<gid>-control`` / ``<gid>-treated:<well key>``. *units* draws those
    comparisons only (all of them by default): a figure too narrow for every well at a readable spacing draws them
    on several such panels (:func:`well_dot_chunks`)."""
    from widgets.plot_card import plot_tokens

    _ground, ink, muted = _inks(ax)
    publication = _is_publication(ax)
    outline = {mode: plot_tokens(mode)[1] for mode in ("screen", "publication")}
    units = result.units if units is None else units
    controls = _shared_controls(units)
    ctrl = np.array([value_of(u, w) for u, w in controls], dtype=float)
    finite = ctrl[np.isfinite(ctrl)]
    if finite.size:
        ax.axhspan(finite.min(), finite.max(), color=muted, alpha=0.15).set_gid(f"{gid}-band")
    xs = list(range(len(controls)))
    ax.scatter(xs, ctrl, color=muted, zorder=3, gid=f"{gid}-control")
    ticks = [w.key.split(":", 1)[-1] for _u, w in controls]
    labelled = [(x, v, label_of(u, w)) for x, v, (u, w) in zip(xs, ctrl, controls)] if label_of else []
    control = result.prepared.params.control
    groups = [(control if "control" in control.lower() else f"{control} (control)", 0, len(controls) - 1)]
    tops = list(finite)
    x = len(controls) + 1
    for unit in units:
        start = x
        for w in unit.treated:
            value, colour = float(value_of(unit, w)), _well_colour(w.key)
            if math.isfinite(value):
                dot = ax.scatter([x], [value], color=colour, edgecolors=outline["publication" if publication
                                                                               else "screen"],
                                 linewidths=0.8, zorder=4, gid=f"{gid}-treated:{w.key}")
                _paired(dot, ((colour, outline["screen"]), (colour, outline["publication"])))
                tops.append(value)
                if label_of:
                    labelled.append((x, value, label_of(unit, w)))
            else:
                _ink(ax.annotate(missing, (x, 0), xycoords=("data", "axes fraction"), textcoords="offset points",
                                 xytext=(0, 3), rotation=90, ha="center", va="bottom", fontsize=_TITLE_PT * 0.7,
                                 color=ink, gid=f"{gid}-missing:{w.key}"))
            xs.append(x)
            ticks.append(w.key.split(":", 1)[-1])
            x += 1
        groups.append((unit.label + (f"\n{p_of(unit)}" if p_of else ""), start, x - 1))
        x += 1
    # Points of width each well has on the axes. Packed tightly (many wells on a phone), a count label ("15/150",
    # about six characters) is wider than its well's slot, so it stands upright over its dot, a line's height wide;
    # the well names stand upright too rather than slanting into their neighbours.
    slot_pt = ax.get_position().width * ax.figure.get_figwidth() * 72 / max(x - 0.6, 1.0)
    count_pt = _TITLE_PT * 0.65
    upright = slot_pt < count_pt * 0.55 * 6 + 2
    for xi, value, text in labelled:
        _ink(ax.annotate(text, (xi, value), textcoords="offset points", xytext=(0, 5), ha="center", va="bottom",
                         rotation=90 if upright else 0, fontsize=count_pt, color=ink, gid=f"{gid}-count"))
    tick_pt = _TITLE_PT * 0.75
    angle, align = (90, "center") if slot_pt < tick_pt * 2.5 else (60, "right")
    ax.set_xticks(xs, ticks, rotation=angle, ha=align, fontsize=tick_pt)
    # The page's style pass flattens x tick labels to the Properties angle unless the renderer records its own: the
    # well names were drawn upright and then laid flat into one another on a phone.
    own_xtick_angle(ax, angle, align)
    ax.set_xlim(-0.7, x - 1.3)
    ax.set_ylabel(ylabel)
    low = min(0.0, min(tops, default=0.0))
    high = max(tops, default=1.0) or 1.0
    if low < 0:                                      # a negative score (an HC peak) keeps its whole dot on the axes
        low -= 0.06 * (high - low)
    # Each group's name is wrapped to its own width on the axes, so the names of narrow groups on a phone's figure
    # stack rather than run into one another; the dots get the height the tallest name leaves.
    pt = _TITLE_PT * 0.8
    per_slot_in = ax.get_position().width * ax.figure.get_figwidth() / max(x - 0.6, 1.0)
    names = [[part for line in name.split("\n")
              for part in textwrap.wrap(line, max(6, int((last - first + 1.6) * per_slot_in * 72 / (pt * 0.55))),
                                        break_long_words=False)]
             for name, first, last in groups]
    tallest = max((len(n) for n in names), default=1)
    axes_in = max(ax.get_position().height * ax.figure.get_figheight(), 0.3)
    # Above the highest dot: its count label (upright, as long as its text; else one line) and the group names.
    longest = max((len(text) for _x, _v, text in labelled), default=0)
    label_pt = (longest * count_pt * 0.6 if upright else count_pt * 1.3) + 5 if labelled else 0
    share = min(0.75, (pt * _LEADING * tallest + 8 + label_pt) / 72 / axes_in)
    ax.set_ylim(low, low + (high - low) * 1.08 / (1 - share))
    for (_name, first, last), wrapped in zip(groups, names):
        if first:
            ax.axvline(first - 1, color=muted, linewidth=0.6, alpha=0.6).set_gid(f"{gid}-rule")
        _ink(ax.text((first + last) / 2, 0.98, "\n".join(wrapped), transform=ax.get_xaxis_transform(), ha="center",
                     va="top", fontsize=pt, fontweight="bold", color=ink, linespacing=_LEADING, gid=f"{gid}-group"))


#: The least width one well's slot may have on a shared panel, in points: a 9 pt label (the page's size) stood
#: upright is about 11 pt across, and two neighbours closer than that read as one ("A01A02A03").
MIN_SLOT_PT = 13.0


def well_dot_chunks(units, axes_in: float) -> list:
    """*units* split into the runs that each fit one shared panel *axes_in* wide with every well's slot at least
    :data:`MIN_SLOT_PT`: each run's control wells, its groups' wells and a gap between groups, as
    :func:`draw_well_dots` lays them out. One run when they all fit; a run is never empty, so a comparison too
    large for any panel still gets one of its own."""
    capacity = max(1.0, axes_in * 72 / MIN_SLOT_PT)
    chunks, current = [], []

    def slots(run) -> float:
        return len(_shared_controls(run)) + sum(len(u.treated) + 1 for u in run) + 0.4

    for unit in units:
        if current and slots([*current, unit]) > capacity:
            chunks.append(current)
            current = []
        current.append(unit)
    if current:
        chunks.append(current)
    return chunks


def _count_rate(unit, well) -> float:
    count = next((c for c in unit.primary.counts if c.key == well.key), None)
    return count.rate * 100 if count is not None else math.nan


def _count_label(unit, well) -> str:
    count = next((c for c in unit.primary.counts if c.key == well.key), None)
    return f"{count.k}/{count.n}" if count is not None else ""


#: The shared panel of each test row whose plot is one dot per well: ``(value of a well, y label, title, gid, label
#: of a dot, what a well with no value is marked)``. A row not here has no shared panel.
SHARED_PANELS = {
    "exceedance": (_count_rate, "% of nanowells past the cutoff",
                   "each well's share past the cutoff · grey band: the control wells", "rare-exceed", _count_label,
                   "no count"),
    "hc": (lambda u, w: u.hc[w.key].value, "peak excess score",
           "each well's highest Higher Criticism score (the dot on its curve) · grey band: the control wells",
           "rare-hc-peak", None, "no HC score"),
    "shape": (lambda u, w: u.a2[w.key][0], "upper-tail A²",
              "each well's shape score · grey band: the control wells", "rare-a2", None, "no score"),
}


def _small_panel(key: str, value_label: str, log_axis: bool) -> Callable[[object, object], None] | None:
    """The per-comparison panel of the test row *key*, or ``None`` when the row has none."""
    if key == "exceedance":
        return lambda ax, u: draw_ecdf_strata(ax, u, value_label, log_axis, cutoff=True)
    if key == "hc":
        return lambda ax, u: draw_hc(ax, u, heading=False)
    if key == "pooled":
        return lambda ax, u: draw_gap(ax, u, value_label, log_axis)
    return None


#: The least width of one per-comparison panel's axes, in inches: a figure holds as many side by side as fit in what
#: its margins leave, at most :data:`MAX_COLUMNS`, and the rest wrap to the next line of the same test row — so the
#: figure never grows sideways, whatever the number of comparisons. A phone's figure (about 4 in) holds one.
COLUMN_MIN_IN = 2.4
#: Inches between two per-comparison panels side by side: the next panel's tick labels and y label.
_COLUMN_GAP_IN = 0.85
MAX_COLUMNS = 3
#: Heights, in inches: a shared one-dot-per-well panel with its group names, ticks and title; one per-comparison panel
#: with its two-line title, ticks and labels; the caption above it; the gap after each test row.
SHARED_IN, SMALL_IN, CAPTION_IN, ROW_GAP_IN = 2.6, 2.5, 0.3, 0.3
#: Inches between a test's block frame and what it holds, above its heading and below its last panel.
BLOCK_PAD_IN = 0.1
#: Inches the block frame stands in from the figure's left and right edges, and the width of its role bar in points.
_BLOCK_INSET_IN, _ROLE_BAR_PT = 0.06, 3.5
#: Inches from the figure's left edge to where every line of text starts, and from there to a panel's y axis: the
#: room its tick labels and its y label take. Both are in inches, not shares of the width, so a phone's figure keeps
#: them as a desk's does: as shares, a 4 in figure left 0.08 in for text the role bar ends 0.11 in into.
_CONTENT_IN, _AXIS_ROOM_IN = 0.3, 0.72
#: Inches from a panel's right edge to the figure's: the block's inset and a little room inside it.
_RIGHT_IN = _BLOCK_INSET_IN + 0.2
#: How much larger than its drawn size an axes title is allowed for when it is wrapped to its axes' width, in
#: points per character: the body face's average character is about half its size, and the page's style pass can
#: enlarge titles after the draw.
_TITLE_GROWTH = 0.62
#: What of each height the axes leave to the text around them before their title is known, and the ticks and
#: labels below; once drawn, the axes start under their title whatever its length.
_ABOVE_IN, _BELOW_IN = 0.5, 0.62
#: Inches: the figure's top margin, and the pad around the text under the panels.
_TOP_IN, _TEXT_PAD_IN = 0.12, 0.12
#: Inches between the bottom of the label at the figure's top left (``renderer.stamp_subset``) and the page's first
#: line. The label wraps to half the figure's width, so a subset's name with the moving-average tag takes several
#: lines on a phone; the page measures them (``renderer.stamp_height_in``) rather than leaving room for one.
_STAMP_GAP_IN = 0.08
#: The headline's size: the figure's largest text, since it is the answer; a test row's heading is the next.
_HEAD_PT = 9.0
_TEST_PT = 8.5


def role_colour(key: str) -> str:
    """The colour of a test block's role bar (:func:`_draw_block`), from its role in ``rare_export_compute.TEST_ROWS``:
    the accent for the test that decides the verdict, the warning amber for the one that is description only (it treats
    nanowells as independent), the secondary ink for the supporting tests."""
    from ui.theme import Colors

    role = next(row[2] for row in rx.TEST_ROWS if row[0] == key)
    if role.startswith("decides"):
        return Colors.accent
    if role.startswith("description"):
        return Colors.warn
    return Colors.text_secondary


def _draw_block(fig, probe, key: str, top_in: float, bottom_in: float, width: float, height: float) -> None:
    """Frame one test's block — its heading, its p line and every panel of its row — so the plots of one test read as
    one unit and the next test's begin visibly apart: a faint fill (gid ``nw-rare-block:<key>``), an outline in the
    secondary ink (figure lines tagged so a theme flip repaints them, gid ``nw-rare-block-edge:<key>``) and a bar down
    its left edge in the test's role colour (:func:`role_colour`, gid ``nw-rare-block-role:<key>``). *top_in* and
    *bottom_in* are inches down from the figure's top."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle

    _ground, ink, muted = _inks(probe)
    x0, x1 = _BLOCK_INSET_IN / width, 1.0 - _BLOCK_INSET_IN / width
    y0, y1 = 1.0 - bottom_in / height, 1.0 - top_in / height
    fill = Rectangle((x0, y0), x1 - x0, y1 - y0, transform=fig.transFigure, facecolor=ink, alpha=0.045,
                     edgecolor="none", zorder=-10, gid=f"nw-rare-block:{key}")
    fig.patches.append(fill)
    fill.set_figure(fig)
    edge = Line2D([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0], transform=fig.transFigure, color=muted,
                  linewidth=0.8, alpha=0.7, zorder=-9, gid=f"nw-rare-block-edge:{key}")
    bar = Line2D([x0, x0], [y0, y1], transform=fig.transFigure, color=role_colour(key), linewidth=_ROLE_BAR_PT,
                 solid_capstyle="butt", zorder=-8, gid=f"nw-rare-block-role:{key}")
    for line in (edge, bar):
        line.set_figure(fig)
        fig.lines.append(line)
    _ink(edge, muted=True)


def _small_x_label(key: str, value_label: str, log_axis: bool) -> str:
    """The x label of the test row *key*'s per-comparison panel, as its draw function writes it."""
    if key == "hc":
        return "share of the well's nanowells, most extreme first"
    return _x_label(value_label, log_axis)


#: The size the page's style pass draws axis labels at, in points (Properties' default, measured on the page).
_PAGE_LABEL_PT = 9.0


def _x_label_lines(fig, label: str, panel_frac: float) -> list[str]:
    """*label* broken to a panel *panel_frac* of the figure wide, measured at the page's label size (:func:`_wrapped`):
    the renderer breaks it so at every width — the page's phone fit does the same on a phone, and at the desk an x
    label wider than its panel ran off the figure from the right-hand column."""
    return _wrapped(fig, [label], panel_frac * 0.96, _PAGE_LABEL_PT, "")


def _x_label_extra_in(lines: int) -> float:
    """Inches more than one line that an x label broken onto *lines* lines takes under its panel."""
    return (max(lines, 1) - 1) * _PAGE_LABEL_PT * _LEADING / 72


def columns_for(width_in: float) -> int:
    """How many per-comparison panels a figure *width_in* wide holds side by side, each at least
    :data:`COLUMN_MIN_IN` across in what the margins leave."""
    usable = width_in - _CONTENT_IN - _AXIS_ROOM_IN - _RIGHT_IN
    return max(1, min(MAX_COLUMNS, int((usable + _COLUMN_GAP_IN) // (COLUMN_MIN_IN + _COLUMN_GAP_IN))))


def render_rare_page(fig, result, lines=(), *, note: str = "", lead=(), stamped: str = "") -> float:
    """The Statistics view's figure for the rare-responder tests, **one row per test**, and returns the height in
    inches it needs to hold all of it at the figure's width — the page grows the canvas to that and scrolls, so no
    comparison and no test is ever left off the figure.

    From the top: the groups tested and the comparisons they make (``rare_export_compute.groups_line``, gid
    ``nw-rare-groups``); each comparison's headline — its verdict and the counts behind it — under its name
    (``Comparison i of n: <group> vs <control>``, gid ``nw-rare-comparison-head:<i>``; the headline's gid
    ``nw-rare-unit``); then one row per test
    (``rare_export_compute.TEST_ROWS``), headed by the test's name, its role and what it asks (gid
    ``nw-rare-test:<key>``) and each comparison's p (gid ``nw-rare-test-p:<key>``). In a row, a panel that plots one
    dot per well holds every comparison on one axes (:func:`draw_well_dots`); a panel that plots curves is drawn once
    per comparison, captioned with the comparison's name and p (gid ``nw-rare-comparison:<key>:<i>``; the axes'
    ``nw-rare-panel:<key>:<i>``, a shared panel's ``nw-rare-shared:<key>``), as many side
    by side as the width holds (:func:`columns_for`) and the rest wrapping below in the same row. Under the rows: each
    comparison's caveats (marked ⚠), then *lines* and *note* (gid ``nw-rare-line``). *lead* — a control chosen by its
    name, nanowells the run's flags left out — is drawn under the groups line instead: the figure scrolls, and what
    qualifies the whole result must not be below the fold.

    *stamped* is the label (a saved subset's name, the moving average) written at the figure's top left once the page
    is drawn (``renderer.stamp_subset``), ``""`` for none: the first line starts under every line it wraps to
    (``renderer.stamp_height_in``) and :data:`_STAMP_GAP_IN` more."""

    fig.clear()
    width, height = max(fig.get_figwidth(), 1.0), max(fig.get_figheight(), 1.0)
    params, units = result.prepared.params, result.units
    value_label = result.prepared.value_label
    log_axis = log_axis_for(result)
    # Never more columns than comparisons, but at least two when the width holds them: one comparison drawn a third
    # of the width left two thirds of the row empty, and drawn the whole width its curves stretched flat.
    cols = max(1, min(columns_for(width), max(len(units), min(columns_for(width), 2))))
    lines_of = -(-len(units) // cols) if units else 0
    step = _REPORT_PT * _LEADING / 72
    probe = fig.add_axes([0, 0, 0.001, 0.001], frameon=False)
    probe.axis("off")
    probe.set_gid(PAGE_MARK)            # the page's figure holds the rare page while this axes is on it
    ink = _inks(probe)[1]
    y = max(_TOP_IN, stamp_height_in(fig, stamped) + _STAMP_GAP_IN if stamped else 0.0)  # inches from the top

    content_x = _CONTENT_IN / width                   # every line of text starts here, clear of a block's role bar
    content_w = 1.0 - 2 * content_x

    def text(content, pt, gid, *, bold=False, x=content_x, wrap=content_w) -> None:
        nonlocal y
        wrapped = _wrapped(fig, [content], wrap, pt, "", bold=bold)
        _ink(fig.text(x, 1 - y / height, "\n".join(wrapped), fontsize=pt, fontweight="bold" if bold else "normal",
                      va="top", color=ink, linespacing=_LEADING, gid=gid))
        y += len(wrapped) * pt * _LEADING / 72

    def axes(x0: float, top: float, w: float, h: float, below: float = _BELOW_IN):
        ax = fig.add_axes([x0, 1 - (top + h - below) / height, w, (h - _ABOVE_IN - below) / height])
        _prepare_axes(ax, GRID_TYPE_SCALE)
        return ax

    def fit_title(ax, top: float, h: float, below: float = _BELOW_IN) -> None:
        # The axes start under their own title, however many lines it took: a fixed allowance left an inch of
        # nothing over a one-line title and ran a four-line one into the caption above.
        # Wrapped to the axes' own width first, at the size the page's style pass may draw it: a title one panel of
        # three wide ran into its neighbour's, and on a phone off the figure.
        if ax.get_title():
            chars = max(16, int(ax.get_position().width * width * 72 / (ax.title.get_fontsize() * _TITLE_GROWTH)))
            ax.title.set_text("\n".join(part for line in ax.get_title().split("\n")
                                         for part in (textwrap.wrap(line, chars) or [""])))
        lines = ax.get_title().count("\n") + 1 if ax.get_title() else 0
        above = 0.12 + lines * _TITLE_PT * _LEADING / 72
        bottom = top + h - below
        ax.set_position([ax.get_position().x0, 1 - bottom / height, ax.get_position().width,
                         max(bottom - top - above, 0.3) / height])

    if units:
        text(rx.groups_line(result), _HEAD_PT, "nw-rare-groups", bold=True)
        y += 0.06
    for item in lead:                       # what qualifies the whole result: at the top, never below the fold
        for part in _wrapped(fig, [item], content_w):
            _ink(fig.text(content_x, 1 - y / height, part, fontsize=_REPORT_PT, va="top", color=ink, gid="nw-rare-line"))
            y += step
    if lead:
        y += 0.04
    for i, unit in enumerate(units):
        y += 0.04
        text(rx.comparison_title(result, i), _HEAD_PT, f"nw-rare-comparison-head:{i}", bold=True)
        text(rx.unit_text(result, unit)["headline"], _HEAD_PT, "nw-rare-unit")
    left, gap, right = (_CONTENT_IN + _AXIS_ROOM_IN) / width, _COLUMN_GAP_IN / width, _RIGHT_IN / width
    panel_w = (1.0 - left - right - (cols - 1) * gap) / cols
    for key, *_rest in rx.TEST_ROWS if units else ():
        y += ROW_GAP_IN
        block_top = y
        y += BLOCK_PAD_IN
        text(rx.test_heading(key, params), _TEST_PT, f"nw-rare-test:{key}", bold=True)
        text("   ".join(f"{u.label}: {rx.test_p(u, key, params, len(units))}" for u in units), _REPORT_PT,
             f"nw-rare-test-p:{key}")
        if key in SHARED_PANELS:
            value_of, ylabel, title, gid, label_of, missing = SHARED_PANELS[key]
            # As many comparisons as the width holds at a readable spacing; the rest on another such panel below,
            # the control wells drawn again on each.
            for j, run in enumerate(well_dot_chunks(units, (1.0 - left - right) * width)):
                ax = axes(left, y, 1.0 - left - right, SHARED_IN)
                ax.set_gid(f"nw-rare-shared:{key}" + (f":{j}" if j else ""))
                _title(ax, title)
                fit_title(ax, y, SHARED_IN)
                draw_well_dots(ax, result, value_of, ylabel, gid, label_of=label_of, missing=missing,
                               p_of=lambda u, k=key: rx.test_p(u, k, params, len(units), short=True), units=run)
                y += SHARED_IN
        draw = _small_panel(key, value_label, log_axis)
        if draw is not None:
            y += 0.12                                    # the captions clear the p line above them
            # The phone fit breaks an x label wider than its panel onto more lines (``plot_style``): the room under
            # the panel grows by those lines, or the last one sat on the next comparison's caption.
            x_label = _x_label_lines(fig, _small_x_label(key, value_label, log_axis), panel_w)
            extra = _x_label_extra_in(len(x_label))
            small_in = SMALL_IN + extra
            # Every caption whole, wrapped to its column, never cut: the room above the panels is the tallest one's.
            captions = [_wrapped(fig, [f"{rx.comparison_title(result, i)} · {rx.test_p(u, key, params, len(units))}"],
                                 panel_w + gap * 0.45, _REPORT_PT, "", bold=True) for i, u in enumerate(units)]
            caption_in = max(CAPTION_IN, max(len(c) for c in captions) * _REPORT_PT * _LEADING / 72 + 0.08)
            for i, unit in enumerate(units):
                line, col = divmod(i, cols)
                top = y + line * (caption_in + small_in)
                x0 = left + col * (panel_w + gap)
                _ink(fig.text(x0 - 0.45 / width, 1 - top / height, "\n".join(captions[i]), fontsize=_REPORT_PT,
                              fontweight="bold", va="top", color=ink, linespacing=_LEADING,
                              gid=f"nw-rare-comparison:{key}:{i}"))
                ax = axes(x0, top + caption_in, panel_w, small_in, _BELOW_IN + extra)
                ax.set_gid(f"nw-rare-panel:{key}:{i}")
                draw(ax, unit)
                ax.xaxis.label.set_text("\n".join(x_label))
                fit_title(ax, top + caption_in, small_in, _BELOW_IN + extra)
            y += lines_of * (caption_in + small_in)
        y += BLOCK_PAD_IN
        _draw_block(fig, probe, key, block_top, y, width, height)
    y += _TEXT_PAD_IN
    caveats = [f"⚠ {rx.comparison_title(result, i).split(':', 1)[0]} ({u.label}): {c}"
               for i, u in enumerate(units) for c in rx.unit_text(result, u)["caveats"]]
    for item in [*caveats, *lines, *([note] if note else [])]:
        for part in _wrapped(fig, [item], content_w):
            _ink(fig.text(content_x, 1 - y / height, part, fontsize=_REPORT_PT, va="top", color=ink, gid="nw-rare-line"))
            y += step
    fig._aw_rare_laid_out = (round(width, 2), round(height, 2))
    fig._aw_rare_need_in = y + _TEXT_PAD_IN
    return fig._aw_rare_need_in


#: The gid of an invisible axes :func:`render_rare_page` puts on its figure: while it is there, the figure holds the
#: rare page, and :func:`needed_height` reads the height that page asked for. Any other view clears the figure, and
#: the mark with it.
PAGE_MARK = "nw-rare-page"


def needed_height(fig) -> float | None:
    """The height in inches the rare page on *fig* needs at its laid-out width, or ``None`` when *fig* holds no rare
    page (another view, or a message)."""
    if not any(ax.get_gid() == PAGE_MARK for ax in fig.axes):
        return None
    return getattr(fig, "_aw_rare_need_in", None)


def needs_relayout(fig) -> bool:
    """Whether the rare page on *fig* was laid out for another size than the figure now has — the canvas grew to the
    height the page asked for, or the window was resized — and must be drawn again."""
    laid = getattr(fig, "_aw_rare_laid_out", None)
    if needed_height(fig) is None or laid is None:
        return False
    return laid != (round(fig.get_figwidth(), 2), round(fig.get_figheight(), 2))


def render_crops(fig, responders, load, *, max_crops: int = MAX_CROPS) -> None:
    """Every flagged nanowell's crop, up to *max_crops*, for review by eye.

    *load* is ``load(path) -> 2-D array | None``. A row with no path, or whose crop cannot be read, is drawn as a
    named placeholder rather than left out, so a missing image is never mistaken for a nanowell not flagged."""
    fig.clear()
    rows = responders.head(max_crops) if len(responders) else responders
    n = len(rows)
    if n == 0:
        ax = fig.add_subplot(111)
        ax.axis("off")
        _ink(ax.text(0.5, 0.5, "No nanowell was flagged", ha="center", va="center", color=_inks(ax)[1],
                     gid="rare-crops-none"))
        return
    cols = int(math.ceil(math.sqrt(n * 1.5)))
    grid = fig.subplots(int(math.ceil(n / cols)), cols, squeeze=False)
    for ax in grid.flat:
        ax.axis("off")
    for ax, (_i, row) in zip(grid.flat, rows.iterrows()):
        image = load(row["crop_path"]) if row["crop_path"] else None
        why = "flag: " + " + ".join(t for t, on in (("> T", row["above_threshold"]), ("HC", row["hc_candidate"]))
                                    if on)
        title = f"{row['well_key']} #{row['nanowell_id']}\nFC {row['fc']:.3g} (N0={row['n0']:g}) {why}"
        if image is None:
            _ink(ax.text(0.5, 0.5, "no crop path" if not row["crop_path"] else "crop unreadable", ha="center",
                         va="center", fontsize=_TITLE_PT * 0.8, color=_inks(ax)[2],
                         gid=f"rare-crop-missing:{row['well_key']}:{row['nanowell_id']}"), muted=True)
        else:
            ax.imshow(image, cmap="gray", gid=f"rare-crop:{row['well_key']}:{row['nanowell_id']}")
        _title(ax, title, _TITLE_PT * 0.7)
    more = len(responders) - n
    _ink(fig.suptitle(f"Flagged nanowells for manual review ({len(responders)} flagged"
                      + (f"; {more} more not drawn" if more > 0 else "") + ")", fontsize=_TITLE_PT,
                      color=_inks(grid.flat[0])[1]))
