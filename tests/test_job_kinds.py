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
#
# **The universe comes from the feature registry, not from another table.**
# These checks used to take it from ``KIND_LABEL``, which is one of the tables
# being checked — a roster held against another roster (CLAUDE.md §7.5 #10),
# and it failed exactly as that rule predicts. A tool was added to
# ``datasets/tab.py`` and to none of these, so ``KIND_LABEL`` was missing the
# same row the check was looking for, and the guard written for this defect
# passed while the defect shipped: a queued job whose row read ``nd2_import``
# with an empty second line, which could not be chained after anything, was
# absent from the "+ Add" menu, and wore another tool's icon.
#
# ``datasets.tab._FEATURES`` is the thing these tables are a list *of*, which
# is what makes it a guard rather than a restatement.


#: Kinds deliberately absent from a table, with the reason. An exemption is a
#: row with a written reason and never an absence: a kind left out on purpose
#: and one nobody remembered look identical in code six months later
#: (CLAUDE.md §7.5 #10). Paired with a liveness check below, so an exemption
#: that stops describing a real decision fails rather than lingering.
_EXEMPT = {
    "_DESCRIBE_KEYS": {
        "analyze": (
            "Analyze does not describe itself from spec keys. Its spec holds "
            "an options blob rather than named input and output paths, so "
            "`describe` branches to `_analyze_label` before this table is "
            "consulted at all — an entry here would be dead data."),
    },
}


def _queueable_kinds() -> list[str]:
    """Every kind a Datasets tool can queue, plus Analyze, from the registry."""
    from datasets.tab import _FEATURES

    return [feat.kind for feat in _FEATURES if feat.kind] + ["analyze"]


@pytest.mark.parametrize("table_name", ["KIND_LABEL", "CHAIN_TARGET",
                                        "_DESCRIBE_KEYS", "ADD_KINDS",
                                        "KIND_ICON"])
def test_every_queueable_tool_appears_in_every_kind_table(table_name):
    """A tool missing from one of these does not fail — it degrades quietly.

    Each omission costs something different and none of them raises: no
    ``KIND_LABEL`` is a queue row showing the raw kind string, no
    ``_DESCRIBE_KEYS`` is a row with no paths on it, no ``CHAIN_TARGET`` means
    ``set_chain`` returns ``False`` and the job simply cannot be chained, no
    ``ADD_KINDS`` hides the tool from the queue's own menu, and no
    ``KIND_ICON`` gives it whichever icon the fallback names.
    """
    import services.job_kinds as jk
    from datasets.job_queue.row import KIND_ICON
    from datasets.job_queue.view import ADD_KINDS

    tables = {
        "KIND_LABEL": jk.KIND_LABEL,
        "CHAIN_TARGET": jk.CHAIN_TARGET,
        "_DESCRIBE_KEYS": jk._DESCRIBE_KEYS,
        "ADD_KINDS": ADD_KINDS,
        "KIND_ICON": KIND_ICON,
    }
    table = tables[table_name]
    kinds = _queueable_kinds()
    assert "czi_import" in kinds, "the registry roster is empty by accident"

    exempt = _EXEMPT.get(table_name, {})
    missing = [k for k in kinds if k not in table and k not in exempt]
    assert not missing, f"{table_name} has no entry for: {missing}"
    stale = [k for k in exempt if k in table]
    assert not stale, (
        f"{table_name} now has an entry for {stale}, so its exemption here "
        f"describes nothing and should be removed")


def test_every_exemption_names_a_kind_that_still_exists():
    """An exemption for a kind nobody has any more is an exemption nobody
    checked — the shape §7.5 #10 warns about, one level up."""
    kinds = set(_queueable_kinds())
    for table_name, exempt in _EXEMPT.items():
        unknown = sorted(set(exempt) - kinds)
        assert not unknown, (
            f"{table_name} exempts {unknown}, which no feature declares")


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
