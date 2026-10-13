"""A child the app starts in a group of its own does so on Windows too.

Stop reaches a running child through ``services.subprocess_kill.
signal_subprocess``, which on Windows sends ``CTRL_BREAK_EVENT``. That event is
addressed to a console *process group*. A child started without
``CREATE_NEW_PROCESS_GROUP`` leads no group, and the event then goes to every
process attached to the console — the app included, which has no handler for it
and is ended by Windows with ``STATUS_CONTROL_C_EXIT``: no traceback, nothing in
the boot log, the window simply gone. Stop on a batched remote run did exactly
that on Windows, because both ``ssh`` spawns wrote
``start_new_session=(os.name == "posix")`` and gave Windows nothing.

The rule, decidable from the syntax tree: a function that asks for a new POSIX
session (``start_new_session`` as a keyword or a kwargs key) must also ask for a
new Windows process group (``CREATE_NEW_PROCESS_GROUP``) — or take both from
``own_process_group_kwargs()``, which is the one place that spells the pair.
A function is the unit because the existing sites build a kwargs dict in an
``if os.name == "posix": … else: …`` and pass it on, so the two halves are in
the same function but not the same call.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

_HELPER = "own_process_group_kwargs"


def _mentions(node: ast.AST) -> tuple[bool, bool, bool]:
    """``(asks for a POSIX session, asks for a Windows group, uses the helper)``."""
    posix = windows = helper = False
    for sub in ast.walk(node):
        if isinstance(sub, ast.keyword) and sub.arg == "start_new_session":
            posix = True
        elif isinstance(sub, ast.Constant) and sub.value == "start_new_session":
            posix = True
        elif isinstance(sub, ast.Constant) and sub.value == "CREATE_NEW_PROCESS_GROUP":
            windows = True
        elif isinstance(sub, ast.Attribute) and sub.attr == "CREATE_NEW_PROCESS_GROUP":
            windows = True
        elif isinstance(sub, ast.Name) and sub.id == "CREATE_NEW_PROCESS_GROUP":
            windows = True
        elif (isinstance(sub, ast.Call) and isinstance(sub.func, (ast.Name, ast.Attribute))
              and (getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)) == _HELPER):
            helper = True
    return posix, windows, helper


def test_a_new_session_comes_with_a_new_windows_group():
    offenders: list[str] = []
    for path in python_sources():
        tree = parse_source(path)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            posix, windows, _helper = _mentions(fn)
            if posix and not windows:
                offenders.append(f"{rel(path)}:{fn.lineno} {fn.name}")
    assert not offenders, (
        "These functions start a child in a new POSIX session and give Windows "
        "no process group of its own, so Stop's CTRL_BREAK_EVENT reaches the "
        "whole console and ends the app. Spread "
        "`**own_process_group_kwargs()` (services/subprocess_kill.py) into the "
        "Popen instead:\n  " + "\n  ".join(offenders))


def test_the_two_ssh_spawns_take_their_group_from_the_helper():
    """The sites that shipped the bug, named, so the helper cannot be bypassed
    there by a later edit that happens to satisfy the general rule another way."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    for module, function in (("services/remote_process.py", "_start_driver"),
                             ("services/remote_runner.py", "_start_ssh")):
        tree = parse_source(repo / module)
        fns = [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == function]
        assert fns, f"{module}::{function} is gone; point this test at the ssh spawn"
        assert _mentions(fns[0])[2], (
            f"{module}::{function} spawns ssh without "
            f"`**own_process_group_kwargs()`")


def test_the_helper_asks_each_platform_for_its_own_group(monkeypatch):
    """The two halves, each read under the platform that uses it."""
    import subprocess

    from services import subprocess_kill

    monkeypatch.setattr(subprocess_kill.os, "name", "posix")
    assert subprocess_kill.own_process_group_kwargs() == {"start_new_session": True}
    monkeypatch.setattr(subprocess_kill.os, "name", "nt")
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    assert subprocess_kill.own_process_group_kwargs() == {"creationflags": 0x200}
