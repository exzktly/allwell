"""The Upload rail counts wells that are on the remote, not rsync's chatter.

The dashboard counts a well as uploaded off a ``[remote] ↑ …<well>.zip`` line,
and those used to come only from rsync's own per-file output. rsync stays silent
in three ordinary cases — a cache hit where it moves nothing, an incremental
re-send where it skips matching files, and an rsync without ``--info=name1`` at
all (macOS still ships 2.6.9) — so a transfer that fully succeeded left the rail
reading 0/N, which looks like a stalled upload.

Batched mode hit it hardest: ``upload_subset`` had no compensator of any kind,
while ``ensure_uploaded`` at least synthesised lines on a cache hit. Both now
emit from the success path, where the caller knows which archives it asked for
and that rsync returned 0.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("PySide6")

from services.pipeline_runner import ProgressTracker
from services.remote_upload import _log_wells_uploaded
from widgets.run_dashboard import RunDashboard


def _lines(wells) -> list[str]:
    out: list[str] = []
    _log_wells_uploaded([f"/scratch/{w}.zip" for w in wells], out.append)
    return out


def test_one_line_per_well():
    assert len(_lines(["C02", "C03", "D04"])) == 3


def test_the_line_names_the_well_archive():
    (line,) = _lines(["C02"])
    assert "C02.zip" in line
    assert line.startswith("[remote] ↑ ")


def test_the_tracker_reads_them_as_uploads():
    """The contract that matters: the dashboard's own parser must match."""
    tracker = ProgressTracker()
    seen = []
    for line in _lines(["C02", "D04"]):
        for kind, payload in tracker.parse(line.rstrip("\n")):
            if kind == "well_stage" and payload[1] == "upload":
                seen.append(payload[0])
    assert seen == ["C02", "D04"]


def test_the_rail_reaches_n_of_n(qapp):
    """End to end: N wells announced → the Upload step reads N of N."""
    wells = ["A01", "A02", "B01", "B02"]
    d = RunDashboard()
    d.reset(remote=True, total=len(wells))
    d.set_total(len(wells))
    tracker = ProgressTracker()
    for line in _lines(wells):
        for kind, payload in tracker.parse(line.rstrip("\n")):
            if kind == "well_stage":
                d.set_well_stage(payload[0], payload[1])
    assert d._done.get("upload") == set(wells)


def test_repeated_announcements_do_not_double_count(qapp):
    """Safe alongside rsync's live per-file lines, which say the same thing."""
    d = RunDashboard()
    d.reset(remote=True, total=2)
    for _ in range(3):
        d.set_well_stage("A01", "upload")
    assert d._done.get("upload") == {"A01"}


@pytest.mark.parametrize("raw", [
    "[remote] ↑ (1) C02.zip",          # rsync --info=name1, fresh transfer
    "[remote] ↑ C02.zip already on remote",
    "[remote] ↑ C02.zip on remote",
])
def test_every_shape_of_upload_line_still_parses(raw):
    """The rail's regex has to keep matching the older phrasings too — a log
    from a run in flight during an upgrade will carry a mix."""
    tracker = ProgressTracker()
    wells = [p[0] for k, p in tracker.parse(raw)
             if k == "well_stage" and p[1] == "upload"]
    assert wells == ["C02"]


def test_the_helper_is_reached_from_both_upload_paths():
    """Wiring guard: the batched path is the one that had no compensator."""
    import inspect

    from services.remote_upload import DatasetCache

    for method in (DatasetCache.ensure_uploaded, DatasetCache.upload_subset):
        src = inspect.getsource(method)
        assert "_log_wells_uploaded" in src, method.__name__


def test_a_failed_batch_upload_announces_nothing():
    """rc != 0 means those wells are not on the remote — saying so would draw
    an Upload rail that is ahead of the data."""
    import inspect

    from services.remote_upload import DatasetCache

    src = inspect.getsource(DatasetCache.upload_subset)
    guard = re.search(r"if rc == 0 and log_fn:\s*\n\s*_log_wells_uploaded", src)
    assert guard is not None, src
