"""An Image Table grid reaches PowerPoint as one editable frame per field.

Before this, a grid saved to Saved Plots stored a PNG and nothing else, so the
deck builder had no capture to draw shapes from and placed the whole plate as a
single picture. Every overlay was pixels too: ``draw_cell_chrome`` burns the
identity label and the scale bar into each cell before the figure ever sees it.
The result was one flat raster per slide — nothing to select, nothing to retype,
no way to pull one field out.

These tests pin the replacement end to end, and each is written against the
wrong implementation that would otherwise pass it:

* the frame's picture is the field **before** the chrome was burned in — a
  collector that recorded the drawn cell instead would still produce a frame per
  field, and would still say the label is "in" the plan, while the label the
  user tried to edit was baked into the picture underneath it;
* the scale bar's length is measured against the **source** pixels, not the
  downsampled ones the deck carries — the fixture pools the picture hard enough
  that the two answers differ, so a bar measured off the wrong array comes out a
  different physical length;
* the deck really is groups of shapes — asserted by reading the written
  ``.pptx`` back, not by trusting the builder's own counters.

Fast lane: the export builder runs on the same plain-data fake ctx
``test_image_table_facet_export`` uses, so no ``WellViewerApp`` is built.
"""
from __future__ import annotations

import zipfile
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("matplotlib")
np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from well_viewer.domain import image_frames as IF
from well_viewer.features.image_browser.image_table import export as E
from well_viewer.features.image_browser.image_table import image_compose as IC
from well_viewer.features.image_browser.image_table._frames import FrameCollector

_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}

#: Source cells are this many pixels on a side, and the frame's picture is
#: pooled well below it. A mark measured against the pooled array would land in
#: a different place, which is the whole point of the gap.
SRC_PX = 320
POOLED_PX = 80


def _cell(well, chan, tp, fov="1"):
    return {"well": well, "chan": chan, "tp": tp, "fov": fov}


def _cells():
    return [[_cell("A01", "GFP", "0h", "1"), _cell("A01", "GFP", "0h", "2")]]


def _source_rgb(seed: int):
    """A field with structure, so a pooled copy is not the same as the original."""
    rng = np.random.default_rng(seed)
    return (rng.random((SRC_PX, SRC_PX, 3)) * 255).astype(np.uint8)


def _export_ctx(cells, *, bar_um=0.0, pixel_um=0.0, label_mode="identity"):
    from well_viewer.features.image_browser.image_table.state import ImageTableState

    st = ImageTableState()
    st.row_dims, st.col_dims = ["well"], ["field"]
    st.label_mode = label_mode
    st.label_cells = "all"
    st.label_img_corner = "tl"
    st.label_font_px = 12
    st.label_color = "white"
    st.label_bg = True
    st.scale_bar_show = bool(bar_um)
    st.scale_bar_um = bar_um
    st.pixel_size_um = pixel_um
    st.scale_bar_cells = "all"
    st.scale_bar_img_corner = "br"
    st.scale_bar_color = "white"
    st.scale_bar_thickness_px = 4
    return SimpleNamespace(
        window=None,
        state=SimpleNamespace(
            image_table=st,
            dataset=SimpleNamespace(well_labels={}, data_dir=None),
            channel=SimpleNamespace(ratio_metrics=[]),
        ),
        runtime=SimpleNamespace(
            image_table=SimpleNamespace(cells=cells, crop_tool=None)),
        notify=SimpleNamespace(set_status=lambda *_a, **_k: None),
    )


def _install_sources(monkeypatch, sources):
    """Serve *sources* to the loader, one per distinct (well, field)."""
    seen = {}

    def _load(_ctx, _cache, well, chan, tp, fov, **_kw):
        return seen.setdefault((well, fov), sources[len(seen) % len(sources)])

    monkeypatch.setattr(E, "_load_array", _load)
    from well_viewer.features.image_browser.image_table import controller as ITC
    monkeypatch.setattr(ITC, "_prefetch_cell_arrays", lambda *a, **k: None)
    monkeypatch.setattr(
        E, "cell_display_rgb",
        lambda _ctx, _cache, cell, _r, _crop, arr: arr)


def _build(monkeypatch, ctx, cells, sources, *, show_titles=False,
           keep_figure=False):
    """Run the real export builder with a frame collector attached."""
    _install_sources(monkeypatch, sources)
    if show_titles:
        base = E._export_opts
        monkeypatch.setattr(
            E, "_export_opts",
            lambda c: dict(base(c), show_titles=True))
    collector = FrameCollector()
    fig, _kw = E._build_export_figure(ctx, cell_max_px=POOLED_PX,
                                      frames=collector)
    assert fig is not None
    return (collector.grid, fig) if keep_figure else collector.grid


# ── the picture in a frame is the field, not the field with its overlays ─────

def test_a_frame_carries_the_field_without_its_overlays_burned_in(monkeypatch):
    """The whole point: the label is text on the slide, not pixels under it.

    A collector that recorded the drawn cell would produce exactly the same
    frame count, the same rectangles and the same ``TextMark`` — and the label
    the user retyped would sit on top of a picture that still showed the old
    one. So this compares the frame's pixels against the *undecorated* source
    and requires them equal.
    """
    from PIL import Image

    source = _source_rgb(1)
    cells = _cells()
    ctx = _export_ctx(cells)
    grid = _build(monkeypatch, ctx, cells, [source])

    assert len(grid.frames) == 2
    frame = grid.frames[0]
    got = np.asarray(Image.open(__import__("io").BytesIO(frame.png)).convert("RGB"))
    want = np.asarray(E._area_pool_rgb(source, POOLED_PX))
    assert got.shape == want.shape
    assert np.array_equal(got, want)

    # And the same source *with* the chrome burned in is a different picture —
    # otherwise the assertion above would hold for either implementation.
    decorated = IC.draw_cell_chrome(
        E._area_pool_rgb(source, POOLED_PX), label_text="A01  GFP  T:0h  FOV:1",
        label_corner="tl", label_font_px=12, label_color="white", label_bg=True)
    assert not np.array_equal(np.asarray(decorated), want)


def test_every_field_becomes_its_own_frame_named_for_the_field(monkeypatch):
    cells = _cells()
    ctx = _export_ctx(cells)
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(2)])
    names = [f.name for f in grid.frames]
    assert names == ["A01  GFP  T:0h  FOV:1", "A01  GFP  T:0h  FOV:2"]
    # Distinct rectangles: two fields side by side, not two frames on one box.
    assert grid.frames[0].rect.x != grid.frames[1].rect.x


def test_the_identity_label_arrives_as_text(monkeypatch):
    cells = _cells()
    ctx = _export_ctx(cells)
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(3)])
    label = grid.frames[0].texts[0]
    assert label.text == "A01  GFP  T:0h  FOV:1"
    assert label.plate is True            # the translucent backing, as drawn
    assert label.color == "FFFFFF"
    # Inside the field it names, not floating beside it.
    rect, frame = label.rect, grid.frames[0].rect
    assert frame.x <= rect.x and rect.x + rect.w <= frame.x + frame.w + 1e-9
    assert frame.y <= rect.y and rect.y + rect.h <= frame.y + frame.h + 1e-9


def test_a_grid_with_labels_off_carries_no_label_marks(monkeypatch):
    cells = _cells()
    ctx = _export_ctx(cells, label_mode="none")
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(4)])
    assert all(not f.texts for f in grid.frames)


# ── the scale bar keeps the length it states ─────────────────────────────────

def test_the_scale_bar_is_measured_against_the_source_pixels(monkeypatch):
    """A 40 µm bar at 0.5 µm/px is 80 of 320 source pixels — a quarter of the field.

    Measured against the *pooled* 80 px picture the deck carries, the same
    80-pixel request would be the whole field. The fixture pools by 4x precisely
    so the right and the wrong answer differ by that factor; without the gap no
    assertion of any strength could tell them apart.
    """
    cells = _cells()
    ctx = _export_ctx(cells, bar_um=40.0, pixel_um=0.5)
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(5)])
    frame = grid.frames[0]
    assert len(frame.bars) == 1
    bar = frame.bars[0]
    assert bar.rect.w == pytest.approx(frame.rect.w * 80 / SRC_PX, rel=1e-6)
    # Bottom-right, where the setting puts it.
    assert bar.rect.x > frame.rect.x + frame.rect.w / 2
    assert bar.rect.y > frame.rect.y + frame.rect.h / 2


def test_a_bar_too_long_for_its_field_is_absent_from_the_frame(monkeypatch):
    """Refused in the deck exactly as in the picture.

    A bar clamped to fit would state a scale nobody measured, and it would look
    identical to a correct one. ``bar_fits`` refuses it for the raster; the
    frame has to refuse the same bar or the deck quietly reintroduces the very
    number the picture declines to draw.
    """
    cells = _cells()
    ctx = _export_ctx(cells, bar_um=400.0, pixel_um=0.5)   # 800 px in a 320 px field
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(6)])
    assert all(not f.bars for f in grid.frames)


# ── the axis labels stay outside the fields ──────────────────────────────────

def test_facet_labels_are_grid_level_marks_not_frame_children(monkeypatch):
    """Dragging one field must not take its row header with it."""
    cells = _cells()
    ctx = _export_ctx(cells)
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(7)])
    assert grid.labels, "the facet plan's row/column labels reached no mark"
    texts = {m.text for m in grid.labels}
    assert "A01" in texts
    # None of the grid-level labels is also inside a frame. (The earlier version
    # of this assertion ended in ``or True`` and could not fail.)
    framed = {id(t) for f in grid.frames for t in f.texts}
    assert not framed & {id(m) for m in grid.labels}


# ── the stored plan ──────────────────────────────────────────────────────────

def test_a_plan_survives_the_round_trip_through_its_zip(monkeypatch):
    cells = _cells()
    ctx = _export_ctx(cells, bar_um=40.0, pixel_um=0.5)
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(8)])
    back = IF.from_bytes(IF.to_bytes(grid))
    assert back.width_in == pytest.approx(grid.width_in)
    assert [f.name for f in back.frames] == [f.name for f in grid.frames]
    assert back.frames[0].texts == grid.frames[0].texts
    assert back.frames[0].bars == grid.frames[0].bars
    assert back.frames[0].png == grid.frames[0].png
    assert back.labels == grid.labels


def test_an_unreadable_plan_is_refused_rather_than_half_read():
    with pytest.raises(IF.FramedGridError):
        IF.from_bytes(b"not a zip")
    with pytest.raises(IF.FramedGridError):
        IF.from_bytes(b"")


def test_a_plan_from_a_newer_build_is_declined():
    """Half-reading it would place some fields right and drop the rest."""
    import io
    import json

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("grid.json", json.dumps(
            {"version": IF.FORMAT_VERSION + 1, "width_in": 4, "height_in": 3,
             "frames": [], "labels": []}))
    with pytest.raises(IF.FramedGridError):
        IF.from_bytes(buf.getvalue())


# ── the deck the plan produces ───────────────────────────────────────────────

def _slide_xml(path):
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(zf.read("ppt/slides/slide1.xml"))


def _groups(slide):
    return slide.findall(".//p:grpSp", _NS)


def _drawn_image_boxes(fig):
    """Each cell axes' drawn box, in the plan's inches-from-the-top-left.

    ``imshow(aspect="equal")`` shrinks the axes to the image's ratio at *draw*
    time, so this renders first and reads the box that resulted rather than the
    slot the axes was assigned.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from well_viewer.features.image_browser.image_table import renderer as R

    # An explicit Agg canvas, because ``apply_aspect`` — the thing that shrinks
    # an ``aspect="equal"`` axes to its image's ratio — only runs during a real
    # draw. Without one this reads the axes' assigned slot and reports the
    # letterboxing as though it had not happened, which is a measurement that
    # confirms whatever it is shown.
    FigureCanvasAgg(fig)
    fig.canvas.draw()
    fig_w, fig_h = (float(v) for v in fig.get_size_inches())
    boxes = []
    for ax in fig.axes:
        if ax.get_gid() == R.FACET_GID or not ax.images:
            continue
        box = ax.images[0].get_window_extent()
        dpi = float(fig.dpi)
        boxes.append((box.x0 / dpi, fig_h - box.y1 / dpi,
                      box.width / dpi, box.height / dpi))
    return sorted(boxes, key=lambda b: (round(b[1], 3), round(b[0], 3)))


def _ext(shape):
    """``(cx, cy)`` in EMU off a shape's own transform."""
    ext = shape.find(".//a:xfrm/a:ext", _NS)
    return int(ext.get("cx")), int(ext.get("cy"))


def _off(shape):
    """``(x, y)`` in EMU off a shape's own transform."""
    off = shape.find(".//a:xfrm/a:off", _NS)
    return int(off.get("x")), int(off.get("y"))


def test_the_deck_places_one_group_per_field_holding_picture_and_text(
        monkeypatch, tmp_path):
    """Read back out of the written file, not off the builder's own counters."""
    from well_viewer.features.saved_plots._deck import build_image_grid_deck

    cells = _cells()
    ctx = _export_ctx(cells, bar_um=40.0, pixel_um=0.5)
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(9), _source_rgb(11)])
    out = tmp_path / "grid.pptx"
    assert build_image_grid_deck(grid, out, title="Image grid (1×2)") == 2

    slide = _slide_xml(out)
    groups = _groups(slide)
    assert len(groups) == 2
    for index, group in enumerate(groups):
        assert len(group.findall("p:pic", _NS)) == 1          # the field
        runs = [t.text for t in group.findall(".//a:t", _NS)]
        assert any("A01" in (r or "") for r in runs), runs    # editable label

        # The scale bar, measured in the EMU that actually reach the slide.
        # Every text run carries a ``solidFill`` of its own, so "some shape in
        # this group has a solid fill" is satisfied by the label alone — with
        # that assertion, deleting the bar shape outright and drawing it at half
        # length both left this file green. The bar is the shape with no text in
        # it, and what it has to be right about is its length.
        bars = [sp for sp in group.findall("p:sp", _NS)
                if not sp.findall(".//a:t", _NS)]
        assert len(bars) == 1, "no textless shape in the group — the scale bar"
        want = grid.frames[index].bars[0].rect.w / grid.frames[index].rect.w
        assert _ext(bars[0])[0] == pytest.approx(_ext(group)[0] * want, rel=2e-3)

        # The label's translucent plate: a fill on the *shape*, not on a run.
        label = next(sp for sp in group.findall("p:sp", _NS)
                     if any("A01" in (t.text or "")
                            for t in sp.findall(".//a:t", _NS)))
        fill = label.find("p:spPr/a:solidFill/a:srgbClr", _NS)
        assert fill is not None, "the label lost the plate the picture draws"
        assert fill.find("a:alpha", _NS) is not None
    # Each field carries its own image part rather than one shared raster.
    with zipfile.ZipFile(out) as zf:
        assert len([n for n in zf.namelist() if n.startswith("ppt/media/")]) == 2


def test_a_deck_of_frames_is_a_valid_package_a_reader_can_walk(
        monkeypatch, tmp_path):
    """Structurally valid to something other than this module's own writer."""
    pptx = pytest.importorskip("pptx")

    from well_viewer.features.saved_plots._deck import build_image_grid_deck

    cells = _cells()
    ctx = _export_ctx(cells, bar_um=40.0, pixel_um=0.5)
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(10), _source_rgb(12)])
    out = tmp_path / "grid.pptx"
    build_image_grid_deck(grid, out)

    deck = pptx.Presentation(str(out))
    shapes = list(deck.slides[0].shapes)
    groups = [s for s in shapes if s.shape_type == pptx.enum.shapes.MSO_SHAPE_TYPE.GROUP]
    assert len(groups) == 2
    inner = list(groups[0].shapes)
    kinds = {s.shape_type for s in inner}
    assert pptx.enum.shapes.MSO_SHAPE_TYPE.PICTURE in kinds
    assert pptx.enum.shapes.MSO_SHAPE_TYPE.TEXT_BOX in kinds
    assert any("A01" in s.text_frame.text
               for s in inner if s.has_text_frame)


# ── the gallery route: a stored plan wins over the flat picture ──────────────

def _grid_bytes(monkeypatch):
    cells = _cells()
    ctx = _export_ctx(cells, bar_um=40.0, pixel_um=0.5)
    return IF.to_bytes(_build(monkeypatch, ctx, cells,
                              [_source_rgb(20), _source_rgb(21)]))


def _flat_png() -> bytes:
    """A stand-in for the grid snapshot the record has always stored."""
    import io

    import matplotlib.image as mpimg

    buf = io.BytesIO()
    mpimg.imsave(buf, (np.linspace(0, 1, 300).reshape(10, 10, 3) * 255)
                 .astype("uint8"), format="png")
    return buf.getvalue()


def _record(**kw):
    base = dict(id="g1", title="Image grid (1×2)", tab="Image Table",
                created_at="2026-01-01T10:00:00", summary="Tab: Image Table",
                svg_filename="", frames_filename="g1.frames.zip")
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_gallery_deck_places_a_stored_grid_as_frames_not_a_picture(
        monkeypatch, tmp_path):
    """The defect this whole change is about, asserted from the written file.

    Placing the flat snapshot would also produce a slide with the plot's title
    on it and a picture under it, and would also return a ``DeckResult`` with
    one plot on it. What separates the two is that the frames slide has a group
    per field and no single full-width raster — so that is what is asserted,
    together with the counters that have to agree with it.
    """
    from well_viewer.features.saved_plots._deck import build_saved_plots_deck

    plan = tmp_path / "g1.frames.zip"
    plan.write_bytes(_grid_bytes(monkeypatch))
    png = tmp_path / "g1.png"
    png.write_bytes(_flat_png())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck(
        [_record()], lambda r: None, lambda r: png, out,
        frames_path_for=lambda r: plan)

    assert (result.framed, result.raster, result.missing) == (1, 0, 0)
    assert result.vector == 1
    slide = _slide_xml_at(out, 2)
    assert len(slide.findall(".//p:grpSp", _NS)) == 2
    # Two field pictures, and no third picture placed at the top level — a
    # fallback that ran anyway would leave the flat snapshot on the slide too.
    assert len(slide.findall(".//p:pic", _NS)) == 2
    assert len(slide.findall("./p:cSld/p:spTree/p:pic", _NS)) == 0
    assert "one group per field" in result.summary()


def test_a_grid_whose_plan_is_unreadable_falls_back_to_the_picture(
        monkeypatch, tmp_path):
    """Losing the plan must lose the frames, not the slide."""
    from well_viewer.features.saved_plots._deck import build_saved_plots_deck

    plan = tmp_path / "g1.frames.zip"
    plan.write_bytes(b"this is not a zip")
    png = tmp_path / "g1.png"
    png.write_bytes(_flat_png())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck(
        [_record()], lambda r: None, lambda r: png, out,
        frames_path_for=lambda r: plan)

    assert (result.framed, result.raster) == (0, 1)
    assert "as a picture" in result.summary()


def test_a_record_with_a_frame_plan_is_not_queued_for_a_redraw():
    """It is already editable; redrawing it would be work thrown away.

    A grid has no chart behind it, so the redraw would be skipped anyway — but
    it would still be counted into the progress bar and still walk the record
    onto its tab first.
    """
    from well_viewer.features.saved_plots.controller import _wants_redraw

    assert _wants_redraw(_record(frames_filename="")) is True
    assert _wants_redraw(_record()) is False


def test_deleting_a_grid_takes_its_frame_plan_with_it(tmp_path):
    """Otherwise the dataset folder keeps plans nothing can name."""
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(data_dir=tmp_path)))
    store = SavedPlotsStore()
    store._persist = lambda _ctx: None            # no persistence.json here
    rec = SavedPlot(id="g1", created_at="2026-01-01T00:00:00", tab="Image Table",
                    title="grid", png_filename="g1.png",
                    frames_filename="g1.frames.zip", params={})
    assert store.add(ctx, rec, _flat_png(), b"", b"a-plan")
    images = tmp_path / "saved_plots"
    assert (images / "g1.frames.zip").exists()

    store.delete(ctx, "g1")
    assert not (images / "g1.frames.zip").exists()


def test_a_plan_that_cannot_be_written_clears_the_name_it_would_have_had(
        tmp_path, monkeypatch):
    """A name pointing at a file nobody wrote sends the deck looking for frames.

    It would then place nothing rather than falling back to the picture, so the
    grid would vanish off its own slide.
    """
    from well_viewer.features.saved_plots.state import SavedPlot, SavedPlotsStore

    ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(data_dir=tmp_path)))
    store = SavedPlotsStore()
    store._persist = lambda _ctx: None
    rec = SavedPlot(id="g1", created_at="2026-01-01T00:00:00", tab="Image Table",
                    title="grid", png_filename="g1.png",
                    frames_filename="g1.frames.zip", params={})

    real = type(tmp_path).write_bytes

    def _fail(self, data):
        if self.name.endswith(".frames.zip"):
            raise OSError("disk full")
        return real(self, data)

    monkeypatch.setattr(type(tmp_path), "write_bytes", _fail)
    assert store.add(ctx, rec, _flat_png(), b"", b"a-plan")
    assert rec.frames_filename == ""


def _slide_xml_at(path, index: int):
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(zf.read(f"ppt/slides/slide{index}.xml"))


# ── a field the picture could take no chrome into takes none in the deck ────

def test_a_field_too_small_for_chrome_gets_no_marks_in_the_deck(monkeypatch):
    """The refusal has to reach both surfaces or the deck states a wrong scale.

    ``draw_cell_chrome`` returns early on an array under 4 px on a side, so a
    200x3 crop takes no bar and no label however well the bar fits its width —
    and ``apply_cell_chrome`` records that refusal, so the status line tells the
    user the bar was left out. A frame plan that gated on the length alone put a
    rectangle on the slide anyway, presented as a 25 µm scale: a number the
    picture refused to draw and the app said it had refused.
    """
    cells = _cells()
    ctx = _export_ctx(cells, bar_um=25.0, pixel_um=0.5)      # 50 px of a 200 px width
    sliver = np.zeros((3, 200, 3), dtype=np.uint8)
    grid = _build(monkeypatch, ctx, cells, [sliver])

    assert grid.frames, "the fields did not reach the plan at all"
    assert all(not f.bars for f in grid.frames)
    assert all(not any(t.name == "Field label" for t in f.texts)
               for f in grid.frames)
    # And the app really did tell the user the bar was refused, so the two
    # halves of this test are describing the same pass.
    assert IC.scale_bar_refusal_report(ctx) == ["A01"]


def test_a_field_large_enough_still_gets_its_marks(monkeypatch):
    """The guard above must not be refusing everything.

    Without this the previous test passes against a frame builder that emits no
    marks at all, which is the implementation it is least able to distinguish.
    """
    cells = _cells()
    ctx = _export_ctx(cells, bar_um=40.0, pixel_um=0.5)
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(30)])
    assert all(f.bars for f in grid.frames)
    assert all(any(t.name == "Field label" for t in f.texts)
               for f in grid.frames)


# ── a grid with no pictures loses the frames, not the snapshot ──────────────

def test_a_grid_whose_fields_all_failed_to_encode_falls_back_to_the_snapshot(
        monkeypatch, tmp_path):
    """Placing a page of empty boxes and calling it editable is the worse trade.

    The flat snapshot is what the user actually saw. A deck that discards it for
    a grid of labelled blanks — and reports that grid as editable — is degraded
    output dressed as good output.
    """
    from well_viewer.features.saved_plots._deck import build_saved_plots_deck

    cells = _cells()
    ctx = _export_ctx(cells)
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(31), _source_rgb(32)])
    blank = IF.FramedGrid(
        width_in=grid.width_in, height_in=grid.height_in,
        frames=tuple(IF.ImageFrame(name=f.name, rect=f.rect, png=b"",
                                   texts=f.texts, bars=f.bars)
                     for f in grid.frames),
        labels=grid.labels)
    assert not blank.has_pictures

    plan = tmp_path / "g1.frames.zip"
    plan.write_bytes(IF.to_bytes(blank))
    png = tmp_path / "g1.png"
    png.write_bytes(_flat_png())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck(
        [_record()], lambda r: None, lambda r: png, out,
        frames_path_for=lambda r: plan)

    assert (result.framed, result.raster) == (0, 1)
    assert _slide_xml_at(out, 2).findall(f".//{{{_NS['p']}}}pic")


# ── one malformed plan must not take the whole gallery down ─────────────────

def _unclamped_label_width(label, frame) -> float:
    """What the label's box would measure with nothing holding it in.

    Reproduces ``_frames._label_mark``'s own estimate from the mark that came
    out, so the test can say whether this fixture actually exercises the clamp.
    """
    from well_viewer.features.image_browser.image_table import _frames as FR

    size_in = label.size_pt / 72.0
    return FR._ADVANCE_FRACTION * size_in * len(label.text)


def _plan_with(**overrides) -> bytes:
    import io
    import json

    plan = {"version": IF.FORMAT_VERSION, "width_in": 4.0, "height_in": 3.0,
            "frames": [], "labels": []}
    plan.update(overrides)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("grid.json", json.dumps(plan))
    return buf.getvalue()


@pytest.mark.parametrize("overrides, why", [
    ({"labels": [{"text": "x", "rect": [0, 0, 1, 1], "color": "red"}]},
     "a colour word rather than six hex digits"),
    ({"frames": {"nope": 1}}, "frames is not a list"),
    ({"labels": ["just a string"]}, "a mark is not an object"),
    ({"width_in": {"nope": 1}}, "a size that is not a number"),
    ({"width_in": 0.0}, "a zero width, which scales every rect off the slide"),
    ({"height_in": -3.0}, "a negative height"),
])
def test_every_malformed_plan_is_a_framed_grid_error(overrides, why):
    """The documented contract, held against the shapes JSON can actually take.

    ``_deck._hex`` raises on a colour that is not six hex digits — by design,
    because every colour it was written for is a theme token — and a plan read
    off disk is not. Letting any of these escape as itself aborts
    ``build_saved_plots_deck`` and takes every *other* plot's slide with it,
    against ``_try_frames``' own promise to fall back to the picture.
    """
    with pytest.raises(IF.FramedGridError):
        IF.from_bytes(_plan_with(**overrides))


def test_a_malformed_plan_costs_that_plot_its_frames_and_nothing_else(
        tmp_path):
    from well_viewer.features.saved_plots._deck import build_saved_plots_deck

    plan = tmp_path / "g1.frames.zip"
    plan.write_bytes(_plan_with(
        labels=[{"text": "x", "rect": [0, 0, 1, 1], "color": "red"}]))
    png = tmp_path / "g1.png"
    png.write_bytes(_flat_png())
    out = tmp_path / "deck.pptx"

    result = build_saved_plots_deck(
        [_record()], lambda r: None, lambda r: png, out,
        frames_path_for=lambda r: plan)

    assert (result.framed, result.raster) == (0, 1)


# ── the facet group band survives the trip ─────────────────────────────────

def test_a_group_strip_keeps_the_band_the_figure_draws(monkeypatch):
    """``FacetLabel.band`` exists so both surfaces read one description.

    Carrying the flag and never reading it is the exact drift the descriptor was
    added to prevent, and it flattens the grouping the band is there to express.
    """
    cells = [[_cell("A01", "GFP", "0h"), _cell("A01", "DAPI", "0h")],
             [_cell("A02", "GFP", "0h"), _cell("A02", "DAPI", "0h")]]
    ctx = _export_ctx(cells)
    ctx.state.image_table.row_dims = ["well", "channel"]
    ctx.state.image_table.col_dims = ["timepoint"]
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(40), _source_rgb(41)])

    banded = [m for m in grid.labels if m.plate]
    assert banded, "no facet label carries the band the figure fills"
    assert all(m.plate_color != "000000" for m in banded), (
        "the group band took the identity label's dark plate")


# ── a field with no picture is placed, and says what it is ─────────────────

def test_a_field_that_could_not_be_read_is_placed_and_says_so(monkeypatch):
    """Both "missing field" notes claim such a field is placed with its labels.

    A frame with no picture and no marks produced no shapes and was dropped, so
    the slide had a hole where the grid has a cell and the note described
    something that had not happened.
    """
    cells = _cells()
    ctx = _export_ctx(cells, label_mode="none")
    monkeypatch.setattr(E, "_load_array", lambda *a, **k: None)
    from well_viewer.features.image_browser.image_table import controller as ITC
    monkeypatch.setattr(ITC, "_prefetch_cell_arrays", lambda *a, **k: None)
    collector = FrameCollector()
    fig, _kw = E._build_export_figure(ctx, cell_max_px=POOLED_PX,
                                      frames=collector)
    assert fig is not None
    grid = collector.grid

    assert len(grid.frames) == 2
    assert all(not f.png for f in grid.frames)
    assert all(any(t.text == "(no image)" for t in f.texts)
               for f in grid.frames)


# ── a long label stays inside the field it names ───────────────────────────

def test_a_long_identity_label_is_clamped_into_its_field(monkeypatch):
    """The burn-in clamps its box; an unclamped mark lands outside the group."""
    cells = [[_cell("A01", "GFP", "0h", "1")]]
    ctx = _export_ctx(cells)
    # Long enough that the estimated run is wider than the field itself — the
    # fixture has to make the clamped and unclamped answers differ, or no
    # assertion of any strength can tell them apart.
    ctx.state.dataset.well_labels = {
        "A01": "Vehicle control, replicate 3 of 3, plate 2, operator initials ZS"}
    ctx.state.image_table.label_img_corner = "br"
    ctx.state.image_table.label_font_px = 26
    grid = _build(monkeypatch, ctx, cells, [_source_rgb(50)])

    frame = grid.frames[0]
    label = next(t for t in frame.texts if t.name == "Field label")
    assert _unclamped_label_width(label, frame) > frame.rect.w, (
        "the fixture's label fits the field, so the clamp is untested here")
    assert label.rect.x >= frame.rect.x - 1e-9
    assert label.rect.x + label.rect.w <= frame.rect.x + frame.rect.w + 1e-9
    assert label.rect.y + label.rect.h <= frame.rect.y + frame.rect.h + 1e-9


# ── the per-axes title sits where the figure draws it ──────────────────────

def test_the_title_sits_above_the_picture_and_inside_the_group(monkeypatch):
    """Measured against the axes matplotlib actually drew, not against the slot.

    ``imshow(aspect="equal")`` centres the axes in the whole slot and
    ``set_title`` draws above *that*. Taking a strip out of the slot first is
    internally consistent and puts every picture 0.1 in below where the figure
    put it. And the frame's rectangle becomes the PowerPoint group's extent, so
    a title outside it is drawn but excluded from the group's own box.
    """
    cells = _cells()
    ctx = _export_ctx(cells)
    grid, fig = _build(monkeypatch, ctx, cells,
                       [_source_rgb(60), _source_rgb(61)],
                       show_titles=True, keep_figure=True)

    # Where the figure actually put this field's pixels, read off the drawn
    # axes rather than off the slot the plan and the figure both start from.
    # Asserting only that the title sits above the picture passes under either
    # geometry, because both move together — which is how a plan that put every
    # picture 0.1 in low stayed green.
    drawn = _drawn_image_boxes(fig)
    frame = grid.frames[0]
    want = drawn[0]
    assert frame.rect.x == pytest.approx(want[0], abs=2e-3)
    assert frame.rect.w == pytest.approx(want[2], abs=2e-3)
    assert frame.rect.y + frame.rect.h == pytest.approx(want[1] + want[3],
                                                        abs=2e-3)

    title = next(t for t in frame.texts if t.name == "Field title")
    picture = next(t for t in frame.texts if t.name == "Field label")
    assert title.text.startswith("A01")
    # Above the picture's own label, and inside the frame box the group uses.
    assert title.rect.y + title.rect.h <= picture.rect.y + 1e-9
    assert frame.rect.y <= title.rect.y + 1e-9
    assert (title.rect.x >= frame.rect.x - 1e-9
            and title.rect.x + title.rect.w <= frame.rect.x + frame.rect.w + 1e-9)


# ── the menu action itself ─────────────────────────────────────────────────

def test_the_export_menu_action_writes_a_deck_of_groups(monkeypatch, tmp_path):
    """The control a user actually clicks, driven end to end.

    Every other test here enters one step past it, at the frame collector or at
    the deck builder. A builder that raised, a status line that lied, or a lazy
    proxy that failed to resolve would be invisible to all of them.
    """
    from PySide6.QtWidgets import QFileDialog

    out = tmp_path / "from_the_menu.pptx"
    cells = _cells()
    ctx = _export_ctx(cells, bar_um=40.0, pixel_um=0.5)
    said: list[str] = []
    ctx.notify.set_status = said.append

    _install_sources(monkeypatch, [_source_rgb(70), _source_rgb(71)])
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    E.image_table_export_pptx(ctx)

    assert out.exists(), said
    assert said and "2 fields, each an editable group" in said[0]
    assert len(_groups(_slide_xml(out))) == 2


def test_the_menu_action_writes_nothing_when_no_field_could_be_read(
        monkeypatch, tmp_path):
    """A deck of empty boxes reported as a success is the failure to avoid."""
    from PySide6.QtWidgets import QFileDialog

    out = tmp_path / "never_written.pptx"
    ctx = _export_ctx(_cells())
    said: list[str] = []
    ctx.notify.set_status = said.append

    monkeypatch.setattr(E, "_load_array", lambda *a, **k: None)
    from well_viewer.features.image_browser.image_table import controller as ITC
    monkeypatch.setattr(ITC, "_prefetch_cell_arrays", lambda *a, **k: None)

    def _refuse(*_a, **_k):
        raise AssertionError("a save dialog was opened for an unexportable grid")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(_refuse))
    E.image_table_export_pptx(ctx)

    assert not out.exists()
    assert said == ["Image Table: nothing to export to PowerPoint."]
