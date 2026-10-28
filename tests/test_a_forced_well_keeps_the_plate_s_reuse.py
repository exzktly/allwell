"""Forcing one well redoes that well; it does not cost the rest of the plate its earlier work (tracker F2).

``--force_wells`` was part of the run's argv digest (only ``--force`` was left out), and so were ``--only_wells`` and
``--no_auto_threshold``. A Resume adds ``--force_wells`` itself (``remote_finished_wells.nanowell_forced``), and a
run that sends part of a plate adds ``--no_auto_threshold``, so the resumed run had a different digest from the
attempt it resumed: ``stored_remote_addresses`` found nothing, uploaded batches were sent again, finished wells
recomputed, and the old attempt orphaned. And one forced well disabled re-attach for the whole plate. The digest now
leaves those three out, re-attach declines only for ``--force``, and a forced well is excluded per well where reuse
is decided (``remote_batch_resume.take_from_remote``; the incident itself is driven in
``tests/test_a_batched_restart_reuses_the_remote.py::test_a_resume_that_forces_a_well_takes_over_the_attempt_it_resumes``).
"""

from __future__ import annotations

import types

import pytest

from services.remote_finished_wells import argv_forcing
from services.remote_run_id import earlier_argv_digest, legacy_argv_digests, pipeline_argv_digest

BASE = ["python", "process_microscopy.py", "--input_dir", "/in", "--output_dir", "/out",
        "--nuclear_token", "DAPI", "--fluor_tokens", "GFP", "mCherry"]


@pytest.mark.parametrize("scoped", [
    argv_forcing(BASE, ["A05"]),
    argv_forcing(BASE, ["A05", "B07", "C12"]),                    # several values: the flag is multi-valued
    [*BASE, "--only_wells", "A01", "A02"],
    [*BASE, "--no_auto_threshold"],
    [*argv_forcing(BASE, ["A05"]), "--only_wells", "A05", "--no_auto_threshold"],
], ids=["one forced", "three forced", "only wells", "no auto threshold", "all three"])
def test_which_wells_run_does_not_move_the_run_s_address(scoped):
    assert pipeline_argv_digest(scoped) == pipeline_argv_digest(BASE), (
        f"{scoped[len(BASE) - 4:]} changed the digest, so a Resume that adds it cannot find the attempt it resumes")


def test_a_setting_after_the_force_list_still_counts():
    """The multi-value skip stops at the next flag: a setting written after the well names is still hashed."""
    a = [*argv_forcing(BASE, ["A05", "B07"]), "--segmentation_method", "cellpose"]
    b = [*argv_forcing(BASE, ["A05", "B07"]), "--segmentation_method", "stardist_nuclei"]
    assert pipeline_argv_digest(a) != pipeline_argv_digest(b)


def test_an_attempt_the_build_before_recorded_is_still_found():
    """Addresses journalled before the change carry the earlier definition's digest — of the argv as it was then,
    usually without the force list a Resume adds."""
    resume = argv_forcing([*BASE, "--no_auto_threshold"], ["A05"])
    legacy = legacy_argv_digests(resume)
    searched = {pipeline_argv_digest(resume), *legacy}
    assert earlier_argv_digest(resume) in legacy, "the attempt recorded with this very force list is not searched"
    assert earlier_argv_digest(BASE) in searched, "the attempt recorded before the Resume added its force list is not"
    assert pipeline_argv_digest(resume) not in legacy


def _single_shot(monkeypatch, argv):
    from services import remote_finished_wells, remote_reuse, run_journal

    stored, fresh = "/stg/runs/run-old/out", "/stg/runs/run-new/out"
    monkeypatch.setattr(run_journal, "stored_remote_address", lambda *a, **k: {"remote_output_dir": stored})
    monkeypatch.setattr(remote_finished_wells, "list_remote_outputs",
                        lambda cache, d: remote_finished_wells.RemoteOutputs(frozenset({"A01"}), frozenset({"A01"})))
    log: list[str] = []
    chosen = remote_reuse.reattach_output_dir(types.SimpleNamespace(_cache=None), fresh, "/local/out", argv,
                                              argv_digest="d", source_key="s", host_alias="h", log=log.append)
    return chosen == stored, "".join(log)


def test_a_single_shot_run_with_a_forced_well_still_re_attaches(monkeypatch):
    attached, log = _single_shot(monkeypatch, argv_forcing(BASE, ["A05"]))
    assert attached, f"one forced well declined the earlier directory for the whole plate: {log}"
    attached, _log = _single_shot(monkeypatch, [*BASE, "--force"])
    assert not attached, "--force must still start in a fresh directory"


class _Stop(Exception):
    pass


@pytest.mark.parametrize("argv, forced", [(argv_forcing(BASE, ["A05"]), False), ([*BASE, "--force"], True)])
def test_a_batched_run_with_a_forced_well_still_asks_to_re_attach(tmp_path, monkeypatch, argv, forced):
    """The batched constructor's own call, not the helper: it passed ``forced`` for any forced well."""
    import services.remote_process as rp

    seen = {}

    def capture(runner, local_output_dir, keys, legacy, *, fresh, forced, log):
        seen["forced"] = forced
        raise _Stop

    monkeypatch.setattr(rp, "reattach_output_dir", capture)
    runner = types.SimpleNamespace(
        _cache=types.SimpleNamespace(cache_key=lambda zipped: "k" * 32),
        _host_argv_extras=lambda _f: [],
        host=types.SimpleNamespace(remote_staging_dir="~/.allwell-staging", ssh_alias="h"))
    with pytest.raises(_Stop):
        rp._BatchedRemoteProcess(runner, types.SimpleNamespace(well_zips=()), tmp_path, argv, 2,
                                 output_filter=None)
    assert seen["forced"] is forced
