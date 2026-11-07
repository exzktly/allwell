"""A Nanowells figure redrawn on a phone keeps its whole footer out of the panels.

Phone layout puts constrained layout on a canvas's figure (``plot_style._apply_layout_engine``) and reserves, as the
engine's rectangle, the band the figure's own text occupies below ``fig.subplotpars.bottom``
(``plot_style._figure_text_band``). ``fig.clear()`` keeps the engine, and under it matplotlib ignores
``subplots_adjust`` — so the next view drawn on the same canvas left ``subplotpars`` at the margins of whatever had
been drawn before the phone was entered. Once the Distribution view's footer moved out of its figure, that stale
bottom was 0.10, the Position and Trajectories footers' upper lines sat above it, and the phone pass laid the
panels' x-axis label over them (``tests/test_every_page_renders.py``, nanowell on a phone).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from review.core import plot_style  # noqa: E402
from review.features.nanowell import renderer  # noqa: E402
from review.features.nanowell.views_compute import PositionSeries  # noqa: E402

_NOTE = ("Focus unchecked: 22 under 10 rims are not yet checked against the focus rule; "
         "addresses: A01, A02, B01 not yet verified — see the Identity view.")


def _phone_figure_after_distribution() -> Figure:
    """The figure as the phone leaves it: the Distribution's 0.10 bottom, then constrained layout on top."""
    fig = Figure(figsize=(9.1, 3.59), dpi=100)
    fig.add_subplot()
    fig.subplots_adjust(bottom=0.10)
    fig.set_layout_engine("constrained")
    return fig


def _series(well: str) -> PositionSeries:
    rings = np.arange(4, dtype=float)
    return PositionSeries(well=well, rings=rings, median=rings + 1, q1=rings, q3=rings + 2,
                          n_used=np.full(4, 3), excluded_share=np.full(4, 0.25))


def _footer_top(fig, gid: str) -> float:
    renderer_ = plot_style._renderer_of(fig)
    tops = [t.get_window_extent(renderer_).y1 / fig.bbox.height for t in fig.texts if t.get_gid() == gid]
    assert tops, f"premise: the figure wrote a footer ({gid})"
    return max(tops)


def test_position_footer_is_reserved_whole_after_a_phone_redraw():
    fig = _phone_figure_after_distribution()
    renderer.render_position(fig, [_series("A01"), _series("A02")], label="Count", timepoint="t_c", note=_NOTE)
    band = plot_style._figure_text_band(fig)
    top = _footer_top(fig, "nw-position-footer")
    assert band is not None and band[1] >= top, (
        f"the phone pass reserves the figure up to {band and band[1]:.3f} for its footer, but the footer reaches "
        f"{top:.3f}: its upper lines are left for the panels to be laid over (subplotpars.bottom "
        f"{fig.subplotpars.bottom:.3f})")


def test_the_renderer_s_own_margins_reach_the_figure_under_an_engine():
    """The cause, stated directly: the bottom the renderer asked for is the one the figure holds."""
    fig = _phone_figure_after_distribution()
    renderer.render_position(fig, [_series("A01")], label="Count", timepoint="t_c", note=_NOTE)
    assert fig.subplotpars.bottom > 0.10 + renderer._FOOTER_BASE, (
        f"subplotpars.bottom is {fig.subplotpars.bottom:.3f}, the stale value from before the phone's engine")
