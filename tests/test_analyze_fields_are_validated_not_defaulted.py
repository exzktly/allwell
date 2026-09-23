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

from analyze.tab import _numeric_option_errors            # noqa: E402


@pytest.fixture
def tab(qtbot, monkeypatch):
    import analyze.form

    monkeypatch.setattr(analyze.form, "load_remotes", lambda: {})
    from analyze.tab import AnalyzeTab

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
    screen.

    Asserted through ``_segmentation_validation_errors``, which is the one rule
    for this field and the gated one: the widget lives only on the seeded-
    watershed page and only that routine reads the number, so a copy of the
    check in the ungated numeric scan refused a Cellpose run over a control
    that routine does not show.
    """
    tab._seg_method_cb.setCurrentIndex(
        next(i for i in range(tab._seg_method_cb.count())
             if tab._seg_method_cb.itemData(i) == "stardist_seeded_watershed_cell"))
    tab._min_nucleus_area_edit.setText(typed)
    assert any("Minimum nucleus area" in e
               for e in tab._segmentation_validation_errors()), typed


@pytest.mark.parametrize("method", ["cellpose", "stardist_nuclei",
                                    "classical_threshold"])
def test_a_stale_minimum_nucleus_area_does_not_block_another_routine(tab, method):
    """A field that is not on screen cannot refuse the run it is not part of."""
    tab._min_nucleus_area_edit.setText("0")
    tab._seg_method_cb.setCurrentIndex(
        next(i for i in range(tab._seg_method_cb.count())
             if tab._seg_method_cb.itemData(i) == method))
    assert not any("Minimum nucleus area" in e for e in _errors(tab)), (
        f"a leftover minimum-nucleus-area refused a {method} run, naming a "
        "control that routine does not show")


# ── the QC bounds are compared the way the pipeline reads them ───────────────

def test_the_shipped_default_max_area_is_not_an_upper_bound(tab):
    """Both QC boxes ship holding the text ``0``, and ``0`` means no bound.

    ``build_pipeline_args`` emits ``--qc_max_area`` only when the value is above
    zero, so comparing a typed minimum against an untouched maximum refused the
    form's own default — with a message whose remedy is to blank a box that
    already reads ``0``.
    """
    tab._qc_remove_debris_cb.setChecked(True)
    assert tab._qc_max_area_edit.text().strip() == "0", (
        "the fixture no longer reproduces the shipped default")
    tab._qc_min_area_edit.setText("50")
    assert not any("maximum area" in e for e in _errors(tab)), _errors(tab)


def test_a_real_inverted_pair_is_still_refused(tab):
    tab._qc_remove_debris_cb.setChecked(True)
    tab._qc_min_area_edit.setText("500")
    tab._qc_max_area_edit.setText("100")
    assert any("maximum area" in e for e in _errors(tab))


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


@pytest.mark.parametrize("typed", ["", "auto", "0.2"])
def test_the_thresholds_that_mean_the_model_default_are_accepted(tab, typed):
    """Blank, ``auto`` and the recorded ``-1`` sentinel all mean "use the
    pretrained model's own threshold" and are not user errors."""
    tab._stardist_prob_thresh_edit.setText(typed)
    assert _errors(tab) == []


def test_a_fresh_form_has_nothing_to_complain_about(tab):
    """Guards the guard: none of the above fires on the defaults."""
    assert _errors(tab) == []


# ── nan and inf parse, and then pass every bound ─────────────────────────────
#
# ``float("nan")`` succeeds and compares false against everything, so a check
# written as ``value < low`` / ``value > high`` passes it through a floor, a
# ceiling and both at once. It reached the pipeline as a real setting.

@pytest.mark.parametrize("typed", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_a_non_finite_qc_bound_blocks_the_run(tab, typed):
    tab._qc_remove_debris_cb.setChecked(True)
    tab._qc_min_area_edit.setText(typed)
    errs = _errors(tab)
    assert errs, (
        f"{typed!r} passed the QC minimum-area bound. It compares false "
        "against every check downstream too, so debris removal would silently "
        "keep or drop every cell with nothing saying which."
    )
    assert any("QC minimum area" in e for e in errs), errs


@pytest.mark.parametrize("typed", ["nan", "inf"])
def test_a_non_finite_stardist_threshold_blocks_the_run(tab, typed):
    tab._stardist_prob_thresh_edit.setText(typed)
    errs = _errors(tab)
    assert errs, f"{typed!r} passed as a StarDist probability threshold"


def test_a_negative_threshold_is_refused_rather_than_read_as_the_default(tab):
    """Any negative means "the model's own default" downstream, silently."""
    tab._stardist_prob_thresh_edit.setText("-0.5")
    errs = _errors(tab)
    assert errs, (
        "-0.5 was accepted as a threshold; parse_threshold reads it as 'use "
        "the model's own default', so the run segments at a sensitivity "
        "nobody chose and the form shows -0.5"
    )
    # ...and so is -1. Nothing puts the CLI's sentinel in this box:
    # `_restore_stardist_thresholds` writes "" for any recorded negative, and
    # its own docstring says why — "writing -1.0 into the box would read as a
    # real threshold". A negative here was typed.
    tab._stardist_prob_thresh_edit.setText("-1")
    assert _errors(tab), "a typed -1 was accepted as a threshold"


def test_a_stale_tile_size_does_not_block_a_cellpose_run(tab):
    """A field behind an unselected routine is not read, so it cannot refuse."""
    from analyze.tab import _numeric_option_errors as errs_for

    opts = {"segmentation_method": "cellpose", "stardist_tile_px": "-4"}
    assert errs_for(opts) == [], (
        "a leftover StarDist tile size refused a Cellpose run, naming a "
        "control the chosen routine does not show")
    assert errs_for({**opts, "segmentation_method": "stardist"}), (
        "...and it must still be checked when StarDist is the routine")


# ── "+ Queue" is the same gate as Run ────────────────────────────────────────
#
# The queue button handed the sink whatever the form produced and validated
# nothing, so an unrunnable form became a queue entry indistinguishable from a
# valid one and failed at dequeue time, where there is no dialog to report it.

@pytest.fixture
def refusals(monkeypatch):
    """Record every critical box instead of opening one (they are modal)."""
    from PySide6.QtWidgets import QMessageBox

    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        staticmethod(lambda _p, title, text, *a, **k: seen.append((title, text))))
    return seen


def _queued(tab):
    """Press the real "+ Queue" button; return what reached the sink."""
    jobs: list[dict] = []
    tab.set_queue_sink(jobs.append)
    tab._queue_btn.click()
    return jobs


def test_a_blank_input_queues_for_chaining_and_is_refused_if_never_filled(
        tab, refusals):
    """A queued job may be added blank so "Run after…" can fill its input.

    This refused a blank input outright, which left no way to queue an Analyze
    run after a converter whose output does not exist yet: chaining is set on
    a job that is already in the queue. The refusal moved to where it is
    decidable — the start of the run — and it still names the problem, rather
    than handing the pipeline an input of ``.``.
    """
    tab._input_edit.setText("")
    tab._flatfield_cb.setCurrentText("gaussian")    # the default needs a blank well
    jobs = _queued(tab)
    assert len(jobs) == 1, f"a blank job could not be queued: {refusals!r}"
    assert not refusals
    error = tab.start_run(__import__("services.run_spec", fromlist=["RunSpec"])
                          .RunSpec.from_json(jobs[0]))
    assert error is not None and "no input folder" in error, error


def test_the_run_button_still_refuses_a_blank_input(tab, refusals):
    tab._input_edit.setText("")
    assert tab._validate_run_request() is None
    assert refusals and refusals[0][0] == "Input Error"


def test_queue_refuses_a_mistyped_number(tab, refusals, tmp_path):
    tab._input_edit.setText(str(tmp_path))
    tab._qc_remove_debris_cb.setChecked(True)
    tab._qc_min_area_edit.setText("5oo")
    assert _numeric_option_errors(tab._collect_run_options()), (
        "the fixture no longer produces an invalid form")
    assert _queued(tab) == [], (
        "a job whose QC minimum area does not read as a number was queued; it "
        "would fail at dequeue time with no dialog to report it")
    assert any("5oo" in text for _t, text in refusals), (
        f"the refusal did not name the offending value: {refusals!r}")


def test_queue_accepts_a_form_run_would_accept(tab, refusals, monkeypatch):
    """The two buttons have to agree, or one of them is lying about the form."""
    from pathlib import Path as _P

    monkeypatch.setattr(type(tab), "_validate_run_request",
                        lambda self, **_kw: _P("pm.py"))
    jobs = _queued(tab)
    assert len(jobs) == 1, f"a valid form did not queue: {refusals!r}"
    assert not refusals
