"""Every place that cancels, or asks about, "the run's job" says how it treats a step id (tracker H5).

A run on a granted node (``services.remote_hold``) is a *step* of the job the user is keeping, and ``<pidfile>.slurm``
names ``<job>.<step>``. ``scancel`` of a bare job id ends the job; ``squeue -j <job>.<step>`` is "Invalid job id",
which reads as "finished". So a place that cancels or asks about a job id and does not know about steps does the
wrong thing the day a run lands on a granted node — releasing the node, or judging a running step gone — and does it
silently: the next run simply queues again. Nothing fails, which is why this is a guard and not a test of one site.

This finds every string literal in the application that is a ``scancel`` command or an ``squeue -j`` question
(docstrings and comments are not code and are not counted), names the function or module constant it lives in, and
requires that site to be on the roster below with the reason it is right. A new one fails here until someone has
written down what it does with a step.

The roster is per site, not per file, and it has a liveness half: an entry whose site no longer exists fails too,
so the roster cannot go on vouching for code that was deleted or rewritten. ``tests/test_guards_bite.py`` holds the
cases that show this fires.

The reasons are claims, and each is held by a test, named in it.
"""

from __future__ import annotations

import ast
import re

from tests._repo_scan import parse_source, python_sources, rel

#: A ``scancel`` command (or the word in a message about one).
_SCANCEL = re.compile(r"\bscancel\b")
#: A question put to ``squeue`` about a job id: ``squeue … -j …``.
_SQUEUE_J = re.compile(r"\bsqueue\b.*?\s-j\b")

#: ``"<file>::<site>"`` → why that site is right with a step id. ``<site>`` is the enclosing function
#: (``Class.method`` for a method), or ``<module>:NAME`` for a module-level constant.
ROSTER: dict[str, str] = {
    "services/remote_hold.py::cancel_script":
        "The one place a granted node is cancelled on purpose (Release, quitting, an abandoned request), behind "
        "`_only_my_job`. tests/test_remote_hold_request.py.",
    "services/remote_hold.py::compose_hold_script":
        "Only the word `scancel` in a comment inside the generated script, saying where a SIGTERM can come from; the "
        "script runs no scancel.",
    "services/remote_jobs.py::_only_my_job":
        "The owner check asks about the job part of the id (`${jid%%.*}`), because `squeue -j` takes job ids and "
        "rejects `<job>.<step>`. tests/test_a_run_never_cancels_the_granted_node.py::"
        "test_a_step_of_a_job_that_is_someone_elses_is_not_cancelled.",
    "services/remote_jobs.py::close_remote_job":
        "Cancels the sidecar's id as written, so a `<job>.<step>` ends the step only; a bare id that is a hold's own "
        "job, or its batch / extern step, is refused first by `_aw_is_hold` (services/slurm_ids.py). "
        "tests/test_a_run_never_cancels_the_granted_node.py::test_closing_a_run_on_a_granted_node_ends_its_step_and_"
        "leaves_the_node and ::test_a_sidecar_naming_the_nodes_own_job_is_never_cancelled.",
    "services/remote_jobs.py::kill_remote_pipeline":
        "The word appears only in a log message; the function kills the process in the pidfile and cancels no job.",
    "services/remote_liveness.py::cancel_pending":
        "Cancels a PENDING job whose id the run's own stream named (digits only: a step is never pending and never "
        "named there), and declines a hold's id with `_aw_is_hold`. tests/test_a_run_never_cancels_the_granted_node.py"
        "::test_cancel_pending_declines_the_nodes_own_job.",
    "services/remote_reservation.py::_release_script":
        "Cancels the early request's own job: `Reservation.request` queued it itself, its directory has a random "
        "name, and it is never a hold (a run skips the early request while a node is granted).",
    "services/remote_srun_retry.py::_queue_deadline":
        "Cancels the job id parsed from this wrapper's own `srun: job N queued and waiting` line: an allocation this "
        "wrapper asked for, never a hold.",
    "services/remote_srun_retry.py::wrap_srun_launch":
        "Cancels, and asks `squeue -j` about, the job id parsed from this wrapper's own `srun: job N queued and "
        "waiting` line (digits): a run's own queued allocation, never a hold and never a step.",
    "services/remote_sweep.py::_refusal":
        "A message telling the user which command would end the job; it cancels nothing.",
    "services/remote_sweep.py::_target_script":
        "Asks about a step with `squeue -s` (services/slurm_ids.squeue_listing), cancels a `<job>.<step>` as a step, "
        "and leaves a sidecar naming a hold's own job alone (`AW_HOLDID`). tests/test_a_run_never_cancels_the_"
        "granted_node.py::test_the_sweep_of_a_leftover_step_cancels_the_step_and_not_the_node and "
        "::test_the_sweep_leaves_a_sidecar_that_names_the_node_itself.",
    "services/run_evidence.py::RunEvidence.termination":
        "A sentence for the log saying a job was ended by `scancel`; it cancels nothing.",
    "services/slurm_ids.py::squeue_listing":
        "The sanctioned way to ask: `squeue -s<job>.<step>` for a step and `squeue -j <job>` for a job.",
}


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


def sites_in(tree: ast.Module, path: str) -> set[str]:
    """Every ``"<path>::<site>"`` in *tree* holding a cancel command or a job question, as a string literal.

    f-strings count: their literal parts are ``Constant`` nodes inside a ``JoinedStr``. Docstrings do not.
    """
    docs = _docstring_ids(tree)
    found: set[str] = set()

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = stack
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = [*stack, child.name]
            elif isinstance(child, ast.Assign) and not stack and isinstance(child.targets[0], ast.Name):
                inner = [f"<module>:{child.targets[0].id}"]
            if (isinstance(child, ast.Constant) and isinstance(child.value, str) and id(child) not in docs
                    and (_SCANCEL.search(child.value) or _SQUEUE_J.search(child.value))):
                found.add(f"{path}::{'.'.join(inner) if inner else '<module>'}")
            visit(child, inner)

    visit(tree, [])
    return found


def _all_sites() -> set[str]:
    found: set[str] = set()
    for source in python_sources():
        found |= sites_in(parse_source(source), rel(source))
    return found


def test_every_scancel_and_job_question_has_a_reason_for_how_it_treats_a_step():
    unrostered = sorted(_all_sites() - set(ROSTER))
    assert not unrostered, (
        "These cancel a job, or ask `squeue -j` about one, and are not on ROSTER in "
        "tests/test_every_job_cancel_names_how_it_handles_a_step.py:\n  " + "\n  ".join(unrostered) +
        "\nA run on a granted node is a step (`<job>.<step>`): `scancel <job>` would release the node and "
        "`squeue -j <job>.<step>` reads a running step as finished. Use services/slurm_ids.py (`squeue_listing`, "
        "`hold_check_shell`) or write down why this site never sees a step or a hold, in ROSTER.")


def test_every_roster_entry_still_describes_a_real_site():
    stale = sorted(set(ROSTER) - _all_sites())
    assert not stale, ("ROSTER names sites that no longer cancel or ask about a job — delete the entry, or the "
                       "roster is vouching for code that is gone:\n  " + "\n  ".join(stale))
    assert all(len(reason) > 40 for reason in ROSTER.values()), "a roster entry needs a real reason"
