"""A Stop pressed the instant a run starts must not be discarded.

`start()` returns as soon as the worker thread is created; the thread may not
be scheduled for some time after that, and on a loaded machine that gap is
long enough to click a button in. The worker's first act was to clear
`_stop_requested` and five sibling flags — so a Stop landing in the gap was
wiped, the run carried on, and the button had already returned to its idle
state. The user saw a cancel that did nothing and no way to ask again until
the next repaint.

The flags are per-*run* state, and the run begins when `start()` is called,
not when the OS gets round to the thread. Clearing them before the thread
exists closes the window rather than narrowing it.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
import threading


from services.pipeline_runner import PipelineRunner

_PER_RUN_FLAGS = (
    "_stop_requested", "_suspend_requested", "_error_was_stopped",
    "_error_was_suspended", "_had_error", "_journal_dir", "_journal_token",
)


def test_a_stop_set_before_the_worker_runs_survives_into_it(monkeypatch):
    """The gap, made deterministic rather than raced for.

    A real race would pass on the unfixed code most of the time, which is a
    test that cannot fail (§7.3). So the thread is stopped from auto-starting,
    the Stop is set in the gap that leaves, and the worker body is then run by
    hand — exactly the ordering the scheduler produces on a loaded machine,
    with none of the chance.
    """
    started: list = []

    class _HeldThread:
        def __init__(self, target=None, args=(), daemon=None, **kw):
            self._target, self._args = target, args
            started.append(self)

        def start(self):        # the scheduler has not got to it yet
            pass

        def is_alive(self):
            return True

        def run_now(self):
            self._target(*self._args)

    monkeypatch.setattr(threading, "Thread", _HeldThread)

    runner = PipelineRunner()
    runner.start(__import__("pathlib").Path("pm.py"), {},
                 resolve_dirs=lambda *_a, **_k: None)
    assert started, "start() did not create a worker"

    runner._stop_requested.set()          # the user clicks Stop, in the gap
    started[0].run_now()                  # the scheduler finally runs it

    assert runner._stop_requested.is_set(), (
        "the worker cleared the user's Stop on its way in, so the run carried "
        "on with the cancellation discarded and the button had already gone "
        "back to idle")


def test_no_per_run_flag_is_cleared_inside_the_worker():
    """The structural rule, so a seventh flag added later cannot reintroduce it."""
    src = textwrap.dedent(inspect.getsource(PipelineRunner._run_pipeline_thread))
    tree = ast.parse(src)

    cleared: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and t.attr in _PER_RUN_FLAGS:
                    cleared.append(t.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "clear"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in _PER_RUN_FLAGS):
            cleared.append(node.func.value.attr)

    assert not cleared, (
        f"these per-run flags are reset inside the worker thread, so a Stop "
        f"landing between start() and the thread being scheduled is wiped: "
        f"{sorted(set(cleared))}")


def test_start_clears_every_one_of_them():
    """The other direction: moved, not merely deleted."""
    src = textwrap.dedent(inspect.getsource(PipelineRunner.start))
    for flag in _PER_RUN_FLAGS:
        assert flag in src, (
            f"{flag} is no longer reset per run, so it leaks from the "
            f"previous run into this one")


def test_a_fresh_runner_answers_about_its_journal_without_raising():
    """`stop()` and the queue's busy gate can both reach a runner that has
    never run; an AttributeError there is a crash in a Qt slot."""
    runner = PipelineRunner()
    assert runner._journal_dir is None
    assert runner._journal_token is None
    assert runner.is_busy is False
