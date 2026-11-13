"""Draw the rare-responder analysis: the ECDFs with the threshold, the exceedance counts over the control null, the
Higher Criticism curves, the report, and the image crops of every flagged nanowell.

Pure matplotlib onto a figure it is handed; every number comes from ``rare_report_compute`` and is only drawn here.
:func:`render_rare_page` is what the Nanowells page's Statistics view draws for the *Rare responders* test: one row
of the three panels per treatment (at most :data:`MAX_UNIT_ROWS`), the report's lines beneath. The single-panel
functions draw one of them onto a whole figure. Each artist that carries a result has a gid (``rare-…``), so a test
reads the figure back rather than the result. Control wells are drawn in the secondary ink, each treatment well in
its own colour on the plate (``selections_model.color_for_well``), every text in the plot tokens' inks with the theme
tag, so it repaints on a theme flip. The loading strata are a magnitude, not an identity, so they are drawn in the
page's occupancy ramp (the colours the Distribution view gives a nanowell's cell count) rather than in well colours a
reader would take for wells: its screen / publication pair, marked so a capture repaints it.
"""

from __future__ import annotations

import math
import textwrap

import numpy as np

from review.features.nanowell.renderer import GRID_TYPE_SCALE, _ink, _inks, _is_publication, _prepare_axes

#: Crops drawn at most per figure; the rest are counted in its title.
MAX_CROPS = 48
#: Treatments drawn as panels on the Statistics view; the report below covers every one.
MAX_UNIT_ROWS = 3
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


def _pooled(group, stratum, log_axis: bool) -> tuple[np.ndarray, np.ndarray] | None:
    """The pooled ECDF of *group* in *stratum*; with *log_axis*, its x as log₂ of the value (a value of 0 left out).

    The log is taken here, on a linear axis, rather than by ``set_xscale("log")``: the page's style pass sets each
    axis's scale from the Properties panel after the draw, which put a "(log)" label over a linear axis."""
    parts = [w.values[stratum] for w in group if stratum in w.values]
    if not parts:
        return None
    x, y = _ecdf(np.concatenate(parts))
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


def _x_label(value_label: str, log_axis: bool) -> str:
    return "fold change (final ÷ start), log scale — a fold change of 0 is left out" if log_axis else value_label


def _fold_ticks(ax, log_axis: bool) -> None:
    """On the log axis, ticks at whole powers of two labelled as the fold changes they are (``2×``, ``4×``), not as
    their logarithms."""
    if not log_axis:
        return
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"{2 ** v:g}×"))


def _stratum_name(stratum: str) -> str:
    return f"{stratum} cell at start" if str(stratum) == "1" else f"{stratum} cells at start"


def draw_ecdf_stratum(ax, unit, stratum: str, value_label: str, log_axis: bool) -> None:
    """One stratum's pooled control ECDF against its pooled treatment ECDF, the threshold marked."""
    _ground, ink, muted = _inks(ax)
    treated_colour = _well_colour(unit.treated[0].key) if unit.treated else ink
    for group, colour, gid, label in ((unit.controls, muted, "rare-ecdf-control", "control"),
                                      (unit.treated, treated_colour, "rare-ecdf-treatment", unit.treatment)):
        xy = _pooled(group, stratum, log_axis)
        if xy is not None:
            ax.step(*xy, where="post", color=colour, label=label, linewidth=1.2)[0].set_gid(f"{gid}:{stratum}")
    if stratum in unit.thresholds and np.isfinite(_x(unit.thresholds[stratum], log_axis)):
        ax.axvline(_x(unit.thresholds[stratum], log_axis), color=ink, linestyle="--", linewidth=1).set_gid(
            f"rare-threshold:{stratum}")
    _fold_ticks(ax, log_axis)
    _title(ax, _stratum_name(stratum))
    ax.set_xlabel(_x_label(value_label, log_axis))
    ax.set_ylim(0, 1.01)


def draw_ecdf_strata(ax, unit, value_label: str, log_axis: bool) -> None:
    """Every stratum on one axes: control dotted, treatment solid, one colour per stratum, each threshold dashed."""
    strata = _strata(unit)
    publication = _is_publication(ax)
    for stratum, pair in zip(strata, _stratum_shades(len(strata))):
        colour = pair[1] if publication else pair[0]
        for group, style, gid in ((unit.controls, ":", "rare-ecdf-control"),
                                  (unit.treated, "-", "rare-ecdf-treatment")):
            xy = _pooled(group, stratum, log_axis)
            if xy is not None:
                line = ax.step(*xy, where="post", color=colour, linestyle=style, linewidth=1.1,
                               label=_stratum_name(stratum) if style == "-" else None)[0]
                _paired(line, pair).set_gid(f"{gid}:{stratum}")
        if stratum in unit.thresholds and np.isfinite(_x(unit.thresholds[stratum], log_axis)):
            _paired(ax.axvline(_x(unit.thresholds[stratum], log_axis), color=colour, linestyle="--", linewidth=0.8),
                    pair).set_gid(f"rare-threshold:{stratum}")
    ax.set_xlabel(_x_label(value_label, log_axis))
    ax.set_ylabel("share of nanowells at or below")
    _fold_ticks(ax, log_axis)
    ax.set_ylim(0, 1.01)
    _label_strata(ax, unit, strata, log_axis, publication)
    _title(ax, f"How far nanowells grew, by starting cells\nsolid: {unit.treatment} · dotted: control")


def _label_strata(ax, unit, strata, log_axis: bool, publication: bool) -> None:
    """Label the lines on the panel itself rather than in a key: each stratum's treatment curve where it crosses its
    own height (staggered, since every cumulative curve ends at the same top right corner, where labels at the ends
    would pile up), with a dot in the stratum's shade; and the dashed line as the control cutoff."""
    _ground, ink, _muted = _inks(ax)
    for i, (stratum, pair) in enumerate(zip(strata, _stratum_shades(len(strata)))):
        xy = _pooled(unit.treated, stratum, log_axis)
        if xy is None or not len(xy[0]):
            continue
        level = 0.78 - 0.56 * i / (len(strata) - 1) if len(strata) > 1 else 0.5   # spread over the panel's height
        x = float(xy[0][min(int(np.searchsorted(xy[1], level)), len(xy[0]) - 1)])
        dot = ax.scatter([x], [level], s=14, color=pair[1] if publication else pair[0], zorder=5,
                         gid=f"rare-ecdf-dot:{stratum}")
        _paired(dot, ((pair[0], pair[0]), (pair[1], pair[1])))
        _ink(ax.annotate(_stratum_name(stratum), (x, level), textcoords="offset points", xytext=(6, 0), va="center",
                         fontsize=_TITLE_PT * 0.75, color=ink, gid=f"rare-ecdf-label:{stratum}"))
    cuts = [_x(unit.thresholds[s], log_axis) for s in strata if s in unit.thresholds]
    cuts = [c for c in cuts if np.isfinite(c)]
    if cuts:
        # At the foot of the dashed line, clear of the stratum labels, which sit at mid-height.
        _ink(ax.annotate("control cutoff", (max(cuts), 0.02), textcoords="offset points", xytext=(-3, 0),
                         rotation=90, ha="right", va="bottom", fontsize=_TITLE_PT * 0.7, color=ink,
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
    _title(ax, "Share of each well's nanowells past the cutoff")


def draw_hc(ax, unit) -> None:
    """The HC curve over each well's nanowells, most extreme first: control wells (leave-one-out) in the secondary
    ink, treatment wells in their plate colours, a dot at each curve's peak. A treatment well with no curve (HC
    undefined for it) is named in the title rather than silently missing."""
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
            missing.append(w.key.split(":", 1)[-1])
    ax.set_xlabel("share of the well's nanowells, most extreme first")
    ax.set_ylabel("excess score")
    perm = unit.hc_perm
    p = perm.p if perm is not None else math.nan
    if np.isfinite(p):
        floor = " (lowest possible)" if np.isfinite(perm.min_p) and p <= perm.min_p * (1 + 1e-9) else ""
        head = f"Higher Criticism: p = {p:.3g}{floor}"
    else:
        head = "Higher Criticism: no group p"
    sub = f"colour: {unit.treatment} wells · grey: control" + (
        f" · not drawn: {', '.join(missing)}" if missing else "")
    _title(ax, f"{head}\n{sub}")


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


def _wrapped(fig, lines, width_frac: float, pt: float = None, indent: str = "   ") -> list[str]:
    """*lines* wrapped to what fits *width_frac* of the figure's width at *pt* (the report's size) in the body face,
    whose average character is about half its point size wide."""
    pt = pt or _REPORT_PT
    chars = max(30, int(fig.get_figwidth() * width_frac * 72 / (pt * 0.52)))
    return [part for line in lines for part in (textwrap.wrap(line, chars, subsequent_indent=indent) or [""])]


#: The height one row of panels needs to be read, in inches: its two-line title, ticks and labels take 1.2 of it.
ROW_MIN_IN = 2.5
#: Below this figure width, in inches, a treatment's three panels are stacked in one column rather than laid side by
#: side: under it each of three panels is less than about 1.25 in across once the y labels and gaps are taken, and
#: its tick labels run together. A phone's figure is about 4 in wide; a desk window's, 7 in and over.
STACK_BELOW_IN = 6.5
#: A stacked panel's least height, in inches: lower than :data:`ROW_MIN_IN` so one treatment's three panels and its
#: verdict fit a phone's figure (about 7.3 in tall) without running off its bottom.
STACKED_ROW_MIN_IN = 1.9
#: Inches: the figure's top margin, the space a row's x labels and two-line title take beyond its axes, the pad
#: around the text under the panels, and the gap under a headline.
_TOP_IN, _ROW_PAD_IN, _TEXT_PAD_IN, _HEAD_GAP_IN = 0.12, 1.2, 0.12, 0.1
#: The headline's size: the figure's largest text, since it is the answer.
_HEAD_PT = 9.0


def render_rare_page(fig, result, lines=(), *, note: str = "", lead=()) -> None:
    """The Statistics view's figure for the rare-responder tests. For each treatment, **the answer first**: its headline
    (``rare_export_compute.headline`` — the verdict and the counts behind it, gid ``nw-rare-unit``) above its row of
    panels — how far nanowells grew, the share of each well past the cutoff, Higher Criticism. Under the panels, each
    treatment's deciding and supporting tests and its caveats (``unit_text``; each line gid ``nw-rare-line``, a
    caveat marked ⚠), then any extra *lines* and *note*. *lead* is drawn before all of it and never cut (a control
    chosen by its name says so there). The full report is not drawn here: the page shows it under
    the figure (Details).

    **The panels are laid out first, in inches.** Each row gets at least :data:`ROW_MIN_IN`; the text takes only what
    its lines need, and as many treatments are drawn as their rows fit — the rest named in a line — so a long report
    or a short window never squeezes the panels below legibility. A figure narrower than :data:`STACK_BELOW_IN` (a
    phone's) stacks each treatment's three panels in a column, each at least :data:`STACKED_ROW_MIN_IN` tall:
    side by side at that width each panel was about an inch across, its axis labels run into its neighbours."""
    from review.features.nanowell.rare_export_compute import unit_text

    fig.clear()
    width, height = max(fig.get_figwidth(), 1.0), max(fig.get_figheight(), 1.0)
    stacked = width < STACK_BELOW_IN
    per_unit, cols = (3, 1) if stacked else (1, 3)      # rows of panels per treatment, panels per row
    row_min = STACKED_ROW_MIN_IN if stacked else ROW_MIN_IN
    step_in = _REPORT_PT * _LEADING / 72
    head_step = _HEAD_PT * _LEADING / 72
    texts = [unit_text(result, u) for u in result.units]
    heads = [_wrapped(fig, [t["headline"]], 0.96, _HEAD_PT, "") for t in texts]
    head_in = [len(h) * head_step + _HEAD_GAP_IN for h in heads]
    # The deciding test, then the caveats — what undermines the verdict — then the supporting tests: when a short figure
    # cannot hold them all, it is the supporting tests that go to Details first.
    body = [[t["lines"][0], *[f"⚠ {c}" for c in t["caveats"]], *t["lines"][1:]] for t in texts]
    room = 0
    used = _TOP_IN + (len(body[0]) if body else 0) * step_in + 2 * _TEXT_PAD_IN
    for h in head_in[:MAX_UNIT_ROWS]:
        if room and used + h + per_unit * row_min > height:
            break
        used += h + per_unit * row_min
        room += 1
    n_units = max(1, room) if result.units else 0
    units, heads, head_in = result.units[:n_units], heads[:n_units], head_in[:n_units]
    extra = len(result.units) - len(units)
    items = [*[line for b in body[:n_units] for line in b], *lines]
    # Always drawn, whatever else is cut: the first treatment's deciding test, and that some treatments are not drawn.
    must = _wrapped(fig, [f"{extra} more treatment(s) are not drawn: each is in Details, below the figure."], 0.96) \
        if extra else []
    first = _wrapped(fig, list(lead), 0.96)
    # The text takes what the panels leave, never more. It is cut between sentences, never inside one — a caveat cut
    # half way reads as a different sentence — and what is left out is counted; Details holds it all.
    fits = max(1, int((height - _TOP_IN - sum(head_in) - len(units) * per_unit * row_min - 2 * _TEXT_PAD_IN)
                      // step_in))
    tail = _wrapped(fig, [note], 0.96) if note else []
    text, left_out = list(first), 0
    for i, item in enumerate(items):
        wrapped = _wrapped(fig, [item], 0.96)
        if i and (left_out or len(text) + len(wrapped) + len(must) + 1 > fits):
            left_out += 1
            continue
        text += wrapped
    text += must
    if left_out:                                   # one pointer to Details, not two
        text.append(f"… {left_out} more, and the full report: Details ▸ under the figure.")
    else:
        text += tail
    text_in = len(text) * step_in + 2 * _TEXT_PAD_IN
    band_in = max(row_min, (height - _TOP_IN - text_in - sum(head_in)) / max(1, len(units) * per_unit))
    log_axis = not result.prepared.params.normalize
    left, gap = 0.75 / width, 0.85 / width
    panel_w = (1.0 - left - 0.25 / width - (cols - 1) * gap) / cols
    axes_h = band_in - _ROW_PAD_IN
    cursor = height - _TOP_IN
    probe = None
    for unit, head, h_in in zip(units, heads, head_in):
        head_top = cursor
        cursor -= h_in
        panels = (lambda ax, u=unit: draw_ecdf_strata(ax, u, result.prepared.value_label, log_axis),
                  lambda ax, u=unit: draw_exceedance(ax, u), lambda ax, u=unit: draw_hc(ax, u))
        for c, draw in enumerate(panels):
            row = c if stacked else 0
            bottom = cursor - (row + 1) * band_in + 0.62      # the x labels sit below the axes
            ax = fig.add_axes([left + (0 if stacked else c) * (panel_w + gap), bottom / height, panel_w,
                               axes_h / height])
            _prepare_axes(ax, GRID_TYPE_SCALE)
            draw(ax)
            probe = probe or ax
        cursor -= per_unit * band_in
        ink = _inks(probe)[1]
        _ink(fig.text(0.02, head_top / height, "\n".join(head), fontsize=_HEAD_PT, fontweight="bold", va="top",
                      color=ink, linespacing=_LEADING, gid="nw-rare-unit"))
    if probe is None:
        probe = fig.add_axes([0, 0, 0.001, 0.001], frameon=False)
        probe.axis("off")
    ink = _inks(probe)[1]
    y0 = (text_in - _TEXT_PAD_IN) / height
    for i, line in enumerate(text):
        _ink(fig.text(0.02, y0 - i * step_in / height, line, fontsize=_REPORT_PT, va="top",
                      color=ink, gid="nw-rare-line"))


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
