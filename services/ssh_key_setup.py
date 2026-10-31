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

from services.remote_config import keepalive_options, known_hosts_options, resolve_ssh_binary
from services.subprocess_text import TEXT_UTF8


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

    Residual risk (audit X11): a passphrase-less private key is a bearer
    credential — anyone who can read the file gains the same cluster access
    the user has, with no second factor. This is the standard trade-off for
    unattended HPC automation (rsync/SLURM under ``BatchMode=yes`` can't
    answer an interactive prompt), and is mitigated, not eliminated, by:

    * ``~/.ssh`` forced to ``0700`` and the private key to ``0600`` below, so
      only the owner can read it (ssh itself refuses looser permissions);
    * the key granting access to the *analysis* account only, scoped by the
      cluster's own ``authorized_keys``/account policy;
    * users for whom that residual risk is unacceptable encrypting the key
      with ``ssh-keygen -p`` + ``ssh-agent`` (AllWell detects a
      passphrase-protected key via :func:`key_has_passphrase` and guides the
      user rather than failing opaquely).
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
            cmd, capture_output=True, timeout=30, **TEXT_UTF8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"could not run ssh-keygen: {exc}"
    if result.returncode != 0:
        return False, result.stderr.strip() or f"ssh-keygen exited {result.returncode}"
    # Defense-in-depth (audit X11): ssh-keygen already writes the private key
    # 0600, but make the owner-only mitigation explicit so the passphrase-less
    # key can't be left group/world-readable. No-op on Windows (POSIX perms
    # ignored; the NTFS ACL is inherited from ~/.ssh).
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return True, ""


def key_has_passphrase(path: Path) -> bool:
    """Return True iff the private key at ``path`` is protected by a
    passphrase that ``BatchMode=yes`` ssh can't satisfy non-interactively.

    AllWell's policy is "never store passwords / passphrases" — every
    ssh and rsync call runs with ``BatchMode=yes`` so a passphrase
    prompt turns into "Permission denied" rather than a hang. A user
    who unknowingly reuses a passphrase-protected key (manually
    generated, or left over from a previous tool) hits this every
    time and the failure mode looks like "wrong key" rather than
    "right key but I can't unlock it."

    Detection: ``ssh-keygen -y -P "" -f <path>`` derives the public
    half of the key using an empty passphrase. If the key is
    passphrase-less the command exits 0 with the public key on stdout;
    if it's passphrase-protected, the wrong passphrase ("") fails with
    a non-zero exit. The wizard reads this signal and prompts the user
    to regenerate rather than silently reusing an unusable key.

    Returns ``False`` if:

    * The file doesn't exist (no key to check).
    * ``ssh-keygen`` isn't available (fail-safe: don't block on a
      detection mechanism we can't run).
    * The command times out (unlikely; keys are tiny).

    Returns ``True`` only on a confirmed wrong-passphrase failure.
    """
    if not path.is_file():
        return False
    try:
        result = subprocess.run(
            ["ssh-keygen", "-y", "-P", "", "-f", str(path)],
            capture_output=True, timeout=10, **TEXT_UTF8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode != 0


def has_ssh_copy_id() -> bool:
    """Whether ``ssh-copy-id`` is available on this machine.

    Standard on macOS / Linux / Git Bash for Windows; missing on bare
    Windows OpenSSH. The wizard offers a fallback when it's not.
    """
    return shutil.which("ssh-copy-id") is not None


# The user's terminal on Windows is cmd.exe or PowerShell, not a POSIX shell.
_WINDOWS = os.name == "nt"


def _shell_word(text: str) -> str:
    """Quote *text* as one argument for whichever shell the user pastes into.

    ``shlex.quote`` writes POSIX single quotes, which cmd.exe does not treat
    as quotes at all. On Windows the user's terminal is usually cmd.exe or
    PowerShell, and both honour double quotes, so a Windows key path (which
    contains no ``"``) is wrapped in those instead. Anything made only of
    characters no shell reads specially is left bare.
    """
    if text and all(c.isalnum() or c in "_-.@/:+=" for c in text):
        return text
    if _WINDOWS:
        return f'"{text}"'
    return shlex.quote(text)


def _public_key_material(pub_path: Path) -> str | None:
    """Return ``"<type> <base64>"`` from a ``.pub`` file, or None.

    The comment field is dropped on purpose: it is free text (``user@host``,
    or anything a reused key carries) and could hold a ``$`` or a quote that
    one of the shells would interpret. The type and the base64 blob are made
    only of ``[A-Za-z0-9+/=-]``, which every shell passes through untouched.
    """
    try:
        fields = pub_path.read_text(encoding="utf-8", errors="replace").split()
    except OSError:
        return None
    if len(fields) < 2:
        return None
    material = f"{fields[0]} {fields[1]}"
    if not all(c.isalnum() or c in "+/=- " for c in material):
        return None
    return material


#: Shell text that ends ``authorized_keys`` with a newline when it holds text that does not: a file whose last line
#: was written without one (by hand, or by an editor that drops it) would otherwise have the key glued onto that
#: line, where sshd reads it as part of the previous key's comment and never offers it. ``tail -c1 | wc -l`` counts
#: 0 exactly when the last byte is not a newline; ``[ -s ]`` skips an empty file. It is written without ``$`` and
#: without a double quote because the whole remote command sits inside one double-quoted argument that PowerShell
#: and the local POSIX shell would otherwise expand or end (see :func:`build_copy_command`).
_END_LAST_LINE = (
    "[ -s ~/.ssh/authorized_keys ] && tail -c1 ~/.ssh/authorized_keys | wc -l | grep -q 0 "
    "&& echo >> ~/.ssh/authorized_keys"
)


def _key_entry_pattern(material: str) -> str:
    """An extended regex matching *material* only where it starts a field — at the start of a line or after a space
    (a line with options, ``command="…" ssh-ed25519 …``) — never inside another field.

    A plain substring check also matched a key glued onto the end of the previous line's comment, so a re-run found
    the broken entry, appended nothing, and left the key unusable. *material* is made only of
    ``[A-Za-z0-9+/= -]`` (:func:`_public_key_material`), of which only ``+`` is special in an extended regex."""
    return "(^| )" + material.replace("+", "[+]")


def build_copy_command(ssh_alias: str, key_path: Path) -> str:
    """Return the exact shell command the user should run to install
    the public key on the remote.

    Prefers ``ssh-copy-id`` when available. Otherwise it returns a single
    ``ssh`` call with the public key written into the remote command itself,
    double-quoted, so the same text works when pasted into cmd.exe,
    PowerShell, Git Bash, or a macOS/Linux terminal. An earlier
    ``cat '<pub>' | ssh <alias> '<remote command>'`` form relied on POSIX
    single quotes; cmd.exe does not honour those, so it split the line at
    each ``&&`` and the remote shell received ``'mkdir -p ~/.ssh`` with an
    unmatched quote ("unexpected EOF while looking for matching `''"). The
    pipe form is kept only as a last resort, when the ``.pub`` file cannot
    be read here.

    The remote half appends the key only when it is not already present as an
    entry of its own (:func:`_key_entry_pattern`), so running the command twice
    leaves one entry, as ``ssh-copy-id`` would; and it first ends a last line
    that has no newline (:data:`_END_LAST_LINE`), as ``ssh-copy-id`` also does,
    so the key starts a line of its own.
    We never execute the command ourselves, so the user's password stays out
    of AllWell's process.
    """
    key_path = key_path.expanduser()
    pub_path = key_path.with_suffix(key_path.suffix + ".pub")
    alias = _shell_word(ssh_alias)
    if has_ssh_copy_id():
        return f"ssh-copy-id -i {_shell_word(str(pub_path))} {alias}"
    material = _public_key_material(pub_path)
    prepare = "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys"
    finish = "chmod 600 ~/.ssh/authorized_keys"
    if material is not None:
        return (
            f'ssh {alias} "{prepare} && '
            f"(grep -qE '{_key_entry_pattern(material)}' ~/.ssh/authorized_keys || "
            f"({_END_LAST_LINE}; echo '{material}' >> ~/.ssh/authorized_keys)) && "
            f'{finish}"'
        )
    return (
        f"cat {_shell_word(str(pub_path))} | ssh {alias} "
        f'"{prepare} && cat >> ~/.ssh/authorized_keys && {finish}"'
    )


def probe_ssh_connection(
    ssh_alias: str,
    ssh_key_path: str | Path = "",
    *,
    timeout: int = 10,
) -> tuple[bool, str]:
    """One-shot ``ssh <alias> echo ok`` probe for the dialog test buttons.

    Honors ``ssh_key_path`` when set so a freshly-installed wizard key
    is actually used (the bug in v5.2.1: the Test SSH buttons built
    bare ssh argv that fell back to ssh's default identity search and
    couldn't find ``~/.ssh/allwell_<alias>``, so a successful wizard
    run was followed by a Test that said "Permission denied").

    ``BatchMode=yes`` disables interactive password prompts so the
    call fails fast with a clear message instead of hanging. The
    framework's policy is "never store passwords" — if no key works,
    that's a config problem the user fixes via the SSH Key wizard.

    Returns ``(True, "")`` on ``ok`` response, else ``(False, <detail>)``.
    """
    cmd = [
        resolve_ssh_binary(),
        *known_hosts_options(),      # the file transfers check too, so a key this accepts is one they accept
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new",
        *keepalive_options(),
    ]
    if ssh_key_path:
        key = Path(ssh_key_path).expanduser()
        cmd += ["-i", str(key)]
    cmd += [ssh_alias, "echo", "ok"]

    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout, **TEXT_UTF8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode == 0 and result.stdout.strip() == "ok":
        return True, ""
    detail = (result.stderr or result.stdout).strip()
    if not detail:
        detail = f"ssh exited {result.returncode}"
    return False, detail[:240]
