"""Directed integration test: control-normalised fold-change error propagation.

Both fresh-eyes audits flagged that the real-app tests assert bar *means* under
fold-change but never the *error bars*, even though the trickiest part of the
fold-change path is the spread propagation — dividing every bar by an uncertain
control injects the control's relative error into every bar's error bar.

This test drives the full tab-builder / app-loop path on a real
``WellViewerApp`` + the deterministic mini-dataset (so it uses the ``loaded_app``
fixture and runs in the slow/local lane, not the fast CI gate). It reads the raw
(unscaled) bar values, then the control-normalised ones, and asserts the exact
quadrature relationship the domain layer implements — including that the
control's own bar carries a non-zero error bar that tracks its own uncertainty.
"""

from __future__ import annotations

import math

import pytest

from tests.fixtures.mini_dataset import WELLS
from well_viewer.features.barplot.collect import FC_STATE_OFF, collect_bar_items
from well_viewer.core.channel_state_controller import set_active_channel
from well_viewer.core.fold_change_controls_view import set_fold_change_state

pytestmark = pytest.mark.qt

_CONTROL = "A01"
_TREATED = "B01"
_TP = 24.0


def _by_key(items):
    return {it.key: it for it in items}


def test_vs_control_propagates_the_control_spread_into_every_bar(loaded_app):
    app = loaded_app
    set_active_channel(app._ctx, "gfp")
    app._set_selected_wells(set(WELLS))

    # Raw (unscaled) per-well mean ± spread — the fold-change inputs.
    _, raw_items, _ = collect_bar_items(app._ctx, _TP, fc_state=FC_STATE_OFF)
    raw = _by_key(raw_items)
    assert {_CONTROL, _TREATED} <= set(raw)
    mu_c, sig_c = raw[_CONTROL].mean, raw[_CONTROL].spread
    mu_t, sig_t = raw[_TREATED].mean, raw[_TREATED].spread
    # The mini-dataset's per-well cells differ, so both bars have a real spread.
    assert sig_c > 0 and sig_t > 0

    # Control-normalised fold-change through the real app state.
    set_fold_change_state(app._ctx, vs_control_on=True, control_label=_CONTROL)
    assert app._app_state.plot.fc_vs_control_on is True
    _, fc_items, _ = collect_bar_items(app._ctx, _TP)
    fc = _by_key(fc_items)

    # (1) The control normalises to itself: mean == 1.
    assert math.isclose(fc[_CONTROL].mean, 1.0, rel_tol=1e-9)

    # (2) A treated bar is the ratio of the raw means.
    assert math.isclose(fc[_TREATED].mean, mu_t / mu_c, rel_tol=1e-9)

    # (3) Error bars track the control mean: the propagated spread folds the
    #     control's relative error in quadrature with the bar's own —
    #     spread = |mean| * sqrt((σ_bar/μ_bar)² + (σ_ctrl/μ_ctrl)²).
    rel_c_sq = (sig_c / mu_c) ** 2
    expected_t = abs(mu_t / mu_c) * math.sqrt((sig_t / mu_t) ** 2 + rel_c_sq)
    assert math.isclose(fc[_TREATED].spread, expected_t, rel_tol=1e-9)

    # (4) Even the control's OWN bar carries a non-zero error bar — its relative
    #     uncertainty enters both numerator and denominator, so the normalised
    #     spread is √2 · (σ_ctrl / μ_ctrl), not zero. (A naive "divide by self →
    #     0 ± 0" implementation would fail here.)
    expected_c = math.sqrt(2.0) * (sig_c / mu_c)
    assert fc[_CONTROL].spread > 0
    assert math.isclose(fc[_CONTROL].spread, expected_c, rel_tol=1e-9)
