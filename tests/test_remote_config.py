"""Tests for services/remote_config.py.

Covers TOML parsing, defaulting, error cases, and the in-place
bandwidth-update path that the runner uses after each transfer.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from services.remote_config import (
    RemoteHost,
    get_remote,
    load_remotes,
    update_bandwidth,
)


def _write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "remotes.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_remotes_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLWELL_REMOTES", raising=False)
    assert load_remotes(tmp_path / "nonexistent.toml") == {}


def test_load_remotes_minimal_entry(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
        [remote.bigbox]
        ssh_alias = "bigbox"
        """,
    )
    remotes = load_remotes(cfg)
    assert set(remotes) == {"bigbox"}
    host = remotes["bigbox"]
    assert isinstance(host, RemoteHost)
    assert host.name == "bigbox"
    assert host.ssh_alias == "bigbox"
    # Defaults filled in.
    assert host.remote_project_dir == "~/allwell"
    assert host.cache_budget_gb == 200
    assert host.cpu_only is False


def test_load_remotes_full_entry(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
        [remote.dev]
        ssh_alias = "user@dev.example"
        remote_project_dir = "~/code/allwell"
        remote_staging_dir = "~/.cache/allwell"
        remote_python = "~/venvs/aw/bin/python"
        cpu_only = true
        extra_args = ["--workers", "16"]
        cache_budget_gb = 50
        measured_bandwidth_mbps = 87.4
        """,
    )
    host = load_remotes(cfg)["dev"]
    assert host.ssh_alias == "user@dev.example"
    assert host.remote_project_dir == "~/code/allwell"
    assert host.remote_python == "~/venvs/aw/bin/python"
    assert host.cpu_only is True
    assert host.extra_args == ("--workers", "16")
    assert host.cache_budget_gb == 50
    assert host.measured_bandwidth_mbps == pytest.approx(87.4)


def test_load_remotes_multiple_hosts(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
        [remote.a]
        ssh_alias = "a"
        [remote.b]
        ssh_alias = "b"
        """,
    )
    remotes = load_remotes(cfg)
    assert set(remotes) == {"a", "b"}


def test_load_remotes_missing_alias_is_error(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
        [remote.broken]
        cpu_only = true
        """,
    )
    with pytest.raises(ValueError, match="ssh_alias"):
        load_remotes(cfg)


def test_load_remotes_malformed_toml_is_error(tmp_path):
    cfg = _write_config(tmp_path, "this is not = valid = toml [[")
    with pytest.raises(ValueError, match="Cannot parse"):
        load_remotes(cfg)


def test_load_remotes_bad_extra_args(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
        [remote.bad]
        ssh_alias = "x"
        extra_args = "not a list"
        """,
    )
    with pytest.raises(ValueError, match="extra_args"):
        load_remotes(cfg)


def test_get_remote_returns_none_for_unknown(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
        [remote.exists]
        ssh_alias = "x"
        """,
    )
    assert get_remote("exists", cfg).ssh_alias == "x"
    assert get_remote("missing", cfg) is None


def test_env_var_override(tmp_path, monkeypatch):
    cfg = _write_config(
        tmp_path,
        """
        [remote.env]
        ssh_alias = "from-env"
        """,
    )
    monkeypatch.setenv("ALLWELL_REMOTES", str(cfg))
    remotes = load_remotes()
    assert remotes["env"].ssh_alias == "from-env"


def test_update_bandwidth_rewrites_existing_value(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
        [remote.bw]
        ssh_alias = "bw"
        measured_bandwidth_mbps = 10.0
        """,
    )
    update_bandwidth("bw", 95.5, path=cfg)
    reloaded = load_remotes(cfg)
    assert reloaded["bw"].measured_bandwidth_mbps == pytest.approx(95.5)


def test_update_bandwidth_adds_key_if_section_present_without_it(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
        [remote.fresh]
        ssh_alias = "fresh"
        """,
    )
    update_bandwidth("fresh", 50.0, path=cfg)
    reloaded = load_remotes(cfg)
    assert reloaded["fresh"].measured_bandwidth_mbps == pytest.approx(50.0)


def test_update_bandwidth_leaves_other_sections_alone(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
        [remote.a]
        ssh_alias = "a"
        measured_bandwidth_mbps = 10.0

        [remote.b]
        ssh_alias = "b"
        measured_bandwidth_mbps = 20.0
        """,
    )
    update_bandwidth("a", 99.0, path=cfg)
    reloaded = load_remotes(cfg)
    assert reloaded["a"].measured_bandwidth_mbps == pytest.approx(99.0)
    assert reloaded["b"].measured_bandwidth_mbps == pytest.approx(20.0)


def test_update_bandwidth_skips_unknown_host(tmp_path):
    cfg = _write_config(
        tmp_path,
        """
        [remote.known]
        ssh_alias = "known"
        """,
    )
    # Should not raise; should not corrupt the file.
    update_bandwidth("nonexistent", 50.0, path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "nonexistent" not in text


def test_update_bandwidth_missing_file_is_silent(tmp_path):
    # Best-effort; missing file is acceptable.
    update_bandwidth("ghost", 50.0, path=tmp_path / "nope.toml")


def test_ssh_argv_shape():
    host = RemoteHost(name="x", ssh_alias="my-alias")
    argv = host.ssh_argv()
    assert argv[0] == "ssh"
    assert "-tt" in argv
    assert "my-alias" in argv
    # ControlMaster wires for connection reuse.
    assert any("ControlMaster=auto" in a for a in argv)
    assert any("ControlPersist=60s" in a for a in argv)


def test_ssh_argv_no_tty():
    host = RemoteHost(name="x", ssh_alias="alias")
    argv = host.ssh_argv(allocate_tty=False)
    assert "-tt" not in argv
    assert "alias" in argv
