"""Shared matplotlib axis styling.

Extracted from ``well_viewer.runtime_app`` so plot controllers and export
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

import theme
from well_viewer.core.safe import swallowed

_logger = logging.getLogger("well_viewer")

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
            from well_viewer.core.app_fonts import register_mpl_ui_font
            register_mpl_ui_font()  # make matplotlib aware of the bundled .ttf
        except Exception:
            swallowed()
    try:
        import theme as _theme
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


def apply_time_rescale(ax, factor) -> None:
    """Print a time axis's ticks multiplied by *factor*, leaving the data alone.

    Used by the plots whose x-axis is elapsed time (the Line tab's three panels).
    The tick *positions* stay at the values the pipeline parsed out of the
    filenames, so nothing that reads the axis — limits, picks, exports — sees a
    different number; only the text changes.

    The factor is stamped on the axes as ``_aw_time_scale`` because
    :func:`apply_export_style_prefs` runs after the renderer and calls
    ``set_xscale``, which resets an axis's locator and formatter. That pass reads
    the stamp back and re-installs, the same way ``_aw_log_plus1`` and
    ``_categorical_xaxis`` let an axes carry its own requirements through the
    shared restyle.

    A factor of one installs nothing: every redraw rebuilds its axes, so an
    unstamped axis is already showing the default formatter.
    """
    from well_viewer.domain.time_rescale import format_scaled, is_active

    ax._aw_time_scale = factor
    if not is_active(factor):
        return
    from matplotlib.ticker import FuncFormatter
    ax.xaxis.set_major_formatter(
        FuncFormatter(lambda v, _pos, _f=factor: format_scaled(v, _f)))


def mark_time_rescale(*axes, factor) -> None:
    """Name the rescale factor under a stack of categorical time panels.

    The companion to :func:`apply_time_rescale`, for the figures whose x-axis is
    *categorical* — the Bar tab and its batch-export twin, where the timepoint is
    the tick text rather than a position, so there is no formatter to carry the
    marker and no x-label in the ordinary case.

    The label goes on the bottom-most panel only: the panels are stacked over one
    shared x reading, and three copies of the same sentence is noise. Nothing is
    written while the factor is off, so an unrescaled figure draws exactly as it
    did before this existed.

    **Called more than once per figure, it still marks exactly one panel.** The
    Bar tab's per-cell modes (violin / beeswarm / all points) reach two callers:
    ``redraw_bars`` marks the three-panel stack, then ``draw_violin`` marks the
    two panels it drew — which put a second copy on the middle panel and left the
    first on the bottom one. So each marked axes is tagged, and every call clears
    the tags it finds on the same figure before writing to whichever candidate
    sits lowest. The bottom is decided by axes position rather than by argument
    order, because the caller that knows the factor is not always the one that
    knows how many panels the figure ended up with.

    Without it a bar reading ``t=16`` sits under a Timepoint menu reading 4 and
    beside an exported CSV reading 4, with nothing on the figure saying why.

    Lives here rather than in the bar renderer because the batch-export panel
    builds its own figure and needs it too — reaching across from
    ``features/batch_export`` into ``features/barplot`` for it would be a feature
    importing a feature.
    """
    from well_viewer.domain.time_rescale import describe

    marker = describe(factor)
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


def apply_axis_log_scale(ax, which: str, on: bool) -> None:
    """Set an axis to log scale, honouring a scatter ``_aw_log_plus1`` request.

    Plain ``log`` can't show 0 (it drops it). Axes tagged ``_aw_log_plus1`` (the
    per-cell scatter) instead use a ``log10(x+1)`` *display* transform via
    matplotlib ``FuncScale``: the data stays in raw units — so gates, clicks and
    exports are unaffected ("display-only, not saved") — while zeros land at the
    left edge. Ticks sit at real decade values (incl. 0) positioned by the
    transform, so it still reads like a log axis.
    """
    set_scale = ax.set_xscale if which == "x" else ax.set_yscale
    if not on:
        set_scale("linear")
        return
    if not bool(getattr(ax, "_aw_log_plus1", False)):
        set_scale("log")
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
AXIS_SCOPED_PREF_KEYS = frozenset({
    "x_lim_min", "x_lim_max", "y_lim_min", "y_lim_max",
    "x_log", "y_log", "axis_buckets", "axis_target",
})

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
    """

    __slots__ = ("_global", "_axis")

    def __init__(self, global_prefs: dict, axis_prefs: dict) -> None:
        self._global = global_prefs
        self._axis = axis_prefs

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
    global_prefs = _ensure_export_style_prefs(ctx)
    store = _ensure_axis_prefs_store(ctx)
    axis = store.setdefault(figure_pref_key(ctx, fig), {})
    return _FigurePrefs(global_prefs, axis)


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
            if line_style and line_style != "keep" and not scatter_only:
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

        show_grid = bool(prefs.get("grid_show", True))
        ax.grid(show_grid, alpha=float(prefs.get("grid_alpha", 0.25)), linestyle=str(prefs.get("grid_style", "--")))

        # Axis limits are applied per-panel (each axis reads its own bucketed
        # limit), not just to the targeted axis — so a limit set on any panel of a
        # multi-panel figure sticks and renders on every redraw. (Log scaling
        # below stays scoped to the targeted axis.)
        xlo = _axis_limit(idx, "x_lim_min")
        xhi = _axis_limit(idx, "x_lim_max")
        ylo = _axis_limit(idx, "y_lim_min")
        yhi = _axis_limit(idx, "y_lim_max")
        if xlo is not None or xhi is not None:
            cur = ax.get_xlim()
            _apply_axis_limits(xlo if xlo is not None else cur[0],
                                xhi if xhi is not None else cur[1], setter=ax.set_xlim)
        if ylo is not None or yhi is not None:
            cur = ax.get_ylim()
            _apply_axis_limits(ylo if ylo is not None else cur[0],
                                yhi if yhi is not None else cur[1], setter=ax.set_ylim)

        if target_idx is None or idx == target_idx:
            # ``_aw_force_xlog``/``_aw_force_ylog`` let an axis pin its own log
            # state (the scatter plot defaults to log/log via a scatter-specific
            # pref) so the shared x_log/y_log — which other plot types reset —
            # can't clobber it on a later restyle.
            if not getattr(ax, "_categorical_xaxis", False):
                _fx = getattr(ax, "_aw_force_xlog", None)
                _xlog = bool(prefs.get("x_log", False)) if _fx is None else bool(_fx)
                apply_axis_log_scale(ax, "x", _xlog)
            if not getattr(ax, "_categorical_yaxis", False):
                _fy = getattr(ax, "_aw_force_ylog", None)
                _ylog = bool(prefs.get("y_log", False)) if _fy is None else bool(_fy)
                apply_axis_log_scale(ax, "y", _ylog)
            # ``set_xscale`` above resets the axis's locator and formatter, so a
            # time axis that asked to have its ticks rescaled has to say so
            # again. The renderer stamped the factor; re-install from it.
            _ts = getattr(ax, "_aw_time_scale", None)
            if _ts is not None:
                apply_time_rescale(ax, _ts)

        _tick_minor = bool(_axis_style(idx, "tick_minor", False))
        if _tick_minor:
            ax.minorticks_on()
        else:
            ax.minorticks_off()
        length = float(_axis_style(idx, "tick_length", 4.0))
        direction = str(_axis_style(idx, "tick_direction", "out"))
        if bool(_axis_style(idx, "tick_major", True)):
            ax.tick_params(which="major", length=length, direction=direction)
        else:
            ax.tick_params(which="major", length=0)
        if _tick_minor:
            ax.tick_params(which="minor", length=max(1.0, length * 0.6), direction=direction)

        leg = ax.get_legend()
        if leg is not None:
            show_leg = bool(prefs.get("legend_show", True))
            leg.set_visible(show_leg)
            if show_leg:
                loc_name = str(prefs.get("legend_loc", "best"))
                try:
                    leg.set_loc(loc_name)
                except Exception:
                    try:
                        from matplotlib.legend import Legend as _Legend
                        leg._loc = _Legend.codes.get(loc_name, 0)
                    except Exception:
                        swallowed()  # was: pass (silent except)
            for txt in leg.get_texts():
                txt.set_fontsize(float(prefs.get("legend_font_size", 9)))
                txt.set_color(_title_fg)
                txt.set_fontfamily(_plot_font_family())

        fixed_pos = getattr(ax, "_fixed_axes_position", None)
        if fixed_pos is not None:
            ax.set_position(fixed_pos)

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
    prefs = figure_prefs(ctx, fig)
    apply_export_style_prefs(fig, prefs)
    if canvas is not None:
        canvas.draw_idle()
