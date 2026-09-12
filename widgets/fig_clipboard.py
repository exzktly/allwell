"""Figure → clipboard helpers (widget-safe, app-agnostic).

The figure-export *pipeline* (render to SVG/PDF/PNG and write to the system
clipboard) lives here so it can be shared by the ``widgets/`` layer — notably
``widgets.plot_card.PlotCard`` (per-axis "copy this panel") — without a widget
reaching sideways into ``review.features.*`` (architecture guardrail G8:
``widgets/`` may import only ``theme`` + Qt + matplotlib + other ``widgets/``).

These functions previously lived in
``review/features/export/style.py``; that module now re-exports them so
existing ``from review.features.export.style import …`` call sites keep
working. They depend only on matplotlib + PySide6 (+ optional PIL/AppKit), so
they are safe to import from anywhere.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

from util.aw_safe import swallow, swallowed

if TYPE_CHECKING:                                # matplotlib is imported lazily
    from matplotlib.backend_bases import RendererBase
    from matplotlib.transforms import BboxBase

_logger = logging.getLogger("review")


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
#: Set from how long the resulting deck takes to *open*, which is the only number
#: that decides it. Measured end to end (LibreOffice Impress converting the deck
#: this repository's own export produces, on a 4-core container):
#:
#:   10,000 shapes →   8 s
#:   20,000        →  17 s
#:   40,000        →  46 s
#:  100,000        → 2 min 39 s
#:  249,000        → killed, out of memory at 13.9 GB
#:
#: Superlinear, with a hard wall. An earlier version of this constant was set to
#: 250,000 from the *build* time instead — which is linear, and is not what the
#: user waits for — and produced decks that cannot be opened at all.
#:
#: Passing it costs very little, which is why a conservative number is right:
#: the figure is not refused, only its densest collections are rasterized (see
#: :func:`render_figure_svg`), so a scatter of any size still exports with its
#: axes, ticks, labels, title and legend as editable shapes. The cap decides
#: only when the *point cloud* stops being individual shapes, and a cloud of
#: twenty-five thousand marks is not something anyone restyles mark by mark.
MAX_VECTOR_PATHS = 25_000

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
    kw.update(savefig_kwargs)
    # Resolve a "tight" crop into the explicit rectangle it stands for, before
    # anything is rendered. The crop decides where the axes sit on the page, and
    # a consumer that has to *guess* the crop cannot know that: recomputing it
    # here after the fact lands within a point or two of Matplotlib's own, and a
    # point or two is a visibly misplaced figure once it is scaled onto a slide.
    # Asked for an explicit Bbox, Matplotlib honours it exactly, so from here on
    # the page really is the rectangle this function chose.
    kw["bbox_inches"] = crop = _resolved_crop(fig, kw.get("bbox_inches"))
    axes_frac = figure_axes_fraction(fig, crop)
    if max_paths is None or figure_path_count(fig) <= max_paths:
        # No layer was given up, so no marker: an SVG with no marker is one whose
        # every mark is a shape.
        return _mark_axes_rect(
            _render_figure(fig, "svg", kw, {"svg.fonttype": "none"}), axes_frac)
    kw.setdefault("dpi", RASTERIZED_LAYER_DPI)
    with _dense_layers_rasterized(fig, max_paths) as touched:
        rendered = _render_figure(fig, "svg", kw, {"svg.fonttype": "none"})
    rendered = _mark_axes_rect(rendered, axes_frac)
    if touched and rendered:
        _logger.info("%d dense layer(s) rasterized to keep the rest of the "
                     "figure editable", len(touched))
        # Recorded **in the file**, not only in this log line. The consumer reads
        # an embedded image as an ordinary picture inside an otherwise editable
        # figure — which is exactly what a heat map legitimately is — so without
        # a marker it cannot tell "this figure contains a picture" from "this
        # figure's data became a picture", and reported a rasterized point cloud
        # as fully editable.
        rendered = _mark_rasterized(rendered, len(touched))
    return rendered


#: Written into a capture whose dense layers were rasterized, so the consumer can
#: say so. An XML comment: invisible to every renderer, preserved by every parser,
#: and impossible to confuse with figure content.
RASTERIZED_MARKER = "allwell:rasterized-layers="


def _insert_marker(svg: bytes, marker: str) -> bytes:
    """Put an XML comment in front of the ``<svg>`` element."""
    head = svg.find(b"<svg")
    if head < 0:
        return svg
    return svg[:head] + f"<!-- {marker} -->".encode() + svg[head:]


def _mark_rasterized(svg: bytes, layers: int) -> bytes:
    """Record in the SVG itself that *layers* dense layers became images."""
    return _insert_marker(svg, f"{RASTERIZED_MARKER}{layers}")


def rasterized_layer_count(svg: bytes) -> int:
    """How many dense layers were rasterized when *svg* was captured, or 0."""
    import re

    match = re.search(RASTERIZED_MARKER.encode() + rb"(\d+)", svg or b"")
    return int(match.group(1)) if match else 0


#: Written into a capture to record where the figure's plotting area sits on the
#: page it was rendered onto. Four numbers — ``x0,y0,x1,y1`` as fractions of the
#: page's width and height, with *y* measured downward from the top the way SVG
#: measures it — so a consumer scaling the page onto a slide can put the *axes*
#: somewhere chosen rather than wherever the crop happened to leave them.
#:
#: Fractions rather than points because the consumer already knows the page size
#: it parsed, and a fraction cannot disagree with it: a marker in absolute units
#: written against one page and read against another (a ``viewBox`` that scales,
#: a re-crop) would be silently wrong rather than obviously absent.
AXES_RECT_MARKER = "allwell:axes-frac="

#: The smallest share of a page a recorded plotting area may claim in either
#: direction before it is read as damage rather than as a measurement.
_MIN_AXES_FRACTION = 0.05


def _mark_axes_rect(svg: bytes, frac) -> bytes:
    """Record where the plotting area sits, when it could be established."""
    if not svg or frac is None:
        return svg
    return _insert_marker(
        svg, AXES_RECT_MARKER + ",".join(f"{v:.6f}" for v in frac))


def axes_fraction_from_svg(svg: bytes) -> tuple[float, float, float, float] | None:
    """Where the plotting area sits on *svg*'s page, or ``None``.

    ``None`` for every capture written before this was recorded, and for one
    whose figure had no visible axes to anchor on — both are "this figure cannot
    be aligned with the others", which the caller states rather than papering
    over with a guess.
    """
    import re

    match = re.search(AXES_RECT_MARKER.encode() + rb"([-\d.,eE+]+)", svg or b"")
    if not match:
        return None
    try:
        values = [float(part) for part in match.group(1).split(b",")]
    except ValueError:
        return None
    if len(values) != 4:
        return None
    x0, y0, x1, y1 = values
    if not (x1 > x0 and y1 > y0):
        return None
    if (x1 - x0) < _MIN_AXES_FRACTION or (y1 - y0) < _MIN_AXES_FRACTION:
        # Not a plotting area. The page a capture is cropped to is the figure's
        # own ink, so a real axes — or the union of a panel grid's axes — is most
        # of it; a sliver is a marker that has been edited or corrupted in the
        # dataset folder, and honouring it would scale the figure by the
        # reciprocal of that sliver and throw it off the slide.
        return None
    return (x0, y0, x1, y1)


def _measuring_renderer(fig) -> RendererBase | None:
    """A laid-out renderer to measure *fig* against, or ``None``.

    A figure on screen carries a canvas that can hand one over; a bare
    ``Figure`` built for an export does not, and asks the figure itself. Neither
    is guaranteed across the Matplotlib versions this app supports, and a
    measurement that cannot be taken is not an error here — it means one capture
    records no plotting area and the deck says so, which is the outcome the
    export is already built to state.
    """
    with swallow("figure measure: layout"):
        fig.draw_without_rendering()
    for getter in (getattr(fig.canvas, "get_renderer", None),
                   getattr(fig, "_get_renderer", None)):
        if getter is None:
            continue
        try:
            renderer = getter()
        except Exception:
            swallowed("figure measure: renderer")
            continue
        if renderer is not None:
            return renderer
    return None


def _resolved_crop(fig, bbox_inches):
    """The crop rectangle a render will actually use, as an explicit ``Bbox``.

    ``"tight"`` is resolved here rather than left to ``savefig`` so this module
    knows the page it is about to produce; ``None`` means the whole canvas, which
    is a rectangle too. Anything already explicit is handed straight back, and a
    ``"tight"`` that could not be resolved is handed back as it came — the render
    is then exactly what it always was, minus the marker.
    """
    from matplotlib.transforms import Bbox, BboxBase

    if isinstance(bbox_inches, BboxBase):
        return bbox_inches
    if bbox_inches is None:
        width, height = fig.get_size_inches()
        return Bbox.from_bounds(0.0, 0.0, float(width), float(height))
    return figure_crop_bbox(fig) or bbox_inches


def figure_crop_bbox(fig, *, pad_in: float | None = None) -> BboxBase | None:
    """The rectangle ``savefig(bbox_inches="tight")`` would crop to, in inches.

    Matplotlib computes this inside ``print_figure`` and then throws it away, so
    a caller that needs to know where anything ended up on the page has no way to
    ask. Asking for it here and passing it back in as an explicit ``Bbox`` costs
    one extra ``get_tightbbox`` and makes the page a known quantity.

    The extents come from a renderer built here rather than the one the output
    backend will build, so this can differ from Matplotlib's own tight crop by a
    point or so where the two measure a font differently. That is a hair more or
    less whitespace and nothing else — every artist keeps its position on the
    page, because the page is then *defined* as this rectangle.

    ``None`` when the figure could not be measured.
    """
    import matplotlib as _mpl

    renderer = _measuring_renderer(fig)
    if renderer is None:
        return None
    if pad_in is None:
        pad_in = float(_mpl.rcParams.get("savefig.pad_inches", 0.1) or 0.0)
    try:
        return fig.get_tightbbox(renderer).padded(pad_in)
    except Exception:
        swallowed("figure crop: tight bbox")
        return None


def figure_axes_fraction(
        fig, crop=None) -> tuple[float, float, float, float] | None:
    """Where *fig*'s plotting area sits inside the page *crop* produces.

    Returns ``(x0, y0, x1, y1)`` as fractions of the page's width and height,
    with *y* downward from the top, or ``None`` when there is no visible axes to
    measure — a figure holding only text has no plotting area, and inventing one
    from its ink would align the deck on something that is not a plot.

    The union covers **every** visible axes, a colourbar and an inset included:
    the rectangle this describes is the region a reader would call the plot, and
    a colourbar left outside it would be treated as a margin and overlap the next
    figure when the slides are tiled. Hidden axes are excluded because a
    single-panel capture hides its siblings (:func:`hidden_sibling_axes`) and the
    page it crops to holds only the one panel.
    """
    from matplotlib.transforms import Bbox, BboxBase

    renderer = _measuring_renderer(fig)
    if renderer is None or not isinstance(crop, (BboxBase, type(None))):
        return None
    if crop is None:
        width, height = fig.get_size_inches()
        crop = Bbox.from_bounds(0.0, 0.0, float(width), float(height))
    page_w = float(crop.x1 - crop.x0)
    page_h = float(crop.y1 - crop.y0)
    if page_w <= 0 or page_h <= 0:
        return None
    dpi = float(fig.dpi) or 1.0
    xs: list[float] = []
    ys: list[float] = []
    for ax in getattr(fig, "axes", ()):
        if not ax.get_visible():
            continue
        try:
            extent = ax.get_window_extent(renderer)
        except Exception:
            swallowed("axes fraction: window extent")
            continue
        xs += [extent.x0 / dpi, extent.x1 / dpi]
        ys += [extent.y0 / dpi, extent.y1 / dpi]
    if not xs or not ys:
        return None

    def _clamp(value: float) -> float:
        return min(1.0, max(0.0, value))

    x0 = _clamp((min(xs) - crop.x0) / page_w)
    x1 = _clamp((max(xs) - crop.x0) / page_w)
    y0 = _clamp((crop.y1 - max(ys)) / page_h)
    y1 = _clamp((crop.y1 - min(ys)) / page_h)
    if not (x1 > x0 and y1 > y0):
        return None
    return (x0, y0, x1, y1)


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

