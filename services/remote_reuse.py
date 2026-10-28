"""What a single-shot remote run takes from an earlier one, decided against what the remote holds now.

Two decisions ``RemoteRunner.spawn`` makes before it launches anything, moved here so they share one listing of the
remote (``services.remote_finished_wells.list_remote_outputs``) and one definition of a finished well
(``pipeline.run_report.finished_wells``) with every other remote reader:

* **the pre-flight cache hit** (:func:`preflight_cache_hit`) — whether every uploaded well is *finished* on the
  remote already, so the pipeline is replaced by ``true``. Finished means sealed: a well that failed some groups,
  was wound down part way, or was written by a build that sealed nothing, leaves an ``out.zip`` too, and counting
  those made a run that ended with PARTIAL wells "Remote pipeline completed successfully" on a plain re-run, with
  Auto-delete then cleaning the remote (tracker B2). A listing that fails is "unknown", never "nothing there".
* **re-attaching** (:func:`reattach_output_dir`) — whether the address the run journal recorded is used instead of
  the freshly derived one. That asks whether the recorded directory still holds **any** output, sealed or not:
  re-attaching is what lets a well left part way continue from its checkpoints (``pipeline.fov_checkpoint`` keys
  them on the output directory), and the host's own skip runs every unsealed well again
  (``pipeline.well_skip.finished_output``), so an unsealed well there is work to continue, not a result to keep.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from services import remote_finished_wells as _finished
from services.remote_run_id import _choose_remote_output_dir, _forced_from_argv, _pipeline_launch_is_pointless
from util.aw_safe import swallowed
from util.well_token import canonical_well_label


def _sample(wells, n: int = 5) -> str:
    shown = sorted(wells)[:n]
    return f"{shown}{'…' if len(wells) > n else ''}"


def preflight_cache_hit(runner, zipped, pipeline_argv: list[str], remote_output: str,
                        log: Callable[[str], None]) -> bool:
    """Whether every well of *zipped* is already finished in *remote_output*, so the pipeline need not launch.

    The replacement remote command is ``true``; the verify-and-fetch that follows pulls whatever is missing
    locally, which is the case this exists for: a run that finished its compute and whose download was interrupted.
    """
    expected = len(zipped.well_zips)
    if expected <= 0:
        return False
    log(f"[remote] pre-flight: checking {runner.host.ssh_alias}:{remote_output} for prior outputs\n")
    listing = _finished.list_remote_outputs(runner._cache, remote_output)
    if listing is None:
        log("[remote] pre-flight: could not list the remote output dir — launching the pipeline, which decides "
            "for itself which wells are finished\n")
        return False
    finished = listing.finished
    part_way = listing.part_way
    log(f"[remote] pre-flight: remote output dir has {len(finished)}/{expected} well(s) finished and sealed "
        f"(found wells: {_sample(finished)})"
        + (f"; {len(part_way)} more with output but no completion seal, which run again: {_sample(part_way)}"
           if part_way else "") + "\n")
    uploaded = {canonical_well_label(wz.stem) or wz.stem.upper() for wz in zipped.well_zips}
    if _pipeline_launch_is_pointless(pipeline_argv, expected, set(finished), uploaded=uploaded,
                                     incremental=zipped.incremental):
        log("[remote] FULL CACHE HIT — every well has output already; skipping pipeline launch and proceeding "
            "straight to verify + fetch.\n")
        return True
    if len(finished) >= expected:
        # The remote has everything, but the run was told to reprocess. Say so: the cache-hit line above is the one
        # a user expects here, and its absence is otherwise unexplained.
        log("[remote] remote output dir is already complete, but this run was asked to reprocess — launching the "
            "pipeline anyway (it will overwrite).\n")
    return False


def reattach_output_dir(runner, fresh: str, local_output_dir: Path, pipeline_argv: list[str], *,
                        argv_digest: str, source_key: str, host_alias: str, log: Callable[[str], None]) -> str:
    """The remote output dir to use: *fresh*, or the recorded one when it still holds output.

    Everything decidable without the network is decided by ``remote_run_id._choose_remote_output_dir``; this
    supplies it with the one fact only an ssh round-trip can establish, and keeps that round-trip off the path
    entirely when no stored address exists — every first run of a plate, and every run whose upload was not
    rewritten. A listing that fails holds nothing as far as this is concerned: the fresh directory costs a
    recomputed well, never a wrong one.
    """
    from services import run_journal

    try:
        stored = run_journal.stored_remote_address(
            local_output_dir, argv_digest=argv_digest, source_key=source_key, host_alias=host_alias)
    except OSError:
        swallowed(f"could not read the run journal in {local_output_dir}")
        return fresh
    candidate = str((stored or {}).get("remote_output_dir") or "")
    if not candidate or candidate == fresh:
        return fresh
    # Only --force declines the whole directory. A well named in --force_wells is redone where it lies — the host
    # forces it, the pre-flight never calls a forced run a cache hit, and verification requires its archive be
    # replaced — so one forced well no longer costs every other well its earlier output (tracker F2).
    forced, _forced_wells = _forced_from_argv(pipeline_argv)
    # The listing is skipped for a forced run: it cannot change the answer, and an ssh round-trip whose result is
    # discarded is a wait the user pays for twice — once here, once when the pipeline runs anyway.
    has_outputs = False
    if not forced:
        listing = _finished.list_remote_outputs(runner._cache, candidate)
        has_outputs = listing is not None and bool(listing.with_output)
    chosen, reason = _choose_remote_output_dir(fresh, candidate, forced=forced, remote_has_outputs=has_outputs)
    if reason:
        log(f"[remote] {reason}\n")
    return chosen
