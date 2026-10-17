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

from review.core.plot_annotations import (
    _LABEL_OFFSET_OVER_MARK_PT,
    LABEL_HEADROOM,
    annotate_value,
)
from review.features.barplot._decorate import bar_legend_entries
from review.features.barplot.collect import cluster_centers, subcluster_centers
from core.safe import swallowed
from review.domain.numeric import is_number


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
        from review.core.plot_style import axis_scoped_value, figure_prefs
        prefs = figure_prefs(ctx, fig)
        idx = list(fig.axes).index(ax) + 1
        raw = axis_scoped_value(prefs, idx, "y_lim_max", None)
        if raw in (None, ""):
            return None
        return float(raw)
    except Exception:
        swallowed("barplot: could not read the panel's user y-limit")
        return None


#: How far above the sub-band row the band row sits, in points, when a second
#: split level is on. One 8 pt line plus a little air.
_BAND_ROW_LIFT_PT = 11

#: A bar panel's title pad, in points, for none / one / two header rows.
#: The standard pad is ``apply_ax_style``'s; each header row adds a line.
_TITLE_PAD_PT = (6, 18, 30)


def cluster_header_pad(items) -> int:
    """The title pad each bar panel needs to clear its cluster header rows.

    One answer for both figures that draw these headers — the Bar tab and
    Batch Export's bar panel — because Batch Export styles its title through
    ``apply_ax_style`` and never reached the renderer's own pad, so its header
    rows were printed over the panel title and, with a second split level,
    over the figure's suptitle as well. Call it on the items *after*
    ``render_bar_items`` has normalised them: that is when a redundant header
    has been dropped and the rows left are the ones actually drawn.
    """
    rows = (1 if any(getattr(it, "group_key", "") for it in items) else 0) + (
        1 if any(getattr(it, "subgroup_key", "") for it in items) else 0)
    return _TITLE_PAD_PT[rows]


def annotate_cluster_headers(items, xs: list[float], ax) -> None:
    """Per-cluster label centred above a panel — each of the bar figure's panels.

    With a second split level (*Then by*), two rows: the sub-band labels sit
    where the band labels normally do, directly over their bars, and the band
    labels move up a line above them — outer above inner, the way a nested
    table header reads.
    """
    if not items or not xs:
        return
    from review.core.plot_style import follows_theme, tokens_for
    _fg, _muted = tokens_for(ax)[1], tokens_for(ax)[2]
    subs = list(subcluster_centers(items, xs))
    lift = _BAND_ROW_LIFT_PT if subs else 0
    for gk, cx in cluster_centers(items, xs):
        follows_theme(ax.annotate(
            gk, xy=(cx, 1.02), xycoords=ax.get_xaxis_transform(),
            xytext=(0, lift), textcoords="offset points",
            ha="center", va="bottom", fontsize=8, fontweight="bold",
            color=_fg, annotation_clip=False))
    for sub, cx in subs:
        follows_theme(ax.text(cx, 1.02, sub, transform=ax.get_xaxis_transform(),
                              ha="center", va="bottom", fontsize=7,
                              color=_muted), muted=True)


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
    :func:`~review.core.plot_annotations.value_label_settings` — passed in
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


#: Marks the Colour-by legend and the Split-by notice, so a later pass — or a
#: test — can find them among the panel's other artists.
DECORATION_TAG = "_aw_decoration"


def annotate_decorations(ctx, items, xs, ax, *, headers: bool,
                         wells_by_key=None, header_axes=()) -> None:
    """Every annotation pass over the mean panel, in one call.

    The cluster headers also go over each of *header_axes* — the bar figure's
    Fraction and Events panels. Each panel is read on its own and its bars are
    banded the same way; with the headers on the top panel alone, the lower two
    showed the grouping as gaps between bars, with nothing naming the groups.

    The cluster headers, the Colour-by legend and the Split-by notice. Together
    because they always go together and are all about the same thing — how the
    bars are grouped and inked — and because a caller that drew the legend and
    forgot the notice would produce the figure this arrangement exists to
    prevent: bars banded by a variable, with nothing on the axes saying that
    the split replaced the layout the user had arranged.

    All three are ordinary artists, so the next redraw's ``cla()`` clears them
    — the property that keeps them from going stale, and the reason none of
    them is written to the persistent status bar.
    """
    if ax is None:
        return
    if headers:
        for _ax in (ax, *header_axes):
            if _ax is not None:
                annotate_cluster_headers(items, xs, _ax)
    setattr(ax, "_aw_bar_items", list(items or ()))
    annotate_colour_legend(ctx, ax, wells_by_key=wells_by_key)
    annotate_split_notice(ctx, ax)


def _bar_colour_mode(ctx) -> str:
    """The Bar tab's Colours setting, or ``"plate"`` when there is no ctx.

    ``"plate"`` is the default and the mode in which the decoration inks
    actually reach the bars; ``"grey"`` replaces every bar's colour with
    ``mono_bar_color``.
    """
    plot = getattr(getattr(ctx, "state", None), "plot", None)
    return str(getattr(plot, "bar_color_mode", "plate") or "plate")


def annotate_colour_legend(ctx, ax, *, wells_by_key=None) -> None:
    """A swatch-and-label legend for the *Colour by* variable, or nothing.

    Nothing in plate-colour mode, which is the default and needs no legend
    because the plate *is* the legend — that is the whole argument for it being
    the default. Once the bars are coloured by a variable instead, the plate no
    longer explains them and the figure has to.

    Drawn from the *drawn* bars, so it lists the values on screen rather than
    every value the variable defines. A legend naming six doses over a figure
    showing two is a claim about what is in front of the reader, and the reader
    counts the legend.

    **Colour is the only channel here, which is a constraint on the palette
    rather than on this legend.** A reader who cannot separate two inks cannot
    use the key either, however it is drawn — so the answer is inks a dichromat
    can separate, which is what ``variable_series.VALUE_PICKS`` is chosen and
    measured for, and not a second visual channel bolted onto the legend. A
    redundant encoding on the bars themselves (hatching, say) was not added:
    *Split by* already gives the same fact a labelled position when a reader
    wants one that does not depend on colour at all, and hatching every bar to
    duplicate a distinction the palette already carries would cost every
    figure legibility to serve a case the palette covers.
    """
    if ax is None:
        return
    from matplotlib.patches import Patch

    from review.core.plot_style import follows_theme

    # Nothing in grey mode either, and for the same reason: the bars are not
    # carrying the variable's inks. ``redraw_bars`` passes ``mono_bar_color``
    # for every bar, which discards the ``item.color`` ``decorate_bar_items``
    # just set — so a legend here would be a key to a colouring the figure does
    # not have, printed over uniformly grey bars for a reader to map onto them.
    # Worse than no legend, because it is confidently wrong.
    if _bar_colour_mode(ctx) != "plate":
        return

    items = list(getattr(ax, "_aw_bar_items", ()) or ())
    entries = bar_legend_entries(ctx, items, wells_by_key=wells_by_key)
    if not entries:
        return
    handles = [Patch(facecolor=colour, edgecolor="none", label=label)
               for label, colour in entries]
    legend = ax.legend(handles=handles, loc="upper right", fontsize=7,
                       framealpha=0.0, handlelength=1.0, borderpad=0.2)
    setattr(legend, DECORATION_TAG, True)
    follows_theme(legend)


def annotate_split_notice(ctx, ax) -> None:
    """Say what *Split by* had to replace, when it replaced something.

    On the axes rather than on the status line, for the reason the significance
    note is: ``notify.set_status`` writes the persistent bar and a redraw with
    nothing to report has no reason to clear it, so the sentence outlives its
    condition. A note drawn here is cleared by the same redraw that resolves it.
    """
    if ax is None:
        return
    from review.core.plot_style import follows_theme, tokens_for

    reader = getattr(getattr(ctx, "state", None), "feature_slice", None)
    slice_ = reader("bar") if callable(reader) else None
    notice = str(getattr(slice_, "split_notice", "") or "")
    if not notice:
        return
    note = follows_theme(ax.text(
        0.01, 0.94, notice, transform=ax.transAxes, ha="left", va="top",
        fontsize=7, color=tokens_for(ax)[2], zorder=6, clip_on=False),
        muted=True)
    setattr(note, DECORATION_TAG, True)
