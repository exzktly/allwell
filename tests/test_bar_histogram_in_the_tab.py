"""The Bar tab's Display popover drives Histogram through its real controls (CLAUDE.md §7.6, §7.8 #3).

The tab is built by ``build_bar_plots_tab`` in the minimal shell on the mini dataset; the Show selector and the Bins
box are the real widgets, the figure is the real one, and what reached it is read back from its artists.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def tab(qtbot, mini_dataset):
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    from review.core.ui_helpers import PLOTTING_BAND_KEY
    from review.features.barplot.collect import bar_well_display_label
    from review.features.barplot.controller import ordered_bar_keys
    from review.features.barplot.tab_view import build_bar_plots_tab
    from tests._minimal_shell import load_dataset, minimal_shell
    from widgets.control_band import ControlBand

    ctx, window = minimal_shell(qtbot)
    window._ctx, window._app_state, window._features = ctx, ctx.state, ctx.features
    load_dataset(ctx, mini_dataset)
    frame = QWidget()
    QHBoxLayout(frame)
    window.setCentralWidget(frame)
    ctx.widgets.register(PLOTTING_BAND_KEY, ControlBand(frame))
    build_bar_plots_tab(ctx, frame)
    ctx.runtime.barplot.current_keys = lambda: ordered_bar_keys(ctx)
    ctx.runtime.barplot.well_display_label = lambda label: bar_well_display_label(ctx, label)
    ctx.state.selection.selected_wells = set(sorted(ctx.state.dataset.well_paths)[:2])
    from review.features.barplot.controller import update_bar_tp_menu
    update_bar_tp_menu(ctx)                       # as a load does: the first timepoint ticked
    return ctx


def _histogram_note(ctx):
    from review.features.barplot.renderer import HISTOGRAM_NOTE_GID

    ax = ctx.widgets.get("_ax_bar_mean")
    return [t.get_text() for t in ax.texts if t.get_gid() == HISTOGRAM_NOTE_GID]


def _histogram_bars(ctx):
    from review.features.barplot.renderer import HISTOGRAM_GID

    return [p for p in ctx.widgets.get("_ax_bar_mean").patches if p.get_gid() == HISTOGRAM_GID]


def test_show_histogram_draws_it_and_hands_bins_the_control(tab):
    from review.features.barplot import _per_cell_mode as pcm

    seg, spin, slider = (tab.widgets.get(n) for n in ("_bar_show_seg", "_hist_bins_spin", "_violin_slider"))
    assert seg.currentData() == "bars" and not spin.isEnabled() and not slider.isEnabled()
    seg.setCurrentIndex(pcm.MODE_KEYS.index("histogram"))
    bar = tab.state.bar
    assert (bar.swarm, bar.violin, bar.histogram) == (False, False, True)
    assert spin.isEnabled() and not slider.isEnabled()
    assert _histogram_bars(tab)
    assert _histogram_note(tab)[0].startswith("30 bins")

    spin.setValue(12)
    assert bar.hist_bins == 12
    assert _histogram_note(tab)[0].startswith("12 bins")

    seg.setCurrentIndex(pcm.MODE_KEYS.index("violin"))
    assert (bar.violin, bar.histogram) == (True, False)
    assert slider.isEnabled() and not spin.isEnabled()
    assert not _histogram_bars(tab)


def test_a_restored_view_puts_the_selector_on_its_mode(tab):
    from review.features.barplot.renderer import redraw_bars
    from review.persistence import view_state as vs

    vs._apply_bar(tab, {"histogram": True, "hist_bins": 9})
    redraw_bars(tab)
    assert tab.widgets.get("_bar_show_seg").currentData() == "histogram"
    assert tab.widgets.get("_hist_bins_spin").value() == 9
    assert tab.widgets.get("_hist_bins_spin").isEnabled()
    assert _histogram_note(tab)[0].startswith("9 bins")
