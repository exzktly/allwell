"""Draw the rare-responder analysis: the ECDFs with the threshold, the exceedance counts over the control null, the
Higher Criticism curves, and the image crops of every flagged nanowell.

Pure matplotlib onto a figure it is handed; every number comes from ``rare_report_compute`` and is only drawn here.
Each artist that carries a result has a gid (``rare-…``), so a test reads the figure back rather than the result.
Control wells are drawn in the secondary ink, treatment wells in the plate's well colours.
"""

from __future__ import annotations

import math

import numpy as np

from review.features.nanowell.renderer import _inks, _prepare_axes, GRID_TYPE_SCALE

#: Crops drawn at most per figure; the rest are counted in its title.
MAX_CROPS = 48
_TITLE_PT = 8.0


def _colours(n: int) -> list:
    from ui.theme import WELL_COLORS_TUPLE

    return [WELL_COLORS_TUPLE[i % len(WELL_COLORS_TUPLE)] for i in range(n)]


def _ecdf(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.sort(np.asarray(values, dtype=float))
    return x, np.arange(1, len(x) + 1) / len(x)


def _axes_row(fig, n: int):
    fig.clear()
    axes = fig.subplots(1, max(1, n), squeeze=False)[0]
    for ax in axes:
        _prepare_axes(ax, GRID_TYPE_SCALE)
    return axes


def render_ecdf(fig, unit, value_label: str, *, log_axis: bool = True) -> None:
    """Per loading stratum, the pooled control ECDF against the pooled treatment ECDF, the threshold marked."""
    strata = sorted({s for w in unit.controls + unit.treated for s in w.values})
    axes = _axes_row(fig, len(strata))
    t_colour = _colours(1)[0]
    for ax, s in zip(axes, strata):
        _ground, ink, muted = _inks(ax)
        for group, colour, gid, label in ((unit.controls, muted, "rare-ecdf-control", "control"),
                                          (unit.treated, t_colour, "rare-ecdf-treatment", unit.treatment)):
            parts = [w.values[s] for w in group if s in w.values]
            if not parts:
                continue
            x, y = _ecdf(np.concatenate(parts))
            if log_axis:
                x, y = x[x > 0], y[x > 0]
            (line,) = ax.step(x, y, where="post", color=colour, label=label, linewidth=1.2)
            line.set_gid(f"{gid}:{s}")
        if s in unit.thresholds:
            line = ax.axvline(unit.thresholds[s], color=ink, linestyle="--", linewidth=1)
            line.set_gid(f"rare-threshold:{s}")
        if log_axis:
            ax.set_xscale("log")
        ax.set_title(f"N0 = {s}", fontsize=_TITLE_PT, color=ink)
        ax.set_xlabel(value_label + (" (log; FC = 0 not shown)" if log_axis else ""))
        ax.set_ylim(0, 1.01)
    axes[0].set_ylabel("cumulative fraction")
    axes[0].legend(fontsize=_TITLE_PT * 0.85, frameon=False)
    fig.suptitle(f"{unit.label}: ECDF by loading stratum, threshold = {unit.primary.percentile:g}th percentile "
                 "of control", fontsize=_TITLE_PT)


def render_exceedance(fig, unit) -> None:
    """Each well's exceedance rate; control wells (leave-one-out) as the null, treatment wells with their p."""
    axes = _axes_row(fig, 1)
    ax = axes[0]
    _ground, ink, muted = _inks(ax)
    counts = unit.primary.counts
    controls = [c for c in counts if not c.treated]
    treated = [c for c in counts if c.treated]
    ctrl_rates = np.array([c.rate for c in controls], dtype=float) * 100
    if ctrl_rates.size:
        band = ax.axhspan(np.nanmin(ctrl_rates), np.nanmax(ctrl_rates), color=muted, alpha=0.15)
        band.set_gid("rare-null-band")
        mean = ax.axhline(np.nanmean(ctrl_rates), color=muted, linestyle=":", linewidth=1)
        mean.set_gid("rare-null-mean")
    xs = np.arange(len(counts))
    ax.scatter(xs[:len(controls)], ctrl_rates, color=muted, zorder=3, gid="rare-exceed-control")
    for i, (c, colour) in enumerate(zip(treated, _colours(len(treated)))):
        x = len(controls) + i
        ax.scatter([x], [c.rate * 100], color=colour, zorder=4, gid=f"rare-exceed-treated:{c.key}")
        if np.isfinite(c.empirical_p):
            ax.annotate(f"p={c.empirical_p:.2g}", (x, c.rate * 100), textcoords="offset points", xytext=(0, 6),
                        ha="center", fontsize=_TITLE_PT * 0.8, color=ink)
    ax.set_xticks(xs, [c.key for c in controls + treated], rotation=60, fontsize=_TITLE_PT * 0.8)
    ax.set_ylabel(f"% nanowells above the {unit.primary.percentile:g}th-percentile threshold")
    ax.set_title(f"{unit.label}: exceedance per well over the control-well null "
                 f"({unit.primary.method})", fontsize=_TITLE_PT, color=ink)


def render_hc(fig, unit) -> None:
    """The HC curve against i/n: control wells (leave-one-out) in the secondary ink, treatment wells coloured."""
    axes = _axes_row(fig, 1)
    ax = axes[0]
    _ground, ink, muted = _inks(ax)
    for w in unit.controls:
        h = unit.hc[w.key]
        if len(h.curve_x):
            ax.plot(h.curve_x, h.curve_y, color=muted, linewidth=0.8, gid=f"rare-hc-control:{w.key}")
    for w, colour in zip(unit.treated, _colours(len(unit.treated))):
        h = unit.hc[w.key]
        if not len(h.curve_x):
            continue
        ax.plot(h.curve_x, h.curve_y, color=colour, linewidth=1.3, gid=f"rare-hc-treated:{w.key}",
                label=f"{w.key}: HC={h.value:.2f} at i*={h.i_star}")
        ax.scatter([h.i_star / h.n], [h.value], color=colour, zorder=4, gid=f"rare-hc-istar:{w.key}")
    ax.set_xlabel("i / n")
    ax.set_ylabel("HC+")
    ax.legend(fontsize=_TITLE_PT * 0.8, frameon=False)
    p = unit.hc_perm.p if unit.hc_perm is not None else math.nan
    ax.set_title(f"{unit.label}: Higher Criticism (permutation p = {p:.3g})" if np.isfinite(p)
                 else f"{unit.label}: Higher Criticism (no group p: {unit.refusal or 'undefined'})",
                 fontsize=_TITLE_PT, color=ink)


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
        ax.text(0.5, 0.5, "No nanowell was flagged", ha="center", va="center", gid="rare-crops-none")
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
            ax.text(0.5, 0.5, "no crop path" if not row["crop_path"] else "crop unreadable", ha="center",
                    va="center", fontsize=_TITLE_PT * 0.8, gid=f"rare-crop-missing:{row['well_key']}:{row['nanowell_id']}")
        else:
            ax.imshow(image, cmap="gray", gid=f"rare-crop:{row['well_key']}:{row['nanowell_id']}")
        ax.set_title(title, fontsize=_TITLE_PT * 0.7)
    more = len(responders) - n
    fig.suptitle(f"Flagged nanowells for manual review ({len(responders)} flagged"
                 + (f"; {more} more not drawn" if more > 0 else "") + ")", fontsize=_TITLE_PT)
