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
    "_Docs/requirements.txt",
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
) -> subprocess.CompletedProcess:
    """Run ``ssh <alias> <remote_argv>`` and return the result."""
    cmd = [_ssh_binary(), alias, *remote_argv]
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        input=input,
    )


# ── probes ─────────────────────────────────────────────────────────────────


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
    # shellcheck disable=SC1091
    . "$MINIFORGE_DIR/etc/profile.d/conda.sh" 2>/dev/null || true
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
"""


def probe_host(alias: str) -> HostProbe:
    """Single-ssh-round-trip probe of the remote's current state."""
    say(f"Probing {alias} for existing setup…")
    result = _ssh_run(alias, ["bash", "-s"], input=_PROBE_SCRIPT, check=True)
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
    )


# ── install steps ─────────────────────────────────────────────────────────


def install_miniforge_if_missing(alias: str, probe: HostProbe, force: bool) -> None:
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
    _ssh_run(alias, ["bash", "-s"], input=install_script, check=True, capture=False)


def ensure_env_and_staging(alias: str, probe: HostProbe, force: bool) -> None:
    """Make the conda env + staging directories if missing.

    The env's heavyweight pip dependencies (TensorFlow, StarDist, etc.)
    are installed by :func:`install_pip_deps` after the required files
    have been rsynced to the remote — that way pip can read
    ``_Docs/requirements.txt`` from disk.
    """
    setup_script = r"""
set -euo pipefail
MINIFORGE_DIR="$HOME/miniforge3"
ENV_NAME=allwell
# shellcheck disable=SC1091
source "$MINIFORGE_DIR/etc/profile.d/conda.sh"

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
    _ssh_run(alias, ["bash", "-s"], input=setup_script, check=True, capture=False)


def sync_required_files(alias: str) -> None:
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
    host = RemoteHost(name=alias, ssh_alias=alias)
    RemoteRunner(host).sync_required_files(log_fn=lambda m: sys.stdout.write(m))


def install_pip_deps(alias: str, probe: HostProbe) -> None:
    """Pip-install ``_Docs/requirements.txt`` in the conda env.

    Skipped when the probe found a usable tensorflow already — that's
    the most expensive part of bootstrap and re-doing it on every
    invocation would make ``--force``-less re-runs unbearably slow.
    """
    if probe.tensorflow_version:
        say(
            f"Remote already has tensorflow {probe.tensorflow_version} — "
            "skipping pip install."
        )
        return
    pip_script = r"""
set -euo pipefail
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate allwell
if [ -f "$HOME/allwell/_Docs/requirements.txt" ]; then
    pip install --no-build-isolation -r "$HOME/allwell/_Docs/requirements.txt"
else
    echo "ERROR: $HOME/allwell/_Docs/requirements.txt not found on remote." >&2
    exit 1
fi
"""
    say("Installing pip dependencies on remote (TensorFlow, StarDist, …)")
    _ssh_run(alias, ["bash", "-s"], input=pip_script, check=True, capture=False)


# ── bandwidth probe ────────────────────────────────────────────────────────


def measure_bandwidth(alias: str) -> float:
    """50 MB throwaway-file upload; return Mbps as a float.

    Uses a random-bytes payload so deflate / dedup heuristics in the SSH
    cipher or filesystem don't make the result optimistic.
    """
    say("Measuring upload bandwidth (50 MB throwaway)…")
    with tempfile.NamedTemporaryFile(prefix="allwell-bw-", suffix=".bin", delete=False) as fh:
        # 50 MB of os.urandom in 1 MB chunks — cheap on modern CPUs, no
        # disk allocation surprises.
        for _ in range(50):
            fh.write(os.urandom(1024 * 1024))
        local_probe = Path(fh.name)
    try:
        start = time.monotonic()
        subprocess.run(
            [
                _rsync_binary(), "-a",
                _to_rsync_path(local_probe),
                f"{alias}:/tmp/allwell-bw-probe",
            ],
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

    Idempotent: re-runs with the same alias leave the file untouched
    if a block for that alias already exists. Returns
    ``(path, block_string, wrote_changes)``.
    """
    path = cfg_path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cpu_only = "false" if probe.gpu_visible else "true"
    block = (
        f"[remote.{alias}]\n"
        f"ssh_alias = \"{alias}\"\n"
        f"remote_project_dir = \"{REMOTE_HOME_REPO}\"\n"
        f"remote_staging_dir = \"{REMOTE_STAGING}\"\n"
        f"remote_python = \"{REMOTE_PYTHON}\"\n"
        f"cpu_only = {cpu_only}\n"
        f"extra_args = []\n"
        f"cache_budget_gb = 200\n"
        f"measured_bandwidth_mbps = {mbps:.2f}\n"
    )

    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if re.search(rf"^\[remote\.{re.escape(alias)}\]\s*$", existing, re.MULTILINE):
        say(f"Config block for {alias} already exists at {path} — leaving file alone.")
        return path, block, False

    sep = "\n" if existing and not existing.endswith("\n\n") else ""
    new = existing + sep + block
    path.write_text(new, encoding="utf-8")
    say(f"Appended new block to {path}")
    return path, block, True


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

    # 1. Probe.
    probe = probe_host(args.alias)
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
            args.alias, ["bash", "-c", "rm -rf ~/miniforge3 ~/allwell"],
            check=False, capture=False,
        )
        probe = HostProbe(False, False, False, "", False, False, "")

    # 3. Install miniforge if missing.
    install_miniforge_if_missing(args.alias, probe, args.force)

    # 4. Ensure env + staging.
    ensure_env_and_staging(args.alias, probe, args.force)

    # 5. Sync required files.
    sync_required_files(args.alias)

    # 6. Install pip deps (skipped if TF already importable).
    install_pip_deps(args.alias, probe)

    # 7. Bandwidth probe.
    mbps = measure_bandwidth(args.alias)

    # 8. Write remotes.toml.
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
