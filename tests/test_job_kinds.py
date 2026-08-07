"""The Job Queue's per-kind tables and the names it derives from a spec.

Qt-free — everything here is plain data and string building, which is why it
lives in ``services/`` rather than beside the page that renders it.
"""

from __future__ import annotations

import pytest

from services.job_kinds import (
    CHAIN_KIND,
    CHAIN_TARGET,
    KIND_LABEL,
    describe,
    describe_detail,
    produced_for,
)


# ── table coverage ────────────────────────────────────────────────────────────

def test_every_kind_can_receive_a_chained_input():
    """A kind missing from CHAIN_TARGET is a job that cannot be chained to."""
    missing = sorted(set(KIND_LABEL) - set(CHAIN_TARGET))
    assert not missing, f"CHAIN_TARGET has no entry for: {missing}"


def test_the_chain_kind_is_one_materialise_can_resolve():
    from services.job_queue import _CHAIN_KINDS

    assert CHAIN_KIND in _CHAIN_KINDS


def test_a_datasets_tool_reports_the_root_it_built():
    assert produced_for("subset", "/d/plateA_sub") == {"dataset_root": "/d/plateA_sub"}


def test_analyze_reports_a_dataset_root_as_well_as_its_out_folder():
    """Regression: chaining anything after Analyze handed it `out/`.

    AnalyzeTab emits its output directory, and every successor — Validate,
    Subset, Concatenate, Merge Plates, and Analyze itself — takes a *dataset
    root*, the folder holding the `in/` + `out/` pair. Handed `out/`, Subset
    looks for `out/in/`, finds no wells, and quietly subsets nothing.
    `resolve_input_output` returns `<root>/out` in every layout branch, so the
    root is the parent by construction.
    """
    assert produced_for("analyze", "/data/plateA_processed/out") == {
        "out_dir": "/data/plateA_processed/out",
        "dataset_root": "/data/plateA_processed",
    }


def test_nothing_is_recorded_for_a_job_that_reported_no_path():
    """An empty path must not become a `dataset_root` of "" that materialise
    would then hand a successor as a real answer."""
    assert produced_for("analyze", "") == {}
    assert produced_for("subset", "") == {}


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
