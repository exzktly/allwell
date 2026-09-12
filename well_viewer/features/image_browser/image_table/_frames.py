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

from ui import theme
from well_viewer.domain.image_frames import (
    BarMark, FramedGrid, ImageFrame, Rect, TextMark,
)
from well_viewer.features.image_browser.image_table import renderer as _renderer
from well_viewer.features.image_browser.image_table.image_compose import (
    _chrome_rgb, bar_ink_rgb, bar_rect_px, chrome_fits, label_font_px_for,
    label_margin_px,
)

logger = logging.getLogger(__name__)

_PT_PER_IN = 72.0

#: Mean glyph advance as a fraction of the font size. An independent estimate,
#: not a shared constant — ``_drawingml`` has its own for sizing a text frame,
#: and this module may not import it (Saved Plots is removable). Nothing holds
#: the two equal and nothing needs to: only the *box* depends on this, and the
#: run's own alignment does the positioning, so a box a few percent out still
#: puts the label on its corner. What it does size exactly is the translucent
#: plate, which is why it is estimated at all rather than left to autofit.
_ADVANCE_FRACTION = 0.62

#: How much taller than the font a single-line label's box is.
_LINE_FRACTION = 1.35

#: The translucent plate behind an identity label, matching the ``(0, 0, 0, 140)``
#: ``draw_cell_chrome`` fills — 140/255 of full opacity, in DrawingML's
#: hundred-thousandths.
PLATE_COLOR = "000000"
PLATE_ALPHA = int(round(140 / 255 * 100_000))

#: Inches reserved for a per-axes title when the caller records one without
#: saying how tall it is. Only a fallback — ``export.TITLE_STRIP_IN`` is the
#: number the figure and the plan actually share.
_TITLE_STRIP_IN = 0.2

#: What a cell the figure could not render draws, so the frame says the same.
_NO_IMAGE = "(no image)"


def _token(color: str) -> str:
    """A theme colour as the six bare hex digits a mark carries."""
    return str(color or "").lstrip("#").upper()


#: The exported figure's own ink, taken from the tokens rather than restated:
#: a title is `axes.titlecolor` in publication mode and a placeholder is the
#: colour the figure writes "(no image)" in. A literal here is a second copy of
#: a token that then drifts (CLAUDE.md §5).
_TITLE_COLOR = _token(theme.CPub.text)
_PLACEHOLDER_COLOR = _token(theme.CPub.placeholder)
_BAND_COLOR = _token(theme.CPub.band_face)


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


def _title_mark(cell: _Cell, image: Rect) -> TextMark:
    """The per-axes title, in the room the figure leaves above the picture.

    Enlarging the cell by :data:`export.TITLE_STRIP_IN` does not put that whole
    strip above the picture: ``imshow(aspect="equal")`` centres the axes in the
    taller slot, so the room above is what is left between the picture's top
    edge and the top of the grid — measured at half the strip for a square
    field. Reserving the full strip put the title off the top of the page, on a
    plan that was internally consistent and therefore read as correct.

    The size is the figure's own ``title_fontsize`` rather than derived from
    that room, so the two surfaces render the same words at the same size.

    A picture with nothing above it (one that fills its slot's height exactly)
    gets its title across the top of the picture instead of losing it — a title
    that silently disappears from some rows is the shortfall this codebase keeps
    being bitten by.
    """
    strip = max(cell.title_h_in, 0.0) or _TITLE_STRIP_IN
    top = max(0.0, image.y - strip)
    room = image.y - top
    rect = (Rect(image.x, top, image.w, room) if room > 1e-6
            else Rect(image.x, image.y, image.w, min(strip, image.h)))
    return TextMark(
        text=cell.title, rect=rect, size_pt=max(1.0, float(cell.title_pt)),
        color=_TITLE_COLOR, align="ctr", anchor="b", name="Field title")


def _union(image: Rect, marks) -> Rect:
    """The box enclosing the picture and every mark placed beside it.

    The frame's rectangle becomes the PowerPoint group's own extent, so a mark
    outside it — a per-axes title, which sits *above* the picture — would be
    drawn but left out of the group's bounding box, its selection rectangle and
    its resize scaling.
    """
    x0, y0 = image.x, image.y
    x1, y1 = image.x + image.w, image.y + image.h
    for mark in marks:
        x0 = min(x0, mark.rect.x)
        y0 = min(y0, mark.rect.y)
        x1 = max(x1, mark.rect.x + mark.rect.w)
        y1 = max(y1, mark.rect.y + mark.rect.h)
    return Rect(x0, y0, x1 - x0, y1 - y0)


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
    # Clamped into the field, the way ``draw_cell_chrome``'s ``max(0, …)`` /
    # ``min(W, …)`` clamp the burned-in box. A long well label at a right-hand
    # corner is offset left by its own estimated width, which without this puts
    # the text outside the picture it names and outside the group around it.
    text_w = min(text_w, image.w)
    text_h = min(text_h, image.h)
    if corner in ("tr", "br"):
        x = max(image.x, image.x + image.w - margin_in - text_w)
        align = "r"
    else:
        x = min(image.x + margin_in, image.x + image.w - text_w)
        align = "l"
    if corner in ("bl", "br"):
        y = max(image.y, image.y + image.h - margin_in - text_h)
    else:
        y = min(image.y + margin_in, image.y + image.h - text_h)
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
    title_pt: float
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
               title_pt: float = 7.0, title_h_in: float = 0.0) -> None:
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
                                 title_pt=float(title_pt),
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
        px_h, px_w = _pixel_size(cell.rgb)
        # ``imshow(aspect="equal")`` centres the axes in the **whole** slot, and
        # ``set_title`` then draws above that axes rather than taking a strip
        # out of the slot first. Measured against the figure: carving the strip
        # out first put every picture 0.1 in low and the title box 0.1 in high
        # of where the figure drew them, on a plan that was internally
        # consistent and therefore looked right.
        if px_w and px_h:
            image = _fit(px_w / px_h, slot.x, slot.y, slot.w, slot.h)
        else:
            image = slot
        if cell.title:
            texts.append(_title_mark(cell, image))
        bars: list[BarMark] = []
        # ``chrome_fits`` is the same predicate ``draw_cell_chrome`` returns
        # early on. Without it a field the picture could take no chrome into —
        # a 200x3 crop, an array that is not three-channel — would get its label
        # and its bar as shapes anyway, so the deck would show a scale the
        # picture refused and the status line reported as refused. A wrong
        # number on a slide, indistinguishable from a right one (CLAUDE.md §7).
        marked = chrome_fits(cell.rgb)
        for chrome, name in ((cell.chrome, "Field label"),
                             (cell.mark, "Uncorrected mark")):
            if not chrome or not marked:
                continue
            label = _label_mark(chrome, image=image, px_w=px_w, px_h=px_h,
                                name=name)
            if label is not None:
                texts.append(label)
            bar = _bar_mark(chrome, cell.rgb, image=image, px_w=px_w, px_h=px_h)
            if bar is not None:
                bars.append(bar)
        png = _encode_png(cell.png_rgb)
        if not png:
            # The same placeholder the figure draws into a cell it could not
            # render. Without it a field with nothing to say produced no shapes
            # at all and was dropped from the slide, leaving a hole where the
            # grid has a cell — while both "missing field" notes claimed such
            # fields are placed carrying their labels.
            texts.append(TextMark(
                text=_NO_IMAGE, rect=image,
                size_pt=max(1.0, min(image.h, image.w) * 0.12 * _PT_PER_IN),
                color=_PLACEHOLDER_COLOR, align="ctr", anchor="ctr",
                name="No image"))
        return ImageFrame(name=cell.name or "Field", rect=_union(image, texts),
                          png=png, texts=tuple(texts), bars=tuple(bars))

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
                color=_token(spec.color),
                align="ctr", anchor="b" if spec.va == "bottom" else "ctr",
                bold=spec.weight != "normal", vertical=bool(spec.rotation),
                # A group strip is drawn on a filled band in the figure, and
                # ``FacetLabel.band`` exists so both surfaces read one
                # description of the label hierarchy. Carried, not dropped —
                # dropping it is the exact drift the descriptor was added to
                # prevent, and it flattened the grouping the band expresses.
                plate=bool(spec.band), plate_color=_BAND_COLOR,
                plate_alpha=100_000,
                name="Group band" if spec.band else "Axis label"))
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
