"""The facts the Variables tab reports, decided where a test can drive them.

Coverage, what a selection currently holds, which wells hold a value, why an
edit was refused, and what mark a second variable wears — five questions the
tab used to answer inside signal handlers, or not at all. Each of them is now a
pure function over a ``VariableSet``, which is what lets this file exist in the
fast lane with no window.
"""

from __future__ import annotations

import pytest

from review.domain import sample_variables as sv
from review.domain import variable_series as vseries


def _dose():
    """A six-value numeric series — the case the glyph rule was built for."""
    vset, vid = sv.new_variable(sv.VariableSet(), "Dose")
    for label in ("0", "0.1", "0.3", "1", "3", "10"):
        vset = sv.add_value(vset, vid, label)
    return sv.set_kind(vset, vid, sv.NUMBER, "µM"), vid


def _lines():
    vset, vid = sv.new_variable(sv.VariableSet(), "Cell line")
    for label in ("HeLa", "U2OS"):
        vset = sv.add_value(vset, vid, label)
    return vset, vid


def _wells(rows="AB", cols=(1, 2, 3, 4)):
    return [f"{r}{c:02d}" for r in rows for c in cols]


# ── coverage ────────────────────────────────────────────────────────────────

def test_coverage_counts_annotated_against_the_dataset_not_the_map():
    """``48/48`` is finished and ``48/96`` is not, so the denominator is the
    dataset's wells rather than however many assignments happen to exist."""
    vset, vid = _dose()
    first = vset.by_id(vid).values[0].id
    vset = sv.assign(vset, vid, ["A01", "A02", "A03"], first)
    assert sv.coverage(vset, vid, _wells()) == (3, 8)


def test_an_assignment_for_a_well_the_dataset_lost_is_not_coverage():
    """The fixture is built so the two answers differ.

    A dataset swap can leave assignments naming wells the new plate does not
    hold. Counting the map would call this variable finished on a plate where
    nothing at all is annotated — and a count taken over the intersection is
    the only reading that cannot.
    """
    vset, vid = _dose()
    first = vset.by_id(vid).values[0].id
    vset = sv.assign(vset, vid, ["Z99", "Z98"], first)
    assert sv.coverage(vset, vid, _wells()) == (0, 8)


def test_a_variable_nobody_has_filled_reads_zero_of_the_plate():
    vset, vid = _dose()
    assert sv.coverage(vset, vid, _wells()) == (0, 8)


# ── what the selection holds ────────────────────────────────────────────────

def test_the_summary_lists_values_in_the_variables_order_not_by_count():
    """Ordered by the user's list, so the sentence matches the value list.

    The fixture assigns *more* wells to the later dose than the earlier one, so
    a rule that sorted by count would produce the opposite sequence and this
    test would catch it. Ordering by frequency is the plausible wrong
    implementation, and it is the one being excluded here.
    """
    vset, vid = _dose()
    values = vset.by_id(vid).values
    vset = sv.assign(vset, vid, ["A01"], values[0].id)
    vset = sv.assign(vset, vid, ["A02", "A03", "A04"], values[3].id)
    assert sv.selection_summary(vset, vid, ["A01", "A02", "A03", "A04"]) == [
        (values[0].id, 1), (values[3].id, 3)]


def test_unassigned_wells_come_last_under_none():
    vset, vid = _dose()
    first = vset.by_id(vid).values[0].id
    vset = sv.assign(vset, vid, ["A01"], first)
    assert sv.selection_summary(vset, vid, ["A01", "A02"]) == [(first, 1),
                                                               (None, 1)]


def test_an_empty_selection_summarises_to_nothing():
    vset, vid = _dose()
    assert sv.selection_summary(vset, vid, []) == []


# ── which wells hold a value ────────────────────────────────────────────────

def test_wells_with_value_names_exactly_the_wells_holding_it():
    vset, vid = _dose()
    values = vset.by_id(vid).values
    vset = sv.assign(vset, vid, ["A01", "B03"], values[2].id)
    vset = sv.assign(vset, vid, ["A02"], values[4].id)
    assert sv.wells_with_value(vset, vid, values[2].id, _wells()) == ["A01",
                                                                      "B03"]


def test_wells_with_no_value_are_asked_for_as_none():
    vset, vid = _dose()
    first = vset.by_id(vid).values[0].id
    vset = sv.assign(vset, vid, _wells()[1:], first)
    assert sv.wells_with_value(vset, vid, None, _wells()) == ["A01"]


def test_a_well_outside_the_dataset_is_never_returned():
    vset, vid = _dose()
    first = vset.by_id(vid).values[0].id
    vset = sv.assign(vset, vid, ["A01", "Z99"], first)
    assert sv.wells_with_value(vset, vid, first, _wells()) == ["A01"]


# ── why an edit did nothing ─────────────────────────────────────────────────

def test_a_duplicate_label_has_a_reason_naming_both_the_variable_and_the_value():
    vset, vid = _dose()
    reason = sv.refusal_for_value(vset.by_id(vid), "0.3")
    assert reason is not None
    assert "Dose" in reason and "0.3" in reason


def test_the_duplicate_check_ignores_case_and_surrounding_space():
    vset, vid = _lines()
    assert sv.refusal_for_value(vset.by_id(vid), "  hela  ") is not None


def test_a_rename_may_keep_its_own_label():
    """Excluding the row being renamed, or committing an unchanged edit would
    report a collision with itself."""
    vset, vid = _lines()
    variable = vset.by_id(vid)
    same = variable.values[0]
    assert sv.refusal_for_value(variable, same.label, exclude=same.id) is None


def test_a_fresh_label_is_not_refused():
    vset, vid = _dose()
    assert sv.refusal_for_value(vset.by_id(vid), "30") is None


def test_an_empty_label_says_what_to_do():
    vset, vid = _dose()
    assert sv.refusal_for_value(vset.by_id(vid), "   ") == "Type a value first."


def test_the_reason_matches_what_the_mutator_actually_does():
    """The refusal and the refusal it describes are the same event.

    A predicate that drifted from ``add_value`` would put a sentence on screen
    about an edit that succeeded, or stay silent about one that did not — so
    every label is checked both ways round.
    """
    vset, vid = _dose()
    for label in ("0.3", "  0.3 ", "30", "", "0"):
        refused = sv.refusal_for_value(vset.by_id(vid), label) is not None
        unchanged = sv.add_value(vset, vid, label) == vset
        assert refused == unchanged, label


# ── the second variable's mark ──────────────────────────────────────────────

def test_two_cell_lines_keep_their_initials():
    vset, vid = _lines()
    glyphs = vseries.overlay_glyphs(vset.by_id(vid))
    assert list(glyphs.values()) == ["H", "U"]


def test_a_dose_series_is_numbered_rather_than_part_truncated():
    """The defect, pinned as the value rather than as a property.

    ``0, 0.1, 0.3, 1, 3, 10`` used to render ``0, 02, 03, 1, 3, 10``, mixing
    glyphs that mean themselves with glyphs that are codes and marking neither.
    "The glyphs are distinct" was true of that too, so it is the sequence that
    is asserted here.
    """
    vset, vid = _dose()
    glyphs = vseries.overlay_glyphs(vset.by_id(vid))
    assert list(glyphs.values()) == ["1", "2", "3", "4", "5", "6"]


def test_one_colliding_initial_numbers_the_whole_variable():
    """All or nothing: a mixed scheme is the thing being removed.

    ``HeLa`` and ``HEK293`` collide, so *every* value is numbered — including
    ``U2OS``, whose initial was unique. Leaving that one as ``U`` beside ``1``
    and ``2`` is the mixed scheme under a different name.
    """
    vset, vid = sv.new_variable(sv.VariableSet(), "Line")
    for label in ("HeLa", "HEK293", "U2OS"):
        vset = sv.add_value(vset, vid, label)
    assert list(vseries.overlay_glyphs(vset.by_id(vid)).values()) == ["1", "2",
                                                                      "3"]


def test_the_key_spells_out_every_mark_it_draws():
    vset, vid = _dose()
    key = vseries.glyph_key(vset.by_id(vid))
    assert key.startswith("Dose: ")
    for i, label in enumerate(("0", "0.1", "0.3", "1", "3", "10")):
        assert f"{i + 1} = {label}" in key


def test_a_variable_with_no_values_wears_no_marks_and_has_no_key():
    vset, vid = sv.new_variable(sv.VariableSet(), "Empty")
    assert vseries.overlay_glyphs(vset.by_id(vid)) == {}
    assert vseries.glyph_key(vset.by_id(vid)) == ""


@pytest.mark.parametrize("variable", [None])
def test_no_variable_is_not_a_crash(variable):
    assert vseries.overlay_glyphs(variable) == {}
    assert vseries.glyph_key(variable) == ""
