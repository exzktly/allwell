"""The Nanowells page's figures read at the size they are drawn: type scaled to the panel, axes on the card's own
ground, one time axis in hours, one y scale, and the Position panels on one ring axis.

Reported: "axes labelling and sizing is a disaster on the Nanowells tabs". A rendering of every view showed the
Properties pass putting its 18 pt default ticks, labels and titles — sized for one plot filling its card — on
every panel of a grid of wells, so the tick labels "0 h · t00 … 48 h · t48" ran into one another; and every axes
the page added kept matplotlib's white face on the dark screen card, where the light occupancy shades and the
light legend text vanished. Driven through the real builder and the page's own redraw (which ends in the
Properties pass), in the minimal shell of ``tests/test_nanowell_page.py``; what is checked is read back from the
figure.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from review.features.nanowell import controller as nwc  # noqa: E402
from review.features.nanowell import renderer  # noqa: E402
from tests.test_nanowell_page import _loaded, page  # noqa: E402,F401


def _panels(ctx):
    """The page figure's plot panels, in drawing order (the occupancy key and any colour bar left out)."""
    return [ax for ax in ctx.widgets.get("_nw_fig").axes if ax.get_title(loc="left")]


def _all_wells(ctx):
    ctx.state.selection.selected_wells = {"A01", "A02", "B01"}


def test_over_time_the_ticks_are_hours_under_one_time_label_and_the_names_are_in_the_footer(page, qtbot):  # noqa: F811
    _all_wells(page)
    _loaded(qtbot, page)
    panels = _panels(page)
    assert [ax.get_title(loc="left") for ax in panels] == ["A01", "A02", "B01"]
    for ax in panels:
        assert [t.get_text() for t in ax.get_xticklabels()] == ["0", "5"]      # not "0 h · t_c", "5 h · t_a"
        assert ax.get_xlabel() == "Time (h)"                                   # one row: every panel is the bottom
    footer = [t.get_text() for t in page.widgets.get("_nw_fig").texts if t.get_gid() == "nw-footer"]
    assert "Timepoints: 0 h t_c · 5 h t_a." in footer


def test_the_properties_sizes_are_scaled_to_the_panels_and_still_follow_the_sidebar(page, qtbot):  # noqa: F811
    from review.core import plot_style

    _all_wells(page)
    _loaded(qtbot, page)
    ax = _panels(page)[0]
    # The page's redraw ends in the Properties pass; at its 18 pt defaults a panel of a grid is drawn at 9 pt.
    assert {t.get_fontsize() for t in ax.get_xticklabels() + ax.get_yticklabels()} == {9.0}
    assert ax.yaxis.label.get_fontsize() == 9.0
    plot_style._ensure_export_style_prefs(page)["tick_label_size"] = 22
    nwc.redraw(page)
    ax = _panels(page)[0]
    assert {t.get_fontsize() for t in ax.get_xticklabels()} == {11.0}      # raising the size still raises it


def test_every_panel_is_drawn_on_the_card_s_ground_not_matplotlib_s_white(page, qtbot):  # noqa: F811
    from matplotlib.colors import to_hex

    from review.core.plot_style import tokens_for

    _all_wells(page)
    _loaded(qtbot, page)
    for view in ("distribution", "trajectories", "position"):
        nwc.set_view(page, view)
        for ax in _panels(page):
            assert to_hex(ax.get_facecolor()) == to_hex(tokens_for(ax)[0]) != "#ffffff", view


def test_the_panels_share_one_y_scale_written_on_the_left_column_only(page, qtbot):  # noqa: F811
    _all_wells(page)
    _loaded(qtbot, page)
    panels = _panels(page)
    assert len({ax.get_ylim() for ax in panels}) == 1
    shown = [any(t.get_visible() and t.get_text() for t in ax.get_yticklabels()) for ax in panels]
    assert shown == [True, False, False]
    # Whole-number ticks on a count: 2.5 cells is no value a reader can have.
    assert all(float(v).is_integer() for v in panels[0].get_yticks())


def test_trajectories_are_drawn_against_hours_with_the_occupancy_key(page, qtbot):  # noqa: F811
    _all_wells(page)
    _loaded(qtbot, page)
    nwc.set_view(page, "trajectories")
    panels = _panels(page)
    assert [ax.get_xlabel() for ax in panels] == ["Time (h)"] * 3
    assert all(t.get_text() not in ("t_c", "t_a") for ax in panels for t in ax.get_xticklabels())
    fig = page.widgets.get("_nw_fig")
    key = [t.get_text() for ax in fig.axes for t in ax.texts if t.get_gid() == "nw-legend"]
    assert key[0] == "Thick lines · cells at baseline:" and key[1:] == ["0", "1", "2", "3+", "?"]


def test_position_s_two_panels_share_the_ring_axis(page, qtbot):  # noqa: F811
    _all_wells(page)
    _loaded(qtbot, page)
    nwc.set_view(page, "position")
    fig = page.widgets.get("_nw_fig")
    top, bars = fig.axes[0], fig.axes[1]
    assert bars.get_shared_x_axes().joined(top, bars)
    assert top.get_xlim() == bars.get_xlim()


def test_the_extremes_captions_are_drawn_at_a_crop_s_size(page, qtbot):  # noqa: F811
    _loaded(qtbot, page)
    nwc.set_view(page, "extremes")
    fig = page.widgets.get("_nw_fig")
    rows = [ax.yaxis.label for ax in fig.axes if ax.yaxis.label.get_gid() == "nw-extremes-row"]
    assert rows and {round(label.get_fontsize(), 2) for label in rows} == {round(18 * renderer.EXTREMES_TYPE_SCALE, 2)}


@pytest.mark.parametrize("n, shape", [(1, (1, 1)), (4, (1, 4)), (5, (2, 3)), (6, (2, 3)), (7, (2, 4)),
                                      (12, (3, 4))])
def test_wells_are_spread_evenly_over_the_rows(n, shape):
    assert renderer._grid(n) == shape


def test_the_style_pass_scales_an_axes_that_asks_and_leaves_the_rest(qtbot):
    from matplotlib.figure import Figure

    from review.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS, apply_export_style_prefs

    fig = Figure()
    plain, scaled = fig.add_subplot(1, 2, 1), fig.add_subplot(1, 2, 2)
    scaled._aw_type_scale = 0.5
    for ax in (plain, scaled):
        ax.set_ylabel("y")
    apply_export_style_prefs(fig, dict(DEFAULT_EXPORT_STYLE_PREFS))
    assert {t.get_fontsize() for t in plain.get_xticklabels()} == {18.0}
    assert {t.get_fontsize() for t in scaled.get_xticklabels()} == {9.0}
    assert (plain.yaxis.label.get_fontsize(), scaled.yaxis.label.get_fontsize()) == (18.0, 9.0)
