"""A remote run, driven end to end against a host that really has a filesystem.

Three of the four critical defects this subsystem produced were filesystem
mistakes at the boundary — a directory that was never created, a directory
removed that should not have been, an address that named the wrong place — and
none of them was visible from a unit test of the function that decided them.
The decisions were correct; the calls around them were not.

``tests/_fake_remote.py`` runs the remote commands for real against a temporary
``$HOME``, so what these assert is what the host ended up holding.

This file is the safety net for taking ``services/remote_runner.py`` apart. It
has to pass before anything moves, and it is what says a move preserved
behaviour.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services import run_journal as J
from tests._fake_remote import FakeRemote


@pytest.fixture
def host():
    from services.remote_runner import RemoteHost
    return RemoteHost(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )


@pytest.fixture
def plate(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    for name in ("A01.zip", "B02.zip"):
        with zipfile.ZipFile(in_dir / name, "w") as zf:
            zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"\0" * 256)
    (tmp_path / "out").mkdir()
    return tmp_path


@pytest.fixture
def remote(tmp_path):
    return FakeRemote(tmp_path / "remote-host")


def _shell_home(text: str) -> str:
    """``~/x`` and ``"$HOME"/x`` name one path; compare them as one.

    ``_remote_quote`` renders a leading ``~/`` as ``"$HOME"/`` so the remote
    shell expands it, so an assertion written in the form the *runner* holds
    would never match the form the *command* carries.
    """
    return text.replace('"$HOME"/', "~/").replace("$HOME/", "~/")


def _argv(plate):
    return ["/usr/bin/python", "/local/process_microscopy.py",
            "--input_dir", str(plate / "in"),
            "--output_dir", str(plate / "out"),
            "--nuclear_token", "DAPI"]


def _spawn(host, plate, remote, monkeypatch, *, completed=(), **kw):
    """Drive the real ``spawn`` against the fake host; return the runner."""
    from services.remote_runner import (
        DatasetCache, LocalInputMode, OutputFilter, RemoteRunner, ZippedInput,
    )
    def _launched(_argv):
        m = MagicMock()
        m.stdout = MagicMock()
        m.stdout.__iter__ = lambda self: iter([])
        m.wait.return_value = 0
        m.returncode = 0
        m.pid = 4242
        return m

    remote.install(monkeypatch, host, on_launch=_launched)

    _KEY = "cachekey" + "0" * 24
    monkeypatch.setattr(DatasetCache, "cache_key", lambda self, z: _KEY)
    monkeypatch.setattr(DatasetCache, "ensure_uploaded",
                        lambda self, z, **k: (_KEY, 0, 0.01))
    monkeypatch.setattr(RemoteRunner, "sync_required_files",
                        lambda self, **k: None)
    monkeypatch.setattr(RemoteRunner, "_list_remote_completed_wells",
                        lambda self: set(completed))

    in_dir = plate / "in"
    zipped = ZippedInput(zip_dir=in_dir, mode=LocalInputMode.ZIP,
                         well_zips=(in_dir / "A01.zip", in_dir / "B02.zip"))
    runner = RemoteRunner(host)
    log: list[str] = []
    runner.spawn(zipped, plate / "out", pipeline_argv=_argv(plate),
                 output_filter=OutputFilter.FULL, log_fn=log.append, **kw)
    return runner, "".join(log)


# ── what a run leaves on the host ───────────────────────────────────────────

def test_a_run_creates_every_directory_its_control_files_need(
        host, plate, remote, monkeypatch):
    """The output directory *and* the run's own, which are not the same one.

    A re-attaching run's output lives under a previous ``run_id`` while its
    pidfile, sentinel and heartbeat stay under its own — so "the directory was
    created" has to be asked of each separately.
    """
    runner, _log = _spawn(host, plate, remote, monkeypatch)
    run = runner._run

    assert remote.exists(run.remote_output_dir), remote.tree()
    own = str(Path(run.remote_pidfile).parent)
    assert remote.exists(own), (
        f"the run's own directory {own} was never created; host holds "
        f"{remote.tree()}")


def test_the_launched_command_names_the_output_directory_it_created(
        host, plate, remote, monkeypatch):
    """The address the run computed and the address it launched against.

    These are derived in different places, which is the shape every re-attach
    defect has taken.
    """
    runner, _log = _spawn(host, plate, remote, monkeypatch)

    assert remote.launched, "no pipeline was launched"
    launched = " ".join(remote.launched[-1])
    assert runner._run.remote_output_dir in launched, launched


def test_cleanup_removes_the_run_dir_and_leaves_the_dataset_cache(
        host, plate, remote, monkeypatch):
    """Asserted on the host, not on the ``rm -rf`` argv.

    A cleanup that names the right path and never reaches the host, and one
    that reaches it and removes the wrong thing, are indistinguishable from
    the call site.
    """
    runner, _log = _spawn(host, plate, remote, monkeypatch,
                          completed=("A01", "B02"))
    run = runner._run
    own = str(Path(run.remote_pidfile).parent)
    assert remote.exists(own)

    monkeypatch.setattr(type(runner), "_verify_local_manifest",
                        lambda self, *, log: [])
    monkeypatch.setattr(type(runner), "_reconcile_fetch",
                        lambda self, *, log: "")
    monkeypatch.setattr(type(runner), "_rsync_optional",
                        lambda self, *a, **k: None)
    runner.verify_and_cleanup(True, cleanup_mode="run", log_fn=lambda _m: None)

    assert not remote.exists(own), (
        f"the run dir survived cleanup; host holds {remote.tree()}")


def test_a_dry_run_cleanup_removes_nothing(host, plate, remote, monkeypatch):
    """The mode whose whole purpose is telling the truth about what would go."""
    runner, _log = _spawn(host, plate, remote, monkeypatch,
                          completed=("A01", "B02"))
    own = str(Path(runner._run.remote_pidfile).parent)

    monkeypatch.setattr(type(runner), "_verify_local_manifest",
                        lambda self, *, log: [])
    monkeypatch.setattr(type(runner), "_reconcile_fetch",
                        lambda self, *, log: "")
    monkeypatch.setattr(type(runner), "_rsync_optional",
                        lambda self, *a, **k: None)
    runner.verify_and_cleanup(True, cleanup_mode="run", dry_run=True,
                              log_fn=lambda _m: None)

    assert remote.exists(own), "a dry run deleted the run dir"


def test_the_journal_records_the_address_the_run_actually_used(
        host, plate, remote, monkeypatch):
    """The record and the host must agree about where the work went."""
    J.open_run(plate / "out", spec={}, mode="remote", input_dir=plate / "in",
               argv=_argv(plate))
    runner, _log = _spawn(host, plate, remote, monkeypatch)

    journal = J.read_journal(plate / "out")
    assert journal is not None
    recorded = journal.remote.get("remote_output_dir")
    assert recorded == runner._run.remote_output_dir
    assert remote.exists(recorded), (
        f"the journal names {recorded}, which the host does not hold")


# ── the re-attaching run, where the two directories come apart ──────────────

STORED_OUT = "~/.allwell-staging/runs/run-previous/out"


def _record_a_previous_run(plate, remote):
    """A journal naming an address under a *different* run id, and a host that
    really holds output there — so the re-attach both fires and is verified."""
    from services.remote_runner import pipeline_argv_digest, source_fingerprint

    remote.path(STORED_OUT).mkdir(parents=True, exist_ok=True)
    for well in ("A01", "B02"):
        (remote.path(STORED_OUT) / f"{well}_out.zip").write_bytes(b"x")

    J.open_run(plate / "out", spec={}, mode="remote", input_dir=plate / "in",
               argv=_argv(plate))
    J.record_remote(
        plate / "out", run_id="run-previous", cache_key="k",
        host_alias="test-host",
        remote_input_dir="~/.allwell-staging/data/k",
        remote_output_dir=STORED_OUT,
        argv_digest=pipeline_argv_digest(_argv(plate), extras=[]),
        source_key=source_fingerprint(plate / "in"),
    )
    J.close_run(plate / "out", outcome="error")


def test_a_re_attaching_run_still_creates_its_own_directory(
        host, plate, remote, monkeypatch):
    """The one case where the two directories are not the same one.

    An ordinary run's ``mkdir -p …/runs/<id>/out`` makes ``runs/<id>`` on the
    way, so nothing distinguishes "created the output dir" from "created the
    run's own". A re-attaching run's output lives under a *previous* id, and
    then only an explicit ``mkdir`` puts its pidfile, sentinel and heartbeat
    anywhere — which is exactly the defect that shipped, and exactly what the
    non-re-attaching tests above cannot see.
    """
    _record_a_previous_run(plate, remote)
    runner, log = _spawn(host, plate, remote, monkeypatch,
                         completed=("A01", "B02"))

    assert "re-attaching" in log, "the run under test did not re-attach"
    run = runner._run
    assert run.remote_output_dir.endswith("run-previous/out"), run.remote_output_dir

    own = str(Path(run.remote_pidfile).parent)
    assert remote.exists(own), (
        f"the re-attached run's own directory {own} was never created; "
        f"host holds {remote.tree()}")
    assert remote.exists(run.remote_output_dir)


def test_a_re_attached_run_leaves_the_borrowed_directory_alone(
        host, plate, remote, monkeypatch):
    """Cleanup has to tell the two apart on the host, not in the argv."""
    _record_a_previous_run(plate, remote)
    runner, log = _spawn(host, plate, remote, monkeypatch,
                         completed=("A01", "B02"))
    assert "re-attaching" in log
    own = str(Path(runner._run.remote_pidfile).parent)

    monkeypatch.setattr(type(runner), "_verify_local_manifest",
                        lambda self, *, log: [])
    monkeypatch.setattr(type(runner), "_reconcile_fetch",
                        lambda self, *, log: "")
    monkeypatch.setattr(type(runner), "_rsync_optional",
                        lambda self, *a, **k: None)
    runner.verify_and_cleanup(True, cleanup_mode="run", log_fn=lambda _m: None)

    assert remote.exists(STORED_OUT), (
        "cleanup removed the directory this run borrowed from an earlier one")
    assert not remote.exists(own), "the run's own dir survived cleanup"


def test_a_failing_remote_command_aborts_the_run(host, plate, remote, monkeypatch):
    """``check=True`` has to behave like ``check=True``.

    The real ``_ssh_run`` hands ``check`` to ``subprocess.run``, so a failing
    ``mkdir`` aborts. A harness that swallowed it was more forgiving than
    production on the one call this file exists to guard: flipping that call to
    ``check=False`` — a silent continue with no directories, in production —
    passed every other test here.
    """
    from services.remote_runner import DatasetCache

    # A staging dir that cannot be created: a file sits where the directory
    # would go, which is what a stale artefact on a worker host looks like.
    (remote.root / "blocked").parent.mkdir(parents=True, exist_ok=True)
    (remote.root / "blocked").write_text("not a directory")
    blocked = host.__class__(**{**host.__dict__,
                               "remote_staging_dir": "~/blocked/staging"})

    remote.install(monkeypatch, blocked)
    cache = DatasetCache.__new__(DatasetCache)
    cache.host = blocked
    with pytest.raises(Exception):
        cache._ssh_run(["mkdir", "-p", "~/blocked/staging/x"], check=True)


def test_a_re_attaching_run_with_work_left_launches_against_the_borrowed_dir(
        host, plate, remote, monkeypatch):
    """The address the run journals and the address it launches against.

    Both re-attach tests above hand the host *every* well, which trips the
    full-cache-hit branch: the launched command becomes ``exec true`` and no
    pipeline argv is ever built, so neither can see what the pipeline was
    pointed at. One well short is what exercises it.

    Two critical slips pass every other test in this file and fail here: the
    pipeline being launched against the run's *own* output directory while the
    journal and cleanup name the borrowed one — so the run computes into a
    directory nothing fetches and cleanup then deletes it — and ``--input_dir``
    being handed the output directory.
    """
    _record_a_previous_run(plate, remote)
    runner, log = _spawn(host, plate, remote, monkeypatch, completed=("A01",))

    assert "re-attaching" in log, "the run under test did not re-attach"
    assert "FULL CACHE HIT" not in log, (
        "this test needs the partial path, where a pipeline argv is built")
    assert remote.launched, "no pipeline was launched"

    joined = _shell_home(" ".join(remote.launched[-1]))
    borrowed = runner._run.remote_output_dir
    assert borrowed.endswith("run-previous/out"), borrowed
    assert f"--output_dir {_shell_home(borrowed)}" in joined, (
        f"the pipeline was not pointed at the directory the run re-attached "
        f"to.\n  journalled: {borrowed}\n  launched:   {joined[:400]}")
    assert f"--input_dir {_shell_home(runner._run.remote_input_dir)}" in joined, (
        f"the pipeline reads its input from somewhere other than the uploaded "
        f"dataset.\n  launched: {joined[:400]}")


# ── the one modelled piece, exercised ───────────────────────────────────────

def test_the_transfer_model_honours_the_argv_shapes_the_runner_builds(
        tmp_path, remote, monkeypatch):
    """rsync is not installed here, so this piece is modelled — and untested
    modelling is how a harness reports correct code as broken.

    Each rule below is one the runner actually depends on, and each was wrong
    in the first version of this file.
    """
    import services.remote_runner as rr

    from services.remote_runner import RemoteHost
    host = RemoteHost(
        name="t", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0)
    # The real ``monkeypatch``, never a hand-rolled stand-in: ``install``
    # patches the *shared* ``subprocess`` module, so a shim without an undo
    # leaves ``Popen`` replaced for every later test in the session — which is
    # what it did, breaking the local-run harness three files away.
    remote.install(monkeypatch, host)
    rsync = rr._rsync_with_retry

    # 1. `-aR`, as `sync_required_files` uses: the source path is
    #    reproduced under the destination.
    (tmp_path / "_Docs").mkdir()
    (tmp_path / "_Docs" / "requirements-remote.txt").write_text("numpy\n")
    rsync(["rsync", "-aR", "_Docs/requirements-remote.txt",
           "test-host:~/allwell/"], cwd=str(tmp_path))
    assert remote.exists("~/allwell/_Docs/requirements-remote.txt"), (
        f"-R did not reproduce the source path; host holds {remote.tree()}")

    # 2. A *file* destination, as `_rsync_optional` and the sidecar pull
    #    use: it stays a file.
    remote.path("~/out").mkdir(parents=True, exist_ok=True)
    (remote.path("~/out") / "pipeline_info.json").write_text("{}")
    local = tmp_path / "local"
    local.mkdir()
    rsync(["rsync", "-az", "test-host:~/out/pipeline_info.json",
           str(local / "pipeline_info.json")])
    assert (local / "pipeline_info.json").is_file(), (
        "a file destination became a directory")

    # 3. A glob source, as `_retry_missing_wells` passes: it expands.
    for well in ("A01", "B02"):
        (remote.path("~/out") / f"{well}_out.zip").write_bytes(b"x")
    pulled = tmp_path / "pulled"
    pulled.mkdir()
    rsync(["rsync", "-az", "test-host:~/out/*_out.zip", str(pulled) + "/"])
    assert sorted(q.name for q in pulled.glob("*.zip")) == [
        "A01_out.zip", "B02_out.zip"], sorted(pulled.iterdir())

    # 4. A source that resolves to nothing exits 23, as real rsync does.
    #    Returning 0 here made every failure branch downstream unreachable —
    #    `Fetcher`'s per-well "could not pull", `_rsync_optional`'s
    #    "note: X not pulled", and the whole of `_retry_missing_wells` — so a
    #    regression that moved no bytes at all read as a clean run, which is
    #    the shape this harness exists to catch.
    missing = tmp_path / "missing"
    missing.mkdir()
    result = rsync(["rsync", "-az", "test-host:~/nowhere/A01_out.zip",
                    str(missing) + "/"])
    assert result.returncode == 23, (
        "a pull of a file the host does not have reported success; real rsync "
        f"exits 23. Got {result.returncode} and {sorted(missing.iterdir())}")
    assert not list(missing.iterdir()), "it also invented a file"

    # A glob matching nothing is the same failure, and is the shape
    # `_retry_missing_wells` actually builds.
    result = rsync(["rsync", "-az", "test-host:~/nowhere/*_out.zip",
                    str(missing) + "/"])
    assert result.returncode == 23, (
        f"a glob matching nothing reported success: {result.returncode}")


def test_the_fake_refuses_a_host_it_cannot_contain(tmp_path, remote):
    """Containment here is one mechanism, and it does not cover every path.

    `_bash` runs the script with `HOME` pointed at the fake root, so `~/x` and
    `$HOME/x` land inside it. An absolute path is covered by nothing: `path()`
    re-roots it for the assertion helpers, but the shell gets the script
    verbatim. A host with `/scratch/$USER` staging — the normal configuration
    on a cluster whose home is a small-quota NFS mount — would therefore run
    its `mkdir -p` and its `rm -rf` against the machine running this suite
    while `tree()` read the empty fake root and every assertion passed.

    Refusing is the honest answer while nothing rewrites paths inside `_bash`.
    """
    from services.remote_runner import RemoteHost
    host = RemoteHost(
        name="t", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="/scratch/someone/staging",
        remote_python="/opt/python", cpu_only=False, extra_args=(),
        cache_budget_gb=10, measured_bandwidth_mbps=100.0)
    with pytest.raises(AssertionError) as caught:
        remote.install(None, host)
    assert "remote_staging_dir" in str(caught.value), (
        f"it refused, but did not say which field: {caught.value}")


def test_an_unrelated_subprocess_is_not_mistaken_for_a_pipeline_launch(
        tmp_path, remote, monkeypatch):
    """`install` patches the *shared* `subprocess` module, so its `Popen` seam
    sees every process the test session starts, not only the runner's launches.

    Without a prefix match, an `rsync --version` capability probe or any other
    helper binary either aborted the test with a message about a pipeline
    launch it never made, or appended its argv to `launched` — which the
    end-to-end tests above assert on. Nothing in the suite reaches it today
    only because those call sites happen to be stubbed.
    """
    from services.remote_runner import RemoteHost
    host = RemoteHost(
        name="t", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0)
    remote.install(monkeypatch, host)  # no on_launch: a launch would raise

    out = subprocess.run(["/bin/echo", "hello"], capture_output=True, text=True)

    assert out.returncode == 0 and out.stdout.strip() == "hello", (
        "an unrelated binary did not run: the Popen seam swallowed it")
    assert remote.launched == [], (
        f"an unrelated binary was recorded as a pipeline launch: "
        f"{remote.launched}")
