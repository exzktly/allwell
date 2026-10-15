"""Where a remote run lives, and whether an earlier one's work can be reused.

Two questions with one answer between them, which is why they share a module.
``_compute_run_id`` and ``pipeline_argv_digest`` decide the *address* a run
lands on; ``_choose_remote_output_dir`` decides whether an address a previous
run recorded may be used instead. They are keyed on the same facts, and every
defect this subsystem produced around reuse came from one of them being keyed
on less than the other.

``remote_run_dir`` / ``remote_run_paths`` are the single spelling of
``{remote_staging_dir}/runs/{run_id}``, enforced by
``tests/test_one_definition_of_the_run_dir.py`` across the whole repo.

Pure: no ssh, no filesystem, no host state beyond the fields it reads. That is
what lets the highest-stakes predicate in the remote path be tested directly
rather than through a harness — see ``tests/test_reuse_is_keyed_completely.py``,
where four critical slips once passed the entire end-to-end suite.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pipeline.well_skip import _canonical_force_wells
from services.remote_config import RemoteHost

#: The upload handshake files, named here because ``remote_run_paths`` is the
#: only thing that builds their paths. The sentinel is written into the run
#: directory once every byte of the plate has landed; the scheduled job waits
#: for it before starting the pipeline, which is what lets the allocation be
#: requested *while* the upload is still running: on a busy cluster the queue
#: wait and the transfer then overlap instead of running end to end.
UPLOAD_SENTINEL = ".aw_upload_complete"
UPLOAD_HEARTBEAT = ".aw_upload_alive"
#: Touched by the app to ask a running remote pipeline to suspend; the pipeline
#: is told the path with ``--suspend_file`` and polls for it.
SUSPEND_REQUEST = ".aw_suspend_request"

#: What a frozen bundle passes instead of a script path — the launcher routes
#: argv past it into ``process_microscopy.main``. It is launcher plumbing, so
#: the digest skips it exactly as it skips the script path a source run passes.
_LAUNCHER_SENTINEL = "--run-pipeline"


def _without_launcher(pipeline_argv: Sequence[str]) -> list[str]:
    """*pipeline_argv* from its first real flag onward.

    The prefix is `sys.executable` followed by either the local path to
    `process_microscopy.py` or, in a frozen bundle, `--run-pipeline`. Slicing
    at the first flag that is not the sentinel handles both without this
    function needing to know which shape it was handed.
    """
    for n, arg in enumerate(pipeline_argv):
        if arg.startswith("--") and arg != _LAUNCHER_SENTINEL:
            return list(pipeline_argv[n:])
    return []


def pipeline_argv_digest(
    pipeline_argv: list[str], *, extras: Sequence[str] = (),
) -> str:
    """Hash the arguments that decide the *bytes* a run produces.

    Split out of :func:`_compute_run_id` so the run journal can record "this
    plate, configured this way" and a later run can recognise it, without a
    second definition of which flags count. Two runs whose digests differ are
    not producing interchangeable output, whatever else they share.

    Skips the input/output paths (already implied by the cache key, which is
    computed over the input archives) and ``--force`` (a re-processing knob, not
    a change of configuration — which is what lets a forced re-run land in the
    same remote directory and overwrite it).

    Skips the **launcher** too, which is everything before the first real flag:
    ``sys.executable``, and either the local path to ``process_microscopy.py``
    or the frozen bundle's ``--run-pipeline`` sentinel. None of it reaches the
    worker — the remote runs its own interpreter against its own uploaded copy
    of the script — so hashing it keyed the reuse decision on facts about the
    client that cannot change a single byte of the result. It made the digest
    differ between the frozen app and a source run on one machine, and between
    any two machines at all; CI caught the second, which is the only reason the
    first was found. A key that churns for a reason unrelated to the output is
    re-attach dying silently and the whole plate re-uploading, with nothing
    logged anywhere.

    ``extras`` are the flags a *remote* run adds on top of the local argv —
    :meth:`RemoteRunner._host_argv_extras`. They are not in ``pipeline_argv``
    because nothing local produces them, and leaving them out made two runs
    that compute different numbers hash identically. They are appended rather
    than merged, so the digest is stable in their order.

    Skips every flag that cannot change a byte of what the remote writes —
    the ones :data:`util.settings_digest.NOT_KEYED_ON` lists as execution or
    re-processing knobs (:data:`_DIGEST_SKIP_VALUE`, :data:`_DIGEST_SKIP_FLAG`).
    ``--incremental`` and ``--incremental_prior_dir`` are the ones that
    mattered: :meth:`RemoteRunner._build_remote_argv` drops both before the
    host sees the command, yet hashing them made a resumed run (which is always
    incremental) a "differently configured" run than the one it resumed, so the
    journal's recorded address never matched it and the wells that run had
    finished on the server were uploaded and recomputed instead of downloaded
    (``services.remote_recover``). ``--allow_low_disk`` is the one "Load
    previous run" does not restore, so hashing it broke the match for anyone
    who had ticked it.
    """
    return _digest(pipeline_argv, extras, _DIGEST_SKIP_VALUE, _DIGEST_SKIP_FLAG)


#: Flags followed by a value that :func:`pipeline_argv_digest` leaves out. Paths
#: (identity, implied by the cache key) and execution knobs that change only how
#: long a run takes.
_DIGEST_SKIP_VALUE = frozenset({
    "--input_dir", "--output_dir", "--incremental_prior_dir",
    "--workers", "--tf_threads", "--focus_min",
})
#: Bare flags :func:`pipeline_argv_digest` leaves out: re-processing knobs and
#: pre-flight / audit switches that compute nothing.
_DIGEST_SKIP_FLAG = frozenset({
    "--force", "--incremental", "--allow_low_disk", "--no_run_check",
})


def legacy_argv_digests(pipeline_argv: list[str], *,
                        extras: Sequence[str] = ()) -> tuple[str, ...]:
    """The digests an earlier build would have recorded for this configuration.

    Only for *finding* work: a plate whose finished remote output was recorded
    before :func:`pipeline_argv_digest` stopped hashing the execution knobs
    carries a digest the current one cannot produce, and without these the
    download-before-staging step would miss exactly the plates that are stuck
    on the server today. Never used to address a new run. Two shapes, because
    the recording run may or may not itself have been incremental.
    """
    old_values = frozenset({"--input_dir", "--output_dir"})
    old_flags = frozenset({"--force"})
    stripped = _strip(_without_launcher(pipeline_argv),
                      frozenset({"--incremental_prior_dir"}),
                      frozenset({"--incremental"}))
    return tuple(dict.fromkeys((
        _digest(pipeline_argv, extras, old_values, old_flags),
        _digest(stripped, extras, old_values, old_flags),
    )))


def _digest(pipeline_argv, extras, skip_with_value, skip_flags) -> str:
    """Hash *pipeline_argv*, launcher and skipped flags removed, plus *extras*."""
    filtered = _strip(_without_launcher(pipeline_argv), skip_with_value, skip_flags)
    filtered.extend(str(e) for e in extras)
    return hashlib.blake2b("|".join(filtered).encode(), digest_size=8).hexdigest()


def _strip(pipeline_argv, skip_with_value, skip_flags) -> list[str]:
    """*pipeline_argv* without the named flags (and the values that follow them)."""
    filtered: list[str] = []
    i = 0
    while i < len(pipeline_argv):
        a = pipeline_argv[i]
        if a in skip_with_value:
            i += 2
            continue
        if a in skip_flags:
            i += 1
            continue
        filtered.append(a)
        i += 1
    return filtered

def _choose_remote_output_dir(
    fresh: str,
    stored: str,
    *,
    forced: bool,
    remote_has_outputs: bool,
) -> tuple[str, str]:
    """Which remote output directory this run should use, and why.

    A module-level function rather than a branch inside ``spawn`` for two
    reasons: ``spawn`` is already near the complexity ratchet's ceiling, and a
    decision that cannot be called directly cannot be run against fixtures —
    only read, which is how the wrong branch gets described as the app's
    behaviour.

    ``fresh`` is the address derived the usual way, from the staged archives'
    ``(name, size, mtime)`` and the pipeline argv. ``stored`` is the one a
    previous run of this same configuration recorded in the output directory's
    journal, or ``""``. They differ when the previous run's upload was
    *rewritten* into scratch — a channel filter that cleared
    ``REWRITE_MIN_SAVING``, or an incremental split — because a fresh staging
    copy carries a new mtime every run and so keys differently every run. The
    work that run computed is then sitting in a directory nothing will look in
    again.

    Returns ``(chosen, reason)``. The stored address wins only when it exists,
    differs, is not overridden by a forced run, **and** the caller has confirmed
    it still holds output: the journal says where work was sent, which is not
    the same claim as the work still being there. The journal proposes; the
    remote listing decides.
    """
    if not stored or stored == fresh:
        return fresh, ""
    if forced:
        return fresh, (
            "a recorded prior output dir was ignored: this run was asked to "
            "reprocess")
    if not remote_has_outputs:
        return fresh, (
            f"a prior run recorded {stored}, but it holds no output now — "
            f"using {fresh}")
    return stored, (
        f"re-attaching to {stored}, recorded by a prior run of this plate "
        f"(the freshly-derived address would have been {fresh})")

def _compute_run_id(
    cache_key: str, pipeline_argv: list[str], *, extras: Sequence[str] = (),
) -> str:
    """Deterministic run_id from input cache_key + pipeline-args
    hash. Same input + same pipeline config → same run_id, so
    re-running lands in the existing remote output dir and the
    pipeline's own ``output_exists_for_well`` logic skips wells
    whose ``{well}_out.zip`` already exists.

    v5.2.25: skips per-run-irrelevant flags so the cache slot stays
    stable across a forced-reprocess re-run, and so input/output
    dir paths don't churn the hash. ``--force`` users will hit the
    same dir and overwrite the cached outputs (which is what
    ``--force`` is supposed to mean).

    Which flags count is :func:`pipeline_argv_digest`'s answer, not a second
    one kept here: the run journal records that digest so a later run can
    recognise the same configuration, and two spellings of "same config"
    would have the journal offering an address the run id says belongs
    somewhere else.

    ``extras`` carries the host- and filter-level flags a remote run appends to
    the local argv, so a plate run against a CPU-only host, or with a host
    override, or with the Fetch filter changed, addresses its own directory
    instead of finding the other configuration's outputs already complete
    there."""
    return _run_id_body(cache_key, pipeline_argv_digest(pipeline_argv, extras=extras))


def _run_id_body(cache_key: str, argv_digest: str) -> str:
    """The one spelling of a run id from its two keys (``"run-"`` is prefixed by
    the caller). ``services.remote_recover`` recomputes earlier runs' directories
    from it, so the two cannot come to name different places."""
    return f"{cache_key[:16]}-{argv_digest}"

@dataclass(frozen=True)
class RemoteRunPaths:
    """Every remote path one run owns, derived once.

    ``out`` is the only one a re-attaching run ever re-points; the other three
    stay under this run's own ``run_id``, because
    :meth:`RemoteRunner._sweep_orphaned_allocation` finds a crashed session's
    scheduler job by recomputing exactly that address.
    """

    run_dir: str
    out: str
    pidfile: str
    sentinel: str
    heartbeat: str
    #: Under the run's own directory rather than ``out`` so a re-attached run,
    #: whose ``out`` is borrowed from an earlier one, still owns its request.
    suspend_request: str

def remote_run_dir(host: "RemoteHost", run_id: str) -> str:
    """``{remote_staging_dir}/runs/{run_id}`` — the one place this is spelled.

    It was spelled nine times, across three classes, while the *dataset* address
    right beside it had exactly one definition
    (:meth:`DatasetCache.remote_dataset_dir`). Every change to "where does a run
    live" was therefore an N-site sweep with no mechanical way to discover N,
    and the defects that produced were not hypothetical: a re-attaching run's
    own directory went uncreated because one of the ten was the output path and
    the ``mkdir`` used it, and the cleanup that followed named a different one
    again. ``tests/test_one_definition_of_the_run_dir.py`` holds the rule.
    """
    return f"{host.remote_staging_dir}/runs/{run_id}"

def remote_run_paths(host: "RemoteHost", run_id: str) -> RemoteRunPaths:
    """The paths a run owns, so they cannot disagree about their parent."""
    run_dir = remote_run_dir(host, run_id)
    return RemoteRunPaths(
        run_dir=run_dir,
        out=f"{run_dir}/out",
        pidfile=f"{run_dir}/run.pid",
        sentinel=f"{run_dir}/{UPLOAD_SENTINEL}",
        heartbeat=f"{run_dir}/{UPLOAD_HEARTBEAT}",
        suspend_request=f"{run_dir}/{SUSPEND_REQUEST}",
    )

def _forced_from_argv(pipeline_argv: list[str]) -> tuple[bool, frozenset[str]]:
    """The run's re-processing intent, read back off the argv it will run.

    The argv is the only place both spawn paths agree on what the user asked
    for, and the batched runner already reads ``--force`` back out of it the
    same way. Returns ``(force_all, forced_wells)``.
    """
    force_all = "--force" in pipeline_argv
    wells: list[str] = []
    if "--force_wells" in pipeline_argv:
        i = pipeline_argv.index("--force_wells") + 1
        while i < len(pipeline_argv) and not pipeline_argv[i].startswith("--"):
            wells.append(pipeline_argv[i])
            i += 1
    return force_all, _canonical_force_wells(wells)

def _local_input_dir_from_argv(argv) -> "Path | None":
    """The ``--input_dir`` the local argv named, before the remote rewrite.

    This is the user's own plate directory. For an incremental run the *staged*
    upload is a different, partial directory, and a completeness check pointed
    at that one would call a plate finished on the strength of the slice that
    happened to be re-uploaded.
    """
    argv = list(argv or [])
    for i, arg in enumerate(argv):
        if arg == "--input_dir" and i + 1 < len(argv):
            return Path(argv[i + 1])
        if arg.startswith("--input_dir="):
            return Path(arg.split("=", 1)[1])
    return None

def _parse_argv_flags(argv, multi: set[str], single: set[str]) -> dict:
    """Read *multi*- and *single*-valued flags out of a pipeline argv.

    A multi-valued flag (``--fluor_tokens GFP mCherry DAPI``) consumes entries
    until the next ``--``. A plain ``next(it)`` picks up only the FIRST, which is
    how the batched-remote threshold pass came to see one channel of many.
    """
    out: dict = {}
    argv = [str(a) for a in argv]
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in multi:
            values: list[str] = []
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                values.append(argv[i])
                i += 1
            out[arg] = values
            continue
        if arg in single:
            out[arg] = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
            continue
        i += 1
    return out


def _pipeline_launch_is_pointless(
    pipeline_argv: list[str], expected_well_count: int, completed_remote: set[str],
    *, uploaded: set[str] | None = None, incremental: bool = False,
) -> bool:
    """Whether the remote pipeline can be replaced with a no-op.

    True only when the remote already holds output for every well **and the run
    has not been asked to reprocess anything**. That second clause is the whole
    point of this predicate existing.

    ``_compute_run_id`` deliberately leaves ``--force`` out of its hash so a
    forced re-run lands in the *same* remote output dir and overwrites it —
    which is exactly what stocks that dir with a complete set of ``_out.zip``
    files from the previous run. Without a force term here, those two reasonable
    decisions compose into a guarantee: a forced run always resolves to a dir
    that always looks like a cache hit, so the pipeline is replaced by ``true``,
    nothing is recomputed, nothing is fetched, and every local artefact keeps
    the previous run's bytes and mtime while the run reports success. The
    stronger the user's demand to reprocess, the more certain it was to be
    ignored.

    **The wells, not their number, and never an incremental run.** A count
    compared the uploaded plate with a directory that held as many *other*
    wells' outputs — a resumed run uploading C01 and D01 into a directory
    holding B01 and C01 was a "full cache hit". And an incremental run's upload
    is a split of the groups still missing, which an out.zip's existence says
    nothing about: a partial well's archive is exactly what it has to add to.
    """
    if expected_well_count <= 0 or incremental:
        return False
    force_all, forced_wells = _forced_from_argv(pipeline_argv)
    if force_all or forced_wells:
        return False
    if uploaded is not None:
        return bool(uploaded) and uploaded <= completed_remote
    return len(completed_remote) >= expected_well_count
