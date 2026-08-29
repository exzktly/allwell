"""Every slide in an exported deck puts its plotting area in the same place.

The bug this file exists for: a deck built one slide at a time scaled each
figure to fill the space the slide gave it, and a saved capture is cropped to
its own ink — so three plots authored at exactly the same figure size came out
of the export with their axes at three different sizes, in three different
places, purely because their tick labels and titles differed in length. Slides
like that cannot be laid beside one another into a larger composite figure,
which is the whole reason to export editable shapes rather than pictures.

The assertions are taken from the **emitted PowerPoint part**, not from the
placement arithmetic that produced it: the axes background is painted a colour
nothing else in the figure uses, so the shape that carries that fill can be
found in the slide XML and measured. A test that read the placement back out of
``Placement`` would pass on a deck whose slides do not line up.
"""
from __future__ import annotations

import re
import zipfile

import matplotlib
import pytest

matplotlib.use("Agg")

from matplotlib.figure import Figure  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from well_viewer.features.saved_plots import _deck  # noqa: E402
from well_viewer.features.saved_plots._align import (  # noqa: E402
    AnchorBox, axes_rect_points, plan_anchor,
)
from well_viewer.features.saved_plots._deck import build_saved_plots_deck  # noqa: E402
from well_viewer.features.saved_plots._drawingml import (  # noqa: E402
    EMU_PER_INCH, EMU_PER_POINT, Placement, inches_to_emu,
)
from well_viewer.features.saved_plots._svg_shapes import parse_svg  # noqa: E402
from widgets.fig_clipboard import (  # noqa: E402
    AXES_RECT_MARKER, axes_fraction_from_svg, figure_axes_fraction,
    figure_crop_bbox, render_figure_svg,
)

#: Painted onto the axes background so the plotting area can be found again in
#: the slide part. Not a colour any of these figures draws with otherwise.
_AXES_MARK = "#123456"


# ── figures ──────────────────────────────────────────────────────────────────

def _figure(kind: str, *, size=(6.0, 4.0)) -> Figure:
    """One of several plots that differ only in how much room their labels want."""
    fig = Figure(figsize=size)
    ax = fig.add_subplot()
    ax.set_facecolor(_AXES_MARK)
    if kind == "short":
        ax.bar([0, 1, 2], [1, 2, 3])
        ax.set_ylabel("n")
    elif kind == "long":
        # Seven-digit tick labels, a long axis title and a title above: every
        # one of these grows the crop without moving the axes inside the figure.
        ax.bar([0, 1, 2], [1_250_000, 2_400_000, 3_100_000])
        ax.set_ylabel("Integrated intensity (a.u.)")
        ax.set_title("A rather long plot title across the top")
    elif kind == "legend":
        ax.plot([0, 1], [0, 1], label="condition one")
        ax.plot([0, 1], [1, 0], label="condition two")
        ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0))
        ax.set_ylabel("f")
    else:
        raise AssertionError(f"no such figure: {kind}")
    return fig


def _capture(kind: str, **kw) -> bytes:
    return render_figure_svg(_figure(kind, **kw))


def _rec(rid, svg, *, summary="Tab: Bar Plots"):
    return SimpleNamespace(id=rid, title=f"Plot {rid}", tab="Bar Plots",
                           created_at="2026-01-01T10:00:00", summary=summary,
                           svg=svg)


def _deck_of(records, tmp_path, name="deck.pptx"):
    out = tmp_path / name
    result = build_saved_plots_deck(
        records, lambda r: r.svg, lambda r: b"", out, dataset_name="probe")
    return out, result


# ── reading the emitted slide back ───────────────────────────────────────────

_SHAPE_RE = re.compile(rb"<p:sp>.*?</p:sp>", re.S)
_XFRM_RE = re.compile(rb'<a:off x="(-?\d+)" y="(-?\d+)"/><a:ext cx="(\d+)" cy="(\d+)"')


def _axes_rect_on_slide(path, index: int):
    """The marked axes rectangle on slide *index*, in inches, or ``None``.

    The largest shape carrying the marker fill: the axes background is one
    rectangle, but a spine or a tick drawn in the same colour would be smaller.
    """
    with zipfile.ZipFile(path) as zf:
        part = zf.read(f"ppt/slides/slide{index}.xml")
    best = None
    for match in _SHAPE_RE.finditer(part):
        block = match.group()
        if b'val="123456"' not in block:
            continue
        xfrm = _XFRM_RE.search(block)
        if xfrm is None:
            continue
        rect = tuple(int(g) / EMU_PER_INCH for g in xfrm.groups())
        if best is None or rect[2] * rect[3] > best[2] * best[3]:
            best = rect
    return best


def _slide_text(path, index: int) -> str:
    with zipfile.ZipFile(path) as zf:
        part = zf.read(f"ppt/slides/slide{index}.xml").decode()
    return " ".join(re.findall(r"<a:t>([^<]*)</a:t>", part))


# ── the capture records where its plotting area is ───────────────────────────

def test_a_capture_records_where_its_plotting_area_sits():
    """The recorded fraction is the axes rectangle, to the point.

    Checked against Matplotlib's own answer rather than against a number typed
    here: the clip rectangle it emits for an axes' artists *is* that axes'
    rectangle on the page, so the two must agree exactly.
    """
    svg = _capture("short")
    frac = axes_fraction_from_svg(svg)
    assert frac is not None, "the capture recorded no plotting area"

    page = re.search(rb'<svg[^>]*width="([\d.]+)pt" height="([\d.]+)pt"', svg)
    width_pt, height_pt = (float(page.group(1)), float(page.group(2)))
    recorded = axes_rect_points(frac, width_pt, height_pt)

    clip = re.search(rb'<clipPath[^>]*>\s*<rect x="([-\d.e]+)" y="([-\d.e]+)"'
                     rb' width="([-\d.e]+)" height="([-\d.e]+)"', svg)
    x, y, w, h = (float(g) for g in clip.groups())
    assert recorded == pytest.approx((x, y, x + w, y + h), abs=0.05)


def test_a_figure_with_no_axes_records_nothing_rather_than_guessing():
    fig = Figure(figsize=(4, 3))
    fig.text(0.5, 0.5, "no plot here")
    assert axes_fraction_from_svg(render_figure_svg(fig)) is None


def test_a_capture_written_before_this_carries_no_marker():
    """A gallery full of older captures has to stay readable, so the absence of
    the marker is a supported state rather than a parse failure."""
    assert axes_fraction_from_svg(b"<svg width='10pt' height='10pt'></svg>") is None
    assert axes_fraction_from_svg(b"") is None
    assert axes_fraction_from_svg(b"<!-- allwell:axes-frac=1,2 --><svg/>") is None
    # A rectangle with no area is not a plotting area.
    assert axes_fraction_from_svg(
        b"<!-- allwell:axes-frac=0.5,0.1,0.5,0.9 --><svg/>") is None


def test_a_sliver_of_a_page_is_read_as_damage_not_as_a_plotting_area():
    """A page is a figure's own ink, so a real plotting area is most of it.
    Honouring a sliver would scale the figure by the reciprocal of that sliver
    and throw it clean off the slide."""
    assert axes_fraction_from_svg(
        b"<!-- allwell:axes-frac=0.1,0.1,0.11,0.9 --><svg/>") is None
    assert axes_fraction_from_svg(
        b"<!-- allwell:axes-frac=0.1,0.1,0.9,0.11 --><svg/>") is None
    assert axes_fraction_from_svg(
        b"<!-- allwell:axes-frac=0.1,0.1,0.9,0.9 --><svg/>") is not None


# ── the deck lines its figures up ────────────────────────────────────────────

def test_figures_of_one_size_put_their_axes_in_one_place(tmp_path):
    """The reported bug.

    Three plots drawn at exactly ``figsize=(6, 4)``, differing only in the room
    their labels ask for. Before the deck anchored on the plotting area these
    reached the slides as axes of 7.45, 7.10 and 5.73 inches wide, at three
    different origins — figures that cannot be tiled into a composite.
    """
    records = [_rec(i, _capture(kind))
               for i, kind in enumerate(("short", "long", "legend"))]
    out, result = _deck_of(records, tmp_path)
    assert result.vector == 3

    rects = [_axes_rect_on_slide(out, index) for index in (2, 3, 4)]
    assert all(r is not None for r in rects), "no axes rectangle on some slide"
    first = rects[0]
    for other in rects[1:]:
        assert other == pytest.approx(first, abs=0.01)


def test_a_figure_of_another_shape_shares_the_same_anchor(tmp_path):
    """A plot with a different aspect cannot have the same axes rectangle, so it
    is centred on the same one instead — the centres line up even where the
    edges cannot."""
    records = [_rec(0, _capture("short")),
               _rec(1, _capture("short", size=(4.0, 5.0)))]
    out, _ = _deck_of(records, tmp_path)
    wide = _axes_rect_on_slide(out, 2)
    tall = _axes_rect_on_slide(out, 3)
    centre = lambda r: (r[0] + r[2] / 2.0, r[1] + r[3] / 2.0)  # noqa: E731
    assert centre(tall) == pytest.approx(centre(wide), abs=0.01)


def test_every_figure_stays_within_the_body_of_its_slide(tmp_path):
    """Anchoring must not push a label off the slide or under the parameter
    column: the anchor box is sized so the deck's own widest decoration still
    fits beside it."""
    records = [_rec(i, _capture(kind))
               for i, kind in enumerate(("short", "long", "legend"))]
    out, _ = _deck_of(records, tmp_path)
    left = _deck._MARGIN
    right = _deck._SLIDE_W - _deck._MARGIN - _deck._PARAMS_W - _deck._GUTTER
    for index in (2, 3, 4):
        x, y, cx, cy = _axes_rect_on_slide(out, index)
        assert x >= left
        assert x + cx <= right
        assert y >= _deck._BODY_TOP
        assert y + cy <= _deck._BODY_TOP + _deck._BODY_H


def test_a_capture_that_cannot_be_aligned_says_so_on_its_slide(tmp_path):
    """A gallery holding one older capture must not quietly place it a little
    off from the rest — a figure that nearly lines up is the one failure the
    reader cannot see."""
    stale = re.sub(AXES_RECT_MARKER.encode() + rb"[-\d.,eE+]+", b"gone",
                   _capture("short"))
    records = [_rec(0, _capture("long")), _rec(1, stale)]
    out, result = _deck_of(records, tmp_path)
    assert result.vector == 2
    assert "does not record where its axes" not in _slide_text(out, 2)
    assert "does not record where its axes" in _slide_text(out, 3)


def test_a_deck_where_nothing_records_its_axes_says_nothing_about_it(tmp_path):
    """There is no alignment to fall short of, so no slide carries the note."""
    stale = [re.sub(AXES_RECT_MARKER.encode() + rb"[-\d.,eE+]+", b"gone",
                    _capture(kind)) for kind in ("short", "long")]
    records = [_rec(i, svg) for i, svg in enumerate(stale)]
    out, result = _deck_of(records, tmp_path)
    assert result.vector == 2
    for index in (2, 3):
        assert "does not record where its axes" not in _slide_text(out, index)


def test_anchoring_never_draws_a_figure_smaller_than_the_letterbox_would(tmp_path):
    """The anchor of a single-plot deck comes from that plot's own margins, so it
    resolves to the same scale the letterbox chose — anchoring buys alignment
    without spending figure size. What it changes is what sits centred in the
    space left over: the plotting area rather than the page."""
    svg = _capture("legend")
    drawing = parse_svg(svg)
    frac = axes_fraction_from_svg(svg)
    assert frac is not None
    box = dict(x=inches_to_emu(_deck._MARGIN), y=inches_to_emu(_deck._BODY_TOP),
               cx=inches_to_emu(_deck._SLIDE_W - 2 * _deck._MARGIN
                                - _deck._PARAMS_W - _deck._GUTTER),
               cy=inches_to_emu(_deck._BODY_H))
    anchor = plan_anchor([frac], **box)
    anchored = Placement.anchor(
        axes_rect_points(frac, drawing.width_pt, drawing.height_pt),
        x=anchor.x, y=anchor.y, cx=anchor.cx, cy=anchor.cy)
    fitted = Placement.fit(drawing, **box)
    # Not bit-identical: the anchor box is rounded to whole EMU on the way
    # through, which is a third of a millionth of an inch.
    assert anchored.scale == pytest.approx(fitted.scale, rel=1e-6)
    # This figure is limited by the slide's width, so along that axis the two
    # rules put it in the same place; down the slack axis the anchored one
    # centres the axes instead of the page, and still lands inside the body.
    assert anchored.origin_x == pytest.approx(fitted.origin_x, abs=2)
    top = anchored.emu_y(0.0)
    bottom = anchored.emu_y(drawing.height_pt)
    assert top >= inches_to_emu(_deck._BODY_TOP)
    assert bottom <= inches_to_emu(_deck._BODY_TOP + _deck._BODY_H)


# ── the anchor planner ───────────────────────────────────────────────────────

_BOX = dict(x=0, y=0, cx=1000, cy=1000)


def test_the_anchor_leaves_room_for_the_deck_s_widest_decoration():
    """One plot needing half its own width to the right of the axes shrinks the
    anchor for every plot, which is what keeps that one plot's legend on the
    slide."""
    plain = (0.0, 0.0, 1.0, 1.0)                  # nothing but the axes
    legend = (0.0, 0.0, 2.0 / 3.0, 1.0)           # half the axes width again
    alone = plan_anchor([plain], **_BOX)
    shared = plan_anchor([plain, legend], **_BOX)
    assert alone == AnchorBox(x=0, y=0, cx=1000, cy=1000)
    assert shared.cx == pytest.approx(1000 / 1.5, abs=1)
    assert shared.x == 0
    assert shared.cy == 1000


def test_the_anchor_is_offset_by_the_room_the_labels_need():
    """The margin a figure needs on the left is space taken out of the box, not
    space the figure is expected to find inside it."""
    # Axes occupying the right half of its page: it needs its own width again
    # to the left of the anchor.
    frac = (0.5, 0.0, 1.0, 1.0)
    box = plan_anchor([frac], **_BOX)
    assert box.cx == pytest.approx(500, abs=1)
    assert box.x == pytest.approx(500, abs=1)


def test_a_deck_with_nothing_to_anchor_on_plans_no_anchor():
    assert plan_anchor([], **_BOX) is None
    assert plan_anchor([None], **_BOX) is None


def test_the_placement_puts_the_plotting_area_exactly_in_the_box():
    """The one thing :meth:`Placement.anchor` promises."""
    rect = (10.0, 20.0, 110.0, 120.0)             # 100 x 100 points
    box = dict(x=1_000_000, y=2_000_000, cx=100 * EMU_PER_POINT,
               cy=100 * EMU_PER_POINT)
    placement = Placement.anchor(rect, **box)
    assert placement.emu_x(rect[0]) == pytest.approx(box["x"], abs=2)
    assert placement.emu_y(rect[1]) == pytest.approx(box["y"], abs=2)
    assert placement.emu_x(rect[2]) == pytest.approx(box["x"] + box["cx"], abs=2)
    assert placement.emu_y(rect[3]) == pytest.approx(box["y"] + box["cy"], abs=2)


def test_a_plotting_area_of_another_shape_is_centred_not_stretched():
    rect = (0.0, 0.0, 100.0, 50.0)                # twice as wide as it is tall
    box = dict(x=0, y=0, cx=100 * EMU_PER_POINT, cy=100 * EMU_PER_POINT)
    placement = Placement.anchor(rect, **box)
    assert placement.emu_x(rect[0]) == pytest.approx(0, abs=2)
    assert placement.emu_x(rect[2]) == pytest.approx(box["cx"], abs=2)
    # Half the box's height, centred: a quarter of it above and below.
    assert placement.emu_y(rect[1]) == pytest.approx(box["cy"] / 4, abs=2)
    assert placement.emu_y(rect[3]) == pytest.approx(3 * box["cy"] / 4, abs=2)


# ── the crop the marker is measured against ──────────────────────────────────

def test_the_page_is_exactly_the_crop_that_was_asked_for():
    """The marker is a fraction of the page, so the page has to be the rectangle
    the fraction was measured against. Matplotlib honours an explicit ``Bbox``
    exactly, which is why ``"tight"`` is resolved into one before the render
    rather than left for ``savefig`` to recompute."""
    fig = _figure("long")
    crop = figure_crop_bbox(fig)
    assert crop is not None
    svg = render_figure_svg(fig, bbox_inches=crop)
    page = re.search(rb'<svg[^>]*width="([\d.]+)pt" height="([\d.]+)pt"', svg)
    assert float(page.group(1)) == pytest.approx((crop.x1 - crop.x0) * 72, abs=0.05)
    assert float(page.group(2)) == pytest.approx((crop.y1 - crop.y0) * 72, abs=0.05)


def test_a_panel_capture_measures_only_the_panel_it_kept():
    """A single-panel save hides its siblings and crops to the one panel, so the
    recorded plotting area is that panel's — not the union of a grid the page
    does not contain."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from widgets.fig_clipboard import hidden_sibling_axes

    fig = Figure(figsize=(8, 3))
    FigureCanvasAgg(fig)          # the panel crop measures through the canvas
    left, right = fig.subplots(1, 2)
    for ax in (left, right):
        ax.plot([0, 1], [0, 1])
    from widgets.fig_clipboard import axis_tight_bbox_inches

    with hidden_sibling_axes(fig, right):
        crop = axis_tight_bbox_inches(right)
        kept = figure_axes_fraction(fig, crop)
    # The same crop, measured without hiding anything: the left panel lies
    # outside it, so the union runs off the left of the page and clamps to 0.
    both = figure_axes_fraction(fig, crop)

    assert kept is not None and both is not None
    assert both[0] == 0.0
    assert kept[0] > 0.05
    assert kept[2] - kept[0] < both[2] - both[0]
