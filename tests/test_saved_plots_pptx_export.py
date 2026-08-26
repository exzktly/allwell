"""The PowerPoint export: SVG in, editable PowerPoint shapes out.

Three layers get their own tests here because they fail in three different ways.
The SVG reader can mis-place geometry (a transform applied twice, a clip that
removes the wrong half); the DrawingML writer can emit XML PowerPoint refuses to
open; and the deck builder can quietly hand back a picture where the user asked
for editable shapes, which is the failure that looks most like success.

The last of those is what most of this file is about. A deck where some figures
are pictures and some are shapes is indistinguishable from a fully editable one
until someone tries to click a bar in a talk, so the fallback is asserted from
both ends: the slide has to say it, and the returned counts have to show it.
"""
from __future__ import annotations

import zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import matplotlib
import pytest

matplotlib.use("Agg")

from well_viewer.features.saved_plots._deck import (  # noqa: E402
    MAX_SHAPES_PER_SLIDE, build_saved_plots_deck,
)
from well_viewer.features.saved_plots._drawingml import (  # noqa: E402
    EMU_PER_POINT, Placement, ShapeIds, drawing_group_xml,
)
from well_viewer.features.saved_plots._pptx import PptxBuilder  # noqa: E402
from well_viewer.features.saved_plots._svg_shapes import (  # noqa: E402
    ImageShape, PathShape, SvgParseError, TextShape, clip_subpaths,
    parse_path_data, parse_svg, subpath_bounds,
)

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


# ── fixtures ─────────────────────────────────────────────────────────────────

def _bar_svg() -> bytes:
    """A small bar chart with error bars — the shape All-Well's Bar tab draws."""
    from widgets.fig_clipboard import render_figure_svg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot()
    ax.bar([0, 1, 2], [3, 5, 2], color=["#4C72B0", "#DD8452", "#55A868"],
           edgecolor="black")
    ax.errorbar([0, 1, 2], [3, 5, 2], yerr=[0.4, 0.3, 0.5], fmt="none",
                ecolor="k", capsize=4)
    ax.set_title("Mean intensity")
    ax.set_ylabel("AU")
    ax.set_xticks([0, 1, 2])
    ax.set_xticklabels(["Ctrl", "A", "B"])
    return render_figure_svg(fig)


def _imshow_svg() -> bytes:
    """A heat map — the one plot family that reaches the deck as a raster block."""
    import numpy as np
    from widgets.fig_clipboard import render_figure_svg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(3, 3))
    ax = fig.add_subplot()
    ax.imshow(np.linspace(0, 1, 24).reshape(4, 6), cmap="viridis")
    return render_figure_svg(fig)


def _rec(rid, **kw):
    base = dict(id=rid, title=f"Plot {rid}", tab="Bar Plots",
                created_at="2026-01-01T10:00:00", summary="Tab: Bar Plots")
    base.update(kw)
    return SimpleNamespace(**base)


def _slide_xml(path, index: int):
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(zf.read(f"ppt/slides/slide{index}.xml"))


def _slide_text(path, index: int) -> str:
    return " ".join(t.text or "" for t in _slide_xml(path, index).iter(f"{_A}t"))


# ── the SVG reader ───────────────────────────────────────────────────────────

def test_reader_keeps_a_bar_where_matplotlib_drew_it():
    drawing = parse_svg(_bar_svg())
    # The three bars are the only filled paths carrying the series colours.
    fills = {s.fill.rgb for s in drawing.shapes
             if isinstance(s, PathShape) and s.fill is not None}
    assert {"4C72B0", "DD8452", "55A868"} <= fills
    # Every shape lands inside the page it was read from — the check that
    # catches a transform applied twice, which lands geometry off-canvas while
    # leaving each individual shape looking perfectly well-formed.
    for shape in drawing.shapes:
        if isinstance(shape, PathShape):
            x0, y0, x1, y1 = subpath_bounds(shape.subpaths)
            assert -1 <= x0 and x1 <= drawing.width_pt + 1
            assert -1 <= y0 and y1 <= drawing.height_pt + 1


def test_reader_keeps_labels_as_text_not_outlines():
    """The whole reason the capture sets ``svg.fonttype='none'``."""
    texts = [s.text for s in parse_svg(_bar_svg()).shapes
             if isinstance(s, TextShape)]
    assert "Mean intensity" in texts
    assert "Ctrl" in texts and "AU" in texts
    rotated = [s for s in parse_svg(_bar_svg()).shapes
               if isinstance(s, TextShape) and s.text == "AU"]
    assert rotated and abs(rotated[0].rotation_deg + 90.0) < 0.5


def test_reader_places_a_raster_block_the_right_way_up():
    """Matplotlib writes ``imshow`` data flipped, with a flipping transform.

    The element's own transform is already folded into the walker's matrix, so
    re-applying it composes the flip twice and cancels it — leaving the image
    upside down inside a rectangle of exactly the right size, which is invisible
    in every check except this one.
    """
    drawing = parse_svg(_imshow_svg())
    images = [s for s in drawing.shapes if isinstance(s, ImageShape)]
    assert images, "an imshow figure should carry a raster block"
    block = images[0]
    assert block.flip_v is True
    assert 0 <= block.y and block.y + block.height <= drawing.height_pt + 1


def test_reader_rejects_bytes_that_are_not_an_svg():
    with pytest.raises(SvgParseError):
        parse_svg(b"not xml at all <<<")
    with pytest.raises(SvgParseError):
        parse_svg(b"<html><body/></html>")


def test_path_parser_lifts_quadratics_and_follows_relative_commands():
    subpaths = parse_path_data("M 0 0 l 10 0 q 5 0 5 5 Z")
    assert len(subpaths) == 1
    kinds = [seg[0] for seg in subpaths[0]]
    assert kinds == ["M", "L", "C", "Z"]        # the quadratic became a cubic
    assert subpaths[0][1][1] == (10.0, 0.0)     # relative lineto resolved


def test_clipping_cuts_only_what_leaves_the_box():
    inside = [[("M", (2.0, 2.0)), ("L", (8.0, 8.0))]]
    assert clip_subpaths(inside, (0, 0, 10, 10), filled=False) is inside

    crossing = [[("M", (5.0, 5.0)), ("L", (25.0, 5.0))]]
    clipped = clip_subpaths(crossing, (0, 0, 10, 10), filled=False)
    assert subpath_bounds(clipped)[2] == pytest.approx(10.0)

    outside = [[("M", (50.0, 50.0)), ("L", (60.0, 60.0))]]
    assert clip_subpaths(outside, (0, 0, 10, 10), filled=False) == []


# ── the DrawingML writer ─────────────────────────────────────────────────────

def test_writer_emits_one_powerpoint_shape_per_figure_shape():
    drawing = parse_svg(_bar_svg())
    placement = Placement.fit(drawing, x=0, y=0, cx=6_000_000, cy=4_000_000)
    xml = drawing_group_xml(drawing, placement, ShapeIds(), lambda png: "rId2")
    root = ET.fromstring(
        f'<root xmlns:a="{_A[1:-1]}" xmlns:p="{_P[1:-1]}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships">{xml}</root>')
    shapes = root.findall(f".//{_P}sp")
    assert len(shapes) == len(drawing.shapes)
    # Every shape id is distinct — PowerPoint rejects a slide with a repeat.
    ids = [e.get("id") for e in root.iter(f"{_P}cNvPr")]
    assert len(ids) == len(set(ids))


def test_writer_scales_geometry_by_the_placement():
    """A figure placed at half size draws its shapes at half size.

    Asserted on the emitted extent rather than on the placement's own scale
    factor: the scale is an input the writer could ignore, and a shape that
    ignored it would still pass any check made against the input.
    """
    drawing = parse_svg(_bar_svg())
    full = Placement(0, 0, 1.0)
    half = Placement(0, 0, 0.5)
    ids = ShapeIds()
    wide = drawing_group_xml(drawing, full, ids, lambda png: "rId2")
    narrow = drawing_group_xml(drawing, half, ids, lambda png: "rId2")

    def _group_ext(xml: str) -> int:
        return int(xml.split('<a:ext cx="')[1].split('"')[0])

    assert _group_ext(wide) == pytest.approx(_group_ext(narrow) * 2, rel=0.01)
    assert _group_ext(wide) == pytest.approx(
        drawing.width_pt * EMU_PER_POINT, rel=0.01)


def test_a_deck_of_one_empty_slide_is_a_valid_package(tmp_path):
    out = tmp_path / "empty.pptx"
    builder = PptxBuilder()
    builder.new_slide()
    assert builder.write(out) == 1
    with zipfile.ZipFile(out) as zf:
        assert zf.testzip() is None
        names = set(zf.namelist())
        for part in ("[Content_Types].xml", "_rels/.rels",
                     "ppt/presentation.xml", "ppt/slides/slide1.xml",
                     "ppt/slideMasters/slideMaster1.xml",
                     "ppt/slideLayouts/slideLayout1.xml",
                     "ppt/theme/theme1.xml"):
            assert part in names
        for name in names:
            if name.endswith((".xml", ".rels")):
                ET.fromstring(zf.read(name))    # every part is well-formed XML


# ── the deck ─────────────────────────────────────────────────────────────────

def test_a_plot_with_a_vector_capture_reaches_the_slide_as_shapes(tmp_path):
    svg = tmp_path / "a.svg"
    svg.write_bytes(_bar_svg())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck(
        [_rec("1", title="Mean intensity")],
        lambda r: svg, lambda r: None, out, dataset_name="plate-7",
        summary_for=lambda r: r.summary)

    assert (result.vector, result.raster) == (1, 0)
    assert result.slides == 2                      # cover + one plot
    slide = _slide_xml(out, 2)
    # Editable shapes, not one picture of a chart.
    assert slide.findall(f".//{_P}pic") == []
    assert len(slide.findall(f".//{_P}sp")) > 20
    # The figure's own text survived as text, alongside the slide's chrome.
    text = _slide_text(out, 2)
    assert "Mean intensity" in text and "Ctrl" in text and "AU" in text


def test_a_plot_without_a_vector_capture_says_so_on_its_own_slide(tmp_path):
    """The shortfall has to be visible in the artifact, not only in a count.

    A deck whose figures are silently pictures is indistinguishable from one
    whose figures are editable, right up until someone tries to restyle a bar in
    front of a room.
    """
    png = tmp_path / "a.png"
    png.write_bytes(_png_bytes())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck(
        [_rec("1")], lambda r: None, lambda r: png, out)

    assert (result.vector, result.raster) == (0, 1)
    assert _slide_xml(out, 2).findall(f".//{_P}pic")      # placed as a picture
    note = _slide_text(out, 2)
    assert "without a vector capture" in note
    assert "Re-save" in note
    assert "as a picture" in result.summary()


def test_a_plot_with_neither_capture_says_the_figure_is_missing(tmp_path):
    out = tmp_path / "deck.pptx"
    result = build_saved_plots_deck(
        [_rec("1")], lambda r: None, lambda r: None, out)

    assert (result.vector, result.raster) == (0, 0)
    assert result.slides == 2
    text = _slide_text(out, 2)
    assert "could not be read" in text
    # The plot is still named and its parameters still shown: a slide that
    # dropped the record entirely would leave the reader counting plots to
    # notice one had gone.
    assert "Plot 1" in text and "Tab: Bar Plots" in text


def test_an_unreadable_vector_capture_falls_back_and_names_the_reason(tmp_path):
    svg = tmp_path / "broken.svg"
    svg.write_bytes(b"<svg><this is not xml")
    png = tmp_path / "a.png"
    png.write_bytes(_png_bytes())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck([_rec("1")], lambda r: svg, lambda r: png, out)

    assert (result.vector, result.raster) == (0, 1)
    text = _slide_text(out, 2)
    assert "could not be read" in text
    # Distinct from the never-captured wording: the two have different fixes,
    # and telling a user to re-save a plot whose capture is corrupt on disk
    # sends them to do the one thing that will not help.
    assert "Re-save" not in text


def test_a_figure_of_too_many_shapes_becomes_a_picture_and_says_how_many(tmp_path):
    """Past the cap a deck does not open at all, so the cap is not optional.

    The message names the count, because "too complex" gives the user nothing to
    act on while "180,000 shapes" tells them to plot fewer cells.
    """
    from matplotlib.figure import Figure
    import numpy as np
    from widgets.fig_clipboard import render_figure_svg

    rng = np.random.default_rng(0)
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot()
    n = MAX_SHAPES_PER_SLIDE + 500
    ax.scatter(rng.random(n), rng.random(n), s=1)
    svg = tmp_path / "big.svg"
    svg.write_bytes(render_figure_svg(fig))
    png = tmp_path / "a.png"
    png.write_bytes(_png_bytes())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck([_rec("1")], lambda r: svg, lambda r: png, out)

    assert (result.vector, result.raster) == (0, 1)
    text = _slide_text(out, 2)
    assert "separate shapes" in text
    assert f"{MAX_SHAPES_PER_SLIDE:,}" in text


def test_a_heat_map_carries_its_raster_block_into_the_deck(tmp_path):
    svg = tmp_path / "heat.svg"
    svg.write_bytes(_imshow_svg())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck([_rec("1", tab="Heat Map")],
                                    lambda r: svg, lambda r: None, out)

    assert (result.vector, result.raster) == (1, 0)
    slide = _slide_xml(out, 2)
    # The pixel block is a picture *inside* an otherwise editable figure — the
    # axes, ticks and labels around it are still shapes.
    assert slide.findall(f".//{_P}pic")
    assert len(slide.findall(f".//{_P}sp")) > 5
    with zipfile.ZipFile(out) as zf:
        assert any(n.startswith("ppt/media/") for n in zf.namelist())


def test_every_plot_gets_a_slide_in_the_order_it_was_given(tmp_path):
    svg = tmp_path / "a.svg"
    svg.write_bytes(_bar_svg())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck(
        [_rec("1", title="First"), _rec("2", title="Second"),
         _rec("3", title="Third")],
        lambda r: svg, lambda r: None, out)

    assert result.slides == 4
    assert "First" in _slide_text(out, 2)
    assert "Second" in _slide_text(out, 3)
    assert "Third" in _slide_text(out, 4)


def test_an_empty_gallery_still_writes_a_readable_cover(tmp_path):
    out = tmp_path / "deck.pptx"
    result = build_saved_plots_deck([], lambda r: None, lambda r: None, out,
                                    dataset_name="plate-7")
    assert result.slides == 1
    cover = _slide_text(out, 1)
    assert "Saved Plots" in cover and "plate-7" in cover and "0 saved plots" in cover


def test_one_image_shared_by_two_slides_is_stored_once_per_slide(tmp_path):
    """Identical bytes inside one slide are registered once, not per shape."""
    svg = tmp_path / "heat.svg"
    svg.write_bytes(_imshow_svg())
    out = tmp_path / "deck.pptx"
    build_saved_plots_deck([_rec("1"), _rec("2")], lambda r: svg,
                           lambda r: None, out)
    with zipfile.ZipFile(out) as zf:
        media = [n for n in zf.namelist() if n.startswith("ppt/media/")]
        rels = ET.fromstring(zf.read("ppt/slides/_rels/slide2.xml.rels"))
    # One block per slide, and each slide's relationship targets exist.
    assert len(media) == 2
    targets = {r.get("Target") for r in rels}
    assert any(t.startswith("../media/") for t in targets)


def _png_bytes() -> bytes:
    """A tiny real PNG, so the deck builder's header read has something to read."""
    import io

    import matplotlib.image as mpimg
    import numpy as np

    buf = io.BytesIO()
    mpimg.imsave(buf, (np.linspace(0, 1, 300).reshape(10, 10, 3) * 255)
                 .astype("uint8"), format="png")
    return buf.getvalue()


# ── the app's own sequence ───────────────────────────────────────────────────

def _stub_ctx(tmp_path):
    """The slice of ``ctx`` the store, the capture and the export read.

    Deliberately not the full app: the thing under test here is the order in
    which four real modules touch one record, and building an eighteen-hundred
    object window to watch that would put the test in the slow lane for nothing.
    """
    from well_viewer.core.app_runtime import AppRuntime

    dataset = SimpleNamespace(data_dir=tmp_path, persistence_doc=None,
                              well_paths={}, well_labels={})
    state = SimpleNamespace(dataset=dataset)
    app = SimpleNamespace(_app_state=state)
    ctx = SimpleNamespace(app=app, state=state, runtime=AppRuntime(app))
    app._ctx = ctx
    return ctx


@pytest.mark.qt
def test_saving_a_card_then_exporting_gives_editable_shapes(qapp, tmp_path,
                                                            monkeypatch):
    """The whole path, in the order the app runs it.

    Each end of this works in isolation and has for a while: the card renders
    vector, the store writes files, the deck builder draws shapes. What no unit
    test of any one of them can see is whether the card's call and the handler's
    signature agree, and whether the name the record stores is the name the file
    was written under. Both are sequence facts, and both have exactly one
    symptom when wrong — every figure silently exports as a picture.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import get_store
    from widgets.plot_card import PlotCard

    ctx = _stub_ctx(tmp_path)
    monkeypatch.setattr(C, "capture_params", lambda ctx_, tab: ({}, {}))
    monkeypatch.setattr(C, "build_summary", lambda tab, p, t: "Tab: " + tab)
    monkeypatch.setattr(C, "refresh_saved_plots_gallery", lambda ctx_: None)

    card = PlotCard(figsize=(4, 3))
    ax = card.figure.add_subplot(1, 1, 1)
    ax.bar([0, 1], [2, 3])
    ax.set_title("Cells per well")

    # The real wiring, and the real click — not a hand-made call to the handler.
    C.wire_save_to_gallery(ctx, card, "Bar Plots")
    card._hover_ax = ax
    card._on_save_axis_clicked()

    rec = get_store(ctx).records[-1]
    assert rec.svg_filename, "the click should have stored a vector capture"
    stored = tmp_path / "saved_plots" / rec.svg_filename
    assert stored.exists() and stored.read_bytes().startswith(b"<?xml")

    out = tmp_path / "deck.pptx"
    result = build_saved_plots_deck(
        [rec], lambda r: get_store(ctx).vector_path(ctx, r),
        lambda r: get_store(ctx).image_path(ctx, r), out)
    assert (result.vector, result.raster) == (1, 0)
    assert "Cells per well" in _slide_text(out, 2)


def test_a_record_saved_without_vector_never_claims_to_have_one(tmp_path,
                                                               monkeypatch):
    """The Image Table saves a composed raster and has no figure to vectorise.

    ``svg_filename`` must stay empty for it, so the deck builder reports the
    picture rather than looking for a file nobody wrote — and reports it as the
    normal outcome it is, not as a missing file.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import get_store

    ctx = _stub_ctx(tmp_path)
    monkeypatch.setattr(C, "capture_params", lambda ctx_, tab: ({}, {}))
    monkeypatch.setattr(C, "refresh_saved_plots_gallery", lambda ctx_: None)
    status = C.save_plot_from_card(ctx, "Image Table", _png_bytes(), "grid")

    assert status.startswith("Saved")
    rec = get_store(ctx).records[-1]
    assert rec.svg_filename == ""
    assert get_store(ctx).vector_path(ctx, rec) is None


def test_deleting_a_plot_takes_its_vector_capture_with_it(tmp_path):
    """Otherwise the dataset folder accumulates SVGs nothing can name."""
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    rec = SavedPlot(id="p1", created_at="2026-01-01T00:00:00", tab="Bar Plots",
                    title="A", png_filename="p1.png", svg_filename="p1.svg",
                    params={})
    assert store.add(ctx, rec, b"png-bytes", b"<svg/>")
    images = tmp_path / "saved_plots"
    assert (images / "p1.png").exists() and (images / "p1.svg").exists()

    store.delete(ctx, "p1")
    assert not (images / "p1.png").exists()
    assert not (images / "p1.svg").exists()


def test_a_vector_write_that_fails_still_saves_the_plot(tmp_path, monkeypatch):
    """Losing the enhancement must not lose the record.

    And the record must not go on claiming a capture it does not have: a name
    pointing at a file that was never written would make the deck builder look
    for it, fail to read it, and report "the capture could not be read" — a
    different problem with a different fix.
    """
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    real_write = type(tmp_path).write_bytes

    def _fail_on_svg(self, data):
        if self.suffix == ".svg":
            raise OSError("disk full")
        return real_write(self, data)

    monkeypatch.setattr(type(tmp_path), "write_bytes", _fail_on_svg)
    rec = SavedPlot(id="p1", created_at="2026-01-01T00:00:00", tab="Bar Plots",
                    title="A", png_filename="p1.png", svg_filename="p1.svg",
                    params={})
    assert store.add(ctx, rec, b"png-bytes", b"<svg/>") is True
    assert rec.svg_filename == ""
    assert store.vector_path(ctx, rec) is None


# ── the control the user actually presses ────────────────────────────────────

@pytest.mark.qt
def test_the_export_menu_reaches_both_exporters(qapp, monkeypatch):
    """A control that is offered is not a control that works.

    Asserting the menu *contains* a PowerPoint entry would pass against a button
    with no menu attached, or with its action connected to nothing. So this
    triggers the real action off the real button and checks the controller ran —
    the same seam that has hidden a dead callback in this feature area before.
    """
    from PySide6.QtWidgets import QWidget

    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots import tab_view

    called = []
    monkeypatch.setattr(C, "export_report_pdf", lambda ctx: called.append("pdf"))
    monkeypatch.setattr(C, "export_deck_pptx", lambda ctx: called.append("pptx"))

    # The parent is held for the duration: letting it fall out of scope takes
    # the button's C++ object with it and the failure reads as a Qt bug.
    band = QWidget()
    button = tab_view._build_export_button(object(), band)
    menu = button.menu()
    assert menu is not None, "the Export button must actually carry its menu"

    labels = [a.text() for a in menu.actions()]
    assert any("PowerPoint" in label for label in labels)
    for action in menu.actions():
        action.trigger()
    assert called == ["pdf", "pptx"]
