"""Test a remote's scheduler command by running a probe through the real launch path.

Every scheduler problem the app has had was found by a plate run that failed
minutes or hours in: a launcher whose instance type srun ran as the program, a
job given one CPU of a sixteen-core node, a launch command a Windows client cut
short, a node that never booted. Each was invisible to the tests, because the
tests drive stand-in schedulers, and each was cheap to see on the real cluster.

So the Remotes dialog's **Test** button sends a job that does nothing but look
at where it landed, and it is sent the way a run is sent:
:func:`services.remote_commands._compose_remote_cmd` (conda activation, the
environment guard, the ``srun`` retry wrapper and its stray-word refusal),
:func:`~services.remote_commands.pack_for_login_shell`, and the host's own
``ssh -tt`` argv. Only the program differs — a short Python script, run in the
host's conda environment the way the pipeline is, in place of the pipeline. A
launch path that is broken for a run is broken here, in about a minute rather
than at the end of an upload.

The script prints ``[probe] key=value`` lines. :func:`parse_probe` reads them
back and :func:`summarize` turns them into the sentences the dialog shows,
with the same CPU-shortfall rule the pipeline logs
(:func:`pipeline.parallelism.slurm_cpu_shortfall`). Qt-free; the dialog runs
:class:`ProbeRun` on a worker thread.
"""

from __future__ import annotations

import secrets
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from pipeline.parallelism import CELLPOSE_WORKERS_PER_GPU, slurm_cpu_shortfall
from services.remote_commands import _compose_remote_cmd, pack_for_login_shell
from services.remote_conda import _conda_env_selector, _conda_run_prefix_for
from services.remote_config import RemoteHost
from services.subprocess_kill import own_process_group_kwargs, signal_subprocess
from services.subprocess_text import TEXT_UTF8

PROBE_PREFIX = "[probe] "

#: Runs in the host's conda environment on the node the scheduler granted.
#: Every fact is printed even when a later one fails, so a broken Torch still
#: reports the CPUs.
PROBE_SCRIPT = r'''
import os, platform, subprocess
def say(k, v):
    print("[probe] %s=%s" % (k, v), flush=True)
say("node", platform.node())
try:
    say("cpus", len(os.sched_getaffinity(0)))
except AttributeError:
    say("cpus", os.cpu_count())
say("node_cpus", os.cpu_count())
for k in ("SLURM_JOB_ID", "SLURM_CPUS_PER_TASK", "CUDA_VISIBLE_DEVICES"):
    say(k.lower(), os.environ.get(k, ""))
try:
    out = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=20).stdout
    for line in out.splitlines():
        if line.startswith("GPU "):
            say("gpu", line.strip())
except (OSError, subprocess.SubprocessError):
    say("nvidia_smi", "missing")
try:
    import torch
    say("torch", torch.__version__)
    say("torch_cuda", torch.cuda.is_available())
    say("torch_devices", torch.cuda.device_count() if torch.cuda.is_available() else 0)
except Exception as exc:
    say("torch", "not importable: %s" % exc)
try:
    from importlib.metadata import version
    say("cellpose", version("cellpose"))
except Exception:
    say("cellpose", "not installed")
say("done", 1)
'''


@dataclass
class ProbeResult:
    """What the probe reported, and how the launch ended."""

    returncode: int | None = None
    facts: dict[str, str] = field(default_factory=dict)
    gpus: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        return self.facts.get("done") == "1"


def parse_probe(lines: list[str], returncode: int | None) -> ProbeResult:
    result = ProbeResult(returncode=returncode, output=list(lines))
    for raw in lines:
        line = raw.strip()
        at = line.find(PROBE_PREFIX)
        if at < 0:
            continue
        key, _, value = line[at + len(PROBE_PREFIX):].partition("=")
        if key == "gpu":
            result.gpus.append(value)
        else:
            result.facts[key] = value
    return result


def _int(value: str | None) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def summarize(result: ProbeResult) -> tuple[bool, list[str]]:
    """``(passed, sentences)`` for the dialog."""
    f = result.facts
    if not result.completed:
        tail = [ln.rstrip() for ln in result.output if ln.strip()][-8:]
        return False, [
            f"✗ The test job did not finish (exit code {result.returncode}). "
            "The last lines it printed:",
            *tail,
        ]
    job = f.get("slurm_job_id") or ""
    lines = [f"✓ Ran on {f.get('node', '?')}" + (f" as Slurm job {job}." if job else
                                                  " (not inside a Slurm job).")]
    ok = True
    cpus, node_cpus = _int(f.get("cpus")), _int(f.get("node_cpus"))
    if cpus is not None:
        lines.append(f"CPUs: {cpus} of the node's {node_cpus or '?'}.")
        shortfall = slurm_cpu_shortfall(
            cpus, node_cpus=node_cpus if node_cpus is not None else cpus,
            env={"SLURM_JOB_ID": job,
                 "SLURM_CPUS_PER_TASK": f.get("slurm_cpus_per_task", "")})
        if shortfall:
            ok = False
            lines.append(f"⚠ {shortfall}")
    n_gpu = len(result.gpus)
    torch_cuda = f.get("torch_cuda") == "True"
    torch_devices = _int(f.get("torch_devices")) or 0
    if n_gpu:
        lines.append(f"GPUs on the node: {n_gpu} ({'; '.join(result.gpus)}).")
    else:
        lines.append("GPUs: none visible to the job.")
    torch = f.get("torch", "")
    if torch.startswith("not importable"):
        ok = False
        lines.append(f"⚠ Torch {torch} in the run's conda environment — "
                     "Cellpose cannot run. Re-run Bootstrap for this host.")
    elif n_gpu and not torch_cuda:
        ok = False
        lines.append(f"⚠ Torch {torch} cannot use the GPU (a CPU-only build), so "
                     "Cellpose will run on the CPU.")
    elif torch_cuda:
        lines.append(f"Cellpose can use {torch_devices} GPU(s): up to "
                     f"{CELLPOSE_WORKERS_PER_GPU * torch_devices} Cellpose "
                     f"worker(s) by default.")
    cellpose = f.get("cellpose", "")
    lines.append("Cellpose: " + (cellpose if cellpose != "not installed"
                                 else "not installed in the run's environment."))
    return ok, lines


class ProbeRun:
    """One test launch. :meth:`run` blocks (call it off the GUI thread);
    :meth:`cancel` may be called from any thread."""

    def __init__(self, host: RemoteHost) -> None:
        self.host = host
        selector, self.env_warning = _conda_env_selector(host.remote_python)
        argv = [*_conda_run_prefix_for(selector), "python", "-c", PROBE_SCRIPT]
        # A unique pidfile in /tmp on the login node: the launch writes it
        # before anything else, and a run's own staging directory may not
        # exist on a host that has never run.
        pidfile = f"/tmp/allwell-scheduler-test-{secrets.token_hex(4)}.pid"
        # Raises ValueError for a launcher with a loose word, as a run does.
        script = _compose_remote_cmd(host.compute_launcher, pidfile, argv,
                                     cache_hit=False, env_selector=selector)
        self.argv = host.ssh_argv(allocate_tty=True) + [pack_for_login_shell(script)]
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    def run(self, on_line: Callable[[str], None]) -> ProbeResult:
        lines: list[str] = []
        self._proc = subprocess.Popen(
            self.argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, **TEXT_UTF8, **own_process_group_kwargs())
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            lines.append(line)
            on_line(line)
        rc = self._proc.wait()
        return parse_probe(lines, None if self._cancelled else rc)

    def cancel(self) -> None:
        """End the ssh session. Its terminal closing hangs up ``srun``, which
        hands back a queued or running allocation."""
        self._cancelled = True
        signal_subprocess(self._proc)
