"""The Analyze form's nanowell fluorescence background, and the Image correction a nanowell run does not apply.

A nanowell run measures every fluorescence number above the bare nanowell floor of the well's own tile, or above the
empty reference well's (``--nanowell_fluor_background``); it never reads the flat-field or the background method. The
form used to offer those beside a nanowell run all the same, and a hidden "From blank well(s)" left from a standard
run still asked for a Background well before the run could start. Driven through the real form and the real argv.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from analyze.domain.nanowell_fields import nanowell_option_errors  # noqa: E402
from tests.test_nanowell_form import _argv, _flag, tab  # noqa: E402,F401


def test_the_default_sends_nothing_and_the_empty_well_is_sent_when_chosen(tab):  # noqa: F811
    tab._nanowell_cb.setChecked(True)
    combo = tab._nanowell_fluor_background_cb
    assert combo.currentData() == "floor" and combo.isEnabled()
    assert "--nanowell_fluor_background" not in _argv(tab)          # every earlier run's argv, and its digest
    tab._nanowell_empty_well_edit.setText("H12")
    combo.setCurrentIndex(combo.findData("empty_well"))
    argv = _argv(tab)
    assert _flag(argv, "--nanowell_fluor_background") == "empty_well"
    assert _flag(argv, "--nanowell_empty_well") == "H12"
    tab._nanowell_cb.setChecked(False)
    assert not combo.isEnabled()
    assert "--nanowell_fluor_background" not in _argv(tab)


def test_a_sidecar_restores_the_choice_and_a_run_that_named_none_used_the_floor(tab):  # noqa: F811
    combo = tab._nanowell_fluor_background_cb
    tab._apply_nanowell_settings({"nanowell_format": "100um", "nanowell_empty_well": "H12",
                                  "nanowell_fluor_background": "empty_well"})
    assert combo.currentData() == "empty_well"
    tab._apply_nanowell_settings({"nanowell_format": "100um"})
    assert combo.currentData() == "floor"


def test_the_empty_well_background_without_an_empty_well_is_refused_before_the_run():
    opts = {"nanowell_format": "100um", "nanowell_fluor_background": "empty_well", "nanowell_empty_well": ""}
    assert any("no Empty reference well is named" in e for e in nanowell_option_errors(opts))
    assert not nanowell_option_errors({**opts, "nanowell_empty_well": "H12"})
    assert any("not one this version knows" in e
               for e in nanowell_option_errors({**opts, "nanowell_fluor_background": "rolling_ball"}))


def test_a_nanowell_run_hides_image_correction_and_sends_none_of_it(tab):  # noqa: F811
    section = tab._image_correction_body.parentWidget()
    tab._flatfield_cb.setValue("empty_well")
    tab._background_cb.setValue("robust_mesh")
    tab._background_well_edit.setText("")
    assert not section.isHidden()
    assert _flag(_argv(tab), "--background") == "robust_mesh"
    assert tab._background_well_validation_error() is not None      # a standard run needs its blank well
    tab._nanowell_cb.setChecked(True)
    assert section.isHidden()
    argv = _argv(tab)
    assert _flag(argv, "--background") == "none" and "--flatfield" not in argv and "--background_well" not in argv
    assert tab._background_well_validation_error() is None          # and a nanowell run is not asked for one
    tab._nanowell_cb.setChecked(False)
    assert not section.isHidden() and _flag(_argv(tab), "--background") == "robust_mesh"
