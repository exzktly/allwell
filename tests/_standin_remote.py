"""Stand-in ``ssh`` and ``rsync`` binaries: the real fetch code, a local "remote", and failures on cue.

The fetch, verification and cleanup defects of ``_Docs/Markdowns/REMOTE_REVIEW_TRACKER.md`` section A were all
hidden by test doubles that *agree with the code*: a ``SimpleNamespace`` fetcher whose listing always succeeds, a
stub ``_rsync_optional`` whose ``None`` nobody read. A failed remote command read as "nothing there" is invisible
to any double that cannot fail. So these tests replace only the two binaries:

* **ssh** runs the remote command under a local ``bash`` — the script piped to ``bash -s``, or the joined argv —
  with ``HOME`` at the stand-in host's root, and exits **255** with ssh's own "Connection timed out" text whenever
  the script contains a pattern listed in ``ssh_fail`` (one per line; ``a && b`` needs both). That is exactly what a throttled login node
  or a dropped link looks like from the client.
* **rsync** maps every ``<alias>:<path>`` operand to the local path, expands a remote glob the way the remote shell
  would, and hands the rest to the real ``rsync``; it exits with the code given in ``rsync_fail``
  (``<code> <pattern>`` per line) for a source matching a pattern.

Every byte the code under test moves, every listing it makes and every ``rm`` it sends is real. The binaries are
named ``aw-standin-ssh`` / ``aw-standin-rsync`` and reached by patching the two resolvers, so the network guard in
``tests/conftest.py`` (which refuses a spawned ``ssh`` / ``rsync``) stays on: nothing here reaches another host.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from pathlib import Path

_SSH = r'''
import os, subprocess, sys
ctl = os.environ["AW_STANDIN_DIR"]
args = sys.argv[1:]
with_value = {"-o", "-i", "-F", "-p", "-l", "-E", "-c", "-m", "-b", "-J", "-L", "-R", "-D", "-S", "-W", "-Q", "-O"}
i = 0
while i < len(args) and args[i].startswith("-"):
    i += 2 if args[i] in with_value else 1
command = args[i + 1:]
if command[:2] == ["bash", "-s"]:
    script = sys.stdin.buffer.read().decode("utf-8", "replace")
else:
    script = " ".join(command)
with open(os.path.join(ctl, "ssh_log"), "a", encoding="utf-8") as fh:
    fh.write(script.replace("\n", "\\n") + "\n")
try:
    with open(os.path.join(ctl, "ssh_fail"), encoding="utf-8") as fh:
        patterns = [ln.rstrip("\n") for ln in fh if ln.strip()]
except FileNotFoundError:
    patterns = []
if any(all(part.strip() in script for part in p.split("&&")) for p in patterns):
    sys.stderr.write("ssh: connect to host standin port 22: Connection timed out\n")
    sys.exit(255)
root = os.environ["AW_STANDIN_ROOT"]
env = dict(os.environ, HOME=root)
sys.exit(subprocess.run(["bash", "-c", script], cwd=root, env=env).returncode)
'''

_RSYNC = r'''
import glob, os, subprocess, sys
ctl = os.environ["AW_STANDIN_DIR"]
args, out, sources = sys.argv[1:], [], []
i = 0
while i < len(args):
    a = args[i]
    if a == "-e":
        i += 2
        continue
    out.append(a)
    i += 1
if out == ["--version"]:
    sys.exit(subprocess.run([os.environ["AW_REAL_RSYNC"], "--version"]).returncode)
opts = [a for a in out if a.startswith("-")]
operands = [a for a in out if not a.startswith("-")]
mapped = []
for n, op in enumerate(operands):
    if ":" in op and not op.startswith("/"):
        path = op.split(":", 1)[1]
        if path.startswith("~/"):
            path = os.environ["AW_STANDIN_ROOT"] + path[1:]
        if n < len(operands) - 1:
            sources.append(path)
            hits = sorted(glob.glob(path)) if any(c in path for c in "*?[") else [path]
            mapped += hits or [path]
            continue
        op = path
    mapped.append(op)
with open(os.path.join(ctl, "rsync_log"), "a", encoding="utf-8") as fh:
    fh.write(" ".join(sources) + "\n")
try:
    with open(os.path.join(ctl, "rsync_fail"), encoding="utf-8") as fh:
        rules = [ln.rstrip("\n").split(" ", 1) for ln in fh if ln.strip()]
except FileNotFoundError:
    rules = []
for code, pattern in rules:
    if any(pattern in s for s in sources):
        sys.stderr.write(f"rsync: standin failure for {pattern} (code {code})\n")
        sys.exit(int(code))
sys.exit(subprocess.run([os.environ["AW_REAL_RSYNC"], *opts, *mapped]).returncode)
'''


class StandinHost:
    """A local directory standing in for the worker host, reached through stand-in ``ssh`` / ``rsync``."""

    def __init__(self, base: Path, monkeypatch) -> None:
        self.root = base / "host"
        self.ctl = base / "standin-ctl"
        bin_dir = base / "standin-bin"
        for d in (self.root, self.ctl, bin_dir):
            d.mkdir(parents=True, exist_ok=True)
        real_rsync = shutil.which("rsync")
        if real_rsync is None:
            import pytest
            pytest.skip("rsync is not installed")
        self.ssh = bin_dir / "aw-standin-ssh"
        self.rsync = bin_dir / "aw-standin-rsync"
        for path, body in ((self.ssh, _SSH), (self.rsync, _RSYNC)):
            path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
            path.chmod(0o755)
        monkeypatch.setenv("AW_STANDIN_DIR", str(self.ctl))
        monkeypatch.setenv("AW_STANDIN_ROOT", str(self.root))
        monkeypatch.setenv("AW_REAL_RSYNC", real_rsync)
        from services import remote_config, remote_fetch, remote_transport

        monkeypatch.setattr(remote_config, "resolve_ssh_binary", lambda: str(self.ssh))
        monkeypatch.setattr(remote_config, "resolve_ssh_for_rsync", lambda: str(self.ssh))
        for module in (remote_config, remote_fetch, remote_transport):
            monkeypatch.setattr(module, "resolve_rsync_binary", lambda: str(self.rsync))
        # No retry backoff: a stand-in failure is permanent until the test clears it.
        monkeypatch.setattr(remote_transport, "_RSYNC_RETRY_DELAYS", ())

    def host(self):
        from services.remote_config import RemoteHost

        return RemoteHost(
            name="standin", ssh_alias="standin", remote_project_dir="~/allwell",
            remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
            cpu_only=False, extra_args=(), cache_budget_gb=10, measured_bandwidth_mbps=100.0)

    def path(self, rel: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # ── failures on cue ─────────────────────────────────────────────────
    def fail_ssh(self, *patterns: str) -> None:
        """Every ssh whose remote script contains one of *patterns* exits 255; ``"a && b"`` needs both. No
        patterns clears it."""
        (self.ctl / "ssh_fail").write_text("".join(f"{p}\n" for p in patterns), encoding="utf-8")

    def fail_rsync(self, *patterns: str, code: int = 23) -> None:
        """Every rsync whose remote source contains one of *patterns* exits *code*. No patterns clears it."""
        (self.ctl / "rsync_fail").write_text("".join(f"{code} {p}\n" for p in patterns), encoding="utf-8")

    def ssh_scripts(self) -> list[str]:
        try:
            return (self.ctl / "ssh_log").read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

    def rsync_sources(self) -> list[str]:
        try:
            return (self.ctl / "rsync_log").read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []


def standin_env_ok() -> bool:
    return os.name == "posix" and shutil.which("rsync") is not None and shutil.which("bash") is not None
