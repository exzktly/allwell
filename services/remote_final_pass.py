"""A batched nanowell run's end-of-run re-anchoring: one pass, after the last data batch.

The re-anchoring re-runs the (well, timepoint)s whose nanowells could not be numbered from the well's own images,
placed from the plate's other wells (``pipeline.nanowell_task.reanchor_plate``). A batched run does it once, after
its last data batch, so the fit sees every batch's wells and the re-runs share the node's cores.

Each data batch holds such wells' archives in ``<run>/keep`` (``--nanowell_hold_dir``) and
``remote_fetch_nanowells.release_batch`` keeps their files; after the last data batch, :func:`run_final_pass` moves
``keep/`` into the driver's one extra batch, uploads the acquisition record and anchors beside them (the pipeline
reads both from the archives' own folder, and ``keep/`` holds archives only), flags it so the driver adds
``--nanowell_reanchor_only`` (``remote_commands._batch_driver_snippet(final_pass=True)``), waits for it, and releases
the wells it held. Split out of ``remote_process`` so the batch loop stays the batch loop; *proc* is its
``_BatchedRemoteProcess``.
"""

from __future__ import annotations

from pathlib import Path

import posixpath

from services.remote_batch_ledger import checked, fail
from services.remote_commands import BATCH_REANCHOR, BATCH_SKIP, HOLD_DIR
from services.remote_fetch_nanowells import held_wells
from services.remote_handoff import hand_off_and_wait, raise_flag
from services.remote_run_id import remote_run_dir
from util.well_token import canonical_well_label


def adopt_earlier_holds(proc, run_dir: str) -> bool:
    """Make a nanowell run's ``keep/`` and take into it every well an earlier attempt held for a pass it never ran.
    ``False`` (an ERROR has said so) when either could not be done, and the run should stop before it launches
    anything.

    A resume may have its own run directory (its staged archives, so its run id, can differ) while it writes into
    the earlier attempt's output directory (``remote_batch_resume.reattach_output_dir``), where the held wells'
    outputs still are; or the same run directory, whose ``keep/`` the earlier attempt filled. Those wells are
    finished, so no batch here runs them again; without this their archives sit where nothing looks and their
    unaddressed timepoints stay so while the output looks complete (tracker F4).

    Looked for in **every** recorded attempt's run directory, not only the re-attached one's, and moved here only
    when the well's output is in this run's output directory, which is what the pass re-runs it from; one whose
    output is elsewhere stays in its attempt's ``keep/``, which then keeps that directory from being cleaned
    (``remote_reuse.clean_earlier_attempts``). A well one of this run's batches runs is left to that batch to hold
    again. The wells taken are recorded on the run (``nanowell_adopted_holds``), so the pass's release and
    verification pull their re-anchored output back (tracker F3). A script that fails moves nothing it did not
    report, and the run stops rather than leave the held wells' timepoints unaddressed.
    """
    if not getattr(proc, "_final_pass", False):
        return True
    from services.remote_transport import _remote_quote

    run, here = proc._fetcher.run, f"{run_dir}/{HOLD_DIR}"
    then = "stopping before anything is launched"
    if not checked(proc, ["mkdir", "-p", here], f"create {here}, where the batches hold wells for the end-of-run "
                   f"re-anchoring", then=then):
        return False
    out = proc._runner._run.remote_output_dir.rstrip("/")     # the run record; the Fetcher holds the same one
    running = sorted({z.stem for batch in (getattr(proc, "_batches", None) or []) for z in batch})
    q_here, q_out = _remote_quote(here), _remote_quote(out)
    lines = [f"_aw_running=' {' '.join(running)} '"]
    # This run's own keep/ first: a stale archive (no output here, or a well a batch of this run runs) goes.
    lines.append(f"for f in {q_here}/*.zip; do [ -f \"$f\" ] || continue; n=${{f##*/}}; w=${{n%.zip}}; "
                 f"case \"$_aw_running\" in *\" $w \"*) rm -f \"$f\"; continue;; esac; "
                 f"if [ -f {q_out}/\"$w\"_out.zip ]; then echo \"allwell-held: $w\"; else rm -f \"$f\"; fi; done")
    for src in _earlier_run_dirs(proc, run_dir):
        lines.append(f"for f in {_remote_quote(f'{src}/{HOLD_DIR}')}/*.zip; do [ -f \"$f\" ] || continue; "
                     f"n=${{f##*/}}; w=${{n%.zip}}; case \"$_aw_running\" in *\" $w \"*) continue;; esac; "
                     f"[ -f {q_out}/\"$w\"_out.zip ] || continue; [ -e {q_here}/\"$n\" ] && continue; "
                     f"mv -f \"$f\" {q_here}/ && echo \"allwell-held: $w\"; done")
    result = proc._runner._cache._ssh_script("\n".join(lines) + "\ntrue\n", check=False)
    taken = sorted({canonical_well_label(line.split(": ", 1)[1].strip()) or line.split(": ", 1)[1].strip().upper()
                    for line in (getattr(result, "stdout", "") or "").splitlines()
                    if line.startswith("allwell-held: ")})
    if not hasattr(run, "nanowell_adopted_holds"):
        run.nanowell_adopted_holds = set()
    run.nanowell_adopted_holds |= set(taken)                  # what did move is this run's, whatever else failed
    if getattr(result, "returncode", None) != 0:
        fail(proc, f"[remote] ERROR could not take over the wells an earlier attempt held for the end-of-run "
                   f"re-anchoring (exit {getattr(result, 'returncode', None)}); {then}, rather than leaving their "
                   f"unaddressed timepoints unaddressed\n")
        return False
    if taken:
        proc._log(f"[remote] {len(taken)} well(s) held by an earlier attempt for the end-of-run re-anchoring are "
                  f"taken into this run's: {', '.join(taken[:12])}{' …' if len(taken) > 12 else ''}\n")
    return True


def _earlier_run_dirs(proc, run_dir: str) -> list[str]:
    """Every run directory an earlier attempt of this run used: the re-attached output's, and each attempt the
    journal recorded under the run's keys (a batched attempt's input is its run directory's ``in/``)."""
    from services import run_journal

    dirs = [posixpath.dirname(proc._runner._run.remote_output_dir.rstrip("/"))]
    keys = getattr(proc, "_journal_keys", None)
    if keys is not None:
        try:
            attempts = run_journal.recorded_remote_attempts(
                proc._local_output_dir, argv_digests=[keys["argv_digest"], *getattr(proc, "_legacy_digests", ())],
                source_key=keys["source_key"], host_alias=keys["host_alias"])
        except OSError:
            attempts = []
        for a in attempts:
            dirs.append(posixpath.dirname(str(a.get("remote_output_dir") or "").rstrip("/")))
            inp = str(a.get("remote_input_dir") or "").rstrip("/")
            if posixpath.basename(inp) == "in":
                dirs.append(posixpath.dirname(inp))
    return [d for d in dict.fromkeys(dirs) if d and d != run_dir]


def finish_with_final_pass(proc, n_batches: int) -> int | None:
    """After every data batch has run: the final pass, for a run that has one (``proc._final_pass``). Returns its
    exit code — ``0`` when there is none — or ``None`` when the driver went away.

    A non-zero code is not a batch's failure: every well has its results from its batch. The pass's wells are then
    not released, so their remote copies stay for verification and for a later run's pass. A pass that held wells
    and could not be run at all is non-zero too, said as such: their unaddressed timepoints stay unaddressed, and a
    run that reported success over them would look exactly like one whose pass ran. A suspend that landed during the
    last data batch runs no pass: the held wells stay in ``keep/``, and the resumed run's pass takes them.
    """
    if not getattr(proc, "_final_pass", False):
        return 0
    if proc._suspending.is_set():
        proc._log("[remote] suspended before the end-of-run re-anchoring; the wells held for it stay on the remote "
                  "for the resumed run's\n")
        return 0
    return run_final_pass(proc, n_batches)


def _stage(proc, keep: str, staged: str) -> list[str] | None:
    """Move the held wells into the pass's batch directory: their names, ``[]`` when none was held, ``None`` when
    the pass cannot be staged (an ERROR has said why, and the wells are where they were)."""
    ssh = proc._runner._cache._ssh_run
    stays = ("the pass is not run: their unaddressed timepoints stay unaddressed, and the held wells stay in "
             "keep/ for a later run's pass")
    if not checked(proc, ["rm", "-rf", staged], f"clear {staged} for the end-of-run re-anchoring", then=stays):
        return None             # `mv keep staged` onto a directory that survived would move keep/ *inside* it
    held = held_wells(ssh, keep)
    if held is None:
        fail(proc, f"[remote] ERROR could not list the wells held in {keep} for the end-of-run re-anchoring; {stays}\n")
        return None
    names = sorted(held)
    if not names:
        return []
    if not checked(proc, ["mv", keep, staged], "stage the held wells for the end-of-run re-anchoring", then=stays):
        return None
    if not _carry_the_record(proc, names, staged):
        _put_back(proc, staged, keep)
        return None
    return names


def _put_back(proc, staged: str, keep: str) -> None:
    checked(proc, ["mv", staged, keep], f"move the held wells back from {staged}",
            then=f"they are left in {staged}, where no later run's re-anchoring looks")


def run_final_pass(proc, n_batches: int) -> int | None:
    """Stage, run and release the driver's batch ``n_batches + 1`` over the archives the batches held."""
    index = n_batches + 1
    keep, staged = f"{remote_run_dir(proc._runner.host, proc._run_id)}/{HOLD_DIR}", proc._batch_staging_dir(index)
    names = _stage(proc, keep, staged)
    if names and not raise_flag(proc, proc._batch_flag(BATCH_REANCHOR, index)):
        # Without its flag the driver reports the pass done without running it, and the held wells would be
        # released as if re-anchored. Moved back to `keep/`, they wait there for a later run's pass.
        _put_back(proc, staged, keep)
        fail(proc, "[remote] ERROR could not flag the end-of-run re-anchoring; the held wells stay on the remote "
                   "for a later run's\n")
        names = None
    if names:
        proc._log(f"[remote] end-of-run re-anchoring: {len(names)} held well(s) — "
                  f"{', '.join(names[:8])}{' …' if len(names) > 8 else ''}\n")
    else:
        # Missing, the driver skips the pass all the same (`_batch_driver_snippet`), and says it skipped it.
        raise_flag(proc, proc._batch_flag(BATCH_SKIP, index))
        if names is not None:
            proc._log("[remote] end-of-run re-anchoring: no well was held for it\n")
    rc = hand_off_and_wait(proc, index, index)
    if rc != 0 and names:
        _back_to_keep(proc, keep, staged)
    if rc is None:
        return None
    if names is None:
        return rc or 1          # held wells whose pass never ran: said above, and not a success
    if rc and names:
        fail(proc, f"[remote] ERROR the end-of-run re-anchoring exited {rc}; the wells it held keep their results "
                   f"from their batches and stay on the remote\n")
    if rc == 0 and names:
        proc._release_nanowell_batch(rc, [Path(f"{name}.zip") for name in names], final=True)
    return rc


def _back_to_keep(proc, keep: str, staged: str) -> None:
    """After a pass that failed, or whose driver went away: put the held archives back in ``keep/``.

    The pass took them out of ``keep/`` into its batch directory, and the driver then swapped that into ``in/``; a
    pass that did not finish left them in either, where no later run looked, and the next run reported "no well was
    held for it" while the timepoints stayed unaddressed (tracker F4). Best-effort: a move that fails leaves them
    where they are, which is what happened before.
    """
    from services.remote_transport import _remote_quote

    q_keep = _remote_quote(keep)
    in_dir = f"{posixpath.dirname(keep.rstrip('/'))}/in"
    script = (f"mkdir -p {q_keep} && for f in {_remote_quote(staged)}/*.zip {_remote_quote(in_dir)}/*.zip; do "
              f"[ -f \"$f\" ] && mv -f \"$f\" {q_keep}/; done; true\n")
    result = proc._runner._cache._ssh_script(script, check=False)
    ok = result is not None and getattr(result, "returncode", 1) == 0
    proc._log("[remote] the wells held for the end-of-run re-anchoring are back in keep/, for a later run's\n" if ok
              else "[remote] WARN could not put the held wells back in keep/ after the re-anchoring did not finish\n")


def _carry_the_record(proc, names, staged) -> bool:
    """Upload beside the held archives what the pipeline reads from their folder: the acquisition record (scoped to
    them) and the anchors. The archives go too, and rsync finds them already there; a well whose local archive is
    missing is staged from the held copy alone. False when the upload failed — the pass would fail every well."""
    wanted = set(names)
    local = [z for z in proc._zipped.well_zips if (canonical_well_label(z.stem) or z.stem.upper()) in wanted]
    if not local:
        return True
    _sent, _elapsed, rc = proc._runner._cache.upload_subset(
        local, staged, log_fn=proc._log, acquisition_input=proc._zipped, stop_event=proc._stop)
    if rc != 0:
        proc._log(f"[remote] ERROR could not send the acquisition record for the end-of-run re-anchoring "
                  f"(rsync rc={rc})\n")
    return rc == 0
