"""On Windows every ssh the app starts reads one known-hosts file, so a changed host key is fixed in one place.

Reported on Windows: "Error in rsync protocol data stream (code 12)" after "REMOTE HOST IDENTIFICATION HAS
CHANGED", naming ``/home/<user>/.ssh/known_hosts`` — and deleting the known-hosts file, then rebuilding it from the
remote configurator's connection test, changed nothing. The app starts two ``ssh.exe`` on Windows: Git for Windows'
for its own commands and the test, and the one beside ``rsync.exe`` (from rsync's own MSYS2 / Cygwin runtime) for
every transfer. Each runtime has its own home, so the test fixed the file under ``C:\\Users\\<user>`` while the
transfers kept failing on the stale key under the rsync runtime's ``/home/<user>``. Now every ssh is told which file
to use (``remote_config.known_hosts_options``): the Windows profile's ``.ssh/known_hosts``.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

import services.remote_config as rc
from services.remote_config import RemoteHost

HOME = "/Users/zakary singer"                       # a profile path with a space, the case that needs quoting
EXPECTED = ["-o", 'UserKnownHostsFile="/Users/zakary singer/.ssh/known_hosts"']


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(rc, "_is_windows", lambda: True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(HOME)))
    monkeypatch.setattr(rc, "resolve_ssh_binary", lambda: "git-ssh.exe")
    monkeypatch.setattr(rc, "resolve_ssh_for_rsync", lambda: "msys-ssh.exe")


def _inner(argv: list[str]) -> list[str]:
    """The ssh command inside an rsync argv's ``-e`` string, as rsync splits it."""
    return shlex.split(argv[argv.index("-e") + 1])


def test_on_posix_nothing_is_added():
    if rc._is_windows():
        pytest.skip("POSIX only")
    assert rc.known_hosts_options() == []
    assert not any("UserKnownHostsFile" in a for a in RemoteHost(name="t", ssh_alias="t").ssh_argv())


def test_the_option_names_the_profile_s_file_quoted(windows):
    assert rc.known_hosts_options() == EXPECTED


def test_the_app_s_own_ssh_and_every_transfer_name_the_same_file(windows, monkeypatch):
    import services.remote_transport as rt

    monkeypatch.setattr(rt, "resolve_ssh_for_rsync", lambda: "msys-ssh.exe")
    host = RemoteHost(name="t", ssh_alias="lobby")
    argv = host.ssh_argv(allocate_tty=False)
    assert argv[0] == "git-ssh.exe" and argv[1:3] == EXPECTED
    inner = _inner(rt._rsync_ssh_transport(host))
    assert inner[0] == "msys-ssh.exe" and inner[1:3] == EXPECTED


def test_the_connection_test_names_the_file_the_transfers_check(windows, monkeypatch):
    import subprocess

    import services.ssh_key_setup as sks

    monkeypatch.setattr(sks, "resolve_ssh_binary", lambda: "git-ssh.exe")
    seen = {}

    def run(cmd, **_kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    monkeypatch.setattr(sks.subprocess, "run", run)
    assert sks.probe_ssh_connection("lobby") == (True, "")
    assert seen["cmd"][1:3] == EXPECTED


def test_the_bootstrap_s_ssh_and_its_bandwidth_probe_name_it_too(windows, monkeypatch):
    import subprocess

    import services.remote_bootstrap as rb

    monkeypatch.setattr(rb, "_ssh_binary", lambda: "git-ssh.exe")
    monkeypatch.setattr(rb, "_rsync_binary", lambda: "rsync.exe")
    calls = []

    def run(cmd, *args, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rb.subprocess, "run", run)
    rb._ssh_run("lobby", ["true"])
    assert calls[-1][1:3] == EXPECTED
    try:
        rb.measure_bandwidth("lobby")
    except Exception:            # the probe's own bookkeeping after the call is not what is measured here
        pass
    rsync = next(c for c in calls if c and c[0] == "rsync.exe")
    assert _inner(rsync)[1:3] == EXPECTED
