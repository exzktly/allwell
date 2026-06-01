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
    _controlpath_template,
    get_remote,
    load_remotes,
    resolve_rsync_binary,
    resolve_ssh_binary,
    to_rsync_path,
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
    # v5.2.1: ssh_key_path defaults to empty (uses ~/.ssh/config).
    assert host.ssh_key_path == ""


def test_load_remotes_with_ssh_key_path(tmp_path):
    """v5.2.1: the wizard records a per-host key path which must
    round-trip cleanly."""
    cfg = _write_config(
        tmp_path,
        """
        [remote.keyed]
        ssh_alias = "keyed"
        ssh_key_path = "~/.ssh/allwell_keyed"
        """,
    )
    host = load_remotes(cfg)["keyed"]
    assert host.ssh_key_path == "~/.ssh/allwell_keyed"


def test_ssh_argv_includes_identity_when_key_path_set():
    """v5.2.1: a configured ssh_key_path must surface as ``-i <path>``
    so AllWell uses that key over whatever ssh's default search would
    pick."""
    host = RemoteHost(name="x", ssh_alias="x", ssh_key_path="~/.ssh/k")
    argv = host.ssh_argv()
    assert "-i" in argv
    i_idx = argv.index("-i")
    # ~ is expanded in the argv so ssh doesn't have to interpret it
    # via /bin/sh.
    assert not argv[i_idx + 1].startswith("~")
    assert argv[i_idx + 1].endswith("/.ssh/k")


def test_ssh_argv_omits_identity_when_key_path_unset():
    host = RemoteHost(name="x", ssh_alias="x")
    argv = host.ssh_argv()
    assert "-i" not in argv


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


def test_ssh_argv_shape(monkeypatch):
    # Force POSIX explicitly: this test asserts on ControlMaster,
    # which is POSIX-only after v5.x.x (Windows skips it to avoid
    # the Cygwin Unix-socket failures that yield exit 4294967295).
    _force_posix(monkeypatch)
    host = RemoteHost(name="x", ssh_alias="my-alias")
    argv = host.ssh_argv()
    assert argv[0] == "ssh"
    assert "-tt" in argv
    assert "my-alias" in argv
    # ControlMaster wires for connection reuse on POSIX.
    assert any("ControlMaster=auto" in a for a in argv)
    assert any("ControlPersist=60s" in a for a in argv)


def test_ssh_argv_no_tty():
    host = RemoteHost(name="x", ssh_alias="alias")
    argv = host.ssh_argv(allocate_tty=False)
    assert "-tt" not in argv
    assert "alias" in argv


# ── Windows-host helpers ────────────────────────────────────────────────────
#
# All tests below toggle the framework's view of the local platform via
# the ``_is_windows`` helper in ``services.remote_config``. They never
# touch ``os.name`` directly — that's a module-singleton attribute and
# patching it globally breaks pytest's own internals (which try to
# instantiate WindowsPath when ``os.name == 'nt'``).


def _force_windows(monkeypatch) -> None:
    monkeypatch.setattr("services.remote_config._is_windows", lambda: True)


def _force_posix(monkeypatch) -> None:
    monkeypatch.setattr("services.remote_config._is_windows", lambda: False)


def test_to_rsync_path_posix_passthrough(monkeypatch):
    _force_posix(monkeypatch)
    assert to_rsync_path("/home/user/x") == "/home/user/x"
    assert to_rsync_path(Path("/home/user/x")) == "/home/user/x"


def test_to_rsync_path_windows_drive_letter(monkeypatch):
    """C:\\Users\\foo\\plate → /c/Users/foo/plate (Cygwin form Git Bash expects)."""
    _force_windows(monkeypatch)
    assert to_rsync_path("C:\\Users\\foo\\plate") == "/c/Users/foo/plate"
    # Forward-slash form also accepted.
    assert to_rsync_path("D:/data/run1") == "/d/data/run1"
    # Lower-case drive letter normalised.
    assert to_rsync_path("Z:\\projects\\x") == "/z/projects/x"


def test_to_rsync_path_windows_unc_raises(monkeypatch):
    _force_windows(monkeypatch)
    with pytest.raises(NotImplementedError, match="UNC paths"):
        to_rsync_path(r"\\fileserver\share\plate")


def test_resolve_ssh_binary_posix(monkeypatch):
    _force_posix(monkeypatch)
    assert resolve_ssh_binary() == "ssh"


def test_resolve_ssh_binary_windows_uses_git_bash(monkeypatch):
    _force_windows(monkeypatch)
    # Pretend Git for Windows is installed at the system-wide location
    # AND every probed path is a real file. The first candidate wins.
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setattr("services.remote_config.shutil.which", lambda _name: None)
    monkeypatch.setattr("services.remote_config.Path.is_file", lambda self: True)
    result = resolve_ssh_binary()
    assert result.endswith("ssh.exe")


def test_resolve_ssh_binary_windows_falls_back_when_git_bash_missing(monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setattr("services.remote_config.Path.is_file", lambda self: False)
    monkeypatch.setattr("services.remote_config.shutil.which", lambda _name: None)
    assert resolve_ssh_binary() == "ssh"


def test_resolve_ssh_binary_windows_per_user_install(monkeypatch):
    """The default per-user Git for Windows install lands under
    ``%LOCALAPPDATA%\\Programs\\Git\\``, NOT ``C:\\Program Files\\Git\\``.
    AllWell must detect either layout — previously the hardcoded
    ``Program Files`` path missed every per-user install and ssh
    silently fell back to native Windows OpenSSH (or no ssh at all)."""
    _force_windows(monkeypatch)
    monkeypatch.setattr("services.remote_config.shutil.which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", "/home/alice/AppData/Local")
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramW6432", raising=False)
    expected = "/home/alice/AppData/Local/Programs/Git/usr/bin/ssh.exe"
    monkeypatch.setattr(
        "services.remote_config.Path.is_file",
        lambda self: str(self) == expected,
    )
    assert resolve_ssh_binary() == expected


def test_resolve_ssh_binary_windows_via_path_discovery(monkeypatch):
    """When git.exe is on PATH at a non-standard location (scoop,
    chocolatey, custom install), AllWell derives the usr/bin
    directory from it. This is what saves users whose Git install
    is at e.g. ``C:\\Users\\bob\\scoop\\apps\\git\\current\\``."""
    _force_windows(monkeypatch)
    monkeypatch.setattr(
        "services.remote_config.shutil.which",
        lambda name: "/home/bob/scoop/apps/git/current/cmd/git.exe" if name == "git" else None,
    )
    # No env-var-based discovery should win — only the PATH-derived one.
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    expected = "/home/bob/scoop/apps/git/current/usr/bin/ssh.exe"
    monkeypatch.setattr(
        "services.remote_config.Path.is_file",
        lambda self: str(self) == expected,
    )
    assert resolve_ssh_binary() == expected


def test_resolve_ssh_for_rsync_prefers_co_located_ssh(monkeypatch):
    """When rsync.exe and ssh.exe live in the same ``usr\\bin\\``
    directory on Windows, AllWell must pair them. Mismatched MSYS /
    Cygwin runtimes (MSYS2 rsync + Git for Windows ssh) break
    Cygwin's fork-emulation with ``dup() in/out/err failed``, which
    surfaces as rsync exit 12 / "connection unexpectedly closed"."""
    from services.remote_config import resolve_ssh_for_rsync
    _force_windows(monkeypatch)
    msys_rsync = "/tmp/msys64/usr/bin/rsync.exe"
    msys_ssh   = "/tmp/msys64/usr/bin/ssh.exe"
    git_ssh    = "/tmp/git/usr/bin/ssh.exe"
    monkeypatch.setattr(
        "services.remote_config.resolve_rsync_binary",
        lambda: msys_rsync,
    )
    monkeypatch.setattr(
        "services.remote_config.resolve_ssh_binary",
        lambda: git_ssh,
    )
    monkeypatch.setattr(
        "services.remote_config.Path.is_file",
        lambda self: str(self) == msys_ssh,
    )
    assert resolve_ssh_for_rsync() == msys_ssh


def test_resolve_ssh_for_rsync_falls_back_when_no_paired_ssh(monkeypatch):
    """If rsync was found but no ssh sits next to it (e.g., user
    installed rsync via cwRsync, removed its ssh), fall back to the
    normal :func:`resolve_ssh_binary` (Git for Windows etc.)."""
    from services.remote_config import resolve_ssh_for_rsync
    _force_windows(monkeypatch)
    monkeypatch.setattr(
        "services.remote_config.resolve_rsync_binary",
        lambda: "/tmp/orphan/rsync.exe",
    )
    monkeypatch.setattr(
        "services.remote_config.resolve_ssh_binary",
        lambda: "/tmp/git/usr/bin/ssh.exe",
    )
    monkeypatch.setattr(
        "services.remote_config.Path.is_file",
        lambda self: False,
    )
    assert resolve_ssh_for_rsync() == "/tmp/git/usr/bin/ssh.exe"


def test_resolve_ssh_for_rsync_graceful_when_rsync_missing(monkeypatch):
    """If ``resolve_rsync_binary`` raises (no rsync anywhere),
    :func:`resolve_ssh_for_rsync` must NOT propagate that — the
    caller will hit the actionable FileNotFoundError when it
    actually invokes rsync. Until then just return the normal ssh
    so the rest of argv construction doesn't blow up."""
    from services.remote_config import resolve_ssh_for_rsync
    _force_windows(monkeypatch)
    def raises():
        raise FileNotFoundError("no rsync")
    monkeypatch.setattr(
        "services.remote_config.resolve_rsync_binary", raises,
    )
    monkeypatch.setattr(
        "services.remote_config.resolve_ssh_binary",
        lambda: "/tmp/git/usr/bin/ssh.exe",
    )
    assert resolve_ssh_for_rsync() == "/tmp/git/usr/bin/ssh.exe"


def test_resolve_ssh_for_rsync_posix_passthrough(monkeypatch):
    from services.remote_config import resolve_ssh_for_rsync
    _force_posix(monkeypatch)
    assert resolve_ssh_for_rsync() == "ssh"


def test_resolve_rsync_binary_posix(monkeypatch):
    _force_posix(monkeypatch)
    assert resolve_rsync_binary() == "rsync"


def test_resolve_rsync_binary_windows_uses_git_bash(monkeypatch):
    _force_windows(monkeypatch)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setattr("services.remote_config.shutil.which", lambda _name: None)
    monkeypatch.setattr("services.remote_config.Path.is_file", lambda self: True)
    result = resolve_rsync_binary()
    assert result.endswith("rsync.exe")


def test_resolve_rsync_binary_windows_falls_through_to_cwrsync(monkeypatch):
    """When Git for Windows doesn't have rsync but cwRsync is
    installed, the framework must auto-detect cwRsync rather than
    falling through to a non-existent PATH ``rsync``."""
    _force_windows(monkeypatch)
    monkeypatch.setattr(
        "services.remote_config.Path.is_file",
        # Only cwRsync's standard location returns True.
        lambda self: str(self) == r"C:\Program Files\cwRsync\bin\rsync.exe",
    )
    # PATH lookup must NOT be the winner (cwRsync wins first).
    monkeypatch.setattr("shutil.which", lambda _name: r"C:\bogus\rsync.exe")
    result = resolve_rsync_binary()
    assert "cwRsync" in result, result


def test_resolve_rsync_binary_windows_raises_when_nothing_found(monkeypatch):
    """The whole point of the v5.x.x change: when rsync is not
    installed anywhere, we raise a clear FileNotFoundError with
    install guidance, INSTEAD of letting subprocess raise the
    cryptic ``[WinError 2] The system cannot find the file
    specified`` deep inside a sync helper."""
    _force_windows(monkeypatch)
    monkeypatch.setattr("services.remote_config.Path.is_file", lambda self: False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(FileNotFoundError) as exc_info:
        resolve_rsync_binary()
    msg = str(exc_info.value)
    # The message must name each of the documented install routes so
    # users with different package-manager preferences each have a
    # path forward.
    assert "rsync.exe is not installed" in msg
    assert "Git for Windows" in msg
    assert "cwRsync" in msg
    assert "MSYS2" in msg


def test_ssh_argv_on_windows_omits_controlmaster(monkeypatch):
    """ControlMaster is POSIX-only — disable on Windows. Cygwin's
    Unix-socket emulation (used by Git for Windows' ssh.exe) and
    native Windows OpenSSH (no Unix sockets at all) each have
    real-world failure modes — antivirus, MAX_PATH, mismatched HOME,
    ownership-bit translation — that make the multiplexer exit -1
    (= 4294967295 unsigned) before the requested command runs.
    Skipping ControlMaster on Windows costs one TCP handshake per
    invocation but avoids the entire failure class."""
    _force_windows(monkeypatch)
    monkeypatch.setattr("services.remote_config.Path.is_file", lambda self: True)
    host = RemoteHost(name="x", ssh_alias="alias")
    argv = host.ssh_argv()
    assert not any("ControlMaster" in a for a in argv), (
        f"ControlMaster must be off on Windows; argv was: {argv}"
    )
    assert not any("ControlPath" in a for a in argv)
    assert not any("ControlPersist" in a for a in argv)
    # ServerAliveInterval must still be there — it's portable and
    # keeps long-running pipeline ssh's from being killed by NAT.
    assert any("ServerAliveInterval=30" in a for a in argv)
    # The Git Bash binary preference is unchanged.
    assert argv[0].endswith("ssh.exe")
    # Other framework features still apply.
    assert "-tt" in argv
    assert "alias" in argv


def test_ssh_argv_on_posix_keeps_controlmaster(monkeypatch):
    """Mirror of the above for the POSIX side: ControlMaster IS
    enabled (the original behavior), since Unix sockets work
    natively there."""
    _force_posix(monkeypatch)
    host = RemoteHost(name="x", ssh_alias="alias")
    argv = host.ssh_argv()
    assert any("ControlMaster=auto" in a for a in argv)
    assert any("ControlPath=" in a for a in argv)
    assert any("ControlPersist=60s" in a for a in argv)


def test_default_config_path_windows_prefers_appdata_when_file_exists(monkeypatch, tmp_path):
    _force_windows(monkeypatch)
    appdata = tmp_path / "AppData"
    (appdata / "allwell").mkdir(parents=True)
    cfg = appdata / "allwell" / "remotes.toml"
    cfg.write_text("[remote.x]\nssh_alias = \"x\"\n")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("ALLWELL_REMOTES", raising=False)
    from services.remote_config import default_config_path
    assert default_config_path() == cfg


def test_default_config_path_posix_unchanged(monkeypatch):
    _force_posix(monkeypatch)
    monkeypatch.delenv("ALLWELL_REMOTES", raising=False)
    from services.remote_config import default_config_path
    assert str(default_config_path()).endswith(".config/allwell/remotes.toml")


def test_controlpath_template_windows_avoids_colon(monkeypatch):
    """On Windows the OpenSSH ``%r@%h:%p`` default expands to a path
    containing ``:``, which Windows filesystems reserve. Cygwin's
    OpenSSH (Git for Windows) then can't create the control socket
    and ssh exits ``-1`` (= 4294967295 unsigned). The template must
    substitute ``_`` for ``:`` on Windows so the expanded filename is
    valid; the per-(user, host, port) uniqueness is preserved."""
    _force_windows(monkeypatch)
    tpl = _controlpath_template()
    # The expansion vars stay intact …
    assert "%r" in tpl and "%h" in tpl and "%p" in tpl
    # … but the host/port separator is ``_``, not ``:``, after the
    # drive-letter (if any) prefix.
    drive, rest = os.path.splitdrive(tpl)
    assert ":" not in rest, (
        f"Windows ControlPath must not contain ':' after the drive prefix; "
        f"got {tpl!r}"
    )
    assert "%h_%p" in tpl


def test_controlpath_template_posix_unchanged(monkeypatch):
    _force_posix(monkeypatch)
    tpl = _controlpath_template()
    assert tpl.endswith("allwell-cm-%r@%h:%p")
