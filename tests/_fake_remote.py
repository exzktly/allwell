"""A remote host that is real enough to catch the defects a stub cannot.

Every critical defect this subsystem has produced was a *filesystem* mistake:
a directory that was never created, a directory that was removed and should
not have been, a listing that answered for the wrong path. Those are invisible
to a stub that records calls and returns ``MagicMock()``, and a hand-modelled
filesystem is its own hazard — an earlier attempt did not model ``mkdir -p``
creating parents, and reported the healthy path as broken.

So this models nothing. It **runs the remote commands**, locally, with ``HOME``
pointed at a temporary directory:

* ``DatasetCache._ssh_run`` and ``_ssh_script`` both reach
  ``subprocess.run(host.ssh_argv(...) + …)``. The ssh prefix is stripped and
  the remainder handed to a real ``bash``, so ``mkdir -p``, ``touch``,
  ``rm -rf``, ``ls -1 …/*_out.zip`` and every ``~`` / ``"$HOME"`` expansion
  behave exactly as they do on a worker node.
* ``_rsync_with_retry`` is the one funnel every transfer passes through, and
  ``rsync`` is not installed here, so it is replaced by a copy that honours the
  ``alias:path`` form. That is the one piece of modelling, and it is confined
  to moving bytes.
* The pipeline launch (``subprocess.Popen``) runs a script the test supplies,
  so a run announces wells the way the real one does and finishes in
  milliseconds.

What a test then asserts is what reached the host: which directories exist,
what was deleted, what the launched command was. That is the boundary
CLAUDE.md §7.4 #1 asks for, rather than the values the code computed on the
way there.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class FakeRemote:
    """A temporary directory standing in for the worker host's ``$HOME``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        #: Every remote command, in order, as the shell string bash received.
        self.commands: list[str] = []
        #: Every rsync, as ``(sources, destination)`` after alias translation.
        self.transfers: list[tuple[list[str], str]] = []
        #: The argv of each pipeline launch.
        self.launched: list[list[str]] = []
        #: Script the fake pipeline runs; set by the test.
        self.pipeline_script: str = "import sys\n"

    # ── what the host holds ──────────────────────────────────────────────
    def path(self, remote_path: str) -> Path:
        """The local path a remote path names. ``~`` and ``$HOME`` are root."""
        p = str(remote_path)
        for prefix in ("~/", "$HOME/"):
            if p.startswith(prefix):
                return self.root / p[len(prefix):]
        if p in ("~", "$HOME"):
            return self.root
        return Path(p) if p.startswith("/") else self.root / p

    def exists(self, remote_path: str) -> bool:
        return self.path(remote_path).exists()

    def tree(self) -> list[str]:
        """Every path under the host root, relative and sorted."""
        return sorted(str(p.relative_to(self.root))
                      for p in self.root.rglob("*"))

    def ran(self, needle: str) -> bool:
        """Whether any remote command contained *needle*."""
        return any(needle in c for c in self.commands)

    # ── the boundary ─────────────────────────────────────────────────────
    #: Captured at import, before anything is patched. ``subprocess.run``
    #: looks ``Popen`` up in its own module globals, so a fake that patches
    #: ``services.remote_runner.subprocess.Popen`` — the *shared* module —
    #: would have its own shell calls answered by the mock it installed.
    _REAL_POPEN = staticmethod(subprocess.Popen)

    def _bash(self, script: str, stdin: bytes | None = None):
        self.commands.append(script.strip())
        argv = ["bash", "-c", script] if stdin is None else ["bash", "-s"]
        proc = self._REAL_POPEN(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"HOME": str(self.root), "PATH": "/usr/bin:/bin"},
            cwd=str(self.root),
        )
        out, err = proc.communicate(stdin)
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)

    def install(self, monkeypatch, host, *, on_launch=None) -> None:
        """Point ``services.remote_runner`` at this host instead of a real one.

        ``on_launch`` receives the argv of each pipeline launch and returns the
        object ``Popen`` should hand back. The fake owns this seam rather than
        leaving it to the test, because patching it separately patches the
        *shared* ``subprocess`` module and would answer the fake's own shell
        calls with the test's mock.
        """
        import services.remote_runner as rr

        ssh_prefix = host.ssh_argv(allocate_tty=False)
        ssh_prefix_tty = host.ssh_argv(allocate_tty=True)
        real_run = subprocess.run

        def _run(cmd, *a, **kw):
            argv = list(cmd)
            for prefix in (ssh_prefix, ssh_prefix_tty):
                if argv[:len(prefix)] == list(prefix):
                    rest = argv[len(prefix):]
                    stdin = kw.get("input")
                    if rest[:2] == ["bash", "-s"] and stdin is not None:
                        text = (stdin.decode() if isinstance(stdin, bytes)
                                else str(stdin))
                        self.commands.append(text.strip())
                        out = self._bash(text, stdin=text.encode())
                        # `_ssh_script` decodes bytes itself.
                        return out
                    out = self._bash(" ".join(rest))
                    if kw.get("text"):
                        return subprocess.CompletedProcess(
                            argv, out.returncode,
                            out.stdout.decode(errors="replace"),
                            out.stderr.decode(errors="replace"))
                    return subprocess.CompletedProcess(
                        argv, out.returncode, out.stdout, out.stderr)
            return real_run(cmd, *a, **kw)

        def _rsync(cmd, **kw):
            """Move the bytes an rsync would have moved. No rsync here."""
            operands = [a for a in cmd[1:] if not a.startswith("-")]
            # Drop the transport arguments rsync takes with a value.
            operands = [a for a in operands if not a.startswith("ssh")]
            if len(operands) < 2:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            *sources, dest = operands
            self.transfers.append((list(sources), dest))
            dest_p = self._translate(dest, kw.get("cwd"))
            for src in sources:
                src_p = self._translate(src, kw.get("cwd"))
                if not src_p.exists():
                    continue
                if src_p.is_dir():
                    dest_p.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(src_p, dest_p / src_p.name,
                                    dirs_exist_ok=True)
                else:
                    dest_p.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_p, dest_p / src_p.name)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        def _popen(argv, *a, **kw):
            self.launched.append(list(argv))
            if on_launch is not None:
                return on_launch(list(argv))
            raise AssertionError(
                "the run launched a pipeline and the test supplied no "
                "on_launch to answer it")

        monkeypatch.setattr(rr.subprocess, "run", _run)
        monkeypatch.setattr(rr.subprocess, "Popen", _popen)
        monkeypatch.setattr(rr, "_rsync_with_retry", _rsync)
        monkeypatch.setattr(rr, "_rsync_run_cancellable",
                            lambda cmd, **kw: _rsync(cmd, **kw))

    def _translate(self, operand: str, cwd) -> Path:
        """``alias:path`` and ``~/path`` to a local path; anything else as-is."""
        if ":" in operand and not operand.startswith("/"):
            _alias, _, remote = operand.partition(":")
            return self.path(remote)
        p = Path(operand)
        if not p.is_absolute() and cwd:
            return Path(cwd) / p
        return p
