"""A control survives a save/load round trip, and says what it means.

The control used to be persisted as a plain string, which cannot record
whether ``A01`` is the well or a replicate set someone named ``A01``. It is now
an object beside a plain-token copy: the object is authoritative, and the copy
is there so a rolled-back build finds a value it understands instead of
nothing.

A document written before the object existed still loads. Its bare string is
read as a *legacy* reference and resolved by the precedence it was saved under,
which is the only honest reading of a value whose kind was never recorded — and
the next save writes the concrete form, so the ambiguity is retired once per
document rather than living forever.
"""
from __future__ import annotations

from well_viewer.core.plot_state import PlotState
from well_viewer.domain.control_ref import (
    KIND_LEGACY,
    KIND_SET,
    KIND_WELL,
    ControlRef,
)


# ── the reference's own storage form ─────────────────────────────────────────

def test_a_well_reference_round_trips():
    ref = ControlRef.for_well("A01")
    assert ControlRef.from_storage(ref.to_storage()) == ref


def test_a_set_reference_round_trips():
    ref = ControlRef.for_replicate_set("DMSO")
    assert ControlRef.from_storage(ref.to_storage()) == ref


def test_a_set_named_like_a_suffixed_well_round_trips():
    """The case no in-band encoding can carry."""
    ref = ControlRef.for_replicate_set("A01 (well)")
    got = ControlRef.from_storage(ref.to_storage())
    assert got == ref
    assert got.kind == KIND_SET
    assert got.name == "A01 (well)"


def test_a_legacy_bare_string_reads_as_legacy():
    got = ControlRef.from_storage("DMSO")
    assert got == ControlRef(KIND_LEGACY, "DMSO")


def test_a_legacy_suffixed_string_reads_as_the_well():
    """The suffix was the only kind hint a legacy value had, so it is honoured
    on the way in — and then never written again."""
    assert ControlRef.from_storage("A01 (well)") == ControlRef(KIND_WELL, "A01")


def test_an_unknown_kind_reads_as_legacy_rather_than_being_dropped():
    """A name is still useful even when the kind is unreadable."""
    got = ControlRef.from_storage({"kind": "nonsense", "name": "DMSO"})
    assert got == ControlRef(KIND_LEGACY, "DMSO")


def test_absent_and_empty_read_as_no_control():
    assert ControlRef.from_storage(None) is None
    assert ControlRef.from_storage("") is None
    assert ControlRef.from_storage({"kind": "well", "name": ""}) is None


def test_there_is_no_empty_reference():
    """"No control" has one spelling — ``None`` — so nothing has to decide
    whether an empty name counts as set."""
    for factory in (ControlRef.for_well, ControlRef.for_replicate_set,
                    ControlRef.legacy):
        assert factory("") is None
        assert factory(None) is None


# ── the state field ──────────────────────────────────────────────────────────

def test_set_fold_change_accepts_a_reference():
    ps = PlotState()
    ps.set_fold_change(vs_control_on=True, control=ControlRef.for_well("A01"))
    assert ps.fc_control == ControlRef.for_well("A01")


def test_set_fold_change_reads_a_bare_string_as_legacy():
    ps = PlotState()
    ps.set_fold_change(vs_control_on=True, control="DMSO")
    assert ps.fc_control == ControlRef.legacy("DMSO")


def test_clearing_is_explicit():
    """``None`` means "leave it alone" for every other field, so clearing
    cannot also be ``None`` — it is its own flag."""
    ps = PlotState(fc_control=ControlRef.for_well("A01"))
    ps.set_fold_change(vs_t0_on=True)
    assert ps.fc_control == ControlRef.for_well("A01"), "unrelated edit cleared it"
    ps.set_fold_change(vs_control_on=False, clear_control=True)
    assert ps.fc_control is None


def test_the_default_is_no_control():
    assert PlotState().fc_control is None


# ── what reaches a figure or a CSV ───────────────────────────────────────────

def test_display_name_never_carries_the_suffix():
    """It is combo chrome. While it was part of the stored value it leaked into
    axis titles, the fold_change_control CSV column and saved-plot summaries."""
    ref = ControlRef.for_well("A01")
    assert ref.display_name == "A01"
    assert ref.combo_text(disambiguate=True) == "A01 (well)"
    assert ref.combo_text(disambiguate=False) == "A01"


def test_a_set_is_never_given_a_well_suffix():
    ref = ControlRef.for_replicate_set("A01")
    assert ref.combo_text(disambiguate=True) == "A01"
