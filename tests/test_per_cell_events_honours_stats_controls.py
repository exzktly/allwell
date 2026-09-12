"""Properties → Statistics governs the per-cell Events panel too.

``bar_err_settings`` reads the Show mode and the error-bar type off
``PlotState`` and returns ``(err_kind, ci_scale, draw_err)``. The aggregate bar
path consulted it; ``draw_per_well_n_bars`` — the same Events panel, drawn in
per-cell view modes — did not, and drew an error bar whenever the spread was
positive.

Two consequences, both of which make the control lie about the figure beneath
it: setting the error-bar type to "None" (or the Show mode to bars-only) left
error bars on, and "95 % CI" drew a bare SEM at 1.0× where the aggregate path
draws 1.96×. The panel and its sibling must not disagree about a setting they
share.
"""

from __future__ import annotations

import types

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import pytest  # noqa: E402

from review.features.barplot import renderer  # noqa: E402


@pytest.fixture
def ctx(monkeypatch):
    """Enough ctx for the panel, with one well carrying a per-FOV spread."""
    def _build(*, err_kind="sem", show_mode="mean_spread"):
        c = types.SimpleNamespace(
            state=types.SimpleNamespace(
                plot=types.SimpleNamespace(
                    use_fov_replicates=True, use_sem=True,
                    bar_err_kind=err_kind, bar_show_mode=show_mode,
                ),
                channel=types.SimpleNamespace(
                    active_val_col="w2_mean_intensity", active_metric="mean_intensity",
                ),
            ),
        )
        return c
    return _build


def _draw(ctx, monkeypatch):
    """Draw one well and return the error-bar y-extents matplotlib recorded."""
    # AggPoint: index 0 = timepoint, 4 = total n_above, 7 = per-FOV mean,
    # 8 = its spread. Ten fields is what the real collector emits.
    pt = (0.0, 0.0, 0.0, 0.0, 40.0, 0.0, 0.0, 10.0, 2.0, 0.0)
    monkeypatch.setattr(renderer, "get_all_fluor_gates", lambda _c: {})
    monkeypatch.setattr(
        "review.domain.stats_compute.aggregate_group",
        lambda *a, **k: [pt],
    )
    fig, ax = plt.subplots()
    renderer.draw_per_well_n_bars(
        ctx, ax, ["A01"], ["#888888"], ["A01"], 0.0, 0.0,
        pool_groups={"A01": ["A01"]},
    )
    spans = [
        c.get_segments() for c in ax.collections
        if hasattr(c, "get_segments")
    ]
    plt.close(fig)
    return spans


def test_error_bar_type_none_draws_no_error_bar(ctx, monkeypatch):
    spans = _draw(ctx(err_kind="none"), monkeypatch)
    assert not any(len(s) for s in spans), (
        'the Statistics control reads "None" and the panel drew an error bar'
    )


def test_show_mode_mean_draws_no_error_bar(ctx, monkeypatch):
    spans = _draw(ctx(show_mode="mean"), monkeypatch)
    assert not any(len(s) for s in spans)


def test_ci95_widens_the_bar_to_the_normal_approximation(ctx, monkeypatch):
    sem = _draw(ctx(err_kind="sem"), monkeypatch)
    ci = _draw(ctx(err_kind="ci95"), monkeypatch)

    def _height(spans):
        for seg in spans:
            for s in seg:
                ys = [pt[1] for pt in s]
                if len(ys) >= 2:
                    return max(ys) - min(ys)
        return 0.0

    sem_h, ci_h = _height(sem), _height(ci)
    assert sem_h > 0 and ci_h > 0
    # The spread is 2.0, so SEM spans 4.0 and the 95% CI spans 2*1.96*2.0.
    assert ci_h == pytest.approx(sem_h * 1.96, rel=1e-6), (
        f"95 % CI drew {ci_h}, a bare SEM would be {sem_h}"
    )
