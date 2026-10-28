"""Per-host remote-execution config for the AllWell remote-runner.

Loads host definitions from ``remotes.toml`` in the app's folder (or the path
in ``$ALLWELL_REMOTES``). Each entry describes how to reach one SSH host
and run ``process_microscopy.py`` on it.

The TOML schema is documented in the v5.0.0 release notes and the
``bootstrap_remote.sh`` script generates a starter file for the user.

Example::

    [remote.bigbox]
    ssh_alias = "bigbox"
    remote_project_dir = "~/allwell"
    remote_staging_dir = "~/.allwell-staging"
    remote_python = "~/miniforge3/envs/allwell/bin/python"
    cpu_only = false
    extra_args = ["--workers", "0"]
    cache_budget_gb = 200
    measured_bandwidth_mbps = 0.0

On an HPC lobby/login node, add ``compute_launcher`` to run the pipeline on
a scheduler-allocated compute node instead of the SSH host itself. The value is
the command that wraps the pipeline invocation, so anything that takes an argv
and runs it elsewhere works — ``srun`` here, but ``salloc``/``qsub`` wrappers
the same way::

    [remote.cluster]
    ssh_alias = "hpc-login"
    compute_launcher = "srun --partition=gpu --gres=gpu:1 --time=4:00:00"
    ...
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from services.remote_srun_retry import with_exclusive
from services.subprocess_text import TEXT_UTF8
from util.aw_safe import swallowed
from util.json_write import write_text_atomic
from util.state_dir import (
    REMOTES_NAME, adopt_legacy_file, app_file, gui_state_dir, legacy_config_dirs,
)
from services.slurm_batch_script import (
    ScriptLauncher,
    directive_flags,
    launcher_from_batch_script,
    single_task,
)

_LOG = logging.getLogger(__name__)

# tomllib is stdlib on 3.11+; tomli is the 3.10 backport. The project pins
# 3.10.14, so tomli is the expected provider — but we accept either so the
# module is portable to newer interpreters as the project's Python pin
# moves forward.
try:  # pragma: no cover — exercised by whichever interpreter the user has.
    import tomllib as _toml
except ImportError:  # pragma: no cover
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Reading remotes.toml requires the 'tomli' package on Python "
            "<3.11. Install it with: pip install tomli"
        ) from exc


#: Placeholder shown for ``remote_python`` when the host has not set one.
#:
#: It is a *hint about the usual layout*, not a fact about the host: the
#: bootstrap creates the ``allwell`` env inside whichever conda it found, which
#: on a machine with a pre-existing system conda (an HPC module, ``/opt/conda``)
#: is nowhere near ``~/miniforge3``. That is why the runner resolves the env
#: through ``conda run`` by name when the field is left at this value, and only
#: treats it as a real path once the user has set it to something else — see
#: ``services.remote_runner._conda_env_selector``.
DEFAULT_REMOTE_PYTHON = "~/miniforge3/envs/allwell/bin/python"

#: Name of the conda env the bootstrap creates on a remote host.
DEFAULT_CONDA_ENV = "allwell"


@dataclass(frozen=True)
class RemoteHost:
    """One configured remote-execution target.

    ``ssh_alias`` is the only required field — everything else has a
    sensible default. The alias is passed verbatim to ``ssh`` and ``rsync``
    so users can point at a fully-configured ``~/.ssh/config`` entry
    (recommended) or at ``user@host`` directly.
    """

    name: str
    ssh_alias: str
    remote_project_dir: str = "~/allwell"
    remote_staging_dir: str = "~/.allwell-staging"
    remote_scratch_dir: str = ""
    """Where the pipeline keeps its transient working files **on the remote**.

    Exported as ``ALLWELL_SCRATCH_DIR`` inside the composed remote command, which
    is what ``pipeline.io.scratch_base_for`` reads. Empty (the default, and every
    pre-existing config) leaves the pipeline on the remote's ``~/.allwell/scratch``
    — byte-identical to previous behaviour.

    It matters most on a cluster, where that default is the worst available
    choice: a run's working set is roughly six float32 intermediates per
    FOV-group for every well running at once, and an HPC home directory is
    exactly the volume the site gives a small quota. ``$TMPDIR``, ``$SLURM_TMPDIR``
    or ``/scratch/$USER`` are the usual answers, and the value is expanded on the
    remote so those spellings work verbatim.
    """
    remote_python: str = DEFAULT_REMOTE_PYTHON
    cpu_only: bool = False
    extra_args: tuple[str, ...] = field(default_factory=tuple)
    cache_budget_gb: int = 200
    measured_bandwidth_mbps: float = 0.0
    auto_gc: bool = False
    """Whether AllWell may automatically evict oldest dataset caches
    when ``cache_budget_gb`` is exceeded.

    v5.2.11: default is False — shared remote machines should never
    have files deleted without explicit user consent. Power users
    set ``auto_gc = true`` in ``remotes.toml`` (or via a future GUI
    toggle) to re-enable LRU eviction. ``cache_budget_gb`` is still
    the limit when auto_gc fires; without auto_gc the cache grows
    until the user manually clears it.
    """
    ssh_key_path: str = ""
    """Optional path to an SSH private key (``~`` expanded at use time).

    Set by the "Setup SSH Key…" wizard (v5.2.1) or by hand. When empty,
    ssh falls back to whatever ``~/.ssh/config`` provides — the typical
    case for developers who already have a working ssh setup. When set,
    every ssh / rsync invocation gets ``-i <expanded_path>`` so the
    framework never relies on agent-forwarded or password auth.
    """
    compute_launcher: str = ""
    """Optional scheduler command that launches the pipeline onto a
    *compute node the remote schedules*, rather than running it on the
    SSH host directly.

    This is what makes an HPC "lobby" / login-node setup work: you SSH
    into the login node (``ssh_alias``), but the cluster forbids heavy
    compute there — you must acquire a node from the batch scheduler
    (the same mechanism JupyterHub's BatchSpawner uses). When set, the
    runner prepends this command to the pipeline invocation so it runs
    inside a fresh scheduler allocation, e.g.::

        compute_launcher = "srun --partition=gpu --gres=gpu:1 --cpus-per-task=16 --mem=64G --time=4:00:00"

    All the cheap control-plane work (rsync staging, cache check, fetch,
    cleanup) still runs on the login node against the shared filesystem;
    only this one command lands on the allocated node.

    When empty (the default, and every pre-existing config) the pipeline
    runs **directly on the SSH host** — byte-identical to legacy
    behaviour. The string is tokenised with ``shlex.split`` at use time,
    so it stays human-readable on disk.
    """
    exclusive_node: bool = True
    """Whether an ``srun`` :attr:`compute_launcher` asks for the node to
    itself (``--exclusive``), so no other job — another app instance's run
    included — is placed on the same machine.

    On by default, which every config saved before the field existed gets
    too: :attr:`launch_command` adds the word at launch time, and
    :attr:`compute_launcher` stays exactly what the user wrote. A launcher
    that already says ``--exclusive`` or ``--oversubscribe`` is left as it is.
    Written to ``remotes.toml`` only when switched off.
    """
    jupyterhub_config_path: str = ""
    """Optional path to the cluster's ``jupyterhub_config.py`` on the remote.

    Used by the "Fetch profiles" button to discover the discrete compute
    options the cluster's JupyterHub exposes (its spawner ``profile_list`` /
    ``ProfilesSpawner.profiles``) **without** going through the JupyterHub
    web UI — see :func:`query_jupyterhub_profiles`. When empty, a short list
    of conventional locations (``/etc/jupyterhub/jupyterhub_config.py``, the
    ``JUPYTERHUB_CONFIG`` env var) is probed on the remote. Purely a
    discovery convenience: the selected profile is materialised into
    :attr:`compute_launcher`, which remains the source of truth for a run.
    """
    queue_wait_timeout_min: int = 30
    """How long a job may wait in the scheduler queue before the run gives up
    and cancels it, in minutes (``0`` = wait indefinitely).

    On a scheduled host the run can sit in the queue while a node is
    allocated (or, on cloud/autoscaling clusters, provisioned); this is the
    bound on that wait, editable in the remote-server manager.

    It has nothing to do with ssh's keepalive. It once drove
    ``ServerAliveCountMax``, which bounded nothing: sshd answers keepalives
    while a job is queued, so a queued run was never cut off by it, and a
    *dead* connection took ``2 × this`` keepalive intervals — 30 minutes at
    the default, never at ``0`` — to be noticed. Every ssh the app starts
    carries the same fixed keepalive instead (:func:`keepalive_options`).
    """

    @property
    def launch_command(self) -> str:
        """The scheduler command a run is actually sent with:
        :attr:`compute_launcher`, plus ``--exclusive`` when
        :attr:`exclusive_node` is set (:func:`services.remote_srun_retry.with_exclusive`).
        Every launch site reads this rather than the raw field, so the run,
        the batched driver and the scheduler test ask for the same node."""
        if not self.exclusive_node:
            return self.compute_launcher
        return with_exclusive(self.compute_launcher)

    def ssh_argv(self, *, allocate_tty: bool = True, share_connection: bool = True) -> list[str]:
        """Build the leading ``ssh`` command for this host.

        ``-tt`` allocates a PTY so SIGHUP propagates to the remote process
        when the local ssh exits — that's how the existing pgid-based
        ``stop()`` reaches the remote pipeline. The ControlMaster/Persist
        pair lets follow-up ssh's (cleanup, kill) reuse the same TCP
        connection without re-authenticating.

        ControlMaster is POSIX-only. Native Windows OpenSSH has no Unix
        socket support and silently ignores the option; Cygwin's
        OpenSSH (Git for Windows) provides Unix-socket *emulation* but
        the resulting socket file is fragile under real-world Windows
        conditions — antivirus quarantine, MAX_PATH limits, mismatched
        ``$HOME``, and ownership-bit translation between Cygwin and
        NTFS can each cause the multiplexer to fail with ``exit -1``
        (= 4294967295 unsigned) before the requested command runs.
        Skipping ControlMaster on Windows costs one TCP handshake per
        ssh invocation but avoids the entire failure mode.

        On Windows we still prefer the Git-for-Windows-bundled OpenSSH
        over native Windows OpenSSH so other framework features
        (PTY-tied SIGHUP propagation, accept-new, BatchMode) behave
        consistently; only ControlMaster itself is the casualty.

        When :attr:`ssh_key_path` is set the resulting argv adds
        ``-i <expanded path>`` so AllWell uses the key generated by the
        v5.2.1 wizard rather than whichever identity ssh's default
        search order would pick.

        Every call is unattended (:func:`unattended_options`): ``BatchMode``
        so an app started from a terminal fails with "Permission denied"
        rather than blocking on a password prompt nobody can see, and
        ``ConnectTimeout`` so an unreachable host fails in seconds. The fixed
        keepalive (:func:`keepalive_options`) bounds how long a dead link —
        a laptop that slept, a network change — goes unnoticed.

        ``share_connection=False`` is for a run's output stream — the
        single-shot pipeline's and the batch driver's. It opens a connection
        of its own (:func:`own_connection_options`) instead of riding the
        shared master, because everything multiplexed through a master ends
        with it: a master that stops answering, or the ``ssh -O exit`` that
        ``remote_transport.recover_dead_master`` sends to close one, would
        otherwise take a running batch's stream — and, through the PTY's
        SIGHUP, the batch itself — down with the heartbeat's connection.
        """
        argv = [resolve_ssh_binary(), *known_hosts_options()]
        if allocate_tty:
            argv.append("-tt")
        if self.ssh_key_path:
            argv += ["-i", str(Path(self.ssh_key_path).expanduser())]
        if not _is_windows():
            argv += control_master_options() if share_connection else own_connection_options()
        argv += [*unattended_options(), *keepalive_options(), self.ssh_alias]
        return argv


#: Seconds between ssh keepalives, and how many may go unanswered before ssh
#: drops the connection: ``30 × 4`` notices a dead link in about two minutes.
#: A keepalive is answered by sshd itself, so a command that is merely quiet —
#: a job waiting in the queue, a pipeline between log lines — is never cut off
#: by it; only a connection that has stopped carrying anything is.
SSH_KEEPALIVE_INTERVAL_S = 30
SSH_KEEPALIVE_COUNT_MAX = 4
#: Seconds ssh waits for the TCP connection and the server's banner.
SSH_CONNECT_TIMEOUT_S = 15


def keepalive_options() -> list[str]:
    """The keepalive every ssh the app starts carries: its own commands
    (:meth:`RemoteHost.ssh_argv`), rsync's transport
    (``remote_transport._rsync_ssh_transport``) and the bootstrap's.

    Fixed rather than derived from a setting, because what it bounds is how
    long a *dead* connection goes unnoticed, and that should be short on every
    host. With ``ControlMaster=auto`` the heartbeat, the batch hand-off,
    and ``scancel`` share one connection, so a dead master leaves every one
    of them hanging for as long as this allows. A run's output stream has a
    connection of its own (``RemoteHost.ssh_argv(share_connection=False)``),
    which this same keepalive bounds.
    """
    return ["-o", f"ServerAliveInterval={SSH_KEEPALIVE_INTERVAL_S}",
            "-o", f"ServerAliveCountMax={SSH_KEEPALIVE_COUNT_MAX}"]


def unattended_options() -> list[str]:
    """Options that make an ssh safe to run with nobody at a terminal.

    ``BatchMode=yes``: no password or passphrase prompt — a key that does not
    work fails with "Permission denied" rather than waiting on a prompt
    (``ssh_key_setup.key_has_passphrase`` relies on it).
    ``StrictHostKeyChecking=accept-new``: a first-contact host is trusted; a
    *changed* key is still refused. ``ConnectTimeout``: an unreachable host
    fails in seconds rather than after the kernel's TCP retries.
    """
    return ["-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_S}"]


def control_master_options() -> list[str]:
    """The connection-sharing options of :meth:`RemoteHost.ssh_argv` (POSIX only)."""
    return ["-o", "ControlMaster=auto",
            "-o", f"ControlPath={_controlpath_template()}",
            "-o", "ControlPersist=60s"]


def own_connection_options() -> list[str]:
    """Options for an ssh that must not join or create a shared master (POSIX only).

    Explicit rather than merely omitting :func:`control_master_options`, so a
    ``ControlMaster`` / ``ControlPath`` in the user's own ``~/.ssh/config`` does
    not multiplex the stream through a master after all.
    """
    return ["-o", "ControlMaster=no", "-o", "ControlPath=none"]


#: The longest socket path the platform accepts (``sun_path``): 104 bytes on
#: macOS and the BSDs, 108 on Linux. The smaller, so one rule fits both.
_SOCKET_PATH_MAX = 104
#: ``allwell-cm-`` plus the 40 hex digits ``%C`` expands to.
_CONTROL_SOCKET_NAME_LEN = len("allwell-cm-") + 40


def _controlpath_template() -> str:
    """OpenSSH ControlPath for the connection-sharing master.

    ``%C`` is ssh's hash of the local host, remote host, port and user: the
    per-(user, host, port) uniqueness ``%r@%h:%p`` gave, in a fixed forty
    characters. The spelled-out form overran the 104-byte socket limit for a
    long user or host name (ssh: "ControlPath too long", every command exit
    255), and its ``:`` is not a legal filename character on Windows. The
    directory is the first of ``$XDG_RUNTIME_DIR``, ``~/.ssh`` and a per-user
    directory under ``/tmp`` in which the expanded socket path fits.
    """
    candidates = [os.environ.get("XDG_RUNTIME_DIR") or "", str(Path.home() / ".ssh")]
    if not _is_windows():
        candidates.append(f"/tmp/allwell-cm-{os.getuid()}")
    fits = [c for c in candidates
            if c and len(c) + 1 + _CONTROL_SOCKET_NAME_LEN < _SOCKET_PATH_MAX]
    base = Path(fits[0] if fits else candidates[-1])
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    return str(base / "allwell-cm-%C")


# Git for Windows can land in a handful of locations depending on
# whether the user picked the system-wide or per-user installer, ran
# a 32-bit installer on a 64-bit OS, or routed the install through
# scoop / chocolatey / winget. We probe each in turn (most specific
# first, PATH last) so AllWell's helpers find the bundled tools
# regardless of where the user dropped the install.
#
# The function form (not a module-level constant) keeps the candidate
# list up-to-date with the live ``%LOCALAPPDATA%`` / ``%ProgramFiles%``
# environment — important because tests monkeypatch these env vars to
# simulate different install layouts.
def _git_bash_bin_dirs() -> tuple[Path, ...]:
    """Return Git for Windows' candidate ``usr\\bin\\`` directories.

    Probes:

    1. The parent of any ``git.exe`` on PATH (handles every install
       location automatically — Program Files, Program Files (x86),
       per-user under %LOCALAPPDATA%, scoop, chocolatey, custom paths,
       etc., as long as the installer added Git to PATH which every
       Git for Windows install does by default).
    2. ``%ProgramFiles%\\Git\\usr\\bin`` — the system-wide install.
    3. ``%ProgramFiles(x86)%\\Git\\usr\\bin`` — older 32-bit installs.
    4. ``%LOCALAPPDATA%\\Programs\\Git\\usr\\bin`` — the per-user
       installer (the default when running as a non-admin user, which
       a lot of corporate-managed Windows boxes default to).

    Returns the tuple of candidates in preference order; callers
    typically pick the first one whose ``X.exe`` exists.
    """
    candidates: list[Path] = []
    on_path = shutil.which("git")
    if on_path:
        # Git's install layout is ``<root>\cmd\git.exe`` (the wrapper
        # that ends up on PATH) → derive ``<root>\usr\bin``.
        git_root = Path(on_path).resolve().parent.parent
        candidates.append(git_root / "usr" / "bin")
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        root = os.environ.get(env_var)
        if root:
            candidates.append(Path(root) / "Git" / "usr" / "bin")
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Programs" / "Git" / "usr" / "bin")
    # De-dupe while preserving order — multiple env vars may resolve
    # to the same directory.
    seen: set[str] = set()
    unique: list[Path] = []
    for c in candidates:
        key = str(c).lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return tuple(unique)


def _is_windows() -> bool:
    """Module-local platform check.

    Indirected through a helper so tests can ``monkeypatch`` it cleanly
    without poisoning the real ``os.name`` (which would break pytest's
    own internals on a Linux test runner). All Windows-aware code in
    this module funnels through here.
    """
    return os.name == "nt"


def known_hosts_options() -> list[str]:
    """``-o UserKnownHostsFile=…`` naming the one known-hosts file every ssh this app starts must share; ``[]`` on POSIX.

    On Windows the app starts two different ``ssh.exe``: Git for Windows' (:func:`resolve_ssh_binary`) for its own
    commands and the connection test, and the one beside ``rsync.exe`` (:func:`resolve_ssh_for_rsync`) for every
    transfer, which has to come from rsync's own MSYS2 / Cygwin runtime. Each runtime has its own home, so each read
    its own ``~/.ssh/known_hosts`` — Git's under ``C:\\Users\\<you>``, rsync's under its install's ``/home/<you>`` — and
    a host whose key changed was fixed in the first by deleting it (or by the connection test accepting the new key)
    while every transfer went on failing on the stale key in the second: "REMOTE HOST IDENTIFICATION HAS CHANGED",
    then rsync's "error in rsync protocol data stream (code 12)". Naming the file makes them one: the Windows
    profile's ``.ssh/known_hosts``, beside the keys the setup wizard writes (``ssh_key_setup``). Forward slashes,
    which all three runtimes read; quoted when the path holds a space, since the option takes a space-separated list.
    On POSIX every ssh is the same binary with the same home, so nothing is added.
    """
    if not _is_windows():
        return []
    path = (Path.home() / ".ssh" / "known_hosts").as_posix()
    if " " in path:
        path = f'"{path}"'
    return ["-o", f"UserKnownHostsFile={path}"]


def resolve_ssh_binary() -> str:
    """Return the ssh executable this framework should use.

    On Windows: prefer Git for Windows' ssh.exe (Cygwin OpenSSH) from
    whichever install location is present (system-wide, 32-bit,
    per-user, scoop, etc. — see :func:`_git_bash_bin_dirs`). Fall back
    to plain ``ssh`` if no Git for Windows is found, in which case
    Windows' native OpenSSH (or any other ssh on PATH) is used.
    On POSIX: always plain ``ssh``.
    """
    if _is_windows():
        for bin_dir in _git_bash_bin_dirs():
            candidate = bin_dir / "ssh.exe"
            if candidate.is_file():
                return str(candidate)
    return "ssh"


def resolve_ssh_for_rsync() -> str:
    """Return an ssh.exe that's binary-compatible with the resolved rsync.

    On Windows, rsync.exe and ssh.exe must come from the SAME MSYS /
    Cygwin runtime. Cygwin's fork() emulation duplicates the parent's
    handle table into the child before exec'ing ssh; if the child
    process's first action is to load a DIFFERENT msys-2.0.dll (or
    cygwin1.dll) than the parent's, the dup() of stdin/stdout/stderr
    fails before the SSH binary ever starts. rsync reports this as:

        dup() in/out/err failed
        rsync error: error in IPC code (code 14) at pipe.c

    …or sometimes as exit 12 ("connection unexpectedly closed,
    0 bytes received") depending on where in the protocol the death
    is detected.

    The common Windows case: rsync comes from MSYS2 at
    ``C:\\msys64\\usr\\bin\\rsync.exe``, but :func:`resolve_ssh_binary`
    returns Git for Windows' ssh at
    ``C:\\Program Files\\Git\\usr\\bin\\ssh.exe``. Git for Windows
    ships its OWN MSYS2 runtime, different from the standalone MSYS2
    install — fork-emulation across the boundary breaks.

    Fix: prefer ssh.exe in the same directory as the resolved rsync.
    Fall back to :func:`resolve_ssh_binary` if no co-located ssh
    exists (rsync may be cwRsync / Cygwin / etc., each of which
    ships its own ssh in the same folder anyway).

    On POSIX this is the same as :func:`resolve_ssh_binary`.
    """
    if not _is_windows():
        return resolve_ssh_binary()
    try:
        rsync_path = Path(resolve_rsync_binary())
    except FileNotFoundError:
        # No rsync at all — let the caller hit the actionable
        # FileNotFoundError from resolve_rsync_binary directly.
        return resolve_ssh_binary()
    paired = rsync_path.parent / "ssh.exe"
    if paired.is_file():
        return str(paired)
    return resolve_ssh_binary()


def resolve_rsync_binary() -> str:
    """Return the rsync executable this framework should use.

    On Windows we probe a handful of common third-party install
    locations because Git for Windows' default installer does NOT
    bundle rsync.exe — only ssh.exe. Fall back to ``rsync`` on PATH
    if none of those match, and raise a clear ``FileNotFoundError``
    with installation guidance if rsync still isn't found (instead
    of letting ``subprocess.run`` raise ``[WinError 2] The system
    cannot find the file specified`` deep inside a sync helper).
    On POSIX: always plain ``rsync`` (every Linux/macOS system the
    framework supports ships rsync in the base install).
    """
    if not _is_windows():
        return "rsync"
    # Each Git for Windows install root + the third-party rsync homes.
    candidates: list[Path] = []
    for bin_dir in _git_bash_bin_dirs():
        candidates.append(bin_dir / "rsync.exe")
    candidates += [
        Path(r"C:\Program Files\cwRsync\bin\rsync.exe"),
        Path(r"C:\Program Files (x86)\cwRsync\bin\rsync.exe"),
        Path(r"C:\msys64\usr\bin\rsync.exe"),
        Path(r"C:\tools\msys64\usr\bin\rsync.exe"),
        Path(r"C:\cygwin64\bin\rsync.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    on_path = shutil.which("rsync")
    if on_path:
        return on_path
    raise FileNotFoundError(
        "rsync.exe is not installed on this Windows machine. The Git "
        "for Windows installer ships ssh.exe but NOT rsync, so AllWell "
        "cannot sync files to the remote until rsync is available. "
        "Pick ONE of:\n"
        "  1. Copy rsync.exe and the DLLs it needs (msys-xxhash-0.dll, "
        "msys-zstd-1.dll, msys-lz4-1.dll) from the MSYS2 packages at "
        "https://repo.msys2.org/msys/x86_64/ into your Git for Windows "
        "``usr\\bin\\`` directory. Do NOT copy msys-2.0.dll: rsync must "
        "use Git's own runtime, the one Git's ssh.exe uses.\n"
        "  2. Install cwRsync from https://itefix.net/cwrsync (free "
        "version is fine).\n"
        "  3. Install MSYS2 (https://www.msys2.org/), then "
        "``pacman -S rsync openssh`` in an MSYS2 shell (both: rsync "
        "needs an ssh.exe from the same runtime beside it).\n"
        "The step-by-step version is in the Windows section of "
        "requirements.txt.\n"
        "Then re-run the bootstrap. AllWell auto-detects rsync at any "
        "of the locations above without needing PATH edits."
    )


def to_rsync_path(p: Path | str) -> str:
    """Translate a local-filesystem path to the form rsync expects.

    POSIX: ``str(p)`` unchanged. Windows: drive-letter rewritten to
    Cygwin form, e.g. ``C:\\Users\\foo\\plate`` → ``/c/Users/foo/plate``.
    UNC paths (``\\\\server\\share\\dir``) are rewritten to the Cygwin/cwrsync
    network form ``//server/share/dir`` — Cygwin (which cwrsync is built on)
    resolves ``//server/share`` to the UNC resource, so a network share can be
    used directly without first mapping it to a drive letter.
    """
    if not _is_windows():
        return str(p)
    s = str(p)
    # UNC path (``\\server\share\…`` or already ``//server/share/…``) → the
    # Cygwin network form ``//server/share/…`` that cwrsync understands.
    if s.startswith("\\\\") or s.startswith("//"):
        rest = s.lstrip("\\/").replace("\\", "/")
        return "//" + rest
    # On a non-Windows test runner with _is_windows monkeypatched True,
    # Path(s).resolve() would try to construct WindowsPath which can't
    # be instantiated on POSIX. Bypass it: split the drive textually,
    # normalise the rest by hand.
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2).replace("\\", "/")
        return f"/{drive}/{rest}"
    return s.replace("\\", "/")


def default_config_path() -> Path:
    """Return the resolved path to the user's remotes.toml.

    ``remotes.toml`` in the app's folder (``util.state_dir``):
    ``%LOCALAPPDATA%\\allwell`` on Windows, ``~/.allwell`` on macOS and Linux.
    Honors ``$ALLWELL_REMOTES`` for tests and ad-hoc overrides.

    Earlier versions kept it in ``%APPDATA%\\allwell`` or
    ``~/.config/allwell``; a file left in either is moved into the app's
    folder the first time this is asked (``%APPDATA%`` first, which is the one
    those versions preferred when both existed), so the user's hosts carry over.
    """
    override = os.environ.get("ALLWELL_REMOTES")
    if override:
        return Path(override).expanduser()
    return adopt_legacy_file(
        app_file(REMOTES_NAME),
        [d / REMOTES_NAME for d in legacy_config_dirs()],
        log=_LOG,
    )


def default_state_dir() -> Path:
    """Return the directory for All-Well's persisted app state — the job
    queue and the run index: the app's folder (``util.state_dir.gui_state_dir``).

    Deliberately **not** derived from :func:`default_config_path`. That
    function honors ``$ALLWELL_REMOTES``, an override meaning "where my
    remotes live" — routing unrelated state through it would relocate the job
    queue any time a user or test sets that variable, possibly to a read-only
    path. The records it holds carry themselves over from where earlier
    versions kept them (``services.job_queue.queue_path``,
    ``services.run_journal.index_path``).
    """
    return gui_state_dir()


def load_remotes(path: Path | None = None) -> dict[str, RemoteHost]:
    """Parse the remotes.toml file. Returns an empty dict if missing.

    Missing-file is **not** an error: the user may simply have no remotes
    configured. Malformed-file *is* an error and surfaces as a
    ``ValueError`` so the GUI can show it cleanly.
    """
    p = path or default_config_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "rb") as fh:
            doc: dict[str, Any] = _toml.load(fh)
    except _toml.TOMLDecodeError as exc:
        raise ValueError(f"Cannot parse {p}: {exc}") from exc

    remotes_table = doc.get("remote", {}) or {}
    if not isinstance(remotes_table, dict):
        raise ValueError(f"{p}: top-level 'remote' must be a table.")

    out: dict[str, RemoteHost] = {}
    for name, entry in remotes_table.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{p}: 'remote.{name}' must be a table.")
        try:
            out[name] = _parse_entry(name, entry)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{p}: 'remote.{name}': {exc}") from exc
    return out


def get_remote(name: str, path: Path | None = None) -> RemoteHost | None:
    """Convenience accessor: load + look up by name in one call."""
    return load_remotes(path).get(name)


def default_remote_index(n_combo_items: int) -> int:
    """Default index for the Analyze "Run on" combo.

    The combo is ``Local`` (index 0) followed by one item per configured remote.
    When at least one remote exists (``n_combo_items > 1``) the run defaults to
    the first remote (index 1); otherwise it stays on ``Local`` (index 0). Pure
    so it can be unit-tested without Qt.
    """
    return 1 if n_combo_items > 1 else 0


def _toml_string(value: str) -> str:
    """Render ``value`` as a TOML basic string with proper escaping.

    TOML basic strings use ``\\``-escape sequences (``\\\\``, ``\\"``,
    ``\\n`` …) and reserve unescaped ``\\u`` / ``\\U`` as Unicode escape
    introducers. Naively interpolating a Windows path like
    ``C:\\Users\\me\\.ssh\\allwell_foo`` into ``"..."`` produces invalid
    TOML — ``\\U`` is read as the start of a ``\\Uxxxxxxxx`` escape and
    the parser errors with ``invalid hex value`` at the first non-hex
    character. Escape every backslash and embedded quote so the value
    round-trips through any TOML reader.
    """
    escaped = (
        value.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
             .replace("\r", "\\r")
             .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _format_block(host: RemoteHost) -> str:
    """Render a ``RemoteHost`` as a TOML ``[remote.<name>]`` block.

    Single source of truth for what a remote looks like on disk; called
    by both :func:`write_remote` and ``services/remote_bootstrap.py``.
    """
    extras = ", ".join(_toml_string(x) for x in host.extra_args)
    # ssh_key_path is only emitted when set, to keep the common-case
    # (no custom key) block tidy and not parade an empty line at the
    # user. Loaders default the field to "" when absent.
    key_line = (
        f"ssh_key_path = {_toml_string(host.ssh_key_path)}\n"
        if host.ssh_key_path else ""
    )
    # compute_launcher is only emitted when set, like ssh_key_path: the
    # common case (compute runs on the SSH host) leaves the block tidy
    # and loaders default the field to "" when absent.
    launcher_line = (
        f"compute_launcher = {_toml_string(host.compute_launcher)}\n"
        if host.compute_launcher else ""
    )
    # exclusive_node is only emitted when switched off: on is the default,
    # and loaders default the field to true when absent.
    exclusive_line = "" if host.exclusive_node else "exclusive_node = false\n"
    jhub_line = (
        f"jupyterhub_config_path = {_toml_string(host.jupyterhub_config_path)}\n"
        if host.jupyterhub_config_path else ""
    )
    scratch_line = (
        f"remote_scratch_dir = {_toml_string(host.remote_scratch_dir)}\n"
        if host.remote_scratch_dir else ""
    )
    return (
        f"[remote.{host.name}]\n"
        f"ssh_alias = {_toml_string(host.ssh_alias)}\n"
        f"{key_line}"
        f"{launcher_line}"
        f"{exclusive_line}"
        f"{jhub_line}"
        f"remote_project_dir = {_toml_string(host.remote_project_dir)}\n"
        f"remote_staging_dir = {_toml_string(host.remote_staging_dir)}\n"
        f"{scratch_line}"
        f"remote_python = {_toml_string(host.remote_python)}\n"
        f"cpu_only = {'true' if host.cpu_only else 'false'}\n"
        f"extra_args = [{extras}]\n"
        f"cache_budget_gb = {host.cache_budget_gb}\n"
        f"queue_wait_timeout_min = {host.queue_wait_timeout_min}\n"
        f"auto_gc = {'true' if host.auto_gc else 'false'}\n"
        f"measured_bandwidth_mbps = {host.measured_bandwidth_mbps:.2f}\n"
    )


def write_remote(host: RemoteHost, path: Path | None = None) -> bool:
    """Insert or replace a ``[remote.<name>]`` block in ``remotes.toml``.

    Idempotent. Preserves comments and other sections — the file is
    rewritten by line-edit, not parsed-and-reserialised. Returns True
    iff the file content changed.

    Raises ``OSError`` if the file can't be written. Callers that can't
    surface IO failures (the GUI) should catch that explicitly.
    """
    p = path or default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    block = _format_block(host)

    existing = p.read_text(encoding="utf-8") if p.is_file() else ""

    section_re = re.compile(rf"^\[remote\.{re.escape(host.name)}\]\s*$")
    has_section = any(section_re.match(line) for line in existing.splitlines())

    if not has_section:
        # Append. Make sure there's exactly one blank line between the
        # previous section and the new one.
        if existing and not existing.endswith("\n"):
            existing += "\n"
        if existing and not existing.endswith("\n\n"):
            existing += "\n"
        new_text = existing + block
    else:
        # Replace the existing block in place. A "block" is the section
        # header plus every subsequent line up to (but not including)
        # the next ``[…]`` header or EOF.
        out: list[str] = []
        in_target = False
        replaced = False
        for line in existing.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if in_target:
                    in_target = False
                if section_re.match(stripped):
                    in_target = True
                    if not replaced:
                        out.append(block)
                        replaced = True
                    continue
            if in_target:
                continue
            out.append(line)
        new_text = "".join(out)

    if new_text == existing:
        return False
    write_text_atomic(p, new_text)
    return True


def delete_remote(name: str, path: Path | None = None) -> bool:
    """Remove the ``[remote.<name>]`` block from ``remotes.toml``.

    Idempotent: a name that isn't present is a silent no-op. Comments
    and other sections are preserved. Returns True iff the file
    actually changed.
    """
    p = path or default_config_path()
    if not p.is_file():
        return False
    text = p.read_text(encoding="utf-8")
    section_re = re.compile(rf"^\[remote\.{re.escape(name)}\]\s*$")
    out: list[str] = []
    in_target = False
    found = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_target:
                in_target = False
            if section_re.match(stripped):
                in_target = True
                found = True
                continue
        if in_target:
            continue
        out.append(line)
    if not found:
        return False
    new_text = "".join(out).rstrip("\n") + "\n"
    if new_text == text:
        return False
    write_text_atomic(p, new_text)
    return True


def update_bandwidth(name: str, mbps: float, path: Path | None = None) -> None:
    """Rewrite ``measured_bandwidth_mbps`` for ``name`` in place.

    Used by the runner after each successful upload to keep the rolling
    bandwidth estimate fresh. The write is best-effort: a missing or
    unwritable file logs a warning to stderr but does not raise — the
    estimate is a hint, not a correctness requirement. The file is replaced
    atomically (``util.json_write.write_text_atomic``), as every writer here
    does, so a failure leaves the old file whole.
    """
    p = path or default_config_path()
    if not p.is_file():
        return
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return

    new_line = f"measured_bandwidth_mbps = {mbps:.2f}"
    in_section = False
    out_lines: list[str] = []
    written = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not written:
                out_lines.append(new_line)
                written = True
            in_section = stripped == f"[remote.{name}]"
        elif in_section and stripped.startswith("measured_bandwidth_mbps"):
            out_lines.append(new_line)
            written = True
            continue
        out_lines.append(line)
    if in_section and not written:
        out_lines.append(new_line)
        written = True

    if not written:
        # The host isn't in the file — leave it alone rather than appending
        # a half-written section.
        return

    # Atomically: this runs after every upload, and a truncating rewrite cut
    # short (a full disk, the app quitting) lost every configured host.
    try:
        write_text_atomic(p, "\n".join(out_lines) + "\n")
    except OSError:
        swallowed(f"recording the measured bandwidth in {p}", level=logging.WARNING)


def _parse_entry(name: str, entry: dict[str, Any]) -> RemoteHost:
    alias = entry.get("ssh_alias")
    if not isinstance(alias, str) or not alias.strip():
        raise ValueError("'ssh_alias' is required and must be a non-empty string.")

    extra_args = entry.get("extra_args", [])
    if not isinstance(extra_args, list) or not all(
        isinstance(x, str) for x in extra_args
    ):
        raise ValueError("'extra_args' must be a list of strings.")

    defaults = RemoteHost(name=name, ssh_alias=alias.strip())
    return replace(
        defaults,
        remote_project_dir=str(entry.get("remote_project_dir", defaults.remote_project_dir)),
        remote_staging_dir=str(entry.get("remote_staging_dir", defaults.remote_staging_dir)),
        remote_scratch_dir=str(entry.get("remote_scratch_dir", defaults.remote_scratch_dir)),
        remote_python=str(entry.get("remote_python", defaults.remote_python)),
        cpu_only=bool(entry.get("cpu_only", defaults.cpu_only)),
        extra_args=tuple(extra_args),
        cache_budget_gb=int(entry.get("cache_budget_gb", defaults.cache_budget_gb)),
        auto_gc=bool(entry.get("auto_gc", defaults.auto_gc)),
        measured_bandwidth_mbps=float(
            entry.get("measured_bandwidth_mbps", defaults.measured_bandwidth_mbps)
        ),
        ssh_key_path=str(entry.get("ssh_key_path", defaults.ssh_key_path)),
        compute_launcher=str(entry.get("compute_launcher", defaults.compute_launcher)),
        exclusive_node=bool(entry.get("exclusive_node", defaults.exclusive_node)),
        jupyterhub_config_path=str(
            entry.get("jupyterhub_config_path", defaults.jupyterhub_config_path)
        ),
        queue_wait_timeout_min=int(
            entry.get("queue_wait_timeout_min", defaults.queue_wait_timeout_min)
        ),
    )


# ── JupyterHub profile discovery ────────────────────────────────────────────
#
# JupyterHub's spawn page shows a *discrete, admin-curated* set of options
# (the spawner's ``profile_list`` / ``ProfilesSpawner.profiles``), NOT the raw
# SLURM partition list. Those profiles live in ``jupyterhub_config.py`` on the
# cluster and are not exposed by the JupyterHub REST API or by SLURM, so the
# only way to surface them without the web UI is to read that config.
#
# The snippet below runs on the remote login node (over ssh). It is
# deliberately dependency-free: a tiny auto-vivifying ``c`` shim stands in for
# ``traitlets.config.Config`` and a stub importer swallows
# ``import batchspawner`` / ``wrapspawner`` / ``kubespawner`` failures, so the
# config's ``c.X.Y = …`` assignments still land even when those packages aren't
# importable in whatever python3 happens to be on PATH. It prints one JSON
# object describing the discovered profiles (name + raw kwargs); the kwargs →
# ``srun`` mapping is done locally in :func:`profile_kwargs_to_launcher` so it
# stays unit-testable.

_JHUB_PROFILES_SNIPPET = r'''
import json, os, sys, types, builtins

class _C:
    """Auto-vivifying stand-in for traitlets Config (no dependency)."""
    def __init__(self):
        object.__setattr__(self, "_d", {})
    def __getattr__(self, k):
        d = object.__getattribute__(self, "_d")
        if k not in d:
            d[k] = _C()
        return d[k]
    def __setattr__(self, k, v):
        object.__getattribute__(self, "_d")[k] = v

def _store(obj):
    try:
        return object.__getattribute__(obj, "_d")
    except Exception:
        return {}

class _Dummy(types.ModuleType):
    def __getattr__(self, n):
        return _Dummy(n)
    def __call__(self, *a, **k):
        return _Dummy("call")

_real_import = builtins.__import__
def _safe_import(name, *a, **k):
    try:
        return _real_import(name, *a, **k)
    except Exception:
        m = sys.modules.get(name) or _Dummy(name)
        sys.modules[name] = m
        return m
builtins.__import__ = _safe_import

candidates = []
if len(sys.argv) > 1 and sys.argv[1].strip():
    candidates.append(sys.argv[1].strip())
if os.environ.get("JUPYTERHUB_CONFIG"):
    candidates.append(os.environ["JUPYTERHUB_CONFIG"])
candidates += [
    "/etc/jupyterhub/jupyterhub_config.py",
    "/srv/jupyterhub/jupyterhub_config.py",
    os.path.expanduser("~/.jupyterhub/jupyterhub_config.py"),
    os.path.expanduser("~/jupyterhub_config.py"),
]

c = _C()
ns = {"c": c, "get_config": (lambda: c)}
used = None
for path in candidates:
    try:
        with open(path) as fh:
            src = fh.read()
    except OSError:
        continue
    try:
        exec(compile(src, path, "exec"), ns)
    except Exception:
        pass  # partial assignments to c may still have landed
    used = path
    break

if used is None:
    print(json.dumps({"ok": False,
                      "error": "jupyterhub_config.py not found (looked in: %s)"
                               % ", ".join(candidates)}))
    sys.exit(0)

def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except Exception:
        return str(v)

def _kwargs(d):
    return {str(k): _jsonable(v) for k, v in d.items()} if isinstance(d, dict) else {}

profiles = []

# (a) wrapspawner ProfilesSpawner.profiles: list of
#     (display_name, slug, spawner_class, options_dict) tuples.
prof_tuples = _store(c).get("ProfilesSpawner")
prof_tuples = _store(prof_tuples).get("profiles") if prof_tuples is not None else None
if isinstance(prof_tuples, (list, tuple)):
    for item in prof_tuples:
        if isinstance(item, (list, tuple)) and len(item) >= 1:
            name = str(item[0])
            slug = str(item[1]) if len(item) >= 2 else ""
            opts = item[3] if len(item) >= 4 and isinstance(item[3], dict) else {}
            profiles.append({"name": name, "slug": slug, "kwargs": _kwargs(opts)})

# (b) *.profile_list: list of dicts (KubeSpawner / batchspawner / Spawner).
for spawner in ("Spawner", "KubeSpawner", "SlurmSpawner", "BatchSpawnerBase",
                "ProfilesSpawner", "WrapSpawner"):
    sp = _store(c).get(spawner)
    plist = _store(sp).get("profile_list") if sp is not None else None
    if not isinstance(plist, (list, tuple)):
        continue
    for item in plist:
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name") or item.get("name") or item.get("slug") or "profile")
        slug = str(item.get("slug") or item.get("key") or "")
        merged = {}
        for sub in ("kubespawner_override", "batch_spawner_override", "options", "slurm"):
            if isinstance(item.get(sub), dict):
                merged.update(item[sub])
        merged.update({k: v for k, v in item.items()
                       if k not in ("display_name", "name", "slug", "key",
                                    "kubespawner_override", "batch_spawner_override",
                                    "options", "slurm", "default", "description")})
        profiles.append({"name": name, "slug": slug, "kwargs": _kwargs(merged)})

# The spawner-wide settings a profile inherits: the job template (whose
# #SBATCH lines decide what each req_* value means) and req_* defaults.
spawner = {}
for cls in ("Spawner", "BatchSpawnerBase", "SlurmSpawner"):
    sp = _store(c).get(cls)
    for k, v in (_store(sp).items() if sp is not None else ()):
        if (k == "batch_script" or k.startswith("req_")) and isinstance(v, (str, int, float)):
            spawner[k] = v

# De-dup by (name, slug) preserving order.
seen = set()
uniq = []
for p in profiles:
    sig = (p["name"], p["slug"])
    if sig not in seen:
        seen.add(sig)
        uniq.append(p)

print(json.dumps({"ok": True, "source": used, "profiles": uniq,
                  "spawner": spawner}))
'''


# The longest a held allocation may be scaled to ask for. Batched remote mode
# runs every batch inside one allocation, so the ``--time`` a user sized for a
# single batch has to cover the whole plate — but multiplying it by the batch
# count can walk straight past a partition's MaxTime, and a job that asks for
# more than the partition allows is rejected outright or queues far worse. This
# is the ceiling that scaling clamps to.
MAX_HELD_ALLOCATION_S = 24 * 60 * 60


# How much of an archive the channel filter has to be dropping before it is
# worth rewriting that archive locally instead of uploading it as it stands.
#
# Rewriting is not free: the source is read, a new zip is written to a local
# volume, and only then does the upload start. It buys a smaller transfer. When
# the filter is shedding a couple of small frames out of a multi-gigabyte well,
# that trade is plainly bad — the local copy costs more than the bytes saved,
# and the remote pipeline ignores unrequested channels anyway, so the only cost
# of sending them is bandwidth.
#
# Measured on *compressed* bytes, which is what actually crosses the wire and
# what the zip's central directory reports without reading a member.
REWRITE_MIN_SAVING = 0.10

# Slurm accepts six spellings of a time limit. All of them, so a launcher that
# works on the cluster is not rejected here.
_SLURM_TIME_RE = re.compile(
    r"^(?:(?P<d>\d+)-)?(?P<a>\d+)(?::(?P<b>\d+))?(?::(?P<c>\d+))?$")


def parse_slurm_time(spec: str) -> int | None:
    """Seconds in a Slurm ``--time`` value, or ``None`` if it is not one.

    The six forms, which differ by how many colons are present and whether a
    day is prefixed: ``MM``, ``MM:SS``, ``HH:MM:SS``, ``DD-HH``, ``DD-HH:MM``,
    ``DD-HH:MM:SS``. The ambiguity is real — ``5`` is five *minutes* while
    ``1-5`` is one day and five *hours* — so the day prefix changes what the
    first field means, which is why this is a parser rather than a split.
    """
    m = _SLURM_TIME_RE.match((spec or "").strip())
    if not m:
        return None
    days = int(m.group("d") or 0)
    a, b, c = m.group("a"), m.group("b"), m.group("c")
    if m.group("d"):
        # DD-HH[:MM[:SS]] — the first field is hours.
        hours, minutes, seconds = int(a), int(b or 0), int(c or 0)
    elif c is not None:
        hours, minutes, seconds = int(a), int(b), int(c)
    elif b is not None:
        hours, minutes, seconds = 0, int(a), int(b)      # MM:SS
    else:
        hours, minutes, seconds = 0, int(a), 0           # MM
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def render_slurm_time(seconds: int) -> str:
    """*seconds* as a Slurm time limit, in the shortest unambiguous form."""
    seconds = max(60, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}-{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def launcher_time_seconds(launcher: str) -> int:
    """The launcher's ``--time`` in seconds, or ``0`` if it does not set one.

    Read rather than scaled: batched mode uses it as the budget for a *single*
    batch, which is what the number meant before one allocation started
    covering the whole plate.
    """
    if not launcher.strip():
        return 0
    try:
        tokens = shlex.split(launcher)
    except ValueError:
        return 0
    for i, token in enumerate(tokens):
        if token.startswith("--time="):
            return parse_slurm_time(token.split("=", 1)[1]) or 0
        if token in ("--time", "-t") and i + 1 < len(tokens):
            return parse_slurm_time(tokens[i + 1]) or 0
    return 0


def scale_launcher_time(
    launcher: str, factor: int, *, ceiling_s: int = MAX_HELD_ALLOCATION_S,
    transfer_allowance: int = 1,
) -> tuple[str, str]:
    """*launcher* sized for a whole batched run instead of one batch, and why.

    Returns ``(launcher, explanation)``. The explanation is empty when nothing
    changed, and is a log line otherwise — the app is editing scheduler
    arguments the user wrote, so it says so rather than doing it quietly.

    The multiplier is ``factor + transfer_allowance``, not ``factor``. A held
    allocation is not busy for exactly the compute it was sized for: the node
    also sits through every upload after the first, because the driver waits
    inside the allocation for each batch to arrive. Budgeting only the compute
    guarantees the walltime expires somewhere in the last batch of any run
    whose transfers are not free. One batch's budget is the allowance for all
    of them — a guess, so the log line says it is there and the tally names any
    batch the walltime cut short.

    A launcher with no time limit is returned **byte-identical**: the cluster's
    own default applies and inventing a number here would be worse than
    inheriting theirs. So is one already at or above the ceiling.

    Pure, so the six Slurm spellings and the clamp are unit-testable without a
    cluster.
    """
    if factor <= 1 or not launcher.strip():
        return launcher, ""
    scale = factor + max(0, int(transfer_allowance))
    try:
        tokens = shlex.split(launcher)
    except ValueError:
        return launcher, ""      # unbalanced quotes: not ours to rewrite

    def _replace(index: int, spec: str, rebuild) -> tuple[str, str] | None:
        current = parse_slurm_time(spec)
        if current is None:
            return None
        wanted = current * scale
        capped = min(wanted, int(ceiling_s))
        if capped <= current:
            return None
        tokens[index] = rebuild(render_slurm_time(capped))
        note = (
            f"[remote] scheduler: one allocation for all {factor} batches "
            f"(plus one batch's budget for the transfers between them), so "
            f"--time is scaled {spec} → {render_slurm_time(capped)}"
        )
        if capped < wanted:
            note += (f" (clamped at {render_slurm_time(int(ceiling_s))}; "
                     f"{render_slurm_time(wanted)} was asked for)")
        return shlex.join(tokens), note + ".\n"

    for i, token in enumerate(tokens):
        if token.startswith("--time="):
            out = _replace(i, token.split("=", 1)[1], lambda v: f"--time={v}")
            if out:
                return out
            return launcher, ""
        if token in ("--time", "-t") and i + 1 < len(tokens):
            out = _replace(i + 1, tokens[i + 1], lambda v: v)
            if out:
                return out
            return launcher, ""
    return launcher, ""


# batchspawner req_* kwarg → srun flag. Order is the rendered flag order.
_REQ_FLAG_MAP: tuple[tuple[str, str], ...] = (
    ("req_partition", "--partition="),
    ("req_qos", "--qos="),
    ("req_account", "--account="),
    ("req_reservation", "--reservation="),
    ("req_constraint", "--constraint="),
    ("req_nprocs", "--cpus-per-task="),
    ("req_memory", "--mem="),
    ("req_runtime", "--time="),
)


def profile_launcher(
    kwargs: dict, *, batch_script: str = "", program: str = "srun",
) -> ScriptLauncher:
    """A JupyterHub profile's ``srun`` launcher, and what had to be left out.

    An explicit ``launcher`` / ``compute_launcher`` / ``cmd`` string on the
    profile wins outright. Otherwise, when the cluster configures a job
    template (*batch_script*), the template decides which option each value
    becomes — see :mod:`services.slurm_batch_script`. Without one, batchspawner's
    ``req_*`` fields map by name (``req_partition``, ``req_ngpus`` →
    ``--gres=gpu:N``, ``req_runtime`` → ``--time``, ``req_memory`` → ``--mem``,
    ``req_nprocs`` → ``--cpus-per-task``, the same names without the ``req_``
    prefix) and the free-form ``req_options`` is read as the ``#SBATCH``
    directive batchspawner's stock template writes it as: its resource options
    are kept and a word attached to no option is dropped and reported, never
    appended — ``srun`` would run it as the program. The launcher is "" when
    nothing scheduler-shaped is found (the caller skips such profiles). Pure.
    """
    if not isinstance(kwargs, dict):
        return ScriptLauncher("")
    for k in ("launcher", "compute_launcher", "cmd"):
        v = kwargs.get(k)
        if isinstance(v, str) and v.strip():
            return ScriptLauncher(v.strip())
    if isinstance(batch_script, str) and batch_script.strip():
        return launcher_from_batch_script(batch_script, kwargs, program=program)

    def _val(req_key: str) -> Any:
        # Accept both the batchspawner ``req_`` form and the bare key.
        if req_key in kwargs:
            return kwargs[req_key]
        bare = req_key[4:] if req_key.startswith("req_") else req_key
        return kwargs.get(bare)

    parts: list[str] = [program]
    dropped: list[str] = []
    for key, flag in _REQ_FLAG_MAP:
        val = _val(key)
        if val is not None and str(val).strip() != "":
            parts.append(f"{flag}{str(val).strip()}")
    ngpus = _val("req_ngpus")
    if ngpus is not None and str(ngpus).strip() not in ("", "0"):
        parts.append(f"--gres=gpu:{str(ngpus).strip()}")
    extra = _val("req_options")
    if isinstance(extra, str) and extra.strip():
        try:
            flags, stray = directive_flags(shlex.split(extra))
        except ValueError:
            flags, stray = [], [extra.strip()]
        parts.extend(flags)
        dropped.extend(f"“{w}” (not attached to any option)" for w in stray)
    parts = [program, *single_task(parts[1:])]
    if len(parts) == 1:
        return ScriptLauncher("", dropped)  # only the program token
    return ScriptLauncher(shlex.join(parts), dropped)


def profile_kwargs_to_launcher(kwargs: dict, *, program: str = "srun") -> str:
    """:func:`profile_launcher`'s launcher for a profile with no job template."""
    return profile_launcher(kwargs, program=program).launcher


def parse_jupyterhub_profiles_json(
    stdout: str, *, stderr: str = "", returncode: int = 0
) -> tuple[bool, list[tuple[str, str]], str]:
    """Parse the discovery snippet's JSON into ``(ok, [(name, launcher)], detail)``.

    On success *detail* names anything a profile's launcher had to leave out
    (empty when nothing was), so the dialog can say so beside the list.

    Tolerant of leading noise (login banners / motd on the ssh channel): if the
    whole stream isn't valid JSON it retries on the last non-empty line, which
    is where the snippet's single ``print`` lands.
    """
    text = (stdout or "").strip()
    if not text:
        detail = (stderr or "").strip() or f"no output (exit {returncode})"
        return (False, [], detail[:300])
    doc: Any = None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
            try:
                doc = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if not isinstance(doc, dict):
        return (False, [], ((stderr or text).strip() or "unparseable output")[:300])
    if not doc.get("ok"):
        return (False, [], str(doc.get("error") or "no profiles found")[:300])
    spawner = doc.get("spawner") if isinstance(doc.get("spawner"), dict) else {}
    defaults = {k: v for k, v in spawner.items() if k != "batch_script"}
    out: list[tuple[str, str]] = []
    notes: list[str] = []
    skipped = 0
    for p in doc.get("profiles", []):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("slug") or "profile").strip()
        own = p.get("kwargs") if isinstance(p.get("kwargs"), dict) else {}
        made = profile_launcher(
            {**defaults, **own},
            batch_script=str(own.get("batch_script") or spawner.get("batch_script") or ""))
        if made.launcher:
            out.append((name, made.launcher))
            if made.dropped:
                notes.append(f"“{name}” left out {'; '.join(made.dropped)}.")
        else:
            skipped += 1
    if not out:
        return (
            False, [],
            "JupyterHub config had no SLURM-shaped compute profiles"
            + (f" ({skipped} non-SLURM profile(s) skipped)." if skipped else "."),
        )
    return (True, out, " ".join(notes))


def query_jupyterhub_profiles(
    host: RemoteHost, *, timeout: float = 25.0
) -> tuple[bool, list[tuple[str, str]], str]:
    """SSH into ``host`` and discover its JupyterHub compute profiles.

    Reads ``jupyterhub_config.py`` on the remote (the host's
    ``jupyterhub_config_path`` if set, else conventional locations) and returns
    ``(ok, [(display_name, srun_launcher)], detail)``. The profiles are the
    discrete options the cluster's JupyterHub web UI would show; selecting one
    gives a ready-made :attr:`RemoteHost.compute_launcher`. Best-effort and
    side-effect-free — the GUI runs it on a worker thread and surfaces
    ``detail`` on failure.
    """
    remote_cmd = f"python3 -c {shlex.quote(_JHUB_PROFILES_SNIPPET)}"
    path = host.jupyterhub_config_path.strip()
    if path:
        remote_cmd += f" {shlex.quote(path)}"
    argv = host.ssh_argv(allocate_tty=False) + [remote_cmd]
    try:
        result = subprocess.run(
            argv, capture_output=True, timeout=timeout, **TEXT_UTF8,
        )
    except subprocess.TimeoutExpired:
        return (False, [], f"Timed out after {timeout:.0f}s contacting {host.ssh_alias}.")
    except OSError as exc:
        return (False, [], f"Could not launch ssh: {exc}")
    return parse_jupyterhub_profiles_json(
        result.stdout, stderr=result.stderr, returncode=result.returncode,
    )
