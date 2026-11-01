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
    t = time.time() - days * DAY
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
    _old_run(tmp_path, "run-a")
    ds = _old_dataset(tmp_path, "k1")
    label = rs.make_label(local_input_dir="/u/ProjX/in", local_output_dir="/u/ProjX/out",
                          run_id="run-a", cache_key="k1", now=1_700_000_000, client="laptop")
    subprocess.run(["bash", "-c", rs.label_script(str(tmp_path), run_id="run-a", dataset_key="k1",
                                                  label=label)], check=True)

    listing, error = rs.list_storage(cache)
    assert error == ""
    items = _by_name(listing)
    assert items["run-a"].project == "ProjX"
    assert items["run-a"].created == 1_700_000_000
    assert items["run-a"].output_dir == "/u/ProjX/out"
    assert items["k1"].project == "ProjX"
    assert items["run-a"].size and items["run-a"].size >= 1000
    # The pipeline lists the dataset folder as its input: it must hold the plate only.
    assert sorted(p.name for p in ds.iterdir()) == ["A1.zip"]
    assert (tmp_path / "datasets" / rs.DATASET_LABEL_DIR / "k1.json").is_file()


def test_a_label_is_written_once_so_it_records_the_first_use(tmp_path):
    _old_run(tmp_path, "run-a")
    for project, now in (("First", 1), ("Second", 2)):
        label = rs.make_label(local_input_dir=f"/u/{project}/in", local_output_dir="", now=now, client="c")
        subprocess.run(["bash", "-c", rs.label_script(str(tmp_path), run_id="run-a", dataset_key="",
                                                      label=label)], check=True)
    saved = json.loads((tmp_path / "runs" / "run-a" / rs.RUN_LABEL).read_text())
    assert (saved["project"], saved["created"]) == ("First", 1)


def test_labelling_or_setting_an_expiry_is_not_using_the_folder(tmp_path):
    """Both write into the run folder. Counted as use, they hid a 40-day-old run
    behind "in use" for a quarter hour and restarted its expiry clock."""
    cache = _LocalCache(tmp_path)
    _old_run(tmp_path, "run-a")
    label = rs.make_label(local_input_dir="/u/P/in", local_output_dir="/u/P/out", client="c")
    subprocess.run(["bash", "-c", rs.label_script(str(tmp_path), run_id="run-a", dataset_key="",
                                                  label=label)], check=True)
    item = _by_name(rs.list_storage(cache)[0])["run-a"]
    rs.set_expiry(cache, [item], rs.expiry_value(days=3, now=0))

    listing = rs.list_storage(cache)[0]
    item = _by_name(listing)["run-a"]
    assert item.active is False
    assert listing.now - item.last_used > 39 * DAY


def test_a_beating_run_is_in_use_and_survives_a_delete(tmp_path):
    cache = _LocalCache(tmp_path)
    run = _old_run(tmp_path, "run-live")
    (run / ".aw_beat").mkdir()
    (run / ".aw_beat" / "alive").touch()
    _old_run(tmp_path, "run-dead")

    items = _by_name(rs.list_storage(cache)[0])
    assert items["run-live"].active is True
    assert items["run-dead"].active is False

    outcomes, error = rs.delete_items(cache, list(items.values()))
    assert error == ""
    assert outcomes == {("run", "run-live"): "busy", ("run", "run-dead"): "gone"}
    assert run.is_dir()
    assert not (tmp_path / "runs" / "run-dead").exists()


def test_a_run_whose_pid_is_alive_is_in_use(tmp_path):
    cache = _LocalCache(tmp_path)
    for name, pid in (("run-alive", os.getpid()), ("run-gone", 3_999_999)):
        run = _old_run(tmp_path, name)
        (run / "run.pid").write_text(f"{pid}\n")
        _age(run / "run.pid", 40)
    items = _by_name(rs.list_storage(cache)[0])
    assert (items["run-alive"].active, items["run-gone"].active) == (True, False)


def test_deleting_a_dataset_takes_its_stamp_label_and_expiry_with_it(tmp_path):
    cache = _LocalCache(tmp_path)
    _old_dataset(tmp_path, "k1")
    side = [tmp_path / "datasets" / d / n for d, n in (
        (".aw_last_used", "k1"), (rs.DATASET_LABEL_DIR, "k1.json"), (rs.DATASET_EXPIRY_DIR, "k1"))]
    for p in side:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        _age(p, 40)
    item = _by_name(rs.list_storage(cache)[0])["k1"]
    assert rs.delete_items(cache, [item])[0] == {("dataset", "k1"): "gone"}
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
    _old_run(staging, "run-a")
    cache = _LocalCache(staging)
    cache.host = replace(cache.host, remote_staging_dir="~/.allwell-staging")
    listing, error = rs.list_storage(cache)
    assert error == "" and [i.name for i in listing.items] == ["run-a"]


# ── expiry ──────────────────────────────────────────────────────────────────

def _item(kind="run", name="r", last_used=0, expiry="", active=False, output_dir=""):
    return rs.StorageItem(kind, name, modified=last_used, last_used=last_used, active=active,
                          expiry=expiry, label={"output_dir": output_dir} if output_dir else {})


def test_what_expires_and_what_never_does():
    now = 100 * DAY
    items = [
        _item(name="untouched-31d", last_used=now - 31 * DAY),
        _item(name="untouched-29d", last_used=now - 29 * DAY),
        _item(name="kept", last_used=0, expiry=rs.KEEP),
        _item(name="explicit-past", last_used=now, expiry=str(now - 1)),
        _item(name="explicit-future", last_used=0, expiry=str(now + DAY)),
        _item(name="in-use", last_used=0, active=True),
        _item(kind="dataset", name="ds-old", last_used=now - 31 * DAY),
        _item(kind="dataset", name="ds-recent", last_used=now - IN_USE_WINDOW_S + DAY, expiry=str(now - 1)),
    ]
    due = {i.name for i in rs.plan_expired(items, now, host_days=30)}
    assert due == {"untouched-31d", "explicit-past", "ds-old"}
    assert {i.name for i in rs.plan_expired(items, now, host_days=0)} == {"explicit-past"}


def test_label_and_sweep_removes_expired_folders_but_not_the_run_starting(tmp_path):
    cache = _LocalCache(tmp_path, expiry_days=30)
    for name in ("run-mine", "run-earlier", "run-stranger"):
        _old_run(tmp_path, name)
    _old_dataset(tmp_path, "k-mine")
    _old_dataset(tmp_path, "k-stranger")
    # An earlier attempt of the same plate: a resume would re-attach to it.
    (tmp_path / "runs" / "run-earlier" / rs.RUN_LABEL).write_text(json.dumps({"output_dir": "/u/P/out"}))
    run = SimpleNamespace(run_id="run-mine", cache_key="k-mine",
                          remote_input_dir=f"{tmp_path}/datasets/k-mine",
                          remote_output_dir=f"{tmp_path}/runs/run-mine/out",
                          local_input_dir=Path("/u/P/in"), local_output_dir=Path("/u/P/out"))
    lines: list[str] = []

    rs.label_and_sweep(cache, run, lines.append)

    assert sorted(p.name for p in (tmp_path / "runs").iterdir()) == ["run-earlier", "run-mine"]
    assert sorted(p.name for p in (tmp_path / "datasets").iterdir() if not p.name.startswith(".")) == ["k-mine"]
    assert json.loads((tmp_path / "runs" / "run-mine" / rs.RUN_LABEL).read_text())["project"] == "P"
    assert any("removed expired run run-stranger" in line for line in lines)
    assert any("removed expired dataset k-stranger" in line for line in lines)
    # Nothing expired: one ssh call per run start, no more.
    cache.scripts.clear()
    rs.label_and_sweep(cache, run, lines.append)
    assert len(cache.scripts) == 1


def test_label_and_sweep_on_a_host_that_cannot_be_asked_says_so():
    class _Down:
        host = RemoteHost(name="h", ssh_alias="h", storage_expiry_days=1)

        def _ssh_script(self, script, *, check, timeout_s=None):
            return subprocess.CompletedProcess([], 255, "", "")

    run = SimpleNamespace(run_id="run-x", cache_key="k", remote_input_dir="", remote_output_dir="",
                          local_input_dir=None, local_output_dir=None)
    lines: list[str] = []
    rs.label_and_sweep(_Down(), run, lines.append)
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
