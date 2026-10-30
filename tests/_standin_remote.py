"""Stand-in ``ssh`` and ``rsync`` binaries: the real fetch code, a local "remote", and failures on cue.

The fetch, verification and cleanup defects of the remote pipeline review (whose tracker is in git history) section A were all
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

Faults can also be rules (:meth:`StandinHost.ssh_rule`, :meth:`StandinHost.rsync_rule`): the *n*th matching call
fails, hangs or is dropped, and every firing is logged.

:class:`StandinCluster` makes the host a Slurm login node with a conda env — stand-in ``srun`` / ``sinfo`` /
``squeue`` / ``scancel`` / ``conda`` — and rsync the real one at both ends, so a whole run goes through the app and
the remote side as a pair: :func:`start_run` drives ``PipelineRunner.start``, and
:func:`assert_correct_or_visibly_failed` is the rule every scenario is held to (ARCHITECTURE §10.8).
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from pathlib import Path

#: Faults on cue, shared by both stand-ins. A rules file holds one rule per line,
#: ``<action> <skip> <count> <pattern>``: the first rule whose pattern matches (every ``&&``-separated part is
#: in the subject; an empty pattern matches everything) and whose count is not spent takes the call — passing it
#: through while ``skip`` lasts, then firing ``action`` ``count`` times (``-1``: for ever). Each firing is
#: appended to ``<rules file>.fired``, so a test can tell a fault that fired from one that never did.
_RULES = r'''
import fcntl as _fcntl, os as _os


def take_rule(ctl, name, subject):
    path = _os.path.join(ctl, name)
    try:
        fh = open(path, "r+", encoding="utf-8")
    except FileNotFoundError:
        return None
    hit = None
    with fh:
        _fcntl.flock(fh, _fcntl.LOCK_EX)
        rules = [ln.rstrip("\n").split(" ", 3) for ln in fh if ln.strip()]
        for rule in rules:
            action, skip, count = rule[0], int(rule[1]), int(rule[2])
            pattern = rule[3] if len(rule) > 3 else ""
            if count == 0 or not all(part.strip() in subject for part in pattern.split("&&")):
                continue
            if skip > 0:
                rule[1] = str(skip - 1)
            else:
                hit = action
                if count > 0:
                    rule[2] = str(count - 1)
            break
        fh.seek(0)
        fh.truncate()
        fh.write("".join(" ".join(r) + "\n" for r in rules))
    if hit is not None:
        with open(path + ".fired", "a", encoding="utf-8") as log:
            log.write(hit + " " + subject.replace("\n", "\\n")[:400] + "\n")
    return hit
'''

_SSH = _RULES + r'''
import os, subprocess, sys, time
ctl = os.environ["AW_STANDIN_DIR"]
args = sys.argv[1:]
with_value = {"-o", "-i", "-F", "-p", "-l", "-E", "-c", "-m", "-b", "-J", "-L", "-R", "-D", "-S", "-W", "-Q", "-O"}
i = 0
while i < len(args) and args[i].startswith("-"):
    i += 2 if args[i] in with_value else 1
command = args[i + 1:]
stdin_bytes = None
if command[:2] == ["bash", "-s"]:
    stdin_bytes = sys.stdin.buffer.read()
    script = stdin_bytes.decode("utf-8", "replace")
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
action = take_rule(ctl, "ssh_rules", script)
if action == "fail":
    sys.stderr.write("ssh: connect to host standin port 22: Connection timed out\n")
    sys.exit(255)
if action == "hang":
    with open(os.path.join(ctl, "hung_pids"), "a", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()}\n")
    time.sleep(float(os.environ.get("AW_STANDIN_HANG_S", "60")))
if action == "drop":
    sys.exit(0)
root = os.environ["AW_STANDIN_ROOT"]
env = dict(os.environ, HOME=root)
if os.environ.get("AW_STANDIN_PATH"):
    # A cluster stand-in: the host's own PATH (its scheduler and conda first), and none of this
    # machine's Python environment, which a worker node would not have either.
    env["PATH"] = os.environ["AW_STANDIN_PATH"]
    for name in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME"):
        env.pop(name, None)
argv = ["bash", "-s"] if stdin_bytes is not None else ["bash", "-c", script]
sys.exit(subprocess.run(argv, cwd=root, env=env, input=stdin_bytes).returncode)
'''

_RSYNC = _RULES + r'''
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
code = take_rule(ctl, "rsync_rules", " ".join(sources))
if code is not None:
    sys.stderr.write(f"rsync: connection unexpectedly closed (standin, code {code})\n")
    sys.exit(int(code))
if os.environ.get("AW_STANDIN_RSYNC_VIA_SSH"):
    # The real rsync with the app's own `-e`, which names the ssh stand-in: the far end is a real
    # `rsync --server` started by the remote shell, so `~`, globs and paths resolve there.
    # `--log-file` records what each transfer moved, in a file of this harness's own; the app's argv and the
    # output it parses are left exactly as they are.
    sys.exit(subprocess.run([os.environ["AW_REAL_RSYNC"], f"--log-file={os.path.join(ctl, 'rsync_moved')}",
                             "--log-file-format=%o %f", *args]).returncode)
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
        import importlib

        from services import remote_config, remote_transport

        monkeypatch.setattr(remote_config, "resolve_ssh_binary", lambda: str(self.ssh))
        monkeypatch.setattr(remote_config, "resolve_ssh_for_rsync", lambda: str(self.ssh))
        # Every module that imported the resolver by name holds its own reference, so each is patched: the code
        # sync and the upload build their rsync argv in modules of their own.
        for name in ("remote_config", "remote_fetch", "remote_transport", "remote_code_sync", "remote_upload",
                     "remote_plate_grids"):
            module = importlib.import_module(f"services.{name}")
            if hasattr(module, "resolve_rsync_binary"):
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

    def ssh_rule(self, action: str, pattern: str = "", *, skip: int = 0, count: int = 1) -> None:
        """Add a fault for ssh calls whose remote script contains *pattern* (``"a && b"`` needs both; empty
        matches every call): after *skip* matching calls pass, the next *count* (``-1``: all) *action* —
        ``fail`` (exit 255 with ssh's own text), ``hang`` (answer nothing for ``AW_STANDIN_HANG_S``) or
        ``drop`` (exit 0 without running anything: a command lost on the way that reported success)."""
        with (self.ctl / "ssh_rules").open("a", encoding="utf-8") as fh:
            fh.write(f"{action} {skip} {count} {pattern}\n")

    def rsync_rule(self, code: int, pattern: str = "", *, skip: int = 0, count: int = 1) -> None:
        """Add a fault for rsyncs whose remote operands contain *pattern*: after *skip* pass, the next *count*
        exit *code* without moving anything."""
        with (self.ctl / "rsync_rules").open("a", encoding="utf-8") as fh:
            fh.write(f"{code} {skip} {count} {pattern}\n")

    def fired(self, kind: str = "ssh") -> list[str]:
        """Every fault that fired, ``<action> <subject>``, in order — so a test can prove its fault happened."""
        try:
            return (self.ctl / f"{kind}_rules.fired").read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

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


# ── a cluster: the scheduler, conda, and the pipeline itself ─────────────────────────────────────────────────
#
# `StandinCluster` makes the stand-in host a login node in front of a Slurm cluster, so a remote run can be driven
# end to end through the app's real code and the real remote side: the packed login-shell command, the `srun`
# wrapper (`services.remote_srun_retry`), the batch driver (`services.remote_commands`), the conda env guard, and
# the real `process_microscopy.py` from the code directory the run itself synced. The stand-ins below are the
# only modelled parts, and each models one program's interface, not its effects.

#: The job table every scheduler stand-in reads and writes: ``<id> <state> <user> <pgid>`` per line, under a lock.
_JOBS = r'''
import fcntl as _fcntl, os as _os


def jobs_edit(fn):
    """Call ``fn(rows)`` with the table's rows under the lock; what it leaves in *rows* is written back."""
    path = _os.path.join(_os.environ["AW_CLUSTER_DIR"], "jobs")
    with open(path, "a+", encoding="utf-8") as fh:
        _fcntl.flock(fh, _fcntl.LOCK_EX)
        fh.seek(0)
        rows = [ln.split() for ln in fh.read().splitlines() if ln.strip()]
        out = fn(rows)
        fh.seek(0)
        fh.truncate()
        fh.write("".join(" ".join(r) + "\n" for r in rows))
        return out
'''

#: ``srun [options] bash -lc <script>``: a job id, Slurm's own "queued" / "allocated" lines, the job's
#: environment, and the command run in a process group of its own that ``scancel`` (or a node failure) ends.
#: ``--test-only`` answers "starts now" and queues nothing. The node's login profile is not this machine's, so
#: ``bash -l`` runs with ``--noprofile``.
_SRUN = _JOBS + r'''
import getpass, os, signal, subprocess, sys, time
args = sys.argv[1:]
if "--test-only" in args:
    sys.stderr.write("srun: Job 1 to start at " + time.strftime("%Y-%m-%dT%H:%M:%S")
                     + " using 1 processors on nodes standin-node1 in partition cpu\n")
    sys.exit(0)
if "bash" not in args:
    sys.stderr.write("srun: error: the stand-in only runs `bash ...`\n")
    sys.exit(2)
cmd = args[args.index("bash"):]
if cmd[1:2] == ["-lc"]:
    cmd = ["bash", "--noprofile", "-l", "-c", *cmd[2:]]
partition = "cpu"
for n, a in enumerate(args):
    if a in ("-p", "--partition") and n + 1 < len(args):
        partition = args[n + 1]
    elif a.startswith("--partition="):
        partition = a.split("=", 1)[1]
cluster = os.environ["AW_CLUSTER_DIR"]


def _next_id(_rows):
    path = os.path.join(cluster, "next_id")
    jid = int(open(path).read()) if os.path.exists(path) else 4001
    open(path, "w").write(str(jid + 1))
    return str(jid)


jid = jobs_edit(_next_id)
sys.stdout.write(f"srun: job {jid} queued and waiting for resources\n")
sys.stdout.write(f"srun: job {jid} has been allocated resources\n")
sys.stdout.flush()
env = dict(os.environ, SLURM_JOB_ID=jid, SLURMD_NODENAME="standin-node1", SLURM_JOB_PARTITION=partition)
try:
    import ctypes
    ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)        # PR_SET_CHILD_SUBREAPER (Linux; elsewhere a no-op)
except (OSError, AttributeError):
    pass
child = subprocess.Popen(cmd, env=env, start_new_session=True)
jobs_edit(lambda rows: rows.append([jid, "RUNNING", getpass.getuser(), str(child.pid)]))


def _forward(_signum, _frame):
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(sig, _forward)
# Reap every process of the job as it exits, as slurmstepd does: the job's descendants are re-parented here
# (a subreaper), not to this machine's PID 1, which in a container may leave them as zombies that `kill -0`
# still answers for — and the batch driver would then report a pipeline that outlived its batch.
while True:
    pid, status = os.waitpid(-1, 0)
    if pid == child.pid:
        rc = os.waitstatus_to_exitcode(status)
        break
jobs_edit(lambda rows: rows.__setitem__(slice(None), [r for r in rows if r[0] != jid]))
if os.path.exists(os.path.join(cluster, f"node_failed_{jid}")):
    sys.stdout.write("srun: error: Node failure on standin-node1\n")
    sys.stdout.write("srun: error: Job step aborted: Waiting up to 32 seconds for job step to finish.\n")
    sys.stdout.flush()
    sys.exit(1)
if os.path.exists(os.path.join(cluster, f"cancelled_{jid}")):
    sys.stdout.write(f"slurmstepd: error: *** STEP {jid}.0 ON standin-node1 CANCELLED AT "
                     + time.strftime("%Y-%m-%dT%H:%M:%S") + " ***\n")
    sys.stdout.flush()
sys.exit(rc if rc >= 0 else 128 - rc)
'''

#: ``squeue -h [-j <id>] [-u <user>] [-t <states>] -o <%T|%u|%i>``; silent with exit 1 for a job id it does not
#: list, as Slurm is for one that has ended.
_SQUEUE = _JOBS + r'''
import sys
args, jid, fmt, states, user = sys.argv[1:], None, "%T", None, None
i = 0
while i < len(args):
    a = args[i]
    if a in ("-j", "-o", "-t", "-u") and i + 1 < len(args):
        val = args[i + 1]
        if a == "-j":
            jid = val
        elif a == "-o":
            fmt = val
        elif a == "-t":
            states = val
        else:
            user = val
        i += 2
        continue
    i += 1
rows = jobs_edit(lambda rows: list(rows))
if jid is not None:
    rows = [r for r in rows if r[0] == jid]
    if not rows:
        sys.stderr.write("slurm_load_jobs error: Invalid job id specified\n")
        sys.exit(1)
if user is not None:
    rows = [r for r in rows if r[2] == user]
if states is not None:
    short = {"RUNNING": "R", "PENDING": "PD"}
    rows = [r for r in rows if r[1] in states.split(",") or short.get(r[1]) in states.split(",")]
for r in rows:
    print(fmt.replace("%T", r[1]).replace("%u", r[2]).replace("%i", r[0]))
'''

#: ``scancel [-u <user>] <id>…``: ends each job's process group, which is what makes its ``srun`` return.
_SCANCEL = _JOBS + r'''
import os, signal, sys
args = sys.argv[1:]
ids = [a for n, a in enumerate(args) if not a.startswith("-") and (n == 0 or args[n - 1] != "-u")]
rc = 0
for jid in ids:
    found = jobs_edit(lambda rows: [r for r in rows if r[0] == jid])
    if not found:
        sys.stderr.write(f"scancel: error: Kill job error on job id {jid}: Invalid job id specified\n")
        rc = 1
        continue
    open(os.path.join(os.environ["AW_CLUSTER_DIR"], f"cancelled_{jid}"), "w").close()
    with open(os.path.join(os.environ["AW_CLUSTER_DIR"], "cancelled"), "a") as fh:
        fh.write(jid + "\n")
    try:
        os.killpg(int(found[0][3]), signal.SIGTERM)
    except ProcessLookupError:
        pass
sys.exit(rc)
'''

_SINFO = r'''
print("standin-node1 idle")
'''

#: ``conda env list`` / ``conda info --base`` / ``conda run --no-capture-output -n <name>|-p <prefix> <cmd>…``
#: against the envs under ``$HOME/miniforge3``.
_CONDA = r'''
import os, sys
base = os.path.join(os.environ["HOME"], "miniforge3")
envs = os.path.join(base, "envs")
args = sys.argv[1:]
if args[:2] == ["env", "list"]:
    print("# conda environments:\n#")
    print(f"base                     {base}")
    for name in sorted(os.listdir(envs)) if os.path.isdir(envs) else ():
        print(f"{name:<24} {os.path.join(envs, name)}")
    sys.exit(0)
if args[:2] == ["info", "--base"]:
    print(base)
    sys.exit(0)
if args[:1] == ["run"]:
    rest, prefix = args[1:], None
    while rest and rest[0].startswith("-"):
        flag = rest.pop(0)
        if flag == "-n":
            prefix = os.path.join(envs, rest.pop(0))
        elif flag == "-p":
            prefix = rest.pop(0)
    if not prefix or not os.path.isfile(os.path.join(prefix, "conda-meta", "history")):
        sys.stderr.write(f"EnvironmentLocationNotFound: Not a conda environment: {prefix}\n")
        sys.exit(1)
    os.environ["PATH"] = os.path.join(prefix, "bin") + os.pathsep + os.environ.get("PATH", "")
    os.execvp(rest[0], rest)
sys.stderr.write(f"conda stand-in: {args!r} is not modelled\n")
sys.exit(2)
'''


#: For a nanowell run on the node, inside the node's own interpreter (and every interpreter its spawn pool
#: starts), the substitutions the in-process nanowell tests make, acting only when the environment asks:
#:
#: * ``AW_NODE_SEGMENTER_FROM`` (the checkout): Cellpose is not installed here, so its segmenter becomes the
#:   suite's deterministic stand-in (``tests/_nanowell_run_fixture.threshold_segmenter``), as
#:   ``tests/test_nanowell_cli.py`` does;
#: * ``AW_NODE_BLIND`` (``<well>:<timepoint>``): that one (well, timepoint) finds neither its square nor its
#:   boundary, so the end-of-run re-anchoring has to place it from the rest of the plate — the patch
#:   ``tests/test_nanowell_reanchor.py`` makes to ``locate_timepoint`` and ``resolve_origin``.
_NODE_SITECUSTOMIZE = r'''
import importlib.abc
import importlib.machinery
import os
import sys

_FROM = os.environ.get("AW_NODE_SEGMENTER_FROM")
_BLIND = tuple(os.environ.get("AW_NODE_BLIND", "").split(":")) if os.environ.get("AW_NODE_BLIND") else None
# Per thread: a nanowell worker locates its timepoints on several threads at once, and locate_timepoint and the
# resolve_origin it calls run on one thread, so a flag shared by the process would blind whichever timepoint
# resolves next rather than the one asked for.
_STATE = __import__("threading").local()


def _segmenter(module):
    sys.path.append(_FROM)
    from tests._nanowell_run_fixture import threshold_segmenter

    module.cellpose_segmenter = lambda *a, **k: threshold_segmenter()


def _locate(module):
    real = module.locate_timepoint

    def locate(tp, record, well, *args, **kwargs):
        _STATE.blind = (well, tp) == _BLIND
        return real(tp, record, well, *args, **kwargs)

    module.locate_timepoint = locate


def _resolve(module):
    real = module.resolve_origin

    def resolve(fit, *, fiducial=None, array_centre=None, plate_rigid=None, **rest):
        if getattr(_STATE, "blind", False):
            fiducial = array_centre = None
        return real(fit, fiducial=fiducial, array_centre=array_centre, plate_rigid=plate_rigid, **rest)

    module.resolve_origin = resolve


_PATCHES = {}
if _FROM:
    _PATCHES["pipeline.nanowell_measure"] = _segmenter
if _BLIND:
    _PATCHES["pipeline.nanowell_run"] = _locate
    _PATCHES["pipeline.nanowell_lattice"] = _resolve


class _PatchOnImport(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        patch = _PATCHES.pop(name, None)
        if patch is None:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is None:
            return None
        run = spec.loader.exec_module

        def exec_module(module):
            run(module)
            patch(module)

        spec.loader.exec_module = exec_module
        return spec


if _PATCHES:
    sys.meta_path.insert(0, _PatchOnImport())
'''


class StandinCluster(StandinHost):
    """The stand-in host as a Slurm login node, with a conda env that runs the real pipeline.

    * The host's ``PATH`` holds stand-in ``srun`` / ``sinfo`` / ``squeue`` / ``scancel`` / ``conda`` and the
      system's own tools; none of this machine's Python environment reaches it.
    * ``~/miniforge3/etc/profile.d/conda.sh`` exists, so the app's own activation snippet finds conda, and
      ``~/miniforge3/envs/allwell`` is a conda environment (``conda-meta/history``) whose ``python`` is the
      interpreter running this suite. That interpreter has no ``imagecodecs``, which the env guard imports, so
      an empty one stands in for it; the synthetic tiles are uncompressed and nothing decodes through it.
    * rsync is the real one at both ends: the app's ``-e`` names the ssh stand-in, which starts a real
      ``rsync --server`` in the host's shell.

    :meth:`host` is a host with an ``srun`` launcher. Faults come from :meth:`ssh_rule`, :meth:`rsync_rule`
    and :meth:`node_failure`; :meth:`reap` ends whatever a test left running.
    """

    def __init__(self, base: Path, monkeypatch) -> None:
        super().__init__(base, monkeypatch)
        self.cluster = base / "cluster"
        self.cluster.mkdir(parents=True, exist_ok=True)
        node_bin = base / "node-bin"
        node_bin.mkdir(parents=True, exist_ok=True)
        for name, body in (("srun", _SRUN), ("squeue", _SQUEUE), ("scancel", _SCANCEL),
                           ("sinfo", _SINFO), ("conda", _CONDA)):
            path = node_bin / name
            path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
            path.chmod(0o755)
        conda_sh = self.root / "miniforge3" / "etc" / "profile.d" / "conda.sh"
        conda_sh.parent.mkdir(parents=True, exist_ok=True)
        # What a real conda.sh does that matters here: `conda` is callable afterwards, whatever PATH the node's
        # login shell started with.
        conda_sh.write_text(f'export PATH="{node_bin}:$PATH"\n', encoding="utf-8")
        self.site = base / "node-site"
        self.site.mkdir(parents=True, exist_ok=True)
        (self.site / "imagecodecs.py").write_text('"""Stand-in: the harness env has no imagecodecs."""\n',
                                                  encoding="utf-8")
        self.env_prefix = self.root / "miniforge3" / "envs" / "allwell"
        (self.env_prefix / "conda-meta").mkdir(parents=True, exist_ok=True)
        (self.env_prefix / "conda-meta" / "history").write_text("==> standin <==\n", encoding="utf-8")
        (self.env_prefix / "bin").mkdir(parents=True, exist_ok=True)
        python = self.env_prefix / "bin" / "python"
        python.write_text(
            "#!/bin/sh\n"
            f'PYTHONPATH="{self.site}" exec "{sys.executable}" "$@"\n', encoding="utf-8")
        python.chmod(0o755)
        monkeypatch.setenv("AW_CLUSTER_DIR", str(self.cluster))
        monkeypatch.setenv("AW_STANDIN_PATH", f"{node_bin}{os.pathsep}/usr/bin{os.pathsep}/bin")
        monkeypatch.setenv("AW_STANDIN_RSYNC_VIA_SSH", "1")
        monkeypatch.setenv("AW_STANDIN_HANG_S", "30")
        from services import remote_transport

        # The transport's own retry, kept but short: a fault that fires once is what it exists for.
        monkeypatch.setattr(remote_transport, "_RSYNC_RETRY_DELAYS", (0.2, 0.4, 0.8))

    def segment_nanowells_with_a_stand_in(self, monkeypatch, *, blind: str = "") -> None:
        """Make the node's pipeline count nanowell cells with the suite's threshold stand-in (no Cellpose here),
        and, with *blind* (``"<well>:<timepoint>"``), leave that one unable to anchor itself."""
        (self.site / "sitecustomize.py").write_text(textwrap.dedent(_NODE_SITECUSTOMIZE), encoding="utf-8")
        monkeypatch.setenv("AW_NODE_SEGMENTER_FROM", str(Path(__file__).resolve().parent.parent))
        if blind:
            monkeypatch.setenv("AW_NODE_BLIND", blind)

    def host(self, **overrides):
        from services.remote_config import RemoteHost

        fields = dict(name="standin", ssh_alias="standin", remote_project_dir="~/allwell",
                      remote_staging_dir="~/.allwell-staging", cpu_only=False, extra_args=(),
                      cache_budget_gb=10, measured_bandwidth_mbps=100.0,
                      compute_launcher="srun -p cpu --time=1:00:00")
        fields.update(overrides)
        return RemoteHost(**fields)

    def uploaded(self) -> list[str]:
        """The name of every file an rsync actually sent to the host, in order — what crossed the wire, not
        what the app asked for: a file the host already held is in rsync's argv and not here."""
        path = self.ctl / "rsync_moved"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        return [ln.rsplit(" send ", 1)[1].rsplit("/", 1)[-1] for ln in lines if " send " in ln]

    # ── the scheduler ───────────────────────────────────────────────────
    def jobs(self) -> list[list[str]]:
        """The job table: ``[id, state, user, pgid]`` per running job."""
        path = self.cluster / "jobs"
        return [ln.split() for ln in path.read_text().splitlines() if ln.strip()] if path.exists() else []

    def cancelled(self) -> list[str]:
        path = self.cluster / "cancelled"
        return path.read_text().split() if path.exists() else []

    def node_failure(self) -> list[str]:
        """Kill every running job's processes outright, as a node that died does; its ``srun`` then says
        ``Node failure``. Returns the ids of the jobs it killed."""
        import signal

        killed = []
        for jid, _state, _user, pgid in self.jobs():
            (self.cluster / f"node_failed_{jid}").touch()
            try:
                os.killpg(int(pgid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            killed.append(jid)
        return killed

    def reap(self) -> None:
        """End whatever is still running on the cluster, and every ssh a ``hang`` rule left hanging; for a
        test's teardown."""
        import signal

        hung = self.ctl / "hung_pids"
        for pid in (hung.read_text().split() if hung.exists() else ()):
            try:
                os.kill(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        for _jid, _state, _user, pgid in self.jobs():
            try:
                os.killpg(int(pgid), signal.SIGKILL)
            except ProcessLookupError:
                pass


# ── a field-mode plate ───────────────────────────────────────────────────────────────────────────────────────

#: Six wells in batches of two: three batches, so a fault can land before, during and after the middle one.
FIELD_WELLS = ("A01", "A02", "A03", "B01", "B02", "B03")


def _blobs_tif(seed: int) -> bytes:
    """A 64×64 frame with three bright blobs on a dim, noisy background: enough for Otsu + watershed."""
    import io

    import numpy as np
    import tifffile

    rng = np.random.default_rng(seed)
    img = np.full((64, 64), 100, np.uint16)
    for cy, cx in ((16, 16), (16, 44), (44, 30)):
        img[cy - 5:cy + 5, cx - 5:cx + 5] = 5000
    img = (img + rng.integers(0, 50, (64, 64)).astype(np.uint16)).astype(np.uint16)
    buf = io.BytesIO()
    tifffile.imwrite(buf, img)
    return buf.getvalue()


def field_plate(root: Path, wells=FIELD_WELLS) -> tuple[Path, Path]:
    """``(in, out)``: one archive per well, a nuclear (NIR) and a GFP frame each, and an output folder to be."""
    import zipfile

    inp, out = root / "plate" / "in", root / "plate" / "out"
    inp.mkdir(parents=True)
    for n, well in enumerate(wells):
        with zipfile.ZipFile(inp / f"{well}.zip", "w") as zf:
            zf.writestr(f"exp_NIR_{well}_1_T01.tif", _blobs_tif(n))
            zf.writestr(f"exp_GFP_{well}_1_T01.tif", _blobs_tif(n + 100))
    return inp, out


def field_opts(**extra) -> dict:
    """The Analyze form's options for :func:`field_plate`, batched two wells at a time, segmented by
    ``classical_threshold`` — the one method that needs no model, so the real pipeline runs here."""
    return {"filename_schema": "experiment:channel:well:fov:timepoint", "filename_sep": "_",
            "nuclear_token": "NIR", "fluor_tokens": ["GFP"], "csv_prefix": "measurements",
            "segmentation_method": "classical_threshold", "remote_batch_size": 2, **extra}


# ── driving a run against the cluster ────────────────────────────────────────────────────────────────────────

def shorten_deadlines(monkeypatch) -> None:
    """The app's own deadlines at smaller numbers, so a fault ends a scenario in seconds rather than minutes.

    Each is the real mechanism: the bound on a control command and on a script a hung ssh runs into, the listing's
    bound, the hand-off's retry pause and acknowledgement windows, and the driver's poll for its ready flag. The
    first three are defaults bound when their functions were defined, so they are changed where they live.
    """
    from services import remote_commands, remote_handoff, remote_listing
    from services.remote_upload import DatasetCache

    monkeypatch.setitem(DatasetCache._ssh_run.__kwdefaults__, "timeout_s", 4.0)
    monkeypatch.setitem(DatasetCache._ssh_script.__kwdefaults__, "timeout_s", 6.0)
    monkeypatch.setitem(remote_listing.list_remote_glob.__kwdefaults__, "timeout", 6.0)
    monkeypatch.setattr(remote_handoff, "FLAG_RETRY_S", 0.3)
    monkeypatch.setattr(remote_handoff, "FLAG_CONFIRM_S", 4.0)
    monkeypatch.setattr(remote_handoff, "ACK_TIMEOUT_S", 6.0)
    monkeypatch.setattr(remote_commands, "UPLOAD_WAIT_POLL_S", 0.2)


class StandinRun:
    """One run through ``PipelineRunner.start``, as the Analyze tab starts it, and every event it queued."""

    def __init__(self, runner, events, out: Path) -> None:
        self.runner, self.events, self.out = runner, events, out

    @property
    def text(self) -> str:
        return "".join(str(p) for k, p in self.events if k in ("line", "done", "error"))

    @property
    def ending(self) -> tuple[str, str]:
        """The run's one control event: ``("done", …)``, ``("error", …)`` or ``("suspended", …)``."""
        ends = [(k, str(p)) for k, p in self.events if k in ("done", "error", "suspended")]
        assert len(ends) == 1, f"a run must end exactly once; it ended {ends}\n{self.text[-4000:]}"
        return ends[0]


def start_run(cluster: StandinCluster, inp: Path, out: Path, stop_fetchers, opts: dict, *, cleanup: str = "run",
              on_line=None, limit_s: float = 60.0) -> StandinRun:
    """Start a run the way the Analyze tab does — ``PipelineRunner.start`` with the cluster's host — and drain its
    queue until it says ``finished``; a run that has not ended within *limit_s* fails the test as a stall.

    *on_line* sees each line as it arrives, on the test's thread, so a test can act mid-run (a Stop, a node
    failure). *stop_fetchers* is the conftest fixture, handed the run's ``RemoteRunner`` once it exists.
    """
    import queue
    import time

    import services.pipeline_runner as pr

    runner = pr.PipelineRunner()
    events: list[tuple[str, object]] = []
    runner.start(Path("process_microscopy.py"), opts,
                 resolve_dirs=lambda _opts, _q, proc_hook=None: (inp, out),
                 remote=cluster.host(), output_filter="full", cleanup_remote=cleanup,
                 journal_spec={"opts": opts})
    deadline = time.monotonic() + limit_s
    while True:
        if runner._remote_runner is not None:
            stop_fetchers(runner._remote_runner)
        assert time.monotonic() < deadline, (
            f"the run had not ended {limit_s:.0f}s after it started — a stall\n"
            + "".join(str(p) for k, p in events if k == "line")[-4000:])
        try:
            kind, payload = runner.log_q.get(timeout=0.5)
        except queue.Empty:
            continue
        events.append((kind, payload))
        if kind == "line" and on_line is not None:
            on_line(str(payload), runner)
        if kind == "finished":
            break
    runner._thread.join(timeout=30)
    assert not runner._thread.is_alive(), "the run said finished and its worker is still running"
    return StandinRun(runner, events, out)


# ── what a run left, and the rule it is held to ──────────────────────────────────────────────────────────────

def complete_here(out: Path, wells) -> set[str]:
    """The wells of *wells* whose archive is here and whose CSV is here with rows in it."""
    import gzip
    import zipfile

    found = set()
    for well in wells:
        archive, csv = out / f"{well}_out.zip", out / f"measurements_{well}.csv.gz"
        rows = gzip.decompress(csv.read_bytes()).decode().splitlines()[1:] if csv.is_file() else []
        if archive.is_file() and zipfile.is_zipfile(archive) and rows:
            found.add(well)
    return found


def remote_output_wells(cluster: StandinHost) -> set[str]:
    """Wells whose output — archive or CSV — is anywhere under the host's staging directory."""
    import re

    staging = cluster.root / ".allwell-staging"
    found = {p.name[:3] for p in staging.rglob("*_out.zip")}
    found |= {m.group(1) for p in staging.rglob("measurements_*") if (m := re.search(r"_([A-H]\d{2})\.csv", p.name))}
    return found


def journal_states(out: Path) -> dict[str, str]:
    """The run journal's state for each well."""
    from services import run_journal

    journal = run_journal.read_journal(out)
    assert journal is not None, "the run kept no journal"
    return {w: rec.state for w, rec in (journal.wells or {}).items()}


def assert_correct_or_visibly_failed(run: StandinRun, cluster: StandinHost, wells, *, expect: str = "",
                                     complete=complete_here) -> str:
    """The rule every scenario is held to, whatever its fault did. Returns how the run ended, which must be
    *expect* when one is given. *complete* answers which wells are complete here (``complete_here``'s shape).

    * It ended, once (``StandinRun.ending``) — ``start_run`` has already failed a run that did not end.
    * Never a remote deletion of output this computer does not hold: every well the host's pipeline finished is
      either complete here or still on the host.
    * Never a well recorded as fetched without its output here.
    * ``done`` means every well is complete here; anything else is an ``error`` that says why — in its headline,
      or, for the user's own Stop, in the latch the dashboard reads (``analyze.log_pane``: "stopped").
    """
    import re

    kind, message = run.ending
    assert not expect or kind == expect, f"the run ended {kind}, not {expect}: {message.strip()}\n{run.text[-6000:]}"
    here = complete(run.out, wells)
    computed = set(re.findall(r"Well ([A-H]\d{2}) — done\.", run.text))
    lost = sorted(w for w in computed if w not in here and w not in remote_output_wells(cluster))
    assert not lost, (f"the host's output of {lost} was deleted, and this computer does not have it "
                      f"(ended {kind}: {message.strip()})\n{run.text[-5000:]}")
    claimed = sorted(w for w, state in journal_states(run.out).items() if state == "fetched" and w not in here)
    assert not claimed, f"the journal says {claimed} were fetched, and they are not here\n{run.text[-5000:]}"
    if kind == "done":
        assert here == set(wells), (f"the run reported success with {sorted(set(wells) - here)} missing here: "
                                    f"{message.strip()}\n{run.text[-5000:]}")
    elif run.runner.cancel_state_at_failure[0]:
        assert kind == "error", f"a stopped run ended {kind}: {message}"
    else:
        assert kind == "error", f"the run ended {kind}: {message}"
        bare = re.fullmatch(r"[^\n]*exited with code -?\d+\.", message.strip())
        assert message.strip() and not bare, (
            f"the run failed and its headline gives no reason: {message.strip()!r}\n{run.text[-5000:]}")
    return kind
