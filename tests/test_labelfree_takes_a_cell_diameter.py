"""The label-free Cellpose preset takes a cell diameter, end to end.

The diameter field lived on the general-Cellpose page only, and
``build_pipeline_args`` sent ``--cellpose_diameter`` only for
``segmentation_method == "cellpose"``. The label-free preset therefore always
ran with diameter 0 — segmenting every image as if its cells were the 30 px the
cyto3 model was trained on — and a user whose cells are not that size found
nowhere in the app to say so.

The field is now a row shared by both Cellpose methods. These tests drive the
real Analyze tab: pick the method in its combo, type into the field the user
types into, and read the pipeline argv that ``_collect_run_options`` feeds,
because each half (the widget, the option, the argv) was individually fine
before and the defect was a gap between them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.qt


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.form

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
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


@pytest.mark.parametrize("method, shown", [
    ("cellpose_labelfree", True),
    ("cellpose", True),
    ("stardist_nuclei", False),
    ("classical_threshold", False),
])
def test_the_diameter_row_is_offered_for_both_cellpose_methods(tab, method, shown):
    _pick(tab, method)
    # isHidden reflects setVisible intent even while the window is not shown.
    assert tab._cellpose_diameter_row.isHidden() is (not shown)


def test_a_diameter_typed_for_label_free_reaches_the_pipeline(tab):
    _pick(tab, "cellpose_labelfree")
    tab._cellpose_diameter_edit.setText("25")
    opts = tab._collect_run_options()
    assert opts["segmentation_method"] == "cellpose_labelfree"
    argv = _argv(opts)
    assert "--cellpose_diameter" in argv, f"label-free sent no diameter: {argv}"
    assert argv[argv.index("--cellpose_diameter") + 1] == "25.0"
    # Still the preset: model and thresholds stay general Cellpose's alone.
    assert "--cellpose_model" not in argv
    assert "--cellpose_flow_threshold" not in argv


def test_label_free_left_at_zero_sends_no_diameter(tab):
    _pick(tab, "cellpose_labelfree")
    tab._cellpose_diameter_edit.setText("0")
    assert "--cellpose_diameter" not in _argv(tab._collect_run_options())


def test_a_mistyped_label_free_diameter_blocks_the_run(tab):
    """Without this a typo would be read as 0 and quietly fall back to auto."""
    from analyze.domain.fields import _numeric_option_errors

    _pick(tab, "cellpose_labelfree")
    tab._cellpose_diameter_edit.setText("30 px")
    errs = _numeric_option_errors(tab._collect_run_options())
    assert any("Cellpose diameter" in e for e in errs), errs


def test_the_label_free_thresholds_are_still_not_checked(tab):
    """Label-free does not read the flow / cell-probability thresholds, so a
    leftover value there must not block it."""
    from analyze.domain.fields import _numeric_option_errors

    _pick(tab, "cellpose_labelfree")
    tab._cellpose_flow_thr_edit.setText("abc")
    assert _numeric_option_errors(tab._collect_run_options()) == []
