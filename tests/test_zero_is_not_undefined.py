"""Zero is a measurement; undefined is not.

The repository's own design rule is that degraded output must never be
indistinguishable from good output, and the sharpest form of that is the
confusion between "we measured no events" and "we could not compute this".
These tests pin the places the review found it, in both directions.

* The per-well Events panel refused to draw a bar for a well measured to have
  no events above threshold, painting instead the dashed placeholder that means
  "could not be computed" — so a real result read as a failure.
  ``render_bar_items``, which draws the same panel from the other path, already
  decided this correctly.
* ``radial_profile`` gave a one-cell bin ``sem = 0.0``, while an empty bin
  correctly got NaN. A single value has no spread; that is not the same fact as
  several values agreeing exactly, and the misleading case is the one drawn as
  data.
* ``ripley_l`` returned an all-zero ``L(r) - r`` curve inside a zero-width CSR
  envelope for fewer than two cells — the strongest claim the function can
  make, "perfectly random with no uncertainty at all", produced from one cell.
"""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from well_viewer.domain.spatial_compute import radial_profile, ripley_l  # noqa: E402


# ── spatial statistics ──────────────────────────────────────────────────────

def test_a_one_cell_radial_bin_has_an_undefined_spread():
    # Two bins over r/R in [0, 1]: the first holds one cell, the second three.
    r_over_R = np.array([0.10, 0.70, 0.80, 0.90])
    metric = np.array([1.0, 5.0, 6.0, 7.0])
    prof = radial_profile(r_over_R, metric, n_bins=2)

    lone = [i for i, n in enumerate(prof.n_per_bin) if n == 1]
    assert lone, f"expected a single-cell bin, got n_per_bin={prof.n_per_bin}"
    for i in lone:
        assert np.isnan(prof.sem[i]), (
            "a one-cell bin reported a spread of "
            f"{prof.sem[i]} — indistinguishable from cells that agreed exactly"
        )


def test_a_populated_radial_bin_still_gets_a_real_spread():
    r_over_R = np.array([0.1, 0.2, 0.3, 0.4])
    metric = np.array([1.0, 3.0, 5.0, 7.0])
    prof = radial_profile(r_over_R, metric, n_bins=1)
    assert prof.n_per_bin[0] == 4
    assert np.isfinite(prof.sem[0]) and prof.sem[0] > 0


@pytest.mark.parametrize("n", [0, 1])
def test_ripley_says_undefined_rather_than_perfect_randomness(n):
    pts = np.zeros((n, 2), dtype=float)
    res = ripley_l(pts)
    assert res.n_points == n
    assert np.isnan(res.L_minus_r).all(), (
        "a frame with too few cells reported a measured L(r) - r curve"
    )
    assert np.isnan(res.csr_lo).all() and np.isnan(res.csr_hi).all(), (
        "a zero-width CSR envelope claims perfect certainty about a frame "
        "with nothing in it"
    )


def test_ripley_still_computes_for_a_real_frame():
    rng = np.random.default_rng(4)
    pts = rng.uniform(0, 100, size=(200, 2))
    res = ripley_l(pts, n_sims=5)
    assert np.isfinite(res.L_minus_r).any()
    assert np.isfinite(res.csr_lo).any() and np.isfinite(res.csr_hi).any()


# ── the per-well Events panel ───────────────────────────────────────────────

def _n_panel_bars(monkeypatch, pts_by_well):
    """Render ``draw_per_well_n_bars``; report "data" or "placeholder" per bar."""
    import types

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_rgb

    import theme
    from well_viewer.features.barplot import renderer

    ctx = types.SimpleNamespace(state=types.SimpleNamespace(
        channel=types.SimpleNamespace(active_val_col="Nuclear_Mean",
                                      active_metric="mean_intensity",
                                      active_channel="gfp"),
        plot=types.SimpleNamespace(use_fov_replicates=False, use_sem=False,
                                   time_scale=1.0, show_threshold_line=False),
    ))
    wells = list(pts_by_well)
    monkeypatch.setattr(renderer, "aggregate_well",
                        lambda _c, lbl, **_kw: pts_by_well[lbl])
    monkeypatch.setattr(renderer, "get_all_fluor_gates", lambda _c: {})

    fig, ax_n = plt.subplots()
    try:
        renderer.draw_per_well_n_bars(
            ctx, ax_n, wells=wells, colors=["#4488cc"] * len(wells),
            xlabels=wells, target_t=6.0, threshold=0.0,
        )
        ph = tuple(round(v, 3) for v in to_rgb(theme.Colors.placeholder))
        out = []
        for patch in ax_n.patches:
            rgb = tuple(round(v, 3) for v in patch.get_facecolor()[:3])
            out.append("placeholder" if rgb == ph else "data")
        return out
    finally:
        plt.close(fig)


def _agg_point(t: float, n_above: float):
    """An AggPoint-shaped tuple; index 0 is the timepoint and 4 is n_above."""
    return (t, 0.0, 0.0, 0.0, n_above, 0.0, 0.0, 0.0, 0.0, 0.0)


def test_a_well_measured_to_have_no_events_draws_a_real_bar(monkeypatch):
    bars = _n_panel_bars(monkeypatch, {"A01": [_agg_point(6.0, 0.0)]})
    assert bars == ["data"], (
        "a measured zero drew the dashed placeholder that means "
        "'could not be computed'"
    )


def test_a_well_with_no_point_at_this_timepoint_still_draws_the_placeholder(monkeypatch):
    bars = _n_panel_bars(monkeypatch, {"A01": [_agg_point(24.0, 5.0)]})
    assert bars == ["placeholder"]


def test_a_well_with_events_draws_a_real_bar(monkeypatch):
    bars = _n_panel_bars(monkeypatch, {"A01": [_agg_point(6.0, 12.0)]})
    assert bars == ["data"]


def test_a_nan_count_is_undefined_not_zero(monkeypatch):
    bars = _n_panel_bars(monkeypatch, {"A01": [_agg_point(6.0, float("nan"))]})
    assert bars == ["placeholder"]
