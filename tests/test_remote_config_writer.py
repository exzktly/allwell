"""Tests for services.remote_config.write_remote / delete_remote.

These are the v5.1.0 persistence helpers used by the "Manage Remotes"
GUI dialog. They share the surgical line-edit approach the existing
``update_bandwidth`` uses so comments and unrelated sections survive a
write — verified explicitly below.
"""

from __future__ import annotations


import pytest

from services.remote_config import (
    RemoteHost,
    delete_remote,
    get_remote,
    load_remotes,
    update_bandwidth,
    write_remote,
)


def _make_host(name: str = "bigbox", **overrides) -> RemoteHost:
    base = dict(
        name=name,
        ssh_alias=name,
        remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging",
        remote_python="~/miniforge3/envs/allwell/bin/python",
        cpu_only=False,
        extra_args=(),
        cache_budget_gb=200,
        measured_bandwidth_mbps=0.0,
    )
    base.update(overrides)
    return RemoteHost(**base)


# ── write_remote: brand-new section ────────────────────────────────────────


def test_write_remote_creates_file_when_missing(tmp_path):
    cfg = tmp_path / "remotes.toml"
    assert not cfg.exists()
    changed = write_remote(_make_host(), path=cfg)
    assert changed
    assert cfg.exists()
    reloaded = load_remotes(cfg)
    assert "bigbox" in reloaded
    assert reloaded["bigbox"].ssh_alias == "bigbox"


def test_write_remote_appends_to_existing_file(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.existing]\n"
        "ssh_alias = \"existing\"\n",
        encoding="utf-8",
    )
    write_remote(_make_host("newone"), path=cfg)
    reloaded = load_remotes(cfg)
    assert set(reloaded) == {"existing", "newone"}


def test_write_remote_preserves_surrounding_comments(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "# Top-of-file comment.\n"
        "[remote.keep]\n"
        "ssh_alias = \"keep\"\n"
        "# inline note on this host\n",
        encoding="utf-8",
    )
    write_remote(_make_host("added"), path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "# Top-of-file comment." in text
    assert "# inline note on this host" in text
    assert "[remote.keep]" in text
    assert "[remote.added]" in text


# ── write_remote: replace in place ─────────────────────────────────────────


def test_write_remote_replaces_existing_section(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.host]\n"
        "ssh_alias = \"old-alias\"\n"
        "cpu_only = false\n",
        encoding="utf-8",
    )
    write_remote(_make_host("host", ssh_alias="new-alias", cpu_only=True), path=cfg)
    reloaded = load_remotes(cfg)
    assert reloaded["host"].ssh_alias == "new-alias"
    assert reloaded["host"].cpu_only is True


def test_write_remote_replace_leaves_other_sections_alone(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.a]\n"
        "ssh_alias = \"a\"\n"
        "cache_budget_gb = 99\n"
        "\n"
        "[remote.b]\n"
        "ssh_alias = \"b\"\n"
        "cache_budget_gb = 77\n",
        encoding="utf-8",
    )
    write_remote(_make_host("a", cache_budget_gb=42), path=cfg)
    reloaded = load_remotes(cfg)
    assert reloaded["a"].cache_budget_gb == 42
    # b untouched.
    assert reloaded["b"].cache_budget_gb == 77
    assert reloaded["b"].ssh_alias == "b"


def test_write_remote_returns_false_when_content_unchanged(tmp_path):
    cfg = tmp_path / "remotes.toml"
    host = _make_host("same")
    assert write_remote(host, path=cfg) is True
    # Second write of the identical block is a no-op.
    assert write_remote(host, path=cfg) is False


def test_write_remote_extra_args_round_trip(tmp_path):
    cfg = tmp_path / "remotes.toml"
    write_remote(
        _make_host("withargs", extra_args=("--workers", "16", "--force")),
        path=cfg,
    )
    reloaded = load_remotes(cfg)
    assert reloaded["withargs"].extra_args == ("--workers", "16", "--force")


def test_write_remote_ssh_key_path_round_trip(tmp_path):
    """v5.2.1: the wizard's recorded key path must survive a write
    cycle, and an empty path must NOT emit an empty ssh_key_path
    line to keep the common-case block tidy."""
    cfg = tmp_path / "remotes.toml"

    # With a key.
    write_remote(
        _make_host("keyed", ssh_key_path="~/.ssh/allwell_keyed"),
        path=cfg,
    )
    reloaded = load_remotes(cfg)
    assert reloaded["keyed"].ssh_key_path == "~/.ssh/allwell_keyed"
    assert "ssh_key_path" in cfg.read_text(encoding="utf-8")

    # Replace with empty — block stays valid but ssh_key_path line
    # disappears (terser, no empty-string noise).
    write_remote(_make_host("keyed", ssh_key_path=""), path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "ssh_key_path" not in text
    reloaded = load_remotes(cfg)
    assert reloaded["keyed"].ssh_key_path == ""


def test_write_remote_compute_launcher_round_trip(tmp_path):
    """The scheduler command (compute_launcher) must survive a write
    cycle, default to empty when absent, and — like ssh_key_path — NOT
    emit an empty line in the common (run-on-host) case."""
    cfg = tmp_path / "remotes.toml"

    launcher = "srun --partition=gpu --gres=gpu:1 --time=4:00:00"
    write_remote(_make_host("cluster", compute_launcher=launcher), path=cfg)
    reloaded = load_remotes(cfg)
    assert reloaded["cluster"].compute_launcher == launcher
    assert "compute_launcher" in cfg.read_text(encoding="utf-8")

    # Switch back to running on the host itself — the line disappears.
    write_remote(_make_host("cluster", compute_launcher=""), path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "compute_launcher" not in text
    reloaded = load_remotes(cfg)
    assert reloaded["cluster"].compute_launcher == ""


def test_write_remote_jupyterhub_config_path_round_trip(tmp_path):
    """jupyterhub_config_path survives a write cycle and is omitted from the
    block when empty (like ssh_key_path / compute_launcher)."""
    cfg = tmp_path / "remotes.toml"

    write_remote(
        _make_host("cluster", jupyterhub_config_path="/opt/jh/jupyterhub_config.py"),
        path=cfg,
    )
    reloaded = load_remotes(cfg)
    assert reloaded["cluster"].jupyterhub_config_path == "/opt/jh/jupyterhub_config.py"
    assert "jupyterhub_config_path" in cfg.read_text(encoding="utf-8")

    write_remote(_make_host("cluster", jupyterhub_config_path=""), path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "jupyterhub_config_path" not in text
    assert load_remotes(cfg)["cluster"].jupyterhub_config_path == ""


def test_write_remote_queue_wait_timeout_round_trip(tmp_path):
    """queue_wait_timeout_min survives a write cycle and defaults to 30 when
    a pre-existing block omits it."""
    cfg = tmp_path / "remotes.toml"
    write_remote(_make_host("cluster", queue_wait_timeout_min=120), path=cfg)
    assert load_remotes(cfg)["cluster"].queue_wait_timeout_min == 120
    assert "queue_wait_timeout_min = 120" in cfg.read_text(encoding="utf-8")

    legacy = tmp_path / "legacy.toml"
    legacy.write_text("[remote.old]\nssh_alias = \"old\"\n", encoding="utf-8")
    assert load_remotes(legacy)["old"].queue_wait_timeout_min == 30


def test_compute_launcher_defaults_empty_when_absent(tmp_path):
    """A pre-existing block with no compute_launcher key loads with the
    field defaulted to '' (runs on the SSH host, legacy behaviour)."""
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.legacy]\n"
        'ssh_alias = "legacy"\n',
        encoding="utf-8",
    )
    reloaded = load_remotes(cfg)
    assert reloaded["legacy"].compute_launcher == ""


def test_write_remote_windows_path_with_backslashes(tmp_path):
    """A Windows-style ssh_key_path with backslashes must be escaped
    so the TOML parser doesn't read ``\\U`` (from ``\\Users``) as the
    start of a ``\\Uxxxxxxxx`` Unicode escape and reject the file with
    ``invalid hex value``. The path must also round-trip verbatim."""
    cfg = tmp_path / "remotes.toml"
    win_path = r"C:\Users\me\.ssh\allwell_bigbox"
    write_remote(
        _make_host("bigbox", ssh_key_path=win_path),
        path=cfg,
    )
    # Round-trip via the real loader — this would raise
    # tomli.TOMLDecodeError under the pre-fix formatter.
    reloaded = load_remotes(cfg)
    assert reloaded["bigbox"].ssh_key_path == win_path
    # The on-disk representation must escape the backslashes.
    raw = cfg.read_text(encoding="utf-8")
    assert r"C:\\Users\\me\\.ssh\\allwell_bigbox" in raw


# ── delete_remote ──────────────────────────────────────────────────────────


def test_delete_remote_removes_the_section(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "[remote.gone]\n"
        "ssh_alias = \"gone\"\n"
        "cpu_only = true\n",
        encoding="utf-8",
    )
    assert delete_remote("gone", path=cfg) is True
    reloaded = load_remotes(cfg)
    assert reloaded == {}


def test_delete_remote_leaves_other_sections_alone(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text(
        "# keep this comment\n"
        "[remote.a]\n"
        "ssh_alias = \"a\"\n"
        "\n"
        "[remote.b]\n"
        "ssh_alias = \"b\"\n"
        "cache_budget_gb = 77\n",
        encoding="utf-8",
    )
    delete_remote("a", path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "[remote.a]" not in text
    assert "# keep this comment" in text
    reloaded = load_remotes(cfg)
    assert set(reloaded) == {"b"}
    assert reloaded["b"].cache_budget_gb == 77


def test_delete_remote_unknown_is_silent_noop(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text("[remote.x]\nssh_alias = \"x\"\n", encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")
    assert delete_remote("nonexistent", path=cfg) is False
    assert cfg.read_text(encoding="utf-8") == before


def test_delete_remote_missing_file_is_silent(tmp_path):
    assert delete_remote("ghost", path=tmp_path / "nope.toml") is False


# ── update_bandwidth still works after the refactor ────────────────────────


def test_update_bandwidth_still_round_trips(tmp_path):
    cfg = tmp_path / "remotes.toml"
    write_remote(_make_host("bw", measured_bandwidth_mbps=10.0), path=cfg)
    update_bandwidth("bw", 99.5, path=cfg)
    reloaded = load_remotes(cfg)
    assert reloaded["bw"].measured_bandwidth_mbps == pytest.approx(99.5)


# ── interaction with get_remote ────────────────────────────────────────────


def test_write_then_get_remote_round_trip(tmp_path):
    cfg = tmp_path / "remotes.toml"
    write_remote(
        _make_host("rt", remote_project_dir="~/custom"),
        path=cfg,
    )
    host = get_remote("rt", path=cfg)
    assert host is not None
    assert host.remote_project_dir == "~/custom"
