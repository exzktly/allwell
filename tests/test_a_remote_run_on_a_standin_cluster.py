"""A remote run end to end: the app's real remote path and the real remote side, as a pair, with faults on cue.

Every other remote test drives one end. The ones that run the remote commands for real (``tests/_fake_remote.py``,
``tests/_standin_remote.py``'s ``StandinHost``) still stop at the launch — a ``MagicMock`` stands in for the
pipeline, so the batch driver, the ``srun`` wrapper, the conda guard and the pipeline itself never ran against the
code that feeds them. That seam is where incident 1 and A4 lived, and three of a month's fix-of-fix chains
(tracker P1).

Here nothing on the path is a double. ``PipelineRunner.start`` stages the plate, ``RemoteRunner.spawn`` syncs the
code, the batched process uploads each batch with the real ``rsync`` and hands it over with a real ``ssh touch``;
on the far side the packed login-shell command unpacks, the ``srun`` wrapper asks the stand-in scheduler for a
node, the conda guard checks the env, the batch driver swaps each batch in, and the real ``process_microscopy.py``
— the copy the run synced, not this checkout's — segments the wells (``classical_threshold``, which needs no
model). The Fetcher pulls each finished well back as the driver announces it, verification reads the remote, and
Auto-delete removes the run's directory. Only the binaries are stand-ins (``StandinCluster``): ``ssh`` runs the
command in a local ``bash`` with a temporary ``HOME``, and ``srun`` / ``squeue`` / ``scancel`` / ``conda`` model
their own interfaces.

Each fault scenario is held to the one rule the remote path owes a user, whatever went wrong
(``assert_correct_or_visibly_failed``): the run ends **either correct** — every well's CSV and archive here,
verified — **or visibly failed**, with a reason in its headline. Never a well reported complete over missing data,
never a silent stall, and never a remote deletion of output this computer does not hold. The faults of the link
itself — the *n*th ssh failing, one hanging — are ``tests/test_a_remote_run_over_a_failing_link.py``; the nanowell
path through the final re-anchoring pass is ``tests/test_a_remote_nanowell_run_on_a_standin_cluster.py``.
"""

from __future__ import annotations

import json
import re

import pytest

from tests._standin_remote import (
    FIELD_WELLS,
    StandinCluster,
    assert_correct_or_visibly_failed,
    complete_here,
    field_opts,
    field_plate,
    journal_states,
    remote_output_wells,
    shorten_deadlines,
    standin_env_ok,
    start_run,
)

pytestmark = pytest.mark.skipif(not standin_env_ok(), reason="needs bash and rsync")

pytest.importorskip("numpy")
pytest.importorskip("tifffile")
pytest.importorskip("skimage")


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    shorten_deadlines(monkeypatch)
    c = StandinCluster(tmp_path / "remote", monkeypatch)
    yield c
    c.reap()


def _run(cluster, tmp_path, stop_fetchers, **kw):
    inp, out = field_plate(tmp_path)
    return start_run(cluster, inp, out, stop_fetchers, field_opts(), **kw)


# ── a clean batched run ────────────────────────────────────────────────────────────────────────────────────────

def test_a_clean_batched_run_brings_every_well_home_and_cleans_the_host(cluster, tmp_path, stop_fetchers):
    run = _run(cluster, tmp_path, stop_fetchers)
    out = run.out

    assert_correct_or_visibly_failed(run, cluster, FIELD_WELLS, expect="done")
    assert run.ending[1].startswith("Remote pipeline completed successfully"), run.ending
    assert complete_here(out, FIELD_WELLS) == set(FIELD_WELLS)
    starts = [ln for ln in run.text.splitlines() if re.search(r"batch-driver: batch \d starting", ln)]
    assert starts == [f"[remote] batch-driver: batch {i} starting" for i in (1, 2, 3)], starts
    # One allocation for the plate, granted once and handed back.
    assert run.text.count("[remote] scheduler: allocation granted") == 1
    assert cluster.jobs() == [], "the allocation outlived the run"
    assert journal_states(out) == {w: "fetched" for w in FIELD_WELLS}
    # The plate's cell-gating thresholds, computed here after the batches, are in the sidecar Review reads (B7).
    gating = json.loads((out / "pipeline_info.json").read_text()).get("cell_gating") or {}
    assert sorted(gating.get("thresh_frac_on") or {}) == ["gfp", "nir"], gating
    assert gating["thresh_frac_on"]["gfp"] > 0 and gating["thresh_frac_on"]["nir"] == 0.0, gating
    assert "outlived it" not in run.text, "a clean batch's pipeline was reported as outliving it (D7)"
    # Auto-delete: the run's directory is gone from the host, and none of the plate's output is left there.
    assert remote_output_wells(cluster) == set()
    assert not list((cluster.root / ".allwell-staging" / "runs").glob("run-*")), sorted(
        str(q.relative_to(cluster.root)) for q in (cluster.root / ".allwell-staging").rglob("*"))


# ── faults on the remote side ──────────────────────────────────────────────────────────────────────────────────

def test_a_lost_ready_flag_is_noticed_and_handed_over_again(cluster, tmp_path, stop_fetchers):
    """The ``touch`` of batch 2's ready flag reports success and the file never appears."""
    cluster.ssh_rule("drop", "touch && .aw_batch_2_ready")

    run = _run(cluster, tmp_path, stop_fetchers)

    fired = cluster.fired()
    assert len(fired) == 1 and fired[0].startswith("drop touch ") and fired[0].endswith(".aw_batch_2_ready"), fired
    assert_correct_or_visibly_failed(run, cluster, FIELD_WELLS, expect="done")
    assert "its hand-off never reached the remote — handing it over again" in run.text


@pytest.mark.parametrize("code", [12, 23], ids=["connection-closed", "partial-transfer"])
def test_a_download_that_fails_once_is_retried(cluster, tmp_path, stop_fetchers, code):
    """12 is retried by the transport; 23 is not, and the Fetcher's end-of-run retry brings the well home — after
    which verification must judge the well by what arrived, not by the record taken before the retry (B8)."""
    cluster.rsync_rule(code, "out/A03_out.zip")

    run = _run(cluster, tmp_path, stop_fetchers)

    assert cluster.fired("rsync"), "the fault never fired"
    assert_correct_or_visibly_failed(run, cluster, FIELD_WELLS, expect="done")


def test_a_node_that_dies_mid_batch_fails_the_run_and_keeps_what_finished(cluster, tmp_path, stop_fetchers):
    killed: list[str] = []

    def on_line(line, _runner):
        if "batch-driver: batch 2 starting" in line and not killed:
            killed.extend(cluster.node_failure())

    run = _run(cluster, tmp_path, stop_fetchers, on_line=on_line)

    assert killed, "the node never failed"
    assert_correct_or_visibly_failed(run, cluster, FIELD_WELLS, expect="error")
    assert "allocation ended during batch 2/3" in run.ending[1], run.ending
    assert {"A01", "A02"} <= complete_here(run.out, FIELD_WELLS), "batch 1's wells were not kept"


# ── stop, then resume ──────────────────────────────────────────────────────────────────────────────────────────

def test_a_stopped_batched_run_resumes_without_sending_the_plate_again(cluster, tmp_path, stop_fetchers):
    """Stopped once batch 2 is on the remote: batch 1's wells are here, batch 2's archives are on the host. The
    resume — the recorded spec with incremental on, as Analyze's Resume starts it — finishes the plate and sends
    neither again."""
    inp, out = field_plate(tmp_path)

    def stop_once_batch_2_is_up(line, runner):
        if "[remote] batch 2: upload complete" in line:
            runner.stop()

    first = start_run(cluster, inp, out, stop_fetchers, field_opts(), on_line=stop_once_batch_2_is_up)
    assert_correct_or_visibly_failed(first, cluster, FIELD_WELLS, expect="error")
    assert first.runner.cancel_state_at_failure[0], "the Stop was not latched as one"
    assert first.ending[1].startswith("Remote pipeline stopped: the wells that finished are kept"), first.ending
    sent_before = [f for f in cluster.uploaded() if f.endswith(".zip")]
    assert sent_before == ["A01.zip", "A02.zip", "A03.zip", "B01.zip"], sent_before
    assert {"A01", "A02"} <= complete_here(out, FIELD_WELLS)

    second = start_run(cluster, inp, out, stop_fetchers, field_opts(incremental=True))

    assert_correct_or_visibly_failed(second, cluster, FIELD_WELLS, expect="done")
    sent_again = [f for f in cluster.uploaded() if f.endswith(".zip")][len(sent_before):]
    assert sorted(sent_again) == ["B02.zip", "B03.zip"], (
        f"the resume sent {sorted(sent_again)}; the first attempt had already put A03 and B01 on the remote and "
        f"brought A01 and A02 home\n{second.text[-6000:]}")
