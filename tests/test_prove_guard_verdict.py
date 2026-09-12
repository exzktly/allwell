"""`scripts/prove_guard_verdict.py` decides whether a guard proof counts.

`prove_guard.sh` exists because "the guard went red on the base branch" is not
evidence — an import error, a collection error and an exception raised while
formatting the guard's own failure message all look identical in a terminal, and
two of eight hand-made proofs in one branch were exactly those. The classifier
is the piece that tells them apart, which makes it the piece whose own failure
would be silent.

It has already been wrong once: the first version matched pytest's summary line
with `^\\d+ passed`, and pytest pads that line with `=`, so the anchor never
fired and a cleanly-passing run was reported as "could not tell what failed".
Every sample below is real pytest output, recorded from the runs this tool was
built against.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "prove_guard_verdict.py"
_spec = importlib.util.spec_from_file_location("prove_guard_verdict", _MODULE_PATH)
verdict_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verdict_mod)
classify = verdict_mod.classify


# The summary line is "=" padded, which is what defeated the first version.
PASSED = """\
collected 2 items

tests/test_datasets_tool_isolation.py ..                                 [100%]

============================== 2 passed in 0.70s ===============================
"""

ASSERTED = """\
tests/test_divider_naming.py F                                           [100%]
=================================== FAILURES ===================================
E       AssertionError: Divider frame(s) with no sanctioned object name.
E           review/core/status_view.py:95 — top_sep.setFrameShape(...)
E           review/features/batch_export/bar_panel.py:68 — sep.setFrameShape(...)
E           review/features/batch_export/bar_panel.py:68 — sep.setFrameShape(...)
=========================== short test summary info ============================
FAILED tests/test_divider_naming.py::test_no_unnamed_divider_frames
============================== 1 failed in 4.13s ===============================
"""

IMPORT_ERROR = """\
tests/test_plot_card_roster.py F                                         [100%]
E       ImportError: cannot import name 'PLOT_CARD_ATTRS' from 'review.core.plot_card_actions'
=========================== short test summary info ============================
FAILED tests/test_plot_card_roster.py::test_the_shell_imports_the_roster
========================= 1 failed, 1 passed in 2.06s ==========================
"""

COLLECTION_ERROR = """\
==================================== ERRORS ====================================
_____________ ERROR collecting tests/test_datasets_controller_lifecycle.py _____
E   ModuleNotFoundError: No module named 'datasets._common.tool_controller'
=========================== short test summary info ============================
ERROR tests/test_datasets_controller_lifecycle.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
=============================== 1 error in 0.54s ===============================
"""

# The guard asserted, but its message names tool kinds rather than file paths,
# so there is nothing to count. Reporting the assertion without a number is the
# honest answer; claiming zero offenders would read as "caught nothing".
ASSERTED_UNCOUNTABLE = """\
E       AssertionError: set_done is re-defined on: ['concatenate', 'subset']
=========================== short test summary info ============================
FAILED tests/test_datasets_dashboard_outcomes.py::test_no_dashboard_re_hand_rolls
============================== 1 failed in 0.25s ===============================
"""

# A NameError raised while building the failure message: the assertion fired,
# but the run died formatting it. It must not be read as a proof.
NAME_ERROR_IN_MESSAGE = """\
        assert not offenders, (
>           f"§5.3). Current roster: {list(PLOT_CARD_ATTRS)}\\n  "
E       NameError: name 'PLOT_CARD_ATTRS' is not defined
=========================== short test summary info ============================
FAILED tests/test_plot_card_roster.py::test_the_shell_imports_the_roster
========================= 1 failed, 1 passed in 1.97s ==========================
"""


def test_a_clean_run_is_a_pass_not_a_mystery():
    """The `=`-padded summary line is the case that broke the first version."""
    assert classify(PASSED)[0] == "PASSED"


def test_an_assertion_with_offenders_is_a_proof():
    kind, offenders = classify(ASSERTED)
    assert kind == "ASSERTED"
    # Deduplicated: the same site printed twice is one site.
    assert offenders == [
        "review/core/status_view.py:95",
        "review/features/batch_export/bar_panel.py:68",
    ]


@pytest.mark.parametrize("sample, expected_type", [
    (IMPORT_ERROR, "ImportError"),
    (COLLECTION_ERROR, "ModuleNotFoundError"),
    (NAME_ERROR_IN_MESSAGE, "NameError"),
])
def test_a_run_that_errored_is_never_a_proof(sample, expected_type):
    """These three are the false proofs this whole tool exists to refuse."""
    kind, extra = classify(sample)
    assert kind == "ERRORED", f"{expected_type} was accepted as a proof"
    assert extra == [expected_type]


def test_an_assertion_without_countable_offenders_is_still_an_assertion():
    kind, offenders = classify(ASSERTED_UNCOUNTABLE)
    assert kind == "ASSERTED"
    assert offenders == []


def test_the_word_error_in_prose_does_not_flip_a_passing_run():
    """`\\d+ errors?` must match a pytest count, not the word in a test name."""
    passing_with_prose = PASSED.replace(
        "tests/test_datasets_tool_isolation.py ..",
        "tests/test_error_handling.py ..")
    assert classify(passing_with_prose)[0] == "PASSED"
