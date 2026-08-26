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
from well_viewer.features.saved_plots._drawingml import (
    EMU_PER_INCH, Placement, drawing_group_xml, inches_to_emu, text_box_xml,
)
from well_viewer.features.saved_plots._pptx import (
    SLIDE_HEIGHT_EMU, SLIDE_WIDTH_EMU, PptxBuilder, Slide,
)
from well_viewer.features.saved_plots._svg_shapes import SvgParseError, parse_svg

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
#: instead. A scatter of a hundred thousand cells is a hundred thousand
#: PowerPoint shapes, and a deck like that does not open — it is not slow, it
#: hangs. The cap is generous for a plot anyone would put in a talk (the bar and
#: scatter figures this was built against land between forty and a few hundred)
#: and the slide says outright when it bites, so a picture is never mistaken for
#: a figure that simply had nothing editable in it.
MAX_SHAPES_PER_SLIDE = 4000

_TITLE_PT = 22.0
_META_PT = 11.0
_PARAMS_PT = 8.5
_NOTE_PT = 9.5


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
    unsupported: list[str] = field(default_factory=list)

    def note_unsupported(self, items: Iterable[str]) -> None:
        for item in items:
            if item not in self.unsupported:
                self.unsupported.append(item)

    def summary(self) -> str:
        """A one-line status, naming a raster fallback whenever there was one."""
        plots = self.vector + self.raster
        text = f"{plots} plot{'' if plots == 1 else 's'}"
        if self.raster:
            text += (f" — {self.vector} editable, {self.raster} as a picture"
                     " (re-save those plots to make them editable)")
        else:
            text += ", all editable"
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
    """A theme colour as the six bare hex digits DrawingML wants."""
    return (color or "#000000").lstrip("#").upper()[:6]


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


def _slide_chrome(slide: Slide, rec, *, summary: str) -> float:
    """Title, tab badge, timestamp and the parameter column.

    Returns the width in inches left for the figure, which is the full slide
    less the parameter column when there is a summary to show and the full slide
    when there is not — a plot with no recorded parameters should not leave a
    third of the slide blank.
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
    if not summary:
        return content_w
    slide.add(text_box_xml(
        summary, slide.ids,
        x=inches_to_emu(_MARGIN + content_w - _PARAMS_W),
        y=inches_to_emu(_BODY_TOP),
        cx=inches_to_emu(_PARAMS_W), cy=inches_to_emu(_BODY_H),
        size_pt=_PARAMS_PT, color=_hex(theme.CPub.text_muted),
        monospace=True, name="Parameters"))
    return content_w - _PARAMS_W - _GUTTER


def _note(slide: Slide, text: str, *, figure_w: float) -> None:
    """A line under the figure saying what the slide is not.

    Only ever written for a shortfall — a plot placed as a picture, or one whose
    vector capture could not be read. A slide that carries editable shapes says
    nothing, because there is nothing to warn about.
    """
    slide.add(text_box_xml(
        text, slide.ids,
        x=inches_to_emu(_MARGIN), y=inches_to_emu(_SLIDE_H - _MARGIN - 0.28),
        cx=inches_to_emu(figure_w), cy=inches_to_emu(0.28),
        size_pt=_NOTE_PT, color=_hex(theme.CPub.text_faint),
        name="Export note"))


def _place_vector(slide: Slide, svg_bytes: bytes, *, figure_w: float,
                  result: DeckResult) -> str:
    """Draw the figure as PowerPoint shapes.

    Returns ``""`` on success, or a sentence saying why the figure could not be
    drawn as shapes — which the caller puts on the slide after falling back to
    the picture, so the reason travels with the artifact and not only with a log
    line nobody will read.
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
    if len(drawing.shapes) > MAX_SHAPES_PER_SLIDE:
        return (f"This figure is {len(drawing.shapes):,} separate shapes — past "
                f"the {MAX_SHAPES_PER_SLIDE:,} a slide can carry and stay "
                "usable — so it is placed here as a picture.")
    result.note_unsupported(drawing.unsupported)
    placement = Placement.fit(
        drawing,
        x=inches_to_emu(_MARGIN), y=inches_to_emu(_BODY_TOP),
        cx=inches_to_emu(figure_w), cy=inches_to_emu(_BODY_H))
    slide.add(drawing_group_xml(drawing, placement, slide.ids, slide.add_image))
    return ""


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


def _read(path) -> bytes:
    if path is None:
        return b""
    try:
        candidate = Path(path)
        return candidate.read_bytes() if candidate.exists() else b""
    except OSError:
        return b""


def build_saved_plots_deck(
    records: Iterable,
    svg_path_for: Callable[[object], object],
    image_path_for: Callable[[object], object],
    out_path: str | Path,
    *,
    dataset_name: str = "",
    summary_for: Callable[[object], str] | None = None,
) -> DeckResult:
    """Write a ``.pptx`` with a cover slide and one slide per saved plot.

    Each plot is drawn from its vector capture as editable PowerPoint shapes.
    Where no readable capture exists the raster snapshot is placed instead and
    the slide says so; where neither exists the slide carries the plot's title
    and parameters and states that the figure is missing, which is the one
    outcome a silent export would have made indistinguishable from a blank plot.

    Returns a :class:`DeckResult` — raises only on I/O failure writing the file.
    """
    recs = list(records)
    summary_for = summary_for or (lambda r: getattr(r, "summary", "") or "")
    builder = PptxBuilder(title="Saved Plots")
    result = DeckResult()

    _cover_slide(builder, dataset_name=dataset_name, count=len(recs))
    for rec in recs:
        slide = builder.new_slide()
        figure_w = _slide_chrome(slide, rec, summary=summary_for(rec))
        svg_bytes = _read(svg_path_for(rec))
        if svg_bytes:
            reason = _place_vector(slide, svg_bytes, figure_w=figure_w,
                                   result=result)
            if not reason:
                result.vector += 1
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
        _note(slide, "The saved image for this plot could not be read, so the "
                     "figure is missing from this slide.", figure_w=figure_w)

    result.slides = builder.write(out_path)
    return result
