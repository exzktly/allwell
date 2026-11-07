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

import posixpath

import re

from collections.abc import Callable
from pathlib import Path

from pipeline.run_report import cross_build_note
from services import remote_finished_wells as _finished
from services.remote_run_id import _choose_remote_output_dir, _forced_from_argv, _pipeline_launch_is_pointless
from services.slurm_ids import squeue_listing
from util.aw_safe import swallowed
from util.well_token import canonical_well_label


def _sample(wells, n: int = 5) -> str:
    shown = sorted(wells)[:n]
    return ", ".join(shown) + (" …" if len(wells) > n else "")


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
        note = cross_build_note({w: listing.builds.get(w, "") for w in finished})
        if note:
            log(f"[remote] pre-flight: {note}.\n")
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


# ── after a verified run, under Auto-delete ─────────────────────────────────

def holds_pending(cache, run_dir: str) -> bool:
    """Whether *run_dir*'s ``keep/`` still holds archives for an end-of-run re-anchoring nobody ran — a nanowell run
    suspended before its final pass, or one whose pass failed. Unknown (a listing that failed) counts as yes."""
    from services.remote_commands import HOLD_DIR
    from services.remote_transport import _remote_quote

    result = cache._ssh_script(f"ls {_remote_quote(f'{run_dir}/{HOLD_DIR}')}/*.zip >/dev/null 2>&1 "
                               f"&& echo AW_HELD; echo AW_LISTED\n", check=False)
    out = (getattr(result, "stdout", "") or "") if result is not None else ""
    return result is None or getattr(result, "returncode", 1) != 0 or "AW_LISTED" not in out or "AW_HELD" in out


#: Removes the run directory it is given unless a scheduler job recorded there may still be running, its heartbeat
#: is fresh, or either cannot be told. ``squeue`` answering "invalid job id" is a finished job; any other failure of
#: it is "cannot be told", so the directory is kept.
_REMOVE_IF_IDLE = (
    "_aw_clean() {{ d=$1; [ -d \"$d\" ] || {{ echo \"AW_ABSENT $d\"; return; }}; _live=; "
    "_jid=$(cat \"$d/run.pid.slurm\" 2>/dev/null); "
    "ls \"$d/{hold}\"/*.zip >/dev/null 2>&1 && _live=held; "
    # Marked *Keep forever* in the Storage browser (``services.remote_storage``).
    "[ \"$(head -c 4 \"$d/.aw_expiry\" 2>/dev/null)\" = keep ] && _live=kept; "
    "if [ -n \"$_jid\" ]; then if command -v squeue >/dev/null 2>&1; then "
    # (A template for str.format: the brace group's braces are doubled.)
    "_o=$(" + squeue_listing("_jid", "%i").replace("{", "{{").replace("}", "}}") + " 2>&1); _r=$?; "
    "if [ $_r -eq 0 ] && echo \"$_o\" | grep -qx \"$_jid\"; then _live=job; "
    "elif [ $_r -ne 0 ] && ! echo \"$_o\" | grep -qi 'invalid job id'; then _live=unknown; fi; "
    "else _live=unknown; fi; fi; "
    "for _f in \"$d/{heartbeat}\" \"$d/{legacy}\"; do "
    "if [ -e \"$_f\" ]; then _hb=$(stat -c %Y \"$_f\" 2>/dev/null || "
    "stat -f %m \"$_f\" 2>/dev/null || echo 0); "
    "[ $(( $(date +%s) - _hb )) -le {stale} ] && _live=heartbeat; fi; done; "
    "if [ -n \"$_live\" ]; then echo \"AW_LIVE $d $_live\"; else aw_rm \"$_aw_runs\" \"$d\" && echo \"AW_GONE $d\"; fi; }}\n"
)


def remove_if_idle_script(runs_root: str, *, stale: int) -> str:
    """The shell that defines ``_aw_clean <dir>`` (:data:`_REMOVE_IF_IDLE`) with what it needs: the delete layer's
    ``aw_rm``, and *runs_root* as the root every removal must resolve inside. The one way to build it, for the
    cleanup and for its tests."""
    from services.remote_commands import HOLD_DIR
    from services.remote_delete import AW_RM
    from services.remote_run_id import LEGACY_UPLOAD_HEARTBEAT, UPLOAD_HEARTBEAT
    from services.remote_transport import _remote_quote

    return (AW_RM + f"_aw_runs={_remote_quote(runs_root)}\n"
            + _REMOVE_IF_IDLE.format(heartbeat=UPLOAD_HEARTBEAT, legacy=LEGACY_UPLOAD_HEARTBEAT, stale=stale,
                                     hold=HOLD_DIR))


#: A run directory's name: ``run-`` and the run id (``remote_run_id``), letters, digits and dashes.
_RUN_DIR_NAME = re.compile(r"^run-[A-Za-z0-9-]+$")


def confine_to_runs(dirs: dict, runs_root: str) -> dict:
    """The entries of *dirs* (run directory -> its output directory) that are run directories: directly inside
    *runs_root*, named like a run, and passing the delete layer's check (``remote_delete.refusal``). The others are
    what a malformed or foreign journal entry named, and are never removed."""
    from services.remote_delete import refusal

    return {d: out for d, out in dirs.items()
            if not refusal(runs_root, d) and posixpath.dirname(d.rstrip("/")) == runs_root.rstrip("/")
            and _RUN_DIR_NAME.match(posixpath.basename(d.rstrip("/")))}


def clean_earlier_attempts(runner, *, log: Callable[[str], None]) -> None:
    """Remove the run directories earlier attempts of this run left, once this one has verified (tracker F6).

    Each Resume used to leave its predecessor's whole run directory behind — Auto-delete removed only the current
    run's — so every forced or re-addressed resume orphaned an attempt (a nanowell well is about 8 GB) until the
    remote quota filled. The candidates are the attempts the run journal recorded under this run's keys
    (``run_journal.recorded_remote_attempts``). A directory is **kept** when:

    * it is this run's own (``verify_and_cleanup`` decides that one);
    * a scheduler job recorded in it may still be running, or its heartbeat is fresh, or either cannot be told
      (``_REMOVE_IF_IDLE``) — never while that attempt's job may live;
    * its output directory holds a well that is not here, or cannot be listed: work this run did not bring home.
    * it is marked *Keep forever* in the Storage browser (``services.remote_storage``).

    The re-attached output directory this run wrote into is among them, and goes like the rest once verified.
    """
    from services import run_journal
    from services.remote_run_id import remote_run_dir
    from services.remote_transport import _remote_quote
    from services.remote_wait_snippets import UPLOAD_HEARTBEAT_STALE_S

    run = runner._run
    own = remote_run_dir(runner.host, run.run_id)
    try:
        journal = run_journal.read_journal(run.local_output_dir)
        current = dict(getattr(journal, "remote", None) or {})
        attempts = run_journal.recorded_remote_attempts(
            run.local_output_dir, argv_digests=[current.get("argv_digest")],
            source_key=str(current.get("source_key") or ""), host_alias=str(current.get("host_alias") or ""))
    except OSError:
        swallowed(f"reading the run journal in {run.local_output_dir}")
        return
    dirs: dict[str, str] = {}                       # run directory → its output directory
    for a in attempts:
        out = str(a.get("remote_output_dir") or "").rstrip("/")
        inp = str(a.get("remote_input_dir") or "").rstrip("/")
        # posixpath, not pathlib: these are the host's paths, and a WindowsPath turns their slashes round.
        for d in (posixpath.dirname(out) if out else "", posixpath.dirname(inp) if posixpath.basename(inp) == "in" else ""):
            if d and d != own:
                dirs.setdefault(d, out if posixpath.dirname(out) == d else "")
    # The directories come from the local run journal, so they are held to what a run directory is before anything
    # is sent: directly inside ``<staging>/runs``, named like a run (``remote_delete.refusal``), and the host checks
    # again what they resolve to (``aw_rm``). A journal entry naming anything else is reported, never removed.
    runs_root = posixpath.dirname(own)
    confined = confine_to_runs(dirs, runs_root)
    for d in sorted(set(dirs) - set(confined)):
        log(f"[remote] kept {d}: the run journal names it as an earlier attempt, but it is not a run directory "
            f"under {runs_root}, so it is not this app's to remove.\n")
    removable = [d for d, out in confined.items() if _all_brought_home(runner, out, log=log)]
    if not removable:
        return
    script = (remove_if_idle_script(runs_root, stale=UPLOAD_HEARTBEAT_STALE_S)
              + "".join(f"_aw_clean {_remote_quote(d)}\n" for d in removable))
    result = runner._cache._ssh_script(script, check=False)
    for line in ((getattr(result, "stdout", "") or "") if result is not None else "").splitlines():
        if line.startswith("AW_GONE "):
            log(f"[remote] removed an earlier attempt's run dir {line[len('AW_GONE '):]}.\n")
        elif line.startswith("AW_LIVE "):
            where, _, why = line[len("AW_LIVE "):].rpartition(" ")
            log(f"[remote] kept an earlier attempt's run dir {where}: "
                + ("its scheduler job is still running" if why == "job" else
                   "it is still beating its heartbeat" if why == "heartbeat" else
                   "it holds wells waiting for an end-of-run re-anchoring" if why == "held" else
                   "it is marked Keep forever in Manage Remotes → Storage" if why == "kept" else
                   "whether its scheduler job still runs could not be told") + ".\n")


def _all_brought_home(runner, out: str, *, log: Callable[[str], None]) -> bool:
    """Whether every well an earlier attempt's output directory *out* holds output for is here, as a CSV."""
    if not out:
        return True
    listing = _finished.list_remote_outputs(runner._cache, out)
    local = Path(runner._run.local_output_dir)
    missing = None if listing is None else sorted(
        w for w in listing.with_output if not any(local.glob(f"*_{w}.csv*")))
    if missing is None or missing:
        log(f"[remote] kept an earlier attempt's output {out}: "
            + ("it could not be listed" if missing is None else
               f"it holds {len(missing)} well(s) not downloaded here ({_sample(missing)})") + ".\n")
        return False
    return True
