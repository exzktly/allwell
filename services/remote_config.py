"""Per-host remote-execution config for the AllWell remote-runner.

Loads host definitions from ``~/.config/allwell/remotes.toml`` (or the path
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
a scheduler-allocated compute node instead of the SSH host itself (see
``_Docs/Markdowns/REMOTE_EXECUTION_SLURM.md``)::

    [remote.cluster]
    ssh_alias = "hpc-login"
    compute_launcher = "srun --partition=gpu --gres=gpu:1 --time=4:00:00"
    ...
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

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


_DEFAULT_CONFIG_PATH = Path("~/.config/allwell/remotes.toml")


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
    remote_python: str = "~/miniforge3/envs/allwell/bin/python"
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
    """How long to wait for the scheduler to grant a node before giving up,
    in minutes (``0`` = wait indefinitely).

    On a scheduled host the run can sit in the queue while a node is
    allocated (or, on cloud/autoscaling clusters, provisioned) — a phase
    that emits nothing on the wire. AllWell keeps the SSH connection alive
    with a 30s keepalive; this value sets how long a quiet/unresponsive
    connection is tolerated before ssh gives up (it drives
    ``ServerAliveCountMax`` — see :meth:`ssh_argv`). The stock OpenSSH
    default (≈90s) is far too short for a node that has to spin up, so this
    is editable in the remote-server manager. Applies to direct hosts too,
    where it just makes a transiently-slow login more forgiving.
    """

    def _server_alive_count_max(self) -> int:
        """``ServerAliveCountMax`` derived from :attr:`queue_wait_timeout_min`.

        OpenSSH drops a connection after this many unanswered 30s keepalives,
        so ``count * 30s`` is the window a quiet connection (a queued/spinning-
        up node) survives. Two counts per minute, floored at 3; ``0`` ⇒
        effectively unlimited.
        """
        if self.queue_wait_timeout_min <= 0:
            return 1_000_000  # ~347 days at 30s — effectively "wait forever"
        return max(3, self.queue_wait_timeout_min * 2)

    def ssh_argv(self, *, allocate_tty: bool = True) -> list[str]:
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
        """
        argv = [resolve_ssh_binary()]
        if allocate_tty:
            argv.append("-tt")
        if self.ssh_key_path:
            argv += ["-i", str(Path(self.ssh_key_path).expanduser())]
        if not _is_windows():
            argv += [
                "-o", "ControlMaster=auto",
                "-o", f"ControlPath={_controlpath_template()}",
                "-o", "ControlPersist=60s",
            ]
        argv += [
            "-o", "ServerAliveInterval=30",
            "-o", f"ServerAliveCountMax={self._server_alive_count_max()}",
            self.ssh_alias,
        ]
        return argv


def _controlpath_template() -> str:
    """OpenSSH ControlPath template per-user, per-host, per-port.

    On POSIX the default ``%r@%h:%p`` expansion is fine. On Windows the
    expanded path lives on an NTFS / FAT filesystem where ``:`` is
    reserved (only legal as the drive-letter separator). A literal
    ``allwell-cm-user@host:22`` cannot be created as a file — Cygwin's
    OpenSSH (the Git-for-Windows binary we prefer) attempts the bind,
    Windows rejects the filename, ssh exits ``-1`` and Python surfaces
    it as ``4294967295`` (= 0xFFFFFFFF unsigned). Substitute ``_`` for
    ``:`` on Windows so the socket lands at a valid filename without
    changing the per-(user, host, port) uniqueness the template
    guarantees.
    """
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or Path.home() / ".ssh")
    base.mkdir(parents=True, exist_ok=True)
    sep = "_" if _is_windows() else ":"
    return str(base / f"allwell-cm-%r@%h{sep}%p")


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
        "  1. Drop rsync.exe and msys-2.0.dll into your Git for Windows "
        "``usr\\bin\\`` directory (download from a recent MSYS2 package: "
        "https://repo.msys2.org/msys/x86_64/).\n"
        "  2. Install cwRsync from https://itefix.net/cwrsync (free "
        "version is fine).\n"
        "  3. Install MSYS2 (https://www.msys2.org/), then "
        "``pacman -S rsync`` in an MSYS2 shell.\n"
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

    Honors ``$ALLWELL_REMOTES`` for tests and ad-hoc overrides.

    On Windows, ``%APPDATA%\\allwell\\remotes.toml`` takes precedence
    over the legacy ``~/.config/allwell/remotes.toml`` if it exists,
    matching the convention Windows users expect for per-user config.
    The legacy path is still used as a fall-back so an existing
    cross-platform user with ``~/.config/allwell`` keeps working.
    """
    override = os.environ.get("ALLWELL_REMOTES")
    if override:
        return Path(override).expanduser()
    if _is_windows():
        appdata = os.environ.get("APPDATA")
        if appdata:
            appdata_path = Path(appdata) / "allwell" / "remotes.toml"
            legacy = _DEFAULT_CONFIG_PATH.expanduser()
            if appdata_path.is_file() or not legacy.is_file():
                return appdata_path
    return _DEFAULT_CONFIG_PATH.expanduser()


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
    """Default index for the Analyze "Run on:" combo.

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
    by both :func:`write_remote` and ``scripts/_bootstrap_lib.py``.
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
    jhub_line = (
        f"jupyterhub_config_path = {_toml_string(host.jupyterhub_config_path)}\n"
        if host.jupyterhub_config_path else ""
    )
    return (
        f"[remote.{host.name}]\n"
        f"ssh_alias = {_toml_string(host.ssh_alias)}\n"
        f"{key_line}"
        f"{launcher_line}"
        f"{jhub_line}"
        f"remote_project_dir = {_toml_string(host.remote_project_dir)}\n"
        f"remote_staging_dir = {_toml_string(host.remote_staging_dir)}\n"
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
    p.write_text(new_text, encoding="utf-8")
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
    p.write_text(new_text, encoding="utf-8")
    return True


def update_bandwidth(name: str, mbps: float, path: Path | None = None) -> None:
    """Rewrite ``measured_bandwidth_mbps`` for ``name`` in place.

    Used by the runner after each successful upload to keep the rolling
    bandwidth estimate fresh. The write is best-effort: a missing or
    unwritable file logs a warning to stderr but does not raise — the
    estimate is a hint, not a correctness requirement.
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

    try:
        p.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    except OSError:
        pass


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

# De-dup by (name, slug) preserving order.
seen = set()
uniq = []
for p in profiles:
    sig = (p["name"], p["slug"])
    if sig not in seen:
        seen.add(sig)
        uniq.append(p)

print(json.dumps({"ok": True, "source": used, "profiles": uniq}))
'''


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


def profile_kwargs_to_launcher(kwargs: dict, *, program: str = "srun") -> str:
    """Map a JupyterHub profile's spawner kwargs to an ``srun`` launcher.

    Recognises batchspawner's ``req_*`` fields (``req_partition``,
    ``req_ngpus`` → ``--gres=gpu:N``, ``req_runtime`` → ``--time``,
    ``req_memory`` → ``--mem``, ``req_nprocs`` → ``--cpus-per-task``, plus raw
    ``req_options``) and the same names without the ``req_`` prefix. An
    explicit ``launcher`` / ``compute_launcher`` / ``cmd`` string on the
    profile wins outright. Returns "" when nothing scheduler-shaped is found
    (the caller skips such profiles). Pure — unit-tested without any ssh.
    """
    if not isinstance(kwargs, dict):
        return ""
    for k in ("launcher", "compute_launcher", "cmd"):
        v = kwargs.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()

    def _val(req_key: str) -> Any:
        # Accept both the batchspawner ``req_`` form and the bare key.
        if req_key in kwargs:
            return kwargs[req_key]
        bare = req_key[4:] if req_key.startswith("req_") else req_key
        return kwargs.get(bare)

    parts: list[str] = [program]
    for key, flag in _REQ_FLAG_MAP:
        val = _val(key)
        if val is not None and str(val).strip() != "":
            parts.append(f"{flag}{str(val).strip()}")
    ngpus = _val("req_ngpus")
    if ngpus is not None and str(ngpus).strip() not in ("", "0"):
        parts.append(f"--gres=gpu:{str(ngpus).strip()}")
    extra = _val("req_options")
    if isinstance(extra, str) and extra.strip():
        parts.extend(shlex.split(extra))
    if len(parts) == 1:
        return ""  # only the program token — nothing recognised
    return shlex.join(parts)


def parse_jupyterhub_profiles_json(
    stdout: str, *, stderr: str = "", returncode: int = 0
) -> tuple[bool, list[tuple[str, str]], str]:
    """Parse the discovery snippet's JSON into ``(ok, [(name, launcher)], detail)``.

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
    out: list[tuple[str, str]] = []
    skipped = 0
    for p in doc.get("profiles", []):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("slug") or "profile").strip()
        launcher = profile_kwargs_to_launcher(p.get("kwargs") or {})
        if launcher:
            out.append((name, launcher))
        else:
            skipped += 1
    if not out:
        return (
            False, [],
            "JupyterHub config had no SLURM-shaped compute profiles"
            + (f" ({skipped} non-SLURM profile(s) skipped)." if skipped else "."),
        )
    return (True, out, "")


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
            argv, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return (False, [], f"Timed out after {timeout:.0f}s contacting {host.ssh_alias}.")
    except OSError as exc:
        return (False, [], f"Could not launch ssh: {exc}")
    return parse_jupyterhub_profiles_json(
        result.stdout, stderr=result.stderr, returncode=result.returncode,
    )
