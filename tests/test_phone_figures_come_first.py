"""Windows and pages whose figure was buried on a phone, found by rendering every page at 432 x 936 and 936 x 432.

The nanowell crops pop-out put six lines of controls above its figure (the figure began 46% of the way down, and
sideways was off the screen altogether in the Hex layout), Test detection put one control per line above its montage
(53% down), the Saved Plots card's seven action buttons made it wider than the screen, and the Nanowells page's
Trajectories and Position footers were drawn over the panels' lowest part. Each is measured here as a person sees it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget  # noqa: E402

from tests.test_nanowell_page import _page, _write_nanowell_dataset  # noqa: E402
from tests.test_phone_windows import SIDEWAYS, UPRIGHT, _pump, phone, qapp  # noqa: E402,F401


def _open_popout(qtbot, tmp_path, qapp, screen):  # noqa: F811
    from review.features.nanowell import controller as nwc
    from tests.test_phone_windows import _WINDOWS
    from ui import theme
    from ui.phone_shell import PhoneHandles, PhoneShell

    ctx = _page(qtbot, _write_nanowell_dataset(tmp_path))
    ctx.state.selection.selected_wells = {"A01"}
    qapp.setStyleSheet(theme.qss())
    shell = PhoneShell(ctx.window, PhoneHandles())
    shell.desk_button.setParent(ctx.window)
    shell._rail_status.setParent(ctx.window)
    shell.set_on(True)
    _pump(qapp)
    shell._sweep_timer.stop()
    nwc.pop_out_crops(ctx)
    popout = ctx.widgets.get("_nw_popout")
    _WINDOWS.append(popout)
    _pump(qapp)
    shell._sweep(force=False)
    _pump(qapp, 20)
    return ctx, popout


@pytest.mark.parametrize("screen", [UPRIGHT, SIDEWAYS], ids=["upright", "sideways"])
def test_the_pop_out_opens_on_its_figure_with_the_controls_folded_away(qtbot, tmp_path, qapp, phone, screen):  # noqa: F811
    phone(screen)
    ctx, popout = _open_popout(qtbot, tmp_path, qapp, screen)
    card = ctx.widgets.get("_nw_popout_card")
    toggle = ctx.widgets.get("_nw_popout_controls_btn")
    assert toggle.isChecked() and toggle.text() == "Show controls ▸"
    assert not ctx.widgets.get("_nw_popout_tps_btn").isVisibleTo(popout)
    top = card.mapTo(popout, card.rect().topLeft()).y()
    assert top < popout.height() * 0.25, f"the figure starts {top} px down a {popout.height()} px window"
    toggle.click()                                    # the controls are one tap away, and open they fit the width
    _pump(qapp, 20)
    assert ctx.widgets.get("_nw_popout_tps_btn").isVisibleTo(popout)
    for name in ("_nw_popout_image_channel_cb", "_nw_popout_source_cb"):
        combo = ctx.widgets.get(name)
        assert combo.width() < popout.width() * 0.6, f"{name} is as wide as its longest entry"


def test_a_footer_too_long_for_the_figure_is_cut_with_a_line_saying_so():
    from matplotlib.figure import Figure

    from review.features.nanowell import renderer as r

    fig = Figure(figsize=(4, 8), dpi=100)
    lines = [f"line {i}" for i in range(30)]
    kept = r._footer_that_fits(fig, lines)
    assert len(kept) == max(2, int(r._FOOTER_MAX_SHARE / r._line_step(fig)))
    assert kept[:-1] == lines[:len(kept) - 1]
    assert kept[-1] == f"… {30 - (len(kept) - 1)} more lines: enlarge the card to read them."
    assert r._footer_that_fits(fig, lines[:3]) == lines[:3]          # a footer that fits is untouched


def test_the_phone_layout_keeps_panels_clear_of_every_line_of_a_footer():
    """After an engine has laid the figure out, ``subplotpars.bottom`` no longer says where the renderer's footer ends,
    so footer lines above it were read as panel text and the panels were laid out over them."""
    from matplotlib.figure import Figure

    from review.core import plot_style

    fig = Figure(figsize=(4, 8), dpi=100)
    fig.subplots_adjust(bottom=0.1)
    fig.text(0.07, 0.035, "low", gid="nw-traj-footer")
    fig.text(0.07, 0.30, "high line of the same footer", gid="nw-traj-footer")
    band = plot_style._figure_text_band(fig)
    assert band is not None and band[1] > 0.30          # the panels start above the highest footer line


def test_test_detection_puts_its_controls_in_a_flow_so_the_montage_starts_high(qapp, phone):  # noqa: F811
    """In the phone's form of the window (its content in a scroll area, as the app shows it), the montage began 496 px
    down a 932 px window under one control per line; the flow takes it to 281."""
    from pathlib import Path

    from analyze.nanowell_preview import NanowellPreviewWindow
    from tests.test_phone_windows import _open, _shell

    phone(UPRIGHT)
    win, shell = _shell(qapp)
    window = NanowellPreviewWindow(QWidget(win), {"A01": Path("a.zip")}, {"A01": ["t0"]})
    _open(qapp, shell, window)
    _pump(qapp, 20)
    top = window._scroll.mapTo(window, window._scroll.rect().topLeft()).y()
    assert top < 330, f"the montage starts {top} px down: one control per line again"


def test_a_saved_plot_card_is_narrower_than_a_phone(qapp):  # noqa: F811
    """Its seven action buttons were one row, whose width became the card's minimum: wider than a 432 px phone, which
    cut the saved plot off at the right edge. They are two rows now."""
    from review.features.saved_plots.state import SavedPlot, SavedPlotsStore
    from review.features.saved_plots.tab_view import _build_saved_card

    rec = SavedPlot(id="x", created_at="2026-11-07T10:00:00", tab="Distribution", title="A saved plot",
                    png_filename="x.png", params={})
    store = SavedPlotsStore()
    store.image_path = lambda _ctx, _rec: None          # no image on disk: the card's actions are what is measured
    card = _build_saved_card(None, store, rec, is_first=False, is_last=False)
    assert card.minimumSizeHint().width() < 380, card.minimumSizeHint().width()
