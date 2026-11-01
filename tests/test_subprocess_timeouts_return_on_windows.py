"""Guard: a subprocess whose output is captured is never given a timeout through ``subprocess.run``.

On Windows, ``subprocess.run(..., capture_output=True, timeout=…)`` kills a child that timed out and then calls
``communicate()`` with **no** timeout, to collect what the child's reader threads had gathered. Those threads read
until the pipes close, and any process the child started — ssh's ProxyJump hop, a ProxyCommand, a shell's background
job — inherits the pipes and holds them open after the child is killed. The call then never returns, and the thread
that made it is parked for good without a word. That is how a heartbeat thread went silent on a Windows 365 machine:
one beat timed out, nothing was logged, no further beat was sent, and the granted node it was keeping released itself
under a running step ten minutes later.

``util.bounded_run.run_bounded`` is the one spelling: the same arguments as ``subprocess.run``, but a timeout
returns at the timeout on every platform (``tests/test_a_control_command_returns_at_its_timeout_on_windows.py``).

What this rejects:

* ``subprocess.run`` / ``subprocess.check_output`` with a ``timeout=`` (other than ``None``) that captures output —
  ``capture_output=True``, or ``stdout=`` / ``stderr=`` set to ``subprocess.PIPE``; ``check_output`` always captures
  stdout. Through any import spelling: ``subprocess.run``, an aliased module, ``from subprocess import run``.
* The same mistake written by hand: a ``.communicate()`` with no timeout inside an ``except TimeoutExpired`` handler.

A call that captures nothing (output to ``DEVNULL``, a file, or inherited) cannot hang this way and is left alone. A
capture hidden in a ``**kwargs`` dict built elsewhere is not seen; no site in the tree does that today, and the
``tests/test_guards_bite.py`` cases say what is seen.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

_RUNNERS = {"run", "check_output"}


def _subprocess_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """``(names bound to the subprocess module, names bound to subprocess.run / check_output)`` in *tree*."""
    modules, funcs = {"subprocess"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "subprocess":
                    modules.add(a.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for a in node.names:
                if a.name in _RUNNERS:
                    funcs.add(a.asname or a.name)
    return modules, funcs


def _runner(node: ast.Call, modules: set[str], funcs: set[str]) -> str:
    """``"run"`` / ``"check_output"`` when *node* calls one of them, else ``""``."""
    f = node.func
    if isinstance(f, ast.Attribute) and f.attr in _RUNNERS and isinstance(f.value, ast.Name) \
            and f.value.id in modules:
        return f.attr
    if isinstance(f, ast.Name) and f.id in funcs:
        return "check_output" if f.id == "check_output" else "run"
    return ""


def _is_pipe(node: ast.expr) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "PIPE") or \
        (isinstance(node, ast.Name) and node.id == "PIPE")


def _captures(node: ast.Call, runner: str) -> bool:
    if runner == "check_output":
        return True
    for kw in node.keywords:
        if kw.arg == "capture_output" and not (isinstance(kw.value, ast.Constant) and not kw.value.value):
            return True
        if kw.arg in ("stdout", "stderr") and _is_pipe(kw.value):
            return True
    return False


def _has_timeout(node: ast.Call) -> bool:
    return any(kw.arg == "timeout" and not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
               for kw in node.keywords)


def _catches_timeout(handler: ast.ExceptHandler) -> bool:
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any((isinstance(t, ast.Attribute) and t.attr == "TimeoutExpired") or
               (isinstance(t, ast.Name) and t.id == "TimeoutExpired") for t in types if t is not None)


def violations_in(tree: ast.Module, name: str) -> list[str]:
    """Every site in *tree* (a module called *name*) that can wait past its timeout on Windows."""
    modules, funcs = _subprocess_names(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            runner = _runner(node, modules, funcs)
            if runner and _has_timeout(node) and _captures(node, runner):
                out.append(f"{name}:{node.lineno} subprocess.{runner} with a timeout and captured output")
        elif isinstance(node, ast.ExceptHandler) and _catches_timeout(node):
            for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) \
                        and inner.func.attr == "communicate" and not inner.keywords and not inner.args:
                    out.append(f"{name}:{inner.lineno} communicate() with no timeout after a timeout")
    return out


def _violations() -> list[str]:
    out: list[str] = []
    for path in python_sources():
        out.extend(violations_in(parse_source(path), rel(path)))
    return sorted(set(out))


def test_no_captured_subprocess_can_outlive_its_timeout():
    bad = _violations()
    assert not bad, (
        f"{len(bad)} subprocess call(s) can wait past their timeout on Windows — subprocess.run, after killing a "
        "child that timed out, waits with no timeout for pipes a process the child started may hold open:\n  "
        + "\n  ".join(bad)
        + "\n\nUse util.bounded_run.run_bounded (same arguments as subprocess.run)."
    )

