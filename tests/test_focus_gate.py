"""The per-cell focus cut: what it drops, what it keeps, and what it says.

Every test here names the wrong implementation it excludes, because the
failures this layer can have are all quiet ones — a gate that filters nothing,
a gate that filters everything, and a gate that deletes the cells it could not
measure are each a plausible-looking function that passes any test asserting
only "some rows were removed".
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pipeline.field_quality_record import FOCUS_COLUMN
from review.data.focus_gate import (
    FOCUS_PASS_COLUMN,
    compute_focus_pass,
    dataset_has_focus_column,
    focus_counts,
    focus_values,
    plate_focus_counts,
)
from review.domain.qc_params import QCParams


def _frame(values, n_extra_cols: bool = True):
    """A well frame carrying *values* in the focus column.

    ``None`` in *values* becomes a real NaN, which is what pandas reads an
    empty CSV field as and therefore what an unmeasurable cell arrives as.
    """
    df = pd.DataFrame({
        "nucleus_id": list(range(1, len(values) + 1)),
        "area_px": [100] * len(values),
    })
    df[FOCUS_COLUMN] = [np.nan if v is None else v for v in values]
    return df


def _params(**kw):
    return QCParams(**kw)


ON = dict(exclude_soft_cells=True, focus_min=0.10)


# ── the verdict ─────────────────────────────────────────────────────────────

def test_a_soft_cell_is_dropped_and_a_sharp_one_is_kept():
    """Excludes the gate that filters nothing, and the one that filters
    everything — both of which pass a test asserting only that the column is
    there. The values straddle the cut in a known order, so the verdict is
    asserted position by position rather than as a count."""
    df = _frame([0.30, 0.05, 0.11, 0.09, 0.10])
    got = list(compute_focus_pass(df, _params(**ON)))
    assert got == [1, 0, 1, 0, 1], got


def test_the_cut_is_a_floor_not_a_ceiling():
    """Excludes the inequality written the wrong way round, which a test using
    one value on each side of the cut cannot see if it only counts rows."""
    df = _frame([0.02, 0.50])
    assert list(compute_focus_pass(df, _params(**ON))) == [0, 1]


def test_a_cell_that_could_not_be_measured_is_kept():
    """**Undefined is not soft.** Severe defocus degrades segmentation, so the
    column is missing exactly where defocus is worst — but so is a nucleus too
    small to carry a measurable ring, and treating the two alike would quietly
    delete the smallest nuclei on every plate.

    Excludes the implementation that compares NaN and gets the right answer by
    accident, because this asserts the value rather than the behaviour of a
    comparison.
    """
    df = _frame([None, 0.5, None, 0.01])
    assert list(compute_focus_pass(df, _params(**ON))) == [1, 1, 1, 0]


def test_nothing_is_filtered_while_the_switch_is_off():
    """A recorded cut is the user saying "this is my microscope's number", not
    "drop those cells from every plot"."""
    df = _frame([0.01, 0.02, 0.03])
    p = _params(exclude_soft_cells=False, focus_min=0.10)
    assert list(compute_focus_pass(df, p)) == [1, 1, 1]


def test_nothing_is_filtered_with_the_switch_on_and_no_cut():
    """The switch on with no number is a request nothing here can honour: §D.2d
    measured why no threshold can be guessed. Excludes the implementation that
    substitutes a default and silently filters against it."""
    df = _frame([0.01, 0.02, 0.03])
    p = _params(exclude_soft_cells=True, focus_min=None)
    assert p.focus_gate_is_active is False
    assert list(compute_focus_pass(df, p)) == [1, 1, 1]


def test_a_frame_without_the_column_passes_entirely():
    """A dataset analysed before focus was measured. Excludes the
    implementation that raises, and the one that drops every row because a
    missing column reads as zero."""
    df = pd.DataFrame({"nucleus_id": [1, 2], "area_px": [50, 60]})
    assert list(compute_focus_pass(df, _params(**ON))) == [1, 1]
    assert focus_values(df) is None


def test_an_all_nan_column_is_not_the_same_as_no_column():
    """A plate measured and unreadable, against a plate never measured. Both
    keep every row, and they must still be distinguishable — the panel says
    different things about them."""
    measured = _frame([None, None])
    assert focus_values(measured) is not None
    assert focus_values(measured).isna().all()


# ── the counts the panel reports ────────────────────────────────────────────

def test_the_unmeasurable_are_counted_apart_from_the_soft():
    """Excludes folding them into either other number. A cell nobody could
    measure is being **kept**, and a reader who believes the gate removed
    everything soft would be wrong about exactly those rows."""
    df = _frame([0.30, 0.05, None, 0.02, None, 0.40])
    soft, unmeasurable, total = focus_counts(df, _params(**ON))
    assert (soft, unmeasurable, total) == (2, 2, 6)


def test_the_counts_are_reported_before_the_switch_is_thrown():
    """A user choosing a threshold needs the consequence in front of them, not
    after the fact — so the counts follow the cut, not the switch. Excludes the
    implementation that reports zeroes until the gate is on."""
    df = _frame([0.30, 0.05, 0.02])
    off = _params(exclude_soft_cells=False, focus_min=0.10)
    assert focus_counts(df, off) == (2, 0, 3)


def test_no_cut_means_nothing_is_soft_rather_than_everything():
    df = _frame([0.30, 0.05, None])
    assert focus_counts(df, _params()) == (0, 1, 3)


def test_an_empty_frame_reports_nothing_rather_than_raising():
    assert focus_counts(_frame([]), _params(**ON)) == (0, 0, 0)


# ── the plate, through a ctx ────────────────────────────────────────────────

class _Ctx:
    """The attributes this layer reads, and nothing else: the wells the dataset
    has (the plate counts read every one, held or not), their frames, and the
    QC parameters."""

    def __init__(self, cache, params=None):
        self.state = type("S", (), {})()
        self.state.dataset = type("D", (), {})()
        self.state.dataset.cache = cache
        self.state.dataset.well_paths = dict.fromkeys(cache)
        self.state.qc = type("Q", (), {})()
        self.state.qc.params = params


def test_the_plate_counts_sum_every_cached_well():
    ctx = _Ctx({"A01": _frame([0.30, 0.05]),
                "A02": _frame([0.02, None, 0.50])},
               _params(**ON))
    assert plate_focus_counts(ctx) == (2, 1, 5)


def test_a_plate_with_no_column_anywhere_is_distinguishable():
    plain = pd.DataFrame({"nucleus_id": [1], "area_px": [50]})
    assert dataset_has_focus_column(_Ctx({"A01": plain})) is False
    assert dataset_has_focus_column(_Ctx({"A01": _frame([0.2])})) is True


def test_one_well_carrying_the_column_is_enough_to_say_the_plate_has_it():
    """A partly re-run plate. Excludes the ``all()`` spelling, which would call
    a plate columnless because one old well is still cached."""
    plain = pd.DataFrame({"nucleus_id": [1], "area_px": [50]})
    ctx = _Ctx({"A01": plain, "A02": _frame([0.2])})
    assert dataset_has_focus_column(ctx) is True


# ── the projector, and the column it writes onto the cache ──────────────────

def test_the_gate_writes_its_column_onto_the_cache():
    from review.data.focus_gate import apply_focus_gate_to_cache

    ctx = _Ctx({"A01": _frame([0.30, 0.05, None])}, _params(**ON))
    assert apply_focus_gate_to_cache(ctx) == []
    assert list(ctx.state.dataset.cache["A01"][FOCUS_PASS_COLUMN]) == [1, 0, 1]


def test_the_gate_writes_nothing_while_it_is_off():
    """Deliberately not an all-pass column: an explicit 1 everywhere would say
    the plate was measured and found sharp, which is not what a plate with the
    gate switched off says about itself. Leaving the column out puts the frame
    in the state every consumer already treats as all-pass."""
    from review.data.focus_gate import apply_focus_gate_to_cache

    ctx = _Ctx({"A01": _frame([0.30, 0.05])},
               _params(exclude_soft_cells=False, focus_min=0.10))
    assert apply_focus_gate_to_cache(ctx) == []
    assert FOCUS_PASS_COLUMN not in ctx.state.dataset.cache["A01"].columns


# ── the chokepoint ──────────────────────────────────────────────────────────

def test_the_chokepoint_ands_the_focus_column_with_the_others():
    """Excludes the column that is written and never read — the defect this
    application has had before, where a verdict is computed correctly and
    nothing downstream consults it."""
    from review.data.data_loading import df_included_mask

    df = pd.DataFrame({
        "Included":   [1, 1, 1, 1],
        "QC_Pass":    [1, 1, 1, 0],
        "Focus_Pass": [1, 0, 1, 1],
    })
    assert list(df_included_mask(df)) == [True, False, True, False]


def test_an_unreadable_focus_verdict_keeps_the_row():
    """Same fail-open as its four siblings."""
    from review.data.data_loading import df_included_mask

    df = pd.DataFrame({"Included": [1, 1], "Focus_Pass": [np.nan, "x"]})
    assert list(df_included_mask(df)) == [True, True]


# ── the parameters ──────────────────────────────────────────────────────────

def test_the_run_s_recorded_cut_seeds_the_gate_without_switching_it_on():
    p = QCParams.from_pipeline_info({"qc_options": {"focus_min": 0.08}})
    assert p.focus_min == pytest.approx(0.08)
    assert p.exclude_soft_cells is False
    assert p.focus_gate_is_active is False


def test_a_run_that_stated_no_cut_seeds_none_rather_than_zero():
    """0.0 is how the sidecar spells "no cut stated", and a cut of zero would
    exclude every cell in the plate."""
    p = QCParams.from_pipeline_info({"qc_options": {"focus_min": 0.0}})
    assert p.focus_min is None


def test_both_settings_survive_a_save_and_reload():
    p = QCParams(exclude_soft_cells=True, focus_min=0.12)
    back = QCParams.from_dict(p.to_dict())
    assert back.exclude_soft_cells is True
    assert back.focus_min == pytest.approx(0.12)


# ── the sentences the panel says ────────────────────────────────────────────

def test_a_dataset_that_predates_the_column_says_so_rather_than_nothing():
    """A plate nobody measured and a plate measured and found sharp must not
    read alike. Excludes "0 cells are out of focus", which is true of both."""
    from review.data.focus_gate import REGISTER_MUTED, focus_status

    plain = pd.DataFrame({"nucleus_id": [1], "area_px": [50]})
    text, register = focus_status(_Ctx({"A01": plain}, _params(**ON)))
    assert register == REGISTER_MUTED
    assert "before per-cell focus was measured" in text
    assert "0" not in text


def test_a_measured_plate_with_no_cut_points_at_where_a_number_comes_from():
    """§D.2d: nothing here can guess a threshold, so the sentence has to send
    the user somewhere rather than leave them with a blank field."""
    from review.data.focus_gate import focus_status

    text, _ = focus_status(_Ctx({"A01": _frame([0.2, 0.3])}, _params()))
    assert "No threshold set" in text
    assert "Playground" in text


def test_the_unmeasurable_are_named_in_the_sentence_as_kept():
    """Excludes the sentence that reports only the count below the cut, which
    leaves a reader believing the gate removed everything soft."""
    from review.data.focus_gate import REGISTER_PROBLEM, focus_status

    ctx = _Ctx({"A01": _frame([0.3, 0.02, None, None])}, _params(**ON))
    text, register = focus_status(ctx)
    assert register == REGISTER_PROBLEM
    assert "2 cells carry no focus value and are kept" in text


def test_the_singular_sentence_agrees_with_itself():
    """One cell, and the verbs have to follow. Found by printing the real
    panel's text rather than by reading the f-string, which is the only way
    this kind of defect is ever caught."""
    from review.data.focus_gate import focus_status

    text, _ = focus_status(_Ctx({"A01": _frame([0.3, None])}, _params(**ON)))
    assert "1 cell carries no focus value and is kept" in text
    assert "cell carry" not in text and "cell are" not in text


def test_the_effect_line_is_the_only_thing_that_moves_with_the_switch():
    """The finding is reported either way, which would otherwise leave the
    panel saying nothing at all about what ticking the box did."""
    from review.data.focus_gate import focus_effect_text, focus_status

    cache = {"A01": _frame([0.3, 0.02])}
    off = _Ctx(cache, _params(exclude_soft_cells=False, focus_min=0.10))
    on = _Ctx(cache, _params(**ON))
    assert focus_status(off)[0] == focus_status(on)[0]
    # "across the plate" is load-bearing: the count is plate-wide while every
    # plot is scoped to the user's selection, so the two are different
    # quantities and the sentence has to say which one it is.
    assert focus_effect_text(off) == (
        "Currently kept — 1 cell across the plate would be excluded.")
    assert focus_effect_text(on) == "Currently excluding 1 cell across the plate."


def test_the_box_is_dead_when_ticking_it_would_do_nothing():
    """**Not "is there a column".** That weaker predicate leaves the box live
    on a dataset with no threshold: the user ticks it, it stays ticked, and
    every number is then read by someone who believes a filter is on."""
    from review.data.focus_gate import focus_gate_can_apply

    measured_no_cut = _Ctx({"A01": _frame([0.3, 0.02])}, _params())
    nothing_soft = _Ctx({"A01": _frame([0.3, 0.4])}, _params(**ON))
    something_soft = _Ctx({"A01": _frame([0.3, 0.02])}, _params(**ON))
    assert focus_gate_can_apply(measured_no_cut) is False
    assert focus_gate_can_apply(nothing_soft) is False
    assert focus_gate_can_apply(something_soft) is True


# ── the control itself, not its existence ───────────────────────────────────

def _stub_row(qtbot, cache, params):
    """The real ``build_qc_gate_controls``, on the real state and registry.

    §7.6: asserting a control is *offered* is not asserting it *works*, and
    the gap between those two is where every callback bug in a Qt app lives.
    This builds the shipped widget and presses it — on :func:`_live_ctx`,
    whose projector registry is populated, so a tick reaches the column rather
    than stopping at the params.
    """
    from PySide6.QtWidgets import QWidget

    from review.features.qc.tab_view import build_qc_gate_controls

    ctx = _live_ctx(cache, params)
    app = ctx.app
    app._ctx = ctx
    parent = QWidget()
    qtbot.addWidget(parent)
    row = build_qc_gate_controls(ctx, parent)
    qtbot.addWidget(row)
    # Held on the returned object, not left as locals: the widget registry
    # keeps only weak handles, so a `parent` that falls out of scope takes the
    # C++ side of every control with it and the next line reads a deleted
    # QCheckBox.
    app._parent, app._row = parent, row
    return app


def test_the_panel_offers_the_focus_control_seeded_from_the_params(qtbot):
    app = _stub_row(qtbot, {"A01": _frame([0.3, 0.02])},
                    _params(focus_min=0.10))
    w = app._qc_gate_widgets
    assert w["focus_min"].text() == "0.1"
    assert w["exclude_soft_cells"].isChecked() is False
    assert w["exclude_soft_cells"].isEnabled() is True


def test_ticking_the_box_reaches_the_params(qtbot):
    """The §7.6 test. Excludes a checkbox connected to nothing, which every
    test asserting only that the control is *present* passes against.

    The assertion is on the column the tick produces rather than only on the
    params, because a control that writes the right parameter into a gate
    nobody registered is the same bug wearing a different hat.
    """
    app = _stub_row(qtbot, {"A01": _frame([0.3, 0.02])},
                    _params(focus_min=0.10))
    w = app._qc_gate_widgets
    w["exclude_soft_cells"].setChecked(True)
    assert app._app_state.qc.params.exclude_soft_cells is True
    assert app._app_state.qc.params.focus_min == pytest.approx(0.10)
    # The re-projection runs behind the panel's progress popup.
    from tests._panel_jobs import wait_for_panel_jobs
    wait_for_panel_jobs(qtbot, app._ctx)
    # And the column the tick is *for*, written by the projector the handler
    # drives. Asserting the params alone would pass against a gate registered
    # nowhere -- §7.4 #1, put the assertion at the boundary.
    assert list(app._app_state.dataset.cache["A01"]["Focus_Pass"]) == [1, 0]


def test_typing_a_cut_reaches_the_params(qtbot):
    """The other half — without it the test above passes against a box whose
    text is never read."""
    app = _stub_row(qtbot, {"A01": _frame([0.3, 0.02])}, _params())
    w = app._qc_gate_widgets
    w["focus_min"].setText("0.25")
    w["focus_min"].editingFinished.emit()
    assert app._app_state.qc.params.focus_min == pytest.approx(0.25)


def test_a_reload_refills_both_the_switch_and_the_cut(qtbot):
    """``qc_gate_refresh_widgets`` mirrors already-applied params. A control it
    forgets shows the previous dataset's value over the new one's data."""
    from review.features.qc.tab_view import qc_gate_refresh_widgets

    app = _stub_row(qtbot, {"A01": _frame([0.3, 0.02])}, _params())
    app._app_state.qc.params = _params(exclude_soft_cells=True, focus_min=0.22)
    qc_gate_refresh_widgets(app)
    w = app._qc_gate_widgets
    assert w["focus_min"].text() == "0.22"
    assert w["exclude_soft_cells"].isChecked() is True


def test_a_reload_onto_a_dataset_with_no_cut_blanks_the_box(qtbot):
    """Excludes the refresh that skips a None and leaves the previous
    dataset's threshold on screen."""
    from review.features.qc.tab_view import qc_gate_refresh_widgets

    app = _stub_row(qtbot, {"A01": _frame([0.3, 0.02])},
                    _params(focus_min=0.10))
    app._app_state.qc.params = _params()
    qc_gate_refresh_widgets(app)
    assert app._qc_gate_widgets["focus_min"].text() == ""


# ── the re-projection a control depends on ──────────────────────────────────
#
# These use a real ``AppState``, a real ``AppRuntime`` and the real feature
# registry rather than a namespace, because the first version of this probe
# used a ``SimpleNamespace`` whose projector registry was **empty**: it ran,
# printed a confident answer, and measured nothing. A harness that fails open
# will happily confirm anything (CLAUDE.md §7.3 #1).

def _live_ctx(cache, params, pipeline_info=None):
    from types import SimpleNamespace

    from review.core.app_runtime import AppRuntime
    from review.core.app_state import AppState
    from review.core.widget_registry import WidgetRegistry
    from review.features.registry import attach_feature_state, build_feature_registry

    build_feature_registry()          # registers the cache projectors
    st = AppState()
    attach_feature_state(st)
    st.dataset.cache = cache
    st.dataset.well_paths.update(dict.fromkeys(cache))
    st.dataset.pipeline_info = pipeline_info or {}
    st.dataset.fov_integrity = {k: True for k in cache}
    st.qc.params = params
    app = SimpleNamespace(_app_state=st)
    return SimpleNamespace(state=st, app=app, widgets=WidgetRegistry(app),
                           persistence=None, runtime=AppRuntime())


def _flagged_block():
    """A plate where field 1 is wildly out of line and the rest agree."""
    from pipeline.field_quality import FIELD_QUALITY_BLOCK

    rows = [{"well": "A01", "fov": str(f), "timepoint": "0",
             "focus": (0.0001 if f == 1 else 0.30 + 0.001 * f),
             "sat_frac": 0.0, "dyn_range": 1000.0, "nuc_count": 50,
             "nuc_area_median": 80.0, "bg_level": 100.0}
            for f in range(1, 9)]
    return {FIELD_QUALITY_BLOCK: {"wells": {"A01": {"fields": rows}},
                                  "partial_wells": []}}


def _two_fields():
    return pd.DataFrame({"nucleus_id": [1, 2], "area_px": [100, 100],
                         "fov": ["1", "2"], "timepoint": ["0", "0"],
                         "Included": [1, 1]})


def test_turning_a_verdict_layer_on_excludes_immediately():
    """**The defect this found, which had already shipped.** The cache
    projectors run at *load*; the pass that runs when a Cell Gating control
    moves re-projected ``QC_Pass`` alone, which was correct while the panel
    held only the debris gate and silently wrong the moment it grew a second
    switchable layer.

    Measured on the pre-fix tree with this exact fixture: one field flagged,
    the box ticked, **no ``Field_Pass`` column written at all**, and the row
    still included — while the panel reported the flag correctly, so the
    control looked like it had worked. The exclusion did not happen until the
    dataset was reopened.
    """
    from review.data.data_loading import df_included_mask
    from review.data.field_quality_cache import flagged_fields
    from review.features.qc.controller import set_qc_params

    ctx = _live_ctx({"A01": _two_fields()}, QCParams(), _flagged_block())
    flagged, refusal = flagged_fields(ctx)
    assert not refusal and flagged, "the fixture must flag something to measure"
    assert list(df_included_mask(ctx.state.dataset.cache["A01"])) == [True, True]

    set_qc_params(ctx, QCParams(exclude_flagged_fields=True))
    df = ctx.state.dataset.cache["A01"]
    assert "Field_Pass" in df.columns
    assert list(df_included_mask(df)) == [False, True]


def test_turning_a_verdict_layer_off_stops_excluding_immediately():
    """The other half, and its own defect: a projector whose switch is off
    returned early and wrote nothing, which leaves the previous setting's
    column on the frame. Unticking left the rows excluded while the panel said
    "currently kept" over them."""
    from review.data.data_loading import df_included_mask
    from review.features.qc.controller import set_qc_params

    ctx = _live_ctx({"A01": _two_fields()}, QCParams(), _flagged_block())
    set_qc_params(ctx, QCParams(exclude_flagged_fields=True))
    assert list(df_included_mask(ctx.state.dataset.cache["A01"])) == [False, True]

    set_qc_params(ctx, QCParams(exclude_flagged_fields=False))
    df = ctx.state.dataset.cache["A01"]
    assert "Field_Pass" not in df.columns
    assert list(df_included_mask(df)) == [True, True]


def test_the_focus_gate_excludes_and_un_excludes_immediately():
    """The same two properties for the layer this branch adds, asserted
    through the same real path rather than by analogy — the defect above was
    invisible to every test the layer it broke already had."""
    from review.data.data_loading import df_included_mask
    from review.features.qc.controller import set_qc_params

    df = _frame([0.30, 0.02, None])
    df["Included"] = 1
    ctx = _live_ctx({"A01": df}, QCParams())
    assert list(df_included_mask(ctx.state.dataset.cache["A01"])) == [True] * 3

    set_qc_params(ctx, QCParams(exclude_soft_cells=True, focus_min=0.10))
    got = ctx.state.dataset.cache["A01"]
    assert "Focus_Pass" in got.columns
    assert list(df_included_mask(got)) == [True, False, True]

    set_qc_params(ctx, QCParams(exclude_soft_cells=False, focus_min=0.10))
    back = ctx.state.dataset.cache["A01"]
    assert "Focus_Pass" not in back.columns
    assert list(df_included_mask(back)) == [True] * 3


def test_the_two_image_quality_layers_compose_rather_than_replace():
    """Neither subsumes the other — a field whose median is fine can still have
    a soft third — so both columns must be able to sit on one frame and be
    ANDed. Excludes the implementation where the newer layer's projector
    overwrites or removes the older one's column."""
    from review.data.data_loading import df_included_mask
    from review.features.qc.controller import set_qc_params

    df = _two_fields()
    df[FOCUS_COLUMN] = [0.50, 0.02]     # field 1 flagged, field 2's cell soft
    ctx = _live_ctx({"A01": df}, QCParams(), _flagged_block())
    set_qc_params(ctx, QCParams(exclude_flagged_fields=True,
                                exclude_soft_cells=True, focus_min=0.10))
    got = ctx.state.dataset.cache["A01"]
    assert {"Field_Pass", "Focus_Pass"} <= set(got.columns)
    assert list(got["Field_Pass"]) == [0, 1]
    assert list(got["Focus_Pass"]) == [1, 0]
    assert list(df_included_mask(got)) == [False, False]


# ── the well composite, through the data layer ──────────────────────────────

def test_the_marker_sees_a_gradient_no_field_verdict_can():
    """**The case the composite exists for.** The plate's own field verdicts
    are clean; a third of one well's cells are soft while its median is
    perfectly sharp, which is what a focus gradient across a field looks like.
    Excludes the marker built on the field verdict alone, which this branch
    started with."""
    from review.data.focus_gate import wells_needing_review

    clean = _frame([0.30] * 50)
    gradient = _frame([0.30] * 35 + [0.02] * 15)
    ctx = _live_ctx({"A01": clean, "A02": gradient},
                    QCParams(focus_min=0.10))
    marked = wells_needing_review(ctx)
    assert set(marked) == {"A02"}
    assert "15 of 50 cells below 0.1" in marked["A02"]


def test_the_marker_still_carries_the_run_s_own_field_flags():
    """The composite adds a reason; it must not drop the one that was there.
    Excludes the replacement that quietly loses every flagged well on a
    dataset with no focus column."""
    from review.data.focus_gate import wells_needing_review

    ctx = _live_ctx({"A01": _two_fields()}, QCParams(), _flagged_block())
    marked = wells_needing_review(ctx)
    assert "A01" in marked
    assert "flagged" in marked["A01"]


def test_a_dataset_with_neither_marks_nothing():
    """A plate with no focus column and no flagged fields. Excludes the
    implementation that marks every well it has rows for."""
    from review.data.focus_gate import wells_needing_review

    plain = pd.DataFrame({"nucleus_id": [1, 2], "area_px": [50, 60]})
    assert wells_needing_review(_live_ctx({"A01": plain}, QCParams())) == {}


def test_nothing_is_marked_on_focus_before_a_cut_is_chosen():
    """§D.2d: no threshold can be guessed, so a dataset whose run stated none
    is not silently judged against an invented one."""
    from review.data.focus_gate import wells_needing_review

    ctx = _live_ctx({"A01": _frame([0.30] * 20 + [0.02] * 30)}, QCParams())
    assert wells_needing_review(ctx) == {}


def test_the_marker_does_not_wait_for_the_gate_to_be_switched_on():
    """The plate map reports; the gate filters. A user who has not chosen to
    exclude anything is still owed the finding — which is the same split the
    Cell Gating panel already makes between its sentence and its effect line.
    """
    from review.data.focus_gate import wells_needing_review

    cache = {"A01": _frame([0.30] * 35 + [0.02] * 15)}
    off = _live_ctx(cache, QCParams(focus_min=0.10))
    on = _live_ctx(cache, QCParams(exclude_soft_cells=True, focus_min=0.10))
    assert wells_needing_review(off) == wells_needing_review(on)
    assert wells_needing_review(off)


def test_the_unmeasurable_reach_the_composite_as_missing_rather_than_zero():
    """A NaN is counted as unmeasurable, apart from the soft. Excludes the
    adapter that drops them (the composite could then not count them) and the
    one that coerces them to 0.0 (every plate with small nuclei reads as soft):
    the first would tally 2 cells in total, the second 2 soft."""
    from review.data.focus_gate import per_well_focus_tallies
    from review.domain.well_composite import FocusTally

    ctx = _live_ctx({"A01": _frame([0.30, None, 0.02])}, QCParams())
    assert per_well_focus_tallies(ctx, 0.10) == {
        "A01": FocusTally(total=3, unmeasurable=1, measurable=2, median=0.16, soft=1)}


def test_a_well_with_no_column_reaches_the_composite_as_empty():
    """Not omitted: "measured and empty" and "never measured" stay
    distinguishable one layer up."""
    from review.data.focus_gate import per_well_focus_tallies
    from review.domain.well_composite import FocusTally

    plain = pd.DataFrame({"nucleus_id": [1], "area_px": [50]})
    assert per_well_focus_tallies(_live_ctx({"A01": plain}, QCParams()), 0.10) == {"A01": FocusTally()}


# ── the absolute-value guard ────────────────────────────────────────────────
#
# **The one property the whole layer rests on, and the one a later change is
# most likely to undo while making everything look better.** Normalising a
# per-cell score by its own frame is an obvious-seeming improvement — it makes
# the numbers dimensionless and comparable-looking — and it silently destroys
# the only case the metric exists for: a frame that is *uniformly* soft, where
# every cell deviates from its neighbours by nothing. Measured on the
# frame-relative estimator this replaced, the relative median is exactly 1.000
# at sigma 0, 1.5 and 3.0.
#
# These are behavioural rather than an AST scan, deliberately. A scan for a
# division has to name the shapes it knows about and is blind to the next one;
# driving a uniformly-soft frame through the real decision path cannot be
# evaded by spelling, because the wrong answer is the same whatever produced
# it.

def _uniform(value, n=40):
    """A frame whose every cell reads the same — the case a relative score
    cannot see, because there is nothing to be relative to."""
    df = _frame([value] * n)
    df["Included"] = 1
    return df


def test_a_uniformly_soft_frame_is_still_excluded():
    """Excludes any normalisation by a per-frame statistic. Under one, every
    cell here scores 1.0 (or 0.0 deviation) and **nothing** is dropped, which
    is the exact failure the estimator was replaced to fix."""
    from review.data.data_loading import df_included_mask
    from review.features.qc.controller import set_qc_params

    ctx = _live_ctx({"A01": _uniform(0.02)}, QCParams())
    set_qc_params(ctx, QCParams(exclude_soft_cells=True, focus_min=0.10))
    got = ctx.state.dataset.cache["A01"]
    assert list(got["Focus_Pass"]) == [0] * 40
    assert not df_included_mask(got).any()


def test_a_uniformly_sharp_frame_is_still_kept():
    """The other half — without it the test above passes against a gate that
    excludes everything unconditionally."""
    from review.features.qc.controller import set_qc_params

    ctx = _live_ctx({"A01": _uniform(0.40)}, QCParams())
    set_qc_params(ctx, QCParams(exclude_soft_cells=True, focus_min=0.10))
    assert list(ctx.state.dataset.cache["A01"]["Focus_Pass"]) == [1] * 40


def test_two_wells_at_different_focus_get_different_verdicts():
    """A per-*well* normalisation is the same defect one pooling up, and it is
    invisible to a single-well fixture: normalise within each well and these
    two frames — internally uniform, an order of magnitude apart — come out
    identical."""
    from review.features.qc.controller import set_qc_params

    ctx = _live_ctx({"A01": _uniform(0.40), "A02": _uniform(0.02)}, QCParams())
    set_qc_params(ctx, QCParams(exclude_soft_cells=True, focus_min=0.10))
    cache = ctx.state.dataset.cache
    assert list(cache["A01"]["Focus_Pass"]) == [1] * 40
    assert list(cache["A02"]["Focus_Pass"]) == [0] * 40


def test_a_uniformly_soft_plate_marks_every_well_rather_than_none():
    """The composite has the same exposure one level up: a rule keyed on how a
    well compares with the rest of the plate marks nothing on a plate where
    every well is equally bad — which is the worst plate it could be shown."""
    from review.data.focus_gate import wells_needing_review

    cache = {w: _uniform(0.02) for w in ("A01", "A02", "A03", "A04")}
    marked = wells_needing_review(_live_ctx(cache, QCParams(focus_min=0.10)))
    assert set(marked) == set(cache)
    assert all("the whole well is soft" in text for text in marked.values())


def test_a_uniformly_sharp_plate_marks_nothing():
    """And its complement, so the test above cannot pass against a composite
    that marks every well it is given."""
    from review.data.focus_gate import wells_needing_review

    cache = {w: _uniform(0.40) for w in ("A01", "A02", "A03", "A04")}
    assert wells_needing_review(_live_ctx(cache, QCParams(focus_min=0.10))) == {}


def test_the_plot_marks_the_cut_where_the_user_put_it():
    """The third surface, and the one where a relative axis would be most
    tempting: a distribution drawn on a normalised x-axis would put the cut in
    a different place on every frame, and a user comparing two frames would be
    comparing two different rulers."""
    from review.features.image_browser.playground.focus_curve_view import (
        draw_focus_distribution,
    )
    from review.domain.focus_distribution import distribution_from_values

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    for values in ([0.02] * 20, [0.40] * 20, [0.02] * 10 + [0.40] * 10):
        fig = Figure(figsize=(6, 4))
        draw_focus_distribution(fig, distribution_from_values(values, cut=0.10))
        ax = fig.axes[0]
        # By label, not by "is it vertical": on a uniform frame the
        # cumulative curve is *itself* a vertical line, which is correct and
        # which made the geometric filter ambiguous on exactly the fixtures
        # this test needs.
        cut_line = [ln for ln in ax.lines
                    if str(ln.get_label()).startswith("your cut")]
        assert len(cut_line) == 1, values
        assert cut_line[0].get_xdata()[0] == pytest.approx(0.10), values


# ── the numbers are about the cells being analysed ──────────────────────────
#
# An adversarial review found every count here walking every row of the frame
# and consulting none of the sibling verdicts. The fixtures below are built so
# the right and the wrong answer differ (§7.8 rule 2): the frames carry rows
# the other gates have already dropped, and those rows are the unmeasurable
# and the soft ones, which is what a real debris-heavy plate looks like —
# `edge_focus` cannot read a fragment's outline, and a shattered fragment is
# exactly what `qc_region_area` exists to remove.


def _frame_with_debris(kept, debris):
    """*kept* survives the debris gate; *debris* does not. Same column."""
    df = _frame(list(kept) + list(debris))
    df["QC_Pass"] = [1] * len(kept) + [0] * len(debris)
    return df


def test_debris_the_qc_gate_already_dropped_is_not_counted_unmeasurable():
    """**The defect.** `UNMEASURABLE_FRACTION` fired on wells whose unreadable
    outlines were precisely the fragments the debris gate exists to remove, so
    a debris-heavy plate marked every well "needs review" about cells the user
    had already filtered out."""
    df = _frame_with_debris(kept=[0.30, 0.31, 0.29], debris=[None, None, None])
    soft, unmeasurable, total = focus_counts(df, _params(focus_min=0.10))
    assert (soft, unmeasurable, total) == (0, 0, 3), (soft, unmeasurable, total)


def test_soft_rows_already_out_are_not_counted_as_this_gates_effect():
    """"Currently excluding N cells" counted rows the focus layer marks, not
    rows it removes from the analysis — so with 80 % of them already out via
    the debris gate the sentence overstated the effect fivefold."""
    df = _frame_with_debris(kept=[0.30, 0.02], debris=[0.01, 0.01, 0.01, 0.01])
    soft, _unmeasurable, total = focus_counts(df, _params(focus_min=0.10))
    assert (soft, total) == (1, 2), (soft, total)


def test_every_sibling_layer_is_honoured_not_just_the_debris_one():
    """Composed from the chokepoint rather than from a second list here, so a
    layer added later is honoured by having been added there."""
    from review.data.data_loading import VERDICT_LAYERS

    for column in VERDICT_LAYERS:
        if column == FOCUS_PASS_COLUMN:
            continue
        df = _frame([0.02, 0.02, 0.30])
        df[column] = [0, 0, 1]
        soft, _u, total = focus_counts(df, _params(focus_min=0.10))
        assert (soft, total) == (0, 1), f"{column} was ignored: {(soft, total)}"


def test_manual_deletion_takes_a_cell_out_of_the_count_too():
    df = _frame([0.02, 0.30])
    df["Included"] = [0, 1]
    soft, _u, total = focus_counts(df, _params(focus_min=0.10))
    assert (soft, total) == (0, 1)


def test_the_gate_does_not_filter_its_own_evidence_by_its_own_verdict():
    """The one layer that must NOT be subtracted.

    Name the wrong implementation: composing the full chokepoint here — the
    obvious way to write this — makes the panel report nothing to exclude the
    moment the gate is switched on, because `Focus_Pass` has by then removed
    exactly the rows the sentence is counting.
    """
    df = _frame([0.02, 0.30])
    df[FOCUS_PASS_COLUMN] = [0, 1]          # the gate is on and has judged
    soft, _u, total = focus_counts(df, _params(focus_min=0.10))
    assert (soft, total) == (1, 2), (soft, total)


def test_the_well_curve_is_drawn_over_the_analysed_cells():
    """A user choosing a cut off this plot must see the population the cut
    will actually meet, not one padded with debris."""
    from types import SimpleNamespace

    from review.data.focus_gate import well_focus_distribution

    df = _frame_with_debris(kept=[0.30, 0.31], debris=[0.01, 0.01, 0.01])
    ctx = SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(cache={"A01": df}, well_paths={"A01": None}, pipeline_info={}),
        qc=SimpleNamespace(params=_params(focus_min=0.10))))
    dist = well_focus_distribution(ctx, "A01")
    assert dist.values == (0.30, 0.31), dist.values


# ── the box must stay disarmable ────────────────────────────────────────────

def test_a_ticked_box_stays_live_even_when_it_would_drop_nothing():
    """**The defect.** ``setEnabled(can_apply)`` disabled a box without
    unticking it, so the gate stayed armed, ``_fields_params`` read
    ``isChecked()`` and persisted it as on, and the user could not reach the
    control to turn it off. Lowering the cut until nothing was soft was enough
    to get there; raising it again later fired the gate immediately, on a
    control they were last able to touch in a different state.

    Disabling exists to stop someone **arming** a control that would do
    nothing. A control that is already armed must stay disarmable.
    """
    from review.features.qc.tab_view import _arm_state

    class _Box:
        def __init__(self, checked):
            self._checked, self.enabled = checked, None

        def isChecked(self):
            return self._checked

        def setEnabled(self, v):
            self.enabled = v

    armed = _Box(checked=True)
    _arm_state(armed, can_apply=False)
    assert armed.enabled is True, (
        "a ticked box was disabled, so the gate is armed and the user cannot "
        "untick it")

    idle = _Box(checked=False)
    _arm_state(idle, can_apply=False)
    assert idle.enabled is False, (
        "an unticked box that would do nothing must not look live -- that is "
        "the weaker predicate this application has shipped before")

    live = _Box(checked=False)
    _arm_state(live, can_apply=True)
    assert live.enabled is True
