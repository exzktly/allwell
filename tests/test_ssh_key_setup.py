"""Tests for services.ssh_key_setup (v5.2.1).

The helpers are deliberately thin shells around well-known shell tools
(``ssh-keygen``, ``ssh-copy-id``, ``ssh``), so the tests focus on:

* path discipline — generated keys land in ``~/.ssh``, alias is sanitised.
* command shape — the command string the wizard hands to the user is
  exactly what would work in a real shell, with proper quoting.
* idempotency — re-running ``generate_keypair`` on an existing key is a
  no-op rather than overwriting.
* failure surface — caller gets a clear error string when ssh-keygen
  is missing or ssh refuses the verify call.

We do NOT spawn real ssh subprocesses in unit tests; the shell tools
are mocked.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock


from services.ssh_key_setup import (
    build_copy_command,
    default_key_path,
    generate_keypair,
    key_has_passphrase,
    probe_ssh_connection,
)


# ── default_key_path ────────────────────────────────────────────────────────


def test_default_key_path_lives_under_dot_ssh():
    p = default_key_path("bigbox")
    # Test by string suffix because the prefix is the user's home.
    assert str(p).endswith("/.ssh/allwell_bigbox") or \
           str(p).endswith("\\.ssh\\allwell_bigbox")


def test_default_key_path_sanitises_alias():
    """Pathological aliases must not let the key escape ~/.ssh."""
    p = default_key_path("../../etc/passwd")
    # The alias is sanitised to underscores; the file lives under ~/.ssh.
    assert ".." not in p.name
    assert "etc" not in str(p.parent.name)  # parent is .ssh, not etc


def test_default_key_path_distinguishes_aliases():
    a = default_key_path("box-one")
    b = default_key_path("box-two")
    assert a != b


def test_default_key_path_preserves_dashes_and_underscores():
    p = default_key_path("my-lab_box1")
    assert p.name == "allwell_my-lab_box1"


# ── generate_keypair ────────────────────────────────────────────────────────


def test_generate_keypair_skips_when_files_exist(tmp_path):
    """Both private + public present → no-op, return ``(False, "")``."""
    key = tmp_path / "k"
    key.write_text("FAKE PRIVATE KEY")
    (tmp_path / "k.pub").write_text("ssh-ed25519 AAAA...")
    created, err = generate_keypair(key)
    assert created is False
    assert err == ""


def test_generate_keypair_runs_ssh_keygen_when_missing(tmp_path, monkeypatch):
    key = tmp_path / "newkey"
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        # Pretend ssh-keygen succeeded by writing the files it would
        # have written.
        key.write_text("priv")
        key.with_suffix(".pub").write_text("ssh-ed25519 AAAA...")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("services.ssh_key_setup.subprocess.run", fake_run)
    created, err = generate_keypair(key)

    assert created is True
    assert err == ""
    assert captured["cmd"][0] == "ssh-keygen"
    assert "-t" in captured["cmd"]
    # Ed25519 — the modern default; never let RSA sneak back in.
    assert "ed25519" in captured["cmd"]
    # Empty passphrase — every ssh call needs to be non-interactive,
    # a passphrase would defeat the wizard's purpose.
    n_idx = captured["cmd"].index("-N")
    assert captured["cmd"][n_idx + 1] == ""


def test_generate_keypair_hardens_private_key_perms(tmp_path, monkeypatch):
    """Audit X11 — the passphrase-less private key is a bearer credential, so it
    must be owner-only (0600). ssh-keygen writes it that way; we chmod
    explicitly as defense-in-depth and lock the mitigation in here."""
    import os
    import stat
    import sys

    if sys.platform.startswith("win"):
        import pytest
        pytest.skip("POSIX permission bits are not meaningful on Windows")

    key = tmp_path / "newkey"

    def fake_run(cmd, **kwargs):
        # ssh-keygen would write a group/world-readable file on a bad umask;
        # simulate the loose perms so the explicit chmod is what tightens it.
        key.write_text("priv")
        os.chmod(key, 0o644)
        key.with_suffix(".pub").write_text("ssh-ed25519 AAAA...")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("services.ssh_key_setup.subprocess.run", fake_run)
    created, err = generate_keypair(key)
    assert created is True and err == ""
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600


def test_generate_keypair_surfaces_ssh_keygen_error(tmp_path, monkeypatch):
    key = tmp_path / "newkey"

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="boom: cannot write key",
        )

    monkeypatch.setattr("services.ssh_key_setup.subprocess.run", fake_run)
    created, err = generate_keypair(key)
    assert created is False
    assert "boom" in err


def test_generate_keypair_surfaces_missing_ssh_keygen(tmp_path, monkeypatch):
    """If ssh-keygen isn't on PATH the error must be actionable, not a
    raw stacktrace."""
    key = tmp_path / "newkey"
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(side_effect=FileNotFoundError("ssh-keygen not found")),
    )
    created, err = generate_keypair(key)
    assert created is False
    assert "ssh-keygen" in err


# ── build_copy_command ──────────────────────────────────────────────────────


def test_build_copy_command_uses_ssh_copy_id_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr("services.ssh_key_setup.shutil.which", lambda _: "/usr/bin/ssh-copy-id")
    key = tmp_path / "key"
    cmd = build_copy_command("bigbox", key)
    assert cmd.startswith("ssh-copy-id ")
    assert "-i" in cmd
    assert "key.pub" in cmd
    assert "bigbox" in cmd


_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq8+/xY0abc="


def _write_pub(tmp_path, comment="zak@my $pc"):
    key = tmp_path / "key"
    # CRLF and a comment holding a ``$`` — what a key edited or reused on
    # Windows can carry, and what the shells must never be shown.
    (tmp_path / "key.pub").write_text(f"{_PUB} {comment}\r\n", newline="")
    return key


def test_build_copy_command_falls_back_when_ssh_copy_id_missing(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr("services.ssh_key_setup.shutil.which", lambda _: None)
    key = _write_pub(tmp_path)
    cmd = build_copy_command("bigbox", key)
    assert cmd == (
        'ssh bigbox "mkdir -p ~/.ssh && chmod 700 ~/.ssh && '
        "touch ~/.ssh/authorized_keys && "
        f"(grep -qE '(^| ){_PUB.replace('+', '[+]')}' ~/.ssh/authorized_keys || "
        "([ -s ~/.ssh/authorized_keys ] && tail -c1 ~/.ssh/authorized_keys | wc -l | grep -q 0 "
        "&& echo >> ~/.ssh/authorized_keys; "
        f"echo '{_PUB}' >> ~/.ssh/authorized_keys)) && "
        'chmod 600 ~/.ssh/authorized_keys"'
    )


def test_fallback_survives_cmd_exe_which_ignores_single_quotes(
    tmp_path, monkeypatch,
):
    """The reported Windows failure: in cmd.exe, ``'`` is not a quote, so the
    old ``ssh host 'mkdir -p ~/.ssh && ...'`` was split at ``&&`` and the remote
    shell got ``'mkdir -p ~/.ssh`` -> "unexpected EOF while looking for
    matching `''". cmd.exe honours only double quotes; every ``&&``, ``||``
    and ``>>`` must therefore sit inside the one double-quoted argument, and
    nothing outside it may need quoting at all."""
    monkeypatch.setattr("services.ssh_key_setup.shutil.which", lambda _: None)
    cmd = build_copy_command("bigbox", _write_pub(tmp_path))
    head, quoted, tail = cmd.partition('"')
    assert head == "ssh bigbox "
    assert quoted == '"' and tail.endswith('"') and '"' not in tail[:-1]
    # No pipe either: cmd.exe has no ``cat``, and PowerShell 5 has no ``<``.
    assert "|" not in head and "cat " not in cmd
    # The free-text comment (here holding a ``$``, which PowerShell would
    # expand inside double quotes) never reaches the command.
    assert "$" not in cmd and "zak@" not in cmd


def _run_remote_half(tmp_path, monkeypatch, existing=None, times=1):
    """Run the fallback's remote command the way sshd does (``sh -c <arg>``)
    against a scratch HOME whose ``authorized_keys`` holds *existing* (none
    when ``None``), *times* times; the file's text afterwards."""
    import os
    import shlex

    monkeypatch.setattr("services.ssh_key_setup.shutil.which", lambda _: None)
    argv = shlex.split(build_copy_command("bigbox", _write_pub(tmp_path)))
    assert argv[:2] == ["ssh", "bigbox"] and len(argv) == 3
    home = tmp_path / "home"
    home.mkdir()
    auth = home / ".ssh" / "authorized_keys"
    if existing is not None:
        auth.parent.mkdir()
        auth.write_text(existing, newline="")
    for _ in range(times):
        result = subprocess.run(
            ["sh", "-c", argv[2]], env={**os.environ, "HOME": str(home)},
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
    assert auth.stat().st_mode & 0o777 == 0o600
    return auth.read_text()


def test_fallback_remote_half_installs_the_key_once(tmp_path, monkeypatch):
    """Twice into no file at all: one entry, owner-only permissions, no blank
    line ahead of it."""
    assert _run_remote_half(tmp_path, monkeypatch, times=2) == f"{_PUB}\n"


def test_fallback_starts_the_key_on_a_line_of_its_own(tmp_path, monkeypatch):
    """An ``authorized_keys`` whose last line has no newline: the key used to be
    glued onto it (``...admin@xssh-ed25519 ...``), where sshd reads it as the
    previous key's comment and the new key never works. Run twice: still one
    entry."""
    old = "ssh-rsa AAAAOLDKEY admin@x"
    assert _run_remote_half(tmp_path, monkeypatch, old, times=2) == f"{old}\n{_PUB}\n"


def test_fallback_repairs_a_key_an_earlier_run_glued_onto_the_line_before(tmp_path, monkeypatch):
    """A file the old command already broke: the key's text is in it, but only
    inside the previous line's comment. A substring check found it there and
    appended nothing, so re-running never repaired it."""
    glued = f"ssh-rsa AAAAOLDKEY admin@x{_PUB}\n"
    assert _run_remote_half(tmp_path, monkeypatch, glued) == f"{glued}{_PUB}\n"


def test_fallback_finds_the_key_behind_an_options_field(tmp_path, monkeypatch):
    """An entry with options ahead of the key type is the key already
    installed: nothing is appended."""
    with_options = f'no-pty,command="true" {_PUB} zak@pc\n'
    assert _run_remote_half(tmp_path, monkeypatch, with_options, times=2) == with_options


def test_fallback_without_a_readable_pub_still_double_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr("services.ssh_key_setup.shutil.which", lambda _: None)
    cmd = build_copy_command("bigbox", tmp_path / "missing")
    assert cmd.startswith("cat ") and "missing.pub" in cmd
    assert cmd.endswith('chmod 600 ~/.ssh/authorized_keys"')
    assert "chmod 700 ~/.ssh" in cmd and " | ssh bigbox \"" in cmd


def test_windows_paths_are_double_quoted(tmp_path, monkeypatch):
    """``shlex.quote``'s single quotes are literal characters to cmd.exe, so
    ``ssh-copy-id -i 'C:\\...pub'`` would look for a file whose name starts
    with a quote. On Windows the path is double-quoted instead."""
    import services.ssh_key_setup as sks

    monkeypatch.setattr("services.ssh_key_setup.shutil.which", lambda _: "ssh-copy-id")
    monkeypatch.setattr(sks, "_WINDOWS", True)
    assert sks._shell_word(r"C:\Users\Zak Singer\.ssh\allwell_hpc.pub") == \
        r'"C:\Users\Zak Singer\.ssh\allwell_hpc.pub"'
    assert sks._shell_word("hpc") == "hpc"


def test_build_copy_command_quotes_pathological_alias(tmp_path, monkeypatch):
    """An alias with spaces or shell metacharacters must be escaped."""
    monkeypatch.setattr("services.ssh_key_setup.shutil.which", lambda _: "/usr/bin/ssh-copy-id")
    key = tmp_path / "key"
    cmd = build_copy_command("weird user@host with spaces", key)
    # The alias is quoted as one shell argument.
    assert "'weird user@host with spaces'" in cmd


# ── probe_ssh_connection (v5.2.2) ────────────────────────────────────────────


def test_probe_ssh_connection_without_key_omits_i_flag(monkeypatch):
    """No key path → no -i flag. Lets ssh fall through to ~/.ssh/config /
    default identities for users who already have a working ssh setup."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("services.ssh_key_setup.subprocess.run", fake_run)
    ok, detail = probe_ssh_connection("bigbox")
    assert ok is True
    assert "-i" not in captured["cmd"], \
        f"-i should be absent when no key path; got argv: {captured['cmd']}"
    # Alias still passed as the destination.
    assert "bigbox" in captured["cmd"]


def test_probe_ssh_connection_with_key_passes_i_flag(tmp_path, monkeypatch):
    """The v5.2.2 fix: when ssh_key_path is set, -i <expanded-path> is
    inserted so ssh picks up the wizard-generated key instead of
    falling back to default identity search and reporting 'Permission
    denied' on hosts whose only accepted credential is that key."""
    key = tmp_path / "my_key"
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("services.ssh_key_setup.subprocess.run", fake_run)
    probe_ssh_connection("bigbox", key)
    assert "-i" in captured["cmd"]
    # -i and the path must be adjacent and in that order.
    i_idx = captured["cmd"].index("-i")
    assert captured["cmd"][i_idx + 1] == str(key)


def test_probe_ssh_connection_expands_tilde_in_key_path(monkeypatch):
    """`~/.ssh/allwell_<alias>` (the wizard's default key path) needs
    to be expanded before being passed to ssh; raw '~' inside an argv
    element doesn't get shell expansion."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("services.ssh_key_setup.subprocess.run", fake_run)
    probe_ssh_connection("bigbox", "~/.ssh/allwell_bigbox")
    i_idx = captured["cmd"].index("-i")
    assert "~" not in captured["cmd"][i_idx + 1]
    assert captured["cmd"][i_idx + 1].endswith("allwell_bigbox")


def test_probe_ssh_connection_uses_batchmode(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("services.ssh_key_setup.subprocess.run", fake_run)
    probe_ssh_connection("bigbox")
    # Without BatchMode=yes, a missing-key situation would hang on a
    # password prompt instead of failing cleanly.
    assert any("BatchMode=yes" in a for a in captured["cmd"])


def test_probe_ssh_connection_fail_returns_stderr_detail(monkeypatch):
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 255, stdout="", stderr="Permission denied (publickey).\n",
        )),
    )
    ok, detail = probe_ssh_connection("bigbox", "~/key")
    assert ok is False
    assert "Permission denied" in detail

# ── key_has_passphrase (v5.2.6) ─────────────────────────────────────────────


def test_key_has_passphrase_returns_false_for_missing_file(tmp_path):
    """No file → nothing to check. False so callers don't trigger the
    'replace this key' prompt for a key that doesn't exist."""
    assert key_has_passphrase(tmp_path / "nope") is False


def test_key_has_passphrase_returns_false_when_ssh_keygen_succeeds(tmp_path, monkeypatch):
    """ssh-keygen -y -P "" -f <key> with rc 0 means the key has no
    passphrase (empty string unlocked it)."""
    key = tmp_path / "key"
    key.write_text("FAKE")
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 0, stdout="ssh-ed25519 AAAA…", stderr="",
        )),
    )
    assert key_has_passphrase(key) is False


def test_key_has_passphrase_returns_true_when_ssh_keygen_fails(tmp_path, monkeypatch):
    """Non-zero rc with the empty passphrase means a passphrase IS set
    (the wrong passphrase '' was rejected). This is the trap v5.2.6
    detects."""
    key = tmp_path / "key"
    key.write_text("FAKE")
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Load key \"key\": incorrect passphrase",
        )),
    )
    assert key_has_passphrase(key) is True


def test_key_has_passphrase_fails_safe_when_ssh_keygen_missing(tmp_path, monkeypatch):
    """If ssh-keygen isn't on PATH we can't detect — return False so
    the wizard reuses the existing key rather than blocking on a
    detection mechanism we can't run."""
    key = tmp_path / "key"
    key.write_text("FAKE")
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(side_effect=FileNotFoundError("ssh-keygen not on PATH")),
    )
    assert key_has_passphrase(key) is False


def test_key_has_passphrase_fails_safe_on_timeout(tmp_path, monkeypatch):
    """A hung ssh-keygen shouldn't deadlock the wizard."""
    key = tmp_path / "key"
    key.write_text("FAKE")
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(side_effect=subprocess.TimeoutExpired(cmd="ssh-keygen", timeout=10)),
    )
    assert key_has_passphrase(key) is False


def test_key_has_passphrase_invokes_correct_command(tmp_path, monkeypatch):
    """Argv discipline: -y for the derive-pubkey mode, -P '' to attempt
    the empty passphrase, -f to point at the key file."""
    key = tmp_path / "mykey"
    key.write_text("FAKE")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("services.ssh_key_setup.subprocess.run", fake_run)
    key_has_passphrase(key)
    assert captured["cmd"][0] == "ssh-keygen"
    assert "-y" in captured["cmd"]
    p_idx = captured["cmd"].index("-P")
    assert captured["cmd"][p_idx + 1] == ""  # empty passphrase argument
    f_idx = captured["cmd"].index("-f")
    assert captured["cmd"][f_idx + 1] == str(key)


def test_probe_ssh_connection_truncates_long_stderr(monkeypatch):
    """A flood of warnings shouldn't blow up the message-box width."""
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 1, stdout="", stderr="x" * 1000,
        )),
    )
    ok, detail = probe_ssh_connection("bigbox", "~/key")
    assert ok is False
    assert len(detail) <= 240


def test_probe_ssh_connection_handles_timeout(monkeypatch):
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10)),
    )
    ok, detail = probe_ssh_connection("bigbox", "~/key", timeout=10)
    assert ok is False
    assert detail
