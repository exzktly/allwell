"""Properties → Limits & Scale → Colour limits: MATLAB's ``clim`` for every colour-mapped plot with a colour bar.

Asked: the colours of the Array view's nanowells are scaled automatically, and the Properties bar should let the
range be set, as ``clim`` does. ``plot_style._apply_colour_limits`` sets it in the style pass every redraw ends in, so
it reaches each figure with a colour bar; the Heat Map keeps its own Display ▾ range (``_aw_owns_clim``).

The fixture's values run 1 to 9, so the plot's own range (1, 9) differs from every limit typed here, and a pass that
did nothing, or set only one end, or applied a range that is no range, gives a different pair than the one asserted.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("matplotlib")

from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from review.core.plot_style import (  # noqa: E402
    AXIS_LIMIT_PREF_KEYS, DEFAULT_EXPORT_STYLE_PREFS, apply_export_style_prefs, colour_mapped,
)


def _figure(*, norm=None, owned=False):
    fig = Figure()
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(1, 1, 1)
    coll = ax.scatter(np.arange(9), np.arange(9), c=np.arange(1.0, 10.0), norm=norm)
    if owned:
        coll._aw_owns_clim = True
    bar = fig.colorbar(coll, ax=ax)
    return fig, coll, bar


def _styled(fig, **prefs):
    full = dict(DEFAULT_EXPORT_STYLE_PREFS)
    full.update(prefs)
    apply_export_style_prefs(fig, full)


def test_both_ends_set_the_range_and_the_colour_bar_follows():
    fig, coll, bar = _figure()
    _styled(fig, c_lim_min="2", c_lim_max="5")
    assert coll.get_clim() == (2.0, 5.0)
    assert (bar.norm.vmin, bar.norm.vmax) == (2.0, 5.0)
    fig.canvas.draw()
    assert bar.ax.get_ylim() == pytest.approx((2.0, 5.0))


def test_a_blank_end_keeps_the_plot_s_own_and_blanking_gives_it_back():
    fig, coll, _bar = _figure()
    _styled(fig, c_lim_max="4")
    assert coll.get_clim() == (1.0, 4.0)
    _styled(fig)                                        # both blank again, no redraw in between
    assert coll.get_clim() == (1.0, 9.0)


@pytest.mark.parametrize("lo,hi", [("6", "3"), ("5", "5"), ("abc", ""), ("1e", "-")])
def test_text_that_is_no_range_leaves_the_plot_s_own(lo, hi):
    fig, coll, _bar = _figure()
    _styled(fig, c_lim_min=lo, c_lim_max=hi)
    assert coll.get_clim() == (1.0, 9.0)


def test_a_non_positive_low_end_on_a_log_scale_keeps_the_plot_s_own_low_end():
    fig, coll, _bar = _figure(norm=LogNorm())
    _styled(fig, c_lim_min="0", c_lim_max="3")
    assert coll.get_clim() == (1.0, 3.0)


def test_a_range_its_renderer_owns_is_left_alone():
    fig, coll, _bar = _figure(owned=True)
    _styled(fig, c_lim_min="2", c_lim_max="5")
    assert coll.get_clim() == (1.0, 9.0) and colour_mapped(fig) == []


def test_a_plot_without_a_colour_bar_is_not_a_colour_scale():
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    coll = ax.scatter([0, 1], [0, 1], c=[1.0, 9.0])
    _styled(fig, c_lim_min="2", c_lim_max="5")
    assert coll.get_clim() == (1.0, 9.0)


def test_the_limits_are_per_figure_and_dropped_with_the_others():
    """In the limit keys: stored per figure, cleared by Auto scale and on a dataset switch with X and Y."""
    assert {"c_lim_min", "c_lim_max"} <= AXIS_LIMIT_PREF_KEYS


def test_the_array_view_s_filled_nanowells_take_the_range():
    """Through the real Array renderer, the figure the request was about."""
    from review.features.nanowell.array_compute import ArrayScene, ArraySites
    from review.features.nanowell.renderer import render_array
    from review.features.nanowell.state import ArrayLayers

    n = 5
    sites = ArraySites(np.arange(n) * 100.0, np.zeros(n), np.full(n, 40.0), np.array([1.0, 2.0, 3.0, 4.0, 9.0]),
                       ["measured"] * n, np.zeros(n, dtype=bool), [("A01", k, 0) for k in range(n)],
                       [f"{k},0" for k in range(n)])
    fig = Figure(figsize=(8, 3))
    FigureCanvasAgg(fig)
    drawn = render_array(fig, ArrayScene("A01", "t", "lattice", sites, n, 0, {}, 100.0),
                         ArrayLayers(image="off", colour="metric", addresses=False, values=False), label="Cell count")
    (fill,) = [c for c in drawn.ax.collections if c.get_array() is not None and c.colorbar is not None]
    assert fill.get_clim() == (1.0, 9.0)
    _styled(fig, c_lim_min="0", c_lim_max="4")
    assert fill.get_clim() == (0.0, 4.0)
    # The nanowell holding 9 now takes the top colour, the one holding 4 too.
    colours = fill.to_rgba(fill.get_array())
    assert np.allclose(colours[3], colours[4])


def test_typing_in_the_properties_bar_recolours_the_figure(qtbot, monkeypatch):
    """Through the real panel: the two fields under Limits & Scale, committed as a user commits them (Enter), reach
    the figure; Auto scale blanks them."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QLineEdit

    from review.features.export.properties_view import ExportStyleSidebar
    from tests.test_properties_hides_what_it_cannot_do import _stub_app

    fig, coll, _bar = _figure()
    app = _stub_app()
    app._nw_fig = fig
    monkeypatch.setattr(ExportStyleSidebar, "_redraw_bound_figure", lambda self: True, raising=False)
    sb = ExportStyleSidebar(app, None, fig, None, default_name="_nw_fig")
    qtbot.addWidget(sb)
    edits = {e.accessibleName(): e for e in sb.findChildren(QLineEdit)}
    lo, hi = edits["Colour limit, low end"], edits["Colour limit, high end"]
    lo.setText("3")
    lo.editingFinished.emit()
    hi.setText("6")
    hi.editingFinished.emit()
    assert coll.get_clim() == (3.0, 6.0)
    sb._autoscale_axes()
    assert lo.text() == "" and hi.text() == ""
    assert coll.get_clim() == (1.0, 9.0)


#: Which feature package draws each figure the Colour limits row is offered on.
_FEATURE_OF = {"_nw_fig": "nanowell", "_sp_fig": "spatial", "_mv_fig": "multivariate"}


def test_the_row_is_offered_exactly_where_a_colour_bar_is_drawn():
    """The figures declaring the ``colour_limits`` style facet, held against what they claim: the features whose code
    makes a colour bar. A new feature that draws one, and does not declare the facet, fails here rather than going
    without the row; the Heat Map is the one exemption, its range being Display ▾'s (``_aw_owns_clim``)."""
    from pathlib import Path

    from review.features.registry import build_feature_registry

    offered = {attr for attr, facets in build_feature_registry().style_facets().items()
               if "colour_limits" in facets.has}

    root = Path(__file__).resolve().parents[1] / "review" / "features"
    drawing = {pkg.name for pkg in root.iterdir() if pkg.is_dir()
               and any("colorbar(" in f.read_text(encoding="utf-8") for f in pkg.rglob("*.py"))}
    assert drawing - {"heatmap"} == {_FEATURE_OF[attr] for attr in offered}


@pytest.mark.parametrize("kind", ["_line_fig", "_bar_fig", "_scatter_fig", "_heatmap_fig"])
def test_a_figure_without_a_colour_scale_is_not_offered_the_row(qtbot, monkeypatch, kind):
    pytest.importorskip("PySide6")
    from tests.test_properties_hides_what_it_cannot_do import _row_labels, _sidebar

    _app, sb = _sidebar(qtbot, monkeypatch, kind=kind)
    assert "Colour limits" not in _row_labels(sb, "Limits & Scale")
    assert not {"c_lim_min", "c_lim_max"} & set(sb._getters)
