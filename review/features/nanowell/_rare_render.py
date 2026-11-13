"""Draw the rare-responder analysis: the ECDFs with the threshold, the exceedance counts over the control null, the
Higher Criticism curves, the report, and the image crops of every flagged nanowell.

Pure matplotlib onto a figure it is handed; every number comes from ``rare_report_compute`` and is only drawn here.
:func:`render_rare_page` is what the Nanowells page's Statistics view draws for the *Rare responders* test: one row
of the three panels per treatment (at most :data:`MAX_UNIT_ROWS`), the report's lines beneath. The single-panel
functions draw one of them onto a whole figure. Each artist that carries a result has a gid (``rare-…``), so a test
reads the figure back rather than the result. Control wells are drawn in the secondary ink, treatment wells in the
plate's well colours, every text in the plot tokens' inks with the theme tag, so it repaints on a theme flip.
"""

from __future__ import annotations

import math
import textwrap

import numpy as np

from review.features.nanowell.renderer import GRID_TYPE_SCALE, _ink, _inks, _prepare_axes

#: Crops drawn at most per figure; the rest are counted in its title.
MAX_CROPS = 48
#: Treatments drawn as panels on the Statistics view; the report below covers every one.
MAX_UNIT_ROWS = 3
_TITLE_PT = 8.0
_REPORT_PT = 6.5
_LEADING = 1.45


def _colours(n: int) -> list:
    from ui.theme import WELL_COLORS_TUPLE

    return [WELL_COLORS_TUPLE[i % len(WELL_COLORS_TUPLE)] for i in range(n)]


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
    parts = [w.values[stratum] for w in group if stratum in w.values]
    if not parts:
        return None
    x, y = _ecdf(np.concatenate(parts))
    return (x[x > 0], y[x > 0]) if log_axis else (x, y)


def draw_ecdf_stratum(ax, unit, stratum: str, value_label: str, log_axis: bool) -> None:
    """One stratum's pooled control ECDF against its pooled treatment ECDF, the threshold marked."""
    _ground, ink, muted = _inks(ax)
    for group, colour, gid, label in ((unit.controls, muted, "rare-ecdf-control", "control"),
                                      (unit.treated, _colours(1)[0], "rare-ecdf-treatment", unit.treatment)):
        xy = _pooled(group, stratum, log_axis)
        if xy is not None:
            ax.step(*xy, where="post", color=colour, label=label, linewidth=1.2)[0].set_gid(f"{gid}:{stratum}")
    if stratum in unit.thresholds:
        ax.axvline(unit.thresholds[stratum], color=ink, linestyle="--", linewidth=1).set_gid(
            f"rare-threshold:{stratum}")
    if log_axis:
        ax.set_xscale("log")
    _title(ax, f"N0 = {stratum}")
    ax.set_xlabel(value_label + (" (log; FC = 0 not shown)" if log_axis else ""))
    ax.set_ylim(0, 1.01)


def draw_ecdf_strata(ax, unit, value_label: str, log_axis: bool) -> None:
    """Every stratum on one axes: control dotted, treatment solid, one colour per stratum, each threshold dashed."""
    strata = _strata(unit)
    for stratum, colour in zip(strata, _colours(len(strata))):
        for group, style, gid in ((unit.controls, ":", "rare-ecdf-control"),
                                  (unit.treated, "-", "rare-ecdf-treatment")):
            xy = _pooled(group, stratum, log_axis)
            if xy is not None:
                ax.step(*xy, where="post", color=colour, linestyle=style, linewidth=1.1)[0].set_gid(
                    f"{gid}:{stratum}")
        if stratum in unit.thresholds:
            ax.axvline(unit.thresholds[stratum], color=colour, linestyle="--", linewidth=0.8).set_gid(
                f"rare-threshold:{stratum}")
    if log_axis:
        ax.set_xscale("log")
    ax.set_xlabel(value_label + (" (log)" if log_axis else ""))
    ax.set_ylabel("cumulative fraction")
    ax.set_ylim(0, 1.01)
    _title(ax, "ECDF by N0: " + ", ".join(strata) + " (control dotted)")


def draw_exceedance(ax, unit) -> None:
    """Each well's exceedance rate; control wells (leave-one-out) as the null, treatment wells with their p."""
    _ground, ink, muted = _inks(ax)
    counts = unit.primary.counts
    controls = [c for c in counts if not c.treated]
    treated = [c for c in counts if c.treated]
    ctrl_rates = np.array([c.rate for c in controls], dtype=float) * 100
    if ctrl_rates.size and np.isfinite(ctrl_rates).any():
        ax.axhspan(np.nanmin(ctrl_rates), np.nanmax(ctrl_rates), color=muted, alpha=0.15).set_gid("rare-null-band")
        ax.axhline(np.nanmean(ctrl_rates), color=muted, linestyle=":", linewidth=1).set_gid("rare-null-mean")
    xs = np.arange(len(counts))
    ax.scatter(xs[:len(controls)], ctrl_rates, color=muted, zorder=3, gid="rare-exceed-control")
    for i, (c, colour) in enumerate(zip(treated, _colours(len(treated)))):
        x = len(controls) + i
        ax.scatter([x], [c.rate * 100], color=colour, zorder=4, gid=f"rare-exceed-treated:{c.key}")
        if np.isfinite(c.empirical_p):
            _ink(ax.annotate(f"p={c.empirical_p:.2g}", (x, c.rate * 100), textcoords="offset points",
                             xytext=(0, 6), ha="center", fontsize=_TITLE_PT * 0.75, color=ink))
    ax.set_xticks(xs, [c.key.split(":", 1)[-1] for c in controls + treated], rotation=60,
                  fontsize=_TITLE_PT * 0.75)
    ax.set_ylabel(f"% above the {unit.primary.percentile:g}th pct.")
    _title(ax, "Exceedance per well over the control null")


def draw_hc(ax, unit) -> None:
    """The HC curve against i/n: control wells (leave-one-out) in the secondary ink, treatment wells coloured."""
    _ground, _ink_colour, muted = _inks(ax)
    for w in unit.controls:
        h = unit.hc[w.key]
        if len(h.curve_x):
            ax.plot(h.curve_x, h.curve_y, color=muted, linewidth=0.8, gid=f"rare-hc-control:{w.key}")
    for w, colour in zip(unit.treated, _colours(len(unit.treated))):
        h = unit.hc[w.key]
        if len(h.curve_x):
            ax.plot(h.curve_x, h.curve_y, color=colour, linewidth=1.3, gid=f"rare-hc-treated:{w.key}")
            ax.scatter([h.i_star / h.n], [h.value], color=colour, zorder=4, gid=f"rare-hc-istar:{w.key}")
    ax.set_xlabel("i / n")
    ax.set_ylabel("HC+")
    p = unit.hc_perm.p if unit.hc_perm is not None else math.nan
    _title(ax, f"Higher Criticism (permutation p = {p:.3g})" if np.isfinite(p) else "Higher Criticism (no group p)")


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


def _wrapped(fig, lines) -> list[str]:
    width = max(40, int(fig.get_figwidth() * 72 / (_REPORT_PT * 0.6)))
    return [part for line in lines for part in (textwrap.wrap(line, width, subsequent_indent="    ") or [""])]


def render_rare_page(fig, result, lines, *, note: str = "") -> None:
    """The Statistics view's figure for the rare-responder tests: a row of ECDF / exceedance / HC per treatment (at
    most :data:`MAX_UNIT_ROWS`), then *lines* (``rare_export_compute.report_lines``) and *note*, each line a text
    artist with gid ``nw-rare-line``."""
    fig.clear()
    units = result.units[:MAX_UNIT_ROWS]
    extra = len(result.units) - len(units)
    text = _wrapped(fig, [*lines, *([f"{extra} more treatment(s) are in the report only."] if extra else []),
                          *([note] if note else [])])
    height = max(fig.get_figheight(), 1.0)
    step = _REPORT_PT * _LEADING / 72 / height
    text_frac = min(0.6, len(text) * step + 0.04) if units else 0.96
    band = (1.0 - text_frac) / max(1, len(units))
    log_axis = not result.prepared.params.normalize
    for r, unit in enumerate(units):
        bottom = 1.0 - (r + 1) * band
        panels = (lambda ax, u=unit: draw_ecdf_strata(ax, u, result.prepared.value_label, log_axis),
                  lambda ax, u=unit: draw_exceedance(ax, u), lambda ax, u=unit: draw_hc(ax, u))
        for c, draw in enumerate(panels):
            ax = fig.add_axes([0.07 + c * 0.32, bottom + band * 0.3, 0.25, band * 0.55])
            _prepare_axes(ax, GRID_TYPE_SCALE)
            draw(ax)
            if c == 0:
                _ink(ax.text(0.0, 1.22, unit.label, transform=ax.transAxes, fontsize=_TITLE_PT, fontweight="bold",
                             color=_inks(ax)[1], gid="nw-rare-unit"))
    probe = fig.axes[0] if fig.axes else fig.add_axes([0, 0, 0.001, 0.001], frameon=False)
    ink = _inks(probe)[1]
    if not units:
        probe.axis("off")
    for i, line in enumerate(text):
        _ink(fig.text(0.02, text_frac - 0.01 - i * step, line, fontsize=_REPORT_PT, family="monospace", va="top",
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
