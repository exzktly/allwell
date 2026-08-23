"""Audit U6 / U7 — a typed number reaches the validator, not a default.

``_collect_run_options`` ran the three QC bounds through ``_safe_qc_float`` and
the Cellpose parameters through ``_safe_seg_float`` *before* handing the dict to
``_numeric_option_errors``. Both helpers answer with a default on anything they
cannot parse, so by the time the boundary validator saw the value there was
nothing left to catch: ``5oo`` (letter O — a real typo on that row of the
keyboard) and a deliberately blank field were the same ``0.0``, and
``0.0`` for ``qc_min_area`` means *no lower bound*. Debris removal then removed
nothing, the CSV carried every speck, and ``pipeline_info.json`` recorded
``qc_min_area: 0.0`` — indistinguishable from a user who left it empty. The
module's own docstring claimed a bad QC value "is refused up front instead of
being silently dropped into a default".

The StarDist thresholds are the same defect one layer along: the form passed the
raw text through and validated nothing, so ``0,2`` became
``parse_threshold(...) → None`` — "use the pretrained model's own threshold,
about 0.479" — and the run segmented at a sensitivity nobody chose. ``1.5``
passed the form and raised inside the pipeline, after the run had started.

Every case here types into the real widget and collects through the real
form, because the defect lived exactly in the step between the two.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from analyze_tab import _numeric_option_errors            # noqa: E402


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze_tab_form

    monkeypatch.setattr(analyze_tab_form, "load_remotes", lambda: {})
    from analyze_tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    return t


def _errors(tab) -> list[str]:
    """What ``_validate_run_request`` would refuse the run with."""
    return _numeric_option_errors(tab._collect_run_options())


# ── QC bounds ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field, typed, label", [
    ("_qc_min_area_edit", "5oo", "QC minimum area"),
    ("_qc_max_area_edit", "1,000", "QC maximum area"),
    ("_qc_min_solidity_edit", "0,9", "QC minimum solidity"),
])
def test_a_mistyped_qc_bound_blocks_the_run(tab, field, typed, label):
    tab._qc_remove_debris_cb.setChecked(True)
    getattr(tab, field).setText(typed)
    errs = _errors(tab)
    assert any(label in e for e in errs), (
        f"typing {typed!r} into {label} produced no error: {errs}")


def test_a_mistyped_qc_bound_is_not_collected_as_zero(tab):
    """The value the run would have used, not just the error it now raises.

    ``0.0`` is a legitimate QC setting — it means "no lower bound" — so storing
    it for a value the form could not read is the silent substitution this whole
    audit is about, and it is recorded into ``pipeline_info.json`` as though the
    user had chosen it.
    """
    tab._qc_remove_debris_cb.setChecked(True)
    tab._qc_min_area_edit.setText("5oo")
    assert tab._collect_run_options()["qc_min_area"] == "5oo"


def test_a_solidity_outside_zero_to_one_blocks_the_run(tab):
    tab._qc_remove_debris_cb.setChecked(True)
    tab._qc_min_solidity_edit.setText("5")
    assert any("solidity" in e for e in _errors(tab))


def test_qc_bounds_are_not_checked_while_debris_removal_is_off(tab):
    """The fields are hidden and unread with the box unticked; refusing a run
    over one would be a refusal the user cannot act on or even see."""
    tab._qc_remove_debris_cb.setChecked(False)
    tab._qc_min_area_edit.setText("5oo")
    assert _errors(tab) == []


# ── Cellpose parameters ──────────────────────────────────────────────────────

@pytest.mark.parametrize("field, typed, label", [
    ("_cellpose_diameter_edit", "30 px", "Cellpose diameter"),
    ("_cellpose_flow_thr_edit", "abc", "Cellpose flow threshold"),
    ("_cellpose_cellprob_thr_edit", "abc", "Cellpose cell-probability"),
])
def test_a_mistyped_cellpose_parameter_blocks_the_run(tab, field, typed, label):
    tab._seg_method_cb.setCurrentIndex(
        [tab._seg_method_cb.itemData(i)
         for i in range(tab._seg_method_cb.count())].index("cellpose"))
    getattr(tab, field).setText(typed)
    errs = _errors(tab)
    assert any(label in e for e in errs), (
        f"typing {typed!r} into {label} produced no error: {errs}")


def test_cellpose_parameters_are_not_checked_for_another_routine(tab):
    """A leftover value on a parameter page that is not the selected one has no
    effect on the run, so it must not block it either."""
    tab._cellpose_diameter_edit.setText("30 px")
    assert tab._seg_method() != "cellpose"
    assert _errors(tab) == []


# ── minimum nucleus area ─────────────────────────────────────────────────────

@pytest.mark.parametrize("typed", ["fifty", "0", "-3"])
def test_a_bad_minimum_nucleus_area_blocks_the_run(tab, typed):
    """``build_pipeline_args`` floors this at 1 and falls back to 50 on a parse
    failure, so an unreadable value ran a different segmentation than the one on
    screen."""
    tab._min_nucleus_area_edit.setText(typed)
    assert any("Minimum nucleus area" in e for e in _errors(tab)), typed


# ── StarDist detection thresholds ────────────────────────────────────────────

@pytest.mark.parametrize("typed", ["0,2", "O.2", "point three"])
def test_an_unreadable_stardist_threshold_blocks_the_run(tab, typed):
    tab._stardist_prob_thresh_edit.setText(typed)
    errs = _errors(tab)
    assert any("probability threshold" in e for e in errs), (
        f"typing {typed!r} left the run to segment at the model's own "
        f"threshold instead: {errs}")
    assert any(repr(typed) in e for e in errs), "the error should quote what was typed"


def test_an_out_of_range_stardist_threshold_blocks_the_run_before_it_starts(tab):
    """``1.5`` used to pass the form and raise inside the pipeline mid-run."""
    tab._stardist_nms_thresh_edit.setText("1.5")
    assert any("NMS threshold" in e for e in _errors(tab))


@pytest.mark.parametrize("typed", ["", "auto", "0.2", "-1"])
def test_the_thresholds_that_mean_the_model_default_are_accepted(tab, typed):
    """Blank, ``auto`` and the recorded ``-1`` sentinel all mean "use the
    pretrained model's own threshold" and are not user errors."""
    tab._stardist_prob_thresh_edit.setText(typed)
    assert _errors(tab) == []


def test_a_fresh_form_has_nothing_to_complain_about(tab):
    """Guards the guard: none of the above fires on the defaults."""
    assert _errors(tab) == []
