"""Shared matplotlib axis styling.

Extracted from ``well_viewer.runtime_app`` so plot controllers and export
panels can apply the canonical axis look without importing the GUI monolith.

When the axes belongs to a figure inside a ``widgets.PlotCard``, the styling
honours the card's active ``plotTheme()`` — ``"publication"`` (white bg,
``theme.CPub`` ink — the canonical / exported state) or ``"screen"`` (the dark
token set, a per-card live preview). When the axes is on a bare figure (no
card), falls back to the legacy ``ui.theme`` constants (publication-style).
"""

from __future__ import annotations

from ui.theme import PLOT_BG, PLOT_GRD, PLOT_SPN, PLOT_TXT, TXT_PRI
from well_viewer.core.safe import swallowed

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
        return PLOT_BG, TXT_PRI, PLOT_TXT, PLOT_GRD, PLOT_SPN

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
        return PLOT_BG, TXT_PRI, PLOT_TXT, PLOT_GRD, PLOT_SPN


# Internal alias (was the original name).
_tokens_for = tokens_for


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
    # Per-cell scatter defaults to log/log (fluorescence spans decades). These
    # are scatter-specific so other plot types' linear default can't reset them.
    "scatter_x_log": True,
    "scatter_y_log": True,
}


def _to_float_or_none(value: str):
    s = str(value).strip()
    if not s:
        return None
    return float(s)


def _ensure_export_style_prefs(app) -> dict:
    if not hasattr(app, "_export_style_prefs"):
        app._export_style_prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    return app._export_style_prefs


def apply_export_style_prefs(fig, prefs: dict) -> None:
    for ax in fig.axes:
        if not hasattr(ax, "_fixed_axes_position"):
            ax._fixed_axes_position = ax.get_position().frozen()

    fig.patch.set_alpha(1.0)

    axis_target = str(prefs.get("axis_target", "All"))
    target_idx = None if axis_target == "All" else int(axis_target)

    for idx, ax in enumerate(fig.axes, start=1):
        # Theme-aware chrome: this function used to hardcode "black" everywhere
        # (publication-ink), which was correct on export but overrode the
        # PlotCard's screen-theme styling whenever it was called from a redraw.
        _bg, _title_fg, _muted_fg, _grid, _spine = tokens_for(ax)
        ax.patch.set_alpha(1.0)
        ax.xaxis.label.set_color(_muted_fg)
        ax.yaxis.label.set_color(_muted_fg)
        ax.xaxis.label.set_fontsize(int(prefs.get("axis_label_size", 12)))
        ax.yaxis.label.set_fontsize(int(prefs.get("axis_label_size", 12)))
        ax.tick_params(axis="x", labelsize=int(prefs.get("tick_label_size", 10)), colors=_muted_fg)
        ax.tick_params(axis="y", labelsize=int(prefs.get("tick_label_size", 10)), colors=_muted_fg)
        ax.title.set_fontsize(int(prefs.get("title_size", 14)))
        ax.title.set_color(_title_fg)

        _fam = _plot_font_family()
        for tick in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
            tick.set_color(_muted_fg)
            tick.set_fontfamily(_fam)
            tick.set_fontsize(int(prefs.get("tick_label_size", 10)))

        ax.xaxis.label.set_fontfamily(_fam)
        ax.yaxis.label.set_fontfamily(_fam)
        ax.title.set_fontfamily(_fam)

        for tick in ax.get_xticklabels():
            tick.set_rotation(int(prefs.get("x_tick_angle", 0)))

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

        if target_idx is None or idx == target_idx:
            xlo = _to_float_or_none(prefs.get("x_lim_min", ""))
            xhi = _to_float_or_none(prefs.get("x_lim_max", ""))
            ylo = _to_float_or_none(prefs.get("y_lim_min", ""))
            yhi = _to_float_or_none(prefs.get("y_lim_max", ""))
            if xlo is not None or xhi is not None:
                cur = ax.get_xlim()
                ax.set_xlim(xlo if xlo is not None else cur[0], xhi if xhi is not None else cur[1])
            if ylo is not None or yhi is not None:
                cur = ax.get_ylim()
                ax.set_ylim(ylo if ylo is not None else cur[0], yhi if yhi is not None else cur[1])
            # ``_aw_force_xlog``/``_aw_force_ylog`` let an axis pin its own log
            # state (the scatter plot defaults to log/log via a scatter-specific
            # pref) so the shared x_log/y_log — which other plot types reset —
            # can't clobber it on a later restyle.
            if not getattr(ax, "_categorical_xaxis", False):
                _fx = getattr(ax, "_aw_force_xlog", None)
                _xlog = bool(prefs.get("x_log", False)) if _fx is None else bool(_fx)
                ax.set_xscale("log" if _xlog else "linear")
            if not getattr(ax, "_categorical_yaxis", False):
                _fy = getattr(ax, "_aw_force_ylog", None)
                _ylog = bool(prefs.get("y_log", False)) if _fy is None else bool(_fy)
                ax.set_yscale("log" if _ylog else "linear")

        if bool(prefs.get("tick_minor", False)):
            ax.minorticks_on()
        else:
            ax.minorticks_off()
        length = float(prefs.get("tick_length", 4.0))
        direction = str(prefs.get("tick_direction", "out"))
        if bool(prefs.get("tick_major", True)):
            ax.tick_params(which="major", length=length, direction=direction)
        else:
            ax.tick_params(which="major", length=0)
        if bool(prefs.get("tick_minor", False)):
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


def apply_export_style_to_current(app, fig, canvas=None) -> None:
    prefs = _ensure_export_style_prefs(app)
    apply_export_style_prefs(fig, prefs)
    if canvas is not None:
        canvas.draw_idle()
