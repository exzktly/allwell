"""The Storage browser and the expiry sweep can delete nothing but a run or dataset folder.

``services.remote_storage`` runs ``rm -rf`` on a shared server. These tests build
a home directory full of decoys around the staging folder — the user's own files,
a folder somebody else put in ``runs/``, symbolic links named exactly like
All-Well folders but pointing out of the staging folder, links *inside* a run
folder pointing out, bookkeeping folders that are links — run every script the
module builds against it in real bash, and then compare every path outside the
legitimate targets with a snapshot taken before.

The second half disables the Python-side checks altogether and hands the
server hostile names directly, because the rule is that the server refuses on
its own: neither layer may be the only thing standing between a bug and a
deletion.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import remote_storage as rs
from services.remote_config import RemoteHost

DAY = 86400
_CLOCK = int(time.time())
GOOD_RUN = "run-00000000000000a1-00000000000000b2"
GOOD_KEY = "000000000000000000000000000000c3"
LINKED_RUN = "run-00000000000000d4-00000000000000e5"   # a link named like a run, pointing at the user's files
LINKED_KEY = "000000000000000000000000000000f6"
NESTED_RUN = "run-0000000000000107-0000000000000108"   # a real run whose out/ holds a link to the user's files


class _LocalCache:
    def __init__(self, staging: str, *, expiry_days: int = 0):
        self.host = RemoteHost(name="h", ssh_alias="h", remote_staging_dir=staging,
                               storage_expiry_days=expiry_days)

    def _ssh_script(self, script, *, check, timeout_s=None):
        return subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, timeout=60)


def _age(path: Path, days: float = 40) -> None:
    # One clock for the whole file: files aged a moment apart would otherwise differ
    # by microseconds, and ``find -newer`` reads that as a write after the marker.
    t = _CLOCK - days * DAY
    os.utime(path, (t, t), follow_symlinks=False)


def _snapshot(root: Path) -> dict[str, str]:
    """Every path under *root*, with what it is: a link's target, a folder's mode,
    a file's mode, mtime and hash."""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            st = p.lstat()
            if p.is_symlink():
                out[rel] = "link:" + os.readlink(p)
            elif p.is_dir():
                # Not a directory's mtime: removing a target legitimately touches its parent.
                out[rel] = f"dir:{oct(st.st_mode)}"
            else:
                # A file's mode and mtime too: a chmod or a write that restores the
                # same bytes is still a change to somebody else's file.
                out[rel] = (f"file:{oct(st.st_mode)}:{st.st_mtime_ns}:"
                            + hashlib.sha256(p.read_bytes()).hexdigest())
    return out


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """``home/`` with the user's own files and ``.allwell-staging`` full of decoys."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    precious = home / "precious"
    precious.mkdir(parents=True)
    (precious / "thesis.txt").write_text("four years")
    (precious / GOOD_KEY).write_text("a file named like a dataset key")     # bait for side-file removal
    (precious / f"{GOOD_KEY}.json").write_text("{}")
    (home / "notes.txt").write_text("keep me")

    st = home / ".allwell-staging"
    runs, datasets = st / "runs", st / "datasets"
    # The legitimate targets: an old, labelled run and dataset.
    good = runs / GOOD_RUN
    (good / "out").mkdir(parents=True)
    (good / "out" / "A1_out.zip").write_bytes(b"x" * 100)
    (good / rs.RUN_LABEL).write_text(json.dumps({"run_id": GOOD_RUN, "project": "P"}))
    (good / rs.RUN_FETCHED).write_text("1")
    ds = datasets / GOOD_KEY
    ds.mkdir(parents=True)
    (ds / "A1.zip").write_bytes(b"z")
    (datasets / rs.DATASET_LABEL_DIR).mkdir()
    (datasets / rs.DATASET_LABEL_DIR / f"{GOOD_KEY}.json").write_text(json.dumps({"cache_key": GOOD_KEY}))
    # A run whose output holds a link out of the staging folder.
    nested = runs / NESTED_RUN
    (nested / "out").mkdir(parents=True)
    (nested / rs.RUN_LABEL).write_text(json.dumps({"run_id": NESTED_RUN}))
    (nested / "out" / "escape").symlink_to(precious)
    # Somebody else's folder in runs/, labelled as itself to tempt the sweep.
    other = runs / "my-own-results"
    other.mkdir()
    (other / "data.csv").write_text("not All-Well's")
    (other / rs.RUN_LABEL).write_text(json.dumps({"run_id": "my-own-results"}))
    # Links named exactly like All-Well folders, pointing at the user's files.
    (runs / LINKED_RUN).symlink_to(precious)
    (datasets / LINKED_KEY).symlink_to(precious)
    # Bookkeeping folders that are links into the user's files.
    (datasets / rs.DATASET_EXPIRY_DIR).symlink_to(precious)
    (datasets / ".aw_last_used").symlink_to(precious)
    # Siblings of runs/ and datasets/ in the staging folder.
    (st / "holds" / "h").mkdir(parents=True)
    (st / "holds" / "h" / "job").write_text("4001")
    (st / "code").mkdir()
    (st / "code" / "process_microscopy.py").write_text("print()")
    for p in [*st.rglob("*"), st, *precious.iterdir()]:
        _age(p)
    return SimpleNamespace(root=tmp_path, home=home, staging=st, staging_text="~/.allwell-staging")


_BASE = "home/.allwell-staging"
#: What may legitimately change: the good run and dataset (and everything in
#: them), the nested run (the link inside it goes; its target does not), and the
#: good dataset's own label.
_TARGETS = (f"{_BASE}/runs/{GOOD_RUN}", f"{_BASE}/datasets/{GOOD_KEY}", f"{_BASE}/runs/{NESTED_RUN}")
#: The staging folder's clock probe is the one file the scripts may create outside a target.
_TARGET_FILES = {f"{_BASE}/datasets/{rs.DATASET_LABEL_DIR}/{GOOD_KEY}.json", f"{_BASE}/{rs.NOW_PROBE}"}


def _is_target(path: str) -> bool:
    return path in _TARGET_FILES or any(path == t or path.startswith(t + "/") for t in _TARGETS)


def _assert_only_targets_changed(sandbox, before: dict[str, str], *, targets_allowed: bool = True) -> None:
    """Nothing outside the targets changed, vanished **or appeared** — a file
    written through a link into the user's folders is as much a breach as one
    deleted there. With *targets_allowed* False not even the targets may change."""
    after = _snapshot(sandbox.root)
    touched = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    probe = f"{_BASE}/{rs.NOW_PROBE}"            # the clock probe every script touches
    breaches = {k for k in touched if k != probe and not (targets_allowed and _is_target(k))}
    assert breaches == set(), f"paths outside the run/dataset targets changed, vanished or appeared: {sorted(breaches)}"


def test_deleting_everything_listed_touches_nothing_else(sandbox):
    cache = _LocalCache(sandbox.staging_text)
    before = _snapshot(sandbox.root)
    listing, error = rs.list_storage(cache)
    assert error == ""
    names = {i.name for i in listing.items}
    # Links are never listed, so they can never be offered.
    assert LINKED_RUN not in names and LINKED_KEY not in names
    assert {GOOD_RUN, GOOD_KEY, NESTED_RUN, "my-own-results"} <= names
    # The browser refuses the folder All-Well did not make before any script is built.
    with pytest.raises(ValueError, match="did not make it"):
        rs.delete_items(cache, list(listing.items))
    outcomes, error = rs.delete_items(cache, [i for i in listing.items if i.ours])
    assert error == ""
    assert outcomes == {("run", GOOD_RUN): "gone", ("run", NESTED_RUN): "gone", ("dataset", GOOD_KEY): "gone"}
    _assert_only_targets_changed(sandbox, before)
    assert (sandbox.home / "precious" / "thesis.txt").read_text() == "four years"


def test_the_sweep_and_expiry_touch_nothing_else(sandbox):
    cache = _LocalCache(sandbox.staging_text, expiry_days=1)
    before = _snapshot(sandbox.root)
    listing = rs.list_storage(cache)[0]
    # Setting an expiry on every folder All-Well made: the dataset's lands in a
    # bookkeeping folder that is a link, so it is refused rather than written there.
    outcomes, _ = rs.set_expiry(cache, [i for i in listing.items if i.ours], rs.KEEP)
    assert outcomes[("dataset", GOOD_KEY)] == "fail"
    assert outcomes[("run", GOOD_RUN)] == "ok"
    rs.set_expiry(cache, [i for i in listing.items if i.ours], "")

    run = SimpleNamespace(run_id="run-0000000000000999-0000000000000999", cache_key="9" * 32,
                          remote_input_dir="", remote_output_dir="", local_input_dir=None,
                          local_output_dir=Path("/nowhere"))
    lines: list[str] = []
    rs.label_and_sweep(cache, run, lines.append, background=False)
    assert any(GOOD_RUN in line and "removed expired" in line for line in lines)
    assert not any("my-own-results" in line for line in lines)
    _assert_only_targets_changed(sandbox, before)


@pytest.mark.parametrize("staging", ["", "/", "~", "~/", "relative/path", "/a/../b", "/a/./b", "//",
                                     "~/a//b", "/tmp/x\nrm -rf ~", "/tmp/\x00x"])
def test_an_unsafe_staging_folder_is_refused_before_any_script_exists(staging):
    with pytest.raises(rs.UnsafeStagingDir):
        rs.validate_staging_dir(staging)
    item = rs.StorageItem("run", GOOD_RUN, 0, 0)
    for build in (lambda: rs.delete_script(staging, [item]),
                  lambda: rs.set_expiry_script(staging, [item], rs.KEEP),
                  lambda: rs.listing_script(staging, detail=True),
                  lambda: rs.label_script(staging, run_id=GOOD_RUN, dataset_key="", label={})):
        with pytest.raises(rs.UnsafeStagingDir):
            build()


def test_a_staging_folder_that_resolves_to_home_is_refused_by_the_server(sandbox):
    """``~/link-to-home`` passes the text check; the server sees where it really is."""
    (sandbox.home / "runs" / GOOD_RUN).mkdir(parents=True)      # a run-shaped folder right in $HOME
    (sandbox.home / "staging-link").symlink_to(sandbox.home)
    cache = _LocalCache("~/staging-link")
    before = _snapshot(sandbox.root)
    listing, error = rs.list_storage(cache)
    assert "refused" in error and listing.items == ()
    outcomes, error = rs.delete_items(cache, [rs.StorageItem("run", GOOD_RUN, 0, 0)])
    assert outcomes == {("run", GOOD_RUN): "refused"} and "home folder" in error
    _assert_only_targets_changed(sandbox, before, targets_allowed=False)


@pytest.mark.parametrize("name", ["../x", "..", ".", ".aw_labels", "a/b", "", "x\ny", "-rf", " run-1"])
def test_a_hostile_name_is_refused_in_python(name):
    with pytest.raises(ValueError):
        rs.delete_script("~/.allwell-staging", [rs.StorageItem("run", name, 0, 0)])


def test_a_hostile_kind_is_refused_in_python():
    with pytest.raises(ValueError):
        rs.delete_script("~/.allwell-staging", [rs.StorageItem("../../", GOOD_RUN, 0, 0)])


@pytest.mark.parametrize("name", ["../../precious", "../..", "..", ".", "", LINKED_RUN,
                                  f"{NESTED_RUN}/out/escape", "my-own-results"])
def test_the_server_refuses_on_its_own_when_python_lets_a_name_through(sandbox, monkeypatch, name):
    """The Python checks switched off: whatever reaches the server, it removes
    nothing but a real All-Well folder directly inside ``runs/``."""
    monkeypatch.setattr(rs, "_checked", lambda items, **_k: list(items))
    cache = _LocalCache(sandbox.staging_text)
    before = _snapshot(sandbox.root)
    for script in (rs.delete_script(sandbox.staging_text, [rs.StorageItem("run", name, 0, 0)]),
                   rs.set_expiry_script(sandbox.staging_text, [rs.StorageItem("run", name, 0, 0)], rs.KEEP)):
        result = cache._ssh_script(script, check=False)
        assert rs.status_of(result.stdout.split("\t")[0]) == "refused", result.stdout + result.stderr
    _assert_only_targets_changed(sandbox, before, targets_allowed=False)


def test_the_sweep_s_server_side_label_check_holds_even_if_python_skips_it(sandbox, monkeypatch):
    """An All-Well-named run with no label of its own: Python's sweep would skip it;
    here Python is bypassed and the server must still refuse the automatic removal."""
    unlabelled = sandbox.staging / "runs" / "run-0000000000000abc-0000000000000def"
    (unlabelled / "out").mkdir(parents=True)
    cache = _LocalCache(sandbox.staging_text)
    item = rs.StorageItem("run", unlabelled.name, 0, 0)
    outcomes, _ = rs.delete_items(cache, [item], automatic=True, host_days=1)
    assert outcomes == {("run", unlabelled.name): "refused:unlabelled"} and unlabelled.is_dir()
