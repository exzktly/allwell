"""A bad link to the remote is noticed, retried or reported — never waited on.

The transport rows of the remote pipeline review (whose tracker is in git history) (C3, C4, C8,
S4, S5, S8, S9, S10, and the dataset-cache half of E5), each reproduced against
a real local sshd before it was fixed:

* a dead connection took ``2 × queue_wait`` keepalives — 30 minutes at the
  default — to notice, and a connection-sharing master that died (sleep, a
  network change) made every control command time out for that long while a
  fresh ssh answered in 0.2 s;
* rsync exit 255 — a dropped session, a throttled login node — was never
  retried, so one blip ended a batched run hours in;
* a frozen remote held an upload for ever, and Stop could not end it, because
  the reader checked Stop only between lines rsync was not printing;
* a failed control command said "returned non-zero exit status 255" and threw
  ssh's own reason away.

No test here reaches a real host. The binaries are stand-ins on ``PATH`` —
small bash scripts that fail on cue — so the real code runs end to end and
only what is on the other side of the wire is played.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import pytest

from services import remote_transport
from services.remote_config import RemoteHost
from services.remote_upload import DatasetCache


# ── stand-in binaries ────────────────────────────────────────────────────────
#
# Named ``stand-in-ssh`` / ``stand-in-rsync`` rather than ``ssh`` / ``rsync``:
# the suite's autouse guard (``conftest._no_real_network_tools``) refuses any
# binary called by those names. The app finds its binaries through the
# resolvers, so those are what point at the stand-ins.

_RESOLVERS = {
    "ssh": ["services.remote_config.resolve_ssh_binary"],
    "rsync": ["services.remote_upload.resolve_rsync_binary",
              "services.remote_transport.resolve_rsync_binary",
              "services.remote_code_sync.resolve_rsync_binary"],
}


@pytest.fixture
def stand_in(tmp_path, monkeypatch):
    """``stand_in("ssh", body)`` installs a bash stand-in for that binary."""
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setattr("services.remote_config._is_windows", lambda: False)

    def install(which: str, body: str) -> Path:
        path = d / f"stand-in-{which}"
        path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
        path.chmod(0o755)
        for target in _RESOLVERS[which]:
            monkeypatch.setattr(target, lambda p=str(path): p)
        return path
    return install


def _host(**kw) -> RemoteHost:
    return RemoteHost(name="t", ssh_alias="stand-in-host", remote_project_dir="~/aw",
                      remote_staging_dir="~/.s", **kw)


def _option(argv: list[str], name: str) -> str | None:
    """The value of ``-o <name>=…`` in *argv*, or ``None``."""
    for i, a in enumerate(argv):
        if a == "-o" and i + 1 < len(argv) and argv[i + 1].startswith(name + "="):
            return argv[i + 1].split("=", 1)[1]
    return None


# ── C3 / S8: every ssh is unattended and notices a dead link in ~2 min ──────

@pytest.mark.parametrize("queue_wait", [0, 1, 30, 1440])
def test_every_ssh_carries_the_same_short_keepalive(queue_wait, monkeypatch):
    """C3: the keepalive was ``ServerAliveCountMax = 2 × queue_wait_timeout_min``
    — a dead link went unnoticed for 30 minutes at the default and for ever at
    0, while bounding no queue wait (sshd answers keepalives while a job is
    queued). Every ssh the app starts now carries 30 s × 4."""
    from services import remote_bootstrap, ssh_key_setup

    host = _host(queue_wait_timeout_min=queue_wait)
    argvs = {"ssh_argv": host.ssh_argv(allocate_tty=False),
             "ssh_argv -tt": host.ssh_argv(allocate_tty=True),
             "run stream": host.ssh_argv(allocate_tty=True, share_connection=False),
             "rsync -e": remote_transport._rsync_ssh_transport(host)[1].split()}

    seen: list[list[str]] = []

    def fake_run(cmd, *a, **kw):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    remote_bootstrap._ssh_run("stand-in-host", ["true"], check=False)
    argvs["bootstrap ssh"] = seen[-1]
    ssh_key_setup.probe_ssh_connection("stand-in-host")
    argvs["key-setup probe"] = seen[-1]

    for where, argv in argvs.items():
        assert (_option(argv, "ServerAliveInterval"), _option(argv, "ServerAliveCountMax")) \
            == ("30", "4"), f"{where}: {argv}"


def test_no_ssh_waits_on_a_prompt_or_an_unreachable_host():
    """S8: ``ssh_argv`` had no ``BatchMode`` and no ``ConnectTimeout``, though
    ``ssh_key_setup`` said every call had them — an app started from a terminal
    could block on a password prompt nobody saw."""
    argv = _host().ssh_argv(allocate_tty=False)
    assert (_option(argv, "BatchMode"), _option(argv, "ConnectTimeout"),
            _option(argv, "StrictHostKeyChecking")) == ("yes", "15", "accept-new")


def test_the_control_socket_fits_the_platform_limit(tmp_path, monkeypatch):
    """S9: ``allwell-cm-%r@%h:%p`` under ``~/.ssh`` overran the 104-byte socket
    limit for a long user or host name, and ssh then refused every command
    ("ControlPath too long", exit 255). Measured with the expansion a real
    session gives: a long home, a long user and a long login-node name."""
    from services.remote_config import _controlpath_template

    home = tmp_path / ("h" * max(1, 40 - len(str(tmp_path))))
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr("services.remote_config._is_windows", lambda: False)
    tpl = _controlpath_template()
    expanded = (tpl.replace("%C", "0" * 40).replace("%r", "firstname.lastname")
                .replace("%h", "login-node-01.hpc.example-university.edu").replace("%p", "22"))
    assert len(expanded.encode()) < 104, expanded


# ── S9: a failed control command says why ────────────────────────────────────

def test_a_failed_control_command_carries_ssh_s_reason(stand_in):
    """S9: the run's first control command is the code sync's ``mkdir -p``
    (``check=True``). Its failure reached the user as "Command '[…]' returned
    non-zero exit status 255." with ssh's own words dropped."""
    from services.remote_runner import RemoteRunner

    stand_in("ssh",
              'echo "nobody@stand-in-host: Permission denied (publickey)." >&2\nexit 255\n')
    with pytest.raises(subprocess.CalledProcessError) as caught:
        RemoteRunner(_host()).sync_required_files()
    assert "Permission denied (publickey)" in str(caught.value)


def _plate(tmp_path: Path, wells=("A01", "B02"), size=10_000):
    from services.remote_staging import LocalInputMode, ZippedInput
    d = tmp_path / "in"
    d.mkdir(exist_ok=True)
    zips = []
    for w in wells:
        z = d / f"{w}.zip"
        with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr(f"Exp_{w}_F001_T01_DAPI.tif", os.urandom(size))
        zips.append(z)
    return ZippedInput(zip_dir=d, mode=LocalInputMode.ZIP, well_zips=tuple(zips))


def test_a_failed_verification_listing_is_not_blamed_on_rsync(stand_in, tmp_path):
    """S9: after a successful upload the archives are counted on the remote.
    When that count's own ssh failed, the run said "rsync reported success but
    some files did not reach the remote" — blaming the upload for a listing."""
    stand_in("ssh",
              # The count is piped to ``bash -s``; every other command succeeds.
              'for a in "$@"; do [ "$a" = "-s" ] && { echo "Connection closed by 10.0.0.5 port 22" >&2; '
              'exit 255; }; done\nexit 0\n')
    stand_in("rsync",
              '[ "$1" = "--version" ] && { echo "rsync  version 3.2.7  protocol version 31"; exit 0; }\n'
              'echo "sent 100 bytes  received 10 bytes  200 bytes/sec"\nexit 0\n')
    with pytest.raises(RuntimeError) as caught:
        DatasetCache(_host()).ensure_uploaded(_plate(tmp_path))
    msg = str(caught.value)
    assert "did not reach the remote" not in msg, msg
    assert "could not list" in msg and "Connection closed by 10.0.0.5 port 22" in msg, msg


# ── C4 / S5: exit 255 is retried when the connection failed, not the login ──

_COUNTED_RSYNC = (
    '[ "$1" = "--version" ] && { echo "rsync  version 3.2.7  protocol version 31"; exit 0; }\n'
    'n=$(cat "$COUNT" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$COUNT"\n'
    'if [ "$n" -eq 1 ]; then printf "%s\\n" "$FIRST_SAID" >&2; exit 255; fi\n'
    # A file destination (not ``dir/``, not ``host:path``) is written, as a pull does.
    'dest="${@: -1}"; case "$dest" in */|*:*) ;; *) echo pulled > "$dest";; esac\n'
    'echo "sent 100 bytes  received 10 bytes  200 bytes/sec"\nexit 0\n'
)

TRANSPORT_FAILURES = [
    "rsync: [sender] write error: Broken pipe (32)",
    "Connection closed by 10.0.0.5 port 22",
    "kex_exchange_identification: read: Connection reset by peer",
    "ssh: connect to host stand-in-host port 22: Connection timed out",
    "Timeout, server stand-in-host not responding.",
    "rsync: connection unexpectedly closed (0 bytes received so far) [sender]",
]
LOGIN_FAILURES = [
    "nobody@stand-in-host: Permission denied (publickey).",
    "Host key verification failed.",
    # A refused login that also says the connection closed is still a refusal.
    "nobody@stand-in-host: Permission denied (publickey).\nConnection closed by 10.0.0.5 port 22",
]


@pytest.fixture
def counted_rsync(stand_in, tmp_path, monkeypatch):
    stand_in("rsync", _COUNTED_RSYNC)
    count = tmp_path / "rsync-calls"
    monkeypatch.setenv("COUNT", str(count))
    monkeypatch.setattr("services.remote_transport.time.sleep", lambda _s: None)

    def calls() -> int:
        return int(count.read_text()) if count.exists() else 0
    return calls


@pytest.mark.parametrize("said, attempts, rc", [
    *[(s, 2, 0) for s in TRANSPORT_FAILURES],
    *[(s, 1, 255) for s in LOGIN_FAILURES],
])
def test_a_fetch_rsync_255_is_retried_only_when_the_connection_failed(
        said, attempts, rc, counted_rsync, monkeypatch, tmp_path):
    """C4: the Fetcher's transfers go through ``_rsync_with_retry``; exit 255
    was not in its retry set, so a login node throttling the fetch's ten
    fresh connections per well lost the well on the first refusal."""
    monkeypatch.setenv("FIRST_SAID", said)
    ok = remote_transport.pull_small_file(_host(), "~/x/marker.json", tmp_path / "marker.json")
    assert (counted_rsync(), ok) == (attempts, rc == 0)


@pytest.mark.parametrize("said, attempts, rc", [
    (TRANSPORT_FAILURES[0], 2, 0),
    (TRANSPORT_FAILURES[2], 2, 0),
    (LOGIN_FAILURES[0], 1, 255),
])
def test_an_upload_dropped_mid_transfer_is_retried(said, attempts, rc, counted_rsync,
                                                    monkeypatch, tmp_path):
    """S5: a session killed mid-upload exits rsync 255 ("Broken pipe",
    "unexplained error (code 255)"), which ended a single-shot run after one
    attempt and a batched one with every remaining batch unsent."""
    monkeypatch.setenv("FIRST_SAID", said)
    plate = _plate(tmp_path, wells=("A01",))
    _sent, _elapsed, got_rc = DatasetCache(_host()).upload_subset(
        list(plate.well_zips), "~/.s/runs/r/in")
    assert (counted_rsync(), got_rc) == (attempts, rc)


# ── S4 / S10: Stop ends a silent transfer; a failed one is not painted done ──

def test_the_upload_rsync_has_an_idle_timeout_and_a_keepalive(stand_in, tmp_path):
    """S4: the upload rsync had no ``--timeout`` (the fetch had 120 s) and its
    ssh no keepalive, so a stalled link held it for ever."""
    argv_log = tmp_path / "argv"
    stand_in("rsync",
              '[ "$1" = "--version" ] && { echo "rsync  version 3.2.7  protocol version 31"; exit 0; }\n'
              f'printf "%s\\n" "$@" > {argv_log}\nexit 0\n')
    DatasetCache(_host()).upload_subset(list(_plate(tmp_path).well_zips), "~/.s/r/in")
    argv = argv_log.read_text().splitlines()
    assert "--timeout=120" in argv
    transport = argv[argv.index("-e") + 1].split()
    assert (_option(transport, "ServerAliveInterval"), _option(transport, "ServerAliveCountMax")) \
        == ("30", "4")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it has stopped running all the same.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().split(") ", 1)[1][0] != "Z"
    except OSError:
        return True


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_stop_ends_an_upload_that_prints_nothing(stand_in, tmp_path):
    """S4: reproduced against a real sshd with the remote frozen after 61 MB:
    Stop set, and the upload still blocked 120 s later. The reader checked
    Stop between lines, and a frozen remote prints none. The stand-in starts a
    child that holds the output pipe, as rsync's ssh does, and goes silent."""
    pids = tmp_path / "pids"
    stand_in("rsync",
              '[ "$1" = "--version" ] && { echo "rsync  version 3.2.7  protocol version 31"; exit 0; }\n'
              f'sleep 300 & echo "$$ $!" > {pids}\necho "A01.zip"\nwait\n')
    stop = threading.Event()
    result: list = []
    th = threading.Thread(target=lambda: result.append(DatasetCache(_host()).upload_subset(
        list(_plate(tmp_path, wells=("A01",)).well_zips), "~/.s/r/in", stop_event=stop)),
        daemon=True)
    th.start()
    try:
        deadline = time.monotonic() + 10
        while not pids.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        stop.set()
        th.join(timeout=10)
        assert not th.is_alive(), "Stop was set 10 s ago and the upload is still running"
        rsync_pid, ssh_pid = (int(p) for p in pids.read_text().split())
        assert not _alive(ssh_pid), "the process holding rsync's pipe outlived Stop"
        assert result[0][2] != 0
    finally:
        for p in (pids.read_text().split() if pids.exists() else ()):
            try:
                os.kill(int(p), 9)
            except ProcessLookupError:
                pass
        th.join(timeout=10)


def test_stop_cuts_a_retry_backoff_short(stand_in):
    """S10: the streaming upload's backoff was a bare ``time.sleep``, so a Stop
    during it waited the backoff out and then started another attempt."""
    from services import remote_upload

    rsync = stand_in("rsync", "exit 10\n")   # 10: socket I/O error, retried
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    t0 = time.monotonic()
    _sent, _elapsed, rc, attempts = remote_upload._stream_rsync_with_retry(
        [str(rsync), "x", "y"], stop_event=stop)
    assert (attempts, rc) == (1, 10)
    assert time.monotonic() - t0 < 1.5


def _xfer_fractions(log: list[str], well: str) -> list[float]:
    from services.remote_wire import _well_xfer_line
    prefix = _well_xfer_line(well, "up", 0.0).rsplit(" ", 1)[0] + " "
    return [float(line.strip()[len(prefix):]) for line in log if line.startswith(prefix)]


@pytest.mark.parametrize("rc, last", [(23, 0.1), (0, 1.0)])
def test_a_failed_upload_does_not_paint_its_well_uploaded(rc, last, stand_in, tmp_path):
    """S10: the dial of the well in flight closed at full whatever rsync said,
    so a failed or stopped transfer showed that well uploaded. Here a tenth of
    A01's archive went across, then rsync exited 23 (not retried)."""
    plate = _plate(tmp_path, wells=("A01",), size=10_000)
    size = plate.well_zips[0].stat().st_size
    stand_in("rsync",
              '[ "$1" = "--version" ] && { echo "rsync  version 3.2.7  protocol version 31"; exit 0; }\n'
              f'echo "A01.zip"\necho "  {size // 10:,}  10%  1.00MB/s  0:00:01"\nexit {rc}\n')
    log: list[str] = []
    DatasetCache(_host()).upload_subset(list(plate.well_zips), "~/.s/r/in", log_fn=log.append)
    assert round(_xfer_fractions(log, "A01")[-1], 2) == last


# ── C8: a remote script is bounded ──────────────────────────────────────────

def test_a_remote_script_is_bounded(monkeypatch):
    """C8: ``_ssh_script`` passed no timeout, so every pre-launch resume
    listing and the verify listing waited on a dead link for as long as the
    keepalive allowed, with nothing Stop could reach."""
    seen: dict = {}

    def fake_run(cmd, *a, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    DatasetCache(_host())._ssh_script("ls ~\n", check=False)
    assert seen.get("timeout") == 300.0


# ── a dead connection-sharing master ────────────────────────────────────────

_MASTER_SSH = (
    'echo "$*" >> "$STATE/calls"\n'
    'case " $* " in *" -O exit "*) touch "$STATE/master_closed"; exit 0;; esac\n'
    'case " $* " in *"ControlPath=none"*) [ -e "$STATE/network_down" ] && exec sleep 30; exit 0;; esac\n'
    '[ -e "$STATE/master_closed" ] && exit 0\n'
    'exec sleep 30\n'
)


@pytest.fixture
def master_ssh(stand_in, tmp_path, monkeypatch):
    """An ssh whose shared master is dead: everything through it hangs, a
    connection with ``ControlPath=none`` answers, ``-O exit`` closes it."""
    state = tmp_path / "state"
    state.mkdir()
    stand_in("ssh", _MASTER_SSH)
    monkeypatch.setenv("STATE", str(state))
    monkeypatch.setattr(remote_transport, "_MASTER_PROBE_TIMEOUT_S", 1.0)
    return state


def test_a_dead_shared_connection_is_closed_and_the_command_retried(master_ssh):
    """Reproduced against a real sshd: with the master's session frozen, every
    ``_ssh_run`` returned 124 while a fresh ssh answered in 0.2 s, and stayed
    that way until the keepalive gave the master up — 30 minutes before C3."""
    r = DatasetCache(_host())._ssh_run(["touch", "~/x"], check=False, timeout_s=1.0)
    assert r.returncode == 0
    assert (master_ssh / "master_closed").exists()


def test_a_network_that_is_down_leaves_the_master_alone(master_ssh):
    """When a fresh connection fails too, the network is down, not the master:
    closing it would end the driver stream multiplexed through it for nothing."""
    (master_ssh / "network_down").touch()
    r = DatasetCache(_host())._ssh_run(["touch", "~/x"], check=False, timeout_s=1.0)
    assert r.returncode == 124
    assert "did not answer within 1s" in r.stderr
    assert not (master_ssh / "master_closed").exists()


def test_a_slow_command_on_a_healthy_master_is_not_retried(stand_in, tmp_path, monkeypatch):
    """A master that answers its probe means the command was merely slow; the
    timeout is reported (124), and nothing is closed or re-run."""
    calls = tmp_path / "calls"
    stand_in("ssh", f'echo "$*" >> {calls}\n'
              'case " $* " in *" true ") exit 0;; esac\nexec sleep 30\n')
    monkeypatch.setattr(remote_transport, "_MASTER_PROBE_TIMEOUT_S", 1.0)
    r = DatasetCache(_host())._ssh_script("sleep 100\n", check=False, timeout_s=1.0)
    assert r.returncode == 124
    lines = calls.read_text().splitlines()
    assert [ln.endswith(" true") for ln in lines] == [False, True], lines   # the command, the probe
