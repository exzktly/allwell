"""The screen's shortcuts must not reach the exported figure.

Two things the Line tab does to stay responsive with a plate-wide selection are
lossy on purpose: the CDF curve is drawn at a few hundred vertices instead of one
per cell, and the legend names the first dozen traces instead of all ninety-six.
Both are right for a panel a few hundred pixels wide and wrong for a figure saved
at 300 dpi, so both are budgets the *caller* sets and the export paths leave
unset.

The assertions here are at the boundary — what was handed to matplotlib — rather
than on the ``LineSeries`` the renderer was given. A test that checks
``series.cdf_vals`` is still the full array passes happily while the drawn line
carries five hundred points and the legend says ``n=512``; that is the shape of
the bug this file exists to prevent, and the reason the first version of this
change put its decimation in the function the batch exporter also calls.

``render_line_series`` IS the export path — its own docstring says so, and
``batch_export/base_panel`` calls it with its own off-screen axes. So "decimate
in the renderer, not the collector" is not a sufficient rule. The budget has to
be a parameter.
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pytest

from well_viewer.features.lineplot.controller import (
    SCREEN_CDF_POINTS,
    SCREEN_LEGEND_ENTRIES,
    LineAxes,
    LinePoint,
    LineSeries,
    render_line_series,
)

CELLS = 21_600
LEGEND_KW = dict(fontsize=7)


@pytest.fixture
def axes():
    fig, axs = plt.subplots(4, 1, figsize=(4, 8), dpi=100)
    yield LineAxes(*axs)
    plt.close(fig)


def _series(n_series: int, cells: int = CELLS) -> list[LineSeries]:
    rng = np.random.default_rng(0)
    out = []
    for i in range(n_series):
        out.append(LineSeries(
            key=f"W{i:03d}", display=f"W{i:03d}", color="#4C72B0", kind="well",
            wells=[f"W{i:03d}"],
            points=[LinePoint(t=0.0, mean=1.0, spread=0.0, frac=0.5,
                              n_above=1, n_total=2)],
            cdf_vals=rng.normal(100.0, 20.0, cells),
        ))
    return out


def _render(axes, series, **kw):
    render_line_series(
        axes, series, fov_active=False, fc_active=False, threshold=0.0,
        legend_kw=LEGEND_KW, warn_color="#c00", muted_fg="#888", **kw)


# ── The CDF curve ────────────────────────────────────────────────────────────

def test_the_export_draws_every_cdf_vertex(axes):
    """No budget passed — the saved figure gets the whole curve."""
    _render(axes, _series(1))
    line = axes.cdf.get_lines()[0]
    assert len(line.get_xdata()) == CELLS, (
        "the export path thinned the CDF; a figure saved at 300 dpi is read at "
        "a resolution where the dropped vertices can matter"
    )


def test_the_screen_draws_a_thinned_cdf(axes):
    _render(axes, _series(1), cdf_point_budget=SCREEN_CDF_POINTS)
    line = axes.cdf.get_lines()[0]
    assert len(line.get_xdata()) == SCREEN_CDF_POINTS


def test_the_thinned_curve_keeps_its_endpoints_and_shape(axes):
    """Decimation must not move the curve, only sample it."""
    series = _series(1)
    full = np.sort(series[0].cdf_vals)
    _render(axes, series, cdf_point_budget=SCREEN_CDF_POINTS)
    line = axes.cdf.get_lines()[0]
    x = np.asarray(line.get_xdata(), dtype=float)
    y = np.asarray(line.get_ydata(), dtype=float)
    assert x[0] == pytest.approx(full[0])
    assert x[-1] == pytest.approx(full[-1])
    assert np.all(np.diff(x) >= 0), "the thinned curve is no longer sorted"
    assert y[-1] == pytest.approx(1.0), (
        "the last point of a CDF is 1.0; computing y from the thinned length "
        "instead of the original ranks would break that"
    )


def test_a_curve_shorter_than_the_budget_is_untouched(axes):
    _render(axes, _series(1, cells=100), cdf_point_budget=SCREEN_CDF_POINTS)
    assert len(axes.cdf.get_lines()[0].get_xdata()) == 100


def test_the_legend_reports_the_wells_cell_count_not_the_drawn_one(axes):
    """The number in the legend is the well's, and thinning must not touch it.

    ``n`` is read off the full array before decimation. Reading it off the drawn
    curve would put ``n=512`` beside a well holding 21,600 cells — a wrong number
    in the one place a reader looks for the right one.
    """
    _render(axes, _series(1), cdf_point_budget=SCREEN_CDF_POINTS)
    labels = [t.get_text() for t in axes.cdf.get_legend().get_texts()]
    assert any(f"n={CELLS:,}" in text for text in labels), (
        f"expected the full cell count in the CDF legend, got {labels!r}"
    )


# ── The legend ───────────────────────────────────────────────────────────────

def test_the_export_names_every_trace(axes):
    _render(axes, _series(96))
    labels = [t.get_text() for t in axes.mean.get_legend().get_texts()]
    assert len(labels) == 96
    assert not any("not listed" in text for text in labels)


def test_the_screen_caps_the_legend(axes):
    _render(axes, _series(96), legend_max=SCREEN_LEGEND_ENTRIES)
    labels = [t.get_text() for t in axes.mean.get_legend().get_texts()]
    assert len(labels) == SCREEN_LEGEND_ENTRIES + 1, "cap plus the overflow row"


def test_the_capped_legend_says_how_many_it_left_out(axes):
    """A legend that silently stops is a degraded artifact posing as a whole one.

    The reader has no other way to tell that the trace they are hunting for is
    drawn but unnamed, so the shortfall is stated in the legend itself.
    """
    _render(axes, _series(96), legend_max=SCREEN_LEGEND_ENTRIES)
    labels = [t.get_text() for t in axes.mean.get_legend().get_texts()]
    assert labels[-1] == f"+{96 - SCREEN_LEGEND_ENTRIES:,} more (not listed)"


def test_a_selection_under_the_cap_gains_no_overflow_row(axes):
    _render(axes, _series(5), legend_max=SCREEN_LEGEND_ENTRIES)
    labels = [t.get_text() for t in axes.mean.get_legend().get_texts()]
    assert len(labels) == 5
    assert not any("not listed" in text for text in labels)


def test_the_capped_legend_keeps_the_first_traces_in_order(axes):
    """Which twelve is not arbitrary — it is the plot's own legend order."""
    _render(axes, _series(96), legend_max=SCREEN_LEGEND_ENTRIES)
    labels = [t.get_text() for t in axes.mean.get_legend().get_texts()]
    assert labels[:SCREEN_LEGEND_ENTRIES] == [
        f"W{i:03d}" for i in range(SCREEN_LEGEND_ENTRIES)
    ]


# ── The tab's own Export menu saves the figure on screen ─────────────────────
#
# The batch exporter renders onto its own axes and never passes the budgets, so
# it was never at risk. The tab's Export menu is the opposite: ``_line_fig`` IS
# ``card.figure``, and Save Figure… / Copy SVG / Save to Saved Plots call
# ``savefig`` on that same object at 300 dpi. Without an explicit repaint, a
# figure headed for a paper carried the screen's sampled CDF and its
# twelve-of-ninety-six legend — degraded output indistinguishable from good
# output, which is the one failure this whole path exists to avoid.

def test_the_capture_context_lifts_the_budgets_and_puts_them_back():
    """The flag is raised for the capture and restored afterwards."""
    from types import SimpleNamespace

    from well_viewer.core import plot_card_actions as pca

    seen: list[bool] = []
    plot = SimpleNamespace(export_full_fidelity=False)
    ctx = SimpleNamespace(state=SimpleNamespace(plot=plot))

    import well_viewer.core.plot_orchestrator as orch
    original = orch.redraw_app
    orch.redraw_app = lambda c: seen.append(c.state.plot.export_full_fidelity)
    try:
        with pca._captured_at_full_fidelity(ctx, "_line_card"):
            assert plot.export_full_fidelity is True, (
                "the figure was captured while still carrying screen budgets"
            )
    finally:
        orch.redraw_app = original

    assert plot.export_full_fidelity is False, "the flag outlived the capture"
    assert seen == [True, False], (
        "expected a repaint at full fidelity before the capture and a repaint "
        f"back to screen budgets after it, got {seen!r}"
    )


def test_the_flag_is_restored_even_when_the_export_raises():
    """A failed save must not leave the tab painting the slow figure forever."""
    from types import SimpleNamespace

    from well_viewer.core import plot_card_actions as pca

    plot = SimpleNamespace(export_full_fidelity=False)
    ctx = SimpleNamespace(state=SimpleNamespace(plot=plot))

    import well_viewer.core.plot_orchestrator as orch
    original = orch.redraw_app
    orch.redraw_app = lambda c: None
    try:
        with pytest.raises(RuntimeError):
            with pca._captured_at_full_fidelity(ctx, "_line_card"):
                raise RuntimeError("save failed")
    finally:
        orch.redraw_app = original
    assert plot.export_full_fidelity is False


def test_a_card_with_no_budgets_is_not_repainted():
    """Bar and scatter cards draw everything already; capturing must not churn."""
    from types import SimpleNamespace

    from well_viewer.core import plot_card_actions as pca

    calls: list[int] = []
    plot = SimpleNamespace(export_full_fidelity=False)
    ctx = SimpleNamespace(state=SimpleNamespace(plot=plot))

    import well_viewer.core.plot_orchestrator as orch
    original = orch.redraw_app
    orch.redraw_app = lambda c: calls.append(1)
    try:
        with pca._captured_at_full_fidelity(ctx, "_bar_card"):
            pass
    finally:
        orch.redraw_app = original
    assert calls == [], "a card with no screen budgets was repainted twice"


def test_the_renderer_honours_the_flag(axes):
    """And the flag reaches the budgets — the half that makes it worth setting."""
    series = _series(96)
    # Full fidelity: what a capture repaint produces.
    _render(axes, series)
    assert len(axes.cdf.get_lines()[0].get_xdata()) == CELLS
    assert len(axes.mean.get_legend().get_texts()) == 96
