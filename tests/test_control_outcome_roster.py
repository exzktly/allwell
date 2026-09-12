"""Every outcome is produced by something and consumed by everything.

``ControlOutcome`` is a closed set for a reason: without it, "nobody assigned a
control", "the control is gone" and "the control has no data at this timepoint"
all produce the same empty bar and the same empty ``fold_change_mean`` cell.
That only holds while every value is reachable and every consumer handles all
of them. Both halves rot quietly:

* a value nothing produces is a branch that exists and cannot be taken, which
  reads as coverage and is not;
* a consumer that handles four of five shows a user the enum's own spelling —
  or, for the CSV, drops the one case that most needed explaining.

So the roster is asserted from both ends. It is a module-level constant here
rather than a set comprehension over the enum on purpose: adding a value has to
be a deliberate edit in this file too, which is where the question "who
produces it, and who reads it?" gets asked.
"""
from __future__ import annotations

import math

import pytest

from review.core.fold_change_pairing_view import status_word
from review.domain.control_ref import ControlRef
from review.domain.fold_change_pairing import (
    ControlOutcome,
    shortfall_from_items,
)
from review.features.barplot.collect import collect_bar_items
from review.features.export.service import attach_bar_fold_change_columns

from tests.conftest import MockApp, make_agg


#: The full roster, spelled out.
ROSTER = (
    ControlOutcome.OK,
    ControlOutcome.SELF,
    ControlOutcome.UNMAPPED,
    ControlOutcome.CONTROL_UNRESOLVED,
    ControlOutcome.CONTROL_NO_SAMPLE,
)

#: Outcomes that mean the member could not be normalized. The status line and
#: the placeholder rendering key off exactly these.
SHORTFALL = (ControlOutcome.UNMAPPED, ControlOutcome.CONTROL_UNRESOLVED,
             ControlOutcome.CONTROL_NO_SAMPLE)

_SET = ControlRef.for_replicate_set


def test_the_roster_is_the_whole_enum():
    assert set(ROSTER) == set(ControlOutcome)


@pytest.fixture
def ctx():
    app = MockApp(
        per_well_pts={w: [make_agg(0.0, v)] for w, v in
                      (("A01", 100.0), ("B01", 200.0), ("C01", 50.0),
                       ("D01", 150.0))},
        selections=[{"name": "CTRL", "wells": ["A01"], "hidden": False},
                    {"name": "DRUG", "wells": ["B01"], "hidden": False},
                    {"name": "OTHER", "wells": ["C01"], "hidden": False},
                    {"name": "FOURTH", "wells": ["D01"], "hidden": False}],
    )
    c = app._ctx
    c.state.plot.fc_vs_control_on = True
    c.state.plot.fc_control_mode = "per_member"
    return c


# ── every value is produced ──────────────────────────────────────────────────

def _outcomes_from_a_real_collection(ctx) -> set:
    """Outcomes reachable by collecting a plot, not by constructing records."""
    seen: set = set()
    # OK, SELF, UNMAPPED and CONTROL_UNRESOLVED at a timepoint with data —
    # one member left out on purpose, since that is the whole of UNMAPPED.
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL"),
                                     "CTRL": _SET("CTRL"),
                                     "OTHER": _SET("DELETED")}
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    seen.update(i.fc_outcome for i in items)
    # CONTROL_NO_SAMPLE needs a timepoint the control does not reach.
    _ug, items, _b = collect_bar_items(ctx, 99.0)
    seen.update(i.fc_outcome for i in items)
    return seen


def test_every_outcome_is_reachable_from_a_collector(ctx):
    produced = _outcomes_from_a_real_collection(ctx)
    missing = [o for o in ROSTER if o not in produced]
    assert not missing, (
        "these outcomes are declared and unreachable — a branch that exists "
        f"and cannot be taken reads as coverage and is not: {missing}"
    )


def test_a_shortfall_outcome_always_means_a_placeholder(ctx):
    """The rendering contract: never a raw value under a fold-change axis."""
    ctx.state.plot.fc_control_map = {"DRUG": _SET("CTRL"),
                                     "OTHER": _SET("DELETED")}
    _ug, items, _b = collect_bar_items(ctx, 0.0)
    for item in items:
        if item.fc_outcome in SHORTFALL:
            assert math.isnan(item.mean), f"{item.key} drew {item.mean}"
            assert item.has_mean is False


# ── every value is consumed ──────────────────────────────────────────────────

def test_the_editors_status_column_has_a_word_for_each():
    """A hole here shows a user the enum's own spelling."""
    for outcome in ROSTER:
        word = status_word(outcome)
        assert word and word != outcome.value, outcome


def test_the_status_line_names_every_shortfall():
    """A hole here silently drops one reason a plot came out empty."""
    class _Item:
        def __init__(self, outcome):
            self.fc_outcome = outcome

    for outcome in SHORTFALL:
        text = shortfall_from_items([_Item(outcome)])
        assert text, outcome
        assert text != "Fold change:  — drawn blank.", outcome


def test_the_status_line_stays_quiet_for_the_resolved_ones():
    class _Item:
        def __init__(self, outcome):
            self.fc_outcome = outcome

    for outcome in (ControlOutcome.OK, ControlOutcome.SELF):
        assert shortfall_from_items([_Item(outcome)]) == "", outcome


def test_the_csv_writes_every_outcome_as_its_bare_value():
    for outcome in ROSTER:
        row: dict = {}
        attach_bar_fold_change_columns(
            row, fc_mean=1.0, fc_spread=0.0, fc_has=True, band_lbl="SEM",
            vs_control=True, vs_t0=False, control_label="X", outcome=outcome)
        assert row["fold_change_outcome"] == outcome.value
