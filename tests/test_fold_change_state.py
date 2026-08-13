"""``FoldChangeState`` — the object that replaced the fold-change 3-tuple.

The compatibility surface tested here is deliberate, not incidental. Roughly
ten call sites unpack this object as ``a, b, c = fc_state``, the batch panels
compare it against ``(False, "", False)`` literals, and ``barplot/renderer``
indexes it positionally. Those behaviours are what let per-sample controls
widen the request object without rewriting every assertion in the suite, so
each of them is pinned here — if one breaks, the breakage is silent at the
call site and shows up as a wrong plot.
"""
from __future__ import annotations

import pytest

from well_viewer.domain.fold_change_state import (
    FC_STATE_OFF,
    FoldChangeState,
    coerce_fc_state,
)


# ── legacy tuple compatibility ───────────────────────────────────────────────

def test_unpacks_as_the_legacy_three_tuple():
    fc = FoldChangeState(vs_control_on=True, control_label="DMSO", vs_t0_on=True)
    vs_ctrl, label, vs_t0 = fc
    assert (vs_ctrl, label, vs_t0) == (True, "DMSO", True)


def test_unpacking_ignores_the_new_fields():
    """Three-value unpack must still work when the new fields are set.

    This is the sharp edge the guard in test_guards_bite.py exists for: a
    collector that unpacks three values drops ``per_member`` silently.
    """
    fc = FoldChangeState(vs_control_on=True, per_member=True, controls=object())
    vs_ctrl, label, vs_t0 = fc
    assert (vs_ctrl, label, vs_t0) == (True, "", False)


def test_compares_equal_to_the_legacy_off_tuple():
    assert FC_STATE_OFF == (False, "", False)
    assert FoldChangeState(vs_control_on=True, control_label="A01", vs_t0_on=False) \
        == (True, "A01", False)


def test_not_equal_to_a_different_tuple():
    assert FC_STATE_OFF != (True, "", False)


def test_indexes_positionally():
    """``barplot/renderer`` reads ``_fc_state[0]`` and ``[2]``."""
    fc = FoldChangeState(vs_control_on=True, control_label="X", vs_t0_on=True)
    assert fc[0] is True
    assert fc[1] == "X"
    assert fc[2] is True


def test_instance_equality_includes_the_new_fields():
    a = FoldChangeState(vs_control_on=True, per_member=True)
    b = FoldChangeState(vs_control_on=True, per_member=False)
    assert a != b


def test_is_frozen():
    with pytest.raises(Exception):
        FC_STATE_OFF.vs_control_on = True


def test_fc_state_off_is_off():
    assert FC_STATE_OFF.active is False
    assert FC_STATE_OFF.needs_controls is False


# ── derived properties ───────────────────────────────────────────────────────

@pytest.mark.parametrize("vs_ctrl,vs_t0,expected", [
    (False, False, False),
    (True, False, True),
    (False, True, True),
    (True, True, True),
])
def test_active(vs_ctrl, vs_t0, expected):
    assert FoldChangeState(vs_control_on=vs_ctrl, vs_t0_on=vs_t0).active is expected


def test_needs_controls_is_the_untaught_caller_signal():
    """per-member requested, no mapping resolved => the caller must placeholder."""
    assert FoldChangeState(vs_control_on=True, per_member=True).needs_controls is True


def test_needs_controls_false_once_a_mapping_is_attached():
    fc = FoldChangeState(vs_control_on=True, per_member=True).with_controls(object())
    assert fc.needs_controls is False


def test_needs_controls_false_in_single_control_mode():
    assert FoldChangeState(vs_control_on=True, control_label="DMSO").needs_controls is False


def test_needs_controls_false_when_the_control_axis_is_off():
    """vs-t0-only with per_member set is not a missing denominator."""
    assert FoldChangeState(vs_t0_on=True, per_member=True).needs_controls is False


def test_with_controls_leaves_the_original_alone():
    base = FoldChangeState(vs_control_on=True, per_member=True)
    derived = base.with_controls(object())
    assert base.controls is None
    assert derived.controls is not None


# ── coercion ─────────────────────────────────────────────────────────────────

def test_coerce_passes_an_instance_through_unchanged():
    fc = FoldChangeState(vs_control_on=True, per_member=True)
    assert coerce_fc_state(fc) is fc


def test_coerce_none_gives_the_raw_default():
    assert coerce_fc_state(None) == FC_STATE_OFF


def test_coerce_a_legacy_tuple():
    fc = coerce_fc_state((True, "DMSO", True))
    assert isinstance(fc, FoldChangeState)
    assert (fc.vs_control_on, fc.control_label, fc.vs_t0_on) == (True, "DMSO", True)
    assert fc.per_member is False
    assert fc.controls is None


def test_coerce_a_legacy_tuple_normalises_a_none_label():
    assert coerce_fc_state((True, None, False)).control_label == ""


def test_coerce_rejects_nonsense():
    with pytest.raises(TypeError):
        coerce_fc_state("DMSO")
