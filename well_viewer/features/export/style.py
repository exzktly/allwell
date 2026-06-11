"""Export-style profile management + figure export pipeline.

The right-side sidebar widget that hosts the editor lives in
``well_viewer.views.export_style_sidebar_view`` so the GUI portion is
swappable. This module owns the export *pipeline*: figure render / clipboard
write, profile lookups (``EXPORT_PROFILES``), and ``launch_export_editor``.

The neutral figure-styling engine (``DEFAULT_EXPORT_STYLE_PREFS``,
``apply_export_style_prefs``, ``apply_export_style_to_current``) moved to
``well_viewer.core.plot_style`` (T2) so every plot feature can import it
*downward* instead of reaching sideways into this export feature — that
sideways edge had formed a scatter↔export import cycle. The names are
re-exported below so existing
``from well_viewer.features.export.style import …`` call sites keep working.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QMessageBox, QWidget
from well_viewer.core.safe import swallowed
from well_viewer.core.plot_style import (  # noqa: F401  (re-export for compat)
    DEFAULT_EXPORT_STYLE_PREFS,
    _ensure_export_style_prefs,
    apply_export_style_prefs,
    apply_export_style_to_current,
)


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
    """Write the figure to the macOS pasteboard under the UTIs Illustrator
    AND Microsoft Office read.

    Qt's ``QMimeData`` cannot register a payload under a raw macOS UTI
    (``com.adobe.pdf`` / ``public.svg-image``) — it treats the string as a
    MIME type and mangles it — so a vector app pasting from a Qt clipboard
    only ever finds the raster flavour. Writing ``NSPasteboard`` directly is
    the only way to put true vector data where Illustrator looks for it.

    v5.2.30: Microsoft Office on macOS (PowerPoint, Word, Excel) doesn't
    read the modern ``com.adobe.pdf`` UTI for vector paste — it still
    keys on the pre-UTI pasteboard-type name ``"Apple PDF pasteboard
    type"`` (lower-case 'p' in "pasteboard"). Pre-v5.2.30 we declared
    only the UTI, so Office's vector-paste detection missed the PDF and
    fell back to the PNG flavour, rasterising the figure. Now we
    declare the PDF under BOTH names. We also include TIFF
    alongside PNG because Office prefers TIFF for its raster fallback;
    apps that don't recognise TIFF still find PNG further down the
    list.

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
            # PDF goes in TWICE: once under the modern UTI (Adobe,
            # Preview, recent Cocoa apps) and once under the legacy
            # NSPasteboard.Name from before macOS adopted UTIs. Office
            # on macOS still reads the legacy name. Order matters —
            # the legacy name leads so Office's paste handler picks
            # PDF before it gets to the raster fallbacks.
            flavours.append(("Apple PDF pasteboard type", pdf_bytes))
            flavours.append(("com.adobe.pdf", pdf_bytes))
        if svg_bytes:
            flavours.append(("public.svg-image", svg_bytes))
        if png_bytes:
            # TIFF first: Office prefers it for the raster fallback
            # over PNG. tiff_bytes is None when PIL/Pillow isn't
            # available — we just skip it.
            tiff_bytes = _png_to_tiff(png_bytes)
            if tiff_bytes:
                flavours.append(("public.tiff", tiff_bytes))
            flavours.append(("public.png", png_bytes))
        if not flavours:
            return False
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        # declareTypes_owner_ must list every type before any setData
        # call — Illustrator picks the first vector type it
        # recognises, and Office picks the first one IT recognises.
        pb.declareTypes_owner_([uti for uti, _ in flavours], None)
        wrote = False
        for uti, data in flavours:
            nsdata = NSData.dataWithBytes_length_(data, len(data))
            if pb.setData_forType_(nsdata, uti):
                wrote = True
        return wrote
    except Exception:
        return False


def _png_to_tiff(png_bytes: bytes) -> bytes:
    """Convert PNG bytes to TIFF bytes for the macOS pasteboard's
    Office-friendly raster fallback. Returns ``b""`` if PIL isn't
    importable (no Pillow in the environment) or conversion fails."""
    if not png_bytes:
        return b""
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(png_bytes)) as im:
            out = io.BytesIO()
            im.save(out, format="TIFF")
            return out.getvalue()
    except Exception:
        return b""


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


def _ensure_custom_export_profiles(app) -> dict:
    if not hasattr(app, "_export_style_custom_profiles"):
        app._export_style_custom_profiles = {}
    return app._export_style_custom_profiles


def _get_all_profile_names(app) -> list[str]:
    custom = _ensure_custom_export_profiles(app)
    return [*EXPORT_PROFILES.keys(), *custom.keys()]


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
        from well_viewer.features.export.sidebar_view import ExportStyleSidebar

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
                from well_viewer.features.export.sidebar_view import (
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
            from well_viewer.features.export.sidebar_view import (
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
            swallowed()  # was: pass (silent except)
        # Re-populate the line-order lists so they reflect the current rep-set
        # / well selection each time the panel is opened.
        try:
            sb._refresh_line_order_lists()
        except Exception:
            swallowed()  # was: pass (silent except)
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
            swallowed()  # was: pass (silent except)
        return
    from ui.theme import PLOT_BG
    from well_viewer.features.export.style import launch_export_editor
    launch_export_editor(app, fig, default_name, plot_bg=PLOT_BG, canvas=canvas)

