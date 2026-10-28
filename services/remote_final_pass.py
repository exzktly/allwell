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

from services.remote_commands import BATCH_REANCHOR, BATCH_SKIP, HOLD_DIR
from services.remote_fetch_nanowells import held_wells
from services.remote_handoff import hand_off_and_wait, raise_flag
from services.remote_run_id import remote_run_dir
from util.well_token import canonical_well_label


def adopt_earlier_holds(proc, run_dir: str) -> None:
    """Make a nanowell run's ``keep/``; for a resumed one, move into it the wells an earlier attempt held.

    A resume has its own run directory (its staged archives, so its run id, differ) but writes into the earlier
    attempt's output directory (``remote_batch_resume.reattach_output_dir``), where the held wells' outputs still are
    — they were kept for the pass that attempt never reached. Those wells are finished, so no batch here runs them
    again; without this their archives sit in the earlier directory and their unaddressed timepoints stay so.
    """
    if not getattr(proc, "_final_pass", False):
        return
    run, here, ssh = proc._fetcher.run, f"{run_dir}/{HOLD_DIR}", proc._runner._cache._ssh_run
    ssh(["mkdir", "-p", here], check=False)
    if not getattr(run, "reattached", False):
        return
    earlier = f"{posixpath.dirname(run.remote_output_dir.rstrip('/'))}/{HOLD_DIR}"
    ssh(["find", earlier, "-maxdepth", "1", "-name", "*.zip", "-exec", "mv", "-t", here, "{}", "+"], check=False)


def finish_with_final_pass(proc, n_batches: int) -> int | None:
    """After every data batch has run: the final pass, for a run that has one (``proc._final_pass``). Returns its
    exit code — ``0`` when there is none — or ``None`` when the driver went away.

    A non-zero code is not a batch's failure: every well has its results from its batch. The pass's wells are then
    not released, so their remote copies stay for verification and for a later run's pass. A suspend that landed
    during the last data batch runs no pass: the held wells stay in ``keep/``, and the resumed run's pass takes them.
    """
    if not getattr(proc, "_final_pass", False):
        return 0
    if proc._suspending.is_set():
        proc._log("[remote] suspended before the end-of-run re-anchoring; the wells held for it stay on the remote "
                  "for the resumed run's\n")
        return 0
    rc = run_final_pass(proc, n_batches)
    if rc:
        proc._log(f"[remote] the end-of-run re-anchoring exited {rc}; the wells it held keep their results from "
                  f"their batches\n")
    return rc


def run_final_pass(proc, n_batches: int) -> int | None:
    """Stage, run and release the driver's batch ``n_batches + 1`` over the archives the batches held."""
    index = n_batches + 1
    keep, staged = f"{remote_run_dir(proc._runner.host, proc._run_id)}/{HOLD_DIR}", proc._batch_staging_dir(index)
    ssh = proc._runner._cache._ssh_run
    ssh(["rm", "-rf", staged], check=False)
    names = sorted(held_wells(ssh, keep) or ())
    moved = bool(names) and getattr(ssh(["mv", keep, staged], check=False), "returncode", 1) == 0
    if moved and not _carry_the_record(proc, names, staged):
        names, moved = [], False
    if moved and not raise_flag(proc, proc._batch_flag(BATCH_REANCHOR, index)):
        # Without its flag the driver reports the pass done without running it, and the held wells would be
        # released as if re-anchored. Moved back to `keep/`, they wait there for a later run's pass.
        ssh(["mv", staged, keep], check=False)
        proc._log("[remote] ERROR could not flag the end-of-run re-anchoring; the held wells stay on the remote "
                  "for a later run's\n")
        names = []
    elif moved:
        proc._log(f"[remote] end-of-run re-anchoring: {len(names)} held well(s) — "
                  f"{', '.join(names[:8])}{' …' if len(names) > 8 else ''}\n")
    else:
        raise_flag(proc, proc._batch_flag(BATCH_SKIP, index))     # missing, the driver skips the pass all the same
        proc._log("[remote] end-of-run re-anchoring: no well was held for it\n" if not names else
                  "[remote] ERROR could not stage the held wells for the end-of-run re-anchoring; their unaddressed "
                  "timepoints stay unaddressed\n")
        names = []
    rc = hand_off_and_wait(proc, index, index)
    if rc is None:
        return None
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
