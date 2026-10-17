"""One well's merge does not hold the next well's download — or anyone's remote copy but its own.

The report: the first well of an incremental remote run sat at 99% for thirty minutes while every later download
waited behind it and the remote filled. The Fetcher did one well at a time, end to end — download, merge into the
local output, delete the remote copy — so a slow merge stopped the transfers. The merge and everything after it
now run on the Placer's thread (``services/remote_fetch_place``).

Driven through the Fetcher's real worker loop, with a merge that blocks until the test lets it go.
"""

from __future__ import annotations

import threading
import zipfile

from services import remote_fetch
from services.remote_config import RemoteHost
from services.remote_fetch import Fetcher
from services.remote_fetch_place import MAX_PENDING, Placer
from services.remote_run_state import _RemoteRun
from services.remote_runner import LocalInputMode, OutputFilter


def _host():
    return RemoteHost(name="t", ssh_alias="test-host", remote_project_dir="~/allwell",
                      remote_staging_dir="~/.s", remote_python="/opt/python", cpu_only=False, extra_args=(),
                      cache_budget_gb=10, measured_bandwidth_mbps=100.0)


def test_the_second_well_downloads_while_the_first_is_still_merging(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    run = _RemoteRun(run_id="r", cache_key="k", remote_input_dir="/r/in", remote_output_dir="/r/out",
                     remote_pidfile="/r/pid", local_output_dir=out, local_input_mode=LocalInputMode.ZIP,
                     incremental=True)
    events: list[str] = []
    release_merge = threading.Event()
    second_download = threading.Event()

    def fake_rsync(self, argv):
        well = argv[-1].rsplit("-", 1)[-1].split("_")[0]
        events.append(f"download {well}")
        if well == "A02":
            second_download.set()
        with zipfile.ZipFile(argv[-1], "w") as zf:
            zf.writestr(f"{well}_f1_t0_gfp.tif", b"x")
        return True

    def fake_pull(self, host, files, dest_dir):
        well = files[0].rsplit("_", 1)[-1].split(".")[0]
        (dest_dir / f"plate_{well}.csv").write_text(f"well,fov,timepoint,nucleus_id\n{well},1,0,1\n")
        return 1

    def slow_merge(self, tmp_csv_dir):
        well = tmp_csv_dir.name.split("-")[2]
        events.append(f"merge {well} started")
        if well == "A01":
            assert release_merge.wait(10), "the test never released the merge"
        events.append(f"merge {well} done")

    monkeypatch.setattr(Fetcher, "_rsync", fake_rsync)
    monkeypatch.setattr(Fetcher, "_pull_listed", fake_pull)
    monkeypatch.setattr(Fetcher, "_merge_fetched_csvs", slow_merge)
    monkeypatch.setattr(Fetcher, "_list_remote_glob",
                        lambda self, src: ("test-host", [f"/r/out/plate_{src.split('_')[-1].split('.')[0]}.csv"], 10))
    monkeypatch.setattr(Fetcher, "_remote_file_size", lambda self, p: None)
    monkeypatch.setattr(Fetcher, "_already_local", lambda self, w: None)
    monkeypatch.setattr(Fetcher, "_pull_settings_marker", lambda self, w: None)
    monkeypatch.setattr(Fetcher, "_delete_well_files_on_remote", lambda self, w: events.append(f"delete {w}"))
    monkeypatch.setattr(remote_fetch, "compress_remote_csvs", lambda *a, **k: None)

    fetcher = Fetcher(_host(), run, output_filter=OutputFilter.FULL, log_fn=lambda _m: None,
                      delete_after_download=True)
    try:
        fetcher.observe("Well A01 — done.\n")
        fetcher.observe("Well A02 — done.\n")
        assert second_download.wait(10), (
            f"A02 never downloaded while A01's merge was running: {events}")
        assert "delete A01" not in events, "A01's remote copy went before its merge finished"
        release_merge.set()
        fetcher.flush_and_join(timeout=10)
    finally:
        fetcher.cancel()

    assert events.index("download A02") < events.index("merge A01 done")
    assert events.index("merge A01 done") < events.index("delete A01")
    assert events.index("merge A02 done") < events.index("delete A02")
    assert run.fetched_wells == {"A01", "A02"}


def test_the_queue_of_wells_waiting_to_merge_is_bounded():
    """Each waiting well holds its download in the local transit directory; past the bound, transfers wait."""
    placer = Placer(lambda _m: None)
    gate = threading.Event()
    placer.submit(lambda: gate.wait(10))              # taken by the thread at once, then blocks
    for _ in range(MAX_PENDING):
        placer.submit(lambda: None)
    extra = threading.Thread(target=placer.submit, args=(lambda: None,), daemon=True)
    extra.start()
    extra.join(0.5)
    assert extra.is_alive(), "a submit past the bound did not wait"
    gate.set()
    extra.join(5)
    assert not extra.is_alive()
    placer.drain(timeout=5)
