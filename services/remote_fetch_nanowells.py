"""Bring a nanowell well's tables back from the remote host (tracker P9).

``Fetcher._fetch_one`` pulls ``<well>_out.zip`` and the per-cell CSV. A nanowell run also writes
``nanowells/<well>.csv`` (one row per site and timepoint) and ``nanowells/<well>_grid.json`` (the
registration, lattice and status of every timepoint); without them the run's real output stays on the
worker host and the fetched dataset has cells with no sites. They are separate from the cell CSV
because Review loads every top-level ``*.csv`` as a well's cell table.

**A nanowell run's wells are not finished when they say so.** The re-anchoring at the end of each pipeline
invocation (``pipeline.nanowell_task.reanchor_plate``) re-runs the (well, timepoint)s the rest of the plate can
address, incrementally, from the well's input archive and its earlier output. So nothing of a well is deleted from
the remote as it is fetched (:func:`well_files_to_delete`).

It is deleted when its **batch** ends instead (:func:`release_batch`). The re-anchoring re-runs only the wells of
the invocation it ends — a well from an earlier batch is "not part of this run, so it was not re-run" — and what it
reads of the other wells is their grid records under ``nanowells/``, which are small and stay on the remote for the
whole run. Once a batch's pipeline has exited, its wells' input archives, ``<well>_out.zip`` and cell CSVs are
therefore never read again: the wells the re-anchoring re-ran are pulled again, and then all of them are deleted.
That is what lets the batch size bound a nanowell run's remote disk at all; held until the run ends, a 60-well
plate of 8 GB inputs needs the whole plate's input and output on the remote at once.

At verification the whole ``nanowells/`` directory and every re-anchored well not already released are pulled again
(:func:`pull_nanowell_results`). A well whose tables did not arrive fails verification
(:func:`missing_nanowell_tables`), which keeps the remote copy.

This lives in its own module because ``remote_fetch.py`` is line-budgeted: the hooks there are single calls,
and everything else is here.
"""

from __future__ import annotations

import json
from pathlib import Path

NANOWELL_DIR = "nanowells"


def fetch_nanowell_tables(fetcher, well: str) -> int:
    """Pull ``nanowells/<well>.csv``, ``<well>_grid.json`` and ``<well>_template.npz`` beside the well's other
    output; return how many landed.

    Exact names, never a glob on the well label (``A01*`` would also match ``A010``). A file the
    remote does not have is simply not listed; the run's completeness check is what notices a timepoint
    missing from the grid, so a table that failed to arrive is a finding there, not a silent absence.
    A no-op for a run that is not in nanowell mode.
    """
    run = fetcher.run
    if not run.nanowell:
        return 0
    dest = Path(run.local_output_dir) / NANOWELL_DIR
    pulled = 0
    # The template is what a later incremental run falls back to for a crowded timepoint (tracker C16).
    for name in (f"{well}.csv", f"{well}_grid.json", f"{well}_template.npz"):
        host, files, _bytes = fetcher._list_remote_glob(
            f"{fetcher.host.ssh_alias}:{run.remote_output_dir}/{NANOWELL_DIR}/{name}")
        if files:
            dest.mkdir(parents=True, exist_ok=True)
            pulled += fetcher._pull_listed(host, files, dest)
    return pulled


def well_files_to_delete(run, well: str) -> list[str]:
    """The remote files of *well* the Fetcher may delete once it is fetched: none for a nanowell run, whose
    re-anchoring may re-run the well from them; ``verify_and_cleanup`` removes the run directory at the end."""
    return [] if run.nanowell else _per_well_remote_paths(run, well)


def _per_well_remote_paths(run, well: str) -> list[str]:
    """A well's input archive, output archive and cell CSVs on the remote; never anything under ``nanowells/``."""
    rin, rout = run.remote_input_dir, run.remote_output_dir
    return [f"{rin}/{well}.zip", f"{rout}/{well}_out.zip", f"{rout}/*_{well}.csv", f"{rout}/*_{well}.csv.gz"]


def reanchored_wells(nanowell_dir) -> list[str]:
    """The wells the end-of-run re-anchoring re-ran: those with a timepoint whose origin came from the plate's
    rigid motion (``origin_method == "plate_rigid"``), read from the grid records just pulled.

    Not from the sidecar's ``nanowell.reanchor`` summary: a batched run writes that once per batch, each over the
    last, so it names only the last batch's wells (roll-up review X32). The grid records carry it per well.
    """
    wells = set()
    for path in Path(nanowell_dir).glob("*_grid.json"):
        try:
            grid = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        blocks = (grid.get("timepoints") or {}).values() if isinstance(grid, dict) else ()
        if any(isinstance(b, dict) and b.get("origin_method") == "plate_rigid" for b in blocks):
            wells.add(str(grid.get("well") or path.name[: -len("_grid.json")]))
    return sorted(wells)


def pull_nanowell_results(verifier, *, log) -> None:
    """At verification, for a nanowell run: the whole ``nanowells/`` directory as the run left it, then the cell
    CSV and ``<well>_out.zip`` of every well the re-anchoring re-ran (fetched once already, before it did)."""
    from services.remote_csv import retire_superseded_csvs

    run = verifier._run
    if run is None or not run.nanowell:
        return
    remote, local = f"{verifier.host.ssh_alias}:{run.remote_output_dir}", Path(run.local_output_dir)
    verifier._rsync_optional(f"{remote}/{NANOWELL_DIR}/", local / NANOWELL_DIR, log_fn=log)
    for well in reanchored_wells(local / NANOWELL_DIR):
        if well in run.nanowell_released:          # pulled again and deleted when its batch ended
            continue
        verifier._rsync_optional(f"{remote}/{well}_out.zip", local / f"{well}_out.zip", log_fn=log)
        verifier._rsync_optional(f"{remote}/*_{well}.csv.gz", local, log_fn=log)
        retire_superseded_csvs([str(p) for p in local.glob(f"*_{well}.csv.gz")], local)
        log(f"[remote] {well}: pulled again after the end-of-run re-anchoring re-ran it.\n")


def missing_nanowell_tables(run, well: str) -> list[str]:
    """What verification counts missing for *well* of a nanowell run: its sites table or its grid record."""
    if not run.nanowell:
        return []
    folder = Path(run.local_output_dir) / NANOWELL_DIR
    return [f"missing {NANOWELL_DIR}/{name} for {well}" for name in (f"{well}.csv", f"{well}_grid.json")
            if not (folder / name).is_file()]


def release_batch(fetcher, wells, *, log) -> list[str]:
    """After a nanowell batch's pipeline has exited: pull again what its re-anchoring rewrote, then delete the
    batch's per-well files from the remote. Returns the wells released.

    Runs on the Fetcher's worker behind every well the batch announced (``Fetcher.after_queued``), so each well's
    first download has been attempted. A well that never landed is kept for verification to retry, and so is a
    re-anchored well whose second pull did not land: the remote copy is then the only current one. ``nanowells/``
    is never deleted here — every later batch's plate fit reads the grid records in it.
    """
    from services.remote_csv import retire_superseded_csvs
    from services.remote_transport import delete_remote_paths

    run = fetcher.run
    if not run.nanowell:
        return []
    landed = [w for w in wells if w in run.fetched_wells and w not in run.nanowell_released]
    local = Path(run.local_output_dir)
    for well in landed:
        fetch_nanowell_tables(fetcher, well)              # the re-anchoring rewrote the grid and sites of the re-run
    remote = f"{fetcher.host.ssh_alias}:{run.remote_output_dir}"
    rerun = set(reanchored_wells(local / NANOWELL_DIR)) & set(landed)
    released = []
    for well in landed:
        if well in rerun:
            zip_listed, zip_pulled = fetcher._rsync_glob(f"{remote}/{well}_out.zip", local)
            csv_listed, csv_pulled = fetcher._rsync_glob(f"{remote}/*_{well}.csv.gz", local)
            if zip_pulled < zip_listed or csv_pulled < csv_listed:
                log(f"[remote] {well}: the re-anchored output did not all arrive; keeping it on the remote for "
                    f"verification to pull.\n")
                continue
            retire_superseded_csvs([str(p) for p in local.glob(f"*_{well}.csv.gz")], local)
        delete_remote_paths(fetcher.host, _per_well_remote_paths(run, well), what=f"per-well files of {well}",
                            log_fn=log)
        run.nanowell_released.add(well)
        released.append(well)
    if released:
        log(f"[remote] batch done: freed the remote copies of {len(released)} nanowell well(s)"
            + (f", {len(rerun & set(released))} pulled again after re-anchoring" if rerun else "") + ".\n")
    return released
