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
from widgets.fig_clipboard import rasterized_layer_count

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
        elif not (self.unsupported or self.rasterized):
            # "all editable" is a claim about every mark in every figure, so it
            # cannot be said over a lost construct or a data layer that became a
            # picture. Both were reported *after* it and read as qualifications
            # of a sentence that had already overstated the case.
            text += ", all editable"
        # Never behind an early return: a deck can be entirely editable and
        # still have lost a hatch or an arc out of one of its figures, and
        # "all editable" said over the top of that is the shortfall being
        # hidden by the very line meant to describe it.
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
    placement = Placement.fit(
        drawing,
        x=inches_to_emu(_MARGIN), y=inches_to_emu(_BODY_TOP),
        cx=inches_to_emu(figure_w), cy=inches_to_emu(_BODY_H))
    slide.add(drawing_group_xml(drawing, placement, slide.ids, slide.add_image))
    if drawing.unsupported:
        # The figure is on the slide and part of it is not. Said here rather
        # than only in a log line, because the log is not what gets presented.
        _note(slide, "Part of this figure could not be redrawn as shapes and is "
                     "missing from it: " + ", ".join(drawing.unsupported) + ".",
              figure_w=figure_w)
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


def build_saved_plots_deck(
    records: Iterable,
    svg_path_for: Callable[[object], object],
    image_path_for: Callable[[object], object],
    out_path: str | Path,
    *,
    dataset_name: str = "",
    summary_for: Callable[[object], str] | None = None,
    regenerated: set | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> DeckResult:
    """Write a ``.pptx`` with a cover slide and one slide per saved plot.

    Each plot is drawn from its vector capture as editable PowerPoint shapes.
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
        svg_bytes = _read(svg_path_for(rec))
        if svg_bytes:
            reason = _place_vector(slide, svg_bytes, figure_w=figure_w,
                                   result=result)
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
