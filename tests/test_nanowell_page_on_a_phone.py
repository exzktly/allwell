"""The Nanowells page's site detail — the inspector's Detail tab — through the page's real builder in a minimal
shell, at a desk and in phone layout.

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


def test_on_a_phone_the_detail_is_in_the_controls_sheet_and_the_figure_keeps_the_page(qtbot, page, phone):
    """The detail used to take the page from the figure on a phone. It is the inspector's Detail tab now, and on a
    phone the inspector is the Controls sheet: the figure stays where it is, with its dots still there to tap."""
    from review.core import inspector_service as svc
    from tests.test_nanowell_subset_fits_a_phone import _Sheet

    sheet = _Sheet(qtbot, page, (432, 818))
    svc.set_phone_host(page, sheet)
    st = _loaded(qtbot, page)
    card, detail = page.widgets.get("_nw_card"), page.widgets.get("_nw_detail_pane")
    _click_dot(qtbot, page, ("A01", 1, 0))
    assert st.selected_sites == [["A01", 1, 0]]
    insp = svc.inspector(page)
    assert sheet.inspector_is_open() and insp.currentTab() == "detail" and insp.tabWidget("detail") is detail
    assert sheet.holder.isAncestorOf(detail) and detail.isVisibleTo(sheet.holder)
    assert insp.wantedSize() == "half", "the detail asks for the whole sheet, covering the plot it describes"
    assert not card.isHidden() and card.isVisibleTo(page.window), "the figure left the page for the detail"
    page.widgets.get("_nw_detail_clear_btn").click()
    assert st.selected_sites == [] and insp.tabWidget("detail") is None
    assert not card.isHidden()


def _window_rect(widget):
    from PySide6.QtCore import QRect

    return QRect(widget.mapTo(widget.window(), widget.rect().topLeft()), widget.size())


def test_at_a_desk_the_detail_is_the_inspector_s_tab_beside_the_whole_page(qtbot, page):
    """The site detail is the inspector's Detail tab: beside the page from its top to its bottom, with the band, the
    figure and its footer all to its left. Wrong implementation: the detail still a column of the page's own — then
    it is not inside the inspector, and Style opened beside it makes a second panel."""
    from review.core import inspector_service as svc
    from widgets.control_band import ControlBand

    _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    detail, card = page.widgets.get("_nw_detail_pane"), page.widgets.get("_nw_card")
    insp = svc.inspector(page)
    assert svc.is_open(page) and insp.currentTab() == "detail" and insp.tabWidget("detail") is detail
    band = next(b for b in page.window.findChildren(ControlBand) if b.isVisible())
    qtbot.waitUntil(lambda: detail.isVisible() and detail.width() > 0)
    d, i, b, c = _window_rect(detail), _window_rect(insp), _window_rect(band), _window_rect(card)
    column = _window_rect(band.parentWidget())       # the page's column: band, figure and footer
    assert i.top() <= b.top(), f"the inspector starts at y={i.top()}, below the band's top at y={b.top()}"
    assert i.bottom() >= column.bottom(), f"the inspector ends at y={i.bottom()}, above the page's at {column.bottom()}"
    assert b.right() < i.left() and c.right() < i.left(), "the band or the figure runs under the inspector"
    # The detail has exactly the tab's height: a figure asks for none of its own, and given its preferred height
    # instead it was 1,547 px in an 820 px tab, scrolled, its crops under the fold.
    slot = insp.slotOf(detail)
    qtbot.waitUntil(lambda: slot.verticalScrollBar().maximum() == 0, timeout=2000)
    assert d.bottom() <= i.bottom(), f"the detail runs to y={d.bottom()}, past the inspector's {i.bottom()}"
    assert d.height() >= i.height() * 0.6, f"the detail is {d.height()} px of the inspector's {i.height()}"
    strip = page.widgets.get("_nw_strip_canvas")
    assert _window_rect(strip).bottom() <= i.bottom() and strip.height() > 40, "the crop strip is under the fold"


def test_picking_another_nanowell_updates_the_same_detail_in_place(qtbot, page):
    from review.core import inspector_service as svc

    st = _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    detail = page.widgets.get("_nw_detail_pane")
    fig = page.widgets.get("_nw_detail_fig")
    named = lambda: [t.get_text().split(" — ")[0] for t in fig.texts if " — " in t.get_text()]   # noqa: E731
    first = named()
    assert len(first) == 1 and first[0].startswith("A01"), first
    _click_dot(qtbot, page, ("B01", 0, 0))
    assert st.selected_sites == [["B01", 0, 0]]
    assert svc.inspector(page).tabWidget("detail") is detail, "a second detail was built for the second pick"
    second = named()
    assert len(second) == 1 and second[0].startswith("B01"), f"the detail names {second}, not the nanowell just picked"


def test_a_redraw_never_reopens_a_detail_the_user_closed_but_a_new_pick_does(qtbot, page):
    """The detail is redrawn on every redraw of the page (a setting changed, its crops arriving). Wrong
    implementation: opening the inspector on each of those — it could then not be closed at all while a nanowell
    stayed selected."""
    from review.core import inspector_service as svc
    from review.features.nanowell import controller as nwc

    st = _loaded(qtbot, page)
    _click_dot(qtbot, page, ("A01", 1, 0))
    assert svc.is_open(page)
    svc.close(page)
    nwc.redraw(page)
    nwc.redraw_detail(page)
    qtbot.wait(50)
    assert st.selected_sites == [["A01", 1, 0]], "closing the inspector is not clearing the selection"
    assert not svc.is_open(page), "a redraw re-opened the inspector the user closed"
    # The tab is still the selected nanowell's: opening the inspector again finds it there.
    assert svc.inspector(page).tabWidget("detail") is page.widgets.get("_nw_detail_pane")
    _click_dot(qtbot, page, ("B01", 0, 0))
    assert svc.is_open(page) and svc.inspector(page).currentTab() == "detail"


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
        # The footer is broken to the figure's width, so the sentence is read across its lines.
        assert "Too narrow to write each swarm's count" in " ".join(
            t.get_text() for t in sorted(fig.texts, key=lambda t: -t.get_position()[1]))


def _footer_extents(width_in: float) -> tuple[int, list[float]]:
    """How many footer lines the distribution figure draws, and how far right each ends as a fraction of the
    figure's width, on a figure *width_in* inches wide."""
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
    fig = Figure(figsize=(width_in, 6))
    FigureCanvasAgg(fig)
    render_distribution(fig, model)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    texts = [t for t in fig.texts if (t.get_gid() or "").startswith("nw-footer")]
    return len(texts), [t.get_window_extent(renderer).x1 / fig.bbox.width for t in texts]


def test_the_footer_is_broken_to_the_figure_s_width_whatever_the_mode(phone):
    """The footer wraps because the figure is narrow, not because the renderer looked at phone layout: phone layout is
    ON here (the ``phone`` fixture) and a figure wide enough for its footer still leaves every line whole. Wrong
    implementation: a renderer that wraps on the mode would break the wide figure's lines too; one that never wraps
    would run the narrow figure's off its edge."""
    wide_lines, wide_ends = _footer_extents(12.0)
    narrow_lines, narrow_ends = _footer_extents(2.6)
    assert max(wide_ends) <= 1.0 and narrow_lines > wide_lines, (wide_lines, narrow_lines)
    assert max(narrow_ends) <= 1.0, narrow_ends
    phone_layout.set_on(False)
    assert _footer_extents(2.6) == (narrow_lines, narrow_ends), "the mode changed what the renderer drew"
