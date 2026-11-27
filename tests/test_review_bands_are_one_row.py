"""A page's band is one row at the desk (design language §6): the pages whose bands were two rows deep, each fitted by
the user's decision on what moves off the band (tracker §9.1 step 15), measured through the registry's own builder.

The every-page render test measures every page; this one is in the fast lane, so a control added to one of these
bands is caught before the slow lane runs, and it says where the control that left the band went.
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication

from tests._layout_checks import band_is_one_row
from tests._minimal_shell import build_page

#: Desk widths: the every-page render test's desk screens leave the page about these.
WIDTHS = (1400, 1284)


def _one_row(qtbot, name, dataset):
    ctx, window, _frame, bands = build_page(qtbot, name, dataset)
    found = {}
    for width in WIDTHS:
        window.resize(width, 900)
        window.show()
        QApplication.processEvents()
        found[width] = [f.detail for f in band_is_one_row(window)]
    return ctx, window, bands, found


def test_statistics_band_is_one_row_and_the_replicates_readout_is_under_it(qtbot, mini_dataset):
    ctx, window, bands, found = _one_row(qtbot, "Statistics", mini_dataset)
    assert found == {w: [] for w in WIDTHS}
    readout = ctx.widgets.get("_stats_fov_indicator")
    assert readout.text().startswith("Replicates:") and readout.isVisibleTo(window)
    assert not any(band.isAncestorOf(readout) for band in bands), "the readout is back on the band"
    assert readout.property("textRole") == "help"
