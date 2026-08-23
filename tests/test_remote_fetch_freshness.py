"""A remote run has to prove it replaced the output it was told to replace.

The reported failure: a remote run with **Force reprocess all wells** ticked ran
for hours, reported success, and left every ``out/`` artefact at its previous
size with the previous day's mtime.

Two individually reasonable decisions composed into it.

``_compute_run_id`` deliberately leaves ``--force`` out of its hash, so a forced
re-run lands in the *same* remote output dir and overwrites it — which is
exactly what stocks that dir with a complete set of ``_out.zip`` files from the
previous run. The pre-flight cache-hit check then counted those files and, with
no force term of its own, replaced the whole pipeline with ``true``. Nothing was
recomputed, so no well announced completion; nothing announced completion, so
the Fetcher skipped every well as "already complete locally"; and verify tested
that the local files *existed*, which the previous day's files did. The stronger
the demand to reprocess, the more certain it was to be ignored.

These cover the cause, the two downstream decisions that let it pass unnoticed,
and the behaviours that had to survive the fix.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

import pytest

import services.remote_runner as rr


def _stale_well(out_dir: Path, well: str, *, days_old: int = 1) -> Path:
    """A complete, non-empty out zip + CSV, aged into the past."""
    import datetime

    zip_path = out_dir / f"{well}_out.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(f"{well}_f0_t0.tif", b"yesterday's bytes")
    csv_path = out_dir / f"measurements_{well}.csv"
    csv_path.write_text("well,cell,area\n%s,1,100\n" % well)
    old = (datetime.datetime.now() - datetime.timedelta(days=days_old)).timestamp()
    for p in (zip_path, csv_path):
        os.utime(p, (old, old))
    return zip_path




def _run(out_dir: Path, **kw) -> rr._RemoteRun:
    params = dict(
        run_id="run-x", cache_key="k", remote_input_dir="/r/in",
        remote_output_dir="/r/out", remote_pidfile="/r/pid",
        local_output_dir=out_dir, local_input_mode=rr.LocalInputMode.ZIP,
        output_filter=rr.OutputFilter.FULL,
    )
    params.update(kw)
    return rr._RemoteRun(**params)


# ── the cause ────────────────────────────────────────────────────────────────

def test_a_forced_run_still_launches_the_pipeline():
    """The bug, at its origin. The remote dir is complete *because* the forced
    run was routed back to it, so treating that as "nothing to do" means force
    can never reprocess anything on a remote that has run before."""
    argv = ["--input_dir", "/in", "--output_dir", "/out", "--force"]
    complete = {f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 13)}
    assert rr._pipeline_launch_is_pointless(argv, 96, complete) is False


def test_a_per_well_forced_run_still_launches_the_pipeline():
    argv = ["--input_dir", "/in", "--force_wells", "C08", "D08"]
    complete = {f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 13)}
    assert rr._pipeline_launch_is_pointless(argv, 96, complete) is False


def test_an_unforced_rerun_still_skips_the_pipeline():
    """The optimisation this predicate exists for must survive: a plain re-run
    over a remote that already holds every well's output should not recompute
    (the case where a previous run's *fetch* was interrupted)."""
    argv = ["--input_dir", "/in", "--output_dir", "/out"]
    complete = {f"{r}{c:02d}" for r in "ABCDEFGH" for c in range(1, 13)}
    assert rr._pipeline_launch_is_pointless(argv, 96, complete) is True


def test_an_incomplete_remote_always_launches():
    argv = ["--input_dir", "/in"]
    assert rr._pipeline_launch_is_pointless(argv, 96, {"A01", "A02"}) is False
    assert rr._pipeline_launch_is_pointless(argv, 0, set()) is False


@pytest.mark.parametrize("argv,force,wells", [
    (["--force"], True, frozenset()),
    (["--force_wells", "c8", "D08"], False, frozenset({"C08", "D08"})),
    (["--force_wells", "C08", "--incremental"], False, frozenset({"C08"})),
    (["--input_dir", "/in"], False, frozenset()),
])
def test_the_run_intent_is_read_off_the_argv(argv, force, wells):
    """``--force_wells`` is ``nargs="*"``, so its values run until the next
    flag; and ``c8`` and ``C08`` name the same well."""
    assert rr._forced_from_argv(argv) == (force, wells)


# ── the fetch decision ───────────────────────────────────────────────────────

def test_a_forced_well_is_always_fetched(tmp_path):
    """"It already exists locally" is the one reason that cannot justify
    skipping a well the user asked to have replaced — that file is the thing
    being replaced."""
    _stale_well(tmp_path, "C08")
    f = rr.Fetcher.__new__(rr.Fetcher)

    f.run = _run(tmp_path)
    assert f._already_local("C08") is not None          # unforced: skipped

    f.run = _run(tmp_path, force=True)
    assert f._already_local("C08") is None              # forced: fetched
    f.run = _run(tmp_path, force_wells=frozenset({"C08"}))
    assert f._already_local("C08") is None


def test_an_unforced_complete_well_is_still_skipped(tmp_path):
    """Deliberate: re-pulling a well this run produced nothing for costs a full
    per-well transfer to replace a file with itself."""
    _stale_well(tmp_path, "A01")
    f = rr.Fetcher.__new__(rr.Fetcher)
    f.run = _run(tmp_path, force_wells=frozenset({"C08"}))
    assert f._already_local("A01") == (
        "already complete locally and untouched by this run")


def test_a_well_this_run_produced_output_for_is_always_fetched(tmp_path):
    _stale_well(tmp_path, "A01")
    f = rr.Fetcher.__new__(rr.Fetcher)
    f.run = _run(tmp_path)
    f.run.expected_wells.add("A01")
    assert f._already_local("A01") is None


# ── the detector ─────────────────────────────────────────────────────────────

class _Verifier:
    """``_verify_local_manifest`` needs only the run and the remote listing."""

    def __init__(self, run, remote_wells):
        self._run = run
        self._remote = set(remote_wells)

    def _list_remote_completed_wells(self):
        return self._remote


def _verify(run, remote_wells):
    lines: list[str] = []
    missing = rr.RemoteRunner._verify_local_manifest(
        _Verifier(run, remote_wells), log=lines.append)
    return missing, lines


def test_a_forced_run_that_replaced_nothing_fails_verify(tmp_path):
    """The reported case, pinned. Before this, verify asked only whether the
    file existed — and yesterday's file does — so the run reported success over
    hours of discarded compute."""
    zip_path = _stale_well(tmp_path, "C08")
    run = _run(tmp_path, force=True,
               pre_run_artifacts=rr._snapshot_local_artifacts(tmp_path))
    # The remote holds output for the well; the local file was never replaced.
    missing, _lines = _verify(run, {"C08"})
    assert missing == [
        "C08_out.zip was not replaced (same size and timestamp as before the run)"
    ]
    assert zip_path.exists()          # the old file is left alone, not deleted


def test_a_forced_run_that_did_replace_the_output_passes(tmp_path):
    """A real forced run must not be failed by the new check."""
    _stale_well(tmp_path, "C08")
    run = _run(tmp_path, force=True,
               pre_run_artifacts=rr._snapshot_local_artifacts(tmp_path))
    # The fetch lands new bytes.
    with zipfile.ZipFile(tmp_path / "C08_out.zip", "w") as zf:
        zf.writestr("C08_f0_t0.tif", b"today's very different bytes")
    missing, lines = _verify(run, {"C08"})
    assert missing == []
    assert any("all 1 wells present locally" in line for line in lines)


def test_an_unforced_run_does_not_fail_on_a_well_it_left_alone(tmp_path):
    """A well nothing was promised for keeps its old artefact legitimately."""
    _stale_well(tmp_path, "A01")
    run = _run(tmp_path, pre_run_artifacts=rr._snapshot_local_artifacts(tmp_path))
    missing, _ = _verify(run, {"A01"})
    assert missing == []


def test_a_missing_artifact_still_reports_as_missing(tmp_path):
    """The pre-existing check is unchanged: absent beats not-replaced."""
    (tmp_path / "measurements_A01.csv").write_text("well\nA01\n")
    run = _run(tmp_path, force=True,
               pre_run_artifacts=rr._snapshot_local_artifacts(tmp_path))
    missing, _ = _verify(run, {"A01"})
    assert missing == ["missing A01_out.zip locally"]


def test_a_run_with_no_snapshot_does_not_fail_everything(tmp_path):
    """An empty snapshot means every artefact is new, not that none changed —
    a check that fails open here would fail every run that starts clean."""
    _stale_well(tmp_path, "C08")
    run = _run(tmp_path, force=True)          # pre_run_artifacts defaults empty
    missing, _ = _verify(run, {"C08"})
    assert missing == []


# ── the repair ───────────────────────────────────────────────────────────────

def _retry_pulls(monkeypatch, tmp_path, run, well="C08") -> list[str]:
    """Drive ``_retry_missing_wells`` over one well, collecting rsync sources."""
    pulled: list[str] = []

    def _fake_rsync(argv, **kw):
        pulled.append(argv[-2])          # the remote source
    monkeypatch.setattr(rr, "_rsync_with_retry", _fake_rsync)

    runner = rr.RemoteRunner.__new__(rr.RemoteRunner)
    runner._run = run
    runner.host = rr.RemoteHost(
        name="t", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )
    runner._cancelled = __import__("threading").Event()
    runner._retry_missing_wells(
        [f"{well}_out.zip was not replaced (same size and timestamp as "
         f"before the run)"],
        log=lambda _m: None)
    return pulled


def test_a_forced_well_that_was_not_replaced_is_re_pulled(monkeypatch, tmp_path):
    """Reporting the loss is the floor, not the goal. The backstop normally
    leaves a zip that is merely *present* alone — pulling hundreds of MB to
    replace a file with itself is what dragged it into a retry loop — but a
    forced well still carrying its pre-run size and mtime is the one case where
    present is the symptom."""
    _stale_well(tmp_path, "C08")
    run = _run(tmp_path, force=True,
               pre_run_artifacts=rr._snapshot_local_artifacts(tmp_path))
    pulled = _retry_pulls(monkeypatch, tmp_path, run)
    assert pulled == ["test-host:/r/out/C08_out.zip"]


def test_an_unforced_present_zip_is_still_left_alone(monkeypatch, tmp_path):
    """The cost guard the branch exists for must survive."""
    _stale_well(tmp_path, "C08")
    run = _run(tmp_path, pre_run_artifacts=rr._snapshot_local_artifacts(tmp_path))
    assert _retry_pulls(monkeypatch, tmp_path, run) == []


# ── the record that outlives the log ─────────────────────────────────────────

class _Reconciler:
    def __init__(self, run, remote_wells):
        self._run = run
        self._remote = set(remote_wells)

    def _list_remote_completed_wells(self):
        return self._remote


def test_every_well_is_classified_in_a_file_beside_the_outputs(tmp_path):
    """The report that would have answered this in one line. "Why was this
    well not fetched?" was previously answerable only from stdout as it
    streamed, which is no help once the log drawer is closed."""
    run = _run(tmp_path, force=True)
    run.expected_wells.add("A01")
    run.skipped_wells["A02"] = "already complete locally and untouched by this run"
    summary = rr.RemoteRunner._reconcile_fetch(
        _Reconciler(run, {"A01", "A02", "A03"}), log=lambda _m: None)

    assert "1 fetched" in summary
    assert "1 skipped" in summary
    assert "1 never announced completion" in summary

    report = (tmp_path / "remote_fetch_report.txt").read_text()
    assert "A01  fetched" in report
    assert "A02  skipped" in report
    assert "A03  never-announced" in report
    assert "force: True" in report


def test_the_summary_accounts_for_every_well_the_report_lists(
        tmp_path, monkeypatch):
    """The parts have to sum to the whole, on the day a state is added.

    The summary sits directly under "wells with output on the remote: N", so a
    bucket it drops is a well the per-well lines name and the tally does not.
    That happened once with a fixed four-outcome list, which lost ``failed``;
    it was then re-created by driving the tally off the phrase table, which
    loses anything that table does not spell. The order comes from
    ``WELL_STATES`` now and an unrecognised state prints under its own name, so
    neither omission can silently shrink the count.

    Driven by adding a state to ``WELL_STATES`` — which is what a future
    contributor does, and the moment the phrase table falls behind. Both the
    reader and the classifier take the roster from there, so a monkeypatched
    roster is the whole scenario.
    """
    import services.run_journal as J

    extended = (*J.WELL_STATES, "quarantined")
    monkeypatch.setattr(J, "WELL_STATES", extended)

    run = _run(tmp_path)
    J.open_run(tmp_path, spec={}, mode="remote", input_dir=tmp_path / "in")
    J.record_well(tmp_path, "A01", "fetched")
    J.record_well(tmp_path, "A02", "quarantined", reason="held back")
    J.record_well(tmp_path, "A03", "quarantined", reason="held back")

    summary = rr.RemoteRunner._reconcile_fetch(
        _Reconciler(run, {"A01", "A02", "A03"}), log=lambda _m: None)

    report = (tmp_path / "remote_fetch_report.txt").read_text()
    listed = [ln for ln in report.splitlines()
              if ln.startswith(("A01", "A02", "A03"))]
    assert len(listed) == 3, listed
    # Only the tally, not the "(of N with output…)" that follows it.
    tally = summary.split(" (of ")[0]
    counted = sum(int(n) for n in re.findall(r"(\d+) ", tally))
    assert counted == 3, (
        f"the summary accounts for {counted} of the 3 wells it lists: {tally}")
    assert "quarantined" in tally, (
        f"a state with no phrase was dropped rather than named: {tally}")


def test_the_report_survives_an_unwritable_output_dir(tmp_path):
    """A diagnostic must never be the thing that fails the run."""
    run = _run(tmp_path / "gone")          # never created
    lines: list[str] = []
    rr.RemoteRunner._reconcile_fetch(
        _Reconciler(run, {"A01"}), log=lines.append)
    assert any("could not write" in ln for ln in lines)


# ── the marker parse ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("label,canonical", [
    ("A01", "A01"), ("C08", "C08"), ("C8", "C08"), ("h12", "H12"),
])
def test_a_completion_marker_is_parsed_and_canonicalised(label, canonical):
    """A plate zipped as ``C8.zip`` announces "Well C8 — done."; a
    two-digit-only pattern never matched it, so that well never entered
    ``expected_wells`` and was never fetched."""
    line = f"2026-08-02 10:00:00  INFO      Well {label} — done."
    m = rr._RE_WELL_DONE.search(line)
    assert m is not None, line
    from util.well_token import canonical_well_label
    assert canonical_well_label(m.group("well")) == canonical


def test_the_summary_accounts_for_every_well_it_names(tmp_path, monkeypatch):
    """The parts must sum to the whole it prints beside them.

    The summary named a fixed four outcomes, so a well recorded ``failed`` — the
    state this run's own Fetcher writes when output arrives and cannot be
    materialised — appeared in the per-well lines and in none of the numbers.
    The tally sat directly under "wells with output on the remote: N" and no
    longer added up to N, which is a report quietly dropping the one outcome a
    user most needs to see.
    """
    import re

    from services import run_journal as J

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    run = _run(tmp_path)
    out = run.local_output_dir
    J.open_run(out, spec={}, mode="remote", input_dir=tmp_path / "in")
    J.record_well(out, "A01", "fetched")
    J.record_well(out, "A02", "failed", reason="materialize failed")
    J.record_well(out, "A03", "not-fetched", reason="the pull failed")

    summary = rr.RemoteRunner._reconcile_fetch(
        _Reconciler(run, {"A01", "A02", "A03"}), log=lambda _m: None)

    assert "1 failed" in summary, f"a failed well vanished from: {summary}"
    tally = summary.split(": ", 1)[1].split(" (of ", 1)[0]
    counted = sum(int(n) for n in re.findall(r"(\d+) ", tally))
    assert counted == 3, (
        f"the summary accounts for {counted} of 3 wells: {summary}")
