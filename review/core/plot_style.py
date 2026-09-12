"""Shared matplotlib axis styling.

Extracted from ``review.runtime_app`` so plot controllers and export
panels can apply the canonical axis look without importing the GUI monolith.

When the axes belongs to a figure inside a ``widgets.PlotCard``, the styling
honours the card's active ``plotTheme()`` — ``"publication"`` (white bg,
``theme.CPub`` ink — the canonical / exported state) or ``"screen"`` (the dark
token set, a per-card live preview). When the axes is on a bare figure (no
card), falls back directly to ``theme.CPub`` (publication-style).
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping

from ui import theme
from core.safe import swallowed

_logger = logging.getLogger("review")

_PLOT_FONT_REGISTERED = False  # matplotlib font-manager registration runs once


def _plot_font_family() -> str:
    """Font family for matplotlib text — the **shared** UI font token
    (``theme.Typography.family``, i.e. the bundled Inter), so plots always match
    the Qt chrome from a single source of truth. The bundled ``.ttf`` is
    registered with matplotlib's font manager on first use (keeps matplotlib out
    of the startup path). Falls back to the legacy ``"Helvetica"`` request
    (→ matplotlib's own default) when no token/font is available.
    """
    global _PLOT_FONT_REGISTERED
    if not _PLOT_FONT_REGISTERED:
        _PLOT_FONT_REGISTERED = True
        try:
            from startup.app_fonts import register_mpl_ui_font
            register_mpl_ui_font()  # make matplotlib aware of the bundled .ttf
        except Exception:
            swallowed()
    try:
        from ui import theme as _theme
        return _theme.Typography.family or "Helvetica"
    except Exception:
        return "Helvetica"


def tokens_for(ax) -> tuple:
    """Return ``(bg, title_fg, muted_fg, grid, spine)`` for *ax*'s figure.

    Resolution order:
      1. the active ``PlotCard.plotTheme()`` via ``ax.figure._plot_card`` (the
         back-ref ``PlotCard.__init__`` sets);
      2. a fallback that infers ``"screen"`` vs ``"publication"`` from the
         figure's *current* facecolor (``setPlotTheme`` sets it to the right
         value before emitting, so this works even if the back-ref is missing);
      3. the legacy ``ui.theme`` constants (publication-style) for bare figures.
    """
    fig = getattr(ax, "figure", None)
    if fig is None:
        return theme.CPub.bg, theme.Colors.text_primary, theme.CPub.text, theme.CPub.grid, theme.CPub.spine

    mode = None
    card = getattr(fig, "_plot_card", None)
    if card is not None:
        try:
            mode = card.plotTheme()
        except Exception:
            mode = None
    if mode is None:
        # Infer from the figure facecolor (set by PlotCard.setPlotTheme before
        # plotThemeChanged fires, so it tracks the user's most recent toggle).
        try:
            rgba = fig.get_facecolor()
            brightness = sum(float(c) for c in rgba[:3])
            mode = "publication" if brightness > 1.5 else "screen"
        except Exception:
            mode = "publication"
    try:
        from widgets.plot_card import plot_tokens
        return plot_tokens(mode)
    except Exception:
        return theme.CPub.bg, theme.Colors.text_primary, theme.CPub.text, theme.CPub.grid, theme.CPub.spine


# Re-exported so a renderer takes its ink and its "this is ink" marker from one
# import. ``follows_theme`` lives in ``widgets`` because the repaint that honours
# it is ``PlotCard.setPlotTheme``'s, and ``widgets`` carries no app coupling.
from widgets.plot_card import follows_theme  # noqa: E402  (re-export)

__all__ = [*globals().get("__all__", []), "follows_theme"]


# Internal alias (was the original name).
_tokens_for = tokens_for


def reconfigure_stacked_axes(fig, n_axes: int, build):
    """Reuse *fig*'s existing axes when their count matches *n_axes* (clearing
    them in place — avoids the costly ``fig.clear()`` + canvas resize on every
    redraw); otherwise preserve the ``_plot_card`` back-ref across ``fig.clear``
    and rebuild via ``build(fig)``. Returns the axes list.

    Extracted from the scatter (`_setup_scatter_axes`) and distribution
    (`_setup_axes`) tabs, which both hand-rolled this reuse/clear/preserve prefix
    (audit A16); the per-tab subplot layout stays in each ``build`` closure.
    """
    existing = list(fig.axes)
    if len(existing) == n_axes:
        for ax in existing:
            ax.clear()
        return existing
    # ``fig.clear()`` resets figure-level attributes, which can wipe the
    # ``_plot_card`` back-ref that ``tokens_for`` consults — re-attach it after
    # rebuilding so the next card-style read gets the right palette.
    card = getattr(fig, "_plot_card", None)
    fig.clear()
    if card is not None:
        try:
            fig._plot_card = card
        except Exception:
            swallowed()
    return build(fig)


def apply_ax_style(ax, title: str, ylabel: str) -> None:
    """Apply the standard plot style to *ax*."""
    bg, title_fg, muted_fg, grid, spine = tokens_for(ax)
    ax.set_facecolor(bg)
    fig = getattr(ax, "figure", None)
    if fig is not None:
        fig.set_facecolor(bg)
    for sp in ax.spines.values():
        sp.set_color(spine)
        sp.set_linewidth(0.8)
    ax.tick_params(colors=muted_fg, labelsize=8)
    ax.xaxis.label.set_color(muted_fg)
    ax.yaxis.label.set_color(muted_fg)
    ax.set_title(title, color=title_fg, fontsize=9, fontweight="bold", pad=6)
    ax.set_ylabel(ylabel, fontsize=8, labelpad=5, color=muted_fg)
    ax.grid(True, color=grid, linewidth=0.7, linestyle="-")


#: Visual properties a legend's swatch copies from the trace it stands for.
#: Width, dash, marker shape and size, and both marker colours — everything the
#: Properties sidebar's Line and Marker rows can change. Colour is deliberately
#: absent: the swatch already carries the trace's colour from when the legend
#: was built, and re-copying it would be the one property that a *theme* flip,
#: rather than a pref, is entitled to move.
_LEGEND_COPIED_PROPS = (
    "linewidth", "linestyle", "marker", "markersize",
    "markerfacecolor", "markeredgecolor", "markeredgewidth",
)


def sync_legend_handles(ax) -> None:
    """Make each legend swatch look like the trace it names.

    matplotlib builds a legend from *copies* of the artists, taken when the
    legend is created. Everything the export-style pass then does to the data
    lines — width, dash, marker shape, size, fill — lands on the originals and
    on nothing else, so raising the line width to 6 drew six-point traces
    beside a legend still showing the two-point ones it was born with. Measured
    on all four: width, style, marker and marker size were each dropped.

    The swatch is **copied from its own trace** rather than restyled from the
    prefs a second time. That is what makes it right in both colour modes
    without knowing about either: in plate mode the trace carries the pref, so
    the swatch gets the pref; in grey mode the trace carries the dash and
    marker that identify it, so the swatch gets those — and a legend that
    re-derived from the prefs would show twelve identical swatches beside
    twelve distinguishable traces, which is worse than the bug being fixed.

    Paired by **label**, not by position: ``_capped_legend`` truncates a long
    legend and appends a "+N more" entry that names no trace, so an index-wise
    pairing would shift every swatch by one the moment a plate-wide selection
    overflowed. An entry with no matching line is left exactly as it is.
    """
    leg = ax.get_legend()
    if leg is None:
        return
    sources = {}
    for line in ax.lines:
        label = str(line.get_label())
        if label and not label.startswith("_") and label not in sources:
            sources[label] = line
    handles = getattr(leg, "legend_handles", None)
    if handles is None:  # matplotlib < 3.7 spelling
        handles = getattr(leg, "legendHandles", [])
    for handle, text in zip(handles, leg.get_texts()):
        source = sources.get(str(text.get_text()))
        if source is None:
            continue
        for prop in _LEGEND_COPIED_PROPS:
            getter = getattr(source, f"get_{prop}", None)
            setter = getattr(handle, f"set_{prop}", None)
            if getter is None or setter is None:
                continue
            try:
                setter(getter())
            except (TypeError, ValueError):
                swallowed("plot_style: a legend swatch refused a trace property")


def legends_on(ax) -> list:
    """Every legend drawn on *ax*, in the order matplotlib holds them.

    Two kinds, and only one of them is reachable through ``ax.get_legend()``:
    the legend ``ax.legend()`` builds, which is stored as ``ax.legend_``, and
    any number of standalone ``Legend`` artists a caller made itself and passed
    to ``ax.add_artist``. The second kind exists here for a reason —
    ``scatter/_annotations.draw_scatter_agg_legends`` draws a wells key and a
    timepoint key side by side, and keeping them off ``ax.legend_`` is what
    stops the style pass repositioning both to ``legend_loc`` and stacking one
    on the other — but the consequence nobody wrote down is that everything
    else the style pass does to a legend skipped them too.

    Read off ``get_children``, which holds both kinds, rather than from
    ``ax.legend_`` plus ``ax.artists``: the second spelling is a second roster,
    and a legend added by some future route that lands in neither list is
    exactly the silent omission this function exists to end. Returns a list so
    a caller can ask whether there are any without walking the children twice.
    """
    from matplotlib.legend import Legend

    return [child for child in ax.get_children() if isinstance(child, Legend)]


def style_legend(leg, ax) -> None:
    """Recolour a legend's entry + title text to the axes' primary foreground.

    matplotlib legends default their label colour to ``text.color`` (black),
    which is illegible on the dark *screen* theme (black text over a dark, often
    transparent-framed legend reads as grey-on-grey). Setting the label + title
    colour to the theme's primary foreground — recomputed per draw via
    :func:`tokens_for` — keeps legends legible on both the dark screen and the
    light publication background. No-op for a missing legend.
    """
    if leg is None:
        return
    _bg, title_fg, _muted, _grid, _spine = tokens_for(ax)
    for txt in leg.get_texts():
        txt.set_color(title_fg)
    title = leg.get_title()
    if title is not None and title.get_text():
        title.set_color(title_fg)
    # Transparent background everywhere — the legend floats over the plot instead
    # of masking it with an opaque panel (belt-and-suspenders with the rcParam
    # default, so a legend created with an explicit opaque framealpha is fixed
    # up here too).
    frame = leg.get_frame()
    frame.set_alpha(0.0)
    frame.set_facecolor("none")


# ── export-style prefs → figure application ──────────────────────────────────
# These were relocated from ``features/export/style.py`` (T2). They are neutral
# matplotlib figure styling — applying a prefs dict to a figure's axes — and are
# shared by every plot controller (line / bar / scatter / distribution / heatmap)
# plus the export panel. Living here (core, foundational) lets each feature import
# them *downward* instead of reaching sideways into the export feature, which had
# created a scatter↔export import cycle. ``features/export/style.py`` re-exports
# them for backwards compatibility (profiles + the export editor stay there).

DEFAULT_EXPORT_STYLE_PREFS = {
    "axis_label_size": 18,
    "tick_label_size": 18,
    "title_size": 18,
    "x_tick_angle": 0,
    "format": "png",
    "axis_target": "All",
    "legend_show": True,
    "legend_font_size": 12,
    "legend_loc": "best",
    "group_label_size": 8,
    "line_width": 1.8,
    "line_style": "-",
    "marker_size": 5.0,
    "marker_style": "o",
    "marker_edge_width": 0.8,
    "grid_show": True,
    "grid_alpha": 0.25,
    "grid_style": "--",
    "x_lim_min": "",
    "x_lim_max": "",
    "y_lim_min": "",
    "y_lim_max": "",
    "x_log": False,
    "y_log": False,
    # The base a log axis is drawn in, as the user typed it: "10", "2", "e", or
    # any number above 1. Stored as text rather than a float so "e" survives a
    # round trip through the panel and through ``persistence.json`` as the
    # symbol the user picked, instead of coming back as 2.718281828459045.
    "x_log_base": "10",
    "y_log_base": "10",
    "tick_major": True,
    "tick_minor": False,
    "tick_length": 4.0,
    "tick_direction": "out",
    "layout_tight": False,
    "layout_constrained": False,
    "export_profile": "Custom",
    # Per-cell scatter: color points by local 2D density (FACS-style) instead
    # of solid per-well colors.
    "scatter_density": False,
    # Heat Map: printf-style format for the number drawn in each grid cell
    # (e.g. "%.2f", "%.1e", "%d"). Empty picks a format per metric and
    # magnitude — counts as integers, fractions to two places, and so on.
    "heatmap_value_format": "",
    # Per-cell scatter defaults to log/log (fluorescence spans decades). These
    # are scatter-specific so other plot types' linear default can't reset them.
    "scatter_x_log": True,
    "scatter_y_log": True,
}


def _to_float_or_none(value: str) -> float | None:
    s = str(value).strip()
    if not s:
        return None
    return float(s)


def apply_time_rescale(ax, rescale) -> None:
    """Print a time axis's ticks under *rescale*, leaving the data alone.

    Used by the plots whose x-axis is elapsed time (the Line tab's three panels).
    The tick *positions* stay at the values the pipeline parsed out of the
    filenames, so nothing that reads the axis — limits, picks, exports — sees a
    different number; only the text changes.

    *rescale* is a ``domain.time_rescale.TimeRescale``, carrying both the
    multiplier and the offset, so an axis cannot end up printing half of the
    user's correction. A bare number is still read as a factor with no offset,
    which is what the stamp below replays.

    The rescale is stamped on the axes as ``_aw_time_rescale`` because
    :func:`apply_export_style_prefs` runs after the renderer and calls
    ``set_xscale``, which resets an axis's locator and formatter. That pass reads
    the stamp back and re-installs, the same way ``_aw_log_plus1`` and
    ``_categorical_xaxis`` let an axes carry its own requirements through the
    shared restyle.

    An inactive rescale installs nothing: every redraw rebuilds its axes, so an
    unstamped axis is already showing the default formatter.
    """
    from review.domain.time_rescale import (
        format_scaled, is_active, as_rescale,
    )

    rs = as_rescale(rescale)
    ax._aw_time_rescale = rs
    if not is_active(rs):
        return
    from matplotlib.ticker import FuncFormatter
    ax.xaxis.set_major_formatter(
        FuncFormatter(lambda v, _pos, _r=rs: format_scaled(v, _r)))


def mark_time_rescale(*axes, rescale) -> None:
    """Name the rescale correction under a stack of categorical time panels.

    The companion to :func:`apply_time_rescale`, for the figures whose x-axis is
    *categorical* — the Bar tab and its batch-export twin, where the timepoint is
    the tick text rather than a position, so there is no formatter to carry the
    marker and no x-label in the ordinary case.

    The label goes on the bottom-most panel only: the panels are stacked over one
    shared x reading, and three copies of the same sentence is noise. Nothing is
    written while the rescale is off, so an unrescaled figure draws exactly as it
    did before this existed.

    **Called more than once per figure, it still marks exactly one panel.** The
    Bar tab's per-cell modes (violin / beeswarm / all points) reach two callers:
    ``redraw_bars`` marks the three-panel stack, then ``draw_violin`` marks the
    two panels it drew — which put a second copy on the middle panel and left the
    first on the bottom one. So each marked axes is tagged, and every call clears
    the tags it finds on the same figure before writing to whichever candidate
    sits lowest. The bottom is decided by axes position rather than by argument
    order, because the caller that knows the rescale is not always the one that
    knows how many panels the figure ended up with.

    Without it a bar reading ``t=16`` sits under a Timepoint menu reading 4 and
    beside an exported CSV reading 4, with nothing on the figure saying why.

    Lives here rather than in the bar renderer because the batch-export panel
    builds its own figure and needs it too — reaching across from
    ``features/batch_export`` into ``features/barplot`` for it would be a feature
    importing a feature.
    """
    from review.domain.time_rescale import describe

    marker = describe(rescale)
    present = [ax for ax in axes if ax is not None]
    if not marker or not present:
        return
    # Candidates: the panels this call was handed, plus any this figure already
    # carries a marker on. Clearing the old ones is what keeps a second call
    # from leaving a stale copy on a panel that is no longer the lowest.
    candidates = list(present)
    fig = getattr(present[-1], "figure", None)
    for ax in getattr(fig, "axes", ()) or ():
        if getattr(ax, "_aw_time_marker", False):
            ax.set_xlabel("")
            ax._aw_time_marker = False
            if ax not in candidates:
                candidates.append(ax)

    def _bottom(ax):
        try:
            return float(ax.get_position().y0)
        except Exception:
            return 0.0

    target = min(candidates, key=_bottom)
    target.set_xlabel(marker, fontsize=8, labelpad=4)
    target._aw_time_marker = True


#: What the Properties panel offers in its log-base field. Editable, so a base
#: this list does not name is still accepted — these are the ones worth a click.
#: In the order they are reached for rather than sorted: 10 is the default and
#: almost always the answer, 2 and 3 are the doubling and tripling series a
#: dilution plate is laid out in, and *e* is the one that is a symbol rather
#: than a number.
LOG_BASE_CHOICES = ("10", "2", "3", "e")

#: The base a log axis takes when nothing has said otherwise.
DEFAULT_LOG_BASE = 10.0


def log_base_value(text) -> float:
    """The numeric base *text* names — ``"e"``, ``"10"``, ``"2"``, ``"1.5"``.

    Falls back to :data:`DEFAULT_LOG_BASE` for anything matplotlib would refuse,
    which is every base at or below 1: ``log_1(x)`` is undefined and a negative
    base has no real logarithm, so a typo in an editable field would otherwise
    take the axis down rather than the value with it.
    """
    import math

    if isinstance(text, (int, float)) and not isinstance(text, bool):
        base = float(text)
    else:
        cleaned = str(text or "").strip().lower()
        if cleaned in ("e", "ln", "euler"):
            return math.e
        try:
            base = float(cleaned)
        except (TypeError, ValueError):
            return DEFAULT_LOG_BASE
    if not math.isfinite(base) or base <= 1.0:
        return DEFAULT_LOG_BASE
    return base


def axis_log_base(ax, which: str) -> float | None:
    """The base *ax*'s log axis is actually drawn in, or ``None`` if it is not.

    Read off the axis's own transform — ``Axis.get_transform()`` is public and a
    ``LogTransform`` carries its ``base`` — rather than off a value this module
    remembers writing. The difference matters because this module is not the
    only writer: the Distribution tab sets its scales in its renderer and
    ``ax.hist(log=True)`` sets one inside matplotlib. A remembered base goes
    stale the moment one of those runs, and a stale "the axis is already base 2"
    is the one answer that makes :func:`_already_scaled` skip the call that
    would have corrected it.
    """
    axis = ax.xaxis if which == "x" else ax.yaxis
    return getattr(axis.get_transform(), "base", None)


def _already_scaled(ax, which: str, name: str, base: float | None) -> bool:
    """Whether *ax* is already drawn in exactly the scale about to be set.

    ``set_xscale`` is not idempotent: re-setting the scale an axis already has
    still rebuilds it, and rebuilding it **replaces the locator and formatter**.
    That is why the Response tab's dose ticks — one tick per concentration the
    experimenter actually gave — came back as matplotlib's decades on the next
    restyle, and every restyle runs on every Properties edit and every theme
    flip. The renderer's ``_aw_force_xlog`` pin was written to prevent exactly
    that and could not: it decides *whether* the axis is log, and the reset
    happens inside the call that sets it either way.

    So an axis that is already in the requested scale is left alone, and a tab
    that ticked its own axis keeps its ticks.

    The base comes from :func:`axis_log_base`, which asks the axis rather than
    this module's memory; an axis whose transform carries no base is treated as
    not matching, so it is rebuilt rather than assumed.
    """
    current_name = (ax.get_xscale if which == "x" else ax.get_yscale)()
    if current_name != name:
        return False
    if base is None:
        return True
    current = axis_log_base(ax, which)
    return current is not None and abs(float(current) - float(base)) < 1e-9


def log_minor_subs(base: float) -> tuple[float, ...]:
    """Where the minor ticks go between two major ones on a log axis in *base*.

    The **standard subdivisions**: the whole multiples strictly between 1 and
    the base, which for base 10 is the familiar 2, 3, … 9 that makes a decade
    readable between its two labels. Without them a log axis is a pair of
    labelled powers with an unmarked gap, and a reader cannot tell 2 from 5.

    A base too small to have a whole multiple inside it gets its **log-space
    midpoint** instead (√base), the one subdivision evenly placed in the space
    the axis is actually drawn in. Of the bases the panel offers that is base 2
    alone: *e* looks like the same case and is not, since 2 lies strictly inside
    it and so subdivides it like any other base. Returning nothing for base 2
    would leave the base most in need of a mark between its labels with none,
    an axis that labels every doubling being the emptiest of the lot.
    """
    import math

    base = float(base)
    if not math.isfinite(base) or base <= 1.0:
        base = DEFAULT_LOG_BASE
    whole = tuple(float(n) for n in range(2, math.ceil(base)))
    return whole or (math.sqrt(base),)


def apply_log_minor_ticks(ax, which: str, base: float) -> None:
    """Put the standard subdivisions on *ax*'s log axis, unlabelled.

    Unlabelled because the subdivision is a *ruler*, not a reading: nine numbers
    per decade would crowd the axis past legibility, which is why matplotlib
    labels none of them either. What it does not do is place them for every
    base — ``minorticks_on`` finds nothing at all between powers of two — and it
    only places any when the Properties *Minor ticks* row is on, which is off by
    default. On a log axis those ticks are not decoration, so they are installed
    here, where the scale is set, rather than left to a preference about linear
    axes.

    Called on every :func:`apply_axis_log_scale`, including the calls whose
    scale-setting is skipped as a no-op: the restyle that follows every redraw
    can have cleared the locator (``minorticks_off`` installs a ``NullLocator``)
    since the last time the scale itself changed.

    **Only where the majors are the powers.** A subdivision is defined against
    the power it sits under, not against whatever tick happens to be drawn to
    its left, so on an axis whose major ticks are something else the two do not
    line up and the count between one pair of labels differs from the count
    between the next. That is a real axis: the Response tab ticks one dose per
    concentration the experimenter gave, and this function put matplotlib's
    decade subdivisions back between them after every redraw — a 0.1 / 0.3 / 1
    / 3 / 10 series read 1, 6, 1, 6 marks between its labels, and on the
    break-and-column axis those marks read as data. So an axis whose major
    locator is not a :class:`~matplotlib.ticker.LogLocator` has chosen its own
    ticks and keeps them.

    Declining is not enough on its own, because the locator installed by the
    *previous* call outlives the redraw that pinned new majors — so a
    subdivision set left over from this function is taken away again. Only that
    one: a minor locator this function did not install belongs to whoever did,
    and is left where it is.
    """
    from matplotlib.ticker import LogLocator, NullFormatter, NullLocator

    axis = ax.xaxis if which == "x" else ax.yaxis
    if not isinstance(axis.get_major_locator(), LogLocator):
        if isinstance(axis.get_minor_locator(), LogLocator):
            axis.set_minor_locator(NullLocator())
        return
    axis.set_minor_locator(LogLocator(base=float(base),
                                      subs=log_minor_subs(base)))
    axis.set_minor_formatter(NullFormatter())


def apply_axis_log_scale(ax, which: str, on: bool,
                         base: float = DEFAULT_LOG_BASE) -> None:
    """Set an axis to log scale in *base*, honouring a scatter ``_aw_log_plus1``.

    Plain ``log`` can't show 0 (it drops it). Axes tagged ``_aw_log_plus1`` (the
    per-cell scatter) instead use a ``log10(x+1)`` *display* transform via
    matplotlib ``FuncScale``: the data stays in raw units — so gates, clicks and
    exports are unaffected ("display-only, not saved") — while zeros land at the
    left edge. Ticks sit at real decade values (incl. 0) positioned by the
    transform, so it still reads like a log axis. That transform is defined in
    decades, so *base* does not apply to it; the ordinary log path below is
    where the user's base is honoured.

    Setting an axis to the scale it already has is a no-op rather than a rebuild
    — see :func:`_already_scaled` for what the rebuild costs. The *minor* ticks
    are re-installed either way (:func:`apply_log_minor_ticks`), because the
    restyle can have cleared them since the scale last changed.
    """
    set_scale = ax.set_xscale if which == "x" else ax.set_yscale
    if not on:
        if not _already_scaled(ax, which, "linear", None):
            set_scale("linear")
        return
    if not bool(getattr(ax, "_aw_log_plus1", False)):
        if not _already_scaled(ax, which, "log", base):
            set_scale("log", base=base)
        apply_log_minor_ticks(ax, which, base)
        return
    import numpy as _np

    def _fwd(v):
        return _np.log10(_np.clip(_np.asarray(v, dtype=float), 0.0, None) + 1.0)

    def _inv(u):
        return _np.power(10.0, _np.asarray(u, dtype=float)) - 1.0

    set_scale("function", functions=(_fwd, _inv))
    from matplotlib.ticker import FixedLocator, FuncFormatter
    mpl_axis = ax.xaxis if which == "x" else ax.yaxis
    decades = [0, 1, 10, 100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000]
    mpl_axis.set_major_locator(FixedLocator(decades))
    mpl_axis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))


def _ensure_export_style_prefs(ctx) -> dict:
    # A8 teardown: backed by the typed ExportPrefsState slice; seeded lazily on
    # first access (None → defaults) exactly as the old loose attr was. Takes ctx.
    ep = ctx.state.export_prefs
    if ep.style_prefs is None:
        ep.style_prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    else:
        # Backfill any missing default keys. ``view_state`` persists only the
        # non-default diff, so a restored dict (or one built from a partial saved
        # blob) can be missing default-valued keys. Every sidebar/redraw read
        # routes through here (via ``figure_prefs``), and the Properties panel
        # reads keys like ``axis_label_size`` with bracket access — a missing key
        # would raise ``KeyError`` and stop the panel from opening.
        for _k, _v in DEFAULT_EXPORT_STYLE_PREFS.items():
            ep.style_prefs.setdefault(_k, _v)
    return ep.style_prefs


# Prefs that describe a single plot's *view* (axis limits, log scaling, and the
# per-axis bucket/target bookkeeping) rather than the global publication style
# (fonts, line/marker, grid, legend …). These are inherently per-figure: a bar
# chart's y-limit or a distribution's log-y has no meaning on the scatter tab.
# They were historically kept in the one app-wide ``_export_style_prefs`` dict,
# so editing them on one tab leaked onto every other tab. They now live in a
# per-figure store (``app._export_axis_prefs[fig_key]``) overlaid on the global
# style via :class:`_FigurePrefs`.
# The subset of the axis keys that carry a *value in the plate's own units*.
# These are the ones a dataset switch has to drop (``ExportPrefsState.
# reset_for_new_dataset``): a limit typed for one plate re-applies itself on
# every redraw of the next one. A log scale and the bucket/target settings
# beside them are preferences about how to read a figure, not measurements
# from it, so they follow the user across plates.
AXIS_LIMIT_PREF_KEYS = frozenset({
    "x_lim_min", "x_lim_max", "y_lim_min", "y_lim_max",
})

AXIS_SCOPED_PREF_KEYS = AXIS_LIMIT_PREF_KEYS | frozenset({
    "x_log", "y_log", "x_log_base", "y_log_base",
    "axis_buckets", "axis_target",
})


def drop_bucket_limits(buckets) -> None:
    """Remove every axis limit from a mapping of per-panel buckets, in place.

    Split out from :func:`drop_axis_limits` because one caller holds the
    buckets without the entry around them: the Properties sidebar keeps
    ``self._axis_buckets`` in memory for the life of the panel and writes it
    back on the next persist, so its *Auto scale* button has to clear the
    per-panel copies there rather than in the stored preferences. Sharing the
    key list is the whole point — a second copy of it is how a panel comes to
    clear three limits out of four.
    """
    if not isinstance(buckets, dict):
        return
    for bucket in buckets.values():
        if isinstance(bucket, dict):
            for key in AXIS_LIMIT_PREF_KEYS:
                bucket.pop(key, None)


def drop_axis_limits(fig_prefs) -> None:
    """Remove every axis limit from one figure's export-axis entry, in place.

    A limit lives in two places at once. The flat keys on the entry hold the
    value for the axis the Properties sidebar is currently targeting, and
    ``axis_buckets[str(index)]`` holds one copy per panel so a multi-panel
    figure keeps a separate limit per subplot. ``apply_export_style_prefs``
    reads the bucket *first* and only falls back to the flat key, so clearing
    the flat keys alone leaves the drawn limit exactly where it was.

    Every caller that means "forget the limits the user typed" — a dataset
    switch, a fold-change change that moves the values onto another scale —
    has to clear both, which is why they share this one function rather than
    each carrying its own copy of the key list.
    """
    if not isinstance(fig_prefs, dict):
        return
    for key in AXIS_LIMIT_PREF_KEYS:
        fig_prefs.pop(key, None)
    drop_bucket_limits(fig_prefs.get("axis_buckets"))

# Mark styling is per-figure too, for the same reason but a different argument:
# the right mark size is a property of what the plot *draws*, not of the
# publication. Five aggregate points want an 8pt marker; a per-cell cloud of
# 50,000 cells wants 2pt. Sharing one value meant styling the Scatter tab's
# aggregate sub-tab restyled its per-cell sub-tab (and the line and bar plots)
# into illegibility. Typography, grid, legend and ticks stay shared, so one
# setting still gives a whole figure set a consistent look for a paper.
#
# Unlike the axis keys, an unset mark key falls back to the *global* value
# before the neutral default (see :class:`_FigurePrefs`). That keeps a mark
# style saved before this split applying everywhere it used to, until the user
# edits a figure and gives that figure its own value.
MARK_SCOPED_PREF_KEYS = frozenset({
    "line_width", "line_style",
    "marker_size", "marker_style", "marker_edge_width",
})

#: Every key stored per figure rather than app-wide.
FIGURE_SCOPED_PREF_KEYS = AXIS_SCOPED_PREF_KEYS | MARK_SCOPED_PREF_KEYS

# Stable per-figure keys (survive figure recreation across sessions, unlike
# ``id(fig)``) so the per-figure axis prefs can persist. Every figure the
# registry holds under a ``_*_fig`` name belongs here —
# ``tests/test_figure_pref_keys.py`` fails on one that does not.
_FIG_PREF_KEY_MAP = (
    ("_line_fig", "line"),
    ("_bar_fig", "bar"),
    ("_scatter_fig", "scatter_cells"),
    ("_scatter_agg_fig", "scatter_agg"),
    ("_heatmap_fig", "heatmap"),
    ("_distribution_fig", "distribution"),
    ("_sp_fig", "spatial"),
    ("_mv_fig", "multivariate"),
    ("_stats_fig", "stats"),
    ("_response_fig", "response"),
)


def figure_pref_key(ctx, fig) -> str:
    """A stable identifier for *fig* used to scope its axis prefs.

    A8 teardown: resolves the fig through the WidgetRegistry (``ctx.widgets``),
    so it takes ``ctx``.

    The fallback is a diagnostic, not a working key. ``id(fig)`` is a memory
    address: it is stable within a session and different in the next one, so
    prefs stored under it are written to ``persistence.json`` and never match
    again — the styling silently does not come back, and the dead entry stays
    in the file for ever because nothing prunes it. Three registered figures
    (Spatial, Multivariate, Statistics) were reaching this line, and the export
    editor opens two of them, so it was the ordinary path for those tabs rather
    than an edge case. It says so now instead of returning quietly.
    """
    if fig is None:
        # ``None is None`` matches the first *unregistered* name in the map, so
        # a missing figure was handed the key belonging to whichever tab had not
        # been built yet — and returned it without tripping the warning below,
        # because the loop had "found" a match. Callers do pass None: a tab
        # whose lazy body has not been built has no ``_<name>_fig`` in the
        # registry, and ``plot_orchestrator.redraw_app`` reads the line figure
        # straight out of it. The prefs of one tab were then read and written
        # under another tab's key.
        return "fig:none"
    for attr, key in _FIG_PREF_KEY_MAP:
        if ctx.widgets.get_or_none(attr) is fig:
            return key
    _logger.warning(
        "figure_pref_key: no stable key for %r — its axis and mark prefs will "
        "not survive a reload. Add its registry name to _FIG_PREF_KEY_MAP.",
        getattr(fig, "get_label", lambda: fig)() or fig,
    )
    return f"fig:{id(fig)}"


def _ensure_axis_prefs_store(ctx) -> dict:
    # A8 teardown: backed by the typed ExportPrefsState slice (per-figure store).
    return ctx.state.export_prefs.axis_prefs


class _FigurePrefs(MutableMapping):
    """Read/write view over one figure's prefs.

    Figure-scoped keys (:data:`FIGURE_SCOPED_PREF_KEYS`) route to the figure's
    own dict; everything else routes to the shared global style. Writes to a
    scoped key never touch the global dict, so styling one figure cannot restyle
    another. The two scoped families differ in what an *unset* key reads as:

    * axis keys (limits / log / bucket bookkeeping) fall back to the neutral
      default and never consult the global dict — a limit typed on one figure is
      meaningless on another, so it must not be visible there;
    * mark keys fall back to the global value first. Nothing writes those
      globals any more, so in practice that is the value saved before marks
      became per-figure: it keeps applying to every figure the user has not yet
      restyled, instead of silently reverting to the stock 5pt circle.

    Behaves like a plain ``dict`` for every existing call site
    (``[]``/``get``/``in``/``dict(...)``).

    **The two dicts are looked up per access, never captured.** A view can
    outlive the dicts it reads: the Properties sidebar is built once per figure
    and cached for the life of the process, while loading a dataset replaces
    both stores wholesale — ``view_state.restore`` rebuilds ``axis_prefs`` as
    ``{str(k): dict(v)}``, and ``ExportPrefsState.reset_for_new_dataset``
    rebuilds it without the entries that emptied. A view holding the *old*
    entry then writes every axis limit, log toggle, log base and per-axis tick
    size into a dict nothing reads, and reports success: the panel's fields
    keep the values, and the figure never moves. Only the controls that write
    ``PlotState`` — the colour mode, the Show/Error-bars segments — go on
    working, which is what a whole Properties panel looks like when it has
    quietly come loose from its figure.

    The *key* is resolved once, since it is a property of the figure's place in
    the widget registry rather than of the store, and re-deriving it per access
    would re-scan the registry (and re-log its diagnostic) on every read.
    """

    __slots__ = ("_ctx", "_key")

    def __init__(self, ctx, key: str) -> None:
        self._ctx = ctx
        self._key = key

    @property
    def _global(self) -> dict:
        return _ensure_export_style_prefs(self._ctx)

    @property
    def _axis(self) -> dict:
        return _ensure_axis_prefs_store(self._ctx).setdefault(self._key, {})

    def __getitem__(self, key):
        if key in FIGURE_SCOPED_PREF_KEYS:
            if key in self._axis:
                return self._axis[key]
            if key in MARK_SCOPED_PREF_KEYS and key in self._global:
                return self._global[key]
            return DEFAULT_EXPORT_STYLE_PREFS.get(key)
        return self._global[key]

    def __setitem__(self, key, value) -> None:
        if key in FIGURE_SCOPED_PREF_KEYS:
            self._axis[key] = value
        else:
            self._global[key] = value

    def __delitem__(self, key) -> None:
        if key in FIGURE_SCOPED_PREF_KEYS:
            self._axis.pop(key, None)
        else:
            self._global.pop(key, None)

    def __iter__(self):
        seen = set()
        for k in self._global:
            if k not in FIGURE_SCOPED_PREF_KEYS:
                seen.add(k)
                yield k
        for k in FIGURE_SCOPED_PREF_KEYS:
            if k not in seen:
                yield k

    def __len__(self) -> int:
        return sum(1 for _ in self)


def figure_prefs(ctx, fig):
    """A :class:`_FigurePrefs` view for *fig* — the shared publication style
    (typography, grid, legend, ticks) plus that figure's own axis limits, log
    scaling, and mark styling. A8 teardown: takes ``ctx``."""
    # Seed both stores now — a caller that only ever reads must still find the
    # global defaults filled in — but hand the view the *key*, not the dicts it
    # currently resolves to. See :class:`_FigurePrefs` for what capturing them
    # costs a panel that outlives a dataset load.
    _ensure_export_style_prefs(ctx)
    key = figure_pref_key(ctx, fig)
    _ensure_axis_prefs_store(ctx).setdefault(key, {})
    return _FigurePrefs(ctx, key)


def figure_pref_is_set(ctx, fig, key: str) -> bool:
    """Whether *key* has actually been chosen for *fig*, rather than defaulted.

    :func:`figure_prefs` deliberately reads an unset axis key as its neutral
    default, which is right for drawing — but it makes "the user turned this
    off" and "the user never touched it" identical, and a caller whose default
    is *not* the neutral one cannot tell them apart. §7's rule about undefined
    versus computed, applied to a preference.

    The Response tab is the caller: its x axis defaults to logarithmic when the
    doses span two decades, so it has to ask the data on an untouched toggle and
    obey the user on a set one. Reading ``False`` for both would mean the toggle
    could never be honoured in the off position.
    """
    store = _ensure_axis_prefs_store(ctx)
    return key in (store.get(figure_pref_key(ctx, fig)) or {})


def axis_scoped_value(prefs, idx, key, default=None):
    """Per-axis value for a Properties "Axis #" style key, for callers *outside*
    :func:`apply_export_style_prefs` (the bar renderer's group-label footers).

    *prefs* is a :func:`figure_prefs` view — it carries the per-figure
    ``axis_buckets`` — so the panel's own bucket entry (axis ``idx``, 1-based)
    wins, else the shared global value. Same rule the function uses internally."""
    buckets = prefs.get("axis_buckets") or {}
    v = (buckets.get(str(idx)) or {}).get(key)
    if v in (None, ""):
        v = prefs.get(key, default)
    return v


def limits_a_log_axis_can_take(scale: str, lo, hi):
    """*(lo, hi, dropped)* — a typed range with the bounds a log axis refuses.

    ``log(0)`` is ``-inf`` and a negative number has no logarithm, so a
    non-positive bound is not a limit a log axis can be given. matplotlib says
    so **only when the axis is already logarithmic**, and that asymmetry is the
    whole defect: ``ax.clear()`` leaves the axis linear at the top of every
    redraw, so the pass sets the limit first (accepted, no warning) and the
    scale second, and the ``0`` survives into an axis it means nothing on. The
    figure then spans ``-inf``, every real point collapses into the top sliver,
    and it reads as "the traces are crushed to the top". Re-typing the identical
    numbers fixes it because *that* path meets an axis that is already log,
    where matplotlib refuses the bound and autoscales a usable floor — which is
    exactly the shape of a bug that looks like the control not working.

    A **fraction** axis makes it the obvious thing to type: the quantity is
    bounded 0 to 1, so ``0`` is the honest floor, and it is precisely the one
    number a log axis cannot show.

    *dropped* names the bounds that were refused, so the caller can say so.
    Silently substituting a floor would be the substitution §7 forbids: the
    number stays in the Properties field, the plot is drawn to a different
    range, and nothing distinguishes that from the setting having worked.
    """
    if scale != "log":
        return lo, hi, ()
    dropped = tuple(name for name, v in (("min", lo), ("max", hi))
                    if v is not None and float(v) <= 0.0)
    return (None if lo is not None and float(lo) <= 0.0 else lo,
            None if hi is not None and float(hi) <= 0.0 else hi,
            dropped)


def _apply_limits_for_scale(ax, which: str, lo, hi) -> None:
    """Apply *lo*/*hi* to one axis, minus whatever its scale cannot take."""
    scale = ax.get_xscale() if which == "x" else ax.get_yscale()
    lo, hi, dropped = limits_a_log_axis_can_take(scale, lo, hi)
    if dropped:
        _logger.warning(
            "export style: the %s %s typed for this figure %s not a limit a "
            "logarithmic axis can take — log(0) is undefined and a negative "
            "number has no logarithm — so that bound is left to autoscale. "
            "Set a small positive floor, or turn the %s log scale off.",
            which, " and ".join(dropped),
            "are" if len(dropped) > 1 else "is", which)
    if lo is None and hi is None:
        return
    current = ax.get_xlim() if which == "x" else ax.get_ylim()
    _apply_axis_limits(lo if lo is not None else current[0],
                       hi if hi is not None else current[1],
                       setter=ax.set_xlim if which == "x" else ax.set_ylim)


def _hold_typed_limits(ax, xlo, xhi, ylo, yhi) -> None:
    """Re-assert a limit the user typed, and say so when it had been lost.

    **A typed limit is the last word about the view.** It is applied early in
    the pass because the steps after it want a settled range to lay ticks and a
    legend out against — but every one of those steps is a chance to lose it,
    and matplotlib re-autoscales on a good many of them (a scale change, an
    artist added, an axes cleared under a stale reference). The failure is
    silent by construction: the number the user typed is still in the field and
    still in the store, and the plot is simply drawn somewhere else, which reads
    as the setting not working rather than as the setting being discarded.

    So the limit is applied again at the end of the pass, and a mismatch is
    **logged rather than quietly corrected**. §7 of ``CLAUDE.md``: degraded
    output must not be indistinguishable from good output — if something in
    between is undoing a user's axis limits, that is a defect with a cause, and
    a silent re-application would hide it for the next person as effectively as
    it hides it now. The log line names the axis and both numbers, so a report
    of "my limits keep coming back wrong" has something to read.
    """
    for which, raw_lo, raw_hi in (("x", xlo, xhi), ("y", ylo, yhi)):
        scale = ax.get_xscale() if which == "x" else ax.get_yscale()
        # Silently here: the bound was already reported when it was applied,
        # and this runs on every redraw.
        lo, hi, _dropped = limits_a_log_axis_can_take(scale, raw_lo, raw_hi)
        if lo is None and hi is None:
            continue
        current = ax.get_xlim() if which == "x" else ax.get_ylim()
        want = (lo if lo is not None else current[0],
                hi if hi is not None else current[1])
        if all(abs(float(a) - float(b)) <= abs(float(b)) * 1e-9
               for a, b in zip(current, want)):
            continue
        _logger.warning(
            "export style: the %s limits set on this figure were %s after the "
            "style pass but the user asked for %s — re-applying. Something "
            "between the two is autoscaling over a typed limit.",
            which, tuple(round(v, 6) for v in current),
            tuple(round(v, 6) for v in want))
        _apply_axis_limits(want[0], want[1],
                           setter=ax.set_xlim if which == "x" else ax.set_ylim)


def _apply_axis_limits(lo, hi, *, setter) -> None:
    """Apply a user-entered axis-limit override, nudging apart an equal
    lo/hi so matplotlib doesn't emit "identical low and high ylims" and
    silently expand on its own terms. A typed override that matches on both
    fields is a normal thing to do (e.g. locking an axis to a single value)
    so expand around it symmetrically rather than let matplotlib's own
    asymmetric auto-expansion decide.
    """
    if lo is not None and hi is not None and lo == hi:
        eps = abs(lo) * 1e-3 or 1e-3
        lo, hi = lo - eps, hi + eps
    setter(lo, hi)


def apply_export_style_prefs(fig, prefs: dict) -> None:
    for ax in fig.axes:
        if not hasattr(ax, "_fixed_axes_position"):
            ax._fixed_axes_position = ax.get_position().frozen()

    fig.patch.set_alpha(1.0)

    axis_target = str(prefs.get("axis_target", "All"))
    target_idx = None if axis_target == "All" else int(axis_target)

    # Per-axis font sizes: an "Axis #" selection scopes tick / axis-label / title
    # sizes to that one panel (stored in that panel's ``axis_buckets`` entry by
    # the properties editor), falling back to the shared global size for panels
    # with no override. This is why editing a size no longer changes every panel
    # of a multi-panel figure (multivariate / spatial). Callers that pass a plain
    # dict without ``axis_buckets`` just get the global size everywhere.
    _axis_buckets = prefs.get("axis_buckets") or {}

    def _axis_style(_idx, key, default):
        # Per-axis value for a control grouped under the Properties "Axis #"
        # selector (tick rotation / tick length / direction / visibility / group
        # label size): the panel's own bucket entry wins, else the shared global
        # value. So an "Axis #" edit scopes these to one panel — the same
        # global-baseline-plus-bucket model the font sizes use — instead of the
        # old flat read that pushed one value onto every axis.
        v = (_axis_buckets.get(str(_idx)) or {}).get(key)
        if v in (None, ""):
            v = prefs.get(key, default)
        return v

    def _axis_font_size(_idx, key, default):
        return int(float(_axis_style(_idx, key, default)))

    def _axis_limit(_idx, key) -> float | None:
        # Per-axis limit: a panel's own bucket entry wins so every subplot of a
        # multi-panel figure (e.g. the bar Value/Fraction/Events stack) keeps its
        # own x/y limits set via the "Axis #" picker. Fall back to the flat pref
        # only for the targeted axis (or an "All"/single-panel target, and the
        # plain-dict batch-export callers with no buckets), which preserves the
        # prior single-axis behaviour when no bucket entry exists.
        raw = (_axis_buckets.get(str(_idx)) or {}).get(key)
        if raw not in (None, ""):
            return _to_float_or_none(raw)
        if target_idx is None or _idx == target_idx:
            return _to_float_or_none(prefs.get(key, ""))
        return None

    for idx, ax in enumerate(fig.axes, start=1):
        _tick_sz = _axis_font_size(idx, "tick_label_size", 10)
        _label_sz = _axis_font_size(idx, "axis_label_size", 12)
        _title_sz = _axis_font_size(idx, "title_size", 14)
        # Theme-aware chrome: this function used to hardcode "black" everywhere
        # (publication-ink), which was correct on export but overrode the
        # PlotCard's screen-theme styling whenever it was called from a redraw.
        _bg, _title_fg, _muted_fg, _grid, _spine = tokens_for(ax)
        ax.patch.set_alpha(1.0)
        ax.xaxis.label.set_color(_muted_fg)
        ax.yaxis.label.set_color(_muted_fg)
        ax.xaxis.label.set_fontsize(_label_sz)
        ax.yaxis.label.set_fontsize(_label_sz)
        ax.tick_params(axis="x", labelsize=_tick_sz, colors=_muted_fg)
        ax.tick_params(axis="y", labelsize=_tick_sz, colors=_muted_fg)
        ax.title.set_fontsize(_title_sz)
        ax.title.set_color(_title_fg)

        _fam = _plot_font_family()
        for tick in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
            tick.set_color(_muted_fg)
            tick.set_fontfamily(_fam)
            tick.set_fontsize(_tick_sz)

        ax.xaxis.label.set_fontfamily(_fam)
        ax.yaxis.label.set_fontfamily(_fam)
        ax.title.set_fontfamily(_fam)

        # A colour bar is a legend for the plot's norm, not a plot axis of its
        # own: its scale, limits, ticks and the mesh that fills it all belong to
        # the mappable that created it. ``fig.axes`` includes it all the same, so
        # everything below here has to leave it alone — the axis pass reset a
        # LogNorm colour bar to a linear scale, discarding the log locator and
        # formatter matplotlib had installed, and the grid and mark-styling passes
        # drew gridlines and marker edges over the bar. Typography and colours are
        # applied above, so the colour bar still tracks the plot theme.
        if bool(getattr(ax, "_aw_colorbar", False)):
            continue

        # Keep horizontal alignment in step with the applied angle: a
        # horizontal label (0°) reads centered under its bar/tick, while an
        # angled label anchors its right edge at the tick (the conventional
        # slanted look). Without this the bar renderer's ``ha="right"`` (drawn
        # alongside its own 45° rotation) survives even after this pass flattens
        # the rotation back to 0°, leaving labels right-aligned under their bar.
        _x_angle = int(_axis_style(idx, "x_tick_angle", 0))
        _x_ha = "center" if _x_angle == 0 else "right"
        for tick in ax.get_xticklabels():
            tick.set_rotation(_x_angle)
            tick.set_horizontalalignment(_x_ha)

        line_style = str(prefs.get("line_style", "-"))
        marker_style = str(prefs.get("marker_style", "o"))
        # Open / hollow markers piggyback on the existing matplotlib token
        # via an ``_open`` suffix so the combo stays a single dropdown.
        marker_open = marker_style.endswith("_open")
        if marker_open:
            marker_style = marker_style[: -len("_open")] or "o"
        # Error-bar caps live in ``ax.lines`` too — overriding their marker /
        # linestyle replaces the tick at the top / bottom of each bar with
        # the user's data-marker pick, which looks like the error bar is
        # broken. Collect their ids so the override loop below skips them.
        cap_ids: set = set()
        for container in getattr(ax, "containers", []) or []:
            cap_lines = getattr(container, "caplines", None)
            if cap_lines is None:
                children = getattr(container, "lines", None)
                if isinstance(children, tuple) and len(children) >= 2:
                    cap_lines = children[1]
            for cap in (cap_lines or ()):
                cap_ids.add(id(cap))
        for ln in ax.lines:
            if getattr(ln, "_aw_no_style_override", False):
                # Decorative annotation lines (e.g. significance brackets) opt
                # out entirely — stamping the data marker/linestyle on them turns
                # a flat bracket into a row of circles.
                continue
            if id(ln) in cap_ids:
                # Error-bar cap markers have their own intrinsic capsize-
                # based sizing + a default "_" / "|" marker that signals
                # the cap. Touching their markersize / markeredgewidth /
                # marker / linestyle either makes the caps look like
                # data points or stamps the user's data-marker onto the
                # caps — both read as "error bars suddenly appeared".
                continue
            ln.set_linewidth(float(prefs.get("line_width", 1.8)))
            ln.set_markersize(float(prefs.get("marker_size", 5.0)))
            ln.set_markeredgewidth(float(prefs.get("marker_edge_width", 0.8)))
            # ``"keep"`` lets a tab opt out of overriding the renderer's own
            # marker / linestyle pick (e.g. distinct markers per replicate).
            # Lines the renderer drew as markers-only (linestyle='None' +
            # a marker — the scatter / aggregate pattern) keep their no-
            # connecting-line behaviour; overriding it to "-" stamped
            # diagonal lines that read as spurious error bars.
            cur_ls = ln.get_linestyle()
            cur_mk = ln.get_marker()
            scatter_only = (
                getattr(ln, "_scatter_only_artist", False)
                or (
                    cur_ls in ("None", "none", "", " ") and
                    cur_mk not in ("None", "none", "", None)
                )
            )
            # ``_aw_linestyle_is_data``: the Line tab's grey mode encodes a
            # trace in its dash pattern, alongside the marker shape and face
            # below. Stamping one pref pattern over every trace collapses that
            # third cue on every redraw while the legend goes on showing four
            # different dashes.
            if (line_style and line_style != "keep" and not scatter_only
                    and not getattr(ln, "_aw_linestyle_is_data", False)):
                ln.set_linestyle(line_style if line_style != "none" else "None")
            if marker_style and marker_style != "keep":
                # ``_aw_marker_is_data``: renderers that encode a variable in the
                # marker *shape* opt out of the shape override while still
                # honouring size / open-vs-filled. The aggregate scatter gives
                # each well its own shape and draws a legend keyed on those
                # shapes — stamping one pref shape over every point collapsed
                # them all onto the first well's marker while the legend went on
                # advertising five different ones.
                if not getattr(ln, "_aw_marker_is_data", False):
                    ln.set_marker(marker_style if marker_style != "none" else "None")
                # Force both edge and face explicitly. Some renderers
                # (e.g. scatter cells) construct the line with
                # ``markeredgecolor='none'`` for a clean filled-only
                # look — that meant "open circle" set face='none' but
                # the existing edge was 'none' too, producing a fully
                # invisible marker. Pin the edge to the line colour in
                # both modes so the ring is always visible.
                #
                # ``_aw_marker_face_is_data`` is the face's twin of the shape
                # opt-out above, and it is a separate flag because the two are
                # separate claims: the aggregate scatter encodes a well in the
                # *shape* and is happy for the user to make every point hollow,
                # while the Line tab's grey mode encodes a trace in the shape
                # **and** the fill — half its cycle is the same six shapes
                # open. Re-pinning the face here turned every open marker
                # filled on the next redraw, which is every redraw, so six of
                # the twelve traces stopped being distinguishable while the
                # legend went on advertising them.
                if getattr(ln, "_aw_marker_face_is_data", False):
                    continue
                ln_colour = ln.get_color()
                if marker_open:
                    ln.set_markerfacecolor("none")
                    ln.set_markeredgecolor(ln_colour)
                else:
                    ln.set_markerfacecolor(ln_colour)
                    ln.set_markeredgecolor(ln_colour)

        # v5.2.31: ``ax.scatter`` produces a ``PathCollection`` rather
        # than a ``Line2D`` — pre-v5.2.31 the loop above missed every
        # scatter-based artist (notably the beeswarm dots), so changing
        # Marker prefs had no visible effect on them. Renderers that
        # want scatter artists controlled by the export-style panel
        # tag their PathCollection with ``_aw_marker_collection =
        # True``. Other PathCollections (contour, hexbin, errorbar
        # caps, single-point placeholders) stay untouched.
        marker_size = float(prefs.get("marker_size", 5.0))
        marker_edge_width = float(prefs.get("marker_edge_width", 0.8))
        for coll in ax.collections:
            is_density = getattr(coll, "_aw_density_collection", False)
            if not (getattr(coll, "_aw_marker_collection", False) or is_density):
                continue
            # PathCollection ``sizes`` are in points^2 (area), unlike
            # Line2D ``markersize`` which is points (diameter). Square
            # the diameter so the visual size matches between the
            # line- and scatter-based artists on the same plot.
            try:
                coll.set_sizes([marker_size * marker_size])
            except Exception:
                swallowed()  # was: pass (silent except)
            try:
                coll.set_linewidths([marker_edge_width])
            except Exception:
                swallowed()  # was: pass (silent except)
            # Density collections carry a per-point colormap (the whole point of
            # the mode). The single-colour override below would collapse the
            # gradient, so density gets its own handling: honor the marker shape
            # and the open/filled (hollow-circle) choice while keeping the
            # per-point colours — recomputed from the scalar array each time so
            # repeated restyles never lose the gradient or get stuck "open".
            if is_density:
                if marker_style and marker_style != "keep":
                    try:
                        _arr = coll.get_array()
                    except Exception:
                        _arr = None
                    if _arr is not None:
                        try:
                            coll.norm.autoscale_None(_arr)
                            _rgba = coll.cmap(coll.norm(_arr))
                            if marker_open:
                                coll.set_facecolors("none")
                                coll.set_edgecolors(_rgba)
                            else:
                                coll.set_facecolors(_rgba)
                                coll.set_edgecolors("none")
                        except Exception:
                            swallowed()  # leave the gradient untouched on error
                    if marker_style.lower() != "none":
                        try:
                            from matplotlib.markers import MarkerStyle
                            ms = MarkerStyle(marker_style)
                            coll.set_paths([ms.get_path().transformed(ms.get_transform())])
                        except Exception:
                            swallowed()  # was: pass (silent except)
                continue
            if marker_style and marker_style != "keep":
                # Per-point face/edge: read the FIRST currently-set
                # face colour so we preserve the renderer's per-well
                # colour assignment. Open vs filled is a function of
                # the user's pref, not the renderer's choice.
                try:
                    faces = coll.get_facecolors()
                    cur_colour = faces[0] if len(faces) else None
                except Exception:
                    cur_colour = None
                if cur_colour is None:
                    try:
                        edges = coll.get_edgecolors()
                        cur_colour = edges[0] if len(edges) else None
                    except Exception:
                        cur_colour = None
                if cur_colour is not None:
                    if marker_open:
                        try:
                            coll.set_facecolor("none")
                            coll.set_edgecolor(cur_colour)
                        except Exception:
                            swallowed()  # was: pass (silent except)
                    else:
                        try:
                            coll.set_facecolor(cur_colour)
                            coll.set_edgecolor(cur_colour)
                        except Exception:
                            swallowed()  # was: pass (silent except)
                # Marker shape: swap the path in the PathCollection
                # to the requested marker's path. Each point in the
                # collection draws the same path, scaled by size.
                if marker_style.lower() != "none":
                    try:
                        from matplotlib.markers import MarkerStyle
                        ms = MarkerStyle(marker_style)
                        new_path = ms.get_path().transformed(
                            ms.get_transform()
                        )
                        coll.set_paths([new_path])
                    except Exception:
                        swallowed()  # was: pass (silent except)

        # **Off means off.** ``ax.grid(False, alpha=…, linestyle=…)`` does not
        # hide the grid: matplotlib reads line properties beside a false first
        # argument as "style the grid", turns it back **on**, and says so in a
        # ``UserWarning`` — so Properties → Grid → Show grid did nothing on any
        # plot in the app, whichever way it was moved. The styling kwargs only
        # go in when the grid is actually being drawn.
        if bool(prefs.get("grid_show", True)):
            ax.grid(True,
                    alpha=float(prefs.get("grid_alpha", 0.25)),
                    linestyle=str(prefs.get("grid_style", "--")))
        else:
            ax.grid(False)

        # Axis limits are applied per-panel (each axis reads its own bucketed
        # limit), not just to the targeted axis — so a limit set on any panel of a
        # multi-panel figure sticks and renders on every redraw. (Log scaling
        # below stays scoped to the targeted axis.)
        xlo = _axis_limit(idx, "x_lim_min")
        xhi = _axis_limit(idx, "x_lim_max")
        ylo = _axis_limit(idx, "y_lim_min")
        yhi = _axis_limit(idx, "y_lim_max")
        # Whatever the rest of this pass does to the axes, a limit the user
        # typed is the last word — so it is re-asserted at the end and the
        # discrepancy is reported. See :func:`_hold_typed_limits`.
        _typed_limits = (xlo, xhi, ylo, yhi)

        if target_idx is None or idx == target_idx:
            # ``_aw_force_xlog``/``_aw_force_ylog`` let an axis pin its own log
            # state (the scatter plot defaults to log/log via a scatter-specific
            # pref) so the shared x_log/y_log — which other plot types reset —
            # can't clobber it on a later restyle.
            #
            # The *base* is the user's either way. An axis that pins whether it
            # is logarithmic has not thereby chosen what it is logarithmic in,
            # and the Response tab — the one figure that pins — reads the same
            # pref when it draws, so the two agree.
            if not getattr(ax, "_categorical_xaxis", False):
                _fx = getattr(ax, "_aw_force_xlog", None)
                _xlog = bool(prefs.get("x_log", False)) if _fx is None else bool(_fx)
                apply_axis_log_scale(ax, "x", _xlog,
                                     log_base_value(prefs.get("x_log_base", "10")))
            if not getattr(ax, "_categorical_yaxis", False):
                _fy = getattr(ax, "_aw_force_ylog", None)
                _ylog = bool(prefs.get("y_log", False)) if _fy is None else bool(_fy)
                apply_axis_log_scale(ax, "y", _ylog,
                                     log_base_value(prefs.get("y_log_base", "10")))
            # ``set_xscale`` above resets the axis's locator and formatter, so a
            # time axis that asked to have its ticks rescaled has to say so
            # again. The renderer stamped the whole correction; re-install from
            # it — re-installing a factor alone would drop the offset here and
            # nowhere else, which is the worst place for the two to disagree.
            _ts = getattr(ax, "_aw_time_rescale", None)
            if _ts is not None:
                apply_time_rescale(ax, _ts)

        # **After the scale, never before.** A limit can only be validated
        # against the scale the axis will actually be drawn in, and on every
        # redraw ``ax.clear()`` has left this axis linear until the block above
        # ran: applied first, a non-positive bound on a log axis is accepted in
        # silence — matplotlib refuses one only when the axis is *already*
        # logarithmic — and survives into an axis where it means ``-inf``. That
        # is the whole of the "my y limits keep coming back wrong" report: a
        # fraction axis typed as 0 to 1 drew every point in the top thousandth
        # of the figure, and re-typing the same two numbers fixed it because
        # that path met an axis that was already log and had the bound refused.
        _apply_limits_for_scale(ax, "x", xlo, xhi)
        _apply_limits_for_scale(ax, "y", ylo, yhi)

        # ``minorticks_off`` installs a ``NullLocator`` on *both* axes, which on
        # a log axis would take away the subdivisions that make it readable
        # between its two labelled powers — those are a property of the scale,
        # not a preference about tick density, so a log axis keeps them whatever
        # this row says and the row goes on governing the linear axes beside it.
        from matplotlib.ticker import NullLocator as _NullLocator

        _tick_minor = bool(_axis_style(idx, "tick_minor", False))
        _log_axes = [w for w in ("x", "y")
                     if (ax.get_xscale() if w == "x" else ax.get_yscale()) == "log"]
        if _tick_minor:
            ax.minorticks_on()
        elif not _log_axes:
            ax.minorticks_off()
        else:
            for _w in ("x", "y"):
                if _w not in _log_axes:
                    (ax.xaxis if _w == "x" else ax.yaxis).set_minor_locator(
                        _NullLocator())
        for _w in _log_axes:
            apply_log_minor_ticks(
                ax, _w,
                log_base_value(prefs.get(f"{_w}_log_base", "10")))
        length = float(_axis_style(idx, "tick_length", 4.0))
        direction = str(_axis_style(idx, "tick_direction", "out"))
        if bool(_axis_style(idx, "tick_major", True)):
            ax.tick_params(which="major", length=length, direction=direction)
        else:
            ax.tick_params(which="major", length=0)
        if _tick_minor or _log_axes:
            ax.tick_params(which="minor", length=max(1.0, length * 0.6), direction=direction)

        # **Every legend on the axes, not only the one ``ax.legend()`` made.**
        # A legend added with ``add_artist`` is not ``ax.legend_``, so
        # ``get_legend()`` never returns it and this pass used to walk straight
        # past it — the aggregate scatter's two keys are built that way on
        # purpose, and they were the one place in the app where Properties →
        # Legend → Font size did nothing at all.
        legends = legends_on(ax)
        show_leg = bool(prefs.get("legend_show", True))
        for leg in legends:
            leg.set_visible(show_leg)
        # **Position is the primary legend's alone.** The standalone keys pin
        # their own corners precisely so this pass cannot move them: relocating
        # both to ``legend_loc`` would drop one on top of the other, which is
        # the reason they are off ``ax.legend_`` in the first place.
        primary = ax.get_legend()
        if primary is not None and show_leg:
            loc_name = str(prefs.get("legend_loc", "best"))
            try:
                primary.set_loc(loc_name)
            except Exception:
                try:
                    from matplotlib.legend import Legend as _Legend
                    primary._loc = _Legend.codes.get(loc_name, 0)
                except Exception:
                    swallowed()  # was: pass (silent except)
        for leg in legends:
            for txt in leg.get_texts():
                txt.set_fontsize(float(prefs.get("legend_font_size", 9)))
                txt.set_color(_title_fg)
                txt.set_fontfamily(_plot_font_family())
            # The title is legend text too. Left out, a key headed "Well /
            # group" or "Timepoint" kept whatever size it was created at while
            # its own entries resized underneath it, so raising the size made
            # the heading the smaller of the two.
            title = leg.get_title()
            if title is not None and title.get_text():
                title.set_fontsize(float(prefs.get("legend_font_size", 9)))
                title.set_color(_title_fg)
                title.set_fontfamily(_plot_font_family())
        if primary is not None:
            # Last, so the swatches copy the traces *after* this pass has
            # finished restyling them. Run earlier and the legend would faithfully
            # reproduce the widths the lines had a moment ago.
            sync_legend_handles(ax)

        fixed_pos = getattr(ax, "_fixed_axes_position", None)
        if fixed_pos is not None:
            ax.set_position(fixed_pos)

        _hold_typed_limits(ax, *_typed_limits)

    use_constrained = bool(prefs.get("layout_constrained", False))
    use_tight = bool(prefs.get("layout_tight", False))
    try:
        if use_constrained:
            fig.set_layout_engine("constrained")
        elif use_tight:
            fig.set_layout_engine("tight")
        else:
            fig.set_layout_engine(None)
    except Exception:
        fig.set_constrained_layout(use_constrained)
        if use_tight and not use_constrained:
            try:
                fig.tight_layout()
            except Exception:
                swallowed()  # was: pass (silent except)


def apply_export_style_to_current(ctx, fig, canvas=None) -> None:
    # Per-figure view (shared publication style + *this* figure's axis limits,
    # log scaling and mark styling) so neither leaks across tabs. A8 teardown:
    # takes ``ctx``.
    if fig is None:
        # Nothing to style, and asking for a key would allocate a prefs bucket
        # for a figure that does not exist. Callers reach here with None
        # whenever a tab's lazy body has not been built yet.
        return
    prefs = figure_prefs(ctx, fig)
    apply_export_style_prefs(fig, prefs)
    _apply_grouping_notice(ctx, fig)
    if canvas is not None:
        canvas.draw_idle()


#: Marks the figure-level text this module owns, so a redraw replaces its own
#: notice rather than stacking a second one under the first.
_NOTICE_TAG = "_aw_grouping_notice"


def _apply_grouping_notice(ctx, fig) -> None:
    """Say on the figure what the Group-by setting did to its members.

    **Here, because here is the one place every plot passes through.** Grouping
    by a sample variable substitutes the member set for every view at once —
    bars, lines, statistics, the heat map, scatter, the exports — and it drops
    every well with no value for the variable. That is §7's cardinal case: the
    numbers move, the *n* moves, and a figure that silently lost eight wells
    looks exactly like a figure of a plate that had eighty-eight.

    The sentence had a home on exactly one tab. Reaching the other eight would
    have meant editing eight renderers and hoping the ninth never arrives, which
    is the half-swept shape §7.5 #5 is about. Every renderer already ends at
    ``apply_export_style_to_current`` — thirty-nine call sites, and a guard that
    keeps it that way — so a notice drawn from here cannot miss a tab, and a tab
    added tomorrow gets it without knowing it exists.

    Drawn on the **figure** rather than an axes so it survives a multi-panel
    layout, and re-applied rather than appended: the previous notice is removed
    first, so filling in the missing wells clears it on the next redraw instead
    of leaving a sentence that outlived its condition — the defect that moved
    this off the status bar in the first place.
    """
    from review.core.group_by_controller import group_by_shortfall
    from core.safe import swallow

    with swallow("draw the Group-by notice on a figure"):
        for text in list(getattr(fig, "texts", []) or []):
            if getattr(text, _NOTICE_TAG, False):
                text.remove()
        message = group_by_shortfall(ctx)
        if not message:
            return
        artist = fig.text(
            0.01, 0.005, message,
            fontsize=7, ha="left", va="bottom",
            color=theme.Colors.text_secondary, wrap=True,
        )
        setattr(artist, _NOTICE_TAG, True)
