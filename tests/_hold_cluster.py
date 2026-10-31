"""A stand-in cluster for the granted-node tests: real scripts under ``bash`` with fake ``sbatch``, ``scontrol``,
``scancel``, ``sinfo`` and ``squeue`` programs on the PATH and a throwaway HOME as the "remote" home directory.

What the stand-ins do is driven by the test (:meth:`_Cluster.set_job`), usually through :class:`Script`, a ``sleep`` that
advances a fake clock and plays a list of job states, one per nap — so a wait of minutes takes no time, and the code
under test cannot tell.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from services.remote_config import RemoteHost
from services.remote_hold import hold_paths
from services.remote_transport import _remote_join

_SBATCH = r'''#!/bin/bash
n=$(ls "$FAKE/sbatch."*.args 2>/dev/null | wc -l)
if [ -e "$FAKE/sbatch.refuse" ]; then
    echo "sbatch: error: Batch job submission failed: Requested node configuration is not available" >&2
    exit 1
fi
printf '%s\n' "$@" > "$FAKE/sbatch.$((n+1)).args"
# Job ids are cluster-wide: a replacement job gets a new one, not the first job's. A test can set the next id.
id=$(cat "$FAKE/next_id" 2>/dev/null || echo $((4000 + n + 1)))
echo $((id + 1)) > "$FAKE/next_id"
mkdir -p "$FAKE/jobs"; echo "PENDING|None|" > "$FAKE/jobs/$id"
echo "$id"
'''
_SCONTROL = r'''#!/bin/bash
# scontrol show job -o ID
id="$4"
[ -e "$FAKE/jobs/$id" ] || exit 1
IFS='|' read -r state reason node < "$FAKE/jobs/$id"
echo "JobId=$id JobName=allwell-hold UserId=me(1) JobState=$state Reason=$reason NodeList=${node:-(null)}"
'''
_SCANCEL = r'''#!/bin/bash
echo "$@" >> "$FAKE/scancel.log"
for a in "$@"; do case "$a" in [0-9]*) echo "CANCELLED|None|" > "$FAKE/jobs/$a";; esac; done
'''
_SINFO = r'''#!/bin/bash
[ -e "$FAKE/sinfo.out" ] && cat "$FAKE/sinfo.out"
exit 0
'''
_SQUEUE = r'''#!/bin/bash
cat "$FAKE/squeue.owner" 2>/dev/null || id -un
'''


class Cluster:
    """The stand-in host: a bin directory, a state directory and a home directory, and a fake ``DatasetCache``."""

    def __init__(self, root: Path):
        self.root = root
        self.fake = root / "fake"
        self.home = root / "home"
        self.bin = root / "bin"
        for d in (self.fake, self.home, self.bin):
            d.mkdir()
        for name, text in (("sbatch", _SBATCH), ("scontrol", _SCONTROL), ("scancel", _SCANCEL),
                           ("sinfo", _SINFO), ("squeue", _SQUEUE)):
            (self.bin / name).write_text(text, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        self.env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.home),
                    "FAKE": str(self.fake)}

    # the DatasetCache surface GrantedNode and _UploadHeartbeat use
    def _ssh_script(self, script: str, *, check: bool, timeout_s: float = 60):
        return subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, env=self.env,
                              timeout=timeout_s, check=check)

    def _ssh_run(self, remote_argv, *, check: bool, timeout_s: float = 60):
        return subprocess.run(["bash", "-c", _remote_join(remote_argv)], capture_output=True, text=True,
                              env=self.env, timeout=timeout_s, check=check)

    # test controls
    def set_job(self, job: int, state: str, reason: str = "None", node: str = "") -> None:
        (self.fake / "jobs").mkdir(exist_ok=True)
        (self.fake / "jobs" / str(job)).write_text(f"{state}|{reason}|{node}\n", encoding="utf-8")

    def sbatch_calls(self) -> list[list[str]]:
        return [p.read_text(encoding="utf-8").splitlines() for p in sorted(self.fake.glob("sbatch.*.args"))]

    def scancels(self) -> list[str]:
        log = self.fake / "scancel.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def hold_dir(self, host: RemoteHost) -> Path:
        return Path(str(self.home / hold_paths(host).dir.removeprefix("~/")))



HOST = dict(name="gpu", ssh_alias="login", remote_staging_dir="~/stage",
            compute_launcher="srun --partition=gpu --gres=gpu:1 --cpus-per-task=8 --time=4:00:00")


class Script:
    """A ``sleep`` that advances a fake clock and plays a list of job states, one per nap."""

    def __init__(self, cluster, steps, job=4001):
        self.cluster, self.steps, self.job, self.now, self.naps = cluster, list(steps), job, 0.0, []

    def clock(self) -> float:
        return self.now

    def __call__(self, seconds: float) -> None:
        self.now += seconds
        self.naps.append(seconds)
        assert len(self.naps) < 80, "the request kept waiting; it should have given up or been granted"
        if self.steps:
            step = self.steps.pop(0)
            if callable(step):
                step()
            else:
                self.cluster.set_job(self.job, *step)


def granted_files(cluster, host, node="gpu-node-1", gpus="NVIDIA A10G", *, granted_at=None, limit_s=86400, job=4001):
    """What the hold script writes when its node is granted."""
    import time as _time

    d = cluster.hold_dir(host)
    d.mkdir(parents=True, exist_ok=True)
    at = int(_time.time()) if granted_at is None else granted_at
    (d / "granted").write_text(f"node={node}\ngpus={gpus}\ncpus=8\ngranted_at={at}\nlimit_s={limit_s}\njob={job}\n",
                               encoding="utf-8")
