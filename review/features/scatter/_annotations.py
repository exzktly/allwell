"""What is written over the scatter's points: the value labels and the legends.

Nothing here decides what a point *is* — the same seam ``barplot/_annotations``
draws between the bars and the text over them. Two things live on this side of
it: the value-label overlay, and the aggregate figure's two compact legend keys
(well/group as marker shape, timepoint as colour), which are a key to the points
rather than a property of any one of them.

The Properties → Overlays switch (``PlotState.show_value_labels``) is global —
it states the plotted statistic, and that is the same statistic on whichever
tab is showing it — so it has to mean something on every tab that draws a
statistic. The Scatter tab draws two very different things under one name, and
they answer it differently:

* the **Aggregate** view plots one point per well/group and timepoint, each of
  them a ``mean ± spread`` on both axes. That is exactly what the overlay
  states, so every point is labelled with both of its coordinates through
  :func:`~review.core.plot_annotations.annotate_point`;
* the **per-cell** view plots one point per segmented cell. There is no
  statistic under a cell to state, and a plate's worth of cells is tens of
  thousands of points, so the overlay draws nothing there — the same call the
  Line tab's CDF panel makes, and for the same reason.

Drawing nothing is where this went wrong before. With the switch on and the
per-cell view showing, the overlay was indistinguishable from an overlay that
did not work — which is what it was reported as. So the per-cell path says so
on the figure, in muted type, the way ``barplot/renderer._no_significance``
does: attached to the artifact rather than to a status bar, and cleared by the
same ``cla()`` that clears every other artist, so it cannot outlive the state
that produced it.
"""

from __future__ import annotations

import math

from review.core.plot_annotations import LABEL_SPAN_HEADROOM, annotate_point
from review.core.plot_style import style_legend

#: Marks the per-cell view's "the overlay draws nothing here" note, so a later
#: pass — or a test — can find it among the panel's other texts.
VALUE_LABEL_NOTE_TAG = "_aw_value_label_note"

#: What that note says. It names the view that *does* carry the labels rather
#: than only declining, because the user reaches this with the switch already
#: on and the useful fact is where their numbers are.
PER_CELL_NOTE = "Value labels: Aggregate view only — each point here is one cell."


def annotate_agg_points(settings: tuple[bool, str], ax, scatter_data) -> int:
    """Label every aggregate point with its ``(x, y)`` pair. Returns the count.

    *settings* is the ``(enabled, fmt)`` pair from
    :func:`~review.core.plot_annotations.value_label_settings` — passed in
    rather than read off a ``ctx`` here because the Batch Export twin of this
    figure draws from its own ctx and must be able to hand the pair over
    explicitly, exactly as the bar and line paths do.

    Each label wears its point's own colour, which on this figure is the
    timepoint's place on the viridis gradient: with one label per well and
    timepoint, matching the marker is what says which point a number belongs
    to. That also keeps it out of the theme repaint, since a colour carrying
    meaning is data and not chrome.

    The spreads come from the same ``x_err`` / ``y_err`` the error bars are
    drawn from, so a label states the whisker beside it and never any other;
    a spread that was not computed prints no ± at all.
    """
    enabled, fmt = settings
    if ax is None or not enabled or not scatter_data:
        return 0
    drawn = 0
    for data in scatter_data.values():
        xs = list(data.get("x") or [])
        ys = list(data.get("y") or [])
        x_err = list(data.get("x_err") or [])
        y_err = list(data.get("y_err") or [])
        color = data.get("color")
        for i, (x, y) in enumerate(zip(xs, ys)):
            annotate_point(
                ax, x, y,
                x_spread=x_err[i] if i < len(x_err) else None,
                y_spread=y_err[i] if i < len(y_err) else None,
                fmt=fmt, color=color,
            )
            drawn += 1
    if drawn:
        pad_axes_for_labels(ax)
    return drawn


def pad_axes_for_labels(ax) -> None:
    """Widen *ax*'s y-limits so a label on the topmost point clears the title.

    The labels are drawn beyond the point in *ink* — seven typographic points
    above it — which matplotlib's own 5% margin knows nothing about, so the
    extreme point's label lands on the title. The same allowance the line
    panels take (``LABEL_SPAN_HEADROOM``, a multiplier on a span matplotlib has
    already autoscaled), so two surfaces do not reserve different amounts of
    room for the same text.

    A widening only, never a crop: a figure with the labels on must not hide
    data to make room for text. On a log axis the pad is applied in log space —
    adding a linear pad there is either invisible or, at the floor, an invalid
    limit.
    """
    if ax is None:
        return
    lo, hi = ax.get_ylim()
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return
    extra = LABEL_SPAN_HEADROOM - 1.0
    if ax.get_yscale() == "log":
        if lo <= 0 or hi <= 0:
            return
        ax.set_ylim(lo, hi * (hi / lo) ** extra)
        return
    pad = (hi - lo) * extra
    # Padded at the floor too when the panel holds negatives: a label on a
    # point below zero is drawn below it, where the top padding does nothing.
    ax.set_ylim(lo - (pad if lo < 0 else 0.0), hi + pad)


def note_value_labels_unavailable(settings: tuple[bool, str], ax) -> None:
    """Say on the per-cell figure that the overlay draws nothing here.

    Only when the switch is on: a figure whose overlay was never asked for has
    nothing to explain, and a note on every scatter export would be noise.
    """
    enabled, _fmt = settings
    if ax is None or not enabled:
        return
    from review.core.plot_style import follows_theme, tokens_for
    note = follows_theme(ax.text(
        0.01, 0.98, PER_CELL_NOTE, transform=ax.transAxes,
        ha="left", va="top", fontsize=7, color=tokens_for(ax)[2],
        zorder=6, clip_on=False), muted=True)
    setattr(note, VALUE_LABEL_NOTE_TAG, True)


def sampled_note(scatter_data) -> str:
    """The sentence a sampled figure carries, or ``""`` when every cell is drawn."""
    total = sum(int(d.get("n_total", 0)) for d in scatter_data.values())
    drawn = sum(int(d.get("n_drawn", 0)) for d in scatter_data.values())
    if not any("n_total" in d for d in scatter_data.values()) or drawn >= total:
        return ""
    return (f"Drawn: a sample of {drawn:,} of {total:,} cells, in proportion per group; "
            "gate percentages count every cell")


#: Tag on the sampled-figure note, so a test (and a redraw) can find it.
SAMPLED_NOTE_TAG = "_aw_scatter_sampled_note"


def note_sampled(scatter_data, ax) -> None:
    """Say on the figure that its points are a sample, when they are.

    The sentence comes from :func:`sampled_note`; a figure that
    draws every cell carries nothing. Bottom-left, clear of the value-label
    note at the top.
    """
    text = sampled_note(scatter_data)
    if ax is None or not text:
        return
    from review.core.plot_style import follows_theme, tokens_for
    note = follows_theme(ax.text(
        0.01, 0.01, text, transform=ax.transAxes,
        ha="left", va="bottom", fontsize=7, color=tokens_for(ax)[2],
        zorder=6, clip_on=False), muted=True)
    setattr(note, SAMPLED_NOTE_TAG, True)


def draw_scatter_agg_legends(ax_agg, scatter_data, marker_fg) -> None:
    """Build the aggregate scatter's two compact legends from proxy handles.

    The aggregate encodes **well/group identity as marker shape** and
    **timepoint as colour**, so a per-point legend explodes to
    N_wells × N_timepoints rows. Instead draw two small keys:

    - a **wells** legend — one entry per well/group, its marker in a neutral
      colour (``marker_fg``), labelled with the Sample-Definition display name;
    - a **timepoint** colour key — one filled dot per timepoint in its viridis
      colour, labelled ``t=Xh``.

    Single-entry keys are skipped (one group, or one timepoint) so a redundant
    one-row box never appears.
    """
    from matplotlib.lines import Line2D
    from matplotlib.legend import Legend

    wells: dict[str, str] = {}      # group_label -> marker
    tps: dict[float, object] = {}   # timepoint  -> colour
    for data in scatter_data.values():
        glabel = data.get('group_label') or data.get('label', '')
        if glabel not in wells:
            wells[glabel] = data.get('marker', 'o')
        tp = data.get('timepoint')
        if tp is not None and tp not in tps:
            tps[tp] = data.get('color', (0, 0, 0, 1))

    # Both keys are built as *standalone* ``Legend`` artists via ``add_artist``
    # (not ``ax.legend``), which leaves ``ax.legend_`` unset. That is deliberate:
    # ``apply_export_style_to_current`` repositions ``ax.get_legend()`` to the
    # ``legend_loc`` pref ("best"), which would otherwise drop the timepoint key
    # on top of the wells key. Keeping them off ``legend_`` pins their corners.
    def _add(handles, loc, title) -> None:
        leg = Legend(ax_agg, handles, [h.get_label() for h in handles],
                     loc=loc, fontsize=8, title=title)
        leg.get_frame().set_facecolor("none")
        leg.get_frame().set_alpha(0.0)
        ax_agg.add_artist(leg)
        style_legend(leg, ax_agg)

    if len(wells) > 1:
        _add(
            [Line2D([], [], linestyle='none', marker=mk, markersize=8,
                    color=marker_fg, label=glabel)
             for glabel, mk in wells.items()],
            'upper left', "Well / group",
        )
    if len(tps) > 1:
        _add(
            [Line2D([], [], linestyle='none', marker='o', markersize=8,
                    color=color, label=f"t={tp}h")
             for tp, color in sorted(tps.items())],
            'lower right', "Timepoint",
        )
