"""Send a remote nanowell run the tile registration this computer already holds for its wells.

A nanowell well's tiles are registered (placed against each other) before anything is measured, and the result is
kept in its grid record, ``nanowells/<well>_grid.json``, keyed on everything it depends on
(``pipeline.nanowell_run.registration_key``: the algorithm, the acquisition record, every tile's pixels, the
geometry). A run reuses a stored placement whose key matches instead of registering again
(``pipeline.nanowell_task.stored_placements`` / ``nanowell_run.stored_placement``), reading it from the folder named by
``--nanowell_registration_from`` and then from its own output folder.

A remote run had neither: the host's output folder is a fresh one, and ``--nanowell_registration_from`` names a
folder on this computer, so ``RemoteRunner._build_remote_argv`` drops it. Re-running a well on a host therefore
registered every tile again, however many times the well had been registered before. So before a remote nanowell
run starts, :func:`send_registration` uploads, for each well the run sends, the grid record of the first local folder
that has one — the Reuse registration folder, then the run's own output folder, the order the pipeline reads them
in — into ``<run_dir>/reg/nanowells/``, and the run is given ``--nanowell_registration_from <run_dir>/reg``.

Nothing is trusted for having been sent: the host takes a stored placement only when its key matches what it would
compute from the images it has, so a record from other images, another plate or an older algorithm is ignored there
exactly as it would be here. A failed upload costs only the reuse, never the run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from services.remote_run_id import _parse_argv_flags
from util.aw_safe import swallow

#: The pipeline's nanowell folder (``pipeline.nanowell_task.NANOWELL_DIR``), restated so this module stays importable
#: without the pipeline package's heavier imports.
NANOWELL_DIR = "nanowells"
REG_DIR = "reg"


def local_grids(pipeline_argv, wells: Iterable[str]) -> list[Path]:
    """Each of *wells*' grid record on this computer, from the first folder that has one: the Reuse registration
    folder (``--nanowell_registration_from``), then the run's output folder. Empty for a run that is not a nanowell
    run."""
    if "--nanowell_format" not in [str(a) for a in pipeline_argv]:
        return []
    flags = _parse_argv_flags(pipeline_argv, multi=set(),
                              single={"--nanowell_registration_from", "--output_dir"})
    folders = [Path(f).expanduser() for f in (flags.get("--nanowell_registration_from"), flags.get("--output_dir"))
               if f]
    found = []
    for well in sorted(set(wells)):
        for folder in folders:
            path = folder / NANOWELL_DIR / f"{well}_grid.json"
            if path.is_file():
                found.append(path)
                break
    return found


def send_registration(cache, run_dir: str, pipeline_argv, wells: Iterable[str],
                      log: Callable[[str], None]) -> str:
    """Upload *wells*' local grid records into ``<run_dir>/reg/nanowells/``: the folder to pass the host's run as
    ``--nanowell_registration_from``, or ``""`` when there was nothing to send or it could not be sent."""
    grids = local_grids(pipeline_argv, wells)
    if not grids:
        return ""
    reg = f"{run_dir}/{REG_DIR}"
    with swallow("sending the wells' stored registration to the remote"):
        cache._ssh_run(["mkdir", "-p", f"{reg}/{NANOWELL_DIR}"], check=True)
        # No log_fn: these are not wells, and the progress view reads every "↑" line as one.
        _sent, _elapsed, rc = cache.upload_subset(grids, f"{reg}/{NANOWELL_DIR}")
        if rc == 0:
            log(f"[remote] stored registration sent for {len(grids)} well(s); the host reuses each placement whose "
                f"images and settings still match\n")
            return reg
        log(f"[remote] warn: the wells' stored registration could not be sent (rsync rc={rc}); the host registers "
            f"their tiles again\n")
    return ""
