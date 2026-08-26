"""Text and marks drawn over the bar panels.

Three annotation passes, none of which is a bar: the per-cluster header above
the Mean panel, the ``n/a`` mark that says a bar's spread could not be
computed, and the opt-in ``mean ± spread`` value labels. They were inline in
``controller.render_bar_items``, which is under a line budget and is already
the file that decides what every bar *is*; collected here so the drawing of the
data and the labelling of it are separate things to read.

The value-label pass is deliberately a second walk over the same items rather
than a branch inside the draw loop: it needs the error-bar settings the loop
resolved, it must run after the bars so its text sits on top, and keeping it
out of the loop means the panel that draws no labels pays one boolean.
"""

from __future__ import annotations

import math

from well_viewer.core.plot_annotations import (
    _LABEL_OFFSET_OVER_MARK_PT,
    LABEL_HEADROOM,
    annotate_value,
)
from well_viewer.features.barplot.collect import cluster_centers
from well_viewer.core.safe import swallowed
from well_viewer.domain.numeric import is_number


def user_y_ceiling(ctx, ax) -> float | None:
    """The user's own upper y-limit for *ax*, or ``None`` when they set none.

    Read through the same per-panel lookup ``apply_export_style_prefs`` uses —
    the figure's ``axis_buckets`` entry for this panel first, then the flat
    pref — so the number this function honours is the number that pass will
    apply afterwards. Reading only the flat pref would have been right for a
    single-axis figure and wrong for the Value / Fraction / Events stack, which
    is the figure this actually draws on.

    ``None`` means "no override", which is not the same as a limit of zero; a
    caller that conflated them would refuse to draw on a panel the user had
    simply left alone.

    Two annotation passes need it — the significance brackets stack under it,
    and a value label near it is turned inward — so it lives here with them
    rather than in one of the two.
    """
    fig = getattr(ax, "figure", None)
    if fig is None:
        return None
    try:
        from well_viewer.core.plot_style import axis_scoped_value, figure_prefs
        prefs = figure_prefs(ctx, fig)
        idx = list(fig.axes).index(ax) + 1
        raw = axis_scoped_value(prefs, idx, "y_lim_max", None)
        if raw in (None, ""):
            return None
        return float(raw)
    except Exception:
        swallowed("barplot: could not read the panel's user y-limit")
        return None


def annotate_cluster_headers(items, xs: list[float], ax) -> None:
    """Per-cluster label centred above the mean panel."""
    if not items or not xs:
        return
    from well_viewer.core.plot_style import tokens_for
    _fg = tokens_for(ax)[1]
    for gk, cx in cluster_centers(items, xs):
        ax.text(cx, 1.02, gk, transform=ax.get_xaxis_transform(),
                ha="center", va="bottom", fontsize=8, fontweight="bold",
                color=_fg)


def mark_undefined_spread(ax, x: float, y: float, color: str) -> None:
    """Mark a bar whose spread could not be computed, when error bars are on.

    A hollow cap and an ``n/a`` above the bar, in the error-bar colour. The
    point is only to make "not computed" visually distinct from "computed and
    small" — a bar that silently loses its whisker is indistinguishable from one
    whose error bars were turned off, and that ambiguity is how a well with
    pooled (unusable) FOV labels passed for a well with tight replicates.
    """
    if y is None or not math.isfinite(float(y)):
        return
    ax.plot([x], [y], marker="_", markersize=9, markeredgewidth=1.4,
            color=color, alpha=0.7, linestyle="none", zorder=4)
    ax.annotate("n/a", (x, y), textcoords="offset points", xytext=(0, 7),
                ha="center", va="bottom", fontsize=7, color=color, alpha=0.85,
                zorder=5, annotation_clip=False)


def label_bar(ax, x: float, value, spread, *, draw_err: bool,
              ci_scale: float, fmt: str, ceiling: float | None = None) -> None:
    """One bar's value label, anchored clear of its error cap.

    *spread* is printed — and used to place the text — only when an error bar
    was actually drawn for this bar. With the Show mode on "Mean", or the
    error-bar type on "None", the whisker is absent and so is the ± half: a
    label claiming a spread the panel does not draw would be the figure
    describing a plot other than itself.

    Two things move the text off its default place. A bar whose spread is
    undefined already carries :func:`mark_undefined_spread`'s ``n/a`` at the
    same anchor and the same offset, so the value stacks above it rather than
    on top of it. And when *ceiling* names a y-limit the user pinned for this
    panel, a label that would sit near or above it is drawn on the inside of
    the bar instead: ``apply_export_style_to_current`` re-applies that limit
    after the draw, and these labels carry ``annotation_clip=False``, so an
    outside one would end up over the panel title rather than clipped.
    """
    if ax is None or value is None:
        return
    try:
        v = float(value)
        s = float(spread) if spread is not None else float("nan")
    except (TypeError, ValueError):
        return
    if not math.isfinite(v):
        return
    drawn = s * ci_scale if (draw_err and math.isfinite(s) and s > 0) else None
    anchor = (v + drawn if v >= 0 else v - drawn) if drawn is not None else v
    marked = draw_err and math.isnan(s)
    annotate_value(
        ax, x, v, drawn, fmt=fmt, anchor=anchor,
        offset_pt=_LABEL_OFFSET_OVER_MARK_PT if marked else None,
        inside=_over_ceiling(anchor, ceiling, ax),
    )


def _over_ceiling(anchor: float, ceiling: float | None, ax) -> bool:
    """Whether a label at *anchor* would be drawn at or above the user's
    *ceiling* for this panel.

    The margin is the headroom the panel would otherwise have grown by, since
    that is the room the text was sized against; a pinned ceiling is exactly
    the case where that growth does not happen.
    """
    if ceiling is None or not math.isfinite(anchor):
        return False
    lo, hi = ax.get_ylim()
    span = abs(hi - lo) or abs(ceiling) or 1.0
    return anchor > ceiling - span * (LABEL_HEADROOM - 1.0)


def annotate_bar_values(settings: tuple[bool, str], items, xs: list[float], *,
                        ax_mean, ax_frac, ax_n=None, ci_scale: float = 1.0,
                        draw_err: bool = True, ctx=None) -> None:
    """Draw ``mean ± spread`` above every drawn bar, when the overlay is on.

    *settings* is the ``(enabled, fmt)`` pair from
    :func:`~well_viewer.core.plot_annotations.value_label_settings` — passed in
    rather than read off a ``ctx`` here because the batch-export bar path
    renders these panels with ``ctx=None`` on purpose and still wants the
    overlay, so it resolves the pair from its own ctx and hands it over.

    Each panel is labelled with the statistic it draws, gated on the same
    has-flag the bar was: a placeholder column (nothing was computable) gets no
    label, because a number over a dashed placeholder would be the one bar on
    the panel whose text and shape disagree.

    *ctx*, when given, is used for one thing only: reading each panel's pinned
    upper y-limit, so a label near it is turned inward instead of being drawn
    over the title. It is read once per panel rather than once per bar.
    """
    enabled, fmt = settings
    if not enabled:
        return
    ceilings = {id(ax): user_y_ceiling(ctx, ax)
                for ax in (ax_mean, ax_frac, ax_n) if ax is not None}
    for i, item in enumerate(items):
        x = xs[i] if i < len(xs) else i
        if item.has_mean:
            label_bar(ax_mean, x, item.mean, item.spread, draw_err=draw_err,
                      ci_scale=ci_scale, fmt=fmt, ceiling=ceilings[id(ax_mean)])
        if item.has_frac:
            label_bar(ax_frac, x, item.frac, item.frac_spread, draw_err=draw_err,
                      ci_scale=ci_scale, fmt=fmt, ceiling=ceilings[id(ax_frac)])
        if ax_n is not None and is_number(item.n_above):
            label_bar(ax_n, x, item.n_above, item.n_above_spread, draw_err=draw_err,
                      ci_scale=ci_scale, fmt=fmt, ceiling=ceilings[id(ax_n)])
