"""A batched remote run over a link that fails: the *n*th ssh call refused, or one call that never answers.

The same end-to-end pair as ``tests/test_a_remote_run_on_a_standin_cluster.py`` (tracker P1) — the app's real
remote path against the stand-in cluster, the real pipeline on the far side — with the fault in the connection
rather than on the host. Each scenario is held to the rule the remote path owes a user
(``tests._standin_remote.assert_correct_or_visibly_failed``): the run ends either correct, every well here and
verified, or visibly failed with a reason; never a well reported complete over missing data, never a stall, never
a remote deletion of output this computer does not hold.
"""

from __future__ import annotations

import os

import pytest

from tests._standin_remote import (
    FIELD_WELLS,
    StandinCluster,
    assert_correct_or_visibly_failed,
    field_opts,
    field_plate,
    shorten_deadlines,
    standin_env_ok,
    start_run,
)

pytestmark = pytest.mark.skipif(not standin_env_ok(), reason="needs bash and rsync")

pytest.importorskip("numpy")
pytest.importorskip("tifffile")
pytest.importorskip("skimage")

#: How long one run may take before it is called a stall: under pytest's own 90 s, so the stall is what is reported.
RUN_LIMIT_S = 60


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    shorten_deadlines(monkeypatch)
    c = StandinCluster(tmp_path / "remote", monkeypatch)
    yield c
    c.reap()


#: The *n*th ssh call of the run exits 255, with ssh's own "Connection timed out". Which call the *n*th is depends a
#: little on thread timing (the Fetcher and the batch loop interleave), so these are points spread across a clean
#: run's ~75 calls: the code sync, the driver launch, batch 2's hand-off and the verification listing. Set
#: ``ALLWELL_HARNESS_SWEEP=1`` to try every one of them instead (about eight seconds each).
_NTH = tuple(range(1, 80)) if os.environ.get("ALLWELL_HARNESS_SWEEP") else (1, 12, 34, 71)


@pytest.mark.parametrize("n", _NTH)
def test_the_nth_ssh_call_failing_ends_correct_or_visibly_failed(cluster, tmp_path, stop_fetchers, n):
    inp, out = field_plate(tmp_path)
    cluster.ssh_rule("fail", "", skip=n - 1)

    run = start_run(cluster, inp, out, stop_fetchers, field_opts(), limit_s=RUN_LIMIT_S)

    assert cluster.fired(), "the fault never fired, so this measured a clean run"
    assert_correct_or_visibly_failed(run, cluster, FIELD_WELLS)


#: One ssh call that answers nothing. ``shorten_deadlines`` bounds a control command at 4 s and a script or a
#: listing at 6 s; the stand-in hangs for longer than the whole run may take, so a wait the app does not bound is
#: a stall rather than a delay.
_HANGS = {
    "a-hand-off": "touch && .aw_batch_2_ready",
    "a-download-listing": "*_A03.csv.gz",
    "the-verification-listing": "out/*_out.zip",
}


@pytest.mark.parametrize("pattern", list(_HANGS.values()), ids=list(_HANGS))
def test_one_hung_ssh_call_ends_correct_or_visibly_failed(cluster, tmp_path, stop_fetchers, monkeypatch, pattern):
    monkeypatch.setenv("AW_STANDIN_HANG_S", str(RUN_LIMIT_S + 30))
    inp, out = field_plate(tmp_path)
    cluster.ssh_rule("hang", pattern)

    run = start_run(cluster, inp, out, stop_fetchers, field_opts(), limit_s=RUN_LIMIT_S)

    assert cluster.fired(), "the fault never fired, so this measured a clean run"
    assert_correct_or_visibly_failed(run, cluster, FIELD_WELLS)
