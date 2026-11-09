"""Properties → Group label size reaches the bar plot's group labels.

Reported: the bar plot's group labels did not respond to the control. Two
causes. The labels under each block of timepoint bars were sized once, by the
renderer, while a Properties edit re-runs only the style pass — so an edit
changed nothing until something else redrew the plot. And the band and
sub-band labels above each panel (*Split by* / *Then by*, and the per-cell
group headers) were drawn at a fixed 8 / 7 pt and never read the setting.

Every label is drawn by the Bar tab's own renderer; every size is asserted as
a number, at a setting (20) no default produces.
"""

from __future__ import annotations

from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from review.core.plot_style import DEFAULT_EXPORT_STYLE_PREFS, apply_export_style_prefs  # noqa: E402
from tests.test_bar_geometry import _two_tp_ctx  # noqa: E402
from tests.test_colour_and_split_by_variable import _nested_ctx  # noqa: E402


def _drawn(ctx, timepoints, fig=None):
    from review.features.barplot.renderer import draw_grouped_bar_mode

    ctx.widgets = SimpleNamespace(get_or_none=lambda _name: None)
    if fig is None:
        fig = plt.figure(figsize=(9, 9))
    axes = [fig.add_subplot(3, 1, i) for i in (1, 2, 3)]
    ctx.runtime.barplot.fig = fig
    ctx.runtime.barplot.canvas = SimpleNamespace(draw_idle=lambda: None)
    draw_grouped_bar_mode(ctx, ax_mean=axes[0], ax_frac=axes[1], ax_n=axes[2], active_rsets=[],
                          target_ts=list(timepoints), tp_str="0", threshold=0.5, band_lbl="SEM",
                          use_sem=True)
    return fig, axes


def _styled(fig, **prefs):
    merged = dict(DEFAULT_EXPORT_STYLE_PREFS)
    merged.update(prefs)
    apply_export_style_prefs(fig, merged)


def _sizes(ax, names):
    return sorted((t.get_text(), t.get_fontsize()) for t in ax.texts if t.get_text() in names)


def _footers(ax):
    return sorted((t.get_text(), t.get_fontsize()) for t in ax.texts
                  if tuple(getattr(t, "xyann", (0, 0))) == (0, -32))


def test_the_labels_under_each_block_take_the_size_from_the_style_pass():
    fig, (mean, frac, n) = _drawn(_two_tp_ctx("well"), (0.0, 24.0))
    _styled(fig, group_label_size=20)
    for ax in (mean, frac, n):
        assert _footers(ax) == [("CTRL", 20.0), ("TREAT", 20.0)]
    plt.close(fig)


def test_one_panel_s_own_size_leaves_the_others_at_the_shared_one():
    fig, (mean, frac, _n) = _drawn(_two_tp_ctx("well"), (0.0, 24.0))
    _styled(fig, group_label_size=11, axis_buckets={"2": {"group_label_size": 20}})
    assert _footers(mean) == [("CTRL", 11.0), ("TREAT", 11.0)]
    assert _footers(frac) == [("CTRL", 20.0), ("TREAT", 20.0)]
    plt.close(fig)


def test_the_bands_above_each_panel_follow_it_and_stay_clear_of_the_title():
    """Band 20, sub-band one point smaller, the band row lifted clear of the
    sub-band row and the title clear of both — at the bigger size, not the 8 pt
    the rows were spaced for."""
    fig, (mean, *_rest) = _drawn(_nested_ctx(), (0.0,))
    _styled(fig, group_label_size=20)
    assert _sizes(mean, {"HeLa", "U2OS"}) == [("HeLa", 20.0), ("U2OS", 20.0)]
    assert _sizes(mean, {"0 µM", "10 µM"}) == [("0 µM", 19.0), ("0 µM", 19.0),
                                                ("10 µM", 19.0), ("10 µM", 19.0)]
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    box = {t.get_text(): t.get_window_extent(renderer) for t in mean.texts}
    assert box["0 µM"].y1 <= box["HeLa"].y0
    assert box["HeLa"].y1 <= mean.title.get_window_extent(renderer).y0
    plt.close(fig)


def test_the_default_size_draws_the_rows_where_they_always_were():
    from review.features.barplot._annotations import _BAND_ROW_LIFT_PT, _TITLE_PAD_PT

    assert (_BAND_ROW_LIFT_PT, _TITLE_PAD_PT) == (11, (6, 18, 30))
    fig, (mean, *_rest) = _drawn(_nested_ctx(), (0.0,))
    _styled(fig)
    assert _sizes(mean, {"HeLa", "U2OS"}) == [("HeLa", 8.0), ("U2OS", 8.0)]
    assert [tuple(t.xyann) for t in mean.texts if t.get_text() == "HeLa"] == [(0, 11.0)]
    plt.close(fig)


@pytest.mark.qt
def test_the_properties_control_resizes_the_labels_on_screen(qtbot, monkeypatch):
    """The reported path: the Group label size stepper, pressed, with no
    redraw of the plot behind it."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    from review.features.export.properties_view import ExportStyleSidebar
    from tests.test_properties_value_labels import _stub_app

    fig = Figure(figsize=(9, 9))
    canvas = FigureCanvasAgg(fig)
    app = _stub_app()
    app._bar_fig = fig
    _drawn(_two_tp_ctx("well"), (0.0, 24.0), fig=fig)
    redraws: list[int] = []
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_active_plot",
                        lambda self: redraws.append(1), raising=False)
    sb = ExportStyleSidebar(app, None, fig, canvas, default_name="bar")
    qtbot.addWidget(sb)
    sb._setters["group_label_size"](20)
    assert _footers(fig.axes[0]) == [("CTRL", 20.0), ("TREAT", 20.0)]
    assert redraws == []
