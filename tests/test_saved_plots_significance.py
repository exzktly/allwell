"""Saved plots re-run their stats test on restore to redraw significance brackets.

Restoring a saved Bar plot does not persist p-values; instead it stores the test
*config* (groups + test/timepoint/channel/statistic/threshold) and re-runs the
deterministic ``compute_pairwise`` on restore. These tests pin:

- ``compute_pairwise`` is deterministic (same config + data → identical results).
- Saving a Bar plot with significance on captures a ``stats`` config block and
  the ``show_significance`` flag; restoring it repopulates ``stats.pairwise`` and
  the flag (so the Bar redraw draws brackets).
- A saved plot without a ``stats`` block restores cleanly (backward compatible).
"""

from __future__ import annotations

import math

import pytest

from well_viewer.domain.batch_models import BarGroup
from well_viewer.features.stats.controller import compute_pairwise
from well_viewer.features.saved_plots.collect import capture_params, _capture_stats_config
from well_viewer.features.saved_plots.controller import _restore_stats

pytestmark = pytest.mark.qt

_VAL_COL = "gfp_mean_intensity"
_TEST = "t-test (Welch)"


def _two_groups():
    """Two solo-well groups with 2 wells each → per-well means → n=2 samples/group
    on the mini dataset (non-degenerate: distinct, finite p-value)."""
    return [BarGroup("G1", solo_wells=["A01", "A02"]),
            BarGroup("G2", solo_wells=["B01", "B02"])]


def _pairwise(app, groups):
    from well_viewer.features.stats.controller import threshold_for_val_col
    return compute_pairwise(
        app, groups=groups, target_t=0.0, val_col=_VAL_COL, statistic="mean",
        threshold=float(threshold_for_val_col(app, _VAL_COL)), test=_TEST)


def test_compute_pairwise_is_deterministic(loaded_app):
    """Re-running the same config on the same data reproduces the exact result —
    this is what makes storing config (not p-values) sufficient."""
    a = _pairwise(loaded_app, _two_groups())
    b = _pairwise(loaded_app, _two_groups())
    assert a and b and len(a) == len(b) == 1
    assert a[0]["a"] == b[0]["a"] and a[0]["b"] == b[0]["b"]
    assert math.isfinite(a[0]["p"]) and a[0]["p"] == b[0]["p"]
    assert a[0]["p_adj"] == b[0]["p_adj"]


def test_capture_stats_config_records_run_context(loaded_app):
    app = loaded_app
    groups = _two_groups()
    app._app_state.stats.groups = groups
    app._app_state.stats.pairwise = _pairwise(app, groups)

    cfg = _capture_stats_config(app)
    assert cfg is not None
    assert cfg["test"] == _TEST
    assert cfg["val_col"] == _VAL_COL
    assert cfg["target_t"] == 0.0
    assert cfg["statistic"] == "mean"
    assert [g["name"] for g in cfg["groups"]] == ["G1", "G2"]
    assert cfg["groups"][0]["solo_wells"] == ["A01", "A02"]


def test_no_stats_config_without_a_run(loaded_app):
    """No test run (empty ``pairwise``) → nothing to reproduce → no config."""
    loaded_app._app_state.stats.pairwise = []
    assert _capture_stats_config(loaded_app) is None


def test_save_then_restore_repopulates_pairwise_and_flag(loaded_app):
    app = loaded_app
    groups = _two_groups()
    app._app_state.stats.groups = groups
    live = _pairwise(app, groups)
    app._app_state.stats.pairwise = live
    app._app_state.plot.show_significance = True

    params, _thresholds = capture_params(app, "Bar Plots")
    assert params.get("show_significance") is True
    assert isinstance(params.get("stats"), dict)

    # Simulate a fresh reload: transient stats state is gone, flag is off.
    app._app_state.stats.pairwise = []
    app._app_state.stats.groups = []
    app._app_state.plot.show_significance = False

    _restore_stats(app, params["stats"])
    restored = app._app_state.stats.pairwise
    assert restored and len(restored) == len(live) == 1
    assert restored[0]["a"] == live[0]["a"] and restored[0]["b"] == live[0]["b"]
    assert restored[0]["p"] == live[0]["p"]
    assert [g.name for g in app._app_state.stats.groups] == ["G1", "G2"]

    # ``show_significance`` rides back via the view_state snapshot/restore path.
    from well_viewer.persistence import view_state
    view_state.restore(app, dict(params))
    assert app._app_state.plot.show_significance is True


def _set_selections(app, pairs):
    app._app_state.selection.selections = [
        {"name": n, "wells": list(w), "hidden": False} for n, w in pairs]


def test_restore_reproduces_groups_when_replicate_set_renamed(loaded_app):
    """Regression: a saved stats plot must reproduce the same groups even if the
    replicate set was later renamed/deleted. The serialized member carries its
    wells, so the group is rebuilt by composition rather than dropped when the
    name no longer resolves."""
    app = loaded_app
    _set_selections(app, [("CTRL", ["A01", "A02"]), ("DRUG", ["B01", "B02"])])
    rep_by = {r.name: r for r in app._rep_sets_loaded()}
    groups = [BarGroup("CTRL", members=[rep_by["CTRL"]]),
              BarGroup("DRUG", members=[rep_by["DRUG"]])]
    app._app_state.stats.groups = groups
    app._app_state.stats.pairwise = _pairwise(app, groups)
    cfg = _capture_stats_config(app)
    assert cfg["groups"][1]["members"] == [{"name": "DRUG", "wells": ["B01", "B02"]}]

    # User renames DRUG → TREATED (same wells) after saving.
    _set_selections(app, [("CTRL", ["A01", "A02"]), ("TREATED", ["B01", "B02"])])
    app._app_state.stats.pairwise = []
    app._app_state.stats.groups = []
    _restore_stats(app, cfg)

    pw = app._app_state.stats.pairwise
    assert pw and pw[0]["a_wells"] == ["A01", "A02"] and pw[0]["b_wells"] == ["B01", "B02"]
    drug = app._app_state.stats.groups[1]
    assert drug.wells == ["B01", "B02"]  # reconstructed by wells, not dropped


def test_restore_accepts_legacy_name_only_member_format(loaded_app):
    """Plots saved before member wells were stored used ``members: [name]``;
    those still restore by resolving the name against live replicate sets."""
    app = loaded_app
    _set_selections(app, [("CTRL", ["A01", "A02"]), ("DRUG", ["B01", "B02"])])
    from well_viewer.features.stats.controller import threshold_for_val_col
    legacy = {
        "groups": [{"name": "CTRL", "members": ["CTRL"], "solo_wells": []},
                   {"name": "DRUG", "members": ["DRUG"], "solo_wells": []}],
        "test": _TEST, "target_t": 0.0, "val_col": _VAL_COL, "statistic": "mean",
        "threshold": float(threshold_for_val_col(app, _VAL_COL)),
    }
    app._app_state.stats.pairwise = []
    _restore_stats(app, legacy)
    pw = app._app_state.stats.pairwise
    assert pw and pw[0]["a_wells"] == ["A01", "A02"] and pw[0]["b_wells"] == ["B01", "B02"]


def test_restore_without_stats_block_is_noop(loaded_app):
    """Backward compatibility: an older saved plot has no ``stats`` block; restore
    must not raise and must leave ``pairwise`` empty (no brackets)."""
    app = loaded_app
    app._app_state.stats.pairwise = []
    _restore_stats(app, None)          # missing block
    _restore_stats(app, {})            # empty block
    assert app._app_state.stats.pairwise == []
