"""Assemble a PowerPoint deck from saved plots — one editable figure per slide.

The counterpart to :mod:`._report`, which writes the same gallery as a PDF. The
difference is what lands on the page: the PDF embeds each plot's captured raster
snapshot, while this rebuilds the figure out of PowerPoint shapes. A bar arrives
as a rectangle whose fill PowerPoint can change, an axis label as a text box the
user can retype, a marker as a curve — so a figure can be restyled for a talk or
a thesis without going back to the app or to Illustrator.

That is only possible for a plot whose **vector capture** was stored alongside
its PNG (``SavedPlot.svg_filename``). A record saved before the app captured
vector — or one whose capture failed — has only the raster snapshot, and this
places that instead: the plot is still in the deck, at full quality, but it is a
picture. Those two outcomes are never allowed to look alike. The slide says which
it is, and :class:`DeckResult` counts both so the caller can say so too.

Pure builder — no Qt, no app, no dataset — so the whole thing is unit-testable
from bytes on disk.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import theme
from well_viewer.features.saved_plots._align import (
    AnchorBox, axes_rect_points, plan_anchor,
)
from well_viewer.domain.image_frames import (
    FramedGrid, FramedGridError, Rect,
)
from well_viewer.domain.image_frames import from_bytes as framed_grid_from_bytes
from well_viewer.features.saved_plots._drawingml import (
    EMU_PER_INCH, Placement, drawing_group_xml, filled_rect_xml, group_xml,
    inches_to_emu, picture_xml, text_box_xml,
)
from well_viewer.features.saved_plots._pptx import (
    SLIDE_HEIGHT_EMU, SLIDE_WIDTH_EMU, PptxBuilder, Slide,
)
from well_viewer.features.saved_plots._svg_shapes import SvgParseError, parse_svg
from widgets.fig_clipboard import axes_fraction_from_svg, rasterized_layer_count

_logger = logging.getLogger("well_viewer")

# ── slide geometry, in inches ────────────────────────────────────────────────
# Derived from the deck's own slide size rather than restated, so changing the
# slide dimensions moves the layout with them instead of leaving every block
# measured against a page that is no longer that size.
_SLIDE_W = SLIDE_WIDTH_EMU / EMU_PER_INCH
_SLIDE_H = SLIDE_HEIGHT_EMU / EMU_PER_INCH

_MARGIN = 0.5
_TITLE_TOP = 0.34
_TITLE_H = 0.5
_META_H = 0.26
_BODY_TOP = 1.32
_BODY_H = _SLIDE_H - _BODY_TOP - _MARGIN
#: The parameter column beside the figure. Wide enough for the summary's longest
#: routine line ("Wells (12): A01, A02, …") at 9pt without wrapping mid-token.
_PARAMS_W = 3.5
_GUTTER = 0.25

#: The most shapes one slide may carry before the figure is placed as a picture
#: instead — a backstop, not a quality setting. ``fig_clipboard.MAX_VECTOR_PATHS``
#: bounds the capture itself by rasterizing dense layers well below this, so a
#: figure reaching this number got here some other way: an SVG written by an
#: older build, or one edited by hand in the dataset folder.
#:
#: Set where a deck still opens rather than where it merely builds: 40,000 shapes
#: converts in 46 seconds, 100,000 in over two and a half minutes, and 249,000
#: was killed out of memory. Past it the slide takes the picture and names the
#: count, because "too complex" gives the user nothing to act on while
#: "180,000 shapes" tells them what to change.
MAX_SHAPES_PER_SLIDE = 50_000

_TITLE_PT = 22.0
_META_PT = 11.0
_PARAMS_PT = 8.5
_NOTE_PT = 9.5
#: Height of one note line, and the step between stacked notes.
_NOTE_H = 0.28


@dataclass
class DeckResult:
    """What the export actually produced.

    ``vector`` and ``raster`` are the two ways a plot can reach a slide, and they
    are counted separately because they are not the same deliverable: one is
    editable and one is a picture. ``unsupported`` names any SVG construct the
    reader met and could not translate, once each.
    """

    slides: int = 0
    vector: int = 0
    raster: int = 0
    #: Image Table grids placed as one group per field of view — the picture of
    #: each field plus its overlays as editable text and a scale-bar rectangle.
    #: A subset of ``vector``: the slide is made of shapes, and the shape at the
    #: centre of each frame is a photograph, which is what a field of view is.
    framed: int = 0
    #: Plots whose figure is editable but whose data layer had to be rasterized.
    #: A subset of ``vector``, counted apart for the same reason ``redrawn`` is:
    #: they carry a caveat the others do not.
    rasterized: int = 0
    #: Plots whose editable figure was redrawn from saved parameters rather than
    #: captured at save time. A subset of ``vector`` — they are fully editable —
    #: counted apart because they carry a caveat the others do not.
    redrawn: int = 0
    #: Plots whose slide carries no figure at all — neither capture could be
    #: read. Counted separately and not folded into ``raster``, because it is a
    #: third outcome: the slide exists and names the plot, and the figure is
    #: gone. Left uncounted (which it was) a deck that dropped a figure
    #: reported itself as fully editable.
    missing: int = 0
    unsupported: list[str] = field(default_factory=list)

    def note_unsupported(self, items: Iterable[str]) -> None:
        for item in items:
            if item not in self.unsupported:
                self.unsupported.append(item)

    def summary(self) -> str:
        """A one-line status naming every outcome that occurred.

        Counts only. The advice belongs on the slide, where the cause is known:
        "re-save it" is right for a plot that never had a vector capture and
        useless for one past the shape cap or for an Image Table grid, and the
        status line cannot tell which of the three it is looking at.
        """
        plots = self.vector + self.raster + self.missing
        text = f"{plots} plot{'' if plots == 1 else 's'}"
        if self.raster or self.missing:
            parts = [f"{self.vector} editable"]
            if self.raster:
                parts.append(f"{self.raster} as a picture")
            if self.missing:
                parts.append(f"{self.missing} with no figure")
            text += " — " + ", ".join(parts)
        elif not (self.unsupported or self.rasterized or self.framed):
            # "all editable" is a claim about every mark in every figure, so it
            # cannot be said over a lost construct or a data layer that became a
            # picture. Both were reported *after* it and read as qualifications
            # of a sentence that had already overstated the case. Nor over a
            # framed image grid: its labels and bars are shapes and its fields
            # are photographs, which the frame clause below says in full.
            text += ", all editable"
        # Never behind an early return: a deck can be entirely editable and
        # still have lost a hatch or an arc out of one of its figures, and
        # "all editable" said over the top of that is the shortfall being
        # hidden by the very line meant to describe it.
        if self.framed:
            text += (f"; {self.framed} image grid"
                     f"{'' if self.framed == 1 else 's'} as one group per field"
                     " (labels and scale bars editable)")
        if self.redrawn:
            text += (f"; {self.redrawn} redrawn from saved settings"
                     " (older plots, now editable)")
        if self.rasterized:
            text += (f"; {self.rasterized} with a picture for the data layer"
                     " (too dense to redraw mark by mark)")
        if self.unsupported:
            text += f" (not translated: {', '.join(self.unsupported)})"
        return text


def _png_size(png: bytes) -> tuple[int, int] | None:
    """A PNG's pixel dimensions, read from its IHDR chunk.

    Straight from the header rather than through Pillow: this module is meant to
    stay importable and testable without the imaging stack, and the eight bytes
    that carry the size sit at a fixed offset in every PNG.
    """
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        width, height = struct.unpack(">II", png[16:24])
    except struct.error:
        return None
    return (width, height) if width and height else None


def _fit_box(aspect: float, *, x: float, y: float, w: float, h: float) -> tuple:
    """Letterbox a picture of ratio *aspect* (width/height) into a box, in EMU."""
    if aspect <= 0:
        aspect = w / h if h else 1.0
    box_aspect = w / h if h else aspect
    if aspect >= box_aspect:
        used_w, used_h = w, w / aspect
    else:
        used_h, used_w = h, h * aspect
    return (inches_to_emu(x + (w - used_w) / 2.0),
            inches_to_emu(y + (h - used_h) / 2.0),
            inches_to_emu(used_w), inches_to_emu(used_h))


def _hex(color: str) -> str:
    """A theme colour as the six bare hex digits DrawingML wants.

    Every caller passes a ``theme.CPub`` token, so there is no default here. A
    colour that did not arrive is a wiring mistake in this module rather than a
    condition the export can meet, and quietly substituting black would put text
    on a slide in a colour nobody picked — indistinguishable, on the slide, from
    a colour that was chosen.
    """
    digits = (color or "").lstrip("#").upper()
    if len(digits) != 6:
        raise ValueError(f"not a six-digit hex colour: {color!r}")
    return digits


def _cover_slide(builder: PptxBuilder, *, dataset_name: str, count: int) -> None:
    slide = builder.new_slide()
    slide.add(text_box_xml(
        "Saved Plots", slide.ids,
        x=inches_to_emu(_MARGIN), y=inches_to_emu(2.4),
        cx=SLIDE_WIDTH_EMU - 2 * inches_to_emu(_MARGIN), cy=inches_to_emu(1.1),
        size_pt=40.0, bold=True, color=_hex(theme.CPub.text), align="ctr",
        name="Cover title"))
    lines = []
    if dataset_name:
        lines.append(dataset_name)
    lines.append(f"{count} saved plot{'' if count == 1 else 's'}")
    lines.append("Generated " + datetime.now().strftime("%Y-%m-%d %H:%M"))
    slide.add(text_box_xml(
        "\n".join(lines), slide.ids,
        x=inches_to_emu(_MARGIN), y=inches_to_emu(3.7),
        cx=SLIDE_WIDTH_EMU - 2 * inches_to_emu(_MARGIN), cy=inches_to_emu(1.6),
        size_pt=15.0, color=_hex(theme.CPub.text_muted), align="ctr",
        name="Cover detail"))


def _figure_width(summary: str) -> float:
    """The width in inches a slide leaves for its figure.

    The full slide less the parameter column when there is a summary to show and
    the full slide when there is not — a plot with no recorded parameters should
    not leave a third of the slide blank. Answered on its own, without a slide,
    because the deck has to know every slide's figure width before it places the
    first one: the anchor box they share can only be as wide as the narrowest of
    them.
    """
    content_w = _SLIDE_W - 2 * _MARGIN
    return content_w - _PARAMS_W - _GUTTER if summary else content_w


def _slide_chrome(slide: Slide, rec, *, summary: str) -> float:
    """Title, tab badge, timestamp and the parameter column.

    Returns the width in inches left for the figure — :func:`_figure_width`.
    """
    content_w = _SLIDE_W - 2 * _MARGIN
    title = (getattr(rec, "title", "") or getattr(rec, "tab", "")
             or "Saved plot")
    tab = getattr(rec, "tab", "") or ""
    slide.add(text_box_xml(
        title, slide.ids,
        x=inches_to_emu(_MARGIN), y=inches_to_emu(_TITLE_TOP),
        cx=inches_to_emu(content_w - 3.0), cy=inches_to_emu(_TITLE_H),
        size_pt=_TITLE_PT, bold=True, color=_hex(theme.CPub.text),
        name="Plot title"))
    if tab:
        slide.add(text_box_xml(
            tab, slide.ids,
            x=inches_to_emu(_MARGIN + content_w - 3.0),
            y=inches_to_emu(_TITLE_TOP + 0.1),
            cx=inches_to_emu(3.0), cy=inches_to_emu(_META_H),
            size_pt=_META_PT, color=_hex(theme.CPub.text_muted), align="r",
            name="Source tab"))
    created = (getattr(rec, "created_at", "") or "").replace("T", "  ")
    if created:
        slide.add(text_box_xml(
            created, slide.ids,
            x=inches_to_emu(_MARGIN), y=inches_to_emu(_TITLE_TOP + _TITLE_H),
            cx=inches_to_emu(content_w - 3.0), cy=inches_to_emu(_META_H),
            size_pt=_META_PT, color=_hex(theme.CPub.text_muted),
            name="Saved at"))
    if summary:
        slide.add(text_box_xml(
            summary, slide.ids,
            x=inches_to_emu(_MARGIN + content_w - _PARAMS_W),
            y=inches_to_emu(_BODY_TOP),
            cx=inches_to_emu(_PARAMS_W), cy=inches_to_emu(_BODY_H),
            size_pt=_PARAMS_PT, color=_hex(theme.CPub.text_muted),
            monospace=True, name="Parameters"))
    return _figure_width(summary)


def _note(slide: Slide, text: str, *, figure_w: float) -> None:
    """A line under the figure saying what the slide is not.

    Only ever written for a shortfall — a plot placed as a picture, a figure
    redrawn rather than captured, a construct that could not be translated. A
    slide carrying none of those says nothing.

    **Stacked**, because a slide can have more than one: a regenerated figure
    that also lost a hatch earns two. Both used to be written at the same fixed
    y and printed on top of each other, so the slide with the most to say was the
    one that said none of it — the shortfall hidden by the very line meant to
    describe it, in geometry rather than in prose.
    """
    index = slide.note_count
    slide.note_count += 1
    bottom = _SLIDE_H - _MARGIN - _NOTE_H * (index + 1)
    slide.add(text_box_xml(
        text, slide.ids,
        x=inches_to_emu(_MARGIN), y=inches_to_emu(bottom),
        cx=inches_to_emu(figure_w), cy=inches_to_emu(_NOTE_H),
        size_pt=_NOTE_PT, color=_hex(theme.CPub.text_faint),
        name=f"Export note {index + 1}"))


def _place_vector(slide: Slide, svg_bytes: bytes, *, figure_w: float,
                  result: DeckResult, anchor: AnchorBox | None = None) -> str:
    """Draw the figure as PowerPoint shapes.

    Returns ``""`` on success, or a sentence saying why the figure could not be
    drawn as shapes — which the caller puts on the slide after falling back to
    the picture, so the reason travels with the artifact and not only with a log
    line nobody will read.

    *anchor* is the box every figure in this deck puts its plotting area in, so
    the slides can be laid beside one another. A capture that does not record
    where its plotting area is cannot be anchored: it falls back to filling the
    body of the slide, and the slide says so, because a figure that is a little
    off is otherwise indistinguishable from one that lines up.
    """
    try:
        drawing = parse_svg(svg_bytes)
    except SvgParseError as exc:
        _logger.debug("saved_plots: vector capture unreadable: %s", exc)
        return ("The vector capture for this plot could not be read, so it is "
                "placed here as a picture.")
    if not drawing.shapes:
        return ("The vector capture for this plot held nothing to draw, so it "
                "is placed here as a picture.")
    # Recorded before the cap decides anything: a capped figure met the same
    # constructs a drawn one would have, and discarding its findings meant the
    # one plot most likely to be complicated reported nothing.
    result.note_unsupported(drawing.unsupported)
    layers = rasterized_layer_count(svg_bytes)
    if layers:
        # The figure is editable and its data layer is not. Said on the slide,
        # because an embedded image is what a heat map legitimately contains and
        # nothing else distinguishes the two.
        result.rasterized += 1
        _note(slide, "The data layer of this figure was too dense to redraw mark "
                     "by mark, so it is a picture; the axes, labels, title and "
                     "legend are editable shapes.", figure_w=figure_w)
    if len(drawing.shapes) > MAX_SHAPES_PER_SLIDE:
        return (f"This figure is {len(drawing.shapes):,} separate shapes — past "
                f"the {MAX_SHAPES_PER_SLIDE:,} a slide can carry and stay "
                "usable — so it is placed here as a picture.")
    frac = axes_fraction_from_svg(svg_bytes)
    if anchor is not None and frac is not None:
        placement = Placement.anchor(
            axes_rect_points(frac, drawing.width_pt, drawing.height_pt),
            x=anchor.x, y=anchor.y, cx=anchor.cx, cy=anchor.cy)
    else:
        placement = Placement.fit(
            drawing,
            x=inches_to_emu(_MARGIN), y=inches_to_emu(_BODY_TOP),
            cx=inches_to_emu(figure_w), cy=inches_to_emu(_BODY_H))
        if anchor is not None:
            _note(slide, "This figure's capture does not record where its axes "
                         "sit, so it is fitted to the slide rather than lined up "
                         "with the others. Re-save the plot in All-Well to have "
                         "it match.", figure_w=figure_w)
    slide.add(drawing_group_xml(drawing, placement, slide.ids, slide.add_image))
    if drawing.unsupported:
        # The figure is on the slide and part of it is not. Said here rather
        # than only in a log line, because the log is not what gets presented.
        _note(slide, "Part of this figure could not be redrawn as shapes and is "
                     "missing from it: " + ", ".join(drawing.unsupported) + ".",
              figure_w=figure_w)
    return ""


class _GridFit:
    """Maps a frame plan's inches onto the slide's body box.

    The plan is measured in inches from its own top-left; the slide has a box of
    a different size in a different place. One scale for both axes, so a field
    stays the shape it was photographed in.
    """

    def __init__(self, grid: FramedGrid, *, x: float, y: float,
                 w: float, h: float) -> None:
        width = max(1e-6, float(grid.width_in))
        height = max(1e-6, float(grid.height_in))
        self.scale = min(w / width, h / height)
        self.x = x + (w - width * self.scale) / 2.0
        self.y = y + (h - height * self.scale) / 2.0

    def box(self, rect: Rect) -> tuple[int, int, int, int]:
        """One plan rectangle as ``(x, y, cx, cy)`` in EMU on the slide."""
        return (inches_to_emu(self.x + rect.x * self.scale),
                inches_to_emu(self.y + rect.y * self.scale),
                inches_to_emu(rect.w * self.scale),
                inches_to_emu(rect.h * self.scale))

    def points(self, size_pt: float) -> float:
        """A plan font size at the scale the grid is placed."""
        return max(1.0, float(size_pt) * self.scale)


def _bar_outline_emu() -> int:
    """The hairline a scale bar's contrasting outline is drawn at, in EMU.

    Three quarters of a point — thin enough to read as the halo the burned-in
    bar draws around itself rather than as a border of its own.
    """
    return int(round(0.75 * EMU_PER_INCH / 72.0))


def _text_mark_xml(slide: Slide, mark, fit: _GridFit) -> str:
    x, y, cx, cy = fit.box(mark.rect)
    return text_box_xml(
        mark.text, slide.ids, x=x, y=y, cx=cx, cy=cy,
        color=_hex(mark.color), size_pt=fit.points(mark.size_pt),
        bold=bool(mark.bold), align=mark.align, anchor=mark.anchor,
        # The plate's colour and opacity come from the mark: a label's is a
        # translucent dark backing and a facet group strip's is a solid pale
        # band, and one constant here would have to be wrong for one of them.
        plate=mark.plate_color if mark.plate else "",
        plate_alpha=mark.plate_alpha,
        vertical=bool(mark.vertical), name=mark.name)


def _frame_xml(slide: Slide, frame, fit: _GridFit) -> str:
    """One field of view as a PowerPoint group: picture, labels, scale bar.

    A group rather than loose shapes so the field moves, resizes and copies as a
    unit — and ungroups, in one click, into the picture and the text a user came
    here to edit.

    ``""`` only for a frame carrying nothing at all, which the collector no
    longer produces: a field whose picture could not be read is given the
    figure's own ``(no image)`` placeholder, so it keeps its group, its place
    and its labels. Dropping it would close the gap in the grid and leave
    nothing to say a field is missing.
    """
    body = []
    x, y, cx, cy = fit.box(frame.rect)
    rel_id = slide.add_image(frame.png) if frame.png else ""
    if rel_id:
        body.append(picture_xml(slide.ids, rel_id, x=x, y=y, cx=cx, cy=cy,
                                name=f"{frame.name} image"))
    for bar in frame.bars:
        bx, by, bcx, bcy = fit.box(bar.rect)
        body.append(filled_rect_xml(
            slide.ids, x=bx, y=by, cx=bcx, cy=bcy, color=_hex(bar.color),
            outline=_hex(bar.halo), outline_w=_bar_outline_emu(),
            name=bar.name))
    for mark in frame.texts:
        body.append(_text_mark_xml(slide, mark, fit))
    if not body:
        return ""
    return group_xml("".join(body), slide.ids, x=x, y=y, cx=cx, cy=cy,
                     name=frame.name)


def _place_frames(slide: Slide, grid: FramedGrid, *, figure_w: float) -> bool:
    """Draw an Image Table grid as one group per field. False if it held nothing.

    The axis labels are placed at the top level rather than inside any field's
    group, because they name a row or a column: a user dragging one field on the
    slide should not take the row header with it.
    """
    if grid.is_empty:
        return False
    fit = _GridFit(grid, x=_MARGIN, y=_BODY_TOP, w=figure_w, h=_BODY_H - 0.34)
    placed = 0
    for frame in grid.frames:
        body = _frame_xml(slide, frame, fit)
        if body:
            slide.add(body)
            placed += 1
    for mark in grid.labels:
        slide.add(_text_mark_xml(slide, mark, fit))
    return bool(placed or grid.labels)


def _place_raster(slide: Slide, png: bytes, *, figure_w: float) -> bool:
    """Place the captured snapshot as a picture. False if there was no image."""
    if not png:
        return False
    size = _png_size(png)
    aspect = (size[0] / size[1]) if size else 1.0
    x, y, cx, cy = _fit_box(aspect, x=_MARGIN, y=_BODY_TOP, w=figure_w,
                            h=_BODY_H - 0.34)
    rel_id = slide.add_image(png)
    if not rel_id:
        return False
    slide.add(
        "<p:pic><p:nvPicPr>"
        f'<p:cNvPr id="{slide.ids.take()}" name="Saved plot image"/>'
        "<p:cNvPicPr/><p:nvPr/></p:nvPicPr>"
        f'<p:blipFill><a:blip r:embed="{rel_id}"/>'
        "<a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')
    return True


def build_image_grid_deck(grid: FramedGrid, out_path: str | Path, *,
                          title: str = "Image grid") -> int:
    """Write a one-slide deck holding *grid* as one group per field of view.

    The Image Table's own "export to PowerPoint", as against
    :func:`build_saved_plots_deck`, which writes the whole gallery. Same slide
    geometry and the same per-field groups — one placement routine serves both,
    so a grid exported directly and the same grid exported from the gallery are
    the same slide.

    Returns the number of fields whose picture reached the slide. Raises only on
    I/O failure writing the file.
    """
    builder = PptxBuilder(title=title)
    slide = builder.new_slide()
    slide.add(text_box_xml(
        title, slide.ids,
        x=inches_to_emu(_MARGIN), y=inches_to_emu(_TITLE_TOP),
        cx=SLIDE_WIDTH_EMU - 2 * inches_to_emu(_MARGIN),
        cy=inches_to_emu(_TITLE_H),
        size_pt=_TITLE_PT, bold=True, color=_hex(theme.CPub.text),
        name="Grid title"))
    figure_w = _SLIDE_W - 2 * _MARGIN
    _place_frames(slide, grid, figure_w=figure_w)
    missing = sum(1 for f in grid.frames if not f.png)
    if missing:
        _note(slide, f"{missing} field{'' if missing == 1 else 's'} could not be "
                     "read; each keeps its place and its labels and is marked "
                     "\u201c(no image)\u201d.", figure_w=figure_w)
    builder.write(out_path)
    return sum(1 for f in grid.frames if f.png)


def _read(source) -> bytes:
    """The bytes behind a capture, which may be a path or the bytes themselves.

    A capture rendered this session but not writable to disk is held in memory
    (``SavedPlotsStore.hold_vector``), and it is worth just as much to this
    export as one that reached the dataset folder.
    """
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    path = source
    if path is None:
        return b""
    try:
        candidate = Path(path)
        return candidate.read_bytes() if candidate.exists() else b""
    except OSError:
        return b""


def _plan_deck_anchor(recs, svg_path_for, summary_for) -> AnchorBox | None:
    """The box every figure in this deck puts its plotting area in.

    A pass over the captures before any slide is built, because the box is a
    property of the whole deck: it has to be small enough that the widest legend
    and the tallest title in the gallery still fit beside their own axes, and it
    has to be the same on every slide or there was no point computing it. That
    also means it is sized against the **narrowest** figure area any slide will
    offer — a plot with no parameters to show is given the whole slide width, and
    an anchor that used that width would push a neighbouring slide's figure under
    its parameter column.

    Only the recorded axes rectangle is read here, not the figure: a marker is a
    regular expression over the bytes, while parsing one of these captures into
    shapes is the expensive half of the whole export.

    ``None`` when no plot in the deck records one, which leaves every figure
    letterboxed into its slide exactly as before and says nothing about it —
    there is no alignment to fall short of.
    """
    fractions = []
    widths = []
    for rec in recs:
        svg_bytes = _read(svg_path_for(rec))
        if not svg_bytes:
            continue
        frac = axes_fraction_from_svg(svg_bytes)
        if frac is None:
            continue
        fractions.append(frac)
        widths.append(_figure_width(summary_for(rec)))
    if not fractions:
        return None
    return plan_anchor(fractions,
                       x=inches_to_emu(_MARGIN), y=inches_to_emu(_BODY_TOP),
                       cx=inches_to_emu(min(widths)), cy=inches_to_emu(_BODY_H))


def _try_frames(slide: Slide, rec, frames_path_for, *, figure_w: float,
                result: DeckResult) -> bool:
    """Place *rec* as per-field frames when it stored a frame plan.

    False when there is no plan, or the plan cannot be read — in which case the
    caller falls through to the vector capture and then to the picture, exactly
    as for a record that never had one. An unreadable plan is logged rather than
    raised: the deck is still worth building, and the slide the user gets is the
    one they would have had before.
    """
    if frames_path_for is None:
        return False
    data = _read(frames_path_for(rec))
    if not data:
        return False
    try:
        grid = framed_grid_from_bytes(data)
    except FramedGridError as exc:
        _logger.debug("saved_plots: frame plan unreadable: %s", exc)
        return False
    if not grid.has_pictures:
        # Every field failed to encode. The flat snapshot is what the user
        # actually saw, so placing a page of empty boxes and calling it editable
        # would trade a picture of their grid for a picture of nothing.
        _logger.debug("saved_plots: frame plan holds no field pictures")
        return False
    if not _place_frames(slide, grid, figure_w=figure_w):
        return False
    result.vector += 1
    result.framed += 1
    missing = sum(1 for f in grid.frames if not f.png)
    if missing:
        # A field whose picture could not be read leaves a group holding only
        # its labels. Said on the slide, because an empty frame among full ones
        # is otherwise indistinguishable from a field that was genuinely blank.
        _note(slide, f"{missing} field"
                     f"{'' if missing == 1 else 's'} on this slide could not be "
                     "read; each keeps its place and its labels and is marked "
                     "\u201c(no image)\u201d.", figure_w=figure_w)
    return True


def build_saved_plots_deck(
    records: Iterable,
    svg_path_for: Callable[[object], object],
    image_path_for: Callable[[object], object],
    out_path: str | Path,
    *,
    frames_path_for: Callable[[object], object] | None = None,
    dataset_name: str = "",
    summary_for: Callable[[object], str] | None = None,
    regenerated: set | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> DeckResult:
    """Write a ``.pptx`` with a cover slide and one slide per saved plot.

    An Image Table grid that stored a frame plan (*frames_path_for*) is placed
    first and differently: one PowerPoint group per field of view, holding that
    field's picture, its identity label and its scale bar as separate shapes. A
    grid is not a chart and has no vector capture to redraw — its fields are
    photographs — so this is the form in which it is editable at all: the labels
    can be retyped, the bars recoloured, and a field dragged out on its own.

    Every other plot is drawn from its vector capture as editable PowerPoint shapes.
    Where no readable capture exists the raster snapshot is placed instead and
    the slide says so; where neither exists the slide carries the plot's title
    and parameters and states that the figure is missing, which is the one
    outcome a silent export would have made indistinguishable from a blank plot.

    *regenerated* names the plots whose vector capture was **redrawn from their
    saved parameters** rather than captured when the user saved them — the repair
    the export performs for a record filed before the app captured vector. Those
    slides say so, because a redrawn figure is drawn from today's data and
    today's gating and can differ from the image that was saved, in a direction
    nothing else on the slide would reveal.

    *on_progress* is called as ``(done, total, label)`` before each plot's slide
    is built, and once more before the file is written. A plain callable rather
    than anything Qt: this module stays importable and testable without a
    running application, and the caller is the one that knows whether there is a
    progress bar to move.

    Returns a :class:`DeckResult` — raises only on I/O failure writing the file.
    """
    recs = list(records)
    regenerated = regenerated or set()
    summary_for = summary_for or (lambda r: getattr(r, "summary", "") or "")
    builder = PptxBuilder(title="Saved Plots")
    result = DeckResult()

    anchor = _plan_deck_anchor(recs, svg_path_for, summary_for)
    _cover_slide(builder, dataset_name=dataset_name, count=len(recs))
    # One step per plot, plus the write at the end — which is not free for a
    # deck of large figures and is the step a user is most likely to be
    # watching, since it is the one that produces the file.
    total = len(recs) + 1
    for index, rec in enumerate(recs):
        if on_progress is not None:
            on_progress(index, total,
                        getattr(rec, "title", "") or getattr(rec, "tab", ""))
        slide = builder.new_slide()
        figure_w = _slide_chrome(slide, rec, summary=summary_for(rec))
        if _try_frames(slide, rec, frames_path_for, figure_w=figure_w,
                       result=result):
            continue
        svg_bytes = _read(svg_path_for(rec))
        if svg_bytes:
            reason = _place_vector(slide, svg_bytes, figure_w=figure_w,
                                   result=result, anchor=anchor)
            if not reason:
                result.vector += 1
                if getattr(rec, "id", None) in regenerated:
                    result.redrawn += 1
                    _note(slide, "This figure was redrawn from the plot's saved "
                                 "settings, because it was filed before All-Well "
                                 "captured vector. It is editable, and it reflects "
                                 "the current data rather than the image that was "
                                 "saved.", figure_w=figure_w)
                continue
        else:
            reason = ("This plot was saved without a vector capture, so it is "
                      "placed here as a picture. Re-save it in All-Well to "
                      "export it as editable shapes.")
        png = _read(image_path_for(rec))
        if _place_raster(slide, png, figure_w=figure_w):
            result.raster += 1
            _note(slide, reason, figure_w=figure_w)
            continue
        result.missing += 1
        _note(slide, "The saved image for this plot could not be read, so the "
                     "figure is missing from this slide.", figure_w=figure_w)

    if on_progress is not None:
        on_progress(len(recs), total, "")
    result.slides = builder.write(out_path)
    return result
