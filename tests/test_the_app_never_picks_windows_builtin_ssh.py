"""Windows' built-in OpenSSH is this app's last resort, never its choice while another ssh exists — and when it is
used, the run log says so.

That ssh (``C:\\Windows\\System32\\OpenSSH\\ssh.exe``) is reported to hang instead of exiting when a program with no
console window runs it with redirected input and output, which is how this app runs every ssh. The app always
preferred Git for Windows' ``ssh.exe``, but when it did not find Git it handed back a bare ``ssh``, Windows then took
the first on PATH — the built-in one on a stock machine, ahead of an MSYS2 or other ssh later on PATH — and nothing
said so.

Wrong implementations these reject: the bare-``ssh`` fallback (System32 first on PATH wins); preferring a PATH ssh
over a Git install that was found; a run log that names no ssh, or does not warn for the built-in one; a new spawn
site that starts ``ssh`` by name and so bypasses the resolver altogether.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

import services.remote_config as rc
from tests._repo_scan import parse_source, python_sources, rel


def _exe(root: Path, *parts: str) -> Path:
    d = root.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    (d / "ssh.exe").write_text("")
    return d


@pytest.fixture
def windows(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "_is_windows", lambda: True)
    monkeypatch.setattr(rc, "_git_bash_bin_dirs", lambda: ())
    monkeypatch.setattr(rc, "resolve_ssh_for_rsync", lambda: "")
    system32 = _exe(tmp_path, "Windows", "System32", "OpenSSH")
    msys2 = _exe(tmp_path, "msys64", "usr", "bin")
    return monkeypatch, tmp_path, system32, msys2


def test_without_git_another_ssh_on_path_is_taken_ahead_of_the_builtin_one(windows):
    """The reporter's PATH: System32's OpenSSH first, MSYS2's second."""
    monkeypatch, _, system32, msys2 = windows
    monkeypatch.setenv("PATH", os.pathsep.join([str(system32), str(msys2)]))
    assert rc.resolve_ssh_binary() == str(msys2 / "ssh.exe")
    assert not [ln for ln in rc.ssh_binary_lines() if "WARNING" in ln]


def test_git_for_windows_is_taken_whatever_is_on_path(windows):
    monkeypatch, tmp_path, system32, msys2 = windows
    git = _exe(tmp_path, "AppData", "Local", "Programs", "Git", "usr", "bin")
    monkeypatch.setattr(rc, "_git_bash_bin_dirs", lambda: (git,))
    monkeypatch.setenv("PATH", os.pathsep.join([str(system32), str(msys2)]))
    assert rc.resolve_ssh_binary() == str(git / "ssh.exe")
    assert rc.ssh_binary_lines() == [f"[remote] ssh: {git / 'ssh.exe'}\n"]


def test_the_builtin_one_alone_is_used_and_warned_about(windows):
    monkeypatch, _, system32, _ = windows
    monkeypatch.setenv("PATH", str(system32))
    chosen = rc.resolve_ssh_binary()
    assert chosen == str(system32 / "ssh.exe")
    lines = rc.ssh_binary_lines()
    assert lines[0] == f"[remote] ssh: {chosen}\n"
    assert len(lines) == 2 and lines[1].startswith("[remote] WARNING this app is running Windows' built-in ssh")
    assert "Install Git for Windows" in lines[1]


def test_the_transfer_ssh_is_named_and_warned_about_too(windows):
    monkeypatch, _, system32, msys2 = windows
    monkeypatch.setenv("PATH", str(msys2))
    monkeypatch.setattr(rc, "resolve_ssh_for_rsync", lambda: str(system32 / "ssh.exe"))
    lines = rc.ssh_binary_lines()
    assert f"[remote] ssh for transfers: {system32 / 'ssh.exe'}\n" in lines
    assert any("WARNING" in ln and str(system32) in ln for ln in lines)


@pytest.mark.parametrize("path, builtin", [
    (r"C:\Windows\System32\OpenSSH\ssh.exe", True),
    (r"c:\windows\SysWOW64\OpenSSH\ssh.exe", True),
    (r"C:\Windows\Sysnative\OpenSSH\ssh.exe", True),
    (r"C:\Program Files\Git\usr\bin\ssh.exe", False),
    (r"C:\msys64\usr\bin\ssh.exe", False),
    (r"C:\Program Files\OpenSSH\ssh.exe", False),       # a separately installed Win32-OpenSSH release
])
def test_which_ssh_counts_as_the_builtin_one(path, builtin):
    assert rc.is_builtin_windows_ssh(path) is builtin


def test_the_run_log_names_the_ssh_through_the_real_runner():
    """`PipelineRunner._run_pipeline_remote` writes the lines; this pins that it imports and calls the helper."""
    import services.pipeline_runner as pr

    assert pr.ssh_binary_lines is rc.ssh_binary_lines
    source = Path(pr.__file__).read_text(encoding="utf-8")
    assert "for said in ssh_binary_lines():" in source


# ── guard: nothing starts ssh by name ──────────────────────────────────────────────────────────────────────────────

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
