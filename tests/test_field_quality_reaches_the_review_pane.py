"""From the run's ``field_quality`` block to what the user can see and do.

``tests/test_field_quality.py`` measures the statistics and
``tests/test_field_quality_is_wired_into_a_run.py`` pins the pipeline half.
This is the Review half: the projector that turns verdicts into a row filter,
the refusals that stop it filtering on evidence it does not have, the
sentences that say so, and the plate marker.

Every test below names the wrong implementation it excludes (CLAUDE.md §7.8).
The recurring one is the same in every case: a version that *filters anyway*.
A plate the user cannot account for is the failure this subsystem exists to
prevent, and the filter that quietly drops rows is indistinguishable in a plot
from one that had nothing to drop.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd
import pytest

from pipeline.field_quality import FIELD_QUALITY_BLOCK
from review.data import field_quality_cache as fq
from review.domain.fov_integrity import FovIntegrity
from review.domain.qc_params import QCParams

WELLS = ("A01", "A02", "A03", "B01", "B02", "B03")


def _fields(well, *, n=3, focus=27.0, sat=0.0):
    return [{"fov": str(i + 1), "timepoint": "0", "focus": focus + i * 0.01,
             "sat_frac": sat, "dyn_range": 900.0 + i, "nuc_count": 300 + i,
             "nuc_area_median": 150.0 + i, "bg_level": 100.0 + i}
            for i in range(n)]


def _block(*, bad_well=None, bad_fov="2", partial=False):
    """A plate of six wells, three fields each, with one field optionally
    defocused far enough to flag.

    Every metric varies across every field. That is not decoration: with the
    values identical the pooled MAD is zero, ``robust_z`` refuses, and a
    scorer that ought to flag instead *declines* — so the fixture would pass
    against the code it exists to test (§7.8 rule 2).
    """
    wells = {}
    for w in WELLS:
        flds = _fields(w, focus=27.0 + WELLS.index(w) * 0.05)
        if w == bad_well:
            for f in flds:
                if f["fov"] == bad_fov:
                    f["focus"] = 2.0          # badly out of focus
        wells[w] = {"well": w, "fields": flds, "measured": len(flds),
                    "measured_of": len(flds) + (2 if partial and w == WELLS[0]
                                                else 0)}
    return {
        "wells": wells,
        "partial_wells": [WELLS[0]] if partial else [],
        "measured": sum(r["measured"] for r in wells.values()),
        "measured_of": sum(r["measured_of"] for r in wells.values()),
    }


def _frame(well, fovs=("1", "2", "3")):
    return pd.DataFrame({
        "well": [well] * len(fovs),
        "fov": list(fovs),
        "timepoint": ["0"] * len(fovs),
        "nucleus_id": list(range(len(fovs))),
    })


def _ctx(block, *, exclude=True, integrity=None, cache=None):
    cache = cache if cache is not None else {w: _frame(w) for w in WELLS}
    return SimpleNamespace(state=SimpleNamespace(
        dataset=SimpleNamespace(
            pipeline_info=({FIELD_QUALITY_BLOCK: block} if block else {}),
            fov_integrity=dict(integrity or {}),
            cache=cache),
        qc=SimpleNamespace(
            params=QCParams(exclude_flagged_fields=exclude))))


# ── the filter only runs when it was asked for ──────────────────────────────

def test_nothing_is_filtered_until_the_user_turns_it_on():
    """Excludes the default that quietly drops rows.

    The wrong implementation that passes "the flagged field is found" is one
    that writes ``Field_Pass`` regardless: the user opens a dataset, their
    counts are lower than the CSV's, and nothing on screen says why.
    """
    ctx = _ctx(_block(bad_well="A02"), exclude=False)
    assert fq.apply_field_quality_to_cache(ctx) == []
    for df in ctx.state.dataset.cache.values():
        assert "Field_Pass" not in df.columns
    assert QCParams().exclude_flagged_fields is False, "off by default"

    # And with it on, the same plate does filter — or the assertion above is
    # satisfied by a projector that never writes anything at all.
    on = _ctx(_block(bad_well="A02"), exclude=True)
    fq.apply_field_quality_to_cache(on)
    assert "Field_Pass" in on.state.dataset.cache["A02"].columns


def test_the_flagged_field_is_the_one_that_is_dropped():
    """Excludes the column of zeros, and the column of ones.

    Asserts the values, not that they differ: a projector that flagged the
    whole well, or the wrong field, satisfies "some rows are 0".
    """
    ctx = _ctx(_block(bad_well="A02", bad_fov="2"))
    fq.apply_field_quality_to_cache(ctx)
    got = ctx.state.dataset.cache["A02"]
    assert list(got["Field_Pass"]) == [1, 0, 1]
    # Every other well is untouched by A02's defect.
    for w in WELLS:
        if w != "A02":
            assert list(ctx.state.dataset.cache[w]["Field_Pass"]) == [1, 1, 1]


def test_the_chokepoint_honours_the_column():
    from review.data.data_loading import df_included_mask

    df = _frame("A02")
    df["Included"] = 1
    df["Field_Pass"] = [1, 0, 1]
    assert list(df_included_mask(df)) == [True, False, True]
    # Absent reads as all-pass, like every other optional layer.
    assert list(df_included_mask(df.drop(columns=["Field_Pass"]))) == [True] * 3


# ── the three refusals ──────────────────────────────────────────────────────

def test_a_well_whose_fov_labels_are_untrusted_is_refused_not_joined():
    """Excludes the join that applies one field's verdict to a whole well.

    ``fov_integrity`` exists because a well whose filenames do not match the
    schema arrives with **every** row labelled field 1. Joining a per-field
    verdict onto that either spreads one field's verdict across all of them or
    matches nothing at all, and the second is indistinguishable from a well
    that was measured and found clean. The wrong implementation that passes
    "A02's rows were filtered" is the one that goes on to filter B01 too.
    """
    ctx = _ctx(_block(bad_well="B01"),
               integrity={"B01": FovIntegrity(trusted=False, n_labels=1,
                                              n_images=3,
                                              reason="3 images, 1 label")},
               cache={"A01": _frame("A01"),
                      "B01": _frame("B01", ("1", "1", "1"))})
    fq.apply_field_quality_to_cache(ctx)
    assert "Field_Pass" not in ctx.state.dataset.cache["B01"].columns, (
        "a per-field verdict was joined onto a well whose field labels are "
        "provably wrong")
    # The refusal is scoped to that well, not to the plate.
    assert "Field_Pass" in ctx.state.dataset.cache["A01"].columns


def test_a_partly_measured_plate_filters_nothing_at_all():
    """Excludes the filter derived from a biased subset.

    The acquisition metrics are robust z against the plate, so a block
    covering only part of it gives every field a z against a biased median.
    Filtering on that is worse than not filtering: the rows it drops are
    chosen by which wells happened to be re-processed.
    """
    ctx = _ctx(_block(bad_well="A02", partial=True))
    assert fq.apply_field_quality_to_cache(ctx) == []
    for df in ctx.state.dataset.cache.values():
        assert "Field_Pass" not in df.columns
    flagged, refusal = fq.flagged_fields(ctx)
    assert flagged == set(), "a refusal is not a finding"
    assert "only part of this plate" in refusal


def test_a_dataset_with_no_block_filters_nothing_and_says_which():
    """A plate nobody measured and a plate measured and found clean must not
    read alike."""
    ctx = _ctx(None)
    assert fq.apply_field_quality_to_cache(ctx) == []
    text, problem = fq.field_quality_status_text(ctx)
    assert "before field quality was recorded" in text
    assert problem is False

    clean, _ = fq.field_quality_status_text(_ctx(_block()))
    assert "look like the rest of the plate" in clean
    assert clean != text


# ── what the panel says ─────────────────────────────────────────────────────

def test_the_panel_names_what_was_flagged_and_how_many():
    """And a finding is not styled as a problem.

    The three registers exist so a reader can tell a refusal from a result at
    a glance. Returning ``True`` here put "1 of 18 field(s) flagged" in the
    same amber band as "this plate cannot be scored" — the exact conflation
    the registers are for, and visible the moment the panel was rendered.
    """
    ctx = _ctx(_block(bad_well="A02"))
    text, problem = fq.field_quality_status_text(ctx)
    assert "1 of 18 field(s) flagged" in text
    assert "focus" in text
    assert problem is False, "a finding is a result, not a failure"

    # The two states that ARE problems, for contrast — an assertion that only
    # says "False" is satisfied by a function that always returns False.
    _, partial = fq.field_quality_status_text(_ctx(_block(partial=True)))
    assert partial is True
    _, untrusted = fq.field_quality_status_text(_ctx(
        _block(), integrity={"B01": FovIntegrity(trusted=False, n_labels=1,
                                                 n_images=3, reason="x")}))
    assert untrusted is True


def test_an_untrusted_well_is_named_even_when_nothing_was_flagged():
    """Excludes the silent exemption.

    A well that cannot be filtered and a well that passed the filter look
    identical in every plot. The wrong implementation that passes the flagged
    case above is one that mentions untrusted wells only alongside a finding.
    """
    ctx = _ctx(_block(),                      # nothing flagged anywhere
               integrity={"B01": FovIntegrity(trusted=False, n_labels=1,
                                              n_images=3, reason="x")})
    text, problem = fq.field_quality_status_text(ctx)
    assert "look like the rest of the plate" in text
    assert "cannot be filtered" in text and "B01" in text
    assert problem is True


def test_the_partial_refusal_is_quoted_rather_than_paraphrased():
    """The panel and the scorer must say the same thing about the same block."""
    from pipeline.field_quality import records_for_scoring
    from review.domain.field_quality_scores import summarise

    block = _block(partial=True)
    text, problem = fq.field_quality_status_text(_ctx(block))
    assert problem is True
    assert text == summarise(records_for_scoring(block)).declined


# ── the plate map ───────────────────────────────────────────────────────────

def test_the_marker_is_not_a_ninth_colour():
    """The plate's colour vocabulary is closed under a written invariant — no
    state carries a colour another state also carries — and the two candidates
    a ninth would reach for are both spoken for. Field quality is orthogonal
    to coverage anyway: a well can be complete and out of focus."""
    from review.features.home import plate_panel as pp

    assert pp.FLAGGED_FIELD_GLYPH
    assert pp.FLAGGED_FIELD_GLYPH not in pp.STATE_COLORS
    colours = list(pp.STATE_COLORS.values())
    assert len(colours) == len(set(colours)), "two states share a colour"


def test_the_plate_marks_the_wells_that_hold_a_flagged_field():
    """Excludes the marker that is computed and never drawn (§7.4 #1): the
    assertion is on what reached the widget."""
    from review.features.home.plate_panel import (
        FLAGGED_FIELD_GLYPH, paint_field_quality,
    )

    marks: dict[str, str] = {}
    plate = SimpleNamespace(
        clearWellMarkers=lambda: marks.clear(),
        setWellMarker=lambda w, g: marks.__setitem__(w, g))
    ctx = _ctx(_block(bad_well="A02"))
    paint_field_quality(plate, fq.flagged_by_well(ctx))
    assert marks == {"A02": FLAGGED_FIELD_GLYPH}

    # A repaint with nothing flagged clears it, rather than leaving an
    # assertion about a well nobody measured.
    paint_field_quality(plate, fq.flagged_by_well(_ctx(_block())))
    assert marks == {}


def test_a_marked_well_explains_itself_in_its_tooltip():
    """A glyph with no explanation is a puzzle rather than a finding."""
    from review.features.home.plate_panel import well_tooltip

    ctx = _ctx(_block(bad_well="A02"))
    quality = fq.flagged_by_well(ctx)
    tip = well_tooltip("A02", {"A02": "complete"}, {}, quality)
    assert "1 field(s) flagged" in tip
    assert "focus" in tip
    # And a clean well says nothing extra.
    assert well_tooltip("A01", {"A01": "complete"}, {}, quality) == (
        "A01 · complete")


def test_a_plate_that_could_not_be_scored_carries_no_markers():
    """A refusal is not a finding, and a marker is a finding."""
    assert fq.flagged_by_well(_ctx(_block(bad_well="A02", partial=True))) == {}
    assert fq.flagged_by_well(_ctx(None)) == {}


# ── the sentence the run and the pre-flight strip share ─────────────────────

def test_the_preflight_quotes_the_pipelines_own_summary(tmp_path):
    """Excludes the second derivation.

    The Analyze tab's pre-flight strip and the end-of-run log describe the
    same block. Composed separately they drift, and the one a user reads
    before pressing Run is the one that matters. The wrong implementation that
    passes "a sentence appears" is a sentence of the pre-flight's own.
    """
    import json

    from analyze.preflight import _field_quality_line
    from pipeline.field_quality import summarize

    block = _block(partial=True)
    (tmp_path / "pipeline_info.json").write_text(
        json.dumps({"schema": "a:b", FIELD_QUALITY_BLOCK: block}))
    line = _field_quality_line(tmp_path)
    assert line == summarize(block)
    assert "FIELD QUALITY PARTIAL" in line
    assert "biased sample" in line


@pytest.mark.parametrize("payload", [
    None, {}, {"schema": "a:b"}, {"field_quality": {"wells": {}}},
])
def test_the_preflight_says_nothing_rather_than_guessing(tmp_path, payload):
    """A strip that reported its own inability to parse a sidecar would be
    noise in the one place the user is deciding whether to press Run."""
    import json

    from analyze.preflight import _field_quality_line

    if payload is not None:
        (tmp_path / "pipeline_info.json").write_text(json.dumps(payload))
    assert _field_quality_line(tmp_path) == ""


def test_a_whole_plate_summary_names_the_count_rather_than_a_verdict():
    """``summarize`` counts; it does not score. Scoring needs the whole plate
    and is the Review side's job — a pipeline-side sentence that claimed a
    finding would be a second scorer."""
    from pipeline.field_quality import summarize

    line = summarize(_block())
    assert "18 field(s) measured across 6 well(s)" in line
    assert "flag" not in line.lower()


# ── through the real panel, and the real control ────────────────────────────
#
# CLAUDE.md §7.6/§7.8 rule 3: one test per feature drives the entry point the
# app calls rather than the seam under it. Everything above may stay cheap
# because these two are not — a checkbox that is built and connected to
# nothing satisfies every assertion in this file so far.

def _panel_ctx(block, *, exclude=False, integrity=None):
    from review.core.qc_state import QcState
    from review.core.widget_registry import WidgetRegistry

    app = SimpleNamespace()
    app._app_state = SimpleNamespace(
        dataset=SimpleNamespace(
            data_dir=None,
            cache={w: _frame(w) for w in WELLS},
            fov_integrity=dict(integrity or {}),
            pipeline_info=({FIELD_QUALITY_BLOCK: block} if block else {})),
        qc=QcState(params=QCParams(exclude_flagged_fields=exclude)))
    app._ctx = SimpleNamespace(
        state=app._app_state, app=app, widgets=WidgetRegistry(app),
        runtime=SimpleNamespace(qc=SimpleNamespace(refresh_widgets_cb=None)))
    return app


def test_the_panel_offers_the_control_and_pressing_it_filters(qtbot, monkeypatch):
    """Excludes the checkbox that is built and wired to nothing.

    Asserting the control is *offered* is not asserting it *works*, and the
    gap between those two is where every callback bug in a Qt app lives. So
    this clicks the real box and asserts the row cache changed.
    """
    from PySide6.QtWidgets import QWidget

    from review.features.qc.tab_view import build_qc_gate_controls

    app = _panel_ctx(_block(bad_well="A02"))
    # ``set_qc_params`` redraws the whole app; here only the projection matters.
    import review.features.qc.tab_view as tv
    applied: list = []
    monkeypatch.setattr(tv, "set_qc_params", lambda ctx, p: (
        applied.append(p),
        setattr(ctx.state.qc, "params", p),
        fq.apply_field_quality_to_cache(ctx)))

    parent = QWidget()
    qtbot.addWidget(parent)
    row = build_qc_gate_controls(app._ctx, parent)
    qtbot.addWidget(row)

    w = app._qc_gate_widgets
    box = w["exclude_flagged_fields"]
    assert box.isChecked() is False, "off by default, on the real control"
    assert "1 of 18 field(s) flagged" in w["field_quality_status"].text()
    for df in app._app_state.dataset.cache.values():
        assert "Field_Pass" not in df.columns

    box.setChecked(True)
    assert applied and applied[-1].exclude_flagged_fields is True, (
        "pressing the box reached no handler")
    assert list(app._app_state.dataset.cache["A02"]["Field_Pass"]) == [1, 0, 1]


def test_the_panel_disables_the_control_when_there_is_nothing_to_apply(qtbot):
    """A control that would do nothing must not look live."""
    from PySide6.QtWidgets import QWidget

    from review.features.qc.tab_view import build_qc_gate_controls

    app = _panel_ctx(None)
    parent = QWidget()
    qtbot.addWidget(parent)
    qtbot.addWidget(build_qc_gate_controls(app._ctx, parent))

    w = app._qc_gate_widgets
    assert w["exclude_flagged_fields"].isEnabled() is False
    assert "before field quality was recorded" in w["field_quality_status"].text()


def test_a_reload_refills_the_control_from_the_params(qtbot):
    """The Gating reset scope and a dataset load both go through
    ``qc_gate_refresh_widgets``; a control it does not know about keeps the
    previous dataset's value."""
    from PySide6.QtWidgets import QWidget

    from review.features.qc.tab_view import (
        build_qc_gate_controls, qc_gate_refresh_widgets,
    )

    app = _panel_ctx(_block(bad_well="A02"), exclude=True)
    parent = QWidget()
    qtbot.addWidget(parent)
    qtbot.addWidget(build_qc_gate_controls(app._ctx, parent))
    w = app._qc_gate_widgets
    assert w["exclude_flagged_fields"].isChecked() is True

    app._app_state.qc.params = QCParams(exclude_flagged_fields=False)
    qc_gate_refresh_widgets(app)
    assert w["exclude_flagged_fields"].isChecked() is False


def test_a_load_refreshes_the_sentence_not_just_the_controls(qtbot):
    """Excludes the panel that keeps the previous dataset's answer.

    The sentence is a fact about the **dataset**, not about the params, so it
    is the one thing on this panel that a load changes without any control
    moving. ``qc_gate_refresh_widgets`` refilled the widget *values* and left
    it alone — caught by rendering the panel with a block injected and reading
    "This dataset was analysed before field quality was recorded" over a plate
    whose block was right there.

    The wrong implementation that passes every other test in this file is
    exactly that one, because every other test reads the sentence on a panel
    that was built with its block already in place.
    """
    from PySide6.QtWidgets import QWidget

    from review.features.qc.tab_view import (
        build_qc_gate_controls, qc_gate_refresh_widgets,
    )

    app = _panel_ctx(None)                      # built with no block at all
    parent = QWidget()
    qtbot.addWidget(parent)
    qtbot.addWidget(build_qc_gate_controls(app._ctx, parent))
    w = app._qc_gate_widgets
    assert "before field quality was recorded" in w["field_quality_status"].text()
    assert w["exclude_flagged_fields"].isEnabled() is False

    # A dataset load puts a block in place and re-fills the panel.
    app._app_state.dataset.pipeline_info[FIELD_QUALITY_BLOCK] = _block(
        bad_well="A02")
    qc_gate_refresh_widgets(app)
    assert "1 of 18 field(s) flagged" in w["field_quality_status"].text()
    assert w["exclude_flagged_fields"].isEnabled() is True


def test_the_toggle_survives_a_save_and_load(tmp_path):
    """It rides the QC params' existing persistence, so a plate reopened
    filters the way it was left."""
    saved = QCParams(enabled=True, min_area=20.0,
                     exclude_flagged_fields=True).to_dict()
    assert saved["exclude_flagged_fields"] is True
    assert QCParams.from_dict(saved).exclude_flagged_fields is True
    # And a params dict written before this field existed reads as off.
    old = {"enabled": True, "min_area": 20.0}
    assert QCParams.from_dict(old).exclude_flagged_fields is False


# ── A24: the refusal that must not become a measurement ─────────────────────

def test_a_metric_with_no_spread_reports_nothing_rather_than_zero():
    """Excludes ``MAD == 0 → z = 0.0``.

    Zero is a measurement; undefined is not (CLAUDE.md §7 #3). With every
    field's value identical the MAD is zero and the robust z is undefined —
    returning ``0.0`` would say every field sits exactly at the median, which
    is a claim, and one that can never flag anything.
    """
    from review.domain.field_quality_scores import robust_z

    zs, why = robust_z([5.0] * 12)
    assert all(z is None for z in zs)
    assert why

    # And a metric that does vary is scored, or the assertion above is
    # satisfied by a function that always returns None.
    zs, _ = robust_z([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 40.0])
    assert zs[-1] is not None and abs(zs[-1]) > 3


def test_the_marker_gets_a_legend_row_when_the_plate_carries_one():
    """Excludes the glyph nobody can look up.

    The plate's own invariant is that no well is left without a row to explain
    it — that is why every state keeps its legend entry even at a count of
    zero. A marker with no entry breaks it exactly as two states sharing a
    colour would, and the render is where it showed: an ``!`` on A02 and eight
    legend rows, none of them about markers.

    Asserted on what reached the layout, not on the branch being present.
    """
    from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

    from review.features.home import controller as hcon
    from review.features.home.plate_panel import FLAGGED_FIELD_GLYPH

    def _legend_texts(block):
        host = QWidget()
        QGridLayout(host)
        ctx = _ctx(block)
        plate = SimpleNamespace(
            setEnabledWells=lambda *_a: None,
            clearWellColors=lambda: None, clearWellFractions=lambda: None,
            clearWellMarkers=lambda: None,
            setWellState=lambda *_a: None, setWellFraction=lambda *_a: None,
            setWellMarker=lambda *_a: None,
            setWellTooltipProvider=lambda *_a: None)
        widgets = {"_home_plate": plate, "_home_legend": host,
                   "_home_plate_summary": QLabel()}
        orig_widget, orig_clear = hcon._widget, hcon._clear
        try:
            hcon._widget = lambda _c, name: widgets.get(name)
            hcon._clear = lambda _h: None
            hcon._paint_plate(ctx)
        finally:
            hcon._widget, hcon._clear = orig_widget, orig_clear
        return [c.text() for c in host.findChildren(QLabel)]

    with_flag = _legend_texts(_block(bad_well="A02"))
    assert FLAGGED_FIELD_GLYPH in with_flag
    assert "Field flagged on image quality" in with_flag

    # And no row when there is nothing to explain — a legend entry for a mark
    # the plate does not carry is its own kind of noise.
    clean = _legend_texts(_block())
    assert "Field flagged on image quality" not in clean
