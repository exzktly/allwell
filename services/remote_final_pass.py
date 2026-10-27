"""A batched nanowell run's end-of-run re-anchoring: one pass, after the last data batch.

The re-anchoring re-runs the (well, timepoint)s whose nanowells could not be numbered from the well's own images,
placed from the plate's other wells (``pipeline.nanowell_task.reanchor_plate``). It used to run at the end of every
batch, alone in the pipeline's process while the node's other cores waited, and from only that batch's wells — whose
archives the next batch then deleted — so a well its batch could not anchor stayed unaddressed even when later
batches would have supplied the anchors.

Now each data batch holds such wells' archives in ``<run>/keep`` (``--nanowell_hold_dir``) and
``remote_fetch_nanowells.release_batch`` keeps their files; after the last data batch, :func:`run_final_pass` moves
``keep/`` into the driver's one extra batch, flags it so the driver adds ``--nanowell_reanchor_only``
(``remote_commands._batch_driver_snippet(final_pass=True)``), waits for it, and releases the wells it held. Split out
of ``remote_process`` so the batch loop stays the batch loop; *proc* is its ``_BatchedRemoteProcess``.
"""

from __future__ import annotations

from pathlib import Path

from services.remote_commands import BATCH_READY, BATCH_REANCHOR, BATCH_SKIP, HOLD_DIR
from services.remote_run_id import remote_run_dir


def finish_with_final_pass(proc, n_batches: int) -> int | None:
    """After every data batch has run: the final pass, for a run that has one (``proc._final_pass``). Returns its
    exit code — ``0`` when there is none — or ``None`` when the driver went away.

    A non-zero code is not a batch's failure: every well has its results from its batch. It is still the run's code,
    so verification keeps the remote copies the pass held rather than cleaning them up.
    """
    if not getattr(proc, "_final_pass", False):
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
    listed = ssh(["find", keep, "-maxdepth", "1", "-name", "*.zip", "-print"], check=False)
    names = sorted(Path(line.strip()).stem for line in (getattr(listed, "stdout", "") or "").splitlines()
                   if line.strip()) if listed is not None and listed.returncode == 0 else []
    moved = bool(names) and getattr(ssh(["mv", keep, staged], check=False), "returncode", 1) == 0
    if moved:
        ssh(["touch", proc._batch_flag(BATCH_REANCHOR, index)], check=False)
        proc._log(f"[remote] end-of-run re-anchoring: {len(names)} held well(s) — "
                  f"{', '.join(names[:8])}{' …' if len(names) > 8 else ''}\n")
    else:
        ssh(["touch", proc._batch_flag(BATCH_SKIP, index)], check=False)
        proc._log("[remote] end-of-run re-anchoring: no well was held for it\n" if not names else
                  "[remote] ERROR could not stage the held wells for the end-of-run re-anchoring; their unaddressed "
                  "timepoints stay unaddressed\n")
        names = []
    ssh(["touch", proc._batch_flag(BATCH_READY, index)], check=False)
    rc = proc._await_batch(index, index)
    proc._release_nanowell_batch(rc, [Path(f"{name}.zip") for name in names], final=True)
    return rc
