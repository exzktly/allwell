"""SSH key generation + remote install helpers for the v5.2.1 wizard.

A user-facing dialog runs through three steps:

1. Generate an Ed25519 keypair at ``~/.ssh/allwell_<alias>`` (idempotent).
2. Show the user the exact ``ssh-copy-id`` command to run in their own
   terminal — that's the single step that needs the remote password.
3. Verify by running ``ssh -i <key> <alias> echo ok`` and confirming the
   ``ok`` response.

This module is intentionally GUI-agnostic so the dialog code stays thin
and the same helpers are reusable from a CLI wizard later if needed.

Why not pipe the password through Qt? Spawning ssh inside a Python
subprocess and piping a password requires either pexpect (POSIX-only)
or a PTY (cross-platform but complex). Letting the user run
``ssh-copy-id`` in their own terminal:

* uses ssh's well-tested password prompt;
* never puts the password in AllWell's address space;
* works identically on macOS, Linux, and Git-Bash-on-Windows;
* lets the user scroll back through the ssh output if something fails.

The trade is one extra click ("I've done that") for a meaningfully
better security and portability story.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from services.remote_config import resolve_ssh_binary


def default_key_path(alias: str) -> Path:
    """Return the conventional v5.2.1 key path for an alias.

    Lives under ``~/.ssh`` because:

    * That's where every SSH tool looks by default.
    * The directory's 0700 permissions are enforced by ssh itself.
    * Backing it up or sharing across machines is a known workflow.

    The alias is sanitised — only ``[A-Za-z0-9_-]`` are kept — so a
    pathological alias can't escape the ssh dir.
    """
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in alias)
    return Path("~/.ssh").expanduser() / f"allwell_{safe}"


def generate_keypair(
    path: Path, *, comment: str | None = None,
) -> tuple[bool, str]:
    """Generate a passphrase-less Ed25519 keypair at ``path``.

    Idempotent: if both ``path`` and ``path.with_suffix(".pub")`` already
    exist, this is a no-op that returns ``(False, "")`` to signal "no
    work done". Otherwise generates the keypair and returns
    ``(True, "")`` on success. On failure returns ``(False, <stderr>)``
    so the caller can surface the message.

    The key is generated WITHOUT a passphrase. A passphrase would mean
    every ssh call has to prompt for it (defeating the purpose of using
    a key over a password); users who want a passphrase can encrypt the
    key separately with ``ssh-keygen -p`` and configure ``ssh-agent``.
    """
    pub = path.with_suffix(path.suffix + ".pub") if path.suffix else Path(str(path) + ".pub")
    if path.is_file() and pub.is_file():
        return False, ""

    # Make sure ~/.ssh exists with correct perms — ssh refuses to use a
    # key inside a world-readable directory, and a fresh Windows user
    # may not have ~/.ssh at all.
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        # Windows ignores chmod the way POSIX expects; the ssh ACL on
        # Windows is handled differently. Not fatal.
        pass

    cmd = [
        "ssh-keygen",
        "-t", "ed25519",
        "-f", str(path),
        "-N", "",                       # empty passphrase
        "-C", comment or f"allwell-{path.name}",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"could not run ssh-keygen: {exc}"
    if result.returncode != 0:
        return False, result.stderr.strip() or f"ssh-keygen exited {result.returncode}"
    return True, ""


def has_ssh_copy_id() -> bool:
    """Whether ``ssh-copy-id`` is available on this machine.

    Standard on macOS / Linux / Git Bash for Windows; missing on bare
    Windows OpenSSH. The wizard offers a fallback when it's not.
    """
    return shutil.which("ssh-copy-id") is not None


def build_copy_command(ssh_alias: str, key_path: Path) -> str:
    """Return the exact shell command the user should run to install
    the public key on the remote.

    Prefers ``ssh-copy-id`` when available; falls back to a manual
    ``cat | ssh`` pipeline that works wherever bare ssh works.

    The returned string is shell-escape-safe (paths and alias are
    quoted) and intended to be copy-pasted into the user's own
    terminal. We never execute it ourselves so the user's password
    stays out of AllWell's process.
    """
    key_path = key_path.expanduser()
    pub_path = key_path.with_suffix(key_path.suffix + ".pub")
    quoted_alias = shlex.quote(ssh_alias)
    quoted_pub = shlex.quote(str(pub_path))
    if has_ssh_copy_id():
        return f"ssh-copy-id -i {quoted_pub} {quoted_alias}"
    # Manual fallback. Use cat | ssh because that's portable across
    # ssh implementations and shells. mkdir -p && chmod set the right
    # perms on a freshly-created ~/.ssh on the remote.
    return (
        f"cat {quoted_pub} | ssh {quoted_alias} "
        f"'mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        f"cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'"
    )


def verify_key_works(
    ssh_alias: str, key_path: Path, *, timeout: int = 10,
) -> tuple[bool, str]:
    """Test that the key is now accepted on the remote.

    Runs ``ssh -i <key> -o BatchMode=yes <alias> echo ok``. BatchMode
    disables interactive password prompts, so if the key isn't
    installed the call fails fast with a clear message instead of
    hanging or asking for a password.

    Returns ``(True, "")`` on ``ok`` response, else ``(False, <detail>)``.
    """
    key_path = key_path.expanduser()
    cmd = [
        resolve_ssh_binary(),
        "-i", str(key_path),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        ssh_alias,
        "echo", "ok",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode == 0 and result.stdout.strip() == "ok":
        return True, ""
    detail = (result.stderr or result.stdout).strip()
    if not detail:
        detail = f"ssh exited {result.returncode}"
    # Truncate to keep dialog messageboxes readable; the full output is
    # usually two lines, but a misconfigured host can produce a page
    # of warnings.
    return False, detail[:240]
