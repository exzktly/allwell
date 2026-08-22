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
    },
}


def _record(out_dir, **overrides):
    keys = {"argv_digest": "argv-1", "source_key": "src-1"}
    keys.update(overrides)
    J.record_remote(
        out_dir, run_id="run-1", cache_key="k1", host_alias="hpc",
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
