"""``FoldChangeState`` — the object that replaced the fold-change 3-tuple.

It briefly emulated that tuple (iterating, indexing, comparing equal to one) so
the widening could land without touching call sites. The emulation is gone, and
these tests pin why it must not come back:

* it broke ``a == b implies hash(a) == hash(b)``, so a state compared equal to
  the tuple while hashing differently and ``fc in {tuple}`` was False;
* it let a call site unpack three values and silently drop anything added
  later — and for this object, dropping a field means drawing raw values under
  a fold-change axis label.

``coerce_fc_state`` still accepts a tuple as *input*, because a lot of tests
spell a request inline. Nothing hands one back out.
"""
from __future__ import annotations

import pytest

from well_viewer.domain.fold_change_state import (
    FC_STATE_OFF,
    FoldChangeState,
    coerce_fc_state,
)


# ── the hash/equality contract ───────────────────────────────────────────────

def test_equal_states_hash_equal():
    a = FoldChangeState(vs_control_on=True, control_label="DMSO", vs_t0_on=True)
    b = FoldChangeState(vs_control_on=True, control_label="DMSO", vs_t0_on=True)
    assert a == b
    assert hash(a) == hash(b)


def test_usable_as_a_set_member_and_dict_key():
    """The property the tuple emulation broke: ``fc in {fc}`` was False."""
    a = FoldChangeState(vs_control_on=True, control_label="DMSO")
    b = FoldChangeState(vs_control_on=True, control_label="DMSO")
    assert a in {b}
    assert {a: "x"}[b] == "x"
    assert len({a, b}) == 1


def test_differing_states_are_distinct():
    a = FoldChangeState(vs_control_on=True, control_label="DMSO")
    b = FoldChangeState(vs_control_on=True, control_label="Vehicle")
    assert a != b
    assert len({a, b}) == 2


def test_it_is_not_equal_to_a_tuple():
    """Cross-type equality is what made the hash contract unsatisfiable."""
    assert FC_STATE_OFF != (False, "", False)


def test_it_does_not_unpack():
    """A three-value unpack is how a new field gets dropped in silence."""
    with pytest.raises(TypeError):
        _a, _b, _c = FoldChangeState()


def test_it_does_not_index():
    with pytest.raises(TypeError):
        FC_STATE_OFF[0]


def test_is_frozen():
    with pytest.raises(Exception):
        FC_STATE_OFF.vs_control_on = True


# ── derived properties ───────────────────────────────────────────────────────

@pytest.mark.parametrize("vs_ctrl,vs_t0,expected", [
    (False, False, False),
    (True, False, True),
    (False, True, True),
    (True, True, True),
])
def test_active(vs_ctrl, vs_t0, expected):
    assert FoldChangeState(vs_control_on=vs_ctrl, vs_t0_on=vs_t0).active is expected


def test_fc_state_off_is_off():
    assert FC_STATE_OFF.active is False
    assert FC_STATE_OFF.wants_control is False


@pytest.mark.parametrize("vs_ctrl,label,expected", [
    (True, "DMSO", True),
    (True, "", False),      # axis on, nothing named — nothing to resolve
    (False, "DMSO", False),  # a remembered label with the axis off
    (False, "", False),
])
def test_wants_control(vs_ctrl, label, expected):
    fc = FoldChangeState(vs_control_on=vs_ctrl, control_label=label)
    assert fc.wants_control is expected


# ── coercion ─────────────────────────────────────────────────────────────────

def test_coerce_passes_an_instance_through_unchanged():
    fc = FoldChangeState(vs_control_on=True, control_label="DMSO")
    assert coerce_fc_state(fc) is fc


def test_coerce_none_gives_the_raw_default():
    assert coerce_fc_state(None) == FC_STATE_OFF


def test_coerce_a_legacy_tuple():
    fc = coerce_fc_state((True, "DMSO", True))
    assert isinstance(fc, FoldChangeState)
    assert fc.vs_control_on is True
    assert fc.control_label == "DMSO"
    assert fc.vs_t0_on is True


def test_coerce_a_legacy_tuple_normalises_a_none_label():
    assert coerce_fc_state((True, None, False)).control_label == ""


def test_coerce_rejects_nonsense():
    with pytest.raises(TypeError):
        coerce_fc_state("DMSO")


def test_coerce_rejects_a_wrong_length_tuple():
    """Better a loud unpack error than a silently mis-bound field."""
    with pytest.raises(ValueError):
        coerce_fc_state((True, "DMSO"))
