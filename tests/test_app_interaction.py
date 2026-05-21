"""Interaction tests: a state mutation propagates through the app correctly.

These exercise the single-entry-point mutators the teardown will move behind
typed state objects — ``_set_selected_wells``, ``set_active_channel``,
``set_fold_change_state``. Pinning their observable effects now means the
migration can be verified against real behaviour, not just data shape.
"""

from __future__ import annotations

import math

import pytest

from tests.fixtures.mini_dataset import WELLS
from well_viewer.barplot_controller import collect_bar_items
from well_viewer.channel_state_controller import set_active_channel
from well_viewer.tabs.fold_change_controls import set_fold_change_state

pytestmark = pytest.mark.qt


# ── Selection ────────────────────────────────────────────────────────────────

def test_set_selected_wells_updates_state(loaded_app):
    loaded_app._set_selected_wells({"A01", "B02"})
    assert loaded_app._selected_wells == {"A01", "B02"}


def test_set_selected_wells_tracks_prev_sel(loaded_app):
    # commit=False leaves the _prev_sel snapshot intact — with a commit,
    # _on_plate_sel_change consumes it to compute the selection diff.
    loaded_app._set_selected_wells({"A01"}, commit=False)
    loaded_app._set_selected_wells({"A01", "A02"}, commit=False)
    assert loaded_app._prev_sel == {"A01"}


def test_set_selected_wells_noop_when_unchanged(loaded_app):
    loaded_app._set_selected_wells({"A01"})
    loaded_app._prev_sel = {"sentinel"}
    loaded_app._set_selected_wells({"A01"})  # identical set -> early return
    assert loaded_app._prev_sel == {"sentinel"}


# ── Channel switching ────────────────────────────────────────────────────────

def test_set_active_channel_updates_channel_and_val_col(loaded_app):
    set_active_channel(loaded_app, "mcherry")
    assert loaded_app._active_channel == "mcherry"
    assert loaded_app._active_val_col == "mcherry_mean_intensity"


def test_set_active_channel_round_trip(loaded_app):
    set_active_channel(loaded_app, "mcherry")
    set_active_channel(loaded_app, "gfp")
    assert loaded_app._active_channel == "gfp"
    assert loaded_app._active_val_col == "gfp_mean_intensity"


# ── Fold-change ──────────────────────────────────────────────────────────────

def test_set_fold_change_vs_t0_makes_baseline_one(loaded_app):
    loaded_app._set_selected_wells(set(WELLS))
    set_fold_change_state(loaded_app, vs_t0_on=True)
    assert loaded_app._fc_vs_t0_on is True
    _, items, _ = collect_bar_items(loaded_app, 0.0)
    assert items
    for it in items:
        assert math.isclose(it.mean, 1.0, rel_tol=1e-9)


def test_set_fold_change_vs_control_self_is_one(loaded_app):
    loaded_app._set_selected_wells(set(WELLS))
    set_fold_change_state(loaded_app, vs_control_on=True, control_label="A01")
    assert loaded_app._fc_vs_control_on is True
    assert loaded_app._fc_control_label == "A01"
    _, items, _ = collect_bar_items(loaded_app, 24.0)
    by_key = {it.key: it.mean for it in items}
    assert math.isclose(by_key["A01"], 1.0, rel_tol=1e-9)


def test_set_fold_change_round_trip_off(loaded_app):
    set_fold_change_state(loaded_app, vs_t0_on=True)
    set_fold_change_state(loaded_app, vs_t0_on=False)
    assert loaded_app._fc_vs_t0_on is False
