"""A decision to reuse earlier work must be keyed on everything that invalidates it.

This is the guard for the defect class that shipped, unnoticed, in the run
journal: a predicate that decides "we already did this, use the old result"
whose key covered *less* than the set of things that make the old result wrong.
The consequence is not a crash. It is a run that quietly returns stale numbers
and reports success — the one outcome CLAUDE.md §7 says must never be
indistinguishable from a good one.

The concrete case: `stored_remote_address` handed back a remote output
directory keyed on the pipeline arguments alone. The upload cache key that
triggers the lookup churns for **two** reasons — staging rewrote the archives
(same images, new files) or the user replaced the images — and the argv cannot
tell them apart. So a plate re-exported with corrected images, with no setting
touched and Force not ticked, re-attached to the previous run's directory, was
found complete by the pre-flight check, never ran the pipeline, and had the old
results fetched back as its own.

Two things are pinned here, and the split matters because only one of them is
mechanically decidable.

**Every declared key is load-bearing.** Change one alone and the lookup must
decline. A key that is recorded but not compared is decoration, and it reads in
review as protection that is not there. This half is enforced below,
exhaustively, by mutating each key in turn.

**Nothing outside the keys can invalidate the result.** That half no test can
decide for you — it is a claim about the domain. What this file does is make the
claim *explicit*: `REUSE_CONTRACTS` names the keys and, beside each, the reason
it exists. Adding a reuse decision without adding it here is the omission to
catch in review; adding it with a key whose justification you cannot write down
is the signal that you have not finished thinking about what makes the old
result wrong.
"""

from __future__ import annotations

import pytest

from services import run_journal as J

#: Every "reuse earlier work" decision in the app, the keys it compares, and why
#: each key is required. One entry per decision; the reason is not a comment, it
#: is the part a reviewer checks.
REUSE_CONTRACTS = {
    "run_journal.stored_remote_address": {
        "argv_digest": (
            "the pipeline arguments decide the bytes of the output, so a "
            "differently-configured run's results are not interchangeable"),
        "source_key": (
            "fingerprints the user's own input archives. The upload cache key "
            "churns both when staging rewrites and when the images change; "
            "only this separates them, and without it a re-exported plate "
            "reuses the results of the images it replaced"),
        "host_alias": (
            "the address is a path on one machine. Two hosts can share a "
            "filesystem, so a path recorded against one resolves against the "
            "other and its listing succeeds — which is why the machine has to "
            "be compared rather than inferred from the directory being there. "
            "A 'gpu'/'cpu' pair of entries for the same cluster, differing "
            "only in compute_launcher or extra_args, is the live case"),
    },
}

#: What ``argv_digest`` has to cover, and why each item is not optional.
#: Not a second roster of keys — a note on the completeness of one of them,
#: because the digest is computed from the *local* argv and three of the flags
#: that decide the output bytes are appended after that, by the host.
ARGV_DIGEST_MUST_COVER = {
    "--no_save_tophat": (
        "added when the Fetch filter is CSV_ONLY. It drops the tophat QC "
        "images from the output zip, so a FULL run and a CSV_ONLY run do not "
        "produce interchangeable output"),
    "host.extra_args": (
        "arbitrary per-host pipeline flags — segmentation thresholds among "
        "them — so two hosts can compute different numbers from one form"),
    "--cpu_only": (
        "added for a CPU-only host, which changes the execution path"),
}


def _record(out_dir, **overrides):
    keys = {"argv_digest": "argv-1", "source_key": "src-1",
            "host_alias": "hpc"}
    keys.update(overrides)
    J.record_remote(
        out_dir, run_id="run-1", cache_key="k1",
        remote_input_dir="/staging/datasets/k1",
        remote_output_dir="/staging/runs/run-1/out", **keys)
    return keys


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "index_path", lambda: tmp_path / "state" / "idx.json")
    d = tmp_path / "out"
    d.mkdir()
    J.open_run(d, spec={}, mode="remote", input_dir=tmp_path / "in")
    return d


def test_every_reuse_decision_is_registered():
    """A decision missing from the roster is one nobody checked the keys of."""
    assert "run_journal.stored_remote_address" in REUSE_CONTRACTS


def test_matching_every_key_reuses(out_dir):
    """The baseline. Without it, a lookup that declines unconditionally would
    pass every mutation test below while reusing nothing."""
    keys = _record(out_dir)
    assert J.stored_remote_address(out_dir, **keys) is not None


@pytest.mark.parametrize(
    "key", sorted(REUSE_CONTRACTS["run_journal.stored_remote_address"]))
def test_changing_one_key_alone_declines_the_reuse(out_dir, key):
    """Each key, mutated by itself, with the others held identical.

    Mutating one at a time is the point: a lookup that compared only the first
    key would still decline when *everything* changed, and a test that changed
    everything at once would call that protection.
    """
    keys = _record(out_dir)
    changed = dict(keys, **{key: keys[key] + "-changed"})
    assert J.stored_remote_address(out_dir, **changed) is None, (
        f"{key} is recorded but not compared. It exists because "
        f"{REUSE_CONTRACTS['run_journal.stored_remote_address'][key]}.")


@pytest.mark.parametrize(
    "key", sorted(REUSE_CONTRACTS["run_journal.stored_remote_address"]))
def test_an_unknown_key_on_either_side_declines(out_dir, key):
    """An empty key is "I could not determine this", never "it matched".

    ``source_fingerprint`` returns ``""`` for an input directory it cannot
    read, and a record written before a key existed carries ``""`` for it. Both
    are the absence of evidence, and treating absence as agreement is how a
    reuse decision fires in exactly the situation nobody understood.
    """
    keys = _record(out_dir, **{key: ""})
    assert J.stored_remote_address(out_dir, **dict(keys, **{key: ""})) is None
    assert J.stored_remote_address(out_dir, **dict(keys, **{key: "x"})) is None


# ── the completeness of one key, rather than the roster of keys ─────────────

def _host(**kw):
    from services.remote_runner import RemoteHost
    base = dict(
        name="test", ssh_alias="test-host", remote_project_dir="~/allwell",
        remote_staging_dir="~/.allwell-staging", remote_python="/opt/python",
        cpu_only=False, extra_args=(), cache_budget_gb=10,
        measured_bandwidth_mbps=100.0,
    )
    base.update(kw)
    return RemoteHost(**base)


_LOCAL_ARGV = ["/usr/bin/python", "/p/process_microscopy.py",
               "--input_dir", "/in", "--output_dir", "/out",
               "--nuclear_token", "DAPI"]


@pytest.mark.parametrize("covered", sorted(ARGV_DIGEST_MUST_COVER))
def test_the_digest_covers_what_the_host_adds_to_the_argv(covered):
    """Each host-side addition, alone, must move the digest and the run id.

    ``argv_digest`` is computed from the local argv, and ``_build_remote_argv``
    appends these afterwards — so before this they were invisible to both the
    reuse key and the remote address. A run with a different segmentation
    threshold found the previous run's outputs complete and fetched them back.
    """
    from services.remote_runner import (
        OutputFilter, RemoteRunner, _compute_run_id, pipeline_argv_digest,
    )

    plain = RemoteRunner(_host())
    variants = {
        "--no_save_tophat": (RemoteRunner(_host()), OutputFilter.CSV_ONLY),
        "host.extra_args": (
            RemoteRunner(_host(extra_args=("--min_nucleus_area_px", "500"))),
            OutputFilter.FULL),
        "--cpu_only": (RemoteRunner(_host(cpu_only=True)), OutputFilter.FULL),
    }
    other, other_filter = variants[covered]

    base = plain._host_argv_extras(OutputFilter.FULL)
    changed = other._host_argv_extras(other_filter)
    assert base != changed, f"{covered} did not reach _host_argv_extras"

    why = ARGV_DIGEST_MUST_COVER[covered]
    assert (pipeline_argv_digest(_LOCAL_ARGV, extras=base)
            != pipeline_argv_digest(_LOCAL_ARGV, extras=changed)), (
        f"{covered} does not move the reuse key, and it must: {why}")
    assert (_compute_run_id("k" * 32, _LOCAL_ARGV, extras=base)
            != _compute_run_id("k" * 32, _LOCAL_ARGV, extras=changed)), (
        f"{covered} does not move the remote address, and it must: {why}")


def test_the_launched_command_and_the_digest_read_the_same_extras():
    """One definition, two callers — the shape §7.5 #5 exists for.

    If ``_build_remote_argv`` grew a flag the digest did not, the two would
    disagree again in exactly the direction that reuses stale output.
    """
    from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner

    runner = RemoteRunner(_host(cpu_only=True,
                                extra_args=("--min_nucleus_area_px", "500")))
    extras = runner._host_argv_extras(OutputFilter.CSV_ONLY)
    launched = runner._build_remote_argv(
        list(_LOCAL_ARGV), "/r/in", "/r/out", OutputFilter.CSV_ONLY,
        LocalInputMode.ZIP)
    missing = [e for e in extras if e not in launched]
    assert not missing, (
        f"the digest folds in {missing}, which the launched command does not")
