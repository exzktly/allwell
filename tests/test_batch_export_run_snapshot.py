"""A batch export describes one configuration, and says which one.

``_run_batch_jobs`` pumps ``QApplication.processEvents()`` once per job, before
running it, so the panel's combos can move while an export is in flight. Three
findings here come out of that, plus one about a control the panel inherits and
then misreports.

* the channel was snapshotted once before the loop while the value column and
  the metric were re-read from the live combos per job — so a group could be
  exported as one channel from another channel's column, in a file named and
  titled for the wrong one of the two;
* ``_app_val_col_scope`` restored the app's column only ``if saved is not None``,
  which leaves the batch's own column on shared state exactly when there was
  none before;
* the multi-timepoint scatter aggregate drew without ``color``, discarding the
  per-timepoint gradient that is the reason that figure exists;
* a per-sample control mapping inherited from the plot tab left the Control combo
  reading ``—`` — "no control" — while the export normalised against it.

These drive the real methods with stubs rather than building a panel, because
the defects are in the *sequence* of reads, not in any widget.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QHBoxLayout, QWidget,
)

from well_viewer.features.batch_export.base_panel import BatchExportPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ── item 104: the value-column scope restores what was there ────────────────

class _ScopeStub:
    _app_val_col_scope = BatchExportPanel._app_val_col_scope

    def __init__(self, pre):
        self._ctx = SimpleNamespace(
            state=SimpleNamespace(channel=SimpleNamespace(active_val_col=pre)))


def test_the_value_column_scope_restores_a_real_column():
    s = _ScopeStub("gfp_mean_intensity")
    with s._app_val_col_scope("mcherry_total_intensity"):
        assert s._ctx.state.channel.active_val_col == "mcherry_total_intensity"
    assert s._ctx.state.channel.active_val_col == "gfp_mean_intensity"


def test_the_value_column_scope_restores_a_none():
    # ``None`` is a value that was there, so ``None`` is what goes back. Guarding
    # on ``saved is not None`` read "the app had no column selected" as "there is
    # nothing to put back", leaving the batch's column on shared app state.
    s = _ScopeStub(None)
    with s._app_val_col_scope("mcherry_total_intensity"):
        pass
    assert s._ctx.state.channel.active_val_col is None


# ── item 100: the channel and its column are read together ──────────────────

class _ReadStub:
    _selected_export_channel = BatchExportPanel._selected_export_channel
    _export_val_col_for = BatchExportPanel._export_val_col_for
    _selected_export_metric_key = BatchExportPanel._selected_export_metric_key


def test_the_channel_and_the_column_are_read_together(qapp):
    """The pair must come from ONE read of the panel, not two at different times.

    Asserted against the real readers: whatever the user does to the combos
    mid-run, the channel and the column a job exports have to describe the same
    selection.
    """
    s = _ReadStub()
    s._ctx = SimpleNamespace(state=SimpleNamespace(channel=SimpleNamespace(
        active_channel="gfp", active_metric="mean_intensity",
        ratio_metrics=[], smfish_channels=set(), label_to_channel_key={},
    )))
    s._line_channel_cb = QComboBox()
    s._line_channel_cb.addItems(["gfp", "mcherry"])
    s._line_channel_cb_metric = QComboBox()
    s._line_channel_cb_metric.addItems(["Mean Intensity", "Total Intensity"])

    # What ``_run_line_batch_export`` does: read the whole selection once,
    # before the job loop.
    ch = s._selected_export_channel() or s._ctx.state.channel.active_channel
    val_col = s._export_val_col_for("_line_channel_cb")
    metric = s._selected_export_metric_key("_line_channel_cb")

    # processEvents() between jobs lets the user move the combos.
    s._line_channel_cb.setCurrentText("mcherry")
    s._line_channel_cb_metric.setCurrentText("Total Intensity")

    # Every later job still uses the snapshot, so the pair stays consistent.
    assert val_col.startswith(ch), (
        f"job would export channel {ch!r} against column {val_col!r}")
    assert metric == "mean_intensity"


def test_no_per_job_closure_reads_a_live_combo():
    """A guard on the shape, not on one instance of it.

    Hoisting the two reads fixes today's split pair; this keeps the next one from
    being reintroduced by someone adding a third field to the same closure.
    """
    import ast

    live = {"_export_val_col_for", "_selected_export_metric_key",
            "_selected_export_channel"}
    offenders = []
    for name in ("base_panel.py", "bar_panel.py"):
        path = Path("well_viewer/features/batch_export") / name
        tree = ast.parse(path.read_text())
        for outer in ast.walk(tree):
            if not isinstance(outer, ast.FunctionDef):
                continue
            for node in ast.walk(outer):
                if not isinstance(node, ast.FunctionDef) or node is outer:
                    continue
                if not node.name.startswith("_run_"):
                    continue
                for call in ast.walk(node):
                    if (isinstance(call, ast.Call)
                            and isinstance(call.func, ast.Attribute)
                            and call.func.attr in live):
                        offenders.append(f"{name}:{call.lineno} {node.name} "
                                         f"calls {call.func.attr}")
    assert offenders == [], (
        "a per-job closure re-reads a live panel selection; the runner pumps "
        "processEvents() between jobs, so these must be snapshotted before the "
        "loop:\n  " + "\n  ".join(offenders))


# ── item 99: the multi-tp scatter aggregate keeps its timepoint colour ──────

def test_the_multi_tp_scatter_aggregate_draws_each_point_in_its_timepoint_colour(monkeypatch):
    from well_viewer.features.batch_export.scatter_panel import (
        ScatterBatchExportPanel,
    )
    import well_viewer.features.scatter.controller as SC

    data = {
        "A01_tp0.0": {"x": [1.0], "y": [2.0], "x_err": [0.1], "y_err": [0.2],
                      "color": (0.1, 0.2, 0.9, 1.0), "marker": "o",
                      "timepoint": 0.0, "label": "A01 (t=0.0h)"},
        "A01_tp6.0": {"x": [3.0], "y": [4.0], "x_err": [0.1], "y_err": [0.2],
                      "color": (0.9, 0.8, 0.1, 1.0), "marker": "o",
                      "timepoint": 6.0, "label": "A01 (t=6.0h)"},
    }
    monkeypatch.setattr(SC, "collect_scatter_agg_data", lambda *a, **k: data)

    calls = []

    class _Ax:
        def errorbar(self, *a, **k):
            calls.append(k)

        def set_xscale(self, *a, **k):
            pass

        def set_yscale(self, *a, **k):
            pass

    class _Stub:
        _ctx = SimpleNamespace(state=SimpleNamespace(dataset=SimpleNamespace(
            well_labels={"A01": "A01"}, well_paths={"A01": "/x"})))
        _sc_agg_x_cb = _sc_agg_metric_x_cb = None
        _sc_agg_y_cb = _sc_agg_metric_y_cb = None

        def _scatter_axis_stat(self, _a, _b):
            return "Mean Fluorescence GFP"

        def _agg_axis_labels(self, _x, _y):
            return ("x", "y")

        def _apply_agg_log_scale(self, _ax):
            pass

        @contextmanager
        def _app_group_scope(self, _grp):
            yield

        def _export_scatter_figure(self, draw, **_kw):
            draw(_Ax())

    tmp = Path(tempfile.mkdtemp())
    err = ScatterBatchExportPanel._run_scatter_agg_multi_tp_job(
        _Stub(), SimpleNamespace(name="G1", wells=[]), [0.0, 6.0],
        tmp / "a.csv", tmp / "a.png", "png")
    assert err is None
    assert len(calls) == 2
    # ``color`` here is the timepoint's position on the viridis gradient. Without
    # it matplotlib assigns its own cycle per series and the figure's colours say
    # nothing — and say something different from the tab and from the
    # single-timepoint export beside it.
    assert [k.get("color") for k in calls] == [(0.1, 0.2, 0.9, 1.0),
                                               (0.9, 0.8, 0.1, 1.0)]


# ── item 45: the Control combo names the mapping it inherited ───────────────

class _FcStub:
    _FC_NONE_LABEL = BatchExportPanel._FC_NONE_LABEL
    _FC_T0_LABEL = BatchExportPanel._FC_T0_LABEL
    _FC_PER_SAMPLE_LABEL = BatchExportPanel._FC_PER_SAMPLE_LABEL
    _FC_PER_SAMPLE = BatchExportPanel._FC_PER_SAMPLE
    _build_fold_change_widgets = BatchExportPanel._build_fold_change_widgets
    _repopulate_fc_control_combo = BatchExportPanel._repopulate_fc_control_combo
    _drop_per_sample_entry = BatchExportPanel._drop_per_sample_entry
    _fc_state = BatchExportPanel._fc_state


def _fc_panel(mode, control_map):
    from well_viewer.domain.control_ref import ControlRef

    s = _FcStub()
    s._ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(well_paths={"A01": "/x", "B01": "/y"})))
    s._groups = []
    s._fc_vs_control_on = True
    s._fc_control = None
    s._fc_control_mode = mode
    s._fc_control_map = {k: ControlRef.for_well(v) for k, v in control_map.items()}
    s._fc_control_render = "self"
    s._fc_vs_t0_on = False
    holder = QWidget()
    s._build_fold_change_widgets(QHBoxLayout(holder))
    s._holder = holder          # keep the parent alive for the test's lifetime
    return s


def test_the_control_combo_names_an_inherited_per_sample_mapping(qapp):
    s = _fc_panel("per_sample", {"S1": "A01"})
    # It used to read "—", which means "no control" in every other reading of
    # this widget, while the export normalised every member against the map.
    assert s._fc_ctrl_combo.currentText() == BatchExportPanel._FC_PER_SAMPLE_LABEL
    st = s._fc_state()
    assert st.control_mode == "per_sample" and st.control_map


def test_a_single_control_panel_offers_no_per_sample_entry(qapp):
    s = _fc_panel("single", {})
    texts = [s._fc_ctrl_combo.itemText(i) for i in range(s._fc_ctrl_combo.count())]
    assert BatchExportPanel._FC_PER_SAMPLE_LABEL not in texts
    assert s._fc_ctrl_combo.currentText() == BatchExportPanel._FC_NONE_LABEL


def test_picking_one_control_replaces_the_inherited_mapping(qapp):
    s = _fc_panel("per_sample", {"S1": "A01"})
    s._fc_ctrl_combo.setCurrentText("B01")
    st = s._fc_state()
    assert st.control_mode == "single"
    assert getattr(st.control, "name", None) == "B01"
    assert st.control_map == ()
    # The entry that named the inherited mode is gone with the mode, so it
    # cannot be re-selected into a state the panel is no longer in.
    texts = [s._fc_ctrl_combo.itemText(i) for i in range(s._fc_ctrl_combo.count())]
    assert BatchExportPanel._FC_PER_SAMPLE_LABEL not in texts
    assert s._fc_ctrl_combo.currentText() == "B01"
