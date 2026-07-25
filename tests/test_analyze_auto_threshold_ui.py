"""The Analyze tab's Auto-threshold section (headless / offscreen Qt).

Covers the section's own behaviour — default state, the checkbox gating its two
control-well rows, validation of the pair — plus the three round-trip hops the
values make: into the run-options dict, out to the pipeline argv, and back from
a ``pipeline_info.json`` sidecar.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    from analyze_tab import AnalyzeTab
    return AnalyzeTab()


# ── the section ──────────────────────────────────────────────────────────────

def test_calculation_is_on_by_default(tab):
    """Off would silently change what every existing run produces."""
    assert tab._auto_threshold_cb.isChecked() is True


def test_the_control_well_fields_start_empty(tab):
    """Blank selects the Otsu pass — today's behaviour, unchanged."""
    assert tab._pos_control_well_edit.text() == ""
    assert tab._neg_control_well_edit.text() == ""


def test_unchecking_hides_the_control_rows_and_their_labels(tab):
    """The rows to hide are the label+field parents, not the fields — hiding the
    field alone strands its label (``_row`` returns the right-hand cell)."""
    tab._auto_threshold_cb.setChecked(False)
    assert all(row.isHidden() for row in tab._auto_threshold_rows)
    tab._auto_threshold_cb.setChecked(True)
    assert not any(row.isHidden() for row in tab._auto_threshold_rows)


# ── form → opts ──────────────────────────────────────────────────────────────

def test_the_three_values_reach_the_run_options(tab):
    tab._pos_control_well_edit.setText(" B02 ")
    tab._neg_control_well_edit.setText("B11")
    opts = tab._collect_run_options()
    assert opts["auto_threshold"] is True
    assert opts["pos_control_well"] == "B02"      # stripped
    assert opts["neg_control_well"] == "B11"

    tab._auto_threshold_cb.setChecked(False)
    assert tab._collect_run_options()["auto_threshold"] is False


# ── opts → argv ──────────────────────────────────────────────────────────────

@pytest.fixture
def argv(tab):
    """Build a real pipeline argv from a real form, with overrides applied.

    ``build_pipeline_args`` reads the whole options dict, so a hand-rolled stub
    would only prove the stub was well-formed.
    """
    from services.pipeline_service import build_pipeline_args

    def _build(**over):
        opts = tab._collect_run_options()
        for key, value in over.items():
            if value is _ABSENT:
                opts.pop(key, None)
            else:
                opts[key] = value
        return build_pipeline_args("process_microscopy.py", "/in", "/out", opts)

    return _build


_ABSENT = object()


def test_argv_is_silent_when_calculation_is_on_with_no_controls(argv):
    built = argv()
    assert "--no_auto_threshold" not in built
    assert "--pos_control_well" not in built


def test_argv_suppresses_the_pass_when_unchecked(argv):
    assert "--no_auto_threshold" in argv(auto_threshold=False)


def test_argv_carries_both_control_wells(argv):
    built = argv(pos_control_well="B02", neg_control_well="B11")
    assert built[built.index("--pos_control_well") + 1] == "B02"
    assert built[built.index("--neg_control_well") + 1] == "B11"


@pytest.mark.parametrize("pos,neg", [("B02", ""), ("", "B11")])
def test_argv_omits_a_half_given_pair(argv, pos, neg):
    """One control well carries no separation to optimise."""
    built = argv(pos_control_well=pos, neg_control_well=neg)
    assert "--pos_control_well" not in built
    assert "--neg_control_well" not in built


def test_argv_treats_a_legacy_opts_dict_as_on(argv):
    """An options dict from before this feature has no ``auto_threshold`` key."""
    assert "--no_auto_threshold" not in argv(auto_threshold=_ABSENT)


# ── validation ───────────────────────────────────────────────────────────────

def test_no_error_when_both_are_blank(tab):
    assert tab._auto_threshold_validation_error() is None


def test_no_error_when_the_pass_is_switched_off(tab):
    tab._auto_threshold_cb.setChecked(False)
    tab._pos_control_well_edit.setText("B02")     # half-given, but irrelevant
    assert tab._auto_threshold_validation_error() is None


@pytest.mark.parametrize("pos,neg", [("B02", ""), ("", "B11")])
def test_half_given_is_rejected(tab, pos, neg):
    tab._pos_control_well_edit.setText(pos)
    tab._neg_control_well_edit.setText(neg)
    err = tab._auto_threshold_validation_error()
    assert err is not None and "both control wells" in err


def test_an_invalid_well_name_is_named_directly(tab):
    tab._pos_control_well_edit.setText("ZZ99")
    tab._neg_control_well_edit.setText("B11")
    err = tab._auto_threshold_validation_error()
    assert err is not None and "not a valid 96-well position" in err


def test_the_same_well_twice_is_rejected(tab):
    tab._pos_control_well_edit.setText("b2")
    tab._neg_control_well_edit.setText("B02")     # same well, different spelling
    err = tab._auto_threshold_validation_error()
    assert err is not None and "same well" in err


def test_a_well_missing_from_the_input_is_rejected(tab, tmp_path):
    tab._input_edit.setText(str(tmp_path))
    tab._pos_control_well_edit.setText("B02")
    tab._neg_control_well_edit.setText("B11")
    err = tab._auto_threshold_validation_error()
    assert err is not None and "was not found in the input folder" in err


def test_wells_present_in_the_input_pass(tab, tmp_path):
    (tmp_path / "B02.zip").write_bytes(b"")
    (tmp_path / "B11.zip").write_bytes(b"")
    tab._input_edit.setText(str(tmp_path))
    tab._pos_control_well_edit.setText("B02")
    tab._neg_control_well_edit.setText("B11")
    assert tab._auto_threshold_validation_error() is None


# ── sidecar → form ───────────────────────────────────────────────────────────

def test_a_sidecar_restores_all_three_widgets(tab):
    tab._apply_pipeline_info({
        "schema_version": 2,
        "execution_options": {
            "pos_control_well": "C03",
            "neg_control_well": "C10",
            "no_auto_threshold": False,
        },
    })
    assert tab._pos_control_well_edit.text() == "C03"
    assert tab._neg_control_well_edit.text() == "C10"
    assert tab._auto_threshold_cb.isChecked() is True


def test_the_sidecar_records_the_negative_so_the_checkbox_inverts(tab):
    tab._apply_pipeline_info({
        "schema_version": 2,
        "execution_options": {"no_auto_threshold": True},
    })
    assert tab._auto_threshold_cb.isChecked() is False
    # …and the inverted restore drives the row visibility with it.
    assert all(row.isHidden() for row in tab._auto_threshold_rows)
