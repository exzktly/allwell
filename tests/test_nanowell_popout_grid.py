"""The **Nanowell crops** pop-out's choice of timepoints, channels and grid shape, and its **Save ▾**.

Asked for: a way to save one nanowell over time as a montage, choosing which timepoints are shown, whether several
channels are shown at once, and the shape of the grid it is drawn on. The rules are ``crop_grid_compute``'s and are
pinned on their own first; then every control is driven through the real pop-out window — its menus' own actions,
triggered as a click triggers them — and what is checked is read back from the figure's artists and from the call
the Save entry makes.
"""

from __future__ import annotations

import numpy as np
import pytest

from review.features.nanowell.crop_grid_compute import (
    grid_label, grid_shape, save_name, shown_channels, shown_timepoints,
)
from review.features.nanowell.state import NanowellState


# ── the rules ────────────────────────────────────────────────────────────────
def _cells(shape, n_channels, n_tps):
    return [[shape.place(c, k) for k in range(n_tps)] for c in range(n_channels)]


def test_one_channel_on_auto_keeps_the_near_square_wrap_the_pop_out_always_drew():
    shape = grid_shape(1, 5)
    assert (shape.rows, shape.cols) == (2, 3)
    assert _cells(shape, 1, 5) == [[(0, 0), (0, 1), (0, 2), (1, 0), (1, 1)]]


def test_several_channels_on_auto_take_a_row_each_with_the_timepoints_across():
    shape = grid_shape(2, 3)
    assert (shape.rows, shape.cols) == (2, 3)
    assert _cells(shape, 2, 3) == [[(0, 0), (0, 1), (0, 2)], [(1, 0), (1, 1), (1, 2)]]


def test_one_column_runs_the_timepoints_down_with_a_column_per_channel():
    shape = grid_shape(2, 3, "column")
    assert (shape.rows, shape.cols) == (3, 2)
    assert _cells(shape, 2, 3) == [[(0, 0), (1, 0), (2, 0)], [(0, 1), (1, 1), (2, 1)]]


def test_one_row_puts_every_timepoint_across():
    shape = grid_shape(1, 5, "row")
    assert (shape.rows, shape.cols) == (1, 5)


def test_n_across_wraps_the_timepoints_and_keeps_each_line_s_channels_together():
    """Three timepoints, two across, two channels: the first line holds t0 and t1 of both channels (rows 0 and 1),
    the second line t2 of both (rows 2 and 3) — a channel's crops of one timepoint are never split from the other's."""
    shape = grid_shape(2, 3, "columns", 2)
    assert (shape.rows, shape.cols) == (4, 2)
    assert _cells(shape, 2, 3) == [[(0, 0), (0, 1), (2, 0)], [(1, 0), (1, 1), (3, 0)]]
    assert grid_shape(1, 3, "columns", 9).cols == 3                     # never wider than there are timepoints


def test_the_timepoints_shown_keep_the_nanowell_s_order_and_a_stale_choice_shows_them_all():
    assert shown_timepoints(["t_c", "t_a", "t_b"], ["t_b", "t_c"]) == ["t_c", "t_b"]
    assert shown_timepoints(["t_c", "t_a"], []) == ["t_c", "t_a"]
    assert shown_timepoints(["t_c", "t_a"], ["t_z"]) == ["t_c", "t_a"]


def test_the_image_channel_is_drawn_first_and_the_added_ones_in_the_dataset_s_order():
    available = ["", "EGFP", "mCherry"]
    assert shown_channels("mCherry", ["EGFP", "", "mCherry", "Cy5"], available) == ["mCherry", "", "EGFP"]
    assert shown_channels("", [], available) == [""]


def test_the_save_name_says_which_nanowell_and_which_channels():
    assert save_name(("A01", 3, -2), ["", "EGFP"]) == "A01_q3_r-2_phase_EGFP_over_time.png"
    assert grid_label("columns", 4) == "Grid: 4 across" and grid_label("bogus", 0) == "Grid: auto"


def test_the_pop_out_s_choices_are_kept_with_the_page_and_a_bad_value_is_dropped():
    st = NanowellState()
    st.popout_tps, st.popout_channels, st.popout_grid, st.popout_cols = ["t_a"], ["EGFP"], "columns", 3
    again = NanowellState()
    again.apply_config(st.to_config())
    assert (again.popout_tps, again.popout_channels, again.popout_grid, again.popout_cols) == (
        ["t_a"], ["EGFP"], "columns", 3)
    again.apply_config({"popout_grid": "spiral", "popout_cols": 99, "popout_tps": "t_a", "popout_channels": [3]})
    assert (again.popout_tps, again.popout_channels, again.popout_grid, again.popout_cols) == ([], [], "auto", 0)


def test_timepoints_none_is_kept_with_the_page_and_only_a_true_value_sets_it():
    st = NanowellState()
    st.popout_tps_none = True
    again = NanowellState()
    again.apply_config(st.to_config())
    assert again.popout_tps_none is True
    again.apply_config({"popout_tps_none": "yes"})
    assert again.popout_tps_none is False


# ── through the real pop-out ─────────────────────────────────────────────────
pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell.image_compute import Crop  # noqa: E402
from tests.test_nanowell_crop_view import _images, _press_pop_out  # noqa: E402
from tests.test_nanowell_page import _click_dot, _loaded, page  # noqa: E402,F401

#: Phase crops read 0-10 and 0-100; every fluorescence crop 1000-2000, so a fluorescence panel drawn on the phase
#: scale — or a phase panel on the fluorescence one — has the wrong limits.
PHASE = {"t_c": np.linspace(0, 10, 400).reshape(20, 20), "t_a": np.linspace(0, 100, 400).reshape(20, 20)}
FLUOR = {"t_c": np.linspace(1000, 1500, 400).reshape(20, 20), "t_a": np.linspace(1500, 2000, 400).reshape(20, 20)}


@pytest.fixture
def channel_crops(monkeypatch):
    asked = []

    def site_crops(data, metrics, site, *, channel="", **_kw):
        asked.append(channel)
        source = FLUOR if channel else PHASE
        return {tp: Crop(image.copy(), "tile", "") for tp, image in source.items()}

    monkeypatch.setattr(nwc, "site_crops", site_crops)
    return asked


def _open(qtbot, ctx):
    st = _loaded(qtbot, ctx)
    _click_dot(qtbot, ctx, ("A01", 1, 0))
    _press_pop_out(qtbot, ctx)
    qtbot.waitUntil(lambda: len(_images(ctx, "_nw_popout_fig")) == 2, timeout=10000)
    ctx.widgets.get("_nw_popout_rebuild_menus")()                      # what opening each menu does
    return st


def _action(ctx, menu_key, data):
    """The entry of *data* in a menu: a Timepoints or Channels entry is the checkbox inside it (ticking one leaves the
    menu open), a Grid entry the action itself."""
    for action in ctx.widgets.get(menu_key).actions():
        if action.data() == data:
            return getattr(action, "defaultWidget", lambda: None)() or action
    raise AssertionError(f"no entry {data!r} in {menu_key}")


def _panels(ctx):
    """``(row, column, title, clim)`` of every image panel of the pop-out, in row-major order."""
    out = []
    for ax in ctx.widgets.get("_nw_popout_fig").axes:
        for image in ax.get_images():
            spec = ax.get_subplotspec()
            out.append((spec.rowspan.start, spec.colspan.start, ax.get_title(), tuple(round(v, 3) for v in image.get_clim())))
    return sorted(out)


def _menu_entry(ctx, text):
    for action in ctx.widgets.get("_nw_popout_tps_menu").actions():
        if action.text() == text:
            return action
    raise AssertionError(f"no entry {text!r} in the Timepoints menu")


def _said(ctx):
    return [t.get_text() for ax in ctx.widgets.get("_nw_popout_fig").axes for t in ax.texts]


def test_timepoints_chooses_the_panels_and_the_last_tick_taken_away_is_none(page, qtbot, channel_crops):  # noqa: F811
    st = _open(qtbot, page)
    assert page.widgets.get("_nw_popout_tps_btn").text() == "Timepoints: all"
    _action(page, "_nw_popout_tps_menu", "t_a").click()              # a checkbox entry: a click unticks it
    assert st.popout_tps == ["t_c"]
    assert [p[2] for p in _panels(page)] == ["t_c"]
    assert page.widgets.get("_nw_popout_tps_btn").text() == "Timepoints: 1 of 2"
    page.widgets.get("_nw_popout_rebuild_menus")()
    _action(page, "_nw_popout_tps_menu", "t_c").click()              # the last tick: no panel, said so
    assert (st.popout_tps, st.popout_tps_none) == ([], True)
    assert _panels(page) == [] and nwc.NO_TIMEPOINTS_SAID in _said(page)
    page.widgets.get("_nw_popout_rebuild_menus")()
    _menu_entry(page, "All").trigger()
    assert (st.popout_tps, st.popout_tps_none) == ([], False)
    assert sorted(p[2] for p in _panels(page)) == ["t_a", "t_c"]


def test_none_clears_every_tick_and_a_tick_from_none_shows_that_timepoint_alone(page, qtbot, channel_crops):  # noqa: F811
    st = _open(qtbot, page)
    _menu_entry(page, "None").trigger()
    assert page.widgets.get("_nw_popout_tps_btn").text() == "Timepoints: none"
    assert _panels(page) == [] and nwc.NO_TIMEPOINTS_SAID in _said(page)
    page.widgets.get("_nw_popout_rebuild_menus")()
    assert not any(_action(page, "_nw_popout_tps_menu", tp).isChecked() for tp in ("t_a", "t_c"))
    _action(page, "_nw_popout_tps_menu", "t_c").click()              # unticked: a click ticks it
    assert (st.popout_tps, st.popout_tps_none) == (["t_c"], False)
    assert [p[2] for p in _panels(page)] == ["t_c"]


def test_channels_adds_a_row_per_channel_each_on_its_own_scale(page, qtbot, channel_crops):  # noqa: F811
    st = _open(qtbot, page)
    channel = st.data.image_channels[0]
    image_channel = _action(page, "_nw_popout_channels_menu", "")
    assert image_channel.isChecked() and not image_channel.isEnabled()  # the image channel is always drawn
    _action(page, "_nw_popout_channels_menu", channel).click()
    assert st.popout_channels == [channel] and channel in channel_crops
    qtbot.waitUntil(lambda: len(_images(page, "_nw_popout_fig")) == 4, timeout=10000)
    phase_scale = tuple(round(v, 3) for v in np.percentile(np.concatenate([a.ravel() for a in PHASE.values()]), [1, 99]))
    fluor_scale = tuple(round(v, 3) for v in np.percentile(np.concatenate([a.ravel() for a in FLUOR.values()]), [1, 99]))
    assert _panels(page) == [(0, 0, "phase · t_c", phase_scale), (0, 1, "phase · t_a", phase_scale),
                             (1, 0, f"{channel} · t_c", fluor_scale), (1, 1, f"{channel} · t_a", fluor_scale)]
    assert page.widgets.get("_nw_popout_channels_btn").text() == "Channels: 2"
    title = [t.get_text() for t in page.widgets.get("_nw_popout_fig").texts if t.get_gid() == "nw-crop-grid-title"]
    assert title == [f"A01 · nanowell 1,0 over time · phase image + {channel} image"]


def test_grid_lays_the_panels_out_in_the_shape_chosen(page, qtbot, channel_crops):  # noqa: F811
    st = _open(qtbot, page)
    channel = st.data.image_channels[0]
    _action(page, "_nw_popout_channels_menu", channel).click()
    qtbot.waitUntil(lambda: len(_images(page, "_nw_popout_fig")) == 4, timeout=10000)
    page.widgets.get("_nw_popout_rebuild_menus")()
    _action(page, "_nw_popout_grid_menu", ("column", 0)).trigger()
    assert (st.popout_grid, st.popout_cols) == ("column", 0)
    assert [p[:3] for p in _panels(page)] == [(0, 0, "phase · t_c"), (0, 1, f"{channel} · t_c"),
                                              (1, 0, "phase · t_a"), (1, 1, f"{channel} · t_a")]
    assert page.widgets.get("_nw_popout_grid_btn").text() == "Grid: one column"
    _action(page, "_nw_popout_grid_menu", ("row", 0)).trigger()
    assert [p[:2] for p in _panels(page)] == [(0, 0), (0, 1), (1, 0), (1, 1)]


def test_save_figure_saves_the_pop_out_s_figure_under_the_nanowell_s_name(page, qtbot, channel_crops, monkeypatch):  # noqa: F811
    st = _open(qtbot, page)
    channel = st.data.image_channels[0]
    _action(page, "_nw_popout_channels_menu", channel).click()
    saved = []
    monkeypatch.setattr(type(page.figures), "save", lambda self, fig, name: saved.append((fig, name)))
    entries = {a.text(): a for a in page.widgets.get("_nw_popout_save_menu").actions() if a.text()}
    assert list(entries) == ["Save figure…", "Copy figure as SVG", "Save to Saved Plots"]
    entries["Save figure…"].trigger()
    assert saved == [(page.widgets.get("_nw_popout_fig"), f"A01_q1_r0_phase_{channel}_over_time.png")]
    assert page.widgets.get("_nw_popout_save_btn").window() is page.widgets.get("_nw_popout")


def test_the_timepoints_menu_stays_open_while_several_are_ticked(page, qtbot, channel_crops):  # noqa: F811
    """A real click on an entry of the open menu, as a person ticks it: the menu must still be open for the next."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    st = _open(qtbot, page)
    menu = page.widgets.get("_nw_popout_tps_menu")
    nwc.set_popout_no_timepoints(page)
    menu.popup(QPoint(0, 0))
    qtbot.waitUntil(menu.isVisible, timeout=5000)

    def click(tp, *, far_right=False):
        # Where a person clicks an entry: on its label, or anywhere along its row. The menu routes the click to
        # whatever is there — the entry's checkbox, or for a plain checkable action the action, which closes it.
        action = next(a for a in menu.actions() if a.data() == tp)
        rect = menu.actionGeometry(action)
        at = QPoint(rect.right() - 4 if far_right else rect.left() + 12, rect.center().y())
        target = menu.childAt(at) or menu
        QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, target.mapFrom(menu, at) if target is not menu else at)

    click("t_a")
    assert menu.isVisible() and st.popout_tps == ["t_a"]
    click("t_c", far_right=True)                                      # both ticked is every timepoint
    assert menu.isVisible() and (st.popout_tps, st.popout_tps_none) == ([], False)
    menu.close()
