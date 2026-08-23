"""A half-finished well must be recovered however the user restarts the plate.

`resume_aware_spec` forces `--incremental` when the input has an unfinished run
behind it. Without the flag a re-run skips every well whose output zip exists
and processes the rest, which recovers the wells that never began and silently
abandons the ones that were *in flight*: those wrote a CSV and an out.zip from
the FOV-groups they had finished, so they read as complete, are skipped, and
their missing groups are never recovered. The plate comes back looking done and
is permanently 3-of-12 in those wells.

Two things were wrong with it, and they compound.

It asked only `suspend_record`, which holds the runs the user wound down
deliberately. The run journal holds every run that did not finish — including
the ones nobody was there to record: an app that was killed, a machine that lost
power, a crash. That case is precisely the one no artefact can report, because a
killed run and a run that simply processed fewer wells leave identical output.

And it was wired into `start_from_queue` alone. The Run button is how almost
everyone restarts a plate, and it never asked.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

pytest.importorskip("PySide6")


def _source(obj) -> str:
    return inspect.getsource(obj)


def test_the_run_button_asks_the_same_question_the_queue_does():
    """§7.7 #1: test the caller's sequence, not the unit's API.

    Every existing test drove `resume_aware_spec` directly, and all of them
    would pass against a `start_run` that never called it — which is exactly
    how a correct helper ships behind a branch the app does not take.
    """
    import analyze_run_spec

    src = _source(analyze_run_spec.AnalyzeRunMixin.start_run)
    assert "resume_aware_spec" in src, (
        "start_run does not consult the resume record, so restarting a killed "
        "plate with the Run button skips every well that was mid-flight")

    tree = ast.parse(textwrap.dedent(src))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    starts = [n for n in calls
              if isinstance(n.func, ast.Attribute) and n.func.attr == "start"]
    resumes = [n for n in calls
               if isinstance(n.func, ast.Attribute)
               and n.func.attr == "resume_aware_spec"]
    assert starts and resumes, (src, starts, resumes)
    assert resumes[0].lineno < starts[0].lineno, (
        "the spec is adjusted after the runner has already been started")


def test_the_queue_does_not_apply_it_twice():
    """Once `start_run` owns it, `start_from_queue` must not repeat it.

    Harmless today — the function is idempotent — but two callers of one rule
    is how the two come to disagree, which is the whole subject of this audit.
    """
    import analyze_run_spec

    src = _source(analyze_run_spec.AnalyzeRunMixin.start_from_queue)
    tree = ast.parse(textwrap.dedent(src))
    calls = [n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert "resume_aware_spec" not in calls, (
        "start_from_queue still applies the rule that start_run now owns; two "
        "callers of one rule is how the two come to disagree")


def test_the_predicate_reads_the_journal_as_well_as_the_suspend_record():
    """The killed case has no suspend record by definition."""
    import analyze_suspend

    src = _source(analyze_suspend.AnalyzeSuspendMixin._input_has_unfinished_work)
    assert "unfinished_runs" in src, (
        "only the suspend record is consulted, so a run the app was killed "
        "during is never offered incrementally")
    assert "load_all" in src, "the suspend record is no longer consulted"


def test_the_input_directory_is_read_from_the_journal_not_guessed(tmp_path, monkeypatch):
    """The index is keyed on the *output* directory.

    An output directory can be re-pointed between runs, so inferring one from
    the other is how a resume gets offered for a different plate — the shape of
    L7 in the same audit.
    """
    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    out = tmp_path / "out"
    out.mkdir()
    mine = tmp_path / "plateA"
    mine.mkdir()
    J.open_run(out, spec={}, mode="local", input_dir=mine)

    rec = J.read_journal(out)
    assert rec is not None and rec.input_dir == str(mine), (
        "the journal does not carry the input directory it was run against, "
        "so the predicate has nothing to match on")

    entries = J.unfinished_runs()
    assert any(str(e.get("output_dir")) == str(out) for e in entries), (
        "a run with no closed_at is not offered as unfinished")
