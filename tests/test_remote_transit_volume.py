"""A remote run must not write to the home volume.

Reported: a remote run stopped with "No space left on device … the staging is on
~/.allwell/scratch". The compute was happening on a cluster; locally the app was
only sending inputs and receiving results, and the machine's own input and output
volumes both had room. It filled the *home* volume anyway, because every
downloaded ``<well>_out.zip`` was written to the scratch base first and then
materialised into the output directory — so each byte of a plate's results
crossed two filesystems, and the one it crossed first was the one nothing in the
run had any reason to touch.

These assert **where the bytes land**, at the call the Fetcher actually makes.
A test that read the helper's return value would pass just as happily with the
Fetcher still writing somewhere else entirely.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from services.remote_config import RemoteHost
# Only names that exist on the base branch are imported here. The two this
# change introduces (`TRANSIT_SUBDIR`, `_transit_dir`) are imported inside the
# tests that need them, so that the one test carrying the defect can be run
# against pre-fix code and fail on its *assertion* rather than on an ImportError
# raised before it measured anything (CLAUDE.md §7.5).
from services.remote_runner import (
    LocalInputMode,
    OutputFilter,
)
from services.remote_scratch import _scratch_dir
from services.remote_scratch import sweep_stale_scratch
from services.remote_fetch import Fetcher
from services import remote_scratch
from services import remote_scratch

# Every test here drives real `RemoteRunner` / `Fetcher` code against a host
# that does not exist. Answer the ssh and rsync primitives directly rather than
# spawning them to find that out (tests/conftest.py::offline_transport).
pytestmark = pytest.mark.usefixtures("offline_transport")


@pytest.fixture
def host() -> RemoteHost:
    return RemoteHost(name="h", ssh_alias="h")


def _run(tmp_path: Path):
    from services.remote_run_state import _RemoteRun
    out = tmp_path / "out"
    out.mkdir()
    return _RemoteRun(
        run_id="x", cache_key="k", remote_input_dir="/r/in",
        remote_output_dir="/r/out", remote_pidfile="/r/pid",
        local_output_dir=out, local_input_mode=LocalInputMode.ZIP,
    )


def _fetcher(host, run, logs):
    with patch.object(Fetcher, "_loop", lambda self: None):
        return Fetcher(host, run, output_filter=OutputFilter.FULL,
                       log_fn=logs.append)


# ── where a downloaded well waits ────────────────────────────────────────────


def test_a_downloaded_well_waits_on_the_output_volume(host, tmp_path):
    """The defect itself, asserted at the path rsync is pointed at."""
    run = _run(tmp_path)
    logs: list[str] = []
    fetcher = _fetcher(host, run, logs)

    seen: list[str] = []

    def fake_rsync(self, argv):
        seen.append(argv[-1])          # the destination rsync was given
        return False                   # stop the fetch here; the path is the point

    with patch.object(Fetcher, "_rsync", fake_rsync), \
         patch.object(Fetcher, "_remote_file_size", lambda self, p: None):
        fetcher._fetch_one("A01")

    assert seen, "the fetch never reached rsync"
    dest = Path(seen[0])
    # Under the run's own output directory …
    assert run.local_output_dir in dest.parents, dest
    # … and explicitly not on the volume the user was told had filled.
    assert _scratch_dir() not in dest.parents, dest


def test_the_transit_directory_is_hidden_from_every_dataset_scan(host, tmp_path):
    """It lives inside the user's output folder, which Review reads back. Every
    scan in the app drops a name starting with a dot (`image_discovery`,
    `auto_threshold`, `zip_members`), so the name has to keep its dot."""
    from services.remote_scratch import TRANSIT_SUBDIR
    from services.remote_scratch import _transit_dir
    assert TRANSIT_SUBDIR.startswith(".")
    run = _run(tmp_path)
    got = _transit_dir(run.local_output_dir)
    assert got.name.startswith(".")
    assert got == run.local_output_dir / TRANSIT_SUBDIR


# ── the fallback says so ─────────────────────────────────────────────────────


def test_a_volume_that_refuses_falls_back_and_says_which(tmp_path):
    """Falling back silently would be the original failure with the explanation
    removed: the run would quietly return to the volume the user is trying to
    get off, and fill it again with nothing in the log to say why.

    A regular file stands in for the unwritable volume — `mkdir` under it raises
    the same `OSError` a read-only or quota'd output folder does, and unlike a
    chmod it behaves the same for a root-owned CI container.
    """
    remote_scratch._transit_refused.clear()

    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")
    logs: list[str] = []

    got = remote_scratch._transit_dir(blocked, log_fn=logs.append)
    assert got == _scratch_dir()
    first = "".join(logs)
    assert str(blocked) in first
    assert "falling back" in first

    # Once per volume, not once per well: a plate would otherwise repeat it for
    # every one of its wells.
    remote_scratch._transit_dir(blocked, log_fn=logs.append)
    assert "".join(logs).count("falling back") == first.count("falling back") == 1


# ── the leak moves with the directory, so the sweep has to follow ────────────


def test_the_stale_sweep_reaches_the_new_location(tmp_path):
    """A hard quit skips the `finally` that removes a transit dir. Sweeping only
    the home scratch would not have fixed that leak, only relocated it."""
    out = tmp_path / "out"
    out.mkdir()
    from services.remote_scratch import _transit_dir
    leaked = _transit_dir(out) / "allwell-stage-dead"
    leaked.mkdir()
    old = 0.0                                  # epoch: older than any cutoff
    os.utime(leaked, (old, old))

    assert sweep_stale_scratch(also=[out]) >= 1
    assert not leaked.exists()


def test_a_live_transit_dir_is_never_swept(tmp_path):
    """The age gate is what keeps a concurrent run's in-flight staging safe."""
    out = tmp_path / "out"
    out.mkdir()
    from services.remote_scratch import _transit_dir
    live = _transit_dir(out) / "allwell-stage-live"
    live.mkdir()

    sweep_stale_scratch(also=[out])
    assert live.exists()


def test_a_completed_fetch_leaves_nothing_behind(host, tmp_path):
    """The transit file is removed once the well is materialised — the whole
    point is that the volume carries one well at a time, not the plate."""
    run = _run(tmp_path)
    logs: list[str] = []
    fetcher = _fetcher(host, run, logs)
    well = "A01"
    from services.remote_scratch import _transit_dir
    tmp_zip = (_transit_dir(run.local_output_dir)
               / f"allwell-{run.run_id}-{well}_out.zip")
    with zipfile.ZipFile(tmp_zip, "w") as z:
        z.writestr("A01_overlay.tif", b"data")

    with patch.object(Fetcher, "_rsync", return_value=True), \
         patch.object(Fetcher, "_rsync_glob", return_value=(1, 1)), \
         patch.object(Fetcher, "_remote_file_size", lambda self, p: None), \
         patch.object(Fetcher, "_materialize", lambda self, w, z: True), \
         patch.object(Fetcher, "_delete_well_files_on_remote", lambda self, w: None):
        fetcher._fetch_one(well)

    assert not tmp_zip.exists(), "a well's transit copy outlived its download"
