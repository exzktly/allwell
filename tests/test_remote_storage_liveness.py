"""A folder something still needs is never deleted — every in-use rule of ``services.remote_storage``.

Each test reproduces one finding of the adversarial review of the Storage browser
(``_Docs/Markdowns/STORAGE_REVIEW_TRACKER.md``) in real bash, against a staging
tree in a temp directory, with stand-in ``squeue`` / ``stat`` / ``date`` / ``rm``
binaries put first on ``PATH`` where the finding is about what a server's tools
answer. Every in-use check must answer *in use* whenever it cannot tell.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat as stat_mod
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import remote_cache_gc as gc
from services import remote_storage as rs
from services.remote_config import RemoteHost, load_remotes

DAY = 86400
_CLOCK = int(time.time())
BASH = shutil.which("bash") or "/bin/bash"
RUN = "run-0000000000000aa1-0000000000000bb2"
OWN = "run-0000000000000cc3-0000000000000dd4"
KEY = "0000000000000000000000000000abcd"
OTHER_KEY = "0000000000000000000000000000ef01"


class _LocalCache:
    def __init__(self, staging: Path, *, expiry_days: int = 0):
        self.host = RemoteHost(name="h", ssh_alias="h", remote_staging_dir=str(staging),
                               storage_expiry_days=expiry_days)
        self.scripts: list[str] = []

    def _ssh_script(self, script, *, check, timeout_s=None):
        self.scripts.append(script)
        return subprocess.run([BASH, "-s"], input=script, capture_output=True, text=True, timeout=60)

    def _ssh_run(self, argv, *, check, timeout_s=None):
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)


def _age(path: Path, days: float = 40) -> None:
    # One clock for the whole file: files aged a moment apart would otherwise differ
    # by microseconds, and ``find -newer`` reads that as a write after the marker.
    t = _CLOCK - days * DAY
    os.utime(path, (t, t), follow_symlinks=False)


def _age_tree(root: Path, days: float = 40) -> None:
    for p in [*root.rglob("*"), root]:
        _age(p, days)


def _run(staging: Path, name: str = RUN, *, cache_key: str = KEY, fetched: bool = True, days: float = 40) -> Path:
    d = staging / "runs" / name
    (d / "out").mkdir(parents=True)
    (d / "out" / "A1_out.zip").write_bytes(b"x")
    (d / rs.RUN_LABEL).write_text(json.dumps({"run_id": name, "cache_key": cache_key, "project": "P"}))
    if fetched:
        (d / rs.RUN_FETCHED).write_text("1")
    _age_tree(d, days)
    return d


def _dataset(staging: Path, key: str = KEY, *, stamp_days: float = 40) -> Path:
    d = staging / "datasets" / key
    d.mkdir(parents=True)
    (d / "A1.zip").write_bytes(b"z")
    labels = staging / "datasets" / rs.DATASET_LABEL_DIR
    labels.mkdir(exist_ok=True)
    (labels / f"{key}.json").write_text(json.dumps({"cache_key": key}))
    stamps = staging / "datasets" / gc.IN_USE_STAMP_DIR
    stamps.mkdir(exist_ok=True)
    (stamps / key).touch()
    _age_tree(d, 40)
    _age(labels / f"{key}.json")
    _age(stamps / key, stamp_days)
    return d


def _beat(run_dir: Path) -> None:
    (run_dir / ".aw_beat").mkdir(exist_ok=True)
    (run_dir / ".aw_beat" / "alive").touch()


def _item(kind: str, name: str, **kw) -> rs.StorageItem:
    label = {("run_id" if kind == "run" else "cache_key"): name}
    return rs.StorageItem(kind, name, modified=1, last_used=1, label=label, fetched=kind == "run", **kw)


def _delete(cache, kind: str, name: str, **kw) -> str:
    outcomes, _ = rs.delete_items(cache, [_item(kind, name)], **kw)
    return outcomes[(kind, name)]


def _shim(bin_dir: Path, name: str, body: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    p = bin_dir / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(0o755)


@pytest.fixture
def staging(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    st = home / ".allwell-staging"
    (st / "runs").mkdir(parents=True)
    (st / "datasets").mkdir()
    return st


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    return d


# ── C1: a scheduler that cannot answer is not a job that has ended ──────────

def _with_sidecar(staging: Path) -> Path:
    d = _run(staging)
    (d / "run.pid.slurm").write_text("4242\n")
    _age(d / "run.pid.slurm")
    return d


@pytest.mark.parametrize("squeue, expected", [
    ("echo 'slurm_load_jobs error: Socket timed out on send/recv operation' >&2; exit 1", "busy"),
    ("echo RUNNING; exit 0", "busy"),
    ("echo PENDING; exit 0", "busy"),
    ("echo 'slurm_load_jobs error: Invalid job id specified' >&2; exit 1", "gone"),
    ("exit 0", "gone"),
    ("echo COMPLETED; exit 0", "gone"),
])
def test_a_run_whose_job_squeue_cannot_rule_out_is_in_use(staging, bin_dir, squeue, expected):
    d = _with_sidecar(staging)
    _shim(bin_dir, "squeue", squeue + "\n")
    assert rs.status_of(_delete(_LocalCache(staging), "run", RUN)) == expected
    assert d.exists() == (expected == "busy")


def test_a_run_with_a_job_and_no_squeue_on_the_path_is_in_use(staging, monkeypatch, tmp_path):
    _with_sidecar(staging)
    # A PATH holding only the tools the script needs, without squeue (as under
    # Lmod, where squeue arrives with a `module load` that `bash -s` never ran).
    tools = tmp_path / "tools"
    tools.mkdir()
    for tool in ("stat", "date", "find", "head", "tr", "grep", "rm", "mktemp", "mv", "dirname", "ps",
                 "printf", "cat", "du", "cut", "df", "awk", "kill", "sed"):
        found = shutil.which(tool)
        if found:
            (tools / tool).symlink_to(found)
    monkeypatch.setenv("PATH", str(tools))
    assert _delete(_LocalCache(staging), "run", RUN) == "busy"


def test_a_run_s_live_pid_counts_even_when_it_has_a_scheduler_sidecar(staging, bin_dir):
    d = _with_sidecar(staging)
    (d / "run.pid").write_text(f"{os.getpid()}\n")
    _age(d / "run.pid")
    _shim(bin_dir, "squeue", "echo 'Invalid job id specified' >&2; exit 1\n")
    assert _delete(_LocalCache(staging), "run", RUN) == "busy"


# ── C2: a dataset a running run reads ───────────────────────────────────────

@pytest.mark.parametrize("label, expected", [
    ({"run_id": OWN, "cache_key": KEY}, "busy"),          # the running run reads this dataset
    ({"run_id": OWN, "cache_key": OTHER_KEY}, "gone"),    # it reads another one
    (None, "busy"),                                        # no label: it could be reading any
])
def test_a_dataset_a_running_run_reads_is_in_use(staging, label, expected):
    _dataset(staging, KEY)
    running = staging / "runs" / OWN
    (running / "out").mkdir(parents=True)
    if label is not None:
        (running / rs.RUN_LABEL).write_text(json.dumps(label))
    _age_tree(running)
    _beat(running)
    cache = _LocalCache(staging)
    listed = {i.name: i for i in rs.list_storage(cache)[0].items}
    assert listed[KEY].active is (expected == "busy")
    assert _delete(cache, "dataset", KEY) == expected


# ── C3: a folder another run re-attached to ─────────────────────────────────

def test_a_folder_a_running_run_re_attached_to_is_in_use(staging):
    old = _run(staging, RUN)
    (old / rs.RUN_EXPIRY).write_text("1700000000\n")
    own = staging / "runs" / OWN
    own.mkdir()
    cache = _LocalCache(staging)
    label = rs.make_label(local_input_dir="/u/P/in", local_output_dir="/u/P/out", run_id=OWN, cache_key=KEY)
    subprocess.run(["bash", "-s"], input=rs.label_script(str(staging), run_id=OWN, dataset_key="",
                                                        label=label, borrowed_run=RUN),
                   check=True, capture_output=True, text=True)
    assert (old / rs.RUN_BORROWED_BY).read_text().split() == [OWN]
    assert not (old / rs.RUN_FETCHED).exists(), "re-attaching makes its results not downloaded yet"
    assert not (old / rs.RUN_EXPIRY).exists(), "a dated expiry does not survive reuse"
    _age_tree(old)
    _beat(own)                                        # the borrower's heartbeat lives in its own folder
    assert _delete(cache, "run", RUN) == "busy"
    shutil.rmtree(own / ".aw_beat")
    assert _delete(cache, "run", RUN) == "gone"       # once the borrower has stopped, it may go


# ── C4: a server whose stat is not GNU's ────────────────────────────────────

_BSD_STAT = (
    'if [ "$1" = -f ]; then python3 -c "import os,sys; s=os.stat(sys.argv[2]); '
    "print(int(s.st_mtime) if sys.argv[1]=='%m' else s.st_dev)\" \"$2\" \"${4:-$3}\"; exit $?; fi\n"
    "echo 'stat: illegal option' >&2; exit 1\n"
)


def test_a_bsd_stat_reads_real_times_and_nothing_fresh_is_due(staging, bin_dir):
    fresh = _run(staging, RUN, days=0.04)        # finished an hour ago
    _shim(bin_dir, "stat", _BSD_STAT)
    cache = _LocalCache(staging, expiry_days=30)
    item = {i.name: i for i in rs.list_storage(cache)[0].items}[RUN]
    assert time.time() - item.last_used < 2 * 3600
    assert rs.plan_expired([item], int(time.time()), 30) == []
    assert _delete(cache, "run", RUN, automatic=True, host_days=30) == "refused:not-due"
    assert fresh.exists()


def test_an_unreadable_time_is_never_epoch_zero(staging, bin_dir):
    _run(staging, RUN)
    # A stat that reports devices but no modification times, in either dialect.
    real_stat = shutil.which("stat")
    _shim(bin_dir, "stat", f'case "$2" in %Y|%m) exit 1;; esac; exec {real_stat} "$@"\n')
    cache = _LocalCache(staging, expiry_days=1)
    item = {i.name: i for i in rs.list_storage(cache)[0].items}[RUN]
    assert item.last_used == 0
    assert rs.plan_expired([item], int(time.time()), 1) == []
    assert _delete(cache, "run", RUN, automatic=True, host_days=1) == "refused:not-due"


# ── H1: results that were never downloaded ──────────────────────────────────

def test_a_run_whose_results_were_not_downloaded_is_never_removed_automatically(staging):
    d = _run(staging, RUN, fetched=False)
    cache = _LocalCache(staging, expiry_days=1)
    assert _delete(cache, "run", RUN, automatic=True, host_days=1) == "refused:not-downloaded"
    run = SimpleNamespace(run_id=RUN, remote_output_dir=f"{staging}/runs/{RUN}/out")
    rs.mark_fetched(cache, run, lambda _m: None)
    assert (d / rs.RUN_FETCHED).is_file()
    _age_tree(d)
    assert _delete(cache, "run", RUN, automatic=True, host_days=1) == "gone"


def test_starting_a_run_in_a_folder_clears_its_downloaded_marker(staging):
    d = _run(staging, RUN)
    subprocess.run(["bash", "-s"], input=rs.label_script(str(staging), run_id=RUN, dataset_key="", label={}),
                   check=True, capture_output=True, text=True)
    assert not (d / rs.RUN_FETCHED).exists()


# ── H2: nanowell wells waiting for re-anchoring ─────────────────────────────

def test_a_run_holding_wells_for_re_anchoring_is_in_use(staging):
    d = _run(staging, RUN)
    (d / "keep").mkdir()
    (d / "keep" / "A01.zip").write_bytes(b"w")
    _age_tree(d)
    assert _delete(_LocalCache(staging), "run", RUN) == "busy"


# ── H3: an expiry and a label outlive reuse ─────────────────────────────────

def test_reuse_clears_a_dated_expiry_and_keeps_keep(staging):
    run_dir, ds = _run(staging, RUN), _dataset(staging, KEY)
    del ds
    (run_dir / rs.RUN_EXPIRY).write_text("1700000000\n")
    expiry_dir = staging / "datasets" / rs.DATASET_EXPIRY_DIR
    expiry_dir.mkdir()
    (expiry_dir / KEY).write_text("keep\n")
    subprocess.run(["bash", "-s"], input=rs.label_script(str(staging), run_id=RUN, dataset_key=KEY, label={}),
                   check=True, capture_output=True, text=True)
    assert not (run_dir / rs.RUN_EXPIRY).exists()
    assert (expiry_dir / KEY).read_text().strip() == rs.KEEP


def test_the_cache_gc_honours_keep_and_takes_a_dataset_s_side_files_with_it(staging):
    _dataset(staging, KEY)
    _dataset(staging, OTHER_KEY)
    expiry_dir = staging / "datasets" / rs.DATASET_EXPIRY_DIR
    expiry_dir.mkdir()
    (expiry_dir / KEY).write_text("keep\n")
    (expiry_dir / OTHER_KEY).write_text("1700000000\n")
    lines: list[str] = []
    gc.collect_garbage(_LocalCache(staging), 0, lines.append)
    assert (staging / "datasets" / KEY).is_dir(), "a kept dataset is never evicted"
    assert not (staging / "datasets" / OTHER_KEY).exists()
    assert not (expiry_dir / OTHER_KEY).exists()
    assert not (staging / "datasets" / rs.DATASET_LABEL_DIR / f"{OTHER_KEY}.json").exists()


# ── M1: a mount point, and an rm that cannot stay on one filesystem ─────────

def test_an_rm_without_one_file_system_refuses_to_delete(staging, bin_dir):
    d = _run(staging, RUN)
    real_rm = shutil.which("rm")
    _shim(bin_dir, "rm", f'for a in "$@"; do [ "$a" = --one-file-system ] && exit 64; done; exec {real_rm} "$@"\n')
    assert _delete(_LocalCache(staging), "run", RUN) == "refused:rm-cannot-stay-on-one-filesystem"
    assert d.exists()


# ── M2: the staging filesystem's clock, not the login node's ────────────────

def test_now_is_the_staging_filesystem_s_clock_not_the_login_node_s(staging, bin_dir):
    """A login node an hour ahead must not age every heartbeat and write by an hour."""
    real_date = shutil.which("date")
    _shim(bin_dir, "date", f'if [ "$1" = +%s ]; then echo $(( $({real_date} +%s) + 3600 )); '
                           f'else exec {real_date} "$@"; fi\n')
    listing, _ = rs.list_storage(_LocalCache(staging))
    assert abs(listing.now - time.time()) < 120


def test_a_login_node_clock_ahead_of_the_filesystem_does_not_hide_a_heartbeat(staging, bin_dir):
    d = _run(staging, RUN)
    _beat(d)
    real_date = shutil.which("date")
    _shim(bin_dir, "date", f'if [ "$1" = +%s ]; then echo $(( $({real_date} +%s) + 3600 )); '
                           f'else exec {real_date} "$@"; fi\n')
    assert _delete(_LocalCache(staging), "run", RUN) == "busy"


# ── M3: another user's folders, and a host edited to point elsewhere ────────

@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to chown")
def test_another_user_s_folder_is_refused(staging):
    d = _run(staging, RUN)
    os.chown(d, 65534, 65534)
    cache = _LocalCache(staging)
    item = {i.name: i for i in rs.list_storage(cache)[0].items}[RUN]
    assert item.mine is False and not item.actionable
    with pytest.raises(ValueError, match="another user"):
        rs.delete_items(cache, [item])
    outcomes, _ = rs.delete_items(cache, [_item("run", RUN)])
    assert outcomes[("run", RUN)] == "refused:not-yours" and d.exists()


def test_a_live_pid_of_another_user_still_counts(staging):
    """``kill -0`` on another user's process fails with EPERM, which reads as dead;
    ``ps -p`` still sees it. Simulated by making ``kill`` refuse, as it does there."""
    d = _run(staging, RUN)
    (d / "run.pid").write_text(f"{os.getpid()}\n")
    _age(d / "run.pid")
    cache = _LocalCache(staging)
    real = cache._ssh_script
    cache._ssh_script = lambda script, **kw: real("kill() { return 1; }\n" + script, **kw)
    assert _delete(cache, "run", RUN) == "busy"


# ── M4: the server re-checks keep and the due date itself ───────────────────

# Named, not computed at collection: a parameter built from the clock gives
# each xdist worker a different test id, and the run refuses to start.
@pytest.mark.parametrize("expiry", ["keep", "ten-days-ahead"])
def test_an_automatic_delete_re_reads_the_expiry_on_the_server(staging, expiry):
    d = _run(staging, RUN)
    value = rs.KEEP if expiry == "keep" else str(int(time.time()) + 10 * DAY)
    (d / rs.RUN_EXPIRY).write_text(value + "\n")
    _age(d / rs.RUN_EXPIRY)
    assert _delete(_LocalCache(staging), "run", RUN, automatic=True, host_days=1) == "refused:not-due"
    assert d.exists()


# ── M5: the sweep never holds up the run ────────────────────────────────────

def test_the_run_start_sweep_removes_in_the_background(staging):
    _run(staging, RUN)
    release = threading.Event()
    cache = _LocalCache(staging, expiry_days=1)
    real = cache._ssh_script

    def slow(script, **kw):
        if "rm -rf --one-file-system -- ./" in script and "_aw_due" in script:
            release.wait(10)
        return real(script, **kw)

    cache._ssh_script = slow
    run = SimpleNamespace(run_id=OWN, cache_key=KEY, remote_input_dir="", remote_output_dir="",
                          local_input_dir=None, local_output_dir=Path("/elsewhere"))
    lines: list[str] = []
    started = time.monotonic()
    rs.label_and_sweep(cache, run, lines.append)
    assert time.monotonic() - started < 5, "the run waited for the sweep"
    assert (staging / "runs" / RUN).exists()
    release.set()
    for _ in range(100):
        if not (staging / "runs" / RUN).exists():
            break
        time.sleep(0.05)
    assert not (staging / "runs" / RUN).exists()


# ── L1, L7: writes never go through a hard link; labels are private ─────────

def test_an_expiry_write_does_not_go_through_a_hard_link(staging, tmp_path):
    d = _run(staging, RUN)
    (d / rs.RUN_EXPIRY).write_text("1700000000\n")
    backup = tmp_path / "backup-of-expiry"
    os.link(d / rs.RUN_EXPIRY, backup)
    rs.set_expiry(_LocalCache(staging), [_item("run", RUN)], rs.KEEP)
    assert (d / rs.RUN_EXPIRY).read_text().strip() == rs.KEEP
    assert backup.read_text().strip() == "1700000000"


def test_the_dataset_label_folder_is_private(staging):
    _dataset(staging, OTHER_KEY)
    shutil.rmtree(staging / "datasets" / rs.DATASET_LABEL_DIR)
    subprocess.run([BASH, "-s"], input=rs.label_script(str(staging), run_id="", dataset_key=OTHER_KEY,
                                                      label={"project": "P"}),
                   check=True, capture_output=True, text=True)
    assert stat_mod.S_IMODE((staging / "datasets" / rs.DATASET_LABEL_DIR).stat().st_mode) == 0o700


def test_a_label_is_readable_only_by_its_owner(staging):
    d = staging / "runs" / RUN
    d.mkdir()
    subprocess.run(["bash", "-s"], input=rs.label_script(str(staging), run_id=RUN, dataset_key="",
                                                        label={"project": "P"}),
                   check=True, capture_output=True, text=True)
    assert stat_mod.S_IMODE((d / rs.RUN_LABEL).stat().st_mode) == 0o600


# ── L3, L4, L5: hostile values do not raise or mislead ──────────────────────

@pytest.mark.parametrize("created", [1e400, float("nan"), -5, "x", True, 10**30])
def test_a_hostile_label_time_reads_as_unknown(created):
    assert rs.StorageItem("run", RUN, 0, 0, label={"created": created}).created is None


def test_an_infinite_expiry_in_remotes_toml_falls_back_to_never(tmp_path):
    cfg = tmp_path / "remotes.toml"
    cfg.write_text('[remote.h]\nssh_alias = "a"\nstorage_expiry_days = inf\n')
    assert load_remotes(cfg)["h"].storage_expiry_days == 0


@pytest.mark.parametrize("staging_dir", [" ~/.allwell-staging", "~/.allwell-staging ", "~/ spaced "])
def test_a_staging_folder_with_surrounding_spaces_is_refused_not_stripped(staging_dir):
    with pytest.raises(rs.UnsafeStagingDir, match="space"):
        rs.validate_staging_dir(staging_dir)


def test_the_earlier_attempt_clean_up_honours_keep(staging):
    """``remote_reuse.clean_earlier_attempts``'s per-folder script leaves a kept folder alone."""
    from services.remote_commands import HOLD_DIR
    from services.remote_reuse import _REMOVE_IF_IDLE
    from services.remote_run_id import LEGACY_UPLOAD_HEARTBEAT, UPLOAD_HEARTBEAT
    from services.remote_wait_snippets import UPLOAD_HEARTBEAT_STALE_S

    script = _REMOVE_IF_IDLE.format(heartbeat=UPLOAD_HEARTBEAT, legacy=LEGACY_UPLOAD_HEARTBEAT,
                                    stale=UPLOAD_HEARTBEAT_STALE_S, hold=HOLD_DIR)
    kept, plain = _run(staging, RUN), _run(staging, OWN)
    (kept / rs.RUN_EXPIRY).write_text("keep\n")
    out = subprocess.run([BASH, "-s"], input=script + f"_aw_clean {kept}\n_aw_clean {plain}\n",
                         capture_output=True, text=True, check=True).stdout
    assert f"AW_LIVE {kept} kept" in out and kept.is_dir()
    assert f"AW_GONE {plain}" in out and not plain.exists()


def test_runs_swapped_for_a_link_after_the_check_does_not_redirect_the_rm(staging, bin_dir, tmp_path):
    """The in-use check runs ``find`` between the confinement check and the ``rm``;
    a stand-in ``find`` swaps ``runs`` for a link into the user's files right then."""
    _run(staging, RUN)
    precious = tmp_path / "precious"
    (precious / RUN).mkdir(parents=True)
    (precious / RUN / "thesis.txt").write_text("four years")
    _age_tree(precious)                  # old, so the in-use check finds nothing recent there and goes on
    real_find = shutil.which("find")
    runs = staging / "runs"
    _shim(bin_dir, "find",
          f'if [ ! -L {runs} ]; then mv {runs} {runs}.real && ln -s {precious} {runs}; fi\n'
          f'exec {real_find} "$@"\n')
    outcome = _delete(_LocalCache(staging), "run", RUN)
    assert rs.status_of(outcome) == "fail", outcome
    assert (precious / RUN / "thesis.txt").read_text() == "four years"


def test_a_run_written_to_in_the_last_quarter_hour_is_in_use(staging):
    """No heartbeat, no pid, no job: a fresh write alone keeps it."""
    d = _run(staging, RUN)
    (d / "out" / "B2_partial.csv").write_text("x")
    assert _delete(_LocalCache(staging), "run", RUN) == "busy"
    (d / rs.RUN_EXPIRY).write_text("keep\n")          # this module's own files do not count
    _age(d / "out" / "B2_partial.csv")
    assert _delete(_LocalCache(staging), "run", RUN) == "gone"


# ── mounts, in a private mount namespace (no root needed, nothing left mounted) ──

def _unshare_ok() -> bool:
    if not shutil.which("unshare"):
        return False
    return subprocess.run(["unshare", "-rm", BASH, "-c", "true"], capture_output=True).returncode == 0


class _MountedCache(_LocalCache):
    """Runs each script in a fresh user + mount namespace in which *src* is
    bind-mounted on *dst*: the mount exists only for that script, as root or not."""

    def __init__(self, staging: Path, src: Path, dst: Path):
        super().__init__(staging)
        self._prefix = f"mount --bind {shlex.quote(str(src))} {shlex.quote(str(dst))} || exit 97\n"

    def _ssh_script(self, script, *, check, timeout_s=None):
        return subprocess.run(["unshare", "-rm", BASH, "-s"], input=self._prefix + script,
                              capture_output=True, text=True, timeout=60)


needs_unshare = pytest.mark.skipif(not _unshare_ok(), reason="unprivileged mount namespaces are not available")


@needs_unshare
def test_a_mount_point_in_runs_is_refused(staging, tmp_path):
    """A bind mount on the same filesystem keeps its device number; the mount table is what tells."""
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "thesis.txt").write_text("four years")
    _age_tree(precious)
    target = staging / "runs" / RUN
    target.mkdir()
    cache = _MountedCache(staging, precious, target)
    assert RUN not in {i.name for i in rs.list_storage(cache)[0].items}
    assert _delete(cache, "run", RUN) == "refused:outside"
    rs.set_expiry(cache, [_item("run", RUN)], rs.KEEP)
    assert sorted(p.name for p in precious.iterdir()) == ["thesis.txt"]
    assert (precious / "thesis.txt").read_text() == "four years"


@needs_unshare
def test_a_run_folder_with_a_mount_inside_it_is_never_deleted(staging, tmp_path):
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "thesis.txt").write_text("four years")
    _age_tree(precious)
    d = _run(staging, RUN)
    inner = d / "out" / "scratch"
    inner.mkdir()
    _age_tree(d)
    assert _delete(_MountedCache(staging, precious, inner), "run", RUN) == "refused:holds-a-mount"
    assert (precious / "thesis.txt").read_text() == "four years"


@needs_unshare
def test_the_cache_gc_will_not_empty_a_mounted_dataset(staging, tmp_path):
    """The GC evicts through the Storage module's deletion, so it gets the mount check too."""
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "thesis.txt").write_text("four years")
    _age_tree(precious)
    ds = _dataset(staging, KEY)
    lines: list[str] = []
    gc.collect_garbage(_MountedCache(staging, precious, ds), 0, lines.append)
    assert (precious / "thesis.txt").read_text() == "four years", lines


# ── round 2 of the review ───────────────────────────────────────────────────

def test_a_downloaded_marker_older_than_the_results_does_not_vouch_for_them(staging):
    """A run that re-used the folder without clearing the marker (an older build, a
    failed label call) left newer results; the marker no longer speaks for them."""
    d = _run(staging, RUN)
    newer = d / "out" / "B2_out.zip"
    newer.write_bytes(b"new")
    _age(newer, 30)                                     # written ten days after the marker
    _age(d / "out", 30)
    assert _delete(_LocalCache(staging), "run", RUN, automatic=True, host_days=1) == "refused:not-downloaded"
    _age(newer, 40)
    _age(d / "out", 40)
    (d / ".aw_beat").mkdir()
    (d / ".aw_beat" / "alive").touch()
    _age(d / ".aw_beat" / "alive", 30)                  # bookkeeping written later does not count
    assert _delete(_LocalCache(staging), "run", RUN, automatic=True, host_days=1) == "gone"


def test_a_find_that_fails_reads_as_in_use(staging, bin_dir):
    d = _run(staging, RUN)
    real_find = shutil.which("find")
    _shim(bin_dir, "find", 'for a in "$@"; do [ "$a" = -quit ] && { echo "find: unknown predicate" >&2; exit 1; }; done\n'
                           f'exec {real_find} "$@"\n')
    assert _delete(_LocalCache(staging), "run", RUN) == "busy"
    assert d.exists()


def test_the_cache_gc_parses_only_dataset_keys(staging):
    listing = "\n".join(["10\t5\tmy-own-folder", "10\t5\tvictim\tx", f"10\t5\t{KEY}\tjunk", f"10\t5\t{OTHER_KEY}\tkeep",
                          f"10\t6\t{KEY}", "now\t100"])
    datasets, now = gc.parse_listing(listing)
    assert [(d.key, d.last_used, d.kept) for d in datasets] == [(OTHER_KEY, 5, True), (KEY, 6, False)]


def test_the_cache_gc_does_not_follow_a_linked_label_folder(staging, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / f"{KEY}.json").write_text("not ours")
    _dataset(staging, KEY)
    shutil.rmtree(staging / "datasets" / rs.DATASET_LABEL_DIR)
    (staging / "datasets" / rs.DATASET_LABEL_DIR).symlink_to(elsewhere)
    gc.collect_garbage(_LocalCache(staging), 0, lambda _m: None)
    assert not (staging / "datasets" / KEY).exists()
    assert (elsewhere / f"{KEY}.json").read_text() == "not ours"


@pytest.mark.parametrize("content, expected", [("keep\n", True), ("1700000000\n", False), (None, False)])
def test_is_kept_reads_the_folder_s_expiry(staging, content, expected):
    d = _run(staging, RUN)
    if content is not None:
        (d / rs.RUN_EXPIRY).write_text(content)
    assert rs.is_kept(_LocalCache(staging), "run", RUN) is expected


def test_is_kept_answers_kept_when_it_cannot_ask():
    class _Down:
        host = RemoteHost(name="h", ssh_alias="h")

        def _ssh_script(self, script, *, check, timeout_s=None):
            return subprocess.CompletedProcess([], 255, "", "")

    assert rs.is_kept(_Down(), "run", RUN) is True


def test_an_interrupted_removal_is_finished_by_the_next_deletion(staging, bin_dir):
    d, other = _run(staging, RUN), _run(staging, OWN)
    real_rm = shutil.which("rm")
    _shim(bin_dir, "rm", f'for a in "$@"; do case "$a" in ./.aw_trash.*) exit 1;; esac; done; exec {real_rm} "$@"\n')
    assert _delete(_LocalCache(staging), "run", RUN) == "fail:partly-removed"
    trash = [p.name for p in (staging / "runs").iterdir() if p.name.startswith(".aw_trash.")]
    assert not d.exists() and len(trash) == 1, "renamed out of the way, so a new run starts afresh"
    assert RUN not in {i.name for i in rs.list_storage(_LocalCache(staging))[0].items}
    (bin_dir / "rm").unlink()
    assert _delete(_LocalCache(staging), "run", OWN) == "gone"
    assert not other.exists()
    assert [p.name for p in (staging / "runs").iterdir()] == [], "the leftover trash was finished"


def test_the_clock_probe_does_not_write_through_a_hard_link(staging, tmp_path):
    precious = tmp_path / "precious.txt"
    precious.write_text("fifteen bytes!!")
    os.link(precious, staging / rs.NOW_PROBE)
    rs.list_storage(_LocalCache(staging))
    assert precious.read_text() == "fifteen bytes!!"


def test_an_expiry_target_that_is_a_folder_fails_rather_than_pretending(staging):
    _dataset(staging, KEY)
    blocker = staging / "datasets" / rs.DATASET_EXPIRY_DIR / KEY
    blocker.mkdir(parents=True)
    outcomes, _ = rs.set_expiry(_LocalCache(staging), [_item("dataset", KEY)], rs.KEEP)
    assert outcomes[("dataset", KEY)] == "fail"
    assert list(blocker.iterdir()) == []


def test_a_staging_path_with_a_newline_is_refused_by_the_server(staging, tmp_path):
    odd = tmp_path / "home" / "n\nl"
    (odd / "runs").mkdir(parents=True)
    _run(odd, RUN)
    (tmp_path / "home" / "lnk").symlink_to(odd)
    cache = _LocalCache(staging)
    cache.host = RemoteHost(name="h", ssh_alias="h", remote_staging_dir="~/lnk")
    listing, error = rs.list_storage(cache)
    assert listing.items == () and "control character" in error
    outcomes, error = rs.delete_items(cache, [_item("run", RUN)])
    assert outcomes[("run", RUN)] == "refused" and (odd / "runs" / RUN).exists()


def test_a_squeue_warning_on_stderr_is_not_read_as_a_job_state(staging, bin_dir):
    _with_sidecar(staging)
    _shim(bin_dir, "squeue", "echo 'squeue: warning: deprecated option' >&2; echo COMPLETED; exit 0\n")
    assert _delete(_LocalCache(staging), "run", RUN) == "gone"


def test_an_absent_folder_is_reported_absent_not_removed(staging):
    assert _delete(_LocalCache(staging), "run", RUN) == "gone:absent"


def test_a_borrower_on_a_last_line_without_a_newline_still_counts(staging):
    old = _run(staging, RUN)
    (old / rs.RUN_BORROWED_BY).write_text(OWN)          # no trailing newline
    _age(old / rs.RUN_BORROWED_BY)
    own = staging / "runs" / OWN
    own.mkdir()
    _beat(own)
    assert _delete(_LocalCache(staging), "run", RUN) == "busy"


def test_the_server_keeps_a_dataset_used_within_the_in_use_window(staging):
    _dataset(staging, KEY, stamp_days=2)
    (staging / "datasets" / rs.DATASET_EXPIRY_DIR).mkdir()
    (staging / "datasets" / rs.DATASET_EXPIRY_DIR / KEY).write_text("1700000000\n")
    assert _delete(_LocalCache(staging), "dataset", KEY, automatic=True, host_days=1) == "refused:not-due"


def test_mark_fetched_marks_the_borrowed_folder_too(staging):
    old, own = _run(staging, RUN, fetched=False), staging / "runs" / OWN
    own.mkdir()
    run = SimpleNamespace(run_id=OWN, remote_output_dir=f"{staging}/runs/{RUN}/out")
    rs.mark_fetched(_LocalCache(staging), run, lambda _m: None)
    assert (own / rs.RUN_FETCHED).is_file() and (old / rs.RUN_FETCHED).is_file()


def test_the_sweep_protects_every_folder_labelled_with_the_plate_being_run(staging):
    item = rs.StorageItem("run", RUN, 1, 1, label={"run_id": RUN, "output_dir": "/u/P/out"}, fetched=True)
    run = SimpleNamespace(run_id=OWN, cache_key=KEY, remote_output_dir="", local_output_dir=Path("/u/P/out"))
    assert rs.plan_expired([item], 100 * DAY, 1) == [item]
    assert rs.plan_expired([item], 100 * DAY, 1, protect=rs.protect_for_run(run, str(staging))) == []


def test_one_sweep_removes_at_most_its_limit(staging):
    _run(staging, RUN)
    _run(staging, OWN)
    cache = _LocalCache(staging, expiry_days=1)
    listing = rs.list_storage(cache)[0]
    removed = rs.remove_expired(cache, listing, log=lambda _m: None, limit=1)
    assert len(removed) == 1
    assert len([p for p in (staging / "runs").iterdir()]) == 1


def test_sizes_and_free_space_are_read_where_du_and_df_lack_the_gnu_flags(staging, bin_dir):
    """macOS's du has no -b and its df no -B; the listing falls back to KiB rather than showing nothing."""
    _run(staging, RUN)
    real_du, real_df = shutil.which("du"), shutil.which("df")
    _shim(bin_dir, "du", f'case "$1" in *b*) echo "du: illegal option -- b" >&2; exit 1;; esac; exec {real_du} "$@"\n')
    _shim(bin_dir, "df", f'case "$1" in *B*) echo "df: illegal option -- B" >&2; exit 1;; esac; exec {real_df} "$@"\n')
    listing, _ = rs.list_storage(_LocalCache(staging))
    item = {i.name: i for i in listing.items}[RUN]
    assert item.size is not None and item.size % 1024 == 0 and item.size > 0
    assert listing.free_bytes is not None and listing.free_bytes % 1024 == 0

