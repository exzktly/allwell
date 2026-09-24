"""The plate and the log must agree about where a well's download is.

Reported: during the download step of a remote run the plate showed a well as a
filled green circle — fetched — while the run log went on printing
"still downloading…" for minutes.

Both were telling the truth about different things. The plate turns a well green
on the second of its two download lines (``N MB zip`` and ``N CSV file(s)``),
and the fetcher logged the CSV line *before* ``Fetcher._materialize`` put the
zip in the output folder. ``_materialize`` then copied every byte of the zip a
second time — onto the output volume, often a network share — although the
transit directory it came from sits beside that folder so that this step could
be a rename. The drain heartbeat, which cannot see any of that, said "still
downloading…" throughout.

So these assert three things, each at the boundary the user sees:

* the zip is **renamed** into place, never copied, when it can be;
* the line that paints a well fetched is not logged until the zip is in place,
  and is not logged at all when placing it fails;
* the drain heartbeat names the well and the step the worker is actually on.
"""

from __future__ import annotations

import errno
import threading
import types
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

import services.remote_runner as rr
from services.pipeline_runner import ProgressTracker
from services.remote_config import RemoteHost
from services.remote_runner import Fetcher, LocalInputMode, OutputFilter

pytestmark = pytest.mark.usefixtures("offline_transport")


def _run(tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    return rr._RemoteRun(
        run_id="x", cache_key="k", remote_input_dir="/r/in",
        remote_output_dir="/r/out", remote_pidfile="/r/pid",
        local_output_dir=out, local_input_mode=LocalInputMode.ZIP,
    )


def _write_zip(p: Path) -> None:
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("A01_overlay.tif", b"data" * 256)


def _bare_fetcher(dest: Path) -> Fetcher:
    f = object.__new__(Fetcher)
    f.output_filter = OutputFilter.FULL
    f.run = types.SimpleNamespace(local_output_dir=dest, incremental=False)
    f._log = lambda _m: None
    return f


# ── the zip is moved into place, not copied ─────────────────────────────────

def test_the_fetched_zip_is_renamed_into_place_not_copied(tmp_path, monkeypatch):
    dest = tmp_path / "out"
    dest.mkdir()
    transit = rr._transit_dir(dest)
    src = transit / "allwell-x-A01_out.zip"
    _write_zip(src)
    inode = src.stat().st_ino

    def no_copy(*_a, **_k):
        raise AssertionError("the zip was copied, not renamed")
    monkeypatch.setattr(rr.shutil, "copyfile", no_copy)

    assert _bare_fetcher(dest)._materialize("A01", src) is True
    final = dest / "A01_out.zip"
    assert final.stat().st_ino == inode, "the published zip is a different file"
    assert not src.exists()


def test_a_cross_device_rename_falls_back_to_a_staged_copy(tmp_path, monkeypatch):
    """The transit directory falls back to the scratch volume when the output
    folder refuses it; the rename then crosses devices and must still land."""
    dest = tmp_path / "out"
    dest.mkdir()
    src = tmp_path / "scratch.zip"
    _write_zip(src)
    real_replace = rr.os.replace

    def replace(a, b):
        if Path(a) == src:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(a, b)
    monkeypatch.setattr(rr.os, "replace", replace)

    assert _bare_fetcher(dest)._materialize("A01", src) is True
    assert zipfile.is_zipfile(dest / "A01_out.zip")
    assert not list(dest.glob(".*.tmp"))


# ── the plate is not told "fetched" before the zip is in place ──────────────

def _drive_fetch(tmp_path, *, materialize_ok: bool):
    """Run ``_fetch_one`` through the real code with the network answered, and
    return (log lines emitted before _materialize ran, all log lines)."""
    run = _run(tmp_path)
    logs: list[str] = []
    with patch.object(Fetcher, "_loop", lambda self: None):
        fetcher = Fetcher(RemoteHost(name="h", ssh_alias="h"), run,
                          output_filter=OutputFilter.FULL, log_fn=logs.append)

    def fake_rsync(self, argv):
        _write_zip(Path(argv[-1]))
        return True

    def fake_pull(self, host, files, dest_dir):
        (Path(dest_dir) / "res_A01.csv").write_text("a\n1\n")
        return 1

    before: list[list[str]] = []
    real_materialize = Fetcher._materialize

    def spy_materialize(self, well, zip_path):
        before.append(list(logs))
        return real_materialize(self, well, zip_path) if materialize_ok else False

    with patch.object(Fetcher, "_rsync", fake_rsync), \
         patch.object(Fetcher, "_remote_file_size", lambda self, p: 1024), \
         patch.object(Fetcher, "_list_remote_glob",
                      lambda self, src: ("h", ["/r/out/res_A01.csv"], 4)), \
         patch.object(Fetcher, "_pull_listed", fake_pull), \
         patch.object(Fetcher, "_materialize", spy_materialize), \
         patch.object(Fetcher, "_pull_settings_marker", lambda self, w: None):
        fetcher._fetch_one("A01")
    assert before, "the fetch never reached _materialize"
    return before[0], logs


def _download_events(lines):
    tracker = ProgressTracker()
    return [ev for line in lines for ev in tracker.parse(line)
            if ev == ("well_stage", ("A01", "download"))]


def test_a_well_is_not_painted_fetched_before_its_zip_is_in_place(tmp_path):
    before, logs = _drive_fetch(tmp_path, materialize_ok=True)
    assert not _download_events(before), (
        "the plate was told the well was fetched before its zip was placed:\n"
        + "".join(before))
    # …and once it is placed, the well does turn green.
    assert _download_events(logs), "".join(logs)
    assert (tmp_path / "out" / "A01_out.zip").is_file()


def test_a_well_whose_zip_could_not_be_placed_is_never_painted_fetched(tmp_path):
    _before, logs = _drive_fetch(tmp_path, materialize_ok=False)
    assert not _download_events(logs), "".join(logs)
    assert any("materialize failed (1 CSV file(s) did land)" in m for m in logs)


# ── the drain heartbeat names what the worker is doing ─────────────────────

def test_the_drain_heartbeat_names_the_well_and_the_step(tmp_path):
    """Drives the real sequence: the worker is inside ``_materialize`` and the
    run's end is waiting on it in ``flush_and_join``."""
    run = _run(tmp_path)
    run.well_count_total = 2
    logs: list[str] = []
    entered, release = threading.Event(), threading.Event()

    def slow_materialize(self, well, zip_path):
        entered.set()
        release.wait(10)
        return True

    def fake_rsync(self, argv):
        _write_zip(Path(argv[-1]))
        return True

    with patch.object(Fetcher, "_rsync", fake_rsync), \
         patch.object(Fetcher, "_remote_file_size", lambda self, p: None), \
         patch.object(Fetcher, "_list_remote_glob",
                      lambda self, src: ("h", [], 0)), \
         patch.object(Fetcher, "_pull_listed", lambda self, h, f, d: 0), \
         patch.object(Fetcher, "_materialize", slow_materialize), \
         patch.object(Fetcher, "_pull_settings_marker", lambda self, w: None):
        fetcher = Fetcher(RemoteHost(name="h", ssh_alias="h"), run,
                          output_filter=OutputFilter.FULL, log_fn=logs.append)
        fetcher.observe("Well B03  (1/2)  — done.\n")
        fetcher._pending.put("B03")
        assert entered.wait(10), "the worker never reached _materialize"
        joiner = threading.Thread(
            target=fetcher.flush_and_join, kwargs={"timeout": 0.05})
        joiner.start()
        # Wait for at least one heartbeat, then let the worker finish.
        for _ in range(200):
            if any("still working" in m for m in logs):
                break
            joiner.join(0.02)
        release.set()
        joiner.join(10)

    beats = [m for m in logs if "still working" in m]
    assert beats, "".join(logs)
    assert "B03: placing the output in" in beats[0], beats[0]
    assert "still downloading" not in "".join(logs)
    # The heartbeat is chatter, not a per-well outcome.
    assert not any(ev[0] == "well_stage"
                   for ev in ProgressTracker().parse(beats[0]))


# ── the per-well remote delete, moved to remote_transport ───────────────────

def test_the_per_well_remote_delete_pipes_its_script_to_bash(monkeypatch, tmp_path):
    """The Fetcher's per-well delete names the three remote paths and sends the
    script on stdin — as ``bash -c`` arguments ssh re-splits it and the ``rm``
    runs with no operands."""
    import services.remote_transport as rt

    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["input"] = argv, kw.get("input")
        return types.SimpleNamespace(returncode=0, stderr=b"")
    monkeypatch.setattr(rt.subprocess, "run", fake_run)

    f = object.__new__(Fetcher)
    f.host = RemoteHost(name="h", ssh_alias="h")
    f.run = _run(tmp_path)
    f._log = lambda _m: None
    f._delete_well_files_on_remote("A01")

    assert seen["argv"][-2:] == ["bash", "-s"]
    assert seen["input"] == (b"rm -f -- /r/in/A01.zip /r/out/A01_out.zip "
                             b"/r/out/*_A01.csv\n")
