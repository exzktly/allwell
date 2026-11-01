"""Draw the Nanowells page's **Statistics** view: the samples compared, above the report of the tests run on them.

Pure matplotlib onto a figure it is handed; every number comes from :mod:`.statistics_compute` and is only drawn
here. The upper panel shows what was compared — a box per sample over its values with its mean marked (one dot per
nanowell, or per well median on the Groups unit, where the mean carries its 95% interval), or the samples'
cumulative distributions on a Kolmogorov-Smirnov run, which is what that test compares. A bracket with stars joins
each pair whose quoted p (``Comparison.p_used``) is below 0.05. The report below is
``statistics_compute.report_lines``, each line a text artist with its gid, so a test reads the figure back rather
than the result (CLAUDE.md §7.4 #1).
"""

from __future__ import annotations

import math

import numpy as np

from review.features.nanowell import statistics_compute as sc
from review.features.nanowell.renderer import GRID_TYPE_SCALE, _ink, _inks, _no_offset, _prepare_axes

#: The report's type size, and the smallest it is shrunk to before lines are left out (and counted) instead.
REPORT_PT, REPORT_MIN_PT = 7.5, 5.5
#: Brackets drawn at most; the rest of the significant comparisons are in the table.
MAX_BRACKETS = 6
#: At most this many dots are drawn per sample (an even subsample, deterministic); the box and mean use them all.
MAX_DOTS = 400
_LEADING = 1.4
#: A monospace character's advance, in ems.
_CHAR_EM = 0.6
_LEFT, _RIGHT = 0.09, 0.97


def _colour(i: int, sample, unit: str) -> str:
    """A sample's colour as every plot tab colours it: a well's rank colour; a group's, its lowest well's."""
    from review.domain.selections_model import color_for_well, color_for_wells
    from ui.theme import WELL_COLORS_TUPLE

    if unit == sc.NANOWELLS:
        return color_for_well(sample.name)
    return color_for_wells(list(sample.wells)) if sample.wells else WELL_COLORS_TUPLE[i % len(WELL_COLORS_TUPLE)]


def _wrap(lines, width: int) -> list[tuple[str, str]]:
    """*lines* wrapped to *width* characters, each continuation indented and keeping its line's gid."""
    import textwrap

    out = []
    for gid, text in lines:
        parts = textwrap.wrap(text, width=max(20, width), subsequent_indent="    ") or [""]
        out += [(gid, part) for part in parts]
    return out


def render_statistics(fig, result: sc.StatisticsResult, *, note: str = "") -> None:
    """Draw *result* into *fig*: the samples above, the report below."""
    fig.clear()
    lines = sc.report_lines(result)
    if note:
        lines.insert(2, (sc.GID_NOTE, note))
    height_pt, width_pt = fig.get_figheight() * 72.0, fig.get_figwidth() * 72.0
    drawable = [s for s in result.samples if s.n]
    plotted = bool(drawable) and not result.undefined
    # The report takes what it needs, up to 55% of the figure when there is a plot above it. Each line is wrapped to
    # the figure's width; the type shrinks before a line is left out, and a line left out is counted on the figure.
    room = (0.55 if plotted else 0.9) * height_pt
    size = REPORT_PT
    while True:
        wrapped = _wrap(lines, int(0.93 * width_pt / (_CHAR_EM * size)))
        if len(wrapped) * size * _LEADING <= room or size <= REPORT_MIN_PT:
            break
        size = max(REPORT_MIN_PT, size - 0.5)
    lines = wrapped
    fits = max(1, int(room // (size * _LEADING)))
    if len(lines) > fits:
        left_out = len(lines) - fits + 1
        lines = lines[:fits - 1] + [(sc.GID_NOTE, f"… {left_out} more line(s) not shown: the figure is too short. "
                                                   "Enlarge the window or narrow the comparison.")]
    step = size * _LEADING / height_pt
    top = 0.03 + step * len(lines)
    ax0 = fig.add_axes([0, 0, 1, 1])
    ax0.set_axis_off()
    _bg, ink, muted = _inks(ax0)
    for i, (gid, text) in enumerate(lines):
        y = top - (i + 1) * step
        muted_line = gid == sc.GID_NOTE
        _ink(fig.text(0.02, y, text, fontsize=size, family="monospace", color=muted if muted_line else ink, gid=gid,
                      fontweight="bold" if gid == sc.GID_HEADER and i == 0 else "normal"), muted=muted_line)
    if plotted:
        ax = fig.add_axes([_LEFT, top + 0.11, _RIGHT - _LEFT, max(0.15, 0.94 - top - 0.11)])
        _prepare_axes(ax, GRID_TYPE_SCALE)
        if result.test["key"] == "ks":
            _ecdfs(ax, result, ink)
        else:
            _boxes(ax, result, ink, muted)
        _no_offset(ax)


def _ecdfs(ax, result, ink) -> None:
    for i, s in enumerate(result.samples):
        if not s.n:
            continue
        x = np.sort(s.values)
        y = np.arange(1, x.size + 1) / x.size
        ax.step(x, y, where="post", color=_colour(i, s, result.unit), linewidth=1.4,
                label=f"{s.label or s.name} (n = {s.n:,})")
    ax.set_ylim(0, 1.02)
    ax.set_xlabel(result.label, color=ink)
    ax.set_ylabel("Cumulative fraction", color=ink)
    ax.grid(alpha=0.3)
    legend = ax.legend(loc="lower right", frameon=False, fontsize=7)
    for text in legend.get_texts():
        _ink(text)
        text.set_gid("nw-stats-legend")


def _boxes(ax, result, ink, muted) -> None:
    positions = {}
    for i, s in enumerate(result.samples):
        positions[s.name] = float(i)
        colour = _colour(i, s, result.unit)
        if not s.n:
            _ink(ax.text(i, 0.5, "no values", transform=ax.get_xaxis_transform(), ha="center", va="center",
                         fontsize=7, color=muted, gid="nw-stats-empty"), muted=True)
            continue
        if result.unit == sc.NANOWELLS and s.n >= 2:
            box = ax.boxplot([s.values], positions=[i], widths=0.5, showfliers=False, patch_artist=True,
                             medianprops={"color": ink}, whiskerprops={"color": ink}, capprops={"color": ink},
                             boxprops={"edgecolor": ink, "facecolor": "none"})
            for artist in box["boxes"] + box["whiskers"] + box["caps"] + box["medians"]:
                _ink(artist)
        shown = s.values if s.n <= MAX_DOTS else s.values[np.linspace(0, s.n - 1, MAX_DOTS).astype(int)]
        rng = np.random.default_rng(i)                      # the same jitter on every redraw
        ax.scatter(i + rng.uniform(-0.18, 0.18, shown.size), shown, s=6 if result.unit == sc.NANOWELLS else 22,
                   color=colour, alpha=0.45 if result.unit == sc.NANOWELLS else 0.9, linewidths=0, zorder=3)
        mean = s.mean()
        if result.unit == sc.WELLS and s.n >= 2:
            lo, hi = s.ci95()
            _ink(ax.errorbar([i + 0.3], [mean], yerr=[[mean - lo], [hi - mean]], fmt="D", color=ink, markersize=4,
                             capsize=3, linewidth=1.1, zorder=4)[0])
        else:
            _ink(ax.plot([i], [mean], marker="D", color=ink, markersize=4, linestyle="none", zorder=4)[0])
    ax.set_xticks(range(len(result.samples)))
    ax.set_xticklabels([f"{s.label or s.name}\nn = {s.n:,}" for s in result.samples])
    ax.set_xlim(-0.6, len(result.samples) - 0.4)
    ax.set_ylabel(result.label, color=ink)
    ax.grid(axis="y", alpha=0.3)
    _brackets(ax, result, positions, ink)


def _brackets(ax, result, positions, ink) -> None:
    from review.domain.significance import significance_stars

    hits = sorted((c for c in result.comparisons if c.ok and c.p_used is not None and c.p_used < 0.05),
                  key=lambda c: abs(positions[c.a] - positions[c.b]))[:MAX_BRACKETS]
    if not hits:
        return
    low, high = ax.get_ylim()
    span = (high - low) or 1.0
    level = high
    for c in hits:
        x0, x1 = sorted((positions[c.a], positions[c.b]))
        level += 0.08 * span
        _ink(ax.plot([x0, x0, x1, x1], [level - 0.025 * span, level, level, level - 0.025 * span], color=ink,
                     linewidth=0.9)[0])
        _ink(ax.text((x0 + x1) / 2, level, significance_stars(c.p_used), ha="center", va="bottom", fontsize=8,
                     color=ink, gid="nw-stats-bracket"))
    ax.set_ylim(low, level + 0.1 * span if math.isfinite(level) else high)
