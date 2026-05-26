"""Tests for scripts/_bootstrap_lib (v5.2.2 additions).

Coverage is intentionally narrow: the bootstrap module is a sequence
of shell-outs to ssh/rsync/conda whose real behaviour can only be
exercised against a live remote. The v5.2.2 change is purely about
argv construction — does ``_ssh_run`` pass ``-i <key>`` when an
``ssh_key_path`` is configured? — so the tests mock the subprocess
layer and assert the argv shape.

Earlier helpers (probe_host, install_*, measure_bandwidth) are not
re-tested here; the v5.2.2 change is uniform "thread ssh_key_path
through every call", and verifying the leaf ``_ssh_run`` covers that
contract.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# scripts/ isn't a package; the wrappers add the repo root to sys.path
# at runtime. Mirror that here so the import works under pytest.
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _bootstrap_lib as bl  # noqa: E402


# ── _ssh_run: -i flag handling (v5.2.2) ────────────────────────────────────


def test_ssh_run_omits_i_when_no_key(monkeypatch):
    """Default path (no key configured): bare ssh argv, no -i. Matches
    pre-v5.2.2 behaviour so a first-time bootstrap on a host using
    ssh-agent / ~/.ssh/config keys keeps working unchanged."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    bl._ssh_run("bigbox", ["echo", "ok"])
    assert "-i" not in captured["cmd"]
    # Alias is at position 1 (right after the ssh binary).
    assert captured["cmd"][1] == "bigbox"


def test_ssh_run_inserts_i_flag_when_key_set(monkeypatch, tmp_path):
    """v5.2.2 fix: ssh_key_path threads down to -i <expanded_path>
    so a host whose only accepted credential is the wizard-generated
    key bootstraps successfully on a second run."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    key = tmp_path / "allwell_bigbox"
    bl._ssh_run("bigbox", ["echo", "ok"], ssh_key_path=str(key))

    # -i must be present, followed by the expanded key path, and both
    # must come BEFORE the alias (ssh strict argv ordering).
    assert "-i" in captured["cmd"]
    i_idx = captured["cmd"].index("-i")
    assert captured["cmd"][i_idx + 1] == str(key)
    alias_idx = captured["cmd"].index("bigbox")
    assert i_idx < alias_idx, "ssh requires options before the destination"


def test_ssh_run_expands_tilde_in_key_path(monkeypatch):
    """The wizard's default key path uses ~ ; ssh doesn't expand it
    in argv elements so we must expand client-side."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bl.subprocess, "run", fake_run)
    bl._ssh_run(
        "bigbox", ["echo", "ok"],
        ssh_key_path="~/.ssh/allwell_bigbox",
    )
    i_idx = captured["cmd"].index("-i")
    assert "~" not in captured["cmd"][i_idx + 1]
    assert captured["cmd"][i_idx + 1].endswith("allwell_bigbox")


# ── _load_existing_key (v5.2.2) ────────────────────────────────────────────


def test_load_existing_key_returns_empty_when_no_remotes(tmp_path, monkeypatch):
    """Typical first-bootstrap: no remotes.toml at all yet. Returns
    empty string so the rest of the bootstrap behaves as if no key
    is configured."""
    monkeypatch.setenv("ALLWELL_REMOTES", str(tmp_path / "nope.toml"))
    assert bl._load_existing_key("anything") == ""


def test_load_existing_key_returns_path_when_set(tmp_path, monkeypatch):
    """Wizard-then-bootstrap flow: the user ran the SSH Key wizard
    which wrote ssh_key_path into remotes.toml. The bootstrap must
    pick it up so it can authenticate."""
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.jupyterhub]\n"
        "ssh_alias = \"jupyterhub\"\n"
        "ssh_key_path = \"~/.ssh/allwell_jupyterhub\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    assert bl._load_existing_key("jupyterhub") == "~/.ssh/allwell_jupyterhub"


def test_load_existing_key_empty_for_unknown_alias(tmp_path, monkeypatch):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.knownhost]\nssh_alias = \"knownhost\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    assert bl._load_existing_key("not-in-config") == ""


def test_load_existing_key_empty_for_entry_without_key(tmp_path, monkeypatch):
    """An entry exists but the user hasn't run the wizard — empty
    string, not 'None' or a missing-key error."""
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.keyless]\nssh_alias = \"keyless\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    assert bl._load_existing_key("keyless") == ""


def test_load_existing_key_is_silent_on_toml_parse_error(tmp_path, monkeypatch):
    """A corrupt remotes.toml shouldn't block bootstrap — fall back
    to no-key behaviour so the user can still bootstrap from scratch
    (and the bootstrap will rewrite the file at the end)."""
    cfg = tmp_path / "remotes.toml"
    cfg.write_text("this is not [[ valid toml = ", encoding="utf-8")
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    # Must not raise.
    assert bl._load_existing_key("anything") == ""
