"""Export-style preferences, profile management, and figure export pipeline.

The right-side sidebar widget that hosts the editor lives in
``well_viewer.views.export_style_sidebar_view`` so the GUI portion is
swappable. This module owns the headless concerns: defaults, profile
lookups, ``apply_export_style_prefs``, and ``launch_export_editor``.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QMessageBox, QWidget


def _render_figure(fig, fmt, savefig_kwargs, rc_overrides) -> bytes:
    """Render *fig* to *fmt* and return the encoded bytes (``b""`` on error)."""
    import io

    import matplotlib as _mpl

    kw = dict(savefig_kwargs)
    kw.pop("format", None)
    try:
        buf = io.BytesIO()
        with _mpl.rc_context(rc_overrides):
            fig.savefig(buf, format=fmt, **kw)
        return buf.getvalue()
    except Exception:
        return b""


def _macos_pasteboard_write(pdf_bytes: bytes, svg_bytes: bytes,
                            png_bytes: bytes) -> bool:
    """Write the figure to the macOS pasteboard under the UTIs Illustrator reads.

    Qt's ``QMimeData`` cannot register a payload under a raw macOS UTI
    (``com.adobe.pdf`` / ``public.svg-image``) — it treats the string as a
    MIME type and mangles it — so a vector app pasting from a Qt clipboard
    only ever finds the raster flavour. Writing ``NSPasteboard`` directly is
    the only way to put true vector data where Illustrator looks for it.

    Returns ``True`` when the native write succeeded; ``False`` on any
    non-macOS platform, missing pyobjc, or error — the caller then falls
    back to the Qt clipboard so Copy never breaks to "nothing to paste".
    """
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSPasteboard
        from Foundation import NSData
    except Exception:
        return False
    try:
        flavours = []
        if pdf_bytes:
            flavours.append(("com.adobe.pdf", pdf_bytes))
        if svg_bytes:
            flavours.append(("public.svg-image", svg_bytes))
        if png_bytes:
            flavours.append(("public.png", png_bytes))
        if not flavours:
            return False
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        # declareTypes_owner_ must list every type before any setData call —
        # Illustrator picks com.adobe.pdf first because it leads the list.
        pb.declareTypes_owner_([uti for uti, _ in flavours], None)
        wrote = False
        for uti, data in flavours:
            nsdata = NSData.dataWithBytes_length_(data, len(data))
            if pb.setData_forType_(nsdata, uti):
                wrote = True
        return wrote
    except Exception:
        return False


def copy_figure_to_clipboard(fig, *, svg_kwargs=None, pdf_kwargs=None) -> str:
    """Copy *fig* to the clipboard as vector, returning a human status string.

    On macOS the figure is written natively to ``NSPasteboard`` as PDF + SVG
    + PNG, so Illustrator pastes true editable vector. Everywhere else (and
    if the native write fails) it falls back to a Qt ``QMimeData`` carrying
    PDF, SVG and a raster image so Copy still produces something pasteable.

    ``svg_kwargs`` / ``pdf_kwargs`` are extra ``Figure.savefig`` keyword
    arguments (default ``bbox_inches="tight"``); ``format`` is set here.
    """
    svg_kw = {"bbox_inches": "tight"} if svg_kwargs is None else dict(svg_kwargs)
    pdf_kw = {"bbox_inches": "tight"} if pdf_kwargs is None else dict(pdf_kwargs)

    # svg.fonttype="none" keeps text as editable <text> rather than outlines;
    # Type-42 (TrueType) fonts keep PDF text editable downstream.
    svg_bytes = _render_figure(fig, "svg", svg_kw, {"svg.fonttype": "none"})
    pdf_bytes = _render_figure(fig, "pdf", pdf_kw,
                               {"pdf.fonttype": 42, "ps.fonttype": 42})

    png_kw = dict(svg_kw)
    png_kw.setdefault("dpi", 200)
    png_bytes = _render_figure(fig, "png", png_kw, {})

    if not (svg_bytes or pdf_bytes):
        return "Copy failed: the figure could not be rendered."

    if _macos_pasteboard_write(pdf_bytes, svg_bytes, png_bytes):
        return "Copied as vector (PDF/SVG) — paste into Illustrator."

    from PySide6.QtCore import QByteArray, QMimeData
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    mime = QMimeData()
    if pdf_bytes:
        mime.setData("application/pdf", QByteArray(pdf_bytes))
    if svg_bytes:
        svg_qba = QByteArray(svg_bytes)
        mime.setData("image/svg+xml", svg_qba)
        mime.setData("image/svg", svg_qba)
    if png_bytes:
        mime.setData("image/png", QByteArray(png_bytes))
        img = QImage.fromData(png_bytes, "PNG")
        if not img.isNull():
            mime.setImageData(img)
    QApplication.clipboard().setMimeData(mime)
    if sys.platform == "darwin":
        return ("Copied as raster — native vector clipboard unavailable "
                "(install pyobjc-framework-Cocoa for vector paste).")
    return "Copied figure to clipboard (PDF/SVG/PNG)."


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
}

EXPORT_PROFILES = {
    "Custom": {},
    "Illustrator SVG": {"format": "svg", "layout_tight": True},
    "High-res PNG": {"format": "png", "layout_tight": True},
    "Print PDF": {"format": "pdf", "layout_tight": True},
    "Helvetica 22": {
        "axis_label_size": 22,
        "tick_label_size": 22,
        "title_size": 22,
        "legend_font_size": 22,
    },
}


def _ensure_export_style_prefs(app) -> dict:
    if not hasattr(app, "_export_style_prefs"):
        app._export_style_prefs = dict(DEFAULT_EXPORT_STYLE_PREFS)
    return app._export_style_prefs


def _ensure_custom_export_profiles(app) -> dict:
    if not hasattr(app, "_export_style_custom_profiles"):
        app._export_style_custom_profiles = {}
    return app._export_style_custom_profiles


def _get_all_profile_names(app) -> list[str]:
    custom = _ensure_custom_export_profiles(app)
    return [*EXPORT_PROFILES.keys(), *custom.keys()]


def _to_float_or_none(value: str):
    s = str(value).strip()
    if not s:
        return None
    return float(s)


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
        from well_viewer.plot_style import tokens_for as _tokens_for_ax
        _bg, _title_fg, _muted_fg, _grid, _spine = _tokens_for_ax(ax)
        ax.patch.set_alpha(1.0)
        ax.xaxis.label.set_color(_muted_fg)
        ax.yaxis.label.set_color(_muted_fg)
        ax.xaxis.label.set_fontsize(int(prefs.get("axis_label_size", 12)))
        ax.yaxis.label.set_fontsize(int(prefs.get("axis_label_size", 12)))
        ax.tick_params(axis="x", labelsize=int(prefs.get("tick_label_size", 10)), colors=_muted_fg)
        ax.tick_params(axis="y", labelsize=int(prefs.get("tick_label_size", 10)), colors=_muted_fg)
        ax.title.set_fontsize(int(prefs.get("title_size", 14)))
        ax.title.set_color(_title_fg)

        for tick in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
            tick.set_color(_muted_fg)
            tick.set_fontfamily("Helvetica")
            tick.set_fontsize(int(prefs.get("tick_label_size", 10)))

        ax.xaxis.label.set_fontfamily("Helvetica")
        ax.yaxis.label.set_fontfamily("Helvetica")
        ax.title.set_fontfamily("Helvetica")

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
                cur_ls in ("None", "none", "", " ") and
                cur_mk not in ("None", "none", "", None)
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
            if not getattr(ax, "_categorical_xaxis", False):
                ax.set_xscale("log" if bool(prefs.get("x_log", False)) else "linear")
            if not getattr(ax, "_categorical_yaxis", False):
                ax.set_yscale("log" if bool(prefs.get("y_log", False)) else "linear")

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
                        pass
            for txt in leg.get_texts():
                txt.set_fontsize(float(prefs.get("legend_font_size", 9)))
                txt.set_color(_title_fg)
                txt.set_fontfamily("Helvetica")

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
                pass


def apply_export_style_to_current(app, fig, canvas=None) -> None:
    prefs = _ensure_export_style_prefs(app)
    apply_export_style_prefs(fig, prefs)
    if canvas is not None:
        canvas.draw_idle()


class _ExportEditorSession:
    def __init__(self, sidebar) -> None:
        self.sidebar = sidebar


def _resolve_export_dock(app, fig) -> QWidget | None:
    """Return the pre-allocated right-side dock widget for a given figure, or None."""
    mapping = (
        ("_line_fig", "_line_export_dock"),
        ("_bar_fig", "_bar_export_dock"),
        ("_scatter_fig", "_scatter_export_dock"),
        ("_scatter_agg_fig", "_scatter_agg_export_dock"),
        ("_heatmap_fig", "_heatmap_export_dock"),
        ("_distribution_fig", "_distribution_export_dock"),
    )
    for fig_attr, dock_attr in mapping:
        if getattr(app, fig_attr, None) is fig:
            return getattr(app, dock_attr, None)
    return None


def launch_export_editor(app, fig, default_name: str, *, plot_bg: str = "",
                          canvas=None) -> _ExportEditorSession | None:
    try:
        # Lazy import: the view module imports symbols defined above, so a
        # top-level import would create a circular dependency.
        from well_viewer.views.export_style_sidebar_view import ExportStyleSidebar

        dock = _resolve_export_dock(app, fig)
        parent = dock if dock is not None else (canvas.parent() if canvas is not None else app)
        if not hasattr(app, "_export_style_sidebars"):
            app._export_style_sidebars = {}
        key = id(fig)
        sb = app._export_style_sidebars.get(key)
        if sb is None:
            sb = ExportStyleSidebar(app, parent, fig, canvas, default_name=default_name)
            app._export_style_sidebars[key] = sb

            if dock is not None and dock.layout() is not None:
                dock.layout().addWidget(sb)
                # Pin the dock container's width to the SAME constant the
                # sidebar uses for its own setFixedWidth — ``sizeHint()``
                # can lag the fixed-width on the first show (which left
                # the container narrower than its child, clipping content
                # against the window edge). Importing the constant here
                # keeps the two sides in lock-step.
                from well_viewer.views.export_style_sidebar_view import (
                    EXPORT_STYLE_PANEL_WIDTH as _PANEL_W,
                )
                host = getattr(dock, "_dock_host", None)
                if host is not None and hasattr(host, "set_overlay_dock"):
                    host.set_overlay_dock(dock, _PANEL_W)
            elif canvas is not None:
                canvas_parent = canvas.parentWidget()
                if canvas_parent is not None:
                    parent_layout = canvas_parent.layout()
                    if parent_layout is not None and hasattr(parent_layout, "addWidget"):
                        parent_layout.addWidget(sb)

        if dock is not None:
            from well_viewer.views.export_style_sidebar_view import (
                EXPORT_STYLE_PANEL_WIDTH as _PANEL_W,
            )
            host = getattr(dock, "_dock_host", None)
            if host is not None and hasattr(host, "set_overlay_dock"):
                host.set_overlay_dock(dock, _PANEL_W)
            dock.setVisible(True)
        sb.show()
        sb.raise_()
        # Apply the (already-persisted) prefs to the figure once. Use the
        # direct entry point rather than ``sb._on_fields_changed()`` — the
        # latter also copies every widget value back onto ``_export_style_prefs``
        # (a redundant no-op right after the widgets were initialised from
        # those same prefs) and is wired to every widget's change signal, so
        # routing the open path through it risks extra restyle+redraw passes
        # on a heavy (tall, 3-panel) bar figure.
        try:
            apply_export_style_to_current(app, fig, canvas)
        except Exception:
            pass
        # Re-populate the line-order lists so they reflect the current rep-set
        # / well selection each time the panel is opened.
        try:
            sb._refresh_line_order_lists()
        except Exception:
            pass
        return _ExportEditorSession(sb)
    except Exception as exc:
        QMessageBox.warning(app if isinstance(app, QWidget) else None,
                            "Export editor unavailable", str(exc))
        return None


def open_export_style_panel(app, plot_key: str) -> None:
    """Open the floating Export Style sidebar for ``plot_key``'s figure.

    Each plot tab (line / bar / scatter cells / scatter agg /
    distribution / heatmap) pre-allocates a right-side dock when its
    view is built. This routes the per-card "▸" style button to the
    ExportStyleSidebar mounted in that dock.
    """
    mapping = {
        "line":          ("_line_fig",         "_line_canvas",         "line_figure"),
        "bar":           ("_bar_fig",          "_bar_canvas",          "bar_figure"),
        "scatter_cells": ("_scatter_fig",      "_scatter_canvas",      "scatter_figure"),
        "scatter_agg":   ("_scatter_agg_fig",  "_scatter_agg_canvas",  "scatter_agg_figure"),
        "distribution":  ("_distribution_fig", "_distribution_canvas", "distribution_figure"),
        "heatmap":       ("_heatmap_fig",      "_heatmap_canvas",      "heatmap_figure"),
    }
    spec = mapping.get(plot_key)
    if spec is None:
        app._set_status(f"Unknown export panel: {plot_key}")
        return
    fig_attr, canvas_attr, default_name = spec
    fig = getattr(app, fig_attr, None)
    canvas = getattr(app, canvas_attr, None)
    if fig is None:
        app._set_status("Plot is not ready yet.")
        return
    # If a sidebar for this figure is already open, toggle it off so
    # the button behaves as an open/close affordance.
    existing = (getattr(app, "_export_style_sidebars", {}) or {}).get(id(fig))
    if existing is not None and existing.isVisible():
        try:
            existing._close_dock()
        except Exception:
            pass
        return
    from well_viewer.figure_export_editor import launch_export_editor
    launch_export_editor(app, fig, default_name, plot_bg=PLOT_BG, canvas=canvas)

