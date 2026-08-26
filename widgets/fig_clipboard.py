"""Figure → clipboard helpers (widget-safe, app-agnostic).

The figure-export *pipeline* (render to SVG/PDF/PNG and write to the system
clipboard) lives here so it can be shared by the ``widgets/`` layer — notably
``widgets.plot_card.PlotCard`` (per-axis "copy this panel") — without a widget
reaching sideways into ``well_viewer.features.*`` (architecture guardrail G8:
``widgets/`` may import only ``theme`` + Qt + matplotlib + other ``widgets/``).

These functions previously lived in
``well_viewer/features/export/style.py``; that module now re-exports them so
existing ``from well_viewer.features.export.style import …`` call sites keep
working. They depend only on matplotlib + PySide6 (+ optional PIL/AppKit), so
they are safe to import from anywhere.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager

from util.aw_safe import swallow, swallowed

_logger = logging.getLogger("well_viewer")


@contextmanager
def hidden_sibling_axes(fig, keep_ax):
    """Hide every axes except *keep_ax* for the duration of the block, then
    restore the original visibility.

    Used so a bbox-cropped ``savefig`` (or clipboard export) renders ONLY the
    target panel: ``savefig(bbox_inches=<one axis>)`` still rasterises the whole
    figure and merely crops to that rectangle, so a neighbouring panel whose
    title / axis label / tick labels physically fall inside the crop rectangle
    would otherwise bleed into the output. Hiding the siblings keeps the crop
    clean without moving the target (manual ``subplots_adjust`` layouts don't
    relayout on ``set_visible``).
    """
    others = [a for a in fig.axes if a is not keep_ax]
    prev = [a.get_visible() for a in others]
    for a in others:
        a.set_visible(False)
    try:
        yield
    finally:
        for a, vis in zip(others, prev):
            a.set_visible(vis)


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


#: The most drawable primitives a figure may hold before its **dense layers** are
#: rasterized rather than emitted as individual shapes.
#:
#: Set high on purpose. An editable figure is the whole point of the PowerPoint
#: export, so the bar for giving any of it up is a deck that will not open rather
#: than a deck that is merely large. Measured on this repository's own scatter
#: figures, cost is linear with no cliff: 20,000 cells is a 2.1 MB capture and a
#: two-second deck, 100,000 is 10.6 MB and ten seconds. A per-cell scatter of a
#: whole plate therefore stays fully vector, which is what a user restyling one
#: for a figure panel needs.
#:
#: Past this the figure is still not refused — only its densest collections are
#: rasterized (see :func:`render_figure_svg`), so the axes, ticks, labels, title
#: and legend stay editable and only the point cloud becomes a picture.
MAX_VECTOR_PATHS = 250_000

#: Resolution for any layer that had to be rasterized. High enough that the
#: raster half of a mixed figure does not read as blurry beside the vector half
#: when a slide is projected.
RASTERIZED_LAYER_DPI = 300


def figure_path_count(fig) -> int:
    """Roughly how many separate primitives *fig* will draw.

    An estimate on purpose: it reads artist counts rather than rendering, so it
    is constant-time in the number of data points. A scatter contributes its
    offsets (one marker each) rather than its single shared marker path, which
    is the case that matters — everything else is already small.
    """
    total = 0
    for ax in getattr(fig, "axes", ()):
        for collection in getattr(ax, "collections", ()):
            offsets = 0
            try:
                offsets = len(collection.get_offsets())
            except Exception:
                swallowed("figure path count: collection offsets")
            total += max(len(collection.get_paths()), offsets)
        total += (len(getattr(ax, "lines", ())) + len(getattr(ax, "patches", ()))
                  + len(getattr(ax, "texts", ())))
    return total


@contextmanager
def _dense_layers_rasterized(fig, max_paths: int):
    """Mark the figure's densest collections rasterized for the duration.

    Biggest first, stopping as soon as the remaining primitive count is under
    *max_paths*, so a figure gives up the least it can: a scatter of fifty
    thousand cells beside a fitted line loses the cloud and keeps the line.
    Matplotlib renders a rasterized artist into an embedded image and leaves
    everything else — axes, spines, ticks, labels, title, legend — as vector.

    Every artist's previous state is restored on the way out, because the figure
    is the live one on screen and this must not change how it draws there.
    """
    collections = []
    for ax in getattr(fig, "axes", ()):
        for collection in getattr(ax, "collections", ()):
            weight = 0
            try:
                weight = len(collection.get_offsets())
            except Exception:
                swallowed("rasterize: collection offsets")
            collections.append((max(len(collection.get_paths()), weight),
                                collection))
    collections.sort(key=lambda item: item[0], reverse=True)

    remaining = figure_path_count(fig)
    touched = []
    for weight, collection in collections:
        if remaining <= max_paths:
            break
        touched.append((collection, collection.get_rasterized()))
        collection.set_rasterized(True)
        remaining -= weight
    try:
        yield touched
    finally:
        for collection, was in touched:
            with swallow("rasterize: restore"):
                collection.set_rasterized(was)


def render_figure_svg(fig, *, max_paths: int | None = MAX_VECTOR_PATHS,
                      **savefig_kwargs) -> bytes:
    """Render *fig* to SVG bytes, with text left as text.

    ``svg.fonttype="none"`` is the whole point: Matplotlib's default writes every
    glyph as an outlined path, which is faithful but dead — a title exported that
    way cannot be retyped in PowerPoint or Illustrator, only redrawn. With it
    off, each label stays a ``<text>`` element and survives into whatever reads
    the SVG next.

    A figure holding more than *max_paths* primitives has its densest layers
    rasterized rather than being refused: the point cloud becomes one embedded
    image and the rest of the figure stays editable. Pass ``max_paths=None`` to
    render everything as vector regardless, which is what a caller wanting
    vector for its own sake (rather than for a slide deck) should do —
    :func:`copy_figure_to_clipboard` is that caller.

    Returns ``b""`` only if the figure could not be rendered at all, so a caller
    capturing vector alongside a raster never loses the raster to a vector
    failure.
    """
    kw = {"bbox_inches": "tight"}
    kw.setdefault("dpi", RASTERIZED_LAYER_DPI)
    kw.update(savefig_kwargs)
    if max_paths is None or figure_path_count(fig) <= max_paths:
        return _render_figure(fig, "svg", kw, {"svg.fonttype": "none"})
    with _dense_layers_rasterized(fig, max_paths) as touched:
        rendered = _render_figure(fig, "svg", kw, {"svg.fonttype": "none"})
    if touched and rendered:
        _logger.info(
            "saved_plots: %d dense layer(s) rasterized to keep the rest of the "
            "figure editable", len(touched))
    return rendered


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
    Pass ``bbox_inches=<Bbox>`` to crop to a single axis (see
    :func:`axis_tight_bbox_inches`).
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


def axis_tight_bbox_inches(ax, *, pad_in: float = 0.04):
    """Return a ``Bbox`` (in inches) tightly enclosing a single *ax*.

    Includes the axes' tick labels, axis labels and title, so passing the
    result as ``savefig(bbox_inches=…)`` crops the saved figure to just that
    subplot.

    The canvas is drawn first so the renderer exists and artist positions are
    final — this is correct for both ``layout="constrained"`` figures and ones
    using manual ``subplots_adjust``, since ``get_tightbbox`` reads the
    already-laid-out extents. *pad_in* adds a uniform margin (inches).

    ``Axes.get_tightbbox`` can omit a *shared* axis's own label / tick labels —
    in the "Separate axes" stacked scatter (panels share x and y), saving a
    panel cropped the y-axis title off. To be robust to that, union the panel's
    own decoration artists (axis labels, title, tick labels, offset text)
    explicitly. For a non-shared axis these already fall inside
    ``get_tightbbox`` so the union is a no-op.
    """
    from matplotlib.transforms import Bbox

    fig = ax.figure
    fig.canvas.draw()                                       # ensure renderer + final positions
    renderer = fig.canvas.get_renderer()
    extents = [ax.get_tightbbox(renderer)]                  # display px (bottom-left origin)

    decorations = [ax.yaxis.label, ax.xaxis.label, ax.title,
                   ax.yaxis.get_offset_text(), ax.xaxis.get_offset_text()]
    try:
        decorations += list(ax.get_yticklabels()) + list(ax.get_xticklabels())
    except Exception:
        swallowed()
    for art in decorations:
        try:
            if art is None or not art.get_visible():
                continue
            if hasattr(art, "get_text") and not str(art.get_text()):
                continue
            ext = art.get_window_extent(renderer)
            if ext.width > 0 and ext.height > 0:
                extents.append(ext)
        except Exception:
            swallowed()
            continue

    bb = Bbox.union(extents)
    bb = bb.transformed(fig.dpi_scale_trans.inverted())     # -> inches
    return bb.padded(pad_in)                                # uniform margin; scalar = version-safe

