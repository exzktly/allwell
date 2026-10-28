"""The plate-wide transfer label says which transfer it is, and goes when the upload is over.

Reported (tracker D5; incident 1): "Transferring… NN%" was fed by the uploader and the ``Fetcher`` through one
callback and cleared only by a compute event. After a batch's upload, the hand-off and the scheduler wait that
follow write nothing, so the label kept the upload's last tick — 79 % for an hour — and nothing said whether a
figure was the upload's or the download's.

Driven through the real ``TransferProgress`` handed to a real ``Fetcher`` (its direction is the Fetcher's own
choice of which callable to keep), the real ``ProgressTracker`` reading the uploader's own completion line, and a
real ``AnalyzeTab`` draining its queue; the assertion is on the status text the dashboard shows.
"""

from __future__ import annotations

import os
import queue

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

UPLOAD_DONE = "[remote] batch 1: upload complete (12.0 MB); handing it to the driver\n"


@pytest.fixture
def tab(qtbot):
    from analyze.tab import AnalyzeTab

    t = AnalyzeTab()
    qtbot.addWidget(t)
    t._alive = False
    t._dashboard.reset(remote=True, total=4)
    yield t


def _feed(tab, *events) -> str:
    for event in events:
        tab._log_q.put(event)
    tab._poll_log()
    return tab._dashboard._status.text()


def test_the_fetcher_reports_its_progress_as_a_download(stop_fetchers):
    from services.pipeline_runner import TransferProgress
    from services.remote_config import RemoteHost
    from services.remote_fetch import Fetcher
    from services.remote_runner import OutputFilter

    q: queue.Queue = queue.Queue()
    progress = TransferProgress(q.put)
    fetcher = Fetcher(RemoteHost(name="h", ssh_alias="h"), None, output_filter=OutputFilter.FULL,
                      progress_fn=progress)
    stop_fetchers(type("Runner", (), {"_fetcher": fetcher})())
    fetcher._progress(0.25)          # what Fetcher calls after a well lands
    progress(0.5)                     # what the uploader calls
    assert [q.get_nowait(), q.get_nowait()] == [("remote_xfer", ("down", 0.25)), ("remote_xfer", ("up", 0.5))]


def test_the_upload_label_names_the_upload_and_goes_when_it_is_over(tab):
    assert _feed(tab, ("remote_xfer", ("up", 0.79))) == "Uploading… 79%"
    assert _feed(tab, ("line", UPLOAD_DONE)) == "", "the upload's last tick must not outlive the upload"


def test_a_download_is_named_as_one_and_an_upload_ending_leaves_it(tab):
    assert _feed(tab, ("remote_xfer", ("down", 0.25))) == "Downloading… 25%"
    assert _feed(tab, ("line", UPLOAD_DONE)) == "Downloading… 25%"


@pytest.mark.parametrize("line", [
    "[remote] uploaded 812.4 MB in 3m02s\n",
    "[remote] batch 2/5: upload interrupted\n",
    "[remote] batch 2 upload failed (rsync rc=23); aborting remaining batches\n",
    "[remote] empty-well reference uploaded once (3.1 MB) → ~/r/bg\n",
])
def test_every_way_an_upload_ends_retracts_its_label(tab, line):
    _feed(tab, ("remote_xfer", ("up", 0.4)))
    assert _feed(tab, ("line", line)) == ""
