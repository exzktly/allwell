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
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.ssh_key_setup import (
    build_copy_command,
    default_key_path,
    generate_keypair,
    has_ssh_copy_id,
    verify_key_works,
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


def test_build_copy_command_falls_back_when_ssh_copy_id_missing(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr("services.ssh_key_setup.shutil.which", lambda _: None)
    key = tmp_path / "key"
    cmd = build_copy_command("bigbox", key)
    # Manual cat | ssh pipeline.
    assert cmd.startswith("cat ")
    assert "key.pub" in cmd
    assert "bigbox" in cmd
    assert "authorized_keys" in cmd
    # Permissions hardening.
    assert "chmod 700 ~/.ssh" in cmd
    assert "chmod 600 ~/.ssh/authorized_keys" in cmd


def test_build_copy_command_quotes_pathological_alias(tmp_path, monkeypatch):
    """An alias with spaces or shell metacharacters must be escaped."""
    monkeypatch.setattr("services.ssh_key_setup.shutil.which", lambda _: "/usr/bin/ssh-copy-id")
    key = tmp_path / "key"
    cmd = build_copy_command("weird user@host with spaces", key)
    # The alias is quoted as one shell argument.
    assert "'weird user@host with spaces'" in cmd


# ── verify_key_works ────────────────────────────────────────────────────────


def test_verify_key_works_returns_true_on_ok(tmp_path, monkeypatch):
    key = tmp_path / "key"
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 0, stdout="ok\n", stderr="",
        )),
    )
    ok, detail = verify_key_works("bigbox", key)
    assert ok is True
    assert detail == ""


def test_verify_key_works_uses_batchmode(tmp_path, monkeypatch):
    """BatchMode=yes is essential — without it a missing key would
    fall through to a password prompt and the call would hang in CI."""
    key = tmp_path / "key"
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("services.ssh_key_setup.subprocess.run", fake_run)
    verify_key_works("bigbox", key)
    # BatchMode must be present.
    assert any("BatchMode=yes" in arg for arg in captured["cmd"])
    # Identity file must be passed.
    assert "-i" in captured["cmd"]


def test_verify_key_works_returns_detail_on_failure(tmp_path, monkeypatch):
    key = tmp_path / "key"
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 255, stdout="",
            stderr="Permission denied (publickey,password).",
        )),
    )
    ok, detail = verify_key_works("bigbox", key)
    assert ok is False
    assert "Permission denied" in detail


def test_verify_key_works_truncates_long_stderr(tmp_path, monkeypatch):
    """A flood of warnings shouldn't blow up the message-box width."""
    key = tmp_path / "key"
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(return_value=subprocess.CompletedProcess(
            [], 1, stdout="", stderr="x" * 1000,
        )),
    )
    ok, detail = verify_key_works("bigbox", key)
    assert ok is False
    assert len(detail) <= 240


def test_verify_key_works_handles_timeout(tmp_path, monkeypatch):
    key = tmp_path / "key"
    monkeypatch.setattr(
        "services.ssh_key_setup.subprocess.run",
        MagicMock(side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10)),
    )
    ok, detail = verify_key_works("bigbox", key, timeout=10)
    assert ok is False
    assert detail
