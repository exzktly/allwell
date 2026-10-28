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
    """Make a nanowell run's ``keep/``; for a resumed one, move into it the wells an earlier attempt held. ``False``
    (an ERROR has said so) when either could not be done, and the run should stop before it launches anything.

    A resume has its own run directory (its staged archives, so its run id, differ) but writes into the earlier
    attempt's output directory (``remote_batch_resume.reattach_output_dir``), where the held wells' outputs still are
    — they were kept for the pass that attempt never reached. Those wells are finished, so no batch here runs them
    again; without this their archives sit in the earlier directory and their unaddressed timepoints stay so.
    """
    if not getattr(proc, "_final_pass", False):
        return True
    run, here = proc._fetcher.run, f"{run_dir}/{HOLD_DIR}"
    then = "stopping before anything is launched"
    if not checked(proc, ["mkdir", "-p", here], f"create {here}, where the batches hold wells for the end-of-run "
                   f"re-anchoring", then=then):
        return False
    if not getattr(run, "reattached", False):
        return True
    earlier = f"{posixpath.dirname(run.remote_output_dir.rstrip('/'))}/{HOLD_DIR}"
    # `find` over a directory that is not there fails, and an earlier attempt that held nothing has none: asked first.
    there = getattr(proc._runner._cache._ssh_run(["test", "-d", earlier], check=False), "returncode", None)
    if there == 1:
        return True
    return checked(proc, ["find", earlier, "-maxdepth", "1", "-name", "*.zip", "-exec", "mv", "-t", here, "{}", "+"],
                   f"move the wells an earlier attempt held for its re-anchoring from {earlier}",
                   then=f"{then}, rather than leaving their unaddressed timepoints unaddressed")


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
