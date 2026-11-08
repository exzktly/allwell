"""Every figure on the Nanowells page reaches Saved Plots — not only the main card.

The page draws on four plot cards: the main figure (every view — Array,
Distribution, Identity, Statistics, QC, Extremes), the selected nanowell over
time beside it, the crop strip under that, and the **Nanowell crops** pop-out.
Only the main card was ever handed a save handler, so the other three had no
hover save button, and the pop-out's *Save ▾ → Save to Saved Plots* reached a
card with no handler and returned without a word.

Driven through the real page builder and the real pop-out menu entry, and
asserted on what reached the gallery's store, because a card that *offers* the
entry while doing nothing is exactly the defect.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.features.saved_plots.state import get_store  # noqa: E402
from tests.test_nanowell_popout_grid import _open, channel_crops  # noqa: E402,F401
from tests.test_nanowell_page import page  # noqa: E402,F401


def _records(ctx):
    store = get_store(ctx)
    store.ensure_loaded(ctx)
    return list(store.records)


def test_the_pop_out_s_save_to_saved_plots_files_the_pop_out_s_figure(page, qtbot, channel_crops):  # noqa: F811
    _open(qtbot, page)
    entries = {a.text(): a for a in page.widgets.get("_nw_popout_save_menu").actions() if a.text()}
    assert _records(page) == []
    entries["Save to Saved Plots"].trigger()
    records = _records(page)
    assert len(records) == 1
    rec = records[0]
    assert rec.tab == "Nanowells"
    # Filed as part of its tab: a redraw replays onto the page's main card, a different figure from this one.
    assert rec.panel_only is True
    assert rec.title == "Nanowells · crops pop-out"
    assert rec.svg_filename                          # captured as vector, so the deck draws it as shapes
    png = get_store(page).image_path(page, rec)
    assert png is not None and png.stat().st_size > 0


def test_the_site_detail_and_the_crop_strip_save_to_saved_plots(page, qtbot, channel_crops):  # noqa: F811
    _open(qtbot, page)                               # a nanowell selected: the detail and its crops are drawn
    page.widgets.get("_nw_detail_card").saveFigureToGallery()
    page.widgets.get("_nw_strip_card").saveFigureToGallery()
    records = _records(page)
    assert sorted(r.title for r in records) == ["Nanowells · crops", "Nanowells · nanowell over time"]
    assert all(r.tab == "Nanowells" and r.panel_only is True for r in records)


def test_the_secondary_cards_never_displace_the_page_s_main_card_for_a_redraw(page, qtbot, channel_crops):  # noqa: F811
    """The export's vector repair and the headless report replay a record onto the card registered for its tab; a
    secondary card registered after the main one would have them capture the crops under a Distribution's title."""
    _open(qtbot, page)
    assert page.runtime.saved_plots.cards_by_tab["Nanowells"] is page.widgets.get("_nw_card")
