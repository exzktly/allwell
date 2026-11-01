"""Guard: nothing starts ``ssh`` by name.

A bare ``ssh`` is a PATH lookup, and on Windows the first ``ssh.exe`` on PATH is usually the built-in OpenSSH under
``System32``, which is reported to hang when a console-less program runs it with redirected stdio. Every ssh this app
starts comes from ``services.remote_config.resolve_ssh_binary`` (or ``resolve_ssh_for_rsync``), which takes that one
only when nothing else exists (``tests/test_the_app_never_picks_windows_builtin_ssh.py``); an argv that names ``ssh``
itself would step round it.
"""

from __future__ import annotations

import ast

from tests._repo_scan import parse_source, python_sources, rel

_SSH_NAMES = {"ssh", "ssh.exe"}


def violations_in(tree: ast.Module, name: str) -> list[str]:
    """Argument lists (``[...]``, the shape every argv here takes) that start ``ssh`` by name — a PATH lookup that
    bypasses ``resolve_ssh_binary``. Tuples are not read: ``("ssh", path)`` is a label pair, not a command."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.List) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value in _SSH_NAMES:
                out.append(f"{name}:{node.lineno}")
    return out


def test_no_ssh_is_started_by_name():
    bad = sorted(v for path in python_sources() for v in violations_in(parse_source(path), rel(path)))
    assert not bad, (
        "ssh started by name, which lets Windows pick its built-in OpenSSH from PATH:\n  " + "\n  ".join(bad)
        + "\n\nStart it from services.remote_config.resolve_ssh_binary() (or RemoteHost.ssh_argv()).")


def test_the_guard_catches_ssh_started_by_name():
    for bad in ('import subprocess\nsubprocess.run(["ssh", "host", "true"])\n',
                'cmd = ["ssh.exe", "-o", "BatchMode=yes"] + rest\n'):
        assert violations_in(ast.parse(bad), "bad.py"), bad
    assert not violations_in(ast.parse('argv = [resolve_ssh_binary(), "host"]\nlabel = ("ssh", x)\n'), "good.py")
