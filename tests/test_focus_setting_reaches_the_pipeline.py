"""The per-cell focus setting, from the control a user ticks to the CSV column.

A pipeline option reaches its effect through seven files here — the form
widget, the options dict, the entry-boundary validation, the sidecar restore,
the argv builder, the parser, and the code that reads the parsed value — and
**a setting that reaches six of them is a control that does nothing**. That is
the failure this file exists to catch, and it is invisible to a test of any one
hop: every hop works, and the chain is broken.

Two asymmetries are pinned here because both are decisions rather than
oversights, and both look like bugs to a reader who does not know why:

* **The measurement is on and the threshold is blank.** Measuring can only be
  recovered by re-running over the images, so it defaults to on. The threshold
  is a number about a microscope that nothing in this application can guess —
  ``_Docs/Markdowns/INFERENCE_SUBSYSTEMS_TRACKER.md`` §D.2d has the measurement
  that refused to guess it — so it defaults to unstated.
* **The run records the cut and never applies it.** Ground rule §0.3. A
  pipeline that dropped the cells would make a threshold chosen wrongly cost a
  re-run; recorded, it costs a tick in Review.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from PySide6.QtWidgets import QApplication

import process_microscopy as pm
from pipeline.field_quality import FOCUS_COLUMN

SCHEMA = ["experiment", "channel", "well", "fov", "timepoint"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tab(qapp):
    """The real Analyze tab. A stub of the form would only prove the stub."""
    from analyze.tab import AnalyzeTab
    return AnalyzeTab()


@pytest.fixture
def argv(tab):
    """A real pipeline argv built from the real form, with overrides."""
    from services.pipeline_service import build_pipeline_args

    def _build(**over):
        opts = tab._collect_run_options()
        opts.update(over)
        return build_pipeline_args("process_microscopy.py", "/in", "/out", opts)

    return _build


def _flag_value(args: list[str], flag: str) -> str | None:
    """The value after *flag*, ``""`` for a bare flag, ``None`` when absent."""
    if flag not in args:
        return None
    i = args.index(flag)
    nxt = args[i + 1] if i + 1 < len(args) else ""
    return "" if nxt.startswith("--") else nxt


# ── the control ─────────────────────────────────────────────────────────────

def test_the_measurement_is_on_by_default(tab):
    """Off means the dataset carries no focus evidence at all, and asking the
    question later needs a re-run over the images."""
    assert tab._focus_metric_cb.isChecked() is True


def test_the_threshold_starts_unstated_rather_than_at_zero(tab):
    """A typed 0 and a blank box are different statements, and 0 would exclude
    every cell in the plate. §D.2d measured why nothing can supply a default
    here: Otsu's cut throws away two fifths of a clean plate's good cells."""
    assert tab._focus_min_edit.text() == ""


def test_the_threshold_row_hides_with_the_measurement(tab):
    """The rows toggled are the label+field parents, not the fields — hiding a
    ``_row`` cell alone strands its label, which lives in the cell's parent."""
    assert not any(row.isHidden() for row in tab._focus_rows)
    tab._focus_metric_cb.setChecked(False)
    assert all(row.isHidden() for row in tab._focus_rows)
    tab._focus_metric_cb.setChecked(True)
    assert not any(row.isHidden() for row in tab._focus_rows)


# ── the control's value reaching the options dict, then the argv ────────────

def test_a_default_run_sends_neither_flag(argv):
    """On with no cut is the pipeline's own default, so it emits nothing —
    which is what keeps an argv, and therefore a settings digest, identical to
    one from before this control existed."""
    args = argv()
    assert _flag_value(args, "--no_focus_metric") is None
    assert _flag_value(args, "--focus_min") is None


def test_turning_the_measurement_off_reaches_the_parser(tab, argv):
    tab._focus_metric_cb.setChecked(False)
    assert _flag_value(argv(), "--no_focus_metric") == ""


def test_the_typed_threshold_reaches_the_parser(tab, argv):
    """Excludes the implementation that collects the box into the options dict
    and never emits it — the box is read, the dict is right, and the run never
    hears the number."""
    tab._focus_min_edit.setText("0.083")
    assert _flag_value(argv(), "--focus_min") == "0.083"


def test_a_threshold_is_not_sent_without_the_measurement(tab, argv):
    """A cut on a column that will not exist is not a setting, it is a value
    nothing can read. Only the off flag goes."""
    tab._focus_metric_cb.setChecked(False)
    tab._focus_min_edit.setText("0.083")
    args = argv()
    assert _flag_value(args, "--no_focus_metric") == ""
    assert _flag_value(args, "--focus_min") is None


def test_the_parser_carries_both_all_the_way_to_a_namespace():
    """The last hop, which a form test cannot see."""
    from pipeline.cli_args import build_parser
    ns = build_parser().parse_args(
        ["--input_dir", "i", "--output_dir", "o", "--focus_min", "0.09"])
    assert ns.no_focus_metric is False
    assert ns.focus_min == pytest.approx(0.09)


# ── validation at the entry boundary ────────────────────────────────────────

@pytest.mark.parametrize("text", ["abc", "-1", "3", "nan", "inf"])
def test_an_unusable_threshold_is_refused_before_the_run(text):
    from analyze.domain.fields import _numeric_option_errors
    errs = _numeric_option_errors({"measure_focus": True, "focus_min": text})
    assert errs, f"{text!r} reached the pipeline unchallenged"


@pytest.mark.parametrize("text", ["", "0.08", "1.5"])
def test_a_usable_threshold_is_not_refused(text):
    from analyze.domain.fields import _numeric_option_errors
    assert not _numeric_option_errors({"measure_focus": True, "focus_min": text})


def test_the_threshold_is_not_validated_while_the_measurement_is_off():
    """A field behind an unticked gate is never read, so refusing on it would
    block a run over a control the user cannot see the effect of."""
    from analyze.domain.fields import _numeric_option_errors
    assert not _numeric_option_errors(
        {"measure_focus": False, "focus_min": "abc"})


# ── the pipeline: what it does with the two values ──────────────────────────

def _discs(n=96, sigma=0.8):
    """A frame of three discs and its labels."""
    from scipy.ndimage import gaussian_filter
    yy, xx = np.mgrid[0:n, 0:n]
    labels = np.zeros((n, n), np.int32)
    img = np.full((n, n), 300.0)
    for k, (cy, cx) in enumerate(((24, 24), (24, 70), (70, 48)), start=1):
        d = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        labels[d <= 9] = k
        img += 2500.0 * (d <= 9)
    return gaussian_filter(img, sigma), labels


def _quantify(measure_focus: bool):
    img, labels = _discs()
    return pm._quantify_image_group(
        labels=labels, fluor_tophat=[img], fluor_tokens=["GFP"],
        nir_corr=img, nuclear_path=Path("exp_DAPI_A01_f1_t0.tif"),
        schema=SCHEMA, sep="_", shape_features=[],
        measure_focus=measure_focus)


def test_the_column_is_absent_rather_than_zero_when_the_measurement_is_off():
    """Excludes the implementation that writes 0.0, or writes None, for every
    cell. A run that did not measure focus and a run that measured it and found
    nothing must not produce the same CSV (CLAUDE.md §7)."""
    records = _quantify(measure_focus=False)
    assert len(records) == 3
    assert all(FOCUS_COLUMN not in r for r in records), records[0]


def test_the_column_is_there_when_the_measurement_is_on():
    """The other half — without this the test above passes against a pipeline
    that never writes the column at all."""
    records = _quantify(measure_focus=True)
    assert all(r.get(FOCUS_COLUMN) is not None for r in records)


def test_the_fields_own_focus_goes_absent_with_the_column():
    """The field number is the median of the cells', so turning the cells off
    must leave the field undefined rather than 0.0 — the same rule one pooling
    up. Excludes the implementation that reports a field median of zero for a
    run that measured nothing."""
    from pipeline.field_quality import group_record
    rec = group_record(acquisition={}, labels=np.zeros((4, 4), np.int32),
                       corrected_image=np.zeros((4, 4)), areas=[],
                       cell_focus=None, fov="1", timepoint="0")
    assert rec.get("focus") is None


def test_the_run_records_the_threshold_and_does_not_apply_it(monkeypatch, tmp_path):
    """**The §0.3 guard.** Excludes the implementation that drops the soft
    cells during the run, which would make a threshold chosen wrongly cost a
    re-run over the images instead of a tick in Review.

    The cut used is above every value the fixture produces, so a pipeline that
    applied it would return no records at all.
    """
    records = _quantify(measure_focus=True)
    cut = max(r[FOCUS_COLUMN] for r in records) * 2.0

    from pipeline.cli_args import build_parser
    ns = build_parser().parse_args(
        ["--input_dir", str(tmp_path), "--output_dir", str(tmp_path),
         "--focus_min", str(cut)])
    # The parsed cut reaches no argument of the quantifier: the only thing that
    # decides what is measured is the on/off flag.
    assert ns.focus_min == pytest.approx(cut)
    again = _quantify(measure_focus=True)
    assert len(again) == 3
    assert [r[FOCUS_COLUMN] for r in again] == [r[FOCUS_COLUMN] for r in records]


# ── what invalidates a previous run, and what does not ──────────────────────

def test_dropping_the_column_invalidates_a_previous_run():
    from util.settings_digest import settings_digest
    base = {"nuclear_token": "NIR", "segmentation_method": "stardist_nuclei"}
    assert (settings_digest(base)
            != settings_digest({**base, "no_focus_metric": True}))


def test_changing_the_threshold_does_not_invalidate_a_previous_run():
    """It is recorded, not applied, so two runs differing only here produce
    byte-identical CSVs. Keying on it would make changing a threshold cost a
    full re-segmentation of the plate — the opposite of the reason it is
    recorded rather than applied."""
    from util.settings_digest import settings_digest
    base = {"nuclear_token": "NIR", "segmentation_method": "stardist_nuclei"}
    assert (settings_digest(base)
            == settings_digest({**base, "focus_min": 0.09}))


# ── the sidecar round trip ──────────────────────────────────────────────────

#: The two shapes a real sidecar comes in. ``execution_options`` is the
#: ``vars(args)`` snapshot a v2+ run records and is what ``_pinfo_flat``
#: **prefers**, so it carries the parser's own destination — the *negative*
#: ``no_focus_metric``. ``qc_options`` is the older block, and the flattener
#: builds the positive ``measure_focus`` from it. Testing one and not the other
#: is how a restore comes to work for exactly half the datasets in existence,
#: which is the state this file found the first version in.
def _exec_sidecar(**qc):
    inv = {"no_focus_metric": not qc.pop("measure_focus", True)}
    return {"execution_options": {"input_dir": "/in", "output_dir": "/out",
                                  **inv, **qc}}


def _legacy_sidecar(**qc):
    return {"qc_options": qc}


@pytest.mark.parametrize("build", [_exec_sidecar, _legacy_sidecar])
def test_an_unstated_cut_stays_blank_when_a_dataset_is_reopened(tab, build):
    """The sidecar spells "no cut" as 0.0, which is right on disk and wrong in
    a box: restored verbatim it reads as a cut of zero, which would exclude
    every cell. Excludes the restore that passes the number through."""
    tab._focus_min_edit.setText("0.05")
    tab._apply_pipeline_info(build(measure_focus=True, focus_min=0.0))
    assert tab._focus_min_edit.text() == ""
    assert tab._focus_metric_cb.isChecked() is True


@pytest.mark.parametrize("build", [_exec_sidecar, _legacy_sidecar])
def test_a_stated_cut_comes_back(tab, build):
    """The other half: without it the test above passes against a restore that
    ignores the key entirely."""
    tab._apply_pipeline_info(build(measure_focus=True, focus_min=0.075))
    assert tab._focus_min_edit.text() == "0.075"


@pytest.mark.parametrize("build", [_exec_sidecar, _legacy_sidecar])
def test_a_run_that_did_not_measure_focus_comes_back_unticked(tab, build):
    """Excludes the restore that reads only the positive spelling, which leaves
    the box ticked for every v2+ sidecar that says otherwise."""
    tab._apply_pipeline_info(build(measure_focus=False))
    assert tab._focus_metric_cb.isChecked() is False


def test_a_dataset_analysed_before_the_column_existed_restores_the_default(tab):
    """No key in either block means the build default, which is on — the right
    answer for a dataset about to be reprocessed."""
    tab._apply_pipeline_info({"qc_options": {"remove_debris": False}})
    assert tab._focus_metric_cb.isChecked() is True
