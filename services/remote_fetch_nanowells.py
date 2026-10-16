"""Bring a nanowell well's tables back from the remote host (tracker P9).

``Fetcher._fetch_one`` pulls ``<well>_out.zip`` and the per-cell CSV. A nanowell run also writes
``nanowells/<well>.csv`` (one row per site and timepoint) and ``nanowells/<well>_grid.json`` (the
registration, lattice and status of every timepoint); without them the run's real output stays on the
worker host and the fetched dataset has cells with no sites. They are separate from the cell CSV
because Review loads every top-level ``*.csv`` as a well's cell table.

**A nanowell run's wells are not finished when they say so.** The end-of-run re-anchoring
(``pipeline.nanowell_task.reanchor_plate``) re-runs the (well, timepoint)s the rest of the plate can address,
incrementally, from the well's input archive and its earlier output. So nothing of a well is deleted from the
remote as it is fetched (:func:`well_files_to_delete`), and at verification the whole ``nanowells/`` directory
and every re-anchored well's CSV and ``<well>_out.zip`` are pulled again (:func:`pull_nanowell_results`). A well
whose tables did not arrive fails verification (:func:`missing_nanowell_tables`), which keeps the remote copy.
Remote disk for a nanowell run therefore peaks at the whole run's output, not one well's.

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
    if run.nanowell:
        return []
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
