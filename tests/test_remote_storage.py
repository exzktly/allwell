"""What a remote host's staging directory holds, and removing what nobody needs.

``services.remote_storage`` builds bash that runs on the remote. These tests run
that bash for real, against a staging tree in a temp directory, through a fake
``DatasetCache`` whose ``_ssh_script`` pipes to a local ``bash -s`` — so what is
asserted is what the server would do, not what the Python thinks the script says.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import remote_storage as rs
from services.remote_cache_gc import IN_USE_WINDOW_S
from services.remote_config import RemoteHost, load_remotes, write_remote

DAY = 86400
_CLOCK = int(time.time())


def _run_name(tag: int) -> str:
    """A run folder name of the shape All-Well gives one: ``run-<16 hex>-<16 hex>``."""
    return f"run-{tag:016x}-{tag * 7:016x}"


def _key(tag: int) -> str:
    """A dataset key of the shape All-Well gives one: 32 hex characters."""
    return f"{tag:032x}"


RUN_A, RUN_LIVE, RUN_DEAD, RUN_ALIVE, RUN_GONE = (_run_name(i) for i in range(1, 6))
RUN_MINE, RUN_EARLIER, RUN_STRANGER = (_run_name(i) for i in range(11, 14))
KEY_1, KEY_MINE, KEY_STRANGER = (_key(i) for i in range(21, 24))


class _LocalCache:
    """A ``DatasetCache`` stand-in that runs each remote script in local bash."""

    def __init__(self, staging: Path, *, expiry_days: int = 0):
        self.host = RemoteHost(name="h", ssh_alias="h", remote_staging_dir=str(staging),
                               storage_expiry_days=expiry_days)
        self.scripts: list[str] = []

    def _ssh_script(self, script, *, check, timeout_s=None):
        self.scripts.append(script)
        return subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True, timeout=60)


def _age(path: Path, days: float) -> None:
    # One clock for the whole file: files aged a moment apart would otherwise differ
    # by microseconds, and ``find -newer`` reads that as a write after the marker.
    t = _CLOCK - days * DAY
    os.utime(path, (t, t))


def _old_run(staging: Path, name: str, days: float = 40) -> Path:
    d = staging / "runs" / name
    (d / "out").mkdir(parents=True)
    (d / "out" / "A1_out.zip").write_bytes(b"x" * 1000)
    for p in (d / "out" / "A1_out.zip", d / "out", d):
        _age(p, days)
    return d


def _old_dataset(staging: Path, key: str, days: float = 40) -> Path:
    d = staging / "datasets" / key
    d.mkdir(parents=True)
    (d / "A1.zip").write_bytes(b"z" * 10)
    for p in (d / "A1.zip", d):
        _age(p, days)
    return d


def _by_name(listing: rs.Listing) -> dict[str, rs.StorageItem]:
    return {i.name: i for i in listing.items}


# ── project name ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("given, project", [
    ("/Users/z/Screens/ProjX/in", "ProjX"),
    ("/Users/z/Screens/ProjX/IN", "ProjX"),
    ("/data/plate_07", "plate_07"),
    (None, ""),
])
def test_the_project_is_the_folder_holding_the_in_folder(given, project):
    assert rs.project_name(given) == project


# ── labels and the listing ──────────────────────────────────────────────────

def test_a_label_names_the_project_and_keeps_the_dataset_folder_clean(tmp_path):
    cache = _LocalCache(tmp_path)
    _old_run(tmp_path, RUN_A)
    ds = _old_dataset(tmp_path, KEY_1)
    label = rs.make_label(local_input_dir="/u/ProjX/in", local_output_dir="/u/ProjX/out",
                          run_id=RUN_A, cache_key=KEY_1, now=1_700_000_000, client="laptop")
    subprocess.run(["bash", "-c", rs.label_script(str(tmp_path), run_id=RUN_A, dataset_key=KEY_1,
                                                  label=label)], check=True)

    listing, error = rs.list_storage(cache)
    assert error == ""
    items = _by_name(listing)
    assert items[RUN_A].project == "ProjX"
    assert items[RUN_A].created == 1_700_000_000
    assert items[RUN_A].output_dir == "/u/ProjX/out"
    assert items[KEY_1].project == "ProjX"
    assert items[RUN_A].size and items[RUN_A].size >= 1000
    # The pipeline lists the dataset folder as its input: it must hold the plate only.
    assert sorted(p.name for p in ds.iterdir()) == ["A1.zip"]
    assert (tmp_path / "datasets" / rs.DATASET_LABEL_DIR / f"{KEY_1}.json").is_file()


def test_a_label_is_written_once_so_it_records_the_first_use(tmp_path):
    _old_run(tmp_path, RUN_A)
    for project, now in (("First", 1), ("Second", 2)):
        label = rs.make_label(local_input_dir=f"/u/{project}/in", local_output_dir="", now=now, client="c")
        subprocess.run(["bash", "-c", rs.label_script(str(tmp_path), run_id=RUN_A, dataset_key="",
                                                      label=label)], check=True)
    saved = json.loads((tmp_path / "runs" / RUN_A / rs.RUN_LABEL).read_text())
    assert (saved["project"], saved["created"]) == ("First", 1)


def test_labelling_or_setting_an_expiry_is_not_using_the_folder(tmp_path):
    """Both write into the run folder. Counted as use, they hid a 40-day-old run
    behind "in use" for a quarter hour and restarted its expiry clock."""
    cache = _LocalCache(tmp_path)
    _old_run(tmp_path, RUN_A)
    label = rs.make_label(local_input_dir="/u/P/in", local_output_dir="/u/P/out", client="c")
    subprocess.run(["bash", "-c", rs.label_script(str(tmp_path), run_id=RUN_A, dataset_key="",
                                                  label=label)], check=True)
    item = _by_name(rs.list_storage(cache)[0])[RUN_A]
    rs.set_expiry(cache, [item], rs.expiry_value(days=3, now=0))

    listing = rs.list_storage(cache)[0]
    item = _by_name(listing)[RUN_A]
    assert item.active is False
    assert listing.now - item.last_used > 39 * DAY


def test_a_beating_run_is_in_use_and_survives_a_delete(tmp_path):
    cache = _LocalCache(tmp_path)
    run = _old_run(tmp_path, RUN_LIVE)
    (run / ".aw_beat").mkdir()
    (run / ".aw_beat" / "alive").touch()
    _old_run(tmp_path, RUN_DEAD)

    items = _by_name(rs.list_storage(cache)[0])
    assert items[RUN_LIVE].active is True
    assert items[RUN_DEAD].active is False

    outcomes, error = rs.delete_items(cache, list(items.values()))
    assert error == ""
    assert outcomes == {("run", RUN_LIVE): "busy", ("run", RUN_DEAD): "gone"}
    assert run.is_dir()
    assert not (tmp_path / "runs" / RUN_DEAD).exists()


def test_a_run_whose_pid_is_alive_is_in_use(tmp_path):
    cache = _LocalCache(tmp_path)
    for name, pid in ((RUN_ALIVE, os.getpid()), (RUN_GONE, 3_999_999)):
        run = _old_run(tmp_path, name)
        (run / "run.pid").write_text(f"{pid}\n")
        _age(run / "run.pid", 40)
    items = _by_name(rs.list_storage(cache)[0])
    assert (items[RUN_ALIVE].active, items[RUN_GONE].active) == (True, False)


def test_deleting_a_dataset_takes_its_stamp_label_and_expiry_with_it(tmp_path):
    cache = _LocalCache(tmp_path)
    _old_dataset(tmp_path, KEY_1)
    side = [tmp_path / "datasets" / d / n for d, n in (
        (".aw_last_used", KEY_1), (rs.DATASET_LABEL_DIR, f"{KEY_1}.json"), (rs.DATASET_EXPIRY_DIR, KEY_1))]
    for p in side:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        _age(p, 40)
    item = _by_name(rs.list_storage(cache)[0])[KEY_1]
    assert rs.delete_items(cache, [item])[0] == {("dataset", KEY_1): "gone"}
    assert [p for p in side if p.exists()] == []


def test_a_listing_that_could_not_run_is_unknown_not_empty():
    class _Down:
        host = RemoteHost(name="h", ssh_alias="h")

        def _ssh_script(self, script, *, check, timeout_s=None):
            return subprocess.CompletedProcess([], 255, "", "ssh: connect to host h port 22: timed out")

    listing, error = rs.list_storage(_Down())
    assert listing is None and "timed out" in error
    assert rs.parse_listing("") is None


@pytest.mark.parametrize("name", ["../etc", ".aw_labels", "a/b", ""])
def test_a_name_that_could_reach_outside_its_folder_is_refused(name):
    with pytest.raises(ValueError):
        rs.delete_script("~/.allwell-staging", [rs.StorageItem("run", name, 0, 0)])


def test_the_scripts_expand_a_tilde_staging_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    staging = tmp_path / ".allwell-staging"
    _old_run(staging, RUN_A)
    cache = _LocalCache(staging)
    cache.host = replace(cache.host, remote_staging_dir="~/.allwell-staging")
    listing, error = rs.list_storage(cache)
    assert error == "" and [i.name for i in listing.items] == [RUN_A]


# ── expiry ──────────────────────────────────────────────────────────────────

_TAGS = iter(range(100, 10_000))


def _item(kind="run", last_used=0, expiry="", active=False, labelled=True, fetched=True):
    """A listed folder with an All-Well name, labelled as itself and (a run) its
    results downloaded, unless told otherwise."""
    name = _run_name(next(_TAGS)) if kind == "run" else _key(next(_TAGS))
    label = {("run_id" if kind == "run" else "cache_key"): name} if labelled else {}
    return rs.StorageItem(kind, name, modified=last_used, last_used=last_used, active=active,
                          expiry=expiry, label=label, fetched=fetched and kind == "run")


def test_what_expires_and_what_never_does():
    now = 100 * DAY
    cases = {
        "untouched-31d": _item(last_used=now - 31 * DAY),
        "untouched-29d": _item(last_used=now - 29 * DAY),
        "kept": _item(last_used=DAY, expiry=rs.KEEP),
        "explicit-past": _item(last_used=now, expiry=str(now - 1)),
        "explicit-future": _item(last_used=DAY, expiry=str(now + DAY)),
        "in-use": _item(last_used=DAY, active=True),
        "unlabelled-old": _item(last_used=DAY, labelled=False),
        "not-downloaded": _item(last_used=DAY, fetched=False),
        "last-use-unreadable": _item(last_used=0),
        "last-use-unreadable, dated": _item(last_used=0, expiry=str(now - 1)),
        "ds-last-use-unreadable, dated": _item(kind="dataset", last_used=0, expiry=str(now - 1)),
        "ds-old": _item(kind="dataset", last_used=now - 31 * DAY),
        "ds-recent": _item(kind="dataset", last_used=now - IN_USE_WINDOW_S + DAY, expiry=str(now - 1)),
    }
    names = {item.name: case for case, item in cases.items()}
    due = {names[i.name] for i in rs.plan_expired(cases.values(), now, host_days=30)}
    assert due == {"untouched-31d", "explicit-past", "ds-old"}
    assert {names[i.name] for i in rs.plan_expired(cases.values(), now, host_days=0)} == {"explicit-past"}


def test_a_folder_not_named_like_all_well_s_is_never_removed_automatically():
    """Labelled as itself, expired and idle — but a name All-Well never gives, and no marker file."""
    stranger = rs.StorageItem("run", "my-own-results", 0, 0, active=False, label={"run_id": "my-own-results"})
    assert not stranger.ours
    assert rs.plan_expired([stranger], 100 * DAY, host_days=1) == []


def test_label_and_sweep_removes_expired_folders_but_not_the_run_starting(tmp_path):
    cache = _LocalCache(tmp_path, expiry_days=30)
    for name in (RUN_MINE, RUN_EARLIER, RUN_STRANGER):
        _old_run(tmp_path, name)
    _old_dataset(tmp_path, KEY_MINE)
    _old_dataset(tmp_path, KEY_STRANGER)
    # Folders other runs labelled; only a folder labelled as itself is removed automatically.
    for name in (RUN_STRANGER,):
        (tmp_path / "runs" / name / rs.RUN_LABEL).write_text(json.dumps({"run_id": name}))
        (tmp_path / "runs" / name / rs.RUN_FETCHED).write_text("1")
    labels = tmp_path / "datasets" / rs.DATASET_LABEL_DIR
    labels.mkdir()
    (labels / f"{KEY_STRANGER}.json").write_text(json.dumps({"cache_key": KEY_STRANGER}))
    # An earlier attempt of the same plate: a resume would re-attach to it.
    (tmp_path / "runs" / RUN_EARLIER / rs.RUN_LABEL).write_text(
        json.dumps({"run_id": RUN_EARLIER, "output_dir": "/u/P/out"}))
    run = SimpleNamespace(run_id=RUN_MINE, cache_key=KEY_MINE,
                          remote_input_dir=f"{tmp_path}/datasets/{KEY_MINE}",
                          remote_output_dir=f"{tmp_path}/runs/{RUN_MINE}/out",
                          local_input_dir=Path("/u/P/in"), local_output_dir=Path("/u/P/out"))
    lines: list[str] = []

    rs.label_and_sweep(cache, run, lines.append, background=False)

    assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == sorted([RUN_EARLIER, RUN_MINE])
    assert sorted(p.name for p in (tmp_path / "datasets").iterdir() if not p.name.startswith(".")) == [KEY_MINE]
    assert json.loads((tmp_path / "runs" / RUN_MINE / rs.RUN_LABEL).read_text())["project"] == "P"
    assert any(f"removed expired run {RUN_STRANGER}" in line for line in lines)
    assert any(f"removed expired dataset {KEY_STRANGER}" in line for line in lines)
    # Nothing expired: one ssh call per run start, no more.
    cache.scripts.clear()
    rs.label_and_sweep(cache, run, lines.append, background=False)
    assert len(cache.scripts) == 1


def test_label_and_sweep_on_a_host_that_cannot_be_asked_says_so():
    class _Down:
        host = RemoteHost(name="h", ssh_alias="h", storage_expiry_days=1)

        def _ssh_script(self, script, *, check, timeout_s=None):
            return subprocess.CompletedProcess([], 255, "", "")

    run = SimpleNamespace(run_id=RUN_A, cache_key=KEY_1, remote_input_dir="", remote_output_dir="",
                          local_input_dir=None, local_output_dir=None)
    lines: list[str] = []
    rs.label_and_sweep(_Down(), run, lines.append, background=False)
    assert len(lines) == 1 and "nothing expired was removed" in lines[0]


# ── the host setting ────────────────────────────────────────────────────────

def test_the_host_expiry_round_trips_and_is_clamped(tmp_path):
    cfg = tmp_path / "remotes.toml"
    write_remote(RemoteHost(name="h", ssh_alias="a", storage_expiry_days=21), cfg)
    assert load_remotes(cfg)["h"].storage_expiry_days == 21
    write_remote(RemoteHost(name="z", ssh_alias="a"), cfg)
    assert "storage_expiry_days" not in cfg.read_text().split("[remote.z]")[1]
    cfg.write_text(cfg.read_text().replace("storage_expiry_days = 21", "storage_expiry_days = -5"))
    assert load_remotes(cfg)["h"].storage_expiry_days == 0
