"""Cross-platform bootstrap logic for AllWell remote-execution hosts.

The thin ``bootstrap_remote.sh`` (Linux / macOS / Git Bash) and
``bootstrap_remote.ps1`` (native Windows PowerShell) wrappers each
``exec`` this module. All the actual work — remote probes, miniforge
install, env create, file sync, bandwidth measurement, ``remotes.toml``
generation — lives here so the two wrappers stay three-liners and there
is exactly one source of truth.

The remote machine is still expected to be Linux: the probe / install
scripts this module ships over SSH are bash. What changed in v5.0.1 is
where the *orchestration* runs — Python, instead of bash on the local
machine.

Usage::

    python scripts/_bootstrap_lib.py <ssh-alias> [--force]
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# Repo root resolved from this file's location. Used to locate the four
# required pipeline files to sync to the remote.
REPO_ROOT = Path(__file__).resolve().parent.parent

# The minimum set of files the remote needs in order to run the
# pipeline. Kept in lockstep with services.remote_runner's contract — if
# that constant moves, this list must follow.
REQUIRED_FILES: tuple[str, ...] = (
    "process_microscopy.py",
    "auto_threshold_core.py",
    "well_token.py",
    # v5.2.1: headless-only requirements file (no PySide6/pyobjc/
    # pyinstaller). See _Docs/requirements.txt for the GUI superset.
    "_Docs/requirements-remote.txt",
)

ENV_NAME = "allwell"
REMOTE_HOME_REPO = "~/allwell"
REMOTE_STAGING = "~/.allwell-staging"
REMOTE_MINIFORGE = "~/miniforge3"
REMOTE_PYTHON = "~/miniforge3/envs/allwell/bin/python"


def say(msg: str) -> None:
    # ANSI cyan label, no colour on Windows cmd (which doesn't render
    # ANSI without `os.system("")` warming it up). Keep it simple.
    if sys.stdout.isatty() and os.name != "nt":
        sys.stdout.write(f"\033[1;36m[bootstrap]\033[0m {msg}\n")
    else:
        sys.stdout.write(f"[bootstrap] {msg}\n")
    sys.stdout.flush()


# ── data types ─────────────────────────────────────────────────────────────


@dataclass
class HostProbe:
    """Summary of what's already present on the remote.

    Populated by :func:`probe_host` from a single ssh round-trip. Drives
    the conditional install / sync / env-creation logic below.
    """

    has_conda: bool
    has_repo: bool
    has_env: bool
    tensorflow_version: str  # empty string if not importable
    stardist_ok: bool
    gpu_visible: bool
    python_version: str  # from the env's python, empty if env missing
    pip_in_progress: bool = False
    """True iff a detached pip install from a previous bootstrap
    invocation is still running on the remote. Drives the v5.1.1
    'resume tailing instead of restart' behavior in
    :func:`install_pip_deps`."""


# ── argv ───────────────────────────────────────────────────────────────────


def _argv_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bootstrap_remote",
        description=(
            "One-time idempotent setup of an AllWell remote-execution host. "
            "Probes the remote, installs miniforge / the conda env if missing, "
            "syncs the four required pipeline files, measures bandwidth, and "
            "writes a [remote.<alias>] block to ~/.config/allwell/remotes.toml."
        ),
    )
    p.add_argument("alias", help="The SSH alias of the remote host.")
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Remove the existing miniforge install + conda env on the remote "
            "before reinstalling. Use when the remote env is suspected corrupt."
        ),
    )
    return p


# ── ssh helpers ────────────────────────────────────────────────────────────


def _ssh_binary() -> str:
    """Return the ssh executable to use.

    On Windows prefer Git for Windows' OpenSSH (Cygwin build) so the same
    binary that ``services.remote_runner`` uses runs here too — keeps the
    ControlMaster path identical between the bootstrap and per-run code.
    Falls back to whatever ``ssh`` is on PATH.
    """
    if os.name == "nt":
        git_bash_ssh = Path(r"C:\Program Files\Git\usr\bin\ssh.exe")
        if git_bash_ssh.is_file():
            return str(git_bash_ssh)
    return "ssh"


def _rsync_binary() -> str:
    """Return the rsync executable to use.

    Same logic as ``_ssh_binary``: prefer the Git-for-Windows-bundled
    rsync on Windows, fall back to PATH.
    """
    if os.name == "nt":
        git_bash_rsync = Path(r"C:\Program Files\Git\usr\bin\rsync.exe")
        if git_bash_rsync.is_file():
            return str(git_bash_rsync)
    return "rsync"


def _to_rsync_path(p: Path | str) -> str:
    """Translate a local-OS path to the form rsync expects.

    POSIX: ``str(p)`` unchanged. Windows: drive letter rewritten as
    ``/c/Users/foo`` so the Cygwin-based rsync from Git for Windows
    accepts it.
    """
    if os.name != "nt":
        return str(p)
    abs_p = Path(p).resolve()
    drive, rest = os.path.splitdrive(str(abs_p))
    if drive:
        return "/" + drive[0].lower() + rest.replace("\\", "/")
    return str(abs_p).replace("\\", "/")


def _ssh_run(
    alias: str, remote_argv: list[str], *, check: bool = True,
    input: str | None = None, capture: bool = True,
    ssh_key_path: str = "",
) -> subprocess.CompletedProcess:
    """Run ``ssh <alias> <remote_argv>`` and return the result.

    v5.2.2: ``ssh_key_path`` is threaded through every bootstrap ssh
    call so a host whose only accepted credential is the wizard-
    generated ``~/.ssh/allwell_<alias>`` key can be re-bootstrapped
    after the wizard has set things up. When unset (typical first-
    bootstrap path), ssh falls back to its default identity search /
    ``~/.ssh/config`` exactly as before.

    v5.2.3: passes ``StrictHostKeyChecking=accept-new``. The bootstrap
    feeds the probe script in via ``input=...``, so ssh's stdin is a
    pipe — no place to prompt the user for "do you trust this host?".
    On a fresh ``known_hosts`` ssh would then exit 255 with the
    cryptic "Host key verification failed" message. ``accept-new``
    auto-trusts the host on first contact and enforces normal
    key-change detection on subsequent connects. ``ConnectTimeout``
    is set so a misconfigured / unreachable host fails fast instead
    of hanging on TCP-syn retries for the kernel default (~75s).

    v5.2.8: ``BatchMode=yes`` so an authentication failure produces a
    readable "Permission denied" exit code instead of dropping the
    user into an interactive password prompt that AllWell can't help
    them satisfy. Aligned with every other ssh invocation in the
    framework — the project's policy is "never store passwords; if
    keys don't work we want to see why, not endlessly retype". If
    the user genuinely needs password auth, ssh-copy-id (run by the
    user in their own terminal as the Setup SSH Key wizard's Step 2)
    handles that one-shot interaction.
    """
    cmd = [_ssh_binary()]
    if ssh_key_path:
        cmd += ["-i", str(Path(ssh_key_path).expanduser())]
    cmd += [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        "-o", "BatchMode=yes",
        alias, *remote_argv,
    ]
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        input=input,
    )


def _load_existing_key(alias: str) -> str:
    """Read ``ssh_key_path`` for ``alias`` from the user's remotes.toml.

    Returns "" when no entry exists yet (the typical first-time
    bootstrap case — the user hasn't run the wizard or hand-set a
    key). Best-effort: any TOML parse error is swallowed and we
    behave as "no key configured" so a broken config doesn't block
    bootstrap from making progress.
    """
    # Late import: this script is the bootstrap entry point and runs
    # from a wrapper that adds services/ to sys.path; at module-top
    # time the path may not include it yet.
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from services.remote_config import get_remote
        host = get_remote(alias)
    except Exception:  # noqa: BLE001 — best-effort
        return ""
    return host.ssh_key_path if host is not None else ""


# ── probes ─────────────────────────────────────────────────────────────────


# Bash snippet that finds and sources the right conda.sh wherever conda
# actually lives on the remote. Prepended to every heredoc that needs
# ``conda activate``. Necessary because the probe accepts EITHER
# ``$HOME/miniforge3`` (the bootstrap's install target) OR a pre-
# existing system conda — but the install / activate scripts used to
# hard-code ``$HOME/miniforge3``, which broke on hosts whose conda
# lives elsewhere (HPC modules, ``/opt/conda`` admin installs, etc.).
#
# Discovery order:
#   1. ``$HOME/miniforge3/bin/conda`` — the bootstrap's preferred path.
#   2. ``conda`` on PATH (system / admin conda); use ``conda info --base``
#      to resolve its actual root.
#
# If neither works the host's ``has_conda=true`` probe was lying;
# exit 1 with a clear diagnostic instead of letting bash blow up on
# an "ambiguous redirect" or "command not found".
_LOCATE_CONDA_SH = r"""
_aw_locate_conda_sh() {
    if [ -f "$HOME/miniforge3/etc/profile.d/conda.sh" ]; then
        printf '%s\n' "$HOME/miniforge3/etc/profile.d/conda.sh"
        return 0
    fi
    if command -v conda >/dev/null 2>&1; then
        local base
        base=$(conda info --base 2>/dev/null) || base=""
        if [ -n "$base" ] && [ -f "$base/etc/profile.d/conda.sh" ]; then
            printf '%s\n' "$base/etc/profile.d/conda.sh"
            return 0
        fi
    fi
    return 1
}
_AW_CONDA_SH=$(_aw_locate_conda_sh) || {
    echo "[bootstrap-remote] could not locate conda.sh — has the probe lied?" >&2
    echo "[bootstrap-remote] checked: \$HOME/miniforge3/etc/profile.d/conda.sh and \$(conda info --base)" >&2
    exit 1
}
# shellcheck disable=SC1090
source "$_AW_CONDA_SH"
"""


_PROBE_SCRIPT = r"""
set -u
MINIFORGE_DIR="$HOME/miniforge3"
REPO_DIR="$HOME/allwell"
ENV_NAME=allwell

has_conda() {
    if command -v conda >/dev/null 2>&1; then return 0; fi
    if [ -x "$MINIFORGE_DIR/bin/conda" ]; then return 0; fi
    return 1
}

# Output is a series of key=value lines that the local Python parses.
echo "has_conda=$(has_conda && echo true || echo false)"
echo "has_repo=$([ -d "$REPO_DIR" ] && echo true || echo false)"

ENV_PRESENT=false
TF_VERSION=
STARDIST_OK=false
GPU_VISIBLE=false
PY_VERSION=

if has_conda; then
    # v5.2.4: same discovery as the install scripts — prefer miniforge
    # in $HOME, fall back to a system conda's `conda info --base`. The
    # || true tolerates a host where the source itself fails (PATH
    # conda only), since `conda env list` works off PATH regardless.
    CONDA_SH=""
    if [ -f "$MINIFORGE_DIR/etc/profile.d/conda.sh" ]; then
        CONDA_SH="$MINIFORGE_DIR/etc/profile.d/conda.sh"
    elif command -v conda >/dev/null 2>&1; then
        _base=$(conda info --base 2>/dev/null || echo "")
        if [ -n "$_base" ] && [ -f "$_base/etc/profile.d/conda.sh" ]; then
            CONDA_SH="$_base/etc/profile.d/conda.sh"
        fi
    fi
    if [ -n "$CONDA_SH" ]; then
        # shellcheck disable=SC1090
        . "$CONDA_SH" 2>/dev/null || true
    fi
    if conda env list 2>/dev/null | awk '{print $1}' | grep -qx "$ENV_NAME"; then
        ENV_PRESENT=true
        conda activate "$ENV_NAME" 2>/dev/null || true
        PY_VERSION=$(python -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || true)
        TF_VERSION=$(python -c 'import tensorflow as tf; print(tf.__version__)' 2>/dev/null || true)
        if [ -n "$TF_VERSION" ]; then
            GPU_VISIBLE=$(python -c 'import tensorflow as tf; print(bool(tf.config.list_physical_devices("GPU")))' 2>/dev/null || echo False)
        fi
        if python -c 'import stardist' 2>/dev/null; then
            STARDIST_OK=true
        fi
    fi
fi

echo "has_env=$ENV_PRESENT"
echo "tensorflow_version=$TF_VERSION"
echo "stardist_ok=$STARDIST_OK"
echo "gpu_visible=$GPU_VISIBLE"
echo "python_version=$PY_VERSION"

# v5.1.1: detect a still-running detached pip install from a prior
# bootstrap. The marker is written by install_pip_deps; we only
# consider it "in progress" if the recorded PID is still alive.
PIP_RUNNING_MARKER="$HOME/.allwell-staging/.pip_running"
PIP_IN_PROGRESS=false
if [ -f "$PIP_RUNNING_MARKER" ]; then
    pid_val=$(cat "$PIP_RUNNING_MARKER" 2>/dev/null || true)
    if [ -n "$pid_val" ] && kill -0 "$pid_val" 2>/dev/null; then
        PIP_IN_PROGRESS=true
    fi
fi
echo "pip_in_progress=$PIP_IN_PROGRESS"
"""


def probe_host(alias: str, *, ssh_key_path: str = "") -> HostProbe:
    """Single-ssh-round-trip probe of the remote's current state."""
    say(f"Probing {alias} for existing setup…")
    result = _ssh_run(
        alias, ["bash", "-s"],
        input=_PROBE_SCRIPT, check=True,
        ssh_key_path=ssh_key_path,
    )
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k.strip()] = v.strip()

    def _bool(key: str) -> bool:
        return fields.get(key, "").lower() == "true"

    return HostProbe(
        has_conda=_bool("has_conda"),
        has_repo=_bool("has_repo"),
        has_env=_bool("has_env"),
        tensorflow_version=fields.get("tensorflow_version", ""),
        stardist_ok=_bool("stardist_ok"),
        gpu_visible=_bool("gpu_visible"),
        python_version=fields.get("python_version", ""),
        pip_in_progress=_bool("pip_in_progress"),
    )


# ── install steps ─────────────────────────────────────────────────────────


def install_miniforge_if_missing(
    alias: str, probe: HostProbe, force: bool, *, ssh_key_path: str = "",
) -> None:
    """Install miniforge under ``$HOME/miniforge3`` on the remote.

    Idempotent: skipped when the probe found an existing conda unless
    ``force`` is set, in which case the existing install is removed and
    a fresh one is laid down.
    """
    if probe.has_conda and not force:
        say("miniforge already present on remote — skipping install")
        return

    install_script = r"""
set -euo pipefail
MINIFORGE_DIR="$HOME/miniforge3"
if [ -d "$MINIFORGE_DIR" ]; then
    echo "[bootstrap-remote] removing existing miniforge dir for clean install" >&2
    rm -rf "$MINIFORGE_DIR"
fi
UNAME="$(uname -s)-$(uname -m)"
INSTALLER="/tmp/miniforge-${UNAME}.sh"
URL="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-${UNAME}.sh"
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$URL" -o "$INSTALLER"
elif command -v wget >/dev/null 2>&1; then
    wget -q "$URL" -O "$INSTALLER"
else
    echo "Need curl or wget on remote to fetch miniforge" >&2
    exit 1
fi
bash "$INSTALLER" -b -p "$MINIFORGE_DIR"
rm -f "$INSTALLER"
"""
    say("Installing miniforge on remote (this is the big one-time download)…")
    _ssh_run(
        alias, ["bash", "-s"],
        input=install_script, check=True, capture=False,
        ssh_key_path=ssh_key_path,
    )


def ensure_env_and_staging(
    alias: str, probe: HostProbe, force: bool, *, ssh_key_path: str = "",
) -> None:
    """Make the conda env + staging directories if missing.

    The env's heavyweight pip dependencies (TensorFlow, StarDist, etc.)
    are installed by :func:`install_pip_deps` after the required files
    have been rsynced to the remote — that way pip can read
    ``_Docs/requirements.txt`` from disk.
    """
    # v5.2.4: prepend conda.sh discovery so this works whether the host
    # has miniforge at $HOME or a pre-existing system conda elsewhere
    # (HPC modules, /opt/conda, etc.). Without it, install_miniforge_
    # if_missing is correctly skipped for the system-conda case but the
    # next step here used to fail with "source: no such file or
    # directory" on $HOME/miniforge3/etc/profile.d/conda.sh.
    setup_script = "set -euo pipefail\nENV_NAME=allwell\n" + _LOCATE_CONDA_SH + r"""

# Staging dirs always exist after this.
mkdir -p "$HOME/.allwell-staging/datasets" "$HOME/.allwell-staging/runs"

# Placeholder repo dir so rsync has somewhere to land.
mkdir -p "$HOME/allwell/_Docs"

if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    echo "[bootstrap-remote] env '$ENV_NAME' already present" >&2
else
    echo "[bootstrap-remote] creating conda env '$ENV_NAME' (python 3.10.14)…" >&2
    conda create -y -n "$ENV_NAME" python=3.10.14 setuptools=69.5.1 \
        "numpy>=1.24,<1.25" scipy scikit-image matplotlib pillow imageio h5py \
        tomli -c conda-forge >&2
fi
"""
    say("Ensuring conda env + staging dirs on remote…")
    _ssh_run(
        alias, ["bash", "-s"],
        input=setup_script, check=True, capture=False,
        ssh_key_path=ssh_key_path,
    )


def sync_required_files(alias: str, *, ssh_key_path: str = "") -> None:
    """Rsync the four pipeline files to ``~/allwell/`` on the remote.

    Delegates to :class:`services.remote_runner.RemoteRunner` so the
    runner's per-run sync helper and this one-shot bootstrap call use
    exactly the same code path — no duplicate rsync logic, no Windows-
    path-translation bug that affects one but not the other.
    """
    # Import locally so the script works even if services/ isn't on
    # sys.path (the bash/PowerShell wrappers add it).
    sys.path.insert(0, str(REPO_ROOT))
    from services.remote_config import RemoteHost
    from services.remote_runner import RemoteRunner

    say(f"Syncing {len(REQUIRED_FILES)} required pipeline files → {alias}:~/allwell/")
    # v5.2.2: include ssh_key_path so rsync's underlying ssh transport
    # picks up the wizard-generated key. Without this, sync would
    # bare-ssh and fail with "Permission denied" on hosts that only
    # accept the new key.
    host = RemoteHost(
        name=alias, ssh_alias=alias, ssh_key_path=ssh_key_path,
    )
    RemoteRunner(host).sync_required_files(log_fn=lambda m: sys.stdout.write(m))


def install_pip_deps(
    alias: str, probe: HostProbe, *, ssh_key_path: str = "",
) -> None:
    """Pip-install ``_Docs/requirements.txt`` in the conda env.

    Skipped when the probe found a usable tensorflow already — that's
    the most expensive part of bootstrap and re-doing it on every
    invocation would make ``--force``-less re-runs unbearably slow.

    v5.1.1: the pip install is wrapped in ``nohup`` and redirected to a
    log file, with a ``.pip_running`` marker on the remote. If the SSH
    session drops mid-install, pip keeps going. The local side tails
    the log file (or detects the marker on the next bootstrap and
    resumes tailing) so the user sees progress restore without losing
    the 10–30 minute install. The local ssh waits for the pip process
    to exit so the synchronous code path stays as it was on a stable
    connection.
    """
    if probe.tensorflow_version:
        say(
            f"Remote already has tensorflow {probe.tensorflow_version} — "
            "skipping pip install."
        )
        return
    if probe.pip_in_progress:
        say(
            "A previous bootstrap left pip running detached on the remote. "
            "Re-attaching to its log instead of starting a second install."
        )

    # The wrapper: nohup keeps pip alive past SSH hangup; .pip_running
    # tells the next bootstrap to tail rather than restart; .pip_done /
    # .pip_failed are the terminal markers. We `wait` on pip in the
    # foreground so the local user sees real-time output, but if ssh
    # drops the wait dies and pip continues to completion.
    # v5.2.4: same conda.sh discovery treatment as ensure_env_and_staging
    # so a host using a system / non-miniforge conda doesn't fail here
    # after install_miniforge_if_missing was correctly skipped.
    pip_script = "set -euo pipefail\n" + _LOCATE_CONDA_SH + r"""
conda activate allwell
STAGING="$HOME/.allwell-staging"
mkdir -p "$STAGING"
PIPLOG="$STAGING/.pip_install.log"
RUNNING="$STAGING/.pip_running"
DONE="$STAGING/.pip_done"
FAILED="$STAGING/.pip_failed"

REQ_FILE="$HOME/allwell/_Docs/requirements-remote.txt"
if [ -f "$REQ_FILE" ]; then
    # If a previous bootstrap left pip running in the background, just
    # tail its log — don't start a second pip in parallel.
    if [ -f "$RUNNING" ] && kill -0 "$(cat "$RUNNING")" 2>/dev/null; then
        echo "[bootstrap-remote] pip install already running (pid $(cat "$RUNNING")); tailing log…" >&2
        tail -n +1 --pid="$(cat "$RUNNING")" -f "$PIPLOG"
        if [ -f "$DONE" ]; then exit 0; fi
        if [ -f "$FAILED" ]; then exit 1; fi
        # Process exited but no marker — treat as failure.
        echo "[bootstrap-remote] pip process gone but no done/failed marker — investigate $PIPLOG" >&2
        exit 1
    fi
    rm -f "$DONE" "$FAILED"
    : > "$PIPLOG"
    # Launch pip detached. Use a subshell so the PID we capture is the
    # one whose stdout/stderr lands in PIPLOG.
    (
        ( pip install --no-build-isolation -r "$REQ_FILE" \
            > "$PIPLOG" 2>&1 && touch "$DONE" ) \
        || touch "$FAILED"
        rm -f "$RUNNING"
    ) </dev/null > /dev/null 2>&1 &
    PIP_PID=$!
    echo "$PIP_PID" > "$RUNNING"
    echo "[bootstrap-remote] pip running detached as pid $PIP_PID; tailing log…" >&2
    tail -n +1 --pid="$PIP_PID" -f "$PIPLOG"
    wait "$PIP_PID" || true
    if [ -f "$DONE" ]; then exit 0; fi
    exit 1
else
    echo "ERROR: $REQ_FILE not found on remote." >&2
    echo "(v5.2.1 split the GUI superset off into _Docs/requirements.txt;" >&2
    echo " the headless deps live in _Docs/requirements-remote.txt — make" >&2
    echo " sure your local working copy has that file and re-run bootstrap.)" >&2
    exit 1
fi
"""
    say(
        "Installing pip dependencies on remote (detached; tails log to local). "
        "If the SSH connection drops, pip keeps running and the next bootstrap "
        "resumes tailing."
    )
    _ssh_run(
        alias, ["bash", "-s"],
        input=pip_script, check=True, capture=False,
        ssh_key_path=ssh_key_path,
    )


# ── bandwidth probe ────────────────────────────────────────────────────────


def measure_bandwidth(alias: str, *, ssh_key_path: str = "") -> float:
    """50 MB throwaway-file upload; return Mbps as a float.

    Uses a random-bytes payload so deflate / dedup heuristics in the SSH
    cipher or filesystem don't make the result optimistic.

    v5.2.2: when ssh_key_path is set, rsync gets ``-e "ssh -i <key>"``
    so the underlying ssh transport uses the wizard-generated key
    instead of falling back to ssh's default identity search. The
    cleanup ssh call gets the same -i treatment.
    """
    say("Measuring upload bandwidth (50 MB throwaway)…")
    with tempfile.NamedTemporaryFile(prefix="allwell-bw-", suffix=".bin", delete=False) as fh:
        # 50 MB of os.urandom in 1 MB chunks — cheap on modern CPUs, no
        # disk allocation surprises.
        for _ in range(50):
            fh.write(os.urandom(1024 * 1024))
        local_probe = Path(fh.name)
    # v5.2.3: pass StrictHostKeyChecking=accept-new + ConnectTimeout
    # through rsync's underlying ssh too, matching the policy in
    # _ssh_run. Without these, a first-contact bootstrap survives
    # probe_host (because that ssh now has accept-new) only to fail
    # again here at the bandwidth probe, where rsync uses its own ssh.
    rsync_argv = [_rsync_binary(), "-a"]
    ssh_inner = [
        "ssh",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        # v5.2.8: BatchMode=yes so rsync's transport ssh fails fast
        # with "Permission denied" instead of dropping the user into
        # an unsatisfiable password prompt mid-bandwidth-probe.
        "-o", "BatchMode=yes",
    ]
    if ssh_key_path:
        # rsync uses an external ssh command set via -e. Shell-quote
        # the key path so a path with spaces survives the round-trip
        # through whatever shell rsync invokes underneath.
        expanded_key = str(Path(ssh_key_path).expanduser())
        ssh_inner += ["-i", shlex.quote(expanded_key)]
    rsync_argv += ["-e", " ".join(ssh_inner)]
    rsync_argv += [
        _to_rsync_path(local_probe),
        f"{alias}:/tmp/allwell-bw-probe",
    ]
    try:
        start = time.monotonic()
        subprocess.run(
            rsync_argv,
            check=True,
            capture_output=True,
            text=True,
        )
        elapsed = max(time.monotonic() - start, 1e-6)
    finally:
        try:
            local_probe.unlink()
        except OSError:
            pass
        _ssh_run(
            alias, ["rm", "-f", "/tmp/allwell-bw-probe"],
            check=False, capture=True,
            ssh_key_path=ssh_key_path,
        )

    mbps = (50 * 8) / elapsed
    say(f"Upload bandwidth: {mbps:.1f} Mbps (50 MB in {elapsed:.2f}s)")
    return mbps


# ── remotes.toml generation ────────────────────────────────────────────────


def default_config_path() -> Path:
    """Where to write ``remotes.toml``.

    On Windows we prefer ``%APPDATA%\\allwell\\remotes.toml`` (the
    convention Windows users expect) but only if ``%APPDATA%`` is set
    and the legacy ``~/.config/allwell/remotes.toml`` doesn't already
    exist. The legacy path stays the default everywhere else for
    cross-platform consistency.
    """
    legacy = Path("~/.config/allwell/remotes.toml").expanduser()
    if os.name == "nt" and not legacy.is_file():
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "allwell" / "remotes.toml"
    return legacy


def write_or_update_remotes_toml(
    alias: str, probe: HostProbe, mbps: float, cfg_path: Path | None = None,
) -> tuple[Path, str, bool]:
    """Write a ``[remote.<alias>]`` block to the user's remotes.toml.

    Idempotent: re-runs with the same alias preserve any prior tuning
    the user did to the block (e.g. ``extra_args``) and only refresh
    ``measured_bandwidth_mbps`` + ``cpu_only`` from the latest probe.
    Returns ``(path, block_string, wrote_changes)``.

    Since v5.1.0 this delegates to
    :func:`services.remote_config.write_remote` so the bootstrap path
    and the GUI's "Manage Remotes" dialog produce byte-identical
    blocks.
    """
    # Late import so this module stays usable when services/ isn't on
    # sys.path yet (early in the wrapper script).
    sys.path.insert(0, str(REPO_ROOT))
    from services.remote_config import (
        RemoteHost, _format_block, get_remote, write_remote,
    )

    path = cfg_path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Start from any prior values for this alias (preserves the user's
    # extra_args / cache_budget_gb / remote_project_dir customisations
    # across re-bootstraps); fall back to defaults otherwise.
    prior = get_remote(alias, path=path)
    base = prior or RemoteHost(name=alias, ssh_alias=alias)
    from dataclasses import replace
    # v5.2.9: do NOT overwrite ssh_alias with the section name. A user
    # may have set up `[remote.j]` with `ssh_alias = "jupyterhub"` so
    # the section is short to type but the ssh target matches their
    # ~/.ssh/config entry. Pre-v5.2.9 this `replace(base, ssh_alias=
    # alias)` clobbered the user's mapping and broke every subsequent
    # ssh attempt. The `base.ssh_alias or alias` fallback covers the
    # fresh-entry case (prior is None → base.ssh_alias == alias
    # already, so the `or` is a no-op).
    host = replace(
        base,
        name=alias,
        ssh_alias=base.ssh_alias or alias,
        remote_project_dir=base.remote_project_dir or REMOTE_HOME_REPO,
        remote_staging_dir=base.remote_staging_dir or REMOTE_STAGING,
        remote_python=base.remote_python or REMOTE_PYTHON,
        cpu_only=not probe.gpu_visible,
        measured_bandwidth_mbps=mbps,
    )
    block = _format_block(host)
    changed = write_remote(host, path=path)
    if changed:
        say(f"Wrote block for {alias} to {path}")
    else:
        say(f"Config block for {alias} already up to date in {path}")
    return path, block, changed


# ── orchestration ──────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = _argv_parser().parse_args(argv)

    # Sanity: make sure the four required files actually exist locally
    # before we go through the trouble of probing the remote.
    missing = [f for f in REQUIRED_FILES if not (REPO_ROOT / f).is_file()]
    if missing:
        print(
            f"ERROR: required pipeline files missing locally: {missing}",
            file=sys.stderr,
        )
        return 1

    # 0. v5.2.2: pick up any ssh_key_path the user already configured for
    # this alias (via the wizard, or by hand). When present every ssh /
    # rsync call in this bootstrap will pass -i <key>, so a host that
    # only accepts the wizard key still bootstraps successfully on a
    # second run. When absent (typical first bootstrap), behavior is
    # unchanged — ssh uses its default identity search / ~/.ssh/config.
    key_path = _load_existing_key(args.alias)
    if key_path:
        say(f"Using SSH key from remotes.toml: {key_path}")
    else:
        # v5.2.8: helpful guidance instead of a cryptic Permission
        # denied later. We don't refuse to proceed — ssh-agent /
        # default keys / SSH config IdentityFile entries are valid
        # paths a user might rely on — but we warn loudly so the
        # "wizard never finished saving" failure mode is obvious.
        say(
            f"No ssh_key_path configured for '{args.alias}' in remotes.toml. "
            "Bootstrap will attempt ssh-agent / default identities. "
            "If that fails with Permission denied, run the Setup SSH Key "
            "wizard from the AllWell GUI and OK the form to save the key."
        )

    # v5.2.9: the CLI argv (`args.alias`) is the [remote.<X>] SECTION
    # name in remotes.toml. The actual ssh-config target (what we pass
    # to `ssh`) is the `ssh_alias` field, which can differ — a user
    # named the section "j" but wired ssh_alias="jupyterhub.example.edu"
    # via their ~/.ssh/config. Resolve the ssh_alias so every ssh call
    # below targets the right host.
    ssh_target = args.alias
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from services.remote_config import get_remote
        host_entry = get_remote(args.alias)
    except Exception:  # noqa: BLE001 — best-effort
        host_entry = None
    if host_entry is not None and host_entry.ssh_alias:
        ssh_target = host_entry.ssh_alias
        if ssh_target != args.alias:
            say(
                f"[remote.{args.alias}] ssh_alias = {ssh_target!r}; "
                "using as ssh target."
            )

    # 1. Probe.
    # v5.2.8: the probe is the very first ssh call we make. If it fails
    # we surface a helpful diagnostic and exit 2 instead of letting the
    # bare CalledProcessError bubble up — the traceback dump is
    # confusing for non-Python users and obscures the real next step.
    # v5.2.9: target ssh_target (host.ssh_alias when known, else
    # args.alias), so a `[remote.j]` section with ssh_alias="jupyterhub"
    # actually ssh's to "jupyterhub", not "j".
    try:
        probe = probe_host(ssh_target, ssh_key_path=key_path)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        print(
            f"\nERROR: ssh to '{ssh_target}' failed (exit {exc.returncode}).\n",
            file=sys.stderr,
        )
        if stderr:
            print(f"  ssh said:\n    {stderr.replace(chr(10), chr(10) + '    ')}\n",
                  file=sys.stderr)
        print(
            "Most likely causes (in order):\n"
            f"  • No SSH key configured for '{args.alias}' in remotes.toml.\n"
            "    → Open AllWell → Manage Remotes → Edit → Setup SSH Key…\n"
            "      then OK the form to persist ssh_key_path.\n"
            "  • The wizard's key exists but isn't installed on the remote.\n"
            "    → Re-run the wizard's Step 2 (copy command in your terminal).\n"
            "  • Stale host key in ~/.ssh/known_hosts after host re-creation.\n"
            f"    → ssh-keygen -R {ssh_target}\n"
            "  • Network / DNS / firewall — verify with `ssh -v "
            f"{ssh_target} echo ok` in your terminal.",
            file=sys.stderr,
        )
        return 2
    say(
        f"Probe result: conda={probe.has_conda} env={probe.has_env} "
        f"repo={probe.has_repo} tf={probe.tensorflow_version or '-'} "
        f"gpu={probe.gpu_visible}"
    )

    # 2. Optional force-clean. We do this by re-probing as 'absent' so the
    #    install steps below behave as if it's a fresh host.
    if args.force:
        say("--force: removing existing miniforge + env on remote")
        _ssh_run(
            ssh_target, ["bash", "-c", "rm -rf ~/miniforge3 ~/allwell"],
            check=False, capture=False,
            ssh_key_path=key_path,
        )
        probe = HostProbe(False, False, False, "", False, False, "")

    # 3. Install miniforge if missing.
    install_miniforge_if_missing(
        ssh_target, probe, args.force, ssh_key_path=key_path,
    )

    # 4. Ensure env + staging.
    ensure_env_and_staging(
        ssh_target, probe, args.force, ssh_key_path=key_path,
    )

    # 5. Sync required files.
    sync_required_files(ssh_target, ssh_key_path=key_path)

    # 6. Install pip deps (skipped if TF already importable).
    install_pip_deps(ssh_target, probe, ssh_key_path=key_path)

    # 7. Bandwidth probe.
    mbps = measure_bandwidth(ssh_target, ssh_key_path=key_path)

    # 8. Write remotes.toml. Uses the SECTION name (args.alias) so a
    # user who set up [remote.j] keeps that section, but
    # write_or_update_remotes_toml preserves any pre-existing
    # ssh_alias so the user's custom mapping survives.
    path, block, wrote = write_or_update_remotes_toml(args.alias, probe, mbps)

    # 9. Final report.
    print()
    print("──────────────────────────────────────────────────────────────────────────")
    print(f"Bootstrap complete for {args.alias}.")
    print()
    print("Try it:")
    print(f"    python scripts/allwell_remote.py --remote {args.alias} \\")
    print("        --input_dir <your-plate-dir> --output_dir ./results")
    print()
    print(f"Config file: {path}")
    print("Block:")
    print(block)
    print("──────────────────────────────────────────────────────────────────────────")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
