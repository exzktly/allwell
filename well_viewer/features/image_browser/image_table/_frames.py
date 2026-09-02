"""Re-express the exported image grid as one frame per field of view.

The PNG/PDF/SVG export draws the grid as a matplotlib figure: every cell's
overlays — the identity label, the scale bar, the "raw" mark — are burned into
that cell's pixels by ``image_compose.draw_cell_chrome``, and the axis labels are
matplotlib text. That is right for a picture and wrong for a slide, where a user
wants to move one field, retype its label, or recolour a bar.

So the export builds a second thing alongside the figure: a
:class:`~well_viewer.domain.image_frames.FramedGrid` holding each field's pixels
*without* the burn-in, plus every overlay stated as a mark with its own place.
The deck builder turns that into one PowerPoint group per field.

Geometry comes from the same gridspec the figure uses — each slot's rectangle is
read back off it rather than recomputed — so the frames land exactly where the
figure's cells did. The marks inside a frame are placed from
``image_compose``'s own geometry helpers (:func:`~well_viewer.features.
image_browser.image_table.image_compose.bar_rect_px` and friends), which is what
keeps a 50 µm bar the same length in the deck as in the picture.

Qt-free. The collector takes plain arrays and dicts, so a grid can be assembled
and asserted on without an application.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any

from well_viewer.domain.image_frames import (
    BarMark, FramedGrid, ImageFrame, Rect, TextMark,
)
from well_viewer.features.image_browser.image_table import renderer as _renderer
from well_viewer.features.image_browser.image_table.image_compose import (
    _chrome_rgb, bar_ink_rgb, bar_rect_px, label_font_px_for, label_margin_px,
)

logger = logging.getLogger(__name__)

_PT_PER_IN = 72.0

#: Mean glyph advance as a fraction of the font size — the same approximation
#: ``_drawingml`` uses to size a text frame. Only the *box* depends on it; the
#: run's own alignment does the positioning, so a box a few percent wide still
#: puts the label on its corner.
_ADVANCE_FRACTION = 0.62

#: How much taller than the font a single-line label's box is.
_LINE_FRACTION = 1.35

#: The translucent plate behind an identity label, matching the ``(0, 0, 0, 140)``
#: ``draw_cell_chrome`` fills — 140/255 of full opacity, in DrawingML's
#: hundred-thousandths.
PLATE_COLOR = "000000"
PLATE_ALPHA = int(round(140 / 255 * 100_000))


def _hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(v))) for v in rgb[:3])
    return f"{r:02X}{g:02X}{b:02X}"


def _fit(aspect: float, x: float, y: float, w: float, h: float) -> Rect:
    """Centre a box of ratio *aspect* (width/height) inside another.

    ``imshow(..., aspect="equal")`` shrinks the axes to the image's own ratio and
    centres it in the slot, so a frame placed on the full slot would be wider or
    taller than the pixels the figure actually drew.
    """
    if aspect <= 0 or w <= 0 or h <= 0:
        return Rect(x, y, w, h)
    box_aspect = w / h
    if aspect >= box_aspect:
        used_w, used_h = w, w / aspect
    else:
        used_h, used_w = h, h * aspect
    return Rect(x + (w - used_w) / 2.0, y + (h - used_h) / 2.0, used_w, used_h)


def _slot_rect(gs, key, fig) -> Rect | None:
    """A gridspec slot's rectangle in inches from the figure's top-left.

    matplotlib measures from the bottom-left in figure fractions; the plan is in
    inches from the top-left, because that is PowerPoint's frame of reference and
    a plan that flipped its axis halfway would misplace every label in a way the
    slide would not reveal.
    """
    try:
        fig_w, fig_h = (float(v) for v in fig.get_size_inches())
        box = gs[key].get_position(fig)
    except (AttributeError, IndexError, TypeError, ValueError):
        logger.debug("image_table: could not read a grid slot's position",
                     exc_info=True)
        return None
    return Rect(box.x0 * fig_w, (1.0 - box.y1) * fig_h,
                box.width * fig_w, box.height * fig_h)


def _encode_png(rgb) -> bytes:
    """One cell's RGB as PNG bytes, or ``b""`` when the imaging stack cannot."""
    if rgb is None:
        return b""
    try:
        from PIL import Image

        import numpy as _np

        a = _np.asarray(rgb)
        if a.ndim != 3 or a.shape[2] < 3:
            return b""
        buf = io.BytesIO()
        Image.fromarray(a[:, :, :3].astype("uint8")).save(buf, format="PNG")
        return buf.getvalue()
    except (ImportError, OSError, TypeError, ValueError):
        logger.debug("image_table: could not encode a frame's picture",
                     exc_info=True)
        return b""


def _label_mark(chrome: dict, *, image: Rect, px_w: int, px_h: int,
                name: str) -> TextMark | None:
    """One corner label of a cell, placed from the burn-in's own geometry."""
    text = str(chrome.get("label_text") or "")
    if not text or px_w <= 0 or px_h <= 0 or image.w <= 0:
        return None
    in_per_px = image.w / float(px_w)
    font_px = label_font_px_for(px_h, int(chrome.get("label_font_px", 12) or 12))
    size_in = font_px * in_per_px
    margin_in = label_margin_px(px_h) * in_per_px
    pad_in = max(1, font_px // 4) * in_per_px
    # Sized to the run, not to the cell, so the translucent plate hugs the text
    # the way the burned-in one does instead of banding the whole field.
    text_w = _ADVANCE_FRACTION * size_in * len(text) + 2 * pad_in
    text_h = size_in * _LINE_FRACTION + 2 * pad_in
    corner = str(chrome.get("label_corner") or "tl").strip().lower()
    if corner in ("tr", "br"):
        x = image.x + image.w - margin_in - text_w
        align = "r"
    else:
        x = image.x + margin_in
        align = "l"
    if corner in ("bl", "br"):
        y = image.y + image.h - margin_in - text_h
    else:
        y = image.y + margin_in
    return TextMark(
        text=text, rect=Rect(x, y, text_w, text_h),
        size_pt=max(1.0, size_in * _PT_PER_IN),
        color=_hex(_chrome_rgb(chrome.get("label_color"))),
        align=align, anchor="ctr",
        plate=bool(chrome.get("label_bg", True)), name=name,
    )


def _bar_mark(chrome: dict, rgb, *, image: Rect, px_w: int, px_h: int) -> BarMark | None:
    """A cell's scale bar, at the length and place the picture would have drawn.

    ``None`` when the bar was refused — a bar too long for its field is not drawn
    at all, in the deck exactly as in the picture, because a bar shortened to fit
    states a physical scale that is not the one measured.
    """
    bar_px = int(chrome.get("bar_px") or 0)
    if bar_px <= 0 or px_w <= 0 or px_h <= 0 or image.w <= 0:
        return None
    rect_px = bar_rect_px(px_w, px_h, bar_px=bar_px,
                          bar_corner=chrome.get("bar_corner", "br"),
                          bar_thickness_px=int(chrome.get("bar_thickness_px", 0) or 0))
    if rect_px is None:
        return None
    x0, y0, x1, y1 = rect_px
    in_per_px = image.w / float(px_w)
    ink = bar_ink_rgb(rgb, rect_px, chrome.get("bar_color", "white"))
    halo = (0, 0, 0) if ink != (0, 0, 0) else (255, 255, 255)
    return BarMark(
        rect=Rect(image.x + x0 * in_per_px, image.y + y0 * in_per_px,
                  (x1 - x0) * in_per_px, (y1 - y0) * in_per_px),
        color=_hex(ink), halo=_hex(halo),
    )


@dataclass
class _Cell:
    """One recorded cell, before the grid's geometry is known."""

    slot: Any
    name: str
    rgb: Any
    png_rgb: Any
    chrome: dict | None
    mark: dict | None
    title: str
    title_h_in: float


class FrameCollector:
    """Records what each cell drew, then assembles the :class:`FramedGrid`.

    Recording is separate from assembly because the two want different moments:
    a cell's pixels are known as it is drawn, and its rectangle only once the
    whole figure has been laid out.
    """

    def __init__(self) -> None:
        self._cells: list[_Cell] = []
        #: The assembled grid, once :meth:`finish` has been called. ``None``
        #: until then — an empty grid and a grid never built are different
        #: things, and the caller has to be able to tell them apart.
        self.grid: FramedGrid | None = None

    def record(self, slot, *, name: str, rgb, chrome: dict | None,
               mark: dict | None, png_rgb=None, title: str = "",
               title_h_in: float = 0.0) -> None:
        """Note one cell: its gridspec slot, its pixels, and its overlays.

        *rgb* is the cell at source resolution and **before**
        ``apply_cell_chrome`` burned anything into it — the whole point of the
        frame is that its overlays are still text. Every mark's geometry is
        measured against it, and the scale bar's automatic colour is sampled
        from it, so the deck's bar is the length and the tone the picture's
        would have been.

        *png_rgb* is what actually goes into the frame as a picture — the same
        image, pooled down to a size a slide can carry. Defaults to *rgb*.
        Keeping the two apart matters: a mark measured against a downsampled
        array picks up that array's rounding, so a bar would come out a
        different length in the deck than in the figure beside it.

        *chrome* is ``cell_chrome_kwargs``' output and *mark* is
        ``uncorrected_mark_kwargs``', either of which may be ``None``.
        """
        self._cells.append(_Cell(slot=slot, name=name, rgb=rgb,
                                 png_rgb=rgb if png_rgb is None else png_rgb,
                                 chrome=chrome, mark=mark, title=str(title or ""),
                                 title_h_in=float(title_h_in)))

    def finish(self, fig, gs, plan) -> FramedGrid:
        """Assemble the grid and keep it on :attr:`grid` for the caller.

        The figure builder owns the gridspec and the facet plan, and it is the
        only place that has both, so it is what closes the collector.
        """
        self.grid = self.build(fig, gs, plan)
        return self.grid

    def build(self, fig, gs, plan) -> FramedGrid:
        """The finished grid, measured against the laid-out figure."""
        try:
            fig_w, fig_h = (float(v) for v in fig.get_size_inches())
        except (AttributeError, TypeError, ValueError):
            logger.debug("image_table: no figure size for the frame plan",
                         exc_info=True)
            return FramedGrid()
        frames = [f for f in (self._frame(cell, gs, fig) for cell in self._cells)
                  if f is not None]
        labels = self._labels(gs, fig, plan) if plan is not None else ()
        return FramedGrid(width_in=fig_w, height_in=fig_h,
                          frames=tuple(frames), labels=labels)

    # ── one field ────────────────────────────────────────────────────────────
    def _frame(self, cell: _Cell, gs, fig) -> ImageFrame | None:
        slot = _slot_rect(gs, cell.slot, fig)
        if slot is None:
            return None
        texts: list[TextMark] = []
        body = slot
        if cell.title and cell.title_h_in > 0:
            # The per-axes title sits in the strip the cell height reserves for
            # it, and the picture takes what is left — the same split the figure
            # makes, so the two surfaces stack the same way.
            strip = min(cell.title_h_in, slot.h)
            texts.append(TextMark(
                text=cell.title, rect=Rect(slot.x, slot.y, slot.w, strip),
                size_pt=max(1.0, strip / _LINE_FRACTION * _PT_PER_IN),
                color="1A1D24", align="ctr", anchor="ctr", name="Field title"))
            body = Rect(slot.x, slot.y + strip, slot.w, slot.h - strip)

        px_h, px_w = _pixel_size(cell.rgb)
        if px_w and px_h:
            image = _fit(px_w / px_h, body.x, body.y, body.w, body.h)
        else:
            image = body
        bars: list[BarMark] = []
        for chrome, name in ((cell.chrome, "Field label"),
                             (cell.mark, "Uncorrected mark")):
            if not chrome:
                continue
            label = _label_mark(chrome, image=image, px_w=px_w, px_h=px_h,
                                name=name)
            if label is not None:
                texts.append(label)
            bar = _bar_mark(chrome, cell.rgb, image=image, px_w=px_w, px_h=px_h)
            if bar is not None:
                bars.append(bar)
        return ImageFrame(name=cell.name or "Field", rect=image,
                          png=_encode_png(cell.png_rgb), texts=tuple(texts),
                          bars=tuple(bars))

    # ── the axis labels around the fields ────────────────────────────────────
    @staticmethod
    def _labels(gs, fig, plan) -> tuple[TextMark, ...]:
        marks: list[TextMark] = []
        for spec in _renderer.facet_label_specs(plan):
            gr, gc, rs, cs = spec.slot
            rect = _slot_rect(gs, (slice(gr, gr + rs), slice(gc, gc + cs)), fig)
            if rect is None:
                continue
            marks.append(TextMark(
                text=spec.text, rect=rect, size_pt=max(1.0, float(spec.pt)),
                color=str(spec.color).lstrip("#").upper(),
                align="ctr", anchor="b" if spec.va == "bottom" else "ctr",
                bold=spec.weight != "normal", vertical=bool(spec.rotation),
                name="Axis label"))
        return tuple(marks)


def _pixel_size(rgb) -> tuple[int, int]:
    """``(height, width)`` of a cell's array, or ``(0, 0)`` when it has none."""
    try:
        shape = rgb.shape
    except AttributeError:
        return 0, 0
    if len(shape) < 2:
        return 0, 0
    return int(shape[0]), int(shape[1])
