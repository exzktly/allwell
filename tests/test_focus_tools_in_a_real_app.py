"""The focus tools, driven through a real ``ReviewApp``.

**Slow lane on purpose.** Every other test of this feature — about two hundred
assertions across seven files — runs on a hand-built context in the fast lane.
That lane never constructs an application, so it cannot see a control wired to
nothing, a panel that reads state the shell has just emptied, or a surface that
does not repaint. CLAUDE.md §7.8 names the price this repository already paid
for that once: a tab that raised on every real construction while roughly a
hundred and thirty of its own tests were green.

Each test here corresponds to a defect an adversarial review found that **no
fast-lane test could have caught**, and each was watched failing against the
unfixed code before the fix went in.

Run with::

    scripts/test.sh tests/test_focus_tools_in_a_real_app.py -m ""
"""
from __future__ import annotations

import dataclasses

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np

from pipeline.field_quality_record import FOCUS_COLUMN
from review.domain.qc_params import QCParams


def _give_the_plate_focus(app, soft_share=0.35, sharp=0.30, soft=0.02):
    """Write a realistic focus column onto every cached well.

    The fixture dataset predates the column, and this session installs no
    TensorFlow, so the values are injected rather than produced. Their *shape*
    is what matters: a sharp population with a soft corner, which is what a
    focus gradient across a field actually looks like.
    """
    rng = np.random.default_rng(11)
    for label, df in app._app_state.dataset.cache.items():
        if df is None or len(df) == 0:
            continue
        n = len(df)
        n_soft = max(1, int(n * soft_share))
        vals = np.concatenate([
            rng.normal(sharp, 0.04, n - n_soft).clip(0.12, None),
            rng.normal(soft, 0.006, n_soft).clip(0.004, None)])
        rng.shuffle(vals)
        df[FOCUS_COLUMN] = vals[:n]
        # The plate-wide "is the column there?" reads each well's open-time
        # summary first, so the injected column has to be declared there too.
        summaries = app._app_state.dataset.summaries
        if label in summaries and FOCUS_COLUMN not in summaries[label].columns:
            summaries[label] = dataclasses.replace(
                summaries[label], columns=(*summaries[label].columns, FOCUS_COLUMN))


# ── finding 3: a reset must not make the panel deny the measurement ─────────

def test_a_gating_reset_does_not_claim_the_dataset_was_never_measured(loaded_app):
    """**The defect.** The reset empties the row cache, and every focus
    predicate reads the cache — so the panel announced "This dataset was
    analysed before per-cell focus was measured", which is false, and named a
    remedy (re-run the whole plate) that is not needed.

    ``focus_gate``'s own module docstring calls this the one distinction that
    must never collapse. A fast-lane test cannot reach it: it needs the real
    reset scope, which needs a real app.
    """
    from review.data.focus_gate import focus_status
    from review.persistence.reset_scopes import clear_analysis

    app = loaded_app
    _give_the_plate_focus(app)
    app._app_state.qc.params = QCParams(focus_min=0.10)

    before, _ = focus_status(app._ctx)
    assert "before per-cell focus was measured" not in before, before

    # The dialog's own entry point, which isolates each scope: it logs a scope
    # that raises and carries on, so the returned list is the only evidence the
    # reset ran at all. Asserting it keeps this test from passing vacuously
    # against a gating scope that blew up before it emptied anything.
    assert clear_analysis(app._ctx, ["gating"]) == ["gating"]

    after, _ = focus_status(app._ctx)
    assert "before per-cell focus was measured" not in after, (
        "the reset emptied the cache and the panel now denies the dataset was "
        "measured at all: " + after)


def test_clearing_curation_does_not_deny_the_measurement_either(loaded_app):
    """The same state by a second route, which notifies nothing at all."""
    from review.data.focus_gate import focus_status
    from review.persistence.reset_scopes import clear_curation

    app = loaded_app
    _give_the_plate_focus(app)
    app._app_state.qc.params = QCParams(focus_min=0.10)
    clear_curation(app._ctx)
    after, _ = focus_status(app._ctx)
    assert "before per-cell focus was measured" not in after, after


# ── finding 6: the plate must repaint when the cut moves ────────────────────

def _plate_markers(app) -> set:
    """The wells the Home plate is currently marking, read off the widget.

    Deliberately the widget's own state rather than ``wells_needing_review``:
    CLAUDE.md §7.4 #1 — the defect here is an outbound call that never
    happened, and a test that reads the derivation passes happily while the
    screen stays wrong. This is what a user would be looking at.
    """
    plate = app._ctx.widgets.get_or_none("_home_plate")
    assert plate is not None, "the Home plate was never built"
    return set(plate._grid._markers)


def test_the_home_plate_repaints_when_the_focus_cut_moves(loaded_app):
    """**The defect.** ``wells_needing_review`` is a function of the cut and
    was reachable only through ``refresh_home`` — page activation, dataset
    load, and the initial build. No cut-change path called it.

    So a user could set the cut on Home ▸ Cell Gating, switch to Home ▸
    Overview, and read a plate still marking wells against the *previous*
    number, with the tooltips quoting it. Two surfaces on one page, one cut,
    two answers.

    The fixture has to make the two cuts disagree, or no assertion of any
    strength could tell a repaint from a stale paint (§7.8 rule 2).
    """
    from review.features.home.controller import refresh_home
    from review.features.qc.controller import set_qc_params

    app = loaded_app
    _give_the_plate_focus(app)

    # A cut that marks wells, established through the path that always worked.
    set_qc_params(app._ctx, QCParams(focus_min=0.10, exclude_soft_cells=False))
    refresh_home(app._ctx)
    marked = _plate_markers(app)
    assert marked, (
        "the fixture does not mark any well at a cut of 0.10, so this test "
        "cannot tell a repaint from a stale paint")

    # Now move it to a cut nothing is soft at, WITHOUT leaving the page.
    set_qc_params(app._ctx, QCParams(focus_min=0.001, exclude_soft_cells=False))

    assert not _plate_markers(app), (
        "the plate is still marking " + ", ".join(sorted(_plate_markers(app)))
        + " against the previous cut of 0.10; nothing repainted it")


def test_the_plate_marks_wells_again_when_the_cut_comes_back(loaded_app):
    """The other direction, which a callback wired to run only on a clear
    would pass the test above while failing this one."""
    from review.features.qc.controller import set_qc_params

    app = loaded_app
    _give_the_plate_focus(app)
    set_qc_params(app._ctx, QCParams(focus_min=0.001, exclude_soft_cells=False))
    assert not _plate_markers(app)

    set_qc_params(app._ctx, QCParams(focus_min=0.10, exclude_soft_cells=False))
    assert _plate_markers(app), (
        "raising the cut marked no well, so the plate is not tracking it")


# ── finding 4: the run's recorded cut must not be overridden in silence ─────

def _set_recorded_cut(app, value) -> None:
    """Rewrite the dataset's own sidecar cut, as a re-run would.

    `focus_min` is deliberately outside the reuse key, so a re-run that changes
    only the cut skips every well as up-to-date and still rewrites
    `pipeline_info.json` — that cheap path is the whole reason this defect is
    reachable, and the fixture has to reproduce it rather than a full re-run.
    """
    info = app._app_state.dataset.pipeline_info or {}
    info.setdefault("qc_options", {})["focus_min"] = value
    app._app_state.dataset.pipeline_info = info


def test_a_rerun_that_changes_the_cut_reaches_review(loaded_app):
    """**The defect.** Persistence won permanently.

    The `qc_params` section is written whenever *any* Cell Gating control
    moves — the debris checkbox, a bound, the field-quality box. Once written
    it holds a `focus_min`, and `get_qc_params`' lazy seed from the sidecar is
    never reached again for that dataset. So:

    1. the run records 0.08 and Review seeds 0.08;
    2. the user ticks the debris gate, which freezes 0.08 into persistence;
    3. the user decides 0.08 was wrong, types 0.12 into Analyze and reprocesses
       — which is cheap by design and rewrites the sidecar;
    4. Review reopens showing **0.08** while Analyze's form shows **0.12**.

    One dataset, two numbers, nothing anywhere saying so. This drives the real
    save and the real load rather than the accessor, because the defect is in
    what crosses the disk.
    """
    from review.persistence.qc_params import load_from_data_dir, save_to_data_dir
    from review.features.qc.controller import set_qc_params

    app = loaded_app
    _give_the_plate_focus(app)
    _set_recorded_cut(app, 0.08)

    # (2) The user touches a control that has nothing to do with focus.
    set_qc_params(app._ctx, QCParams(enabled=True, min_area=25.0, focus_min=0.08))
    save_to_data_dir(app._ctx)

    # (3) A re-run records a different cut.
    _set_recorded_cut(app, 0.12)

    # (4) Reopen.
    load_from_data_dir(app._ctx)
    assert app._app_state.qc.params.focus_min == 0.12, (
        "Review is still using the cut frozen into persistence before the "
        f"re-run: {app._app_state.qc.params.focus_min!r}, while the dataset's "
        "own sidecar now records 0.12")
    # And the debris bound the user really did choose survived the round trip,
    # which is what separates this fix from "stop persisting the panel".
    assert app._app_state.qc.params.min_area == 25.0


def test_a_cut_the_user_typed_survives_and_says_whose_it_is(loaded_app):
    """The other half, and the one a blanket "always re-seed" would break.

    A number the user deliberately typed is theirs and must survive a reload.
    What must not survive is the silence: Analyze's form shows the run's
    number, so Review has to name both.
    """
    from review.data.focus_gate import recorded_cut_note
    from review.persistence.qc_params import load_from_data_dir, save_to_data_dir
    from review.features.qc.controller import set_qc_params

    app = loaded_app
    _give_the_plate_focus(app)
    _set_recorded_cut(app, 0.08)

    set_qc_params(app._ctx, QCParams(focus_min=0.20))
    save_to_data_dir(app._ctx)
    load_from_data_dir(app._ctx)

    assert app._app_state.qc.params.focus_min == 0.20, (
        "a cut the user typed was discarded on reload")
    note = recorded_cut_note(app._ctx)
    assert "0.08" in note, (
        "the panel does not name the number the run recorded, so a user "
        f"cannot see why Analyze disagrees with them: {note!r}")


def test_nothing_is_said_when_the_cut_in_force_is_the_run_s(loaded_app):
    """A note on every ordinary dataset would be noise, and would train the
    reader to ignore the one that matters."""
    from review.data.focus_gate import recorded_cut_note
    from review.features.qc.controller import set_qc_params

    app = loaded_app
    _give_the_plate_focus(app)
    _set_recorded_cut(app, 0.08)
    set_qc_params(app._ctx, QCParams(focus_min=0.08))
    assert recorded_cut_note(app._ctx) == ""


# ── finding 8: the real control, pressed ───────────────────────────────────

def _gate_widgets(app):
    w = app._ctx.widgets.get_or_none("_qc_gate_widgets")
    assert w, "the Cell Gating gate row was never built"
    return w


def test_typing_something_unparseable_leaves_the_box_agreeing_with_the_state(loaded_app):
    """**The defect.** Typing "abc" into the cut left the box reading "abc"
    while ``params.focus_min`` was ``None`` and the sentence beneath said "No
    threshold set" — the control and the state disagreeing on screen, with
    nothing to correct it.

    Driven through ``editingFinished`` on the real ``QLineEdit``, because that
    is the seam the user reaches and the one the fix hangs off; a test of the
    parser would pass against every version of this, including the broken one
    (CLAUDE.md §7.6 #2).
    """
    app = loaded_app
    _give_the_plate_focus(app)
    edit = _gate_widgets(app)["focus_min"]

    edit.setText("0.10")
    edit.editingFinished.emit()
    assert app._app_state.qc.params.focus_min == 0.10
    assert edit.text() == "0.10"

    edit.setText("abc")
    edit.editingFinished.emit()
    assert app._app_state.qc.params.focus_min is None, (
        "an unparseable cut must mean no cut")
    assert edit.text() == "", (
        f"the box still reads {edit.text()!r} while no cut is in force")


def test_a_ticked_focus_box_can_still_be_unticked_on_the_real_panel(loaded_app):
    """The same defect as the unit test above, through the panel that refreshes
    it: the refresh is what disabled the box, so a fix applied only to the
    helper would leave the control unreachable anyway."""
    from review.features.qc.tab_view import _refresh_focus_status

    app = loaded_app
    _give_the_plate_focus(app)
    w = _gate_widgets(app)
    box, edit = w["exclude_soft_cells"], w["focus_min"]

    edit.setText("0.10")
    edit.editingFinished.emit()
    box.setChecked(True)
    _refresh_focus_status(app._ctx, w)
    assert box.isEnabled(), "the box is not live at a cut that excludes cells"

    # Drop the cut until nothing is soft. The box must stay reachable.
    edit.setText("0.0001")
    edit.editingFinished.emit()
    _refresh_focus_status(app._ctx, w)
    assert box.isChecked(), "the fixture no longer reproduces the armed state"
    assert box.isEnabled(), (
        "the box is ticked, armed, persisted as on, and the user cannot "
        "reach it to turn it off")
