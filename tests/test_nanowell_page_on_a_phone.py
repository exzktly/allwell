"""The Nanowells page's site detail in phone layout, through the page's real builder in a minimal shell.

Rendered at phone size, selecting one nanowell squeezed the Distribution figure into a strip a few pixels wide beside
the site detail, the detail's heading still said "Selected nanowells", the row of crop controls was cut to "Crops ove",
"Cel", "op ou", twelve crops sat in one row a few pixels each with their notes run together, and the figure's footer
was drawn over its lowest dots because phone layout's constrained layout ignored the room the renderer reserved.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel

from core import phone_layout
from tests.test_nanowell_page import _click_dot, _loaded, _page, _write_nanowell_dataset


@pytest.fixture
def phone():
    phone_layout.set_on(True)
    yield
    phone_layout.set_on(False)


@pytest.fixture
def page(qtbot, tmp_path):
    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01", "B01"}
    return ctx


def test_on_a_phone_the_detail_takes_the_page_and_close_brings_the_figure_back(qtbot, page, phone):
    st = _loaded(qtbot, page)
    card, detail = page.widgets.get("_nw_card"), page.widgets.get("_nw_detail_pane")
    _click_dot(qtbot, page, ("A01", 1, 0))
    assert st.selected_sites == [["A01", 1, 0]]
    assert detail.isVisible() and card.isHidden(), "the figure stayed beside the detail on a phone"
    page.widgets.get("_nw_detail_close_btn").click()
    assert st.selected_sites == [] and detail.isHidden() and not card.isHidden()
    fig = page.widgets.get("_nw_fig")
    qtbot.waitUntil(lambda: any(c.get_gid() == "nw-dots" for ax in fig.axes for c in ax.collections), timeout=5000)


def test_at_a_desk_the_detail_opens_beside_the_figure(qtbot, page):
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    assert page.widgets.get("_nw_detail_pane").isVisible() and page.widgets.get("_nw_card").isVisible()


def test_the_heading_names_one_nanowell(qtbot, page):
    titles = [w.text() for w in page.widgets.get("_nw_detail_pane").findChildren(QLabel)]
    assert "Selected nanowell" in titles and "Selected nanowells" not in titles


def test_the_crop_controls_wrap_rather_than_being_cut(qtbot, page, phone):
    """The row above the crops must not hold the detail pane wider than a phone: a fixed row of five controls asked
    for 491 px and cut every control to a few letters once squeezed. It wraps instead."""
    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    detail = page.widgets.get("_nw_detail_pane")
    host = page.widgets.get("_nw_popout_btn").parentWidget()
    assert host.minimumSizeHint().width() <= 360, f"the crop controls ask for {host.minimumSizeHint().width()} px"
    assert detail.minimumSizeHint().width() <= 360, f"the detail pane asks for {detail.minimumSizeHint().width()} px"
    host.resize(360, host.heightForWidth(360) if host.hasHeightForWidth() else host.height())
    host.layout().setGeometry(host.rect())
    for name in ("_nw_strip_cells_box", "_nw_strip_image_channel_cb", "_nw_strip_range_btn",
                 "_nw_strip_go_to_btn", "_nw_popout_btn"):
        w = page.widgets.get(name)
        assert w.geometry().right() <= 360 and w.width() >= w.minimumSizeHint().width(), (
            f"{name}: {w.width()} px wide, right edge at {w.geometry().right()}")


def test_the_strip_wraps_its_crops_to_its_shape():
    from review.features.nanowell.crop_grid_compute import strip_columns

    assert strip_columns(12, 3.6, 3.6) == 4             # a phone's square strip: three rows of four
    assert strip_columns(12, 12.0, 1.0) == 12           # a desk's wide strip: one row
    assert strip_columns(1, 1.0, 1.0) == 1 and strip_columns(0, 1.0, 1.0) == 1


def test_a_missing_crop_s_reason_is_said_once_in_the_caption():
    from matplotlib.figure import Figure

    from review.features.nanowell.image_compute import Crop
    from review.features.nanowell.renderer import render_crop_strip

    fig = Figure(figsize=(4, 4))
    crops = {tp: Crop(None, "", f"The grid record has no montage for A02 at {tp} (ok).") for tp in ("t00", "t01")}
    render_crop_strip(fig, crops, ("A02", 0, 0), cols=2)
    panels = [t.get_text() for ax in fig.axes for t in ax.texts if t.get_gid() == "nw-crop-missing"]
    assert panels == ["no crop", "no crop"]
    caption = next(t.get_text() for t in fig.texts if t.get_gid() == "nw-crop-caption")
    assert caption.endswith("; no crop at every timepoint: The grid record has no montage for A02 (ok).")
    assert {ax.get_subplotspec().rowspan.start for ax in fig.axes} == {0}, "two crops in two columns are one row"


def test_on_a_phone_the_panels_keep_clear_of_the_footer(phone):
    """Constrained layout ignores text written on the figure; ``plot_style._figure_text_band`` keeps it out."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from review.core.plot_style import _apply_layout_engine

    fig = Figure(figsize=(3, 6))
    FigureCanvasAgg(fig)
    fig.subplots_adjust(bottom=0.4)
    ax = fig.add_subplot(111)
    ax.plot([0, 1], [0, 1])
    texts = [fig.text(0.07, 0.035 + i * 0.03, f"footer line {i}") for i in range(8)]
    _apply_layout_engine(fig, {})
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    footer_top = max(t.get_window_extent(renderer).y1 for t in texts)
    axes_bottom = ax.get_tightbbox(renderer).y0
    assert axes_bottom >= footer_top, (axes_bottom, footer_top)


def test_on_a_narrow_figure_no_swarm_count_runs_into_another():
    """Twelve timepoints of 2,025 nanowells on a phone-narrow figure: the screenshot showed "2,025" run into "2,025"
    across each panel's top. Where the numbers do not fit their slots they are left out and the footer says so."""
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from review.features.nanowell import distribution_compute as dc
    from review.features.nanowell.renderer import render_distribution

    rng = np.random.default_rng(0)
    swarms = [dc.Swarm(f"t{k:02d}", "A01", float(k), rng.integers(0, 12, 2025).astype(float),
                       [("A01", j, 0) for j in range(2025)], ["1"] * 2025, 2025) for k in range(12)]
    model = dc.DistributionModel("n", "n", "Cell count", dc.TIME, True,
                                 [dc.Panel("A01", swarms, [(float(k), f"{k}") for k in range(12)])])
    fig = Figure(figsize=(2.6, 6))
    FigureCanvasAgg(fig)
    render_distribution(fig, model)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    boxes = sorted((t.get_window_extent(renderer) for ax in fig.axes for t in ax.texts if t.get_gid() == "nw-used"),
                   key=lambda b: b.x0)
    assert all(a.x1 <= b.x0 for a, b in zip(boxes, boxes[1:])), "two swarm counts overlap"
    if not boxes:
        assert any("Too narrow to write each swarm's count" in t.get_text() for t in fig.texts)


def _footer_extents(wrap_footer: bool) -> tuple[int, list[float]]:
    """How many footer lines the distribution figure draws, and how far right each ends as a fraction of the
    figure's width, on a figure as narrow as a phone's."""
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from review.features.nanowell import distribution_compute as dc
    from review.features.nanowell.renderer import render_distribution

    rng = np.random.default_rng(0)
    swarms = [dc.Swarm(f"t{k:02d}", "A01", float(k), rng.integers(0, 12, 2025).astype(float),
                       [("A01", j, 0) for j in range(2025)], ["1"] * 2025, 2025) for k in range(12)]
    model = dc.DistributionModel("n", "n", "Cell count", dc.TIME, True,
                                 [dc.Panel("A01", swarms, [(float(k), f"{k}") for k in range(12)])])
    fig = Figure(figsize=(2.6, 6))
    FigureCanvasAgg(fig)
    render_distribution(fig, model, wrap_footer=wrap_footer)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    texts = [t for t in fig.texts if (t.get_gid() or "").startswith("nw-footer")]
    return len(texts), [t.get_window_extent(renderer).x1 / fig.bbox.width for t in texts]


def test_the_renderer_wraps_its_footer_when_told_to_and_never_reads_the_mode(phone):
    """The footer wraps because the caller asked (``wrap_footer``), not because the renderer looked at phone layout.
    Phone layout is ON here (the ``phone`` fixture), and the unwrapped footer still runs off the figure: wrong
    implementation — a renderer that reads the mode itself — would wrap in both calls."""
    plain_lines, plain_ends = _footer_extents(False)
    wrapped_lines, wrapped_ends = _footer_extents(True)
    assert max(plain_ends) > 1.0, "the fixture's footer fits, so it cannot tell the two apart"
    assert wrapped_lines > plain_lines
    assert max(wrapped_ends) <= 1.0, wrapped_ends


def test_the_page_redraw_asks_for_the_wrap_exactly_when_phone_layout_is_on(qtbot, page, monkeypatch):
    """The call the app makes: the page's own redraw hands ``render_distribution`` ``wrap_footer``, from the
    feature's ``_phone.on_phone()``. Spied on at the boundary and delegating, so the figure is still drawn. Wrong
    implementation: a controller that never passes it (the renderer then wraps nothing on a phone), or passes it
    always."""
    from review.features.nanowell import controller as nwc

    _loaded(qtbot, page)
    seen = []
    real = nwc.render_distribution

    def spy(fig, model, **kw):
        seen.append(kw.get("wrap_footer"))
        return real(fig, model, **kw)

    monkeypatch.setattr(nwc, "render_distribution", spy)
    nwc.redraw(page)
    assert seen and set(seen) == {False}, seen
    seen.clear()
    phone_layout.set_on(True)
    try:
        nwc.redraw(page)
    finally:
        phone_layout.set_on(False)
    assert seen and set(seen) == {True}, seen
