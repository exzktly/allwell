"""The Job Queue's per-kind tables and the names it derives from a spec.

Qt-free — everything here is plain data and string building, which is why it
lives in ``services/`` rather than beside the page that renders it.
"""

from __future__ import annotations

import pytest

from services.job_kinds import (
    CHAIN_TARGET,
    KIND_LABEL,
    PRODUCED_KEY,
    describe,
    describe_detail,
)


# ── table coverage ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("table,name", [(PRODUCED_KEY, "PRODUCED_KEY"),
                                        (CHAIN_TARGET, "CHAIN_TARGET")])
def test_every_kind_is_covered(table, name):
    """A kind missing from either table is a job that cannot be chained.

    Both ends of a chain are looked up by kind — the predecessor's
    ``PRODUCED_KEY`` and the successor's ``CHAIN_TARGET`` — so a gap in either
    is a link that silently cannot be made rather than one that fails loudly.
    """
    missing = sorted(set(KIND_LABEL) - set(table))
    assert not missing, f"{name} has no entry for: {missing}"


def test_produced_keys_are_ones_materialise_can_resolve():
    """The three names here are the only outputs a JobSpec records."""
    from services.job_queue import _CHAIN_KINDS

    assert set(PRODUCED_KEY.values()) <= set(_CHAIN_KINDS)


# ── describe ──────────────────────────────────────────────────────────────────

def test_describe_names_input_and_output_by_basename():
    spec = {"source": "/data/plates/plateA", "destination": "/out/plateA_sub"}
    assert describe("subset", spec) == "Subset — plateA → plateA_sub"


def test_describe_joins_a_two_dataset_tool():
    spec = {"dataset1": "/d/runA", "dataset2": "/d/runB", "destination": "/d/runAB"}
    assert describe("concatenate", spec) == "Concatenate — runA + runB → runAB"


def test_describe_omits_the_arrow_for_a_tool_with_no_output():
    assert describe("validate", {"dataset": "/data/plate7"}) == "Validate — plate7"


def test_describe_uses_the_run_spec_label_for_analyze():
    """Analyze already had a naming method; this is its first real caller."""
    assert describe("analyze", {"opts": {"raw": "/data/plate7"}}) == "Analyze — plate7"


@pytest.mark.parametrize("kind,spec", [
    ("subset", {}),
    ("subset", {"source": "   "}),
    ("concatenate", {"destination": "/d/out"}),   # output but no inputs
    ("analyze", {"opts": "not-a-dict"}),
])
def test_describe_falls_back_to_the_kind_label(kind, spec):
    """An incomplete or malformed spec still has to render as a row.

    A job whose spec names nothing is a job the user needs to see in order to
    fix or remove it, so this degrades to the plain tool name rather than
    raising on the queue page.
    """
    assert describe(kind, spec) == KIND_LABEL[kind]


def test_analyze_with_no_input_says_so_rather_than_going_generic():
    """``RunSpec.label`` names the gap, and that is worth preserving.

    A row reading plain "Analyze" is indistinguishable from a well-formed job;
    one reading "Analyze — (no input)" tells the user which of their queued
    runs is the one missing a folder.
    """
    assert describe("analyze", {}) == "Analyze — (no input)"
    assert describe("analyze", {"opts": {}}) == "Analyze — (no input)"


def test_describe_handles_an_unknown_kind():
    """A kind from a newer version's persisted file must not crash the page."""
    assert describe("teleporter", {"source": "/d/x"}) == "teleporter"


# ── describe_detail ───────────────────────────────────────────────────────────

def test_detail_keeps_the_full_paths():
    spec = {"source": "/data/plates/plateA", "destination": "/out/plateA_sub"}
    assert describe_detail("subset", spec) == "/data/plates/plateA → /out/plateA_sub"


def test_detail_is_empty_when_there_is_nothing_to_say():
    """The caller hides the line rather than drawing a bare arrow."""
    assert describe_detail("subset", {}) == ""
    assert describe_detail("analyze", {"opts": {}}) == ""
