"""Pure-Python unit tests for ``PlotState``.

No Qt, no app — these exercise the plot-options behaviour directly: the
fold-change triple and its mutator, and the SEM / FOV toggles.
"""

from __future__ import annotations

from review.core.plot_state import PlotState
from review.domain.control_ref import ControlRef


def test_defaults():
    ps = PlotState()
    assert ps.use_sem is True
    assert ps.use_fov_replicates is False
    assert ps.legend_visible == {"mean": True, "frac": True, "cdf": True, "n": True}
    assert ps.bar_order is None
    assert ps.fc_vs_control_on is False
    assert ps.fc_control is None
    assert ps.fc_vs_t0_on is False


def test_legend_visible_is_per_instance():
    a, b = PlotState(), PlotState()
    a.legend_visible["mean"] = False
    assert b.legend_visible["mean"] is True  # no shared mutable default


# ── fold change ──────────────────────────────────────────────────────────────

def test_fold_change_fields_round_trip():
    ps = PlotState(fc_vs_control_on=True,
                   fc_control=ControlRef.for_well("A01"), fc_vs_t0_on=True)
    assert ps.fc_vs_control_on is True
    assert ps.fc_control == ControlRef.for_well("A01")
    assert ps.fc_vs_t0_on is True


def test_fold_change_active():
    assert PlotState().fold_change_active is False
    assert PlotState(fc_vs_t0_on=True).fold_change_active is True
    assert PlotState(fc_vs_control_on=True).fold_change_active is True


def test_set_fold_change_partial_update():
    ps = PlotState()
    ps.set_fold_change(vs_control_on=True, control="CTRL")
    assert ps.fc_vs_control_on is True
    # A bare string is read as a legacy reference — resolved by the precedence
    # a pre-ControlRef document was saved under.
    assert ps.fc_control == ControlRef.legacy("CTRL")
    assert ps.fc_vs_t0_on is False


def test_set_fold_change_none_leaves_field_unchanged():
    ps = PlotState(fc_control=ControlRef.legacy("CTRL"))
    ps.set_fold_change(vs_t0_on=True)
    assert ps.fc_control == ControlRef.legacy("CTRL")  # untouched
    assert ps.fc_vs_t0_on is True


# ── toggles ──────────────────────────────────────────────────────────────────

def test_toggle_sem():
    ps = PlotState()
    assert ps.toggle_sem() is False
    assert ps.use_sem is False
    assert ps.toggle_sem() is True


def test_toggle_fov_replicates():
    ps = PlotState()
    assert ps.toggle_fov_replicates() is True
    assert ps.use_fov_replicates is True
