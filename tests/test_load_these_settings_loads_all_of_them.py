""""Load these settings" must restore every setting, not eighteen of twenty.

Two options are spelled one way in the Analyze form and the other way in the
sidecar. The form carries `auto_threshold` and `run_check` as positives; the
pipeline records the negatives it was handed on the command line,
`no_auto_threshold` and `no_run_check`. `_apply_pinfo_settings` reads the
sidecar's spelling.

`_previous_settings` prefers the run journal, whose spec holds the *form's*
opts verbatim. So a journal-sourced load handed the applier a dict naming
neither key it was looking for, and both checkboxes silently kept whatever
they were already showing — the two that decide whether the plate gets a gate
at all and whether the run audits itself when it finishes.

Nothing reported an error. The button said it had loaded the settings, and had
loaded all of them but these.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from analyze_preflight import _as_sidecar_polarity  # noqa: E402


def test_the_forms_positives_become_the_sidecars_negatives():
    got = _as_sidecar_polarity(
        {"auto_threshold": False, "run_check": True, "nuclear_token": "DAPI"})

    assert got["no_auto_threshold"] is True, (
        "a run that turned the gate off is loaded as one that left it on")
    assert got["no_run_check"] is False
    assert got["nuclear_token"] == "DAPI", "unrelated settings were disturbed"


def test_both_polarities_round_trip():
    for value in (True, False):
        got = _as_sidecar_polarity({"auto_threshold": value})
        assert got["no_auto_threshold"] is (not value)


def test_a_sidecar_shaped_dict_is_left_alone():
    """The other source. Translating a dict that already speaks the applier's
    language would invert it."""
    already = {"no_auto_threshold": True, "no_run_check": True}
    assert _as_sidecar_polarity(already) == already


def test_an_explicit_negative_wins_over_the_positive():
    """If both are present the applier's own key is authoritative — inventing
    a second answer for a question already answered is how the two spellings
    came to disagree in the first place."""
    got = _as_sidecar_polarity(
        {"auto_threshold": True, "no_auto_threshold": True})
    assert got["no_auto_threshold"] is True


def test_the_journal_branch_translates_and_the_sidecar_branch_does_not():
    """§7.7 #1 — the helper is worth nothing if the caller skips it."""
    import ast
    import inspect
    import textwrap

    import analyze_preflight

    src = textwrap.dedent(inspect.getsource(analyze_preflight._previous_settings))
    tree = ast.parse(src)
    calls = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert any("_as_sidecar_polarity" in c for c in calls), (
        "the journal branch returns the form's own spelling, so the applier "
        "silently drops both settings")
    assert any("_pinfo_flat" in c for c in calls), (
        "the sidecar branch is gone; this test names a shape that no longer "
        "exists")
