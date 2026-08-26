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

from well_viewer.features.saved_plots import _deck  # noqa: E402
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


def test_a_deck_that_lost_a_figure_never_reports_itself_complete(tmp_path):
    """A plot whose slide carries no figure is a third outcome, and it counts.

    This test previously asserted ``(vector, raster) == (0, 0)`` and called that
    correct — which pinned the defect rather than guarding against it. With
    neither counter incremented, ``summary()`` divided zero by nothing and said
    "0 plots, all editable" for a deck that had just dropped a figure, and a
    mixed deck under-reported its own size. The behaviour under test is not the
    pair of counters; it is that the status line can never describe a deck as
    fully editable when it is not.
    """
    out = tmp_path / "deck.pptx"
    result = build_saved_plots_deck(
        [_rec("1")], lambda r: None, lambda r: None, out)

    assert result.missing == 1
    assert (result.vector, result.raster) == (0, 0)
    assert "all editable" not in result.summary()
    assert "1 plot" in result.summary() and "no figure" in result.summary()
    assert result.slides == 2
    text = _slide_text(out, 2)
    assert "could not be read" in text
    # The plot is still named and its parameters still shown: a slide that
    # dropped the record entirely would leave the reader counting plots to
    # notice one had gone.
    assert "Plot 1" in text and "Tab: Bar Plots" in text


def test_a_mixed_deck_counts_every_plot_it_was_given(tmp_path):
    """Three plots in, three plots reported — one of each outcome."""
    svg = tmp_path / "a.svg"
    svg.write_bytes(_bar_svg())
    png = tmp_path / "a.png"
    png.write_bytes(_png_bytes())
    svgs = {"1": svg, "2": None, "3": None}
    pngs = {"1": None, "2": png, "3": None}
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck(
        [_rec("1"), _rec("2"), _rec("3")],
        lambda r: svgs[r.id], lambda r: pngs[r.id], out)

    assert (result.vector, result.raster, result.missing) == (1, 1, 1)
    assert result.summary().startswith("3 plots")
    # The status line states counts and leaves the advice on the slides, where
    # the cause is known: "re-save it" is right for a plot that never had a
    # capture and useless for one past the shape cap.
    assert "re-save" not in result.summary().lower()


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


def test_a_figure_of_too_many_shapes_becomes_a_picture_and_says_how_many(
        tmp_path, monkeypatch):
    """Past the cap a deck does not open at all, so the cap is not optional.

    The message names the count, because "too complex" gives the user nothing to
    act on while "180,000 shapes" tells them to plot fewer cells.
    """
    from matplotlib.figure import Figure
    import numpy as np
    from widgets.fig_clipboard import render_figure_svg

    # The cap is lowered for this test rather than the figure being built past
    # the real one: the contract is what happens on the far side of the
    # threshold, not where the threshold sits — and rendering and parsing a
    # third of a million points made this one test 89% of the file's runtime,
    # paid on every fast-lane run and every CI job.
    monkeypatch.setattr(_deck, "MAX_SHAPES_PER_SLIDE", 300)
    rng = np.random.default_rng(0)
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot()
    ax.scatter(rng.random(800), rng.random(800), s=1)
    svg = tmp_path / "big.svg"
    # ``max_paths=None`` on purpose: this is the backstop case — an oversized SVG
    # that reached the store some other way (an older build wrote it, or the
    # artist-count estimate ran low). The deck must refuse it rather than emit a
    # slide that will not open.
    svg.write_bytes(render_figure_svg(fig, max_paths=None))
    png = tmp_path / "a.png"
    png.write_bytes(_png_bytes())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck([_rec("1")], lambda r: svg, lambda r: png, out)

    assert (result.vector, result.raster) == (0, 1)
    text = _slide_text(out, 2)
    assert "separate shapes" in text
    assert "300" in text


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
    """A ctx carrying the app's **real** state object.

    Not a namespace of the fields that happen to be read: the export snapshots
    the live state and puts it back, and that snapshot walks selection, channel,
    plot, cell-gating and every feature's view-state hook. A stub shaped from
    the outside lets a restore that never ran look like one that did — and it did
    exactly that, which is how "put the user back" shipped both untested and,
    in production, silently skippable.

    ``AppState()`` constructs standalone and costs nothing, so there is no reason
    to approximate it.
    """
    from well_viewer.core.app_runtime import AppRuntime
    from well_viewer.core.app_state import AppState
    from well_viewer.core.widget_registry import WidgetRegistry
    from well_viewer.features.registry import (
        attach_feature_state, build_feature_registry,
    )

    state = AppState()
    # Attach the feature-owned slices, as app construction does. Without them
    # ``view_state.snapshot`` raises on the first slice it reaches — which is
    # the condition the export now declines on, so a stub missing them would
    # test the refusal path and nothing else.
    attach_feature_state(state)
    state.dataset.data_dir = tmp_path
    state.dataset.persistence_doc = None
    app = SimpleNamespace(_app_state=state)
    said: list[str] = []
    ctx = SimpleNamespace(app=app, state=state, runtime=AppRuntime(app),
                          widgets=WidgetRegistry(app), window=None,
                          features=build_feature_registry(),
                          notify=SimpleNamespace(set_status=said.append))
    ctx.said = said
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



# ── the capture, and what it refuses ─────────────────────────────────────────

def test_a_dense_figure_keeps_everything_but_its_point_cloud_editable(tmp_path):
    """Past the cap the figure is *not* refused — only its densest layer is.

    Refusing outright is what shipped, and it defeated the format for exactly
    the per-cell plots that most need restyling: the plot saved with no vector
    capture at all, and the deck then placed a picture and told the user to
    re-save a plot that would fail the same way again. Now the point cloud
    becomes one embedded image and the axes, ticks, labels, title and legend
    stay editable — the Illustrator workflow this export exists to replace.

    Driven with an explicit low ``max_paths`` rather than by building a figure
    past the real one: the contract under test is what happens on the far side
    of the threshold, not where the threshold sits.
    """
    import numpy as np
    from matplotlib.figure import Figure

    from widgets.fig_clipboard import figure_path_count, render_figure_svg

    rng = np.random.default_rng(0)
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot()
    cloud = ax.scatter(rng.random(2000), rng.random(2000), s=1, label="cells")
    ax.plot([0, 1], [0, 1], "r-", label="identity")
    ax.set_title("Cells per well")
    ax.set_xlabel("GFP")
    ax.legend()
    assert figure_path_count(fig) > 500

    svg = render_figure_svg(fig, max_paths=500)
    assert svg.startswith(b"<?xml"), "the figure must still be captured"

    drawing = parse_svg(svg)
    texts = [t.text for t in drawing.shapes if isinstance(t, TextShape)]
    assert "Cells per well" in texts and "GFP" in texts and "cells" in texts
    assert [s for s in drawing.shapes if isinstance(s, ImageShape)], (
        "the dense layer should have become one embedded image")
    # The cloud is a picture; everything else is still shapes.
    assert len([s for s in drawing.shapes if isinstance(s, PathShape)]) > 5

    # And the live figure is left exactly as it was found — this runs against the
    # figure on screen, so a rasterized flag left behind would change how the
    # user's own plot draws for the rest of the session.
    assert cloud.get_rasterized() is False


def test_an_ordinary_scatter_stays_fully_vector(tmp_path):
    """A per-cell scatter of the size people actually restyle must not be
    quietly downgraded to a picture."""
    import numpy as np
    from matplotlib.figure import Figure

    from widgets.fig_clipboard import render_figure_svg

    rng = np.random.default_rng(0)
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot()
    ax.scatter(rng.random(8000), rng.random(8000), s=1)

    drawing = parse_svg(render_figure_svg(fig))
    assert not [s for s in drawing.shapes if isinstance(s, ImageShape)]
    assert len([s for s in drawing.shapes if isinstance(s, PathShape)]) > 7900


def test_the_capture_threshold_sits_where_a_deck_still_opens():
    """Both bounds, because both directions have already gone wrong.

    Too low and an ordinary figure loses its cloud for nothing — 4,000 was the
    first value and it silently refused a twenty-thousand-cell scatter outright.
    Too high and the deck cannot be opened: measured end to end, 40,000 shapes
    converts in 46 s, 100,000 in over two and a half minutes, and 249,000 was
    killed out of memory at 13.9 GB. The second value was 250,000, set from the
    *build* time — which is linear, and is not what the user waits for.

    This is a unit test standing in for a measurement it cannot make, so it pins
    the range the measurement supports rather than the number itself.
    """
    from widgets.fig_clipboard import MAX_VECTOR_PATHS

    assert MAX_VECTOR_PATHS >= 10_000, (
        "an ordinary per-cell scatter must stay fully vector")
    assert MAX_VECTOR_PATHS <= 50_000, (
        "past this a deck takes minutes to open, or does not open at all")


def test_the_capture_never_stores_what_the_deck_would_refuse():
    """Two numbers, one rule. They live in different layers — the capture is in
    ``widgets/`` and must not import a feature — so the tie between them is this
    assertion rather than a shared constant."""
    from widgets.fig_clipboard import MAX_VECTOR_PATHS

    assert MAX_VECTOR_PATHS <= MAX_SHAPES_PER_SLIDE



# ── figures that are not tidy ────────────────────────────────────────────────
#
# Every fixture above is a clean hand-built figure: no hatch, no mathtext, no
# non-default dash, no patch that leaves its axes. A corpus like that validates
# the happy path and encodes whatever assumption the bug is made of — four
# separate defects lived in exactly the gap it left. These are the messy cases.

def _clipped_bar_svg() -> bytes:
    """A filled *and* stroked bar that runs off the top of its axes."""
    from matplotlib.figure import Figure

    from widgets.fig_clipboard import render_figure_svg

    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot()
    ax.bar([0, 1, 2], [3, 9, 2], edgecolor="black", linewidth=2)
    ax.set_ylim(0, 5)
    return render_figure_svg(fig)


def test_a_clipped_bar_does_not_gain_an_edge_along_the_axes(tmp_path):
    """The defect this exists for put a wrong number on the slide.

    A bar of value 9 on a ``ylim`` of 5 is clipped at the axes top. Closing that
    clipped outline as a polygon — right for the fill, wrong for the stroke —
    draws a solid cap exactly at 5, so the bar reads as a bar whose value *is*
    5. Matplotlib draws no such edge: the bar simply runs off the top.
    """
    drawing = parse_svg(_clipped_bar_svg())
    # The bars are the ``patch_`` paths inside the axes; ``patch_1``/``patch_2``
    # are the figure and axes backgrounds, which carry no stroke.
    bars = [s for s in drawing.shapes
            if isinstance(s, PathShape) and s.name.startswith("patch_")
            and s.fill is not None and s.stroke is not None]
    assert len(bars) == 3

    split = [b for b in bars if b.stroke_subpaths is not None]
    assert len(split) == 1, "exactly the bar that leaves the axes is split"
    clipped = split[0]

    # The outline is open runs, not a closed polygon — no cap along the clip.
    assert not any(seg[0] == "Z" for sub in clipped.stroked() for seg in sub)
    # ...while the fill is still closed, so the bar is still a filled bar.
    assert any(seg[0] == "Z" for sub in clipped.subpaths for seg in sub)
    # The bars that stay inside their axes are untouched by any of this.
    for bar in bars:
        if bar is not clipped:
            assert bar.stroke_subpaths is None
            assert any(seg[0] == "Z" for sub in bar.stroked() for seg in sub)


def test_the_clipped_bar_reaches_the_slide_as_one_shape(tmp_path):
    """Split geometry must not split the *object*: a user still selects one bar.

    The two geometries go into one ``custGeom`` as separate ``a:path`` entries,
    one filled and unstroked, one stroked and unfilled.
    """
    svg = tmp_path / "clipped.svg"
    svg.write_bytes(_clipped_bar_svg())
    out = tmp_path / "deck.pptx"
    result = build_saved_plots_deck([_rec("1")], lambda r: svg, lambda r: None,
                                    out)
    assert result.vector == 1

    slide = _slide_xml(out, 2)
    split_shapes = [
        sp for sp in slide.iter(f"{_P}sp")
        if len({(p.get("fill"), p.get("stroke")) for p in sp.iter(f"{_A}path")}) > 1
    ]
    assert len(split_shapes) == 1
    modes = {(p.get("fill"), p.get("stroke"))
             for p in split_shapes[0].iter(f"{_A}path")}
    assert modes == {("norm", "0"), ("none", "1")}


def test_a_hatched_fill_is_reported_rather_than_dropped(tmp_path):
    """``fill: url(#…)`` has no solid colour, so it reads as "no fill".

    That is the same answer as ``fill: none`` and means something else entirely:
    hatched bars come out as empty outlines, and a figure whose only distinction
    between conditions is the hatch loses all of it.
    """
    from matplotlib.figure import Figure

    from widgets.fig_clipboard import render_figure_svg

    fig = Figure(figsize=(3, 2))
    ax = fig.add_subplot()
    ax.bar([0, 1], [3, 5], hatch="//", color="white", edgecolor="k")
    drawing = parse_svg(render_figure_svg(fig))

    assert any("hatch" in item for item in drawing.unsupported)

    svg = tmp_path / "hatched.svg"
    svg.write_bytes(render_figure_svg(fig))
    out = tmp_path / "deck.pptx"
    result = build_saved_plots_deck([_rec("1")], lambda r: svg, lambda r: None,
                                    out)
    # The figure is on the slide and the slide says what is missing from it.
    assert result.vector == 1
    assert "could not be redrawn" in _slide_text(out, 2)
    assert any("hatch" in item for item in result.unsupported)
    # And the status line says it too. A deck can be entirely vector and still
    # have lost something out of one of its figures, so "all editable" must not
    # be said over the top of that.
    assert "all editable" not in result.summary()
    assert "hatch" in result.summary()


def test_a_mathtext_label_keeps_its_reading_order(tmp_path):
    """Matplotlib groups mathtext ``<tspan>``s by font, not by reading order.

    Each carries its own per-glyph positions, so an italic span can hold the μ
    at one end of the label and an α sixty points further along, with the
    upright run sitting between them in space and after them in the document.
    Concatenating the element's text gives neither what the figure says nor
    where it says it — and µm and Greek are ordinary in microscopy axis labels.
    """
    from matplotlib.figure import Figure

    from widgets.fig_clipboard import render_figure_svg

    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot()
    ax.set_xlabel(r"$\mu$m intensity $\alpha_i$")
    drawing = parse_svg(render_figure_svg(fig))

    label_runs = [s for s in drawing.shapes if isinstance(s, TextShape)
                  and any(ch in s.text for ch in "μα") or
                  (isinstance(s, TextShape) and "intensity" in s.text)]
    by_position = sorted(label_runs, key=lambda s: (round(s.y), s.x))
    reading_order = [r.text.strip() for r in by_position]

    assert "μ" in reading_order and "α" in reading_order
    # μ comes before "m intensity", which comes before α — the order on screen.
    assert reading_order.index("μ") < reading_order.index("α")
    assert any("intensity" in t for t in reading_order)
    mu_at = reading_order.index("μ")
    text_at = next(i for i, t in enumerate(reading_order) if "intensity" in t)
    assert mu_at < text_at < reading_order.index("α")
    # No run carries the SVG's pretty-print indentation.
    for run in by_position:
        assert run.text == run.text.strip() or run.text.strip()
        assert "\n" not in run.text

    # The subscript is smaller and sits lower than the character it follows.
    subscripts = [s for s in drawing.shapes if isinstance(s, TextShape)
                  and s.text.strip() == "i" and s.size_pt < 9]
    assert subscripts, "the subscript should survive as its own smaller run"


def test_each_dash_pattern_survives_as_a_different_dash(tmp_path):
    """Three line styles must not become three identical curves.

    A figure that encodes its series in line style stops being readable when
    every pattern is written as ``prstDash val="dash"`` — and the legend keys
    become identical too, so nothing on the slide says which series is which.
    """
    from matplotlib.figure import Figure

    from widgets.fig_clipboard import render_figure_svg

    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot()
    for style in ("--", ":", "-."):
        ax.plot([0, 1, 2], [1, 2, 3], linestyle=style, linewidth=2)
    svg = tmp_path / "dashed.svg"
    svg.write_bytes(render_figure_svg(fig))
    out = tmp_path / "deck.pptx"
    build_saved_plots_deck([_rec("1")], lambda r: svg, lambda r: None, out)

    presets = {d.get("val") for d in _slide_xml(out, 2).iter(f"{_A}prstDash")}
    assert len(presets) == 3, f"three line styles collapsed to {presets}"


def test_a_control_character_in_a_title_cannot_break_the_deck(tmp_path):
    """A title is free text, and a paste out of Word carries ``\x0b``.

    ``escape`` rewrites ``&``, ``<`` and ``>`` and leaves control characters
    alone, so one of them makes the whole slide part unparseable — not one bad
    label, an unopenable slide. LibreOffice renders it blank and PowerPoint
    offers to repair the file, while the export reports success either way.
    """
    svg = tmp_path / "a.svg"
    svg.write_bytes(_bar_svg())
    out = tmp_path / "deck.pptx"

    build_saved_plots_deck(
        [_rec("1", title="Plot \x0b one \x00 two", tab="Bar \x0c Plots")],
        lambda r: svg, lambda r: None, out,
        dataset_name="plate\x1f7", summary_for=lambda r: "Wells\x08: A01")

    slide = _slide_xml(out, 2)          # raises if the part does not parse
    text = " ".join(t.text or "" for t in slide.iter(f"{_A}t"))
    assert "Plot" in text and "one" in text and "two" in text
    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(zf.read(name))


def test_the_characters_xml_does_allow_are_kept(tmp_path):
    """Only the illegal ones go. An ampersand, a quote and an emoji are fine."""
    svg = tmp_path / "a.svg"
    svg.write_bytes(_bar_svg())
    out = tmp_path / "deck.pptx"
    title = 'A & B <control> "quoted" \U0001f600'

    build_saved_plots_deck([_rec("1", title=title)], lambda r: svg,
                           lambda r: None, out)

    assert title in _slide_text(out, 2)


def test_a_viewbox_scales_the_clip_rectangle_with_the_geometry():
    """A clip rectangle has to live in the same space as what it clips.

    Matplotlib's viewBox always matches its width/height, so this is latent
    there — but the reader advertises that it handles the case, and the ``.svg``
    files sit in the dataset folder where they can be edited by hand.
    """
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" '
           b'viewBox="0 0 100 100">'
           b'<defs><clipPath id="c">'
           b'<rect x="10" y="10" width="80" height="80"/></clipPath></defs>'
           b'<g clip-path="url(#c)">'
           b'<path d="M 20 20 L 80 20 L 80 80 L 20 80 Z" style="fill:#ff0000"/>'
           b"</g></svg>")
    drawing = parse_svg(svg)
    bounds = subpath_bounds(drawing.shapes[0].subpaths)
    # The square is wholly inside the clip once both are in the same space, so
    # it survives whole at twice its written size.
    assert bounds == pytest.approx((40.0, 40.0, 160.0, 160.0))


def test_the_app_properties_follow_the_schema_order(tmp_path):
    """``Slides`` before ``Application``, as the extended-properties sequence
    has it. PowerPoint tolerates the other order; a stricter reader need not."""
    out = tmp_path / "deck.pptx"
    build_saved_plots_deck([], lambda r: None, lambda r: None, out)
    with zipfile.ZipFile(out) as zf:
        app = ET.fromstring(zf.read("docProps/app.xml"))
    tags = [child.tag.split("}")[-1] for child in app]
    assert tags.index("Slides") < tags.index("Application")


# ── repairing a plot filed before the app captured vector ────────────────────

@pytest.mark.qt
def test_a_png_only_plot_is_redrawn_so_the_deck_can_make_it_editable(
        qapp, tmp_path, monkeypatch):
    """The repair the export performs for an older record.

    A plot filed before the app captured vector is a PNG and nothing else, and
    no amount of reading it produces shapes. What the record does carry is the
    parameters that drew it, so the export replays those onto the tab, captures
    the live figure, and keeps the result on the record — the redraw is a one-off
    repair, not a cost paid on every export.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore
    from widgets.plot_card import PlotCard

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()

    card = PlotCard(figsize=(4, 3))
    ax = card.figure.add_subplot(1, 1, 1)
    ax.bar([0, 1, 2, 3], [3, 5, 2, 4])
    ax.set_title("Mean intensity per group")
    C.wire_save_to_gallery(ctx, card, "Bar Plots")
    # Replaying the parameters is the app's job and has its own tests; what this
    # one is about is that the redraw happens, is captured, and is kept.
    monkeypatch.setattr(C, "restore_saved_plot", lambda ctx_, pid: None)

    rec = SavedPlot(id="old1", created_at="2026-01-01T00:00:00", tab="Bar Plots",
                    title="Filed last week", png_filename="old1.png", params={})
    assert store.add(ctx, rec, _png_bytes())
    assert rec.svg_filename == "", "the record starts with no vector capture"

    redrawn = C.regenerate_missing_vectors(ctx, store, [rec])

    assert redrawn == {"old1"}
    assert rec.svg_filename, "the redraw should have been kept on the record"
    assert (tmp_path / "saved_plots" / rec.svg_filename).exists()

    out = tmp_path / "deck.pptx"
    result = build_saved_plots_deck(
        [rec], lambda r: store.vector_path(ctx, r),
        lambda r: store.image_path(ctx, r), out, regenerated=redrawn)

    assert (result.vector, result.raster) == (1, 0)
    assert result.redrawn == 1
    text = _slide_text(out, 2)
    assert "Mean intensity per group" in text          # editable, as shapes
    # ...and the slide says the figure was redrawn rather than captured, because
    # it reflects today's data rather than the image that was saved.
    assert "redrawn from the plot's saved settings" in text
    assert "redrawn from saved settings" in result.summary()


@pytest.mark.qt
def test_a_plot_that_already_has_vector_is_never_redrawn(qapp, tmp_path):
    """The capture taken when the user saved is the better record of what they
    saw, so it is never replaced by a later redraw."""
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    rec = SavedPlot(id="new1", created_at="2026-01-01T00:00:00", tab="Bar Plots",
                    title="Saved today", png_filename="new1.png",
                    svg_filename="new1.svg", params={})
    assert store.add(ctx, rec, _png_bytes(), _bar_svg())
    original = (tmp_path / "saved_plots" / "new1.svg").read_bytes()

    assert C.regenerate_missing_vectors(ctx, store, [rec]) == set()
    assert (tmp_path / "saved_plots" / "new1.svg").read_bytes() == original
    assert store.attach_vector(ctx, rec, b"<svg/>") is False


@pytest.mark.qt
def test_a_tab_with_no_figure_is_left_as_a_picture(qapp, tmp_path):
    """The Image Table composes its snapshot from microscopy frames rather than
    drawing a chart, so it has never had vector and cannot acquire it here."""
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    # A card for a *different* tab, so ``cards_by_tab`` is non-empty and the
    # Image Table record actually falls through the per-record "no card" skip.
    # Without it the function returned at the empty-map guard and this test
    # passed against a deleted skip branch.
    card, _ = _card_that_draws_what_it_is_told(ctx)
    assert ctx.runtime.saved_plots.cards_by_tab

    rec = SavedPlot(id="grid", created_at="2026-01-01T00:00:00",
                    tab="Image Table", title="Grid", png_filename="grid.png",
                    params={})
    assert store.add(ctx, rec, _png_bytes())

    assert C.regenerate_missing_vectors(ctx, store, [rec]) == set()
    assert rec.svg_filename == ""


# ── what the redraw must actually guarantee ──────────────────────────────────
#
# The feature shipped with one test that monkeypatched `restore_saved_plot` to a
# no-op, so it proved the plumbing — bytes captured, file written, note on the
# slide — and nothing about the record. Deleting the replay entirely left all 44
# tests green. Everything below is about the part that monkeypatch removed.

def _card_that_draws_what_it_is_told(ctx):
    """A real PlotCard plus a stand-in restore that redraws it per record.

    The stand-in is the point: it makes "which record was replayed" visible in
    the captured bytes, so the assertions can be about identity and ordering
    rather than about the mechanism.
    """
    from well_viewer.features.saved_plots import controller as C
    from widgets.plot_card import PlotCard

    card = PlotCard(figsize=(4, 3))
    ax = card.figure.add_subplot(1, 1, 1)

    def _replay(_ctx, plot_id):
        ax.clear()
        ax.bar([0, 1], [3, 5])
        ax.set_title(f"figure for {plot_id}")

    C.wire_save_to_gallery(ctx, card, "Bar Plots")
    return card, _replay


@pytest.mark.qt
def test_each_record_is_captured_from_its_own_redrawn_figure(qapp, tmp_path,
                                                             monkeypatch):
    """Record N's stored capture must be record N's figure.

    Two things fail this and neither is exotic: capturing before the replay runs
    (every record gets whatever was on screen), and capturing after the *next*
    record's replay (every record gets its successor's figure). Both put a real
    figure on a slide under the wrong plot's title and parameters, which is the
    failure this repository cares most about — wrong output that looks right.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card, replay = _card_that_draws_what_it_is_told(ctx)
    monkeypatch.setattr(C, "restore_saved_plot", replay)

    records = []
    for pid in ("p1", "p2", "p3"):
        rec = SavedPlot(id=pid, created_at="2026-01-01T00:00:00", tab="Bar Plots",
                        title=f"Plot {pid}", png_filename=f"{pid}.png", params={})
        assert store.add(ctx, rec, _png_bytes())
        records.append(rec)

    assert C.regenerate_missing_vectors(ctx, store, records) == {"p1", "p2", "p3"}

    for rec in records:
        stored = (tmp_path / "saved_plots" / rec.svg_filename).read_text()
        assert f"figure for {rec.id}" in stored, (
            f"{rec.id} was captured from another record's figure")
        for other in records:
            if other is not rec:
                assert f"figure for {other.id}" not in stored


@pytest.mark.qt
def test_the_gating_pass_finishes_before_the_figure_is_captured(qapp, tmp_path,
                                                               monkeypatch):
    """`restore_saved_plot` applies gating by kicking a worker thread.

    Capturing on the next line reads the *previous* pass's Included flags, so the
    figure is drawn under a gating that is neither the record's nor the user's —
    and the slide says it "reflects the current data". The recompute has to
    finish on this thread, between the replay and the capture, or the number on
    the slide is wrong with a confident caption.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card, replay = _card_that_draws_what_it_is_told(ctx)
    order = []
    monkeypatch.setattr(C, "restore_saved_plot",
                        lambda c, pid: (order.append("replay"), replay(c, pid))[1])
    monkeypatch.setattr(C, "_gating_recompute_now",
                        lambda c: (order.append("gating"), True)[1])
    real_capture = type(card).captureVectorSvg
    monkeypatch.setattr(type(card), "captureVectorSvg",
                        lambda self: (order.append("capture"),
                                      real_capture(self))[1])

    rec = SavedPlot(id="p1", created_at="2026-01-01T00:00:00", tab="Bar Plots",
                    title="Plot", png_filename="p1.png", params={})
    assert store.add(ctx, rec, _png_bytes())
    assert C.regenerate_missing_vectors(ctx, store, [rec]) == {"p1"}

    assert order == ["replay", "gating", "capture"], (
        "the gating pass must settle between the replay and the capture")


@pytest.mark.qt
def test_the_export_puts_the_user_s_own_work_back(qapp, tmp_path, monkeypatch):
    """Replaying a record replaces the user's replicate sets, gating and stats.

    `view_state.snapshot` captures none of the three, so restoring only what it
    knows about left them replaced by the last regenerated plot's — permanently,
    and with the clobbered gating reaching `pipeline_info.json` on the user's
    next gate edit. An export is not a navigation and must not cost them work.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card, _ = _card_that_draws_what_it_is_told(ctx)

    mine = [{"id": "sel-mine", "name": "my replicates", "wells": ["A01", "A02"]}]
    ctx.state.selection.selections = [dict(s) for s in mine]
    ctx.state.selection.current_selection_id = "sel-mine"
    ctx.state.cell_gating.fluor_gates["gfp"] = 123.0

    def _replay_that_clobbers(_ctx, plot_id):
        # What the real restore does: replace, wholesale.
        _ctx.state.selection.selections = [{"id": "sel-theirs",
                                            "name": "the plot's", "wells": []}]
        _ctx.state.selection.current_selection_id = "sel-theirs"
        _ctx.state.cell_gating.fluor_gates.clear()
        _ctx.state.cell_gating.fluor_gates["rfp"] = 999.0

    monkeypatch.setattr(C, "restore_saved_plot", _replay_that_clobbers)
    monkeypatch.setattr(C, "_gating_recompute_now", lambda c: True)

    rec = SavedPlot(id="p1", created_at="2026-01-01T00:00:00", tab="Bar Plots",
                    title="Plot", png_filename="p1.png", params={})
    assert store.add(ctx, rec, _png_bytes())
    C.regenerate_missing_vectors(ctx, store, [rec])

    assert [s["id"] for s in ctx.state.selection.selections] == ["sel-mine"]
    assert ctx.state.selection.current_selection_id == "sel-mine"
    assert ctx.state.cell_gating.fluor_gates.get("gfp") == 123.0
    assert "rfp" not in ctx.state.cell_gating.fluor_gates


@pytest.mark.qt
def test_a_repair_that_cannot_be_undone_never_starts(qapp, tmp_path,
                                                     monkeypatch):
    """If the live state cannot be snapshotted, nothing is replayed.

    The old code let the snapshot fail into `None` and skipped the restore in
    silence, so a session whose state could not be saved was the one session
    that got its state replaced. Declining is the only safe answer, and it is
    said out loud rather than left as a deck that is quietly less editable.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card, replay = _card_that_draws_what_it_is_told(ctx)
    replayed = []
    monkeypatch.setattr(C, "restore_saved_plot",
                        lambda c, pid: replayed.append(pid))
    monkeypatch.setattr(C.view_state, "snapshot",
                        lambda c: (_ for _ in ()).throw(RuntimeError("no")))

    rec = SavedPlot(id="p1", created_at="2026-01-01T00:00:00", tab="Bar Plots",
                    title="Plot", png_filename="p1.png", params={})
    assert store.add(ctx, rec, _png_bytes())

    assert C.regenerate_missing_vectors(ctx, store, [rec]) == set()
    assert replayed == [], "nothing may be replayed without a way back"
    assert rec.svg_filename == ""
    assert ctx.said and "could not be saved and restored" in ctx.said[0]


def test_a_rasterized_data_layer_is_never_reported_as_fully_editable(tmp_path):
    """The figure is editable and its data is not, and the slide must say so.

    An embedded image is what a heat map legitimately contains, so nothing about
    the picture itself distinguishes "this figure includes a raster block" from
    "this figure's data became a raster block". Without a marker the deck read
    the second as the first and reported the whole thing as editable — the
    cardinal rule, broken by the fix for a different defect.
    """
    import numpy as np
    from matplotlib.figure import Figure

    from widgets.fig_clipboard import rasterized_layer_count, render_figure_svg

    rng = np.random.default_rng(0)
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot()
    ax.scatter(rng.random(2000), rng.random(2000), s=1)
    ax.set_title("Cells")
    svg = tmp_path / "dense.svg"
    svg.write_bytes(render_figure_svg(fig, max_paths=500))
    assert rasterized_layer_count(svg.read_bytes()) == 1

    out = tmp_path / "deck.pptx"
    result = build_saved_plots_deck([_rec("1")], lambda r: svg, lambda r: None,
                                    out)

    assert result.vector == 1 and result.rasterized == 1
    assert "all editable" not in result.summary()
    assert "picture for the data layer" in result.summary()
    text = _slide_text(out, 2)
    assert "too dense to redraw mark by mark" in text
    assert "Cells" in text, "the rest of the figure is still editable text"


def test_a_figure_with_nothing_rasterized_carries_no_marker(tmp_path):
    """A capture with no marker is one whose every mark is a shape."""
    from widgets.fig_clipboard import rasterized_layer_count

    svg = tmp_path / "plain.svg"
    svg.write_bytes(_bar_svg())
    assert rasterized_layer_count(svg.read_bytes()) == 0

    out = tmp_path / "deck.pptx"
    result = build_saved_plots_deck([_rec("1")], lambda r: svg, lambda r: None,
                                    out)
    assert result.rasterized == 0
    assert result.summary().endswith("all editable")


def test_two_shortfalls_on_one_slide_do_not_print_on_top_of_each_other(tmp_path):
    """A slide can have more than one thing to say, and used to say neither.

    Both notes were written at the same fixed height, so the slide with the most
    to report was the one that reported none of it legibly — the shortfall hidden
    by the very line meant to describe it, in geometry rather than in prose.
    """
    import numpy as np
    from matplotlib.figure import Figure

    from widgets.fig_clipboard import render_figure_svg

    # Dense enough to rasterize, and hatched so a construct is also dropped.
    rng = np.random.default_rng(0)
    fig = Figure(figsize=(4, 3))
    ax = fig.add_subplot()
    ax.scatter(rng.random(2000), rng.random(2000), s=1)
    ax.bar([0.2, 0.6], [0.4, 0.7], width=0.1, hatch="//", color="white",
           edgecolor="k")
    svg = tmp_path / "both.svg"
    svg.write_bytes(render_figure_svg(fig, max_paths=500))
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck([_rec("1")], lambda r: svg, lambda r: None,
                                    out, regenerated={"1"})
    assert result.rasterized == 1 and result.redrawn == 1

    slide = _slide_xml(out, 2)
    notes = [sp for sp in slide.iter(f"{_P}sp")
             if (sp.find(f".//{_P}cNvPr") is not None
                 and "Export note" in (sp.find(f".//{_P}cNvPr").get("name") or ""))]
    assert len(notes) >= 2, "this slide has more than one shortfall to report"
    tops = [int(x.get("y")) for n in notes for x in n.iter(f"{_A}off")]
    assert len(set(tops)) == len(tops), "two notes were written at the same height"


@pytest.mark.qt
def test_a_single_panel_record_is_not_regenerated_as_the_whole_figure(
        qapp, tmp_path, monkeypatch):
    """A redraw renders the whole card, and a panel record is one panel of it.

    Regenerating one would put every panel on the slide under the panel's title
    and beside the panel's parameters — a different figure, not a worse one. A
    picture of the right plot beats shapes of the wrong one.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card, replay = _card_that_draws_what_it_is_told(ctx)
    monkeypatch.setattr(C, "restore_saved_plot", replay)
    monkeypatch.setattr(C, "_gating_recompute_now", lambda c: True)

    panel = SavedPlot(id="panel", created_at="2026-01-01T00:00:00",
                      tab="Bar Plots", title="Panel one",
                      png_filename="panel.png", params={}, panel_only=True)
    whole = SavedPlot(id="whole", created_at="2026-01-01T00:00:00",
                      tab="Bar Plots", title="Everything",
                      png_filename="whole.png", params={})
    for rec in (panel, whole):
        assert store.add(ctx, rec, _png_bytes())

    assert C.regenerate_missing_vectors(ctx, store, [panel, whole]) == {"whole"}
    assert panel.svg_filename == "", "a panel record must stay a picture"
    assert whole.svg_filename, "a whole-figure record is repaired as usual"


def test_a_panel_record_round_trips_through_persistence(tmp_path):
    """The flag has to survive the JSON, or the next export substitutes again."""
    from well_viewer.features.saved_plots.state import SavedPlot

    rec = SavedPlot(id="p", created_at="", tab="Bar Plots", title="t",
                    png_filename="p.png", params={}, panel_only=True)
    assert SavedPlot.from_dict(rec.to_dict()).panel_only is True
    whole = SavedPlot(id="w", created_at="", tab="Bar Plots", title="t",
                      png_filename="w.png", params={}, panel_only=False)
    assert SavedPlot.from_dict(whole.to_dict()).panel_only is False

    # A record written before the flag existed reads as **unknown**, not as a
    # whole-figure save. This assertion used to pin the opposite, which is the
    # guess that put a user's three-panel window on a slide in place of the one
    # panel they had saved: absent and False are different facts, and only one
    # of them is safe to redraw from.
    old = {k: v for k, v in rec.to_dict().items() if k != "panel_only"}
    assert SavedPlot.from_dict(old).panel_only is None


@pytest.mark.qt
def test_a_capture_that_could_not_be_written_still_makes_this_deck_editable(
        qapp, tmp_path, monkeypatch):
    """The file on disk is the repair; the bytes are what this export needs.

    A failed write — a full disk, a read-only folder — used to throw away both,
    so the export fell back to a picture for a figure it had already rendered.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card, replay = _card_that_draws_what_it_is_told(ctx)
    monkeypatch.setattr(C, "restore_saved_plot", replay)
    monkeypatch.setattr(C, "_gating_recompute_now", lambda c: True)

    rec = SavedPlot(id="p1", created_at="2026-01-01T00:00:00", tab="Bar Plots",
                    title="Plot", png_filename="p1.png", params={})
    assert store.add(ctx, rec, _png_bytes())
    monkeypatch.setattr(store, "attach_vector",
                        lambda *a, **k: False)     # the write fails

    assert C.regenerate_missing_vectors(ctx, store, [rec]) == {"p1"}
    assert rec.svg_filename == "", "nothing was persisted, so nothing is claimed"
    assert store.held_vector(rec).startswith(b"<?xml")

    out = tmp_path / "deck.pptx"
    result = build_saved_plots_deck(
        [rec], lambda r: store.vector_path(ctx, r) or store.held_vector(r),
        lambda r: store.image_path(ctx, r), out, regenerated={"p1"})
    assert (result.vector, result.raster) == (1, 0)
    assert "figure for p1" in _slide_text(out, 2)


# ── a record that does not say what it saved ─────────────────────────────────

def _multi_panel_card(ctx, panels: int = 3):
    from well_viewer.features.saved_plots import controller as C
    from widgets.plot_card import PlotCard

    card = PlotCard(figsize=(5, 8))
    axes = [card.figure.add_subplot(panels, 1, i + 1) for i in range(panels)]
    for i, ax in enumerate(axes):
        ax.bar([0, 1, 2, 3], [3, 5, 2, 4])
        ax.set_title(f"PANEL {i + 1}")
    C.wire_save_to_gallery(ctx, card, "Bar Plots")
    return card, axes


def _png_of(card, bbox=None, hide=None):
    import io

    from widgets.fig_clipboard import hidden_sibling_axes

    buf = io.BytesIO()
    if hide is not None:
        with hidden_sibling_axes(card.figure, hide):
            card.figure.savefig(buf, format="png", bbox_inches=bbox, dpi=120)
    else:
        card.figure.savefig(buf, format="png", bbox_inches=bbox, dpi=120)
    return buf.getvalue()


@pytest.mark.qt
def test_an_old_panel_record_is_not_redrawn_as_the_whole_figure(qapp, tmp_path):
    """The defect a user hit: one saved panel came back as three.

    A record written before the panel flag existed carries no scope, and
    defaulting it to "whole figure" was a guess. Redrawing such a record renders
    the whole card — a different figure, under the panel's title and beside the
    panel's parameters. The record's own stored image says otherwise: one panel
    of a three-panel card is about three times as wide for its height.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore
    from widgets.fig_clipboard import axis_tight_bbox_inches

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card, axes = _multi_panel_card(ctx)

    rec = SavedPlot(id="old_panel", created_at="2026-01-01T00:00:00",
                    tab="Bar Plots", title="PANEL 1",
                    png_filename="old_panel.png", params={})
    rec.panel_only = None                    # written before the flag existed
    bbox = axis_tight_bbox_inches(axes[0], pad_in=0.05)
    assert store.add(ctx, rec, _png_of(card, bbox, hide=axes[0]))

    assert C._redraw_keeps_the_scope(ctx, store, rec, card) is False
    assert C.regenerate_missing_vectors(ctx, store, [rec]) == set()
    assert rec.svg_filename == "", "the panel record must stay a picture"


@pytest.mark.qt
def test_an_unknown_record_on_a_single_panel_figure_is_redrawn(qapp, tmp_path):
    """Being conservative must not swallow the records the repair is for.

    On a single-axis figure a redraw and a per-panel save are the same picture,
    so the record's unknown scope cannot change what lands on the slide and the
    repair is safe whatever it meant.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore
    from widgets.plot_card import PlotCard

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card = PlotCard(figsize=(5, 4))
    card.figure.add_subplot(1, 1, 1).bar([0, 1, 2, 3], [3, 5, 2, 4])
    C.wire_save_to_gallery(ctx, card, "Bar Plots")

    rec = SavedPlot(id="old_single", created_at="2026-01-01T00:00:00",
                    tab="Bar Plots", title="One plot",
                    png_filename="old_single.png", params={})
    rec.panel_only = None
    assert store.add(ctx, rec, _png_of(card, "tight"))

    assert C._redraw_keeps_the_scope(ctx, store, rec, card) is True


@pytest.mark.qt
def test_an_unknown_record_on_a_multi_panel_figure_is_left_alone(qapp, tmp_path):
    """And on a multi-panel figure it is not knowable, so it is not redrawn.

    This is the deliberate cost: an old *whole-figure* record from a multi-panel
    tab stays a picture too, because nothing in the record distinguishes it from
    a panel save. That is the right way to be wrong — re-saving makes it
    editable, and nothing is misrepresented in the meantime. The alternative,
    guessing from the stored image's aspect ratio, is not evidence at all: a
    plot card takes its size from the widget, so opening the Properties sidebar
    alone moves the live figure's aspect by more than 40%.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card, _ = _multi_panel_card(ctx)

    rec = SavedPlot(id="old_whole", created_at="2026-01-01T00:00:00",
                    tab="Bar Plots", title="All panels",
                    png_filename="old_whole.png", params={})
    rec.panel_only = None
    assert store.add(ctx, rec, _png_of(card, "tight"))

    assert C._redraw_keeps_the_scope(ctx, store, rec, card) is False


@pytest.mark.qt
def test_a_record_that_states_its_scope_is_believed(qapp, tmp_path):
    """A record that says what it saved is taken at its word — the panel count
    is only consulted when the record does not say."""
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card, _ = _multi_panel_card(ctx)

    # Says "one panel" on a card whose panel count would otherwise allow a
    # redraw: the record's own statement wins.
    stated = SavedPlot(id="stated", created_at="", tab="Bar Plots", title="P",
                       png_filename="stated.png", params={}, panel_only=True)
    assert store.add(ctx, stated, _png_of(card, "tight"))
    assert C._redraw_keeps_the_scope(ctx, store, stated, card) is False


@pytest.mark.qt
def test_an_unreadable_figure_leaves_the_plot_as_a_picture(qapp, tmp_path):
    """A card whose panels cannot be counted is not redrawn from."""
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _stub_ctx(tmp_path)
    store = SavedPlotsStore()
    card, _ = _multi_panel_card(ctx)
    rec = SavedPlot(id="broken", created_at="", tab="Bar Plots", title="P",
                    png_filename="broken.png", params={})
    rec.panel_only = None
    assert store.add(ctx, rec, b"not-a-png")
    assert C._redraw_keeps_the_scope(ctx, store, rec, card) is False


# ── the progress bar ─────────────────────────────────────────────────────────

def _bar_ctx(tmp_path):
    """A ctx with a real progress bar registered, as the shell has."""
    from PySide6.QtWidgets import QProgressBar, QWidget

    ctx = _stub_ctx(tmp_path)
    ctx.bar_host = QWidget()                       # held: Qt owns the child
    bar = QProgressBar(ctx.bar_host)
    bar.hide()
    ctx.widgets.register("_progress_bar", bar)
    return ctx


@pytest.mark.qt
def test_the_export_reports_progress_across_both_of_its_phases(qapp, tmp_path,
                                                               monkeypatch):
    """An export blocks the GUI thread for as long as it takes.

    It replays old plots onto their tabs, renders each to vector, parses every
    capture and writes a zip — all between the file dialog closing and the file
    appearing. Without a bar that is a frozen window with no account of why, and
    the redraw phase moves the app from tab to tab while it runs.

    Asserted across **both** phases: a bar that covered only the deck build
    would sit at zero through the slow half.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot

    ctx = _bar_ctx(tmp_path)
    store = C.get_store(ctx)
    store.ensure_loaded(ctx)
    card, replay = _card_that_draws_what_it_is_told(ctx)
    monkeypatch.setattr(C, "restore_saved_plot", replay)
    monkeypatch.setattr(C, "_gating_recompute_now", lambda c: True)
    monkeypatch.setattr(C, "_export_destination",
                        lambda c, **kw: str(tmp_path / "deck.pptx"))

    for i in range(3):
        store.add(ctx, SavedPlot(id=f"old{i}", created_at="", tab="Bar Plots",
                                 title=f"Old plot {i}",
                                 png_filename=f"old{i}.png", params={}),
                  _png_bytes())

    seen = []
    real = C._step_progress
    monkeypatch.setattr(C, "_step_progress",
                        lambda c, d, t, m="": (seen.append((d, t, m)),
                                               real(c, d, t, m))[1])
    C.export_deck_pptx(ctx)

    assert seen, "the export must report progress"
    total = seen[0][1]
    # Both phases move it: three redraws, then a step per slide, then the write.
    redraw_steps = [m for _, _, m in seen if "Redrawing" in m]
    slide_steps = [m for _, _, m in seen if "Building slide" in m]
    write_steps = [m for _, _, m in seen if "Writing" in m]
    assert len(redraw_steps) == 3
    assert len(slide_steps) == 3
    assert len(write_steps) == 1
    # It reaches the end, rather than stopping short and vanishing — which reads
    # as an export that gave up.
    assert seen[-1][0] == total
    # And the bar is put away on the way out. ``isHidden`` rather than
    # ``isVisible``: a child of an unshown parent is never "visible", so
    # ``not isVisible()`` is true whether or not anything hid it — an assertion
    # that cannot fail. ``isHidden`` reflects the explicit hide.
    assert ctx.widgets.get_or_none("_progress_bar").isHidden()


@pytest.mark.qt
def test_a_skipped_plot_still_advances_the_bar(qapp, tmp_path, monkeypatch):
    """A record left alone still took a turn.

    A bar that only moved on a successful redraw would sit still through a
    gallery of plots that are all being skipped — which is the common case for
    an older gallery of multi-panel figures, and looks exactly like a hang.
    """
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = _bar_ctx(tmp_path)
    store = SavedPlotsStore()
    _multi_panel_card(ctx)          # multi-panel: an unknown record is skipped

    records = []
    for i in range(3):
        rec = SavedPlot(id=f"old{i}", created_at="", tab="Bar Plots",
                        title=f"Old {i}", png_filename=f"old{i}.png", params={})
        rec.panel_only = None
        assert store.add(ctx, rec, _png_bytes())
        records.append(rec)

    seen = []
    assert C.regenerate_missing_vectors(
        ctx, store, records,
        on_progress=lambda done, title: seen.append(done)) == set()
    assert seen == [1, 2, 3], "every record must advance the count, skips too"


@pytest.mark.qt
def test_the_bar_is_hidden_even_when_the_export_fails(qapp, tmp_path,
                                                      monkeypatch):
    """A bar left running after an export died is its own false statement."""
    from well_viewer.features.saved_plots import controller as C
    from well_viewer.features.saved_plots.state import SavedPlot

    ctx = _bar_ctx(tmp_path)
    store = C.get_store(ctx)
    store.ensure_loaded(ctx)
    store.add(ctx, SavedPlot(id="p1", created_at="", tab="Bar Plots",
                             title="P", png_filename="p1.png", params={},
                             svg_filename=""), _png_bytes())
    monkeypatch.setattr(C, "_export_destination",
                        lambda c, **kw: str(tmp_path / "deck.pptx"))
    monkeypatch.setattr(C, "build_saved_plots_deck",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    C.export_deck_pptx(ctx)

    # ``isHidden``, not ``not isVisible()`` — see the note in the test above.
    assert ctx.widgets.get_or_none("_progress_bar").isHidden()
    assert any("failed" in m.lower() for m in ctx.said), \
        "the failure must reach the status line"
