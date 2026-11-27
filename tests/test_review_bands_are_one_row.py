"""A page's band is one row at the desk (design language §6): the pages whose bands were two rows deep, each fitted by
the user's decision on what moves off the band (tracker §9.1 step 15), measured through the registry's own builder.

The every-page render test measures every page; this one is in the fast lane, so a control added to one of these
bands is caught before the slow lane runs, and it says where the control that left the band went.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QAbstractButton, QApplication

from tests._minimal_shell import build_page

#: The window width measured: with the minimal shell's rail the band gets 1258 px, close to the 1284 px the
#: every-page render test's desk screen gives these bands. A 1284 px *window* (band ~1140) was measured too, and
#: Statistics fitted there by about 2 px — so inside the lane, where a worker's earlier tests leave the font a
#: pixel wider, it failed: that width is narrower than any desk screen gives these pages, and a test that fails on a
#: pixel of drift measures the drift.
WIDTHS = (1400,)


def _one_row(qtbot, name, dataset):
    ctx, window, _frame, bands = build_page(qtbot, name, dataset)
    found = {}
    for width in WIDTHS:
        window.resize(width, 900)
        QApplication.processEvents()
        # ``band_is_one_row``'s own measure, on the bands the builder returned. Not ``band_is_one_row(window)``: it
        # walks every widget of the window, which wraps combo popups the reaper then deletes a second time — a
        # segfault in whichever test runs next (CLAUDE.md §7.10 #6). The every-page test runs it in a process of
        # its own.
        found[width] = [f"needs {band.heightForWidth(band.width())} px at {band.width()} px"
                        for band in bands if band.isVisible() and band.form() is None
                        and band.heightForWidth(band.width()) > band.band_height() + 1]
    return ctx, window, bands, found


def test_statistics_band_is_one_row_and_the_replicates_readout_is_under_it(qtbot, mini_dataset):
    ctx, window, bands, found = _one_row(qtbot, "Statistics", mini_dataset)
    assert found == {w: [] for w in WIDTHS}
    readout = ctx.widgets.get("_stats_fov_indicator")
    assert readout.text().startswith("Replicates:") and readout.isVisibleTo(window)
    assert not any(band.isAncestorOf(readout) for band in bands), "the readout is back on the band"
    assert readout.property("textRole") == "help"


@pytest.mark.parametrize("name, canvas_key", [("Spatial", "_sp_canvas"), ("Multivariate", "_mv_canvas")])
def test_a_map_page_s_band_is_one_row_with_lasso_under_the_figure(qtbot, mini_dataset, name, canvas_key):
    """Lasso cells is a tool under the figure it draws on; Spatial's Recompute and Multivariate's Standardize are
    on the Display page, which the band's Display button opens."""
    from review.core.inspector_panels import panel_of

    ctx, window, bands, found = _one_row(qtbot, name, mini_dataset)
    assert found == {w: [] for w in WIDTHS}
    card = ctx.widgets.get(canvas_key).parentWidget()
    while card is not None and not hasattr(card, "toolbar"):
        card = card.parentWidget()
    assert card is not None
    # Looked up under the toolbar and the band only: ``findChildren`` over the whole window wraps widgets the test's
    # reaper then deletes a second time (CLAUDE.md §7.10 #6).
    lasso = [b for b in card.toolbar.findChildren(QAbstractButton) if b.text() == "Lasso cells"]
    # Not "visible": with no wells selected the figure, toolbar and all, is behind the page's empty state.
    assert len(lasso) == 1, "Lasso cells is not under the figure"
    on_band = [b for band in bands for b in band.findChildren(QAbstractButton)]
    assert not any(b.text() == "Lasso cells" for b in on_band), "Lasso cells is back on the band"
    display = [b for b in on_band if panel_of(b) is not None and panel_of(b).root and panel_of(b).tab == "display"]
    assert len(display) == 1
    # Pressing it is held in tests/test_band_panels_open_in_the_inspector.py.


def test_a_band_draws_a_segmented_control_at_its_row_height(qtbot):
    from widgets.control_band import ControlBand
    from widgets.segmented_control import SegmentedControl

    band = ControlBand()
    qtbot.addWidget(band)
    seg = SegmentedControl()
    for text in ("Cell", "Colony", "Summary"):
        seg.addSegment(text)
    band.add_scope("Level", seg)
    band.resize(800, band.band_height())
    band.show()
    QApplication.processEvents()
    assert seg.sizeHint().height() <= ControlBand.ROW_H, seg.sizeHint().height()
    assert band.heightForWidth(800) <= band.band_height() + 1


def test_segmentation_band_is_one_row_with_its_editing_tools_under_the_image(qtbot, mini_dataset):
    """The zoom, Delete cell, Crop cells (each with its scope control) and Edit are a tool row under the image (the
    user's decision, tracker §9.1 step 15)."""
    ctx, window, bands, found = _one_row(qtbot, "Segmentation", mini_dataset)
    assert found == {w: [] for w in WIDTHS}
    row = ctx.widgets.get("_review_image_tools_row")
    for name in ("_review_image_delete_btn", "_review_image_crop_btn", "_review_image_edit_btn",
                 "_review_image_zoom_lbl"):
        widget = ctx.widgets.get(name)
        assert row.isAncestorOf(widget), f"{name} is not in the tool row under the image"
        assert not any(band.isAncestorOf(widget) for band in bands), f"{name} is back on the band"
