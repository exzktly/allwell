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
from util.settings_digest import KEYED_ON
from tests._repo_scan import parse_source

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
    # The pipeline's own two "we already did this" predicates —
    # `well_skip.output_exists_for_well` and the incremental done-set — both
    # consult one digest, so they are one decision here. Its keys are
    # `util.settings_digest.KEYED_ON` *by reference* rather than restated:
    # a second copy would be a second definition of what makes two runs
    # interchangeable, and the reason each key is in it is written there.
    "settings_record.well_settings_allow_reuse": dict(KEYED_ON),
    # The packed-file converters' resume markers. A conversion publishes
    # each `<dest>/in/<WELL>.zip` atomically, so a cancelled run leaves whole,
    # correct wells and only the rest to do — worth hours on a plate. What
    # makes finishing them dangerous is that two *different* mappings of one
    # source write identical filenames: nothing on disk distinguishes an
    # A01.zip made from scene 0 from one made from scene 7, so only the marker
    # can tell a run that may carry on from one that must refuse. The keys are
    # named per converter rather than shared, because they resolve wells from
    # different inputs and a field that decides the mapping for one is absent
    # from another: only the CZI converter can widen a file to its folder, and
    # only the OME-TIFF one splits a flat frame axis or takes a clicked well
    # order.
    "ome_import.resume_state": {
        "grid_rows": "the imaged block's height; a different one re-labels "
                     "every position after the first row",
        "grid_cols": "the imaged block's width, for the same reason",
        "origin": "which well position 0 is. Off by one well here shifts the "
                  "whole plate and the well count stays right",
        "direction": "whether the stage swept rows or columns; the same "
                     "positions land on a transposed set of wells",
        "serpentine": "snake or raster. A raster read as a snake labels every "
                      "second line backwards, again with the right count",
        "fovs_per_well": "how many positions each well takes, which decides "
                         "where every well after the first begins",
        "frame_order": "how a packed file's flat frame axis splits into "
                       "positions and timepoints — the same frames become "
                       "different images under each reading",
        "experiment": "the first token of every member name, so two values "
                      "produce two disjoint sets of filenames in one archive",
        "well_order": "an explicit click-defined acquisition path, which "
                      "replaces the grid outright when it is set",
        "timepoint_stride": (
            "how many of the timelapse's timepoints are written. The "
            "dangerous direction is the quiet one: a stride-3 run's member "
            "set is a strict SUBSET of a stride-1 run's, and "
            "``well_is_complete`` tests ``issubset``, so without this key a "
            "well already filled by a complete run would be judged done and "
            "left alone while every freshly-converted well beside it held a "
            "third as many timepoints. The plate would go ragged with nothing "
            "raising"),
    },
    "czi_import.resume_state": {
        "well_source": "whether the wells came from the file's scene names or "
                       "from the grid. The same file yields two entirely "
                       "different plates under the two",
        "use_folder": "whether the run converts the selected file alone or "
                      "every .czi beside it as one plate. It is the difference "
                      "between a one-well dataset and a ninety-six-well one "
                      "from the same selection, and the marker's own source "
                      "fingerprint cannot see it — that names the file the "
                      "user picked, which is identical either way. The "
                      "converter's signature carries a fingerprint of the "
                      "whole set for the same reason, so a sibling added or "
                      "re-exported between two runs declines the resume too",
        "grid_rows": "the imaged block's height, when the grid is what maps",
        "grid_cols": "the imaged block's width, for the same reason",
        "origin": "which well position 0 is",
        "direction": "whether the stage swept rows or columns",
        "serpentine": "snake or raster",
        "fovs_per_well": "how many tiles each well takes",
        "experiment": "the first token of every member name",
        "timepoint_stride": (
            "how many of the timelapse's timepoints are written. The "
            "dangerous direction is the quiet one: a stride-3 run's member "
            "set is a strict SUBSET of a stride-1 run's, and "
            "``well_is_complete`` tests ``issubset``, so without this key a "
            "well already filled by a complete run would be judged done and "
            "left alone while every freshly-converted well beside it held a "
            "third as many timepoints. The plate would go ragged with nothing "
            "raising"),
    },
    "nd2_import.resume_state": {
        "well_source": "whether the wells came from the positions' own names "
                       "or from the grid. The same file yields two entirely "
                       "different plates under the two",
        "grid_rows": "the imaged block's height, when the grid is what maps",
        "grid_cols": "the imaged block's width, for the same reason",
        "origin": "which well position 0 is",
        "direction": "whether the stage swept rows or columns",
        "serpentine": "snake or raster",
        "fovs_per_well": "how many positions each well takes. Only a "
                         "grid-mapped file takes it from here — when the "
                         "positions name their wells the file has already "
                         "said — but it decides where every well after the "
                         "first begins whenever it does apply",
        "experiment": "the first token of every member name",
        "timepoint_stride": (
            "how many of the timelapse's timepoints are written. The "
            "dangerous direction is the quiet one: a stride-3 run's member "
            "set is a strict SUBSET of a stride-1 run's, and "
            "``well_is_complete`` tests ``issubset``, so without this key a "
            "well already filled by a complete run would be judged done and "
            "left alone while every freshly-converted well beside it held a "
            "third as many timepoints. The plate would go ragged with nothing "
            "raising"),
    },
    "parquet_cache.read_cached": {
        "st_size": (
            "the CSV's byte count. The pipeline rewrites these files — an "
            "incremental run merges new rows into an existing well — and a "
            "cache that survived that serves the old plate's numbers under the "
            "new plate's name"),
        "st_mtime_ns": (
            "the other half of 'this file changed'. Size alone misses a "
            "rewrite that happens to land on the same byte count, which a "
            "re-run producing the same row count with different values does"),
        "PARSER_VERSION": (
            "the thing being reused is not the CSV, it is load_well_csv's "
            "*output* — a function of the file AND of _STRING_COLS, "
            "_CATEGORY_MAX_UNIQUE_SHARE, the per-channel column rename, the "
            "FOV normalisation, the Included default and the shape_circularity "
            "back-fill. Every one of those has changed at least once. Without "
            "it, adding a derived column means an untouched CSV keeps serving "
            "the frame from before the change: the column is simply absent, "
            "the dropdown offering it does not appear, and nothing says so"),
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
    assert "settings_record.well_settings_allow_reuse" in REUSE_CONTRACTS
    assert "parquet_cache.read_cached" in REUSE_CONTRACTS
    assert "ome_import.resume_state" in REUSE_CONTRACTS
    assert "czi_import.resume_state" in REUSE_CONTRACTS
    assert "nd2_import.resume_state" in REUSE_CONTRACTS


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


# ── the pipeline's own reuse decision ───────────────────────────────────────
#
# Registering a decision in ``REUSE_CONTRACTS`` above is not, by itself, a
# test: every parametrize in this file names ``stored_remote_address``
# explicitly, so an entry added beside it generates nothing and reads in review
# as protection that is not there — the failure mode CLAUDE.md §7.3 opens with.
# These drive the second decision through the same two questions.

_SETTINGS_KEY = "settings_record.well_settings_allow_reuse"


def _settings_out(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    return out


def test_matching_settings_reuse_the_well(tmp_path):
    """The baseline, for the same reason the remote one has one."""
    from pipeline.settings_record import (
        record_well_settings, well_settings_allow_reuse)
    from util.settings_digest import settings_digest

    out = _settings_out(tmp_path)
    key = settings_digest({"segmentation_method": "stardist_nuclei"})
    record_well_settings(out, "A01", key)
    assert well_settings_allow_reuse(out, "A01", key)


@pytest.mark.parametrize("key", sorted(REUSE_CONTRACTS[_SETTINGS_KEY]))
def test_changing_one_setting_alone_declines_the_reuse(tmp_path, key):
    """Every declared key, mutated by itself, driven through the real decision.

    ``tests/test_reuse_is_keyed_on_the_settings.py`` proves each key moves the
    digest. This proves the decision that reads the digest back off disk then
    declines — the two are separate functions in separate modules, and the
    digest moving is worth nothing if the comparison does not happen.
    """
    from pipeline.settings_record import (
        record_well_settings, well_settings_allow_reuse)
    from util.settings_digest import settings_digest

    base = {"segmentation_method": "stardist_nuclei", "nuclear_token": "NIR",
            "fluor_tokens": ["GFP"], "filename_sep": "_"}
    # A value that differs from the baseline's for this key, whatever its type.
    was = base.get(key)
    other = "___changed___" if was is None or isinstance(was, str) else None
    if isinstance(was, list):
        other = [*was, "___changed___"]
    changed = dict(base, **{key: other})

    out = _settings_out(tmp_path)
    record_well_settings(out, "A01", settings_digest(base))

    assert not well_settings_allow_reuse(out, "A01", settings_digest(changed)), (
        f"{key} is in KEYED_ON but the well was reused across a change to it, "
        f"so the old numbers stand under a sidecar advertising the new value. "
        f"It is keyed because: {REUSE_CONTRACTS[_SETTINGS_KEY][key]}.")


def test_an_unrecorded_well_is_grandfathered_deliberately(tmp_path):
    """The one place this decision differs from the remote one, on purpose.

    ``stored_remote_address`` treats an unknown key as a refusal. This treats an
    unrecorded well as acceptable, because refusing would force every user to
    reprocess every plate they have. The cost — a plate written before this
    record existed keeps the old behaviour until its next forced run — is
    stated in the manual, and pinned here so it cannot be changed silently in
    either direction.
    """
    from pipeline.settings_record import well_settings_allow_reuse
    from util.settings_digest import settings_digest

    out = _settings_out(tmp_path)
    assert well_settings_allow_reuse(
        out, "A01", settings_digest({"segmentation_method": "cellpose"}))


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
    tree = parse_source(source_path)

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


#: What this configuration hashes to. Pinned as a literal because re-attach
#: reads a digest journalled by an **earlier launch**, so the instability that
#: kills it is *cross-process* — and two calls inside one interpreter cannot
#: see that. A `hash()` or `id()` of an unordered container anywhere in the
#: filtered-argv path (what an innocuous "order-insensitive" refactor to
#: `frozenset` produces) gives a different digest every launch, re-attach dies
#: silently, and every run re-uploads and recomputes with no error anywhere.
#:
#: Changing this literal deliberately is a real decision, not a test fix: it
#: orphans every address already journalled on a user's machine.
#:
#: The literal moved once, when the digest stopped hashing the launcher. It had
#: been folding in `sys.executable` and the local path to
#: `process_microscopy.py`, so it was not one value at all — it differed
#: between machines, between a frozen bundle and a source run, and after any
#: interpreter change. The pin was therefore *machine-specific*, and passed
#: here while failing in CI; `test_the_launcher_does_not_reach_the_digest`
#: below is the guard that can see the defect a pin cannot.
_PINNED_DIGEST = "ed34655eac9f2335"
_PINNED_RUN_ID = "kkkkkkkkkkkkkkkk-ed34655eac9f2335"


def test_the_keys_are_the_same_on_every_launch():
    """The keys must be *discriminating*, not merely different every time.

    A digest that changed on every call would satisfy every test above and make
    re-attach dead — which is how the feature shipped unreachable once already.
    """
    from services.remote_runner import _compute_run_id, pipeline_argv_digest

    argv, key = _local_argv(), "k" * 32
    assert pipeline_argv_digest(argv, extras=["--cpu_only"]) == _PINNED_DIGEST
    assert _compute_run_id(key, argv, extras=["--cpu_only"]) == _PINNED_RUN_ID


def test_the_launcher_does_not_reach_the_digest():
    """How this machine starts the pipeline is not a property of the run.

    `build_pipeline_args` opens the argv with `sys.executable` and then either
    the local path to `process_microscopy.py` or, in a frozen bundle, the
    `--run-pipeline` sentinel. None of it reaches the worker: the remote runs
    its own interpreter against its own uploaded copy of the script. Hashing it
    keyed the reuse decision on facts that cannot change a byte of the output,
    so an identical plate re-attached or did not depending on whether the user
    launched the .app or ran from source, on where the checkout lived, and on
    which Python was on PATH — each of them re-uploading the whole plate and
    recomputing it with nothing logged.

    The pinned literal above cannot see this, because a pin computed from
    `sys.executable` is itself machine-specific: it passed on the machine that
    wrote it and failed in CI. This test compares the shapes directly.
    """
    from services.remote_runner import pipeline_argv_digest

    flags = ["--input_dir", "/in", "--nuclear_token", "DAPI",
             "--segmentation_method", "stardist_nuclei"]
    source = ["/usr/local/bin/python3", "/home/me/allwell/process_microscopy.py",
              *flags]
    frozen = ["/Applications/All-Well.app/Contents/MacOS/allwell",
              "--run-pipeline", *flags]
    other_machine = ["/opt/hostedtoolcache/Python/3.11.16/x64/bin/python",
                     "/somewhere/else/process_microscopy.py", *flags]

    digests = {name: pipeline_argv_digest(argv) for name, argv in
               (("source", source), ("frozen", frozen),
                ("other machine", other_machine))}
    assert len(set(digests.values())) == 1, (
        "the same configuration hashes differently depending on how the "
        f"pipeline was launched, so re-attach dies on any change to it: "
        f"{digests}")


# ── the per-well frame cache ────────────────────────────────────────────────
#
# Same discipline as the two above, and for the same reason: an entry in the
# roster generates no test by itself. Each key here is mutated alone and the
# read must decline.

_CACHE_KEY = "parquet_cache.read_cached"


def _cached_plate(tmp_path):
    """A well whose frame has been parsed once and cached."""
    import csv as _csv

    from review.data.data_loading import read_well_frame

    path = tmp_path / "measurements_A01.csv"
    with path.open("w", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(["well", "fov", "timepoint_hours", "area_px"])
        for i in range(50):
            writer.writerow(["A01", "1", 0.0, 100.0 + i])
    read_well_frame(path)
    return path


def test_a_matching_key_reuses_the_cached_frame(tmp_path):
    """The baseline. Without it, a read that declined unconditionally would
    pass every mutation below while reusing nothing — which is exactly what a
    review of this cache found, one line and the whole lane still green."""
    pytest.importorskip("pyarrow")
    from review.data import parquet_cache as pc

    plate = _cached_plate(tmp_path)
    assert pc.read_cached(plate) is not None


def test_a_changed_size_declines(tmp_path):
    pytest.importorskip("pyarrow")
    from review.data import parquet_cache as pc

    plate = _cached_plate(tmp_path)
    with plate.open("a") as fh:
        fh.write("A01,1,0.0,999.0\n")
    assert pc.read_cached(plate) is None, REUSE_CONTRACTS[_CACHE_KEY]["st_size"]


def test_a_changed_mtime_at_the_same_size_declines(tmp_path):
    """A re-run that produces the same row count with different values."""
    pytest.importorskip("pyarrow")
    from review.data import parquet_cache as pc

    plate = _cached_plate(tmp_path)
    body = plate.read_text().replace("100.0", "900.0")
    assert len(body) == len(plate.read_text()), "the fixture changed the size"
    plate.write_text(body)
    assert pc.read_cached(plate) is None, (
        REUSE_CONTRACTS[_CACHE_KEY]["st_mtime_ns"])


def test_a_changed_parser_version_declines(tmp_path, monkeypatch):
    """The half no file-stat can see: the same bytes parsing to a new frame."""
    pytest.importorskip("pyarrow")
    from review.data import parquet_cache as pc

    plate = _cached_plate(tmp_path)
    monkeypatch.setattr(pc, "PARSER_VERSION", pc.PARSER_VERSION + 1)
    assert pc.read_cached(plate) is None, (
        REUSE_CONTRACTS[_CACHE_KEY]["PARSER_VERSION"])


# ── the converters' resume markers ─────────────────────────────────────────
#
# Each converter's own test file proves that changing one declared key alone
# declines the resume (``test_ome_import_compute``, ``test_czi_import_compute``,
# ``test_nd2_import_compute``). What those cannot prove is the *reverse*
# direction, because they parametrise over the roster itself: delete a field
# from ``_MAPPING_FIELDS`` and the parametrize simply stops generating a case
# for it, the lane stays green, and a setting that changes the mapping is no
# longer part of the key. That is the roster-against-a-roster problem
# CLAUDE.md §7.5 #10 names, and this is where it is held against the thing it
# is a list of: the converter's own settings dataclass.

#: Settings a converter carries that deliberately do not key its resume, with
#: the reason. An exemption is a row with a written reason, never an absence —
#: a field left out on purpose and one nobody remembered look identical in code
#: six months later.
MAPPING_EXEMPTIONS = {
    "workers": ("the size of the per-well thread pool. It changes how fast "
                "the conversion goes and nothing about what it writes, so "
                "refusing a resume over it would refuse one that was safe"),
    "well_subset": (
        "which wells this run converts. It filters the well *indices* walked "
        "and never the mapping, so a converted well's archive is byte-identical "
        "to the one a whole-plate run would have written — verified by "
        "comparing the two. The signature's question is 'which image lands "
        "under which filename', and the selection does not change that "
        "answer for any file it produces.\n\n"
        "Keying on it would refuse the workflow the setting exists for: "
        "convert six wells today, the rest next week, one dataset. That "
        "top-up is instead permitted by `convert_marker.DONE_MARKER`, which "
        "records the completed run's mapping so a second run can tell its own "
        "dataset from a stranger's plate pointed at the same folder — the "
        "narrower check, on the thing that really does have to match."),
}


@pytest.mark.parametrize("decision,module_path", [
    ("ome_import.resume_state", "datasets.ome_import.ome_import_compute"),
    ("czi_import.resume_state", "datasets.czi_import.czi_import_compute"),
    ("nd2_import.resume_state", "datasets.nd2_import.nd2_import_compute"),
])
def test_the_roster_names_every_setting_that_decides_the_mapping(
        decision, module_path):
    """Read off the settings dataclass, so a new setting fails here by default.

    A converter setting is either part of the key or exempted in writing.
    Adding one and forgetting both is how a resume comes to accept a folder it
    should refuse, and the symptom is a plate that looks complete.
    """
    import dataclasses
    import importlib

    module = importlib.import_module(module_path)
    fields = {f.name for f in dataclasses.fields(module.ConversionParams)}
    keyed = set(module._MAPPING_FIELDS)
    listed = set(REUSE_CONTRACTS[decision])

    assert keyed == listed, (
        f"{module_path} keys on {sorted(keyed)} but the roster names "
        f"{sorted(listed)}. Every key needs its written reason beside it.")
    unaccounted = fields - keyed - set(MAPPING_EXEMPTIONS)
    assert not unaccounted, (
        f"{module_path}.ConversionParams carries {sorted(unaccounted)}, which "
        f"neither keys the resume nor appears in MAPPING_EXEMPTIONS. If the "
        f"setting changes which image lands under which filename it belongs in "
        f"_MAPPING_FIELDS; if it does not, say so in MAPPING_EXEMPTIONS.")
    assert set(MAPPING_EXEMPTIONS) <= fields, (
        "MAPPING_EXEMPTIONS names a setting no converter has any more — an "
        "exemption that stopped describing a real decision.")
