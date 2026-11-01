"""Stand-in ``ssh``, ``squeue`` and ``scancel`` on ``PATH``, so the real code runs its real commands.

The run's end-of-life checks — closing the scheduler job, confirming it closed, probing a silent run — are
shell scripts sent over ``ssh`` (``services.remote_jobs``, ``services.remote_liveness``). A stub that answers
``subprocess.run`` agrees with whatever the test author assumed the script prints; these run the script.

* the ``ssh`` stand-in drops every option and runs its last argument under ``bash`` with ``HOME`` set to the stand-in
  host's home — unless the host is *down* (:meth:`Host.go_down`), when it prints OpenSSH's own refusal and
  exits 255, the way a dropped network does.
* ``squeue -h -j <id> -o %T|%u`` answers from :attr:`Host.jobs`, and is silent with exit 1 for a job it does
  not list, as Slurm is for a finished one. Given a step id (``-j 4001.3``) it fails with "Invalid job id", as
  Slurm does; a step is asked about as ``squeue -h -s4001.3 -o %i`` and answers from :attr:`Host.steps`.
* ``scancel -u <user> <id>`` removes the job and records it in :attr:`Host.cancelled`; given a step id it removes
  that step only and leaves the job.

Installed by prepending a directory to ``PATH`` (``monkeypatch.setenv``). The ssh stand-in is not *named*
``ssh`` — ``conftest._no_real_network_tools`` refuses any spawn of a binary by that name — so
``remote_config.resolve_ssh_binary``, the one place ``RemoteHost.ssh_argv`` learns the binary's name, is
pointed at it; the argv around it is the real one.
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path

_SSH = r"""#!/bin/bash
if [ -e "$AW_STANDIN/down" ]; then
  echo "ssh: connect to host standin port 22: Connection timed out" >&2
  exit 255
fi
cmd="${@: -1}"
cd "$AW_STANDIN/home" && HOME="$AW_STANDIN/home" exec bash -c "$cmd"
"""

_SQUEUE = r"""#!/bin/bash
jid=""; fmt="%T"; step=""
while [ $# -gt 0 ]; do
  case "$1" in -j) jid="$2"; shift;; -o) fmt="$2"; shift;; -s*) step="${1#-s}";; esac; shift
done
if [ -n "$step" ]; then
  if ! grep -q "^${step%%.*} " "$AW_STANDIN/jobs" 2>/dev/null; then
    echo "slurm_load_job_step error: Invalid job id specified" >&2; exit 1
  fi
  grep -qx "$step" "$AW_STANDIN/steps" 2>/dev/null && echo "$step"
  exit 0
fi
case "$jid" in *.*) echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1;; esac
line=$(grep "^$jid " "$AW_STANDIN/jobs" 2>/dev/null | head -n1)
if [ -z "$line" ]; then echo "slurm_load_jobs error: Invalid job id specified" >&2; exit 1; fi
set -- $line
case "$fmt" in %u) echo "$3";; *) echo "$2";; esac
"""

_SCANCEL = r"""#!/bin/bash
jid="${@: -1}"
case "$jid" in *.*)
  if grep -qx "$jid" "$AW_STANDIN/steps" 2>/dev/null; then
    grep -vx "$jid" "$AW_STANDIN/steps" > "$AW_STANDIN/steps.tmp"; mv "$AW_STANDIN/steps.tmp" "$AW_STANDIN/steps"
    echo "$jid" >> "$AW_STANDIN/cancelled"; exit 0
  fi
  echo "scancel: error: Kill job error on job id $jid: Invalid job id specified" >&2; exit 1;;
esac
if grep -q "^$jid " "$AW_STANDIN/jobs" 2>/dev/null; then
  grep -v "^$jid " "$AW_STANDIN/jobs" > "$AW_STANDIN/jobs.tmp"; mv "$AW_STANDIN/jobs.tmp" "$AW_STANDIN/jobs"
  echo "$jid" >> "$AW_STANDIN/cancelled"; exit 0
fi
echo "scancel: error: Kill job error on job id $jid: Invalid job id specified" >&2; exit 1
"""


class Host:
    """The stand-in host: a home directory, a job table and a switch for the network."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.user = getpass.getuser()

    def write(self, remote_path: str, text: str) -> None:
        """Write a file at a ``~/``-relative path on the host."""
        path = self.home / remote_path.removeprefix("~/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def add_job(self, job_id: str, state: str = "RUNNING") -> None:
        with (self.root / "jobs").open("a") as f:
            f.write(f"{job_id} {state} {self.user}\n")

    def add_step(self, step_id: str) -> None:
        """A running step, ``<job>.<step>``, of a job already added."""
        with (self.root / "steps").open("a") as f:
            f.write(f"{step_id}\n")

    @property
    def steps(self) -> list[str]:
        path = self.root / "steps"
        return path.read_text().split() if path.exists() else []

    @property
    def jobs(self) -> dict[str, str]:
        path = self.root / "jobs"
        rows = [ln.split() for ln in path.read_text().splitlines()] if path.exists() else []
        return {r[0]: r[1] for r in rows if r}

    @property
    def cancelled(self) -> list[str]:
        path = self.root / "cancelled"
        return path.read_text().split() if path.exists() else []

    def go_down(self) -> None:
        (self.root / "down").touch()

    def come_back(self) -> None:
        (self.root / "down").unlink(missing_ok=True)


def install(tmp_path: Path, monkeypatch) -> Host:
    """Put the stand-ins first on ``PATH`` and return the host they answer for."""
    root = tmp_path / "standin"
    bindir = root / "bin"
    bindir.mkdir(parents=True)
    for name, body in (("aw-ssh-standin", _SSH), ("squeue", _SQUEUE), ("scancel", _SCANCEL)):
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    monkeypatch.setenv("AW_STANDIN", str(root))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    from services import remote_config

    monkeypatch.setattr(remote_config, "resolve_ssh_binary", lambda: str(bindir / "aw-ssh-standin"))
    return Host(root)
