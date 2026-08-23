"""Re-attaching a remote run to the output directory a previous one recorded.

A remote run's output directory is addressed by hashing the staged input
archives' ``(name, size, mtime)`` plus the pipeline argv. That address is stable
when the archives are uploaded straight from the user's input folder, which is
the common case and the reason the staging planner passes an unfiltered archive
through by its source path.

It is *not* stable when staging rewrites — a channel filter that clears
``REWRITE_MIN_SAVING``, or an incremental split. Those write a fresh copy into
scratch on every run, so the thing being hashed is a file created moments ago
and the same plate addresses a different remote directory each time; whatever
the previous run computed then sits somewhere nothing will look again. The last
three tests measure that rather than asserting it from this docstring.

One detail worth knowing before reading them: ``cache_key`` hashes
``int(st_mtime)``, so two staging passes inside the same wall-clock second
produce the same key even though each wrote a brand-new file. That is why the
drift is invisible in a tight loop and certain in practice, and why the test
that pins it stamps the mtimes instead of racing the clock.

The journal closes it by writing the address down. What it must *not* do is
trust it: the record says where work was sent, which is not the same claim as
the work still being there.
"""

from __future__ import annotations

import os
import zipfile

import pytest

from services import run_journal as J
from services.remote_runner import (
    DatasetCache,
    _choose_remote_output_dir,
    _compute_run_id,
    pipeline_argv_digest,
    stage_local_zips,
)

def _make_host():
    from services.remote_runner import RemoteHost
    return RemoteHost(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )


SCHEMA = "experiment:well:fov:timepoint:channel"
SEP = "_"
FRESH = "/staging/runs/run-fresh/out"
STORED = "/staging/runs/run-stored/out"


# ── the decision ────────────────────────────────────────────────────────────

def test_a_populated_stored_dir_is_re_attached_to():
    chosen, reason = _choose_remote_output_dir(
        FRESH, STORED, forced=False, remote_has_outputs=True)
    assert chosen == STORED
    # Both addresses named, because a run landing somewhere other than where
    # it would have is the kind of thing a user has to be able to see.
    assert STORED in reason and FRESH in reason


def test_an_empty_stored_dir_is_not_re_attached_to():
    """The journal proposes; the remote listing decides. A directory that has
    been cleaned up holds nothing worth re-attaching to."""
    chosen, reason = _choose_remote_output_dir(
        FRESH, STORED, forced=False, remote_has_outputs=False)
    assert chosen == FRESH
    assert STORED in reason and "no output" in reason


def test_a_forced_run_never_re_attaches():
    """``--force`` means "produce this again". Landing in a directory already
    full of the previous run's output is how a forced re-run gets skipped."""
    chosen, reason = _choose_remote_output_dir(
        FRESH, STORED, forced=True, remote_has_outputs=True)
    assert chosen == FRESH
    assert "reprocess" in reason


def test_no_stored_address_is_silent():
    """The overwhelmingly common case — every first run of a plate. It must
    cost nothing and say nothing."""
    assert _choose_remote_output_dir(
        FRESH, "", forced=False, remote_has_outputs=False) == (FRESH, "")


def test_a_stored_address_equal_to_the_fresh_one_is_silent():
    """The stable-key case: the addresses agree, so there is no choice to
    report and nothing for the user to wonder about."""
    assert _choose_remote_output_dir(
        FRESH, FRESH, forced=False, remote_has_outputs=True) == (FRESH, "")


# ── the digest that decides "the same configuration" ────────────────────────

def test_the_digest_ignores_the_paths_and_the_force_flag():
    """``_compute_run_id`` deliberately leaves these out so a forced re-run
    lands in the same directory and overwrites it. The journal keys on the same
    digest, so the two cannot disagree about what "the same run" means."""
    base = ["--nuclear_token", "DAPI"]
    assert pipeline_argv_digest(base) == pipeline_argv_digest(
        ["--input_dir", "/a", "--output_dir", "/b", "--force", *base])


def test_the_digest_notices_a_real_configuration_change():
    assert pipeline_argv_digest(["--nuclear_token", "DAPI"]) != \
        pipeline_argv_digest(["--nuclear_token", "NIR"])


def test_the_run_id_is_built_from_that_digest():
    """One definition. A second spelling of "which flags count" would have the
    journal offering an address the run id says belongs somewhere else."""
    argv = ["--nuclear_token", "DAPI"]
    assert _compute_run_id("k" * 32, argv).endswith(pipeline_argv_digest(argv))


# ── the journal side of it ──────────────────────────────────────────────────

@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    d = tmp_path / "out"
    d.mkdir()
    J.open_run(d, spec={}, mode="remote", input_dir=tmp_path / "in")
    return d


def test_a_recorded_address_survives_to_the_next_run(out_dir):
    """The sequence a real second run takes, not a shortcut through it.

    This test named the property it was pinning and then did not exercise it:
    it recorded an address and read it straight back, inside one run. The app
    never does that. ``PipelineRunner`` opens the journal *before*
    ``RemoteRunner.spawn`` reads it, so the read that matters is the one on the
    far side of the next ``open_run`` — and with the remote block reset there,
    ``stored_remote_address`` returned ``None`` for ever and the re-attach could
    not fire at all. The fixture has to carry the mess of the real order
    (CLAUDE.md §7.1 #4); without the second ``open_run`` below, this passes
    against code where the feature is unreachable.
    """
    argv = ["--nuclear_token", "DAPI"]
    digest = pipeline_argv_digest(argv)

    # Run one records where it sent the work, then ends.
    J.record_remote(
        out_dir, run_id="run-stored", cache_key="k1", host_alias="hpc",
        remote_input_dir="/staging/datasets/k1", remote_output_dir=STORED,
        argv_digest=digest, source_key="src1")
    J.close_run(out_dir, outcome="error")

    # Run two opens its own journal first — as PipelineRunner does — and only
    # then does spawn ask what the last run's address was.
    J.open_run(out_dir, spec={}, mode="remote", input_dir="/data/in", argv=[])
    stored = J.stored_remote_address(
        out_dir, argv_digest=digest, source_key="src1", host_alias="hpc")
    assert stored is not None, (
        "opening the next run's journal wiped the address it exists to carry")
    assert stored["remote_output_dir"] == STORED

    chosen, _ = _choose_remote_output_dir(
        FRESH, stored["remote_output_dir"], forced=False, remote_has_outputs=True)
    assert chosen == STORED


def test_opening_a_new_run_keeps_the_address_and_drops_everything_else(out_dir):
    """The carry-forward is exactly one field wide.

    The record describes *this* run — a previous run's per-well states read as
    this one's would be a straight misreport. The remote address is the one
    thing that legitimately outlives the run that wrote it, because it names a
    directory on another machine that is still there.
    """
    J.record_remote(
        out_dir, run_id="run-stored", cache_key="k1", host_alias="hpc",
        remote_input_dir="/staging/datasets/k1", remote_output_dir=STORED,
        argv_digest="d1", source_key="src1")
    J.record_well(out_dir, "A01", "fetched")
    J.close_run(out_dir, outcome="error", message="boom")

    J.open_run(out_dir, spec={}, mode="remote", input_dir="/data/in", argv=[])
    journal = J.read_journal(out_dir)
    assert journal.remote["remote_output_dir"] == STORED
    assert journal.wells == {}
    assert journal.outcome is None and journal.message == ""
    assert journal.is_open


def test_a_reconfigured_run_gets_no_address(out_dir):
    J.record_remote(
        out_dir, run_id="run-stored", cache_key="k1", host_alias="hpc",
        remote_input_dir="/staging/datasets/k1", remote_output_dir=STORED,
        argv_digest=pipeline_argv_digest(["--nuclear_token", "DAPI"]),
        source_key="src1")
    assert J.stored_remote_address(
        out_dir,
        argv_digest=pipeline_argv_digest(["--nuclear_token", "NIR"]),
        source_key="src1", host_alias="hpc") is None


# ── the churn this exists for, measured rather than asserted ────────────────

def _plate(root):
    """A plate whose GFP frames dwarf its DAPI ones, so dropping GFP clears
    ``REWRITE_MIN_SAVING`` and the staging planner actually rewrites."""
    in_dir = root / "in"
    in_dir.mkdir(parents=True)
    for well in ("A01", "A02"):
        with zipfile.ZipFile(in_dir / f"{well}.zip", "w") as zf:
            for fov in ("F001", "F002"):
                zf.writestr(f"Exp_{well}_{fov}_T01_DAPI.tif", b"\x00" * 2_000)
                zf.writestr(f"Exp_{well}_{fov}_T01_GFP.tif", b"\xff" * 400_000)
    return in_dir


def _stage(in_dir, tokens, *, stamp=None):
    """Stage the plate and return ``(address, staged_from_input)``.

    *stamp* forces every uploaded archive's mtime, which is what makes the
    churn measurable without racing the clock: ``cache_key`` hashes
    ``int(st_mtime)``, so two staging passes inside the same wall-clock second
    key identically even though each wrote a brand-new file. That one-second
    resolution is why the drift is invisible in a tight loop and certain in
    practice, where consecutive runs are minutes or days apart.
    """
    zipped = stage_local_zips(
        in_dir, filename_schema=SCHEMA, filename_sep=SEP,
        fluor_tokens=tokens, nuclear_token="DAPI")
    try:
        if stamp is not None:
            for path in zipped.well_zips:
                os.utime(path, (stamp, stamp))
        from_input = all(p.parent == in_dir for p in zipped.well_zips)
        key = DatasetCache.cache_key(zipped)
        return _compute_run_id(key, ["--x"]), from_input
    finally:
        if zipped.cleanup is not None:
            zipped.cleanup()


def test_a_pass_through_upload_keys_the_same_between_runs(tmp_path):
    """The staging planner's reason for passing an unchanged archive through by
    its source path, stated as a measurement: the file is the user's own, so
    its mtime is whatever it has always been."""
    in_dir = _plate(tmp_path)
    first, from_input = _stage(in_dir, ["DAPI", "GFP"])
    second, _ = _stage(in_dir, ["DAPI", "GFP"])
    assert from_input, "a filter that drops nothing should not stage a copy"
    assert first == second


def test_a_rewritten_upload_stages_a_fresh_file_each_run(tmp_path):
    """The mechanism behind the drift, isolated from the clock.

    A filter that clears ``REWRITE_MIN_SAVING`` writes a new archive into
    scratch rather than uploading the user's own, so the thing being hashed is
    a file created moments ago — and its mtime is the current time, not the
    dataset's.
    """
    in_dir = _plate(tmp_path)
    _, from_input = _stage(in_dir, ["DAPI"])
    assert not from_input


def test_a_rewritten_upload_keys_differently_once_the_second_turns(tmp_path):
    """The gap the recorded address exists to close.

    Two staged copies a second apart address different remote directories, so
    whatever the earlier run computed is left somewhere the later one will
    never look. If this stops being true — because staged copies gain stable
    mtimes, say — the re-attach path has no problem left to solve.
    """
    in_dir = _plate(tmp_path)
    first, _ = _stage(in_dir, ["DAPI"], stamp=1_700_000_000)
    second, _ = _stage(in_dir, ["DAPI"], stamp=1_700_000_001)
    assert first != second


# ── the reconciliation degrades, and does not degrade further ───────────────

def test_the_fetch_report_prefers_the_journal(tmp_path, monkeypatch):
    """A well the journal recorded as *failed* is reported as failed.

    The in-memory state cannot say this: to it, a well that failed to
    materialise and a well nobody mentioned look the same — absent.
    """
    from services.remote_runner import _classify_fetched_wells

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    out = tmp_path / "out"
    out.mkdir()
    J.open_run(out, spec={}, mode="remote", input_dir=tmp_path / "in")
    J.record_well(out, "A01", "fetched")
    J.record_well(out, "A02", "failed", reason="materialize failed")

    counts, lines = _classify_fetched_wells(
        ["A01", "A02"], out, announced={"A01", "A02"}, skipped={})
    assert counts["fetched"] == 1 and counts["failed"] == 1
    assert any("materialize failed" in ln for ln in lines)


def test_the_fetch_report_falls_back_to_the_in_memory_state(tmp_path):
    """No journal — a read-only output volume, a full disk — must cost the
    report its *extra* answers, never the ones it already had.

    Before the journal existed this report was built from exactly these two
    maps, so a fallback that consulted only one of them would turn a well the
    Fetcher deliberately skipped into one it had never heard of.
    """
    from services.remote_runner import _classify_fetched_wells

    out = tmp_path / "out"
    out.mkdir()
    counts, lines = _classify_fetched_wells(
        ["A01", "A02", "A03"], out,
        announced={"A01"},
        skipped={"A02": "already complete locally and untouched by this run"})
    assert counts["fetched"] == 1
    assert counts["skipped"] == 1
    assert counts["never-announced"] == 1
    assert any("already complete locally" in ln for ln in lines)


def _cleanup_deletions(tmp_path, monkeypatch, *, reattached: bool) -> list[str]:
    """Run ``verify_and_cleanup`` for real and return what it removed.

    Asserted at the boundary — the ``rm -rf`` that reaches the remote — rather
    than on the flag that decides it. The previous version of this test built
    two dataclasses and asserted ``run.reattached`` was the value it had just
    passed in; it would have passed unchanged with the cleanup branch deleted.
    """
    from services import remote_runner as rr

    run = rr._RemoteRun(
        run_id="run-new", cache_key="k" * 32, remote_input_dir="/r/in",
        remote_output_dir=(STORED if reattached
                           else "~/.allwell-staging/runs/run-new/out"),
        remote_pidfile="~/.allwell-staging/runs/run-new/run.pid",
        local_output_dir=tmp_path, local_input_mode=rr.LocalInputMode.ZIP,
        output_filter=rr.OutputFilter.FULL, reattached=reattached,
    )

    removed: list[str] = []

    def _ssh_run(self, argv, *, check=False, **kw):
        if list(argv[:2]) == ["rm", "-rf"]:
            removed.extend(argv[2:])
        return None

    monkeypatch.setattr(rr.DatasetCache, "_ssh_run", _ssh_run)

    runner = rr.RemoteRunner.__new__(rr.RemoteRunner)
    runner._run = run
    runner._cache = rr.DatasetCache.__new__(rr.DatasetCache)
    runner.host = _make_host()
    # Nothing is missing, so verify passes and cleanup is reached.
    monkeypatch.setattr(rr.RemoteRunner, "_verify_local_manifest",
                        lambda self, *, log: [])
    monkeypatch.setattr(rr.RemoteRunner, "_reconcile_fetch",
                        lambda self, *, log: "")
    # Real rsync attempts, with the retry ladder behind them — a minute of
    # timeouts for artefacts this test does not look at.
    monkeypatch.setattr(rr.RemoteRunner, "_rsync_optional",
                        lambda self, *a, **kw: None)
    rr.RemoteRunner.verify_and_cleanup(
        runner, True, cleanup_mode="run", log_fn=lambda _m: None)
    return removed


def test_cleanup_leaves_a_re_attached_run_dir_alone(tmp_path, monkeypatch):
    """A re-attached run must not delete the directory it borrowed.

    Its output lives under a *previous* run's ``runs/<id>/``, so the path
    ``verify_and_cleanup`` derives from this run's own ``run_id`` holds nothing
    it produced. Removing that would report a cleanup, leave the real outputs
    on the remote for ever, and take whatever else the other run's directory
    still holds.
    """
    removed = _cleanup_deletions(tmp_path, monkeypatch, reattached=True)
    assert removed == [], f"a re-attached run deleted {removed}"


def test_an_ordinary_run_still_removes_its_run_dir(tmp_path, monkeypatch):
    """The other half: declining for a re-attach must not decline for everyone.

    Without this the test above passes just as well against a cleanup that
    never deletes anything at all.
    """
    removed = _cleanup_deletions(tmp_path, monkeypatch, reattached=False)
    assert removed == ["~/.allwell-staging/runs/run-new"], removed


# ── the discrimination the whole re-attach rests on ─────────────────────────

def _record_run_one(out_dir, in_dir):
    from services.remote_runner import source_fingerprint
    J.open_run(out_dir, spec={}, mode="remote", input_dir=in_dir, argv=[])
    J.record_remote(
        out_dir, run_id="run-OLD", cache_key="keyOLD", host_alias="hpc",
        remote_input_dir="/staging/datasets/keyOLD", remote_output_dir=STORED,
        argv_digest="d1", source_key=source_fingerprint(in_dir))
    J.close_run(out_dir, outcome="ok")


def _run_two_chooses(out_dir, in_dir):
    from services.remote_runner import source_fingerprint
    J.open_run(out_dir, spec={}, mode="remote", input_dir=in_dir, argv=[])
    stored = J.stored_remote_address(
        out_dir, argv_digest="d1", source_key=source_fingerprint(in_dir),
        host_alias="hpc")
    chosen, _ = _choose_remote_output_dir(
        FRESH, (stored or {}).get("remote_output_dir", ""),
        forced=False, remote_has_outputs=True)
    return chosen


def test_the_same_images_re_attach_even_though_the_upload_key_churned(tmp_path,
                                                                     monkeypatch):
    """The case the feature exists for, end to end over real archives."""
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "out"
    out.mkdir()
    in_dir = _plate(tmp_path)
    _record_run_one(out, in_dir)
    assert _run_two_chooses(out, in_dir) == STORED


def test_replaced_images_do_not_re_attach(tmp_path, monkeypatch):
    """The case that would have handed back the previous run's numbers.

    Nothing the user can see distinguishes this from the test above — same
    plate, same folder, same settings, Force not ticked — and the upload cache
    key churns identically in both. Only the input fingerprint separates them,
    and getting it wrong means the pre-flight check finds the stale directory
    complete, skips the pipeline, and reports the old results as this run's.
    """
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "out"
    out.mkdir()
    in_dir = _plate(tmp_path)
    _record_run_one(out, in_dir)

    for archive in sorted(in_dir.glob("*.zip")):
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("Exp_A01_F001_T01_DAPI.tif", b"corrected images")

    assert _run_two_chooses(out, in_dir) == FRESH


# ── reachability: is the decision reached from the app's own sequence? ───────

def test_the_stored_address_survives_the_app_s_real_call_order(tmp_path, monkeypatch):
    """Drive the two callers in the order the app runs them, not the unit's.

    This is the test whose absence let the feature ship dead. Everything about
    the re-attach was unit-tested — the decision function against hand-supplied
    arguments, the journal against its own API — and all of it passed while the
    app could not reach the branch at all, because two different modules touch
    this record and nothing exercised them in sequence:

        PipelineRunner._run_pipeline_thread -> run_journal.open_run
        RemoteRunner.spawn                  -> stored_remote_address

    ``open_run`` reset the block ``spawn`` was about to read. Both functions
    were correct in isolation; the order was the defect, and an order is not
    something a unit test of either end can see.
    """
    from services import pipeline_runner as PR
    from services.remote_runner import source_fingerprint

    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "idx.json")
    out = tmp_path / "out"
    out.mkdir()
    in_dir = _plate(tmp_path)

    # Run one, through the runner's own opener rather than a hand-rolled one.
    runner = PR.PipelineRunner()
    runner._journal_spec = {"opts": {}}
    runner._open_journal(in_dir, out, ["--x"], {})
    J.record_remote(
        out, run_id="run-OLD", cache_key="keyOLD", host_alias="hpc",
        remote_input_dir="/staging/datasets/keyOLD", remote_output_dir=STORED,
        argv_digest="d1", source_key=source_fingerprint(in_dir))
    runner._close_journal()

    # Run two: the runner opens the journal first, exactly as it does in
    # production, and only then does spawn ask what the last run recorded.
    runner._open_journal(in_dir, out, ["--x"], {})
    stored = J.stored_remote_address(
        out, argv_digest="d1", source_key=source_fingerprint(in_dir),
        host_alias="hpc")

    assert stored is not None, (
        "opening this run's journal destroyed the address the next read needs; "
        "the re-attach branch exists but the app cannot reach it")
    assert stored["remote_output_dir"] == STORED
