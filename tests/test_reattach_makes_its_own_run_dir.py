"""A re-attached run still needs the directory its *own* control files live in.

``spawn`` derives four remote paths from this run's ``run_id``: ``out/``, the
pidfile, the upload sentinel and the upload heartbeat. Only the first is ever
re-pointed at a previous run's directory — the other three deliberately stay
under this run's own id, because ``_sweep_orphaned_allocation`` finds a crashed
session's scheduler job by recomputing exactly that address.

The single ``mkdir -p`` was issued against the output directory *after*
``_reattach_output_dir`` had reassigned it. So on a re-attach it created the
previous run's directory (which already existed) and ``runs/<own_run_id>/`` was
created by nothing. That is not a rare interleaving: re-attach fires only when
the fresh address differs from the stored one, which means the cache key
churned, which means this ``run_id`` is new and its directory cannot pre-exist.

What it costs, on a re-attaching run only — an ordinary run's ``mkdir -p
runs/<id>/out`` creates ``runs/<id>`` on the way, which is why the fake below
models ``-p`` making every ancestor rather than just its operand:

* every host — ``echo $$ > run.pid`` has no directory to write into, so
  ``run.pid`` never exists and **Stop can no longer kill the remote
  pipeline**;
* a scheduler host — the heartbeat write fails silently (``check=False``), so
  the granted allocation stops seeing beats and releases the node after
  ``UPLOAD_HEARTBEAT_STALE_S``, the whole plate uploads anyway, and then
  ``touch <sentinel>`` (``check=True``) raises out of ``spawn``.

The pidfile is written by the remote shell script, which these tests do not
run; what they measure is the directory it needs, plus the two writes that do
go through ``_ssh_run`` and can therefore be observed — the heartbeat and the
sentinel. Reverting the fix fails the two re-attach tests, the second with
``RuntimeError: no such directory for ['touch', '…/.aw_upload_complete']``.
The ordinary-run test passes either way and is here to keep the fix from
trading one directory for the other.

These drive the real ``spawn``. A test of ``_choose_remote_output_dir`` cannot
see any of it — the decision it makes is correct; the directory handling around
it was not.
"""

from __future__ import annotations

import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services import run_journal as J
from services.remote_runner import (
    UPLOAD_HEARTBEAT,
    UPLOAD_SENTINEL,
    DatasetCache,
    LocalInputMode,
    OutputFilter,
    RemoteHost,
    RemoteRunner,
    ZippedInput,
    pipeline_argv_digest,
    source_fingerprint,
)

#: A previous run's output directory, under a different ``run_id`` than the one
#: this run will compute — which is what re-attaching means.
STORED_OUT = "~/.allwell-staging/runs/run-previous/out"


@pytest.fixture
def host() -> RemoteHost:
    return RemoteHost(
        name="test", ssh_alias="test-host",
        remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging",
        remote_python="/opt/python",
        cpu_only=False, extra_args=(),
        cache_budget_gb=10, measured_bandwidth_mbps=100.0,
    )


@pytest.fixture
def plate(tmp_path):
    """An input folder of two well archives, and the local output dir."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for name in ("A01.zip", "B02.zip"):
        with zipfile.ZipFile(in_dir / name, "w") as zf:
            zf.writestr("x.tif", b"\0" * 128)
    (tmp_path / "out").mkdir()
    return tmp_path


def _argv(plate) -> list[str]:
    return ["/usr/bin/python", "/local/process_microscopy.py",
            "--input_dir", str(plate / "in"),
            "--output_dir", str(plate / "out")]


def _record_a_previous_run(plate, argv) -> None:
    """A journal whose recorded address is not the one this run will compute."""
    J.open_run(plate / "out", spec={}, mode="remote", input_dir=plate / "in",
               argv=argv)
    J.record_remote(
        plate / "out",
        run_id="run-previous", cache_key="k", host_alias="test-host",
        remote_input_dir="~/.allwell-staging/data/k",
        remote_output_dir=STORED_OUT,
        argv_digest=pipeline_argv_digest(argv),
        source_key=source_fingerprint(plate / "in"),
    )
    J.close_run(plate / "out", outcome="error")


class _FakeRemoteFs:
    """The half of a remote filesystem these paths actually depend on.

    ``mkdir -p`` creates; ``echo >`` and ``touch`` refuse when the parent is
    absent, which is the whole point — the defect was a missing parent, and a
    stub that accepts every write cannot see it.
    """

    def __init__(self) -> None:
        self.dirs: set[str] = set()
        self.files: set[str] = set()
        self.refused: list[str] = []

    def _write(self, path: str) -> bool:
        parent = path.rsplit("/", 1)[0]
        if parent not in self.dirs:
            self.refused.append(path)
            return False
        self.files.add(path)
        return True

    def _mkdirs(self, path: str) -> None:
        """``-p`` makes every missing ancestor, which is load-bearing here.

        ``mkdir -p runs/<id>/out`` creates ``runs/<id>`` on the way, so an
        ordinary run's control files have a parent even though nothing names
        that directory. A fake that created only the operand would report the
        non-re-attaching path as broken too, and overstate the defect.
        """
        parts = path.split("/")
        for i in range(1, len(parts) + 1):
            self.dirs.add("/".join(parts[:i]))

    def run(self, argv: list[str]) -> bool:
        if argv[:2] == ["mkdir", "-p"]:
            for operand in argv[2:]:
                self._mkdirs(operand)
            return True
        if argv[:1] == ["touch"]:
            return self._write(argv[1])
        return True


def _spawn_with(host, plate, monkeypatch, *, completed_remote_wells,
                launcher="", output_filter=OutputFilter.FULL):
    """Drive the real ``spawn``, returning the fake remote filesystem."""
    fs = _FakeRemoteFs()
    runner = RemoteRunner(replace(host, compute_launcher=launcher))

    def _ssh_run(self, argv, *, check, **kw):
        ok = fs.run(list(argv))
        if not ok and check:
            raise RuntimeError(f"no such directory for {argv!r}")
        return MagicMock(stdout="", returncode=0 if ok else 1)

    monkeypatch.setattr(DatasetCache, "_ssh_run", _ssh_run)
    monkeypatch.setattr(
        DatasetCache, "_ssh_script",
        lambda self, script, *, check: MagicMock(
            stdout="".join(f"{w}_out.zip\n" for w in completed_remote_wells),
            returncode=0),
    )
    _KEY = "cachekey" + "0" * 24
    monkeypatch.setattr(DatasetCache, "cache_key", lambda self, zipped: _KEY)
    monkeypatch.setattr(DatasetCache, "ensure_uploaded",
                        lambda self, zipped, **kw: (_KEY, 0, 0.01))
    monkeypatch.setattr(RemoteRunner, "sync_required_files",
                        lambda self, **kw: None)
    monkeypatch.setattr(RemoteRunner, "_list_remote_completed_wells",
                        lambda self: set(completed_remote_wells))
    # Not what these tests are about, and it shells out for real.
    monkeypatch.setattr(RemoteRunner, "_sweep_orphaned_allocation",
                        lambda self, *a, **kw: None)

    def fake_popen(argv, **kw):
        m = MagicMock()
        m.stdout = MagicMock()
        m.stdout.__iter__ = lambda self: iter([])
        m.wait.return_value = 0
        m.returncode = 0
        m.pid = 1234
        return m

    monkeypatch.setattr("services.remote_runner.subprocess.Popen", fake_popen)

    in_dir = plate / "in"
    zipped = ZippedInput(
        zip_dir=in_dir, mode=LocalInputMode.ZIP,
        well_zips=(in_dir / "A01.zip", in_dir / "B02.zip"),
    )
    log: list[str] = []
    runner.spawn(zipped, plate / "out", pipeline_argv=_argv(plate),
                 output_filter=output_filter, log_fn=log.append)
    return fs, "".join(log), runner


def test_a_re_attached_run_creates_the_directory_its_pidfile_lives_in(
        host, plate, monkeypatch):
    _record_a_previous_run(plate, _argv(plate))
    fs, blob, runner = _spawn_with(
        host, plate, monkeypatch, completed_remote_wells={"A01", "B02"})

    assert "re-attaching" in blob, "the run under test did not re-attach"
    own = Path(runner._run.remote_pidfile).parent.as_posix()
    assert own in fs.dirs, (
        f"the run's own directory {own} was never created; only "
        f"{sorted(fs.dirs)} were")


def test_the_re_attached_output_directory_is_created_too(
        host, plate, monkeypatch):
    """The fix must not trade one directory for the other."""
    _record_a_previous_run(plate, _argv(plate))
    fs, blob, runner = _spawn_with(
        host, plate, monkeypatch, completed_remote_wells={"A01", "B02"})

    assert "re-attaching" in blob
    assert runner._run.remote_output_dir in fs.dirs
    assert runner._run.remote_output_dir.endswith("run-previous/out"), (
        "this test is not exercising a re-attach")


def test_an_ordinary_run_still_gets_its_directory(host, plate, monkeypatch):
    """No journal, so no re-attach — the common path must be untouched."""
    fs, blob, runner = _spawn_with(
        host, plate, monkeypatch, completed_remote_wells=set())

    assert "re-attaching" not in blob
    assert runner._run.remote_output_dir in fs.dirs
    assert Path(runner._run.remote_pidfile).parent.as_posix() in fs.dirs


def test_a_partially_complete_re_attach_can_write_its_heartbeat_and_sentinel(
        host, plate, monkeypatch):
    """The end the user feels, on the path that actually raised.

    One of two wells complete on the remote, so this is *not* a full cache hit:
    ``overlap`` is true, an allocation is asked for, the heartbeat beats and
    the upload sentinel is touched — all three under ``runs/<own_run_id>/``.
    With that directory missing, the heartbeat write failed silently
    (``check=False``, so the allocation simply stopped seeing beats) and the
    sentinel ``touch`` raised out of ``spawn`` after the whole plate had
    uploaded.

    Asserted at the boundary — what the run asked the remote to write and
    whether the remote could — rather than on the paths the runner computed,
    which were correct all along.
    """
    _record_a_previous_run(plate, _argv(plate))
    # A scheduler host: ``overlap`` — and with it the allocation, the heartbeat
    # and the sentinel — exists only where a ``compute_launcher`` does.
    fs, blob, runner = _spawn_with(
        host, plate, monkeypatch, completed_remote_wells={"A01"},
        launcher="srun -N1")

    assert "re-attaching" in blob, "the run under test did not re-attach"
    assert "FULL CACHE HIT" not in blob, (
        "this test needs the partial path, where the sentinel is written")
    assert fs.refused == [], f"the remote refused these writes: {fs.refused}"
    own = Path(runner._run.remote_pidfile).parent.as_posix()
    assert f"{own}/{UPLOAD_SENTINEL}" in fs.files
    assert f"{own}/{UPLOAD_HEARTBEAT}" in fs.files


# ── the caller, not the helper ──────────────────────────────────────────────

def _address_from_spawn(host, plate, monkeypatch, *, output_filter):
    """The remote output directory the real ``spawn`` computed."""
    fs, _blob, runner = _spawn_with(
        host, plate, monkeypatch, completed_remote_wells=set(),
        output_filter=output_filter)
    return runner._run.remote_output_dir, runner._run


@pytest.mark.parametrize("label,host_kw,filt", [
    ("cpu_only", {"cpu_only": True}, OutputFilter.FULL),
    ("extra_args", {"extra_args": ("--min_nucleus_area_px", "500")},
     OutputFilter.FULL),
    ("output_filter", {}, OutputFilter.CSV_ONLY),
])
def test_spawn_gives_a_differently_configured_run_its_own_address(
        host, plate, monkeypatch, label, host_kw, filt):
    """Not that the helpers can fold the extras in — that ``spawn`` passes them.

    ``test_reuse_is_keyed_completely`` exercises ``_host_argv_extras`` and
    ``pipeline_argv_digest(extras=…)`` directly, so dropping ``extras=`` from
    ``spawn``'s two call sites left every one of them green along with the
    other 699 tests that import this module. That is §7.6 #2: the convenient
    seam sat underneath the thing that is actually wired up.

    Each host-side addition, alone, must move the address the run lands on —
    otherwise a run configured this way finds the other configuration's output
    already complete and fetches it back as its own.
    """
    base, _ = _address_from_spawn(host, plate, monkeypatch,
                                  output_filter=OutputFilter.FULL)
    other, _ = _address_from_spawn(replace(host, **host_kw), plate, monkeypatch,
                                   output_filter=filt)
    assert base != other, (
        f"{label} left the remote address unchanged at {base}; a run "
        f"configured this way would re-use the other configuration's output")


def test_spawn_records_the_address_under_the_digest_it_used(
        host, plate, monkeypatch):
    """And the journal has to agree with it, or the next run cannot match.

    The address and the recorded key are computed from the same extras, so a
    run of the *same* configuration finds its own record and a differently
    configured one does not.
    """
    from services.remote_runner import pipeline_argv_digest, source_fingerprint

    cpu_host = replace(host, cpu_only=True)
    # `PipelineRunner` opens the journal before `spawn` runs; `spawn` only
    # amends it. Without that there is no record for `record_remote` to write
    # into, and this would measure nothing.
    J.open_run(plate / "out", spec={}, mode="remote", input_dir=plate / "in",
               argv=_argv(plate))
    _address_from_spawn(cpu_host, plate, monkeypatch,
                        output_filter=OutputFilter.FULL)

    runner = RemoteRunner(cpu_host)
    extras = runner._host_argv_extras(OutputFilter.FULL)
    stored = J.stored_remote_address(
        plate / "out",
        argv_digest=pipeline_argv_digest(_argv(plate), extras=extras),
        source_key=source_fingerprint(plate / "in"),
        host_alias=cpu_host.ssh_alias)
    assert stored is not None, (
        "the run recorded an address its own configuration cannot match")

    plain = RemoteRunner(host)
    assert J.stored_remote_address(
        plate / "out",
        argv_digest=pipeline_argv_digest(
            _argv(plate), extras=plain._host_argv_extras(OutputFilter.FULL)),
        source_key=source_fingerprint(plate / "in"),
        host_alias=host.ssh_alias) is None, (
        "a differently configured run matched the record")
