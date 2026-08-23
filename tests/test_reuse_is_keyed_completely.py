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


def test_the_roster_names_every_key_the_decision_actually_takes():
    """The reverse direction, which the roster alone cannot give you.

    ``test_changing_one_key_alone_declines_the_reuse`` proves each *listed* key
    is compared. It says nothing about a key added later — one with a default
    value would be compared by the code, absent from the roster, and carry none
    of the written justification the roster exists to hold. Read off the
    signature, so adding a parameter without a reason to keep it fails here.
    """
    import inspect

    params = inspect.signature(J.stored_remote_address).parameters
    keyed = {name for name, p in params.items()
             if p.kind is inspect.Parameter.KEYWORD_ONLY}
    listed = set(REUSE_CONTRACTS["run_journal.stored_remote_address"])
    assert keyed == listed, (
        f"the decision takes {sorted(keyed)} but the roster names "
        f"{sorted(listed)}. Every key needs its written reason beside it — "
        f"that roster is the only record of what makes the old result wrong.")


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


def _local_argv():
    """The argv ``build_pipeline_args`` really produces, not a sketch of it.

    A hand-written five-flag argv carries none of the flags the form always
    emits, so a host override that *collides* with one — the shape that broke
    the pairing — could not arise in it. Fixtures have to carry the mess of the
    real thing (CLAUDE.md §7.1 #4).
    """
    from pathlib import Path

    from services.pipeline_service import build_pipeline_args

    return build_pipeline_args(
        Path("/p/process_microscopy.py"), Path("/in"), Path("/out"),
        {"nuclear_token": "DAPI", "fluor_tokens": ["GFP"], "csv_prefix": "m",
         "filename_schema": "well_fov_channel", "filename_sep": "_",
         "segmentation_method": "stardist_nuclei"})


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
    assert (pipeline_argv_digest(_local_argv(), extras=base)
            != pipeline_argv_digest(_local_argv(), extras=changed)), (
        f"{covered} does not move the reuse key, and it must: {why}")
    assert (_compute_run_id("k" * 32, _local_argv(), extras=base)
            != _compute_run_id("k" * 32, _local_argv(), extras=changed)), (
        f"{covered} does not move the remote address, and it must: {why}")


def test_the_launched_command_and_the_digest_read_the_same_extras():
    """One definition, two callers — the shape §7.5 #5 exists for.

    If ``_build_remote_argv`` grew a flag the digest did not, the two would
    disagree again in exactly the direction that reuses stale output.
    """
    from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner

    # The override deliberately names a flag ``build_pipeline_args`` always
    # emits, so its value token collides with one already in the argv.
    runner = RemoteRunner(_host(cpu_only=True,
                                extra_args=("--min_nucleus_area_px", "500")))
    extras = runner._host_argv_extras(OutputFilter.CSV_ONLY)
    launched = runner._build_remote_argv(
        list(_local_argv()), "/r/in", "/r/out", OutputFilter.CSV_ONLY,
        LocalInputMode.ZIP)
    # A *subsequence*, not a set of members. A membership test passes on a
    # command whose flag/value pairs have come apart — every token is present
    # and `--min_nucleus_area_px 500` has become a bare `500` that argparse
    # rejects — which is exactly what the first version of this test waved
    # through.
    # De-duplicated first: `_host_argv_extras` can name a bare switch twice
    # when a host lists `--cpu_only` in `extra_args` *and* sets `cpu_only`, and
    # `_build_remote_argv` correctly emits it once. The invariant is about the
    # flags reaching the command, not about their multiplicity.
    wanted, seen = [], set()
    for e in extras:
        if e in _DEDUPED and e in seen:
            continue
        seen.add(e)
        wanted.append(e)
    it = iter(launched)
    matched = [e for e in wanted if any(tok == e for tok in it)]
    assert matched == wanted, (
        f"the digest folds in {wanted}, which the launched command does "
        f"not carry in order — it matched only {matched}.\n"
        f"launched tail: {launched[-10:]}")


def test_the_launched_command_still_parses_with_a_host_override():
    """The end of that: a command the pipeline's own parser accepts.

    The two shapes a host's ``extra_args`` really takes are overriding a flag
    ``build_pipeline_args`` always emits, and the ``--workers 0`` in
    ``remote_config``'s own docstring. Both put a token in the extras that
    already appears in the argv, and a blanket de-duplication dropped it and
    left the value stranded.
    """
    import subprocess
    import sys
    from pathlib import Path

    from services.pipeline_service import build_pipeline_args
    from services.remote_runner import LocalInputMode, OutputFilter, RemoteRunner

    opts = {"nuclear_token": "DAPI", "fluor_tokens": ["GFP"], "csv_prefix": "m",
            "filename_schema": "well_fov_channel", "filename_sep": "_",
            "segmentation_method": "stardist_nuclei"}
    runner = RemoteRunner(_host(cpu_only=True,
                                extra_args=("--min_nucleus_area_px", "500")))
    local = build_pipeline_args(Path("/p/pm.py"), Path("/in"), Path("/out"), opts)
    out = runner._build_remote_argv(list(local), "/r/in", "/r/out",
                                    OutputFilter.CSV_ONLY, LocalInputMode.ZIP)

    # The override must survive as a pair, and it must be the last word on the
    # flag — a host override that lands before the form's own value is not an
    # override.
    assert out[-3:] == ["--min_nucleus_area_px", "500", "--cpu_only"], out[-6:]

    start = next(i for i, a in enumerate(out) if a == "--input_dir")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).parent.parent /
                             "process_microscopy.py"), *out[start:], "--help"],
        capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"the launched command does not parse: {proc.stderr.strip()[-200:]}")


# ── the half-swept sibling ──────────────────────────────────────────────────

#: The two functions that turn a configuration into an address or a reuse key.
#: Every call to either in ``services/remote_runner.py`` must fold in the
#: host-side additions, because a call that does not produces a *smaller* key
#: than its siblings — and a smaller key matches configurations it should not.
_KEYING_CALLS = ("_compute_run_id", "pipeline_argv_digest")

#: The host additions that are bare switches, mirrored from
#: ``RemoteRunner._DEDUPED_HOST_FLAGS``. A host may name one in ``extra_args``
#: *and* set the field that produces it, so the extras list can carry it twice
#: while the command carries it once.
_DEDUPED = frozenset({"--no_save_tophat", "--cpu_only"})


def test_every_keying_call_folds_in_the_host_extras():
    """One call site out of four is enough to re-open the whole defect.

    The first fix updated `spawn`'s two calls and `_BatchedRemoteProcess`'s
    run id, and missed the journal record forty-five lines below it. That made
    the batched digest the smaller key: it equalled a plain FULL run's exactly,
    so a later FULL run re-attached to a batched CSV-only run's directory, was
    found complete, and had output with no tophat images fetched back as its
    own. In the other direction — a host with overrides — the two never matched
    and re-attach silently never fired at all.

    CLAUDE.md §7.5 #5: convert every sibling that answers the same question.
    A grep finds the ones you thought of; this finds the ones you did not.

    It checks the argument's *value*, not merely that the keyword is present:
    ``extras=()`` re-introduced the identical defect and walked past the first
    version of this guard along with 878 other tests.
    """
    import ast
    from pathlib import Path

    source_path = Path(__file__).parent.parent / "services" / "remote_runner.py"
    tree = ast.parse(source_path.read_text(), filename=str(source_path))

    def _is_extras_call(node) -> bool:
        return (isinstance(node, ast.Call)
                and (getattr(node.func, "attr", "")
                     or getattr(node.func, "id", "")) == "_host_argv_extras")

    def _names_holding_extras(fn) -> set[str]:
        """Locals in *fn* bound from ``_host_argv_extras``, plus its own params.

        ``spawn`` binds ``host_extras`` once and passes it twice; and
        ``_compute_run_id`` forwards its own ``extras`` parameter through to
        ``pipeline_argv_digest``. Both are correct, and a guard that could not
        see them would be answered by weakening it rather than by fixing code.
        """
        names = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and _is_extras_call(n.value):
                names.update(t.id for t in n.targets
                             if isinstance(t, ast.Name))
        return names

    # Which enclosing function each call sits in, so a name can be resolved.
    enclosing = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ok_names = _names_holding_extras(fn)
            for n in ast.walk(fn):
                enclosing.setdefault(n, ok_names)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
        if name not in _KEYING_CALLS:
            continue
        extras = next((kw.value for kw in node.keywords if kw.arg == "extras"),
                      None)
        if extras is None:
            offenders.append(
                f"remote_runner.py:{node.lineno} — {name}(…) has no extras=")
            continue
        # The *value*, not just the keyword. `extras=()` types the argument and
        # folds in nothing, so it re-introduces the exact defect this guard was
        # written for — and it did: with that one token, all 878 tests across
        # every file touching this module passed. A guard that proves a keyword
        # was typed proves nothing about the key being complete (§7.5 #5, the
        # sibling spelled differently).
        known = enclosing.get(node, set())
        if not (_is_extras_call(extras)
                or (isinstance(extras, ast.Name) and extras.id in known)):
            offenders.append(
                f"remote_runner.py:{node.lineno} — {name}(…, extras="
                f"{ast.unparse(extras)}) does not come from "
                f"_host_argv_extras(...)")

    assert not offenders, (
        "A call that decides a remote address or a reuse key does not fold in "
        "`RemoteRunner._host_argv_extras`. It therefore produces a smaller key "
        "than its siblings, and a smaller key matches configurations whose "
        "output is not interchangeable — which is how a run fetches another "
        "configuration's numbers and reports success.\n  "
        + "\n  ".join(offenders))


# ── the reuse predicate, driven directly ────────────────────────────────────
#
# Four critical slips in this predicate pass the end-to-end remote harness,
# because that harness exercises `spawn`'s control-file plumbing rather than
# the decisions behind it. These are pure functions and need no harness at all,
# which is exactly why there is no excuse for them being untested: they are the
# highest-stakes decision in the module, and every one of them returns a
# previous run's numbers and reports success when it goes wrong.

def test_a_forced_run_never_re_attaches():
    """`--force` means "compute it again", and re-attaching defeats that.

    A forced run that lands in the previous run's directory finds it full of
    that run's output, the pre-flight check calls the plate complete, the
    pipeline never launches, and yesterday's numbers come back as a successful
    forced re-run — the one outcome a user pressing Force is explicitly trying
    to rule out.
    """
    from services.remote_runner import _choose_remote_output_dir

    fresh, stored = "/stg/runs/run-new/out", "/stg/runs/run-old/out"
    chosen, _reason = _choose_remote_output_dir(
        fresh, stored, forced=True, remote_has_outputs=True)
    assert chosen == fresh, (
        "a forced run re-attached to a populated directory; its outputs would "
        "be found complete and the pipeline would never run")


def test_an_unforced_run_re_attaches_to_the_same_directory():
    """The other half, so the test above cannot pass by never re-attaching."""
    from services.remote_runner import _choose_remote_output_dir

    fresh, stored = "/stg/runs/run-new/out", "/stg/runs/run-old/out"
    chosen, reason = _choose_remote_output_dir(
        fresh, stored, forced=False, remote_has_outputs=True)
    assert chosen == stored and reason


def test_an_empty_recorded_directory_is_not_re_attached_to():
    """A recorded address says where work was *sent*, not that it is there."""
    from services.remote_runner import _choose_remote_output_dir

    fresh, stored = "/stg/runs/run-new/out", "/stg/runs/run-old/out"
    chosen, _reason = _choose_remote_output_dir(
        fresh, stored, forced=False, remote_has_outputs=False)
    assert chosen == fresh


@pytest.mark.parametrize("extras,label", [
    (["--cpu_only"], "cpu_only"),
    (["--no_save_tophat"], "a CSV-only fetch"),
    (["--min_nucleus_area_px", "500"], "a host override"),
])
def test_the_run_id_and_the_digest_both_move_with_the_host_extras(extras, label):
    """Both keys, not just the one that happens to be tested elsewhere.

    `_compute_run_id` decides *where* the run lands and `pipeline_argv_digest`
    decides whether a recorded address is *reusable*. They are computed from
    the same inputs and either one dropping the extras is enough: the first
    collides two configurations in one directory, the second hands back an
    address that belongs to a different one. Both return another
    configuration's numbers reported as this run's.
    """
    from services.remote_runner import _compute_run_id, pipeline_argv_digest

    argv, key = _local_argv(), "k" * 32
    assert (pipeline_argv_digest(argv, extras=[])
            != pipeline_argv_digest(argv, extras=extras)), (
        f"the reuse key does not move for {label}")
    assert (_compute_run_id(key, argv, extras=[])
            != _compute_run_id(key, argv, extras=extras)), (
        f"the remote address does not move for {label}")


def test_the_run_id_is_stable_for_the_same_configuration():
    """The keys must be *discriminating*, not merely different every time.

    A digest that changed on every call would pass every test above and make
    re-attach dead — which is how the feature shipped unreachable once already.
    """
    from services.remote_runner import _compute_run_id, pipeline_argv_digest

    argv, key = _local_argv(), "k" * 32
    assert (pipeline_argv_digest(argv, extras=["--cpu_only"])
            == pipeline_argv_digest(list(argv), extras=["--cpu_only"]))
    assert (_compute_run_id(key, argv, extras=["--cpu_only"])
            == _compute_run_id(key, list(argv), extras=["--cpu_only"]))
