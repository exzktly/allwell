"""Every read of ``host.launch_command`` is on a roster with the reason it may bypass ``RemoteRunner.launcher()``.

While a node is granted, a run's scheduler command is not the host's ``srun …`` line but a step of the node's job
(``services.remote_hold.step_launcher``), and ``RemoteRunner.launcher()`` is the one place that decides. A launch site
that reads ``host.launch_command`` directly asks the scheduler for a node of its own beside the one the user is
keeping: a second allocation, billed, and on a GPU node the out-of-memory failure that started all this. It fails
nothing — the run simply works, on the wrong node — so the sites that may read it are listed, and a new one fails here
until it has said why.

Allowlist the site, not the file (``tests/test_every_job_cancel_names_how_it_handles_a_step.py`` is the same shape).
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

ROSTER: dict[str, str] = {
    "services/remote_runner.py::RemoteRunner.launcher":
        "The seam itself: the host's command when no node is granted, a step of the node's job when one is.",
    "services/remote_process.py::_BatchedRemoteProcess._start_driver":
        "Reads the operator's `--time` to size one batch's time budget (`launcher_time_seconds`); a step carries no "
        "`--time`, and the budget must stay what the operator wrote.",
    "services/remote_reservation.py::Reservation.request":
        "The early request: it asks for a node of its own, and is skipped entirely while a node is granted.",
    "services/remote_hold.py::GrantedNode.request":
        "Translates the host's command into the options of the hold job: the one place a node is asked for on "
        "purpose.",
    "services/scheduler_probe.py::ProbeRun.__init__":
        "The Test button probes the command as written in the form; a probe through the granted node passes its "
        "launcher in explicitly.",
}


def reads_in(tree: ast.Module, path: str) -> set[str]:
    """Every ``"<path>::<function>"`` that reads an attribute named ``launch_command``."""
    found: set[str] = set()

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = stack
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = [*stack, child.name]
            if isinstance(child, ast.Attribute) and child.attr == "launch_command" and isinstance(child.ctx, ast.Load):
                found.add(f"{path}::{'.'.join(stack) or '<module>'}")
            visit(child, inner)

    visit(tree, [])
    return found


def _all_reads() -> set[str]:
    found: set[str] = set()
    for source in python_sources():
        found |= reads_in(parse_source(source), rel(source))
    return found


def test_every_read_of_the_hosts_launch_command_says_why_it_may_bypass_the_seam():
    # The definition (`RemoteHost.launch_command`, a property) is not a read of it.
    unrostered = sorted(r for r in _all_reads() - set(ROSTER))
    assert not unrostered, (
        "These read `.launch_command` directly and are not on ROSTER in "
        "tests/test_every_launch_goes_through_the_launcher_seam.py:\n  " + "\n  ".join(unrostered) +
        "\nWhile a node is granted a run must launch as a step of it: call `RemoteRunner.launcher()`, or write down "
        "why this site must ask for a node of its own.")


def test_every_roster_entry_still_reads_it():
    stale = sorted(set(ROSTER) - _all_reads())
    assert not stale, "ROSTER names sites that no longer read `.launch_command`:\n  " + "\n  ".join(stale)
