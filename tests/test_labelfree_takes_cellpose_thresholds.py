"""The label-free Cellpose preset takes the flow and cell-probability thresholds, end to end.

The two fields lived on the general-Cellpose page only, and ``build_pipeline_args``
sent ``--cellpose_flow_threshold`` / ``--cellpose_cellprob_threshold`` only for
``segmentation_method == "cellpose"``. A label-free (brightfield) run therefore
always segmented at flow 0.4 and cell-probability 0, and the user had nowhere to
lower the cut that recovers large, faint cells — dendritic cells beside T cells —
which the model scores just under 0 and so leaves out entirely. The pipeline
itself already applied both thresholds to the label-free preset; only the form
and the argv builder stood in the way.

These tests drive the real Analyze tab — pick the method in its combo, type into
the fields the user types into — and read the argv the run is launched with,
because the defect was the gap between the widget and the argv.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.qt


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.run_footer

    monkeypatch.setattr(analyze.run_footer, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    return t


def _pick(tab, method: str) -> None:
    idx = tab._seg_method_cb.findData(method)
    assert idx >= 0, f"{method!r} is not offered"
    tab._seg_method_cb.setCurrentIndex(idx)


def _argv(opts: dict) -> list[str]:
    from services.pipeline_service import build_pipeline_args

    return build_pipeline_args(Path("/x/pm.py"), Path("/in"), Path("/out"), opts)


def _value(argv: list[str], flag: str) -> str:
    assert flag in argv, f"{flag} was not sent: {argv}"
    return argv[argv.index(flag) + 1]


@pytest.mark.parametrize("method, shown", [
    ("cellpose_labelfree", True),
    ("cellpose", True),
    ("stardist_nuclei", False),
    ("classical_threshold", False),
])
def test_the_threshold_rows_are_offered_for_both_cellpose_methods(tab, method, shown):
    _pick(tab, method)
    flow_row, prob_row = tab._cellpose_threshold_rows
    assert flow_row.isHidden() is (not shown)
    assert prob_row.isHidden() is (not shown)


def test_thresholds_typed_for_label_free_reach_the_pipeline(tab):
    _pick(tab, "cellpose_labelfree")
    tab._cellpose_flow_thr_edit.setText("0.8")
    tab._cellpose_cellprob_thr_edit.setText("-1.5")
    argv = _argv(tab._collect_run_options())
    assert _value(argv, "--cellpose_flow_threshold") == "0.8"
    assert _value(argv, "--cellpose_cellprob_threshold") == "-1.5"


def test_general_cellpose_still_sends_its_thresholds_and_model(tab):
    _pick(tab, "cellpose")
    tab._cellpose_cellprob_thr_edit.setText("-2")
    argv = _argv(tab._collect_run_options())
    assert _value(argv, "--cellpose_cellprob_threshold") == "-2.0"
    assert _value(argv, "--cellpose_flow_threshold") == "0.4"
    assert "--cellpose_model" in argv


def test_the_pipeline_applies_label_free_thresholds(tab, monkeypatch):
    """The argv is parsed the way the pipeline parses it, and the label-free preset leaves the thresholds alone
    — it sets the model and the segmentation channel only."""
    from pipeline.cli_args import apply_labelfree_preset, build_parser

    _pick(tab, "cellpose_labelfree")
    tab._cellpose_cellprob_thr_edit.setText("-1")
    opts = tab._collect_run_options()
    opts["phase_token"] = "BF"
    argv = _argv(opts)
    args = build_parser().parse_args(argv[argv.index("/x/pm.py") + 1:])
    apply_labelfree_preset(args)
    assert args.segmentation_method == "cellpose_labelfree"
    assert args.cellpose_cellprob_threshold == -1.0
    assert args.cellpose_flow_threshold == 0.4


def test_a_nanowell_run_is_sent_no_thresholds_and_not_blocked_by_them(tab):
    """A nanowell count is its own Cellpose call that leaves both thresholds at Cellpose's defaults, so a value
    left in the hidden rows must neither be sent (it would only change the run's settings key) nor block it."""
    from analyze.domain.fields import _numeric_option_errors

    _pick(tab, "cellpose_labelfree")
    tab._cellpose_cellprob_thr_edit.setText("-1")
    opts = tab._collect_run_options()
    assert _value(_argv(opts), "--cellpose_cellprob_threshold") == "-1.0"   # sent outside nanowell mode
    opts.update(nanowell_format="100um", cellpose_flow_threshold="abc")
    argv = _argv(opts)
    assert "--cellpose_flow_threshold" not in argv
    assert "--cellpose_cellprob_threshold" not in argv
    assert not any("Cellpose" in e and "threshold" in e for e in _numeric_option_errors(opts))
