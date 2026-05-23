"""Pure-Python unit tests for ``PlotState``.

No Qt, no app — these exercise the plot-options behaviour directly: the
fold-change triple and its mutator, and the SEM / FOV toggles.
"""

from __future__ import annotations

from well_viewer.core.plot_state import PlotState


def test_defaults():
    ps = PlotState()
    assert ps.use_sem is True
    assert ps.use_fov_replicates is False
    assert ps.legend_visible == {"mean": True, "frac": True, "cdf": True}
    assert ps.bar_order is None
    assert ps.fc_state == (False, "", False)


def test_legend_visible_is_per_instance():
    a, b = PlotState(), PlotState()
    a.legend_visible["mean"] = False
    assert b.legend_visible["mean"] is True  # no shared mutable default


# ── fold change ──────────────────────────────────────────────────────────────

def test_fc_state_tuple():
    ps = PlotState(fc_vs_control_on=True, fc_control_label="A01", fc_vs_t0_on=True)
    assert ps.fc_state == (True, "A01", True)


def test_fold_change_active():
    assert PlotState().fold_change_active is False
    assert PlotState(fc_vs_t0_on=True).fold_change_active is True
    assert PlotState(fc_vs_control_on=True).fold_change_active is True


def test_set_fold_change_partial_update():
    ps = PlotState()
    ps.set_fold_change(vs_control_on=True, control_label="CTRL")
    assert ps.fc_vs_control_on is True
    assert ps.fc_control_label == "CTRL"
    assert ps.fc_vs_t0_on is False


def test_set_fold_change_none_leaves_field_unchanged():
    ps = PlotState(fc_control_label="CTRL")
    ps.set_fold_change(vs_t0_on=True)
    assert ps.fc_control_label == "CTRL"  # untouched
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
