"""Bring a nanowell well's tables back from the remote host (tracker P9).

``Fetcher._fetch_one`` pulls ``<well>_out.zip`` and the per-cell CSV. A nanowell run also writes
``nanowells/<well>.csv`` (one row per site and timepoint) and ``nanowells/<well>_grid.json`` (the
registration, lattice and status of every timepoint); without them the run's real output stays on the
worker host and the fetched dataset has cells with no sites. They are separate from the cell CSV
because Review loads every top-level ``*.csv`` as a well's cell table.

This lives in its own module because ``remote_fetch.py`` has three lines of headroom: the hook there is
one call, and everything else is here.
"""

from __future__ import annotations

import json
from pathlib import Path

NANOWELL_DIR = "nanowells"


def is_nanowell_run(local_output_dir) -> bool:
    """Whether this run's sidecar (fetched early, before any well) records nanowell settings."""
    try:
        info = json.loads((Path(local_output_dir) / "pipeline_info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(info, dict) and "nanowell" in info


def fetch_nanowell_tables(fetcher, well: str) -> int:
    """Pull ``nanowells/<well>.csv`` and ``<well>_grid.json`` beside the well's other output; return how many landed.

    Two exact names, never a glob on the well label (``A01*`` would also match ``A010``). A file the
    remote does not have is simply not listed; the run's completeness check is what notices a timepoint
    missing from the grid, so a table that failed to arrive is a finding there, not a silent absence.
    A no-op for a run that is not in nanowell mode.
    """
    run = fetcher.run
    if not is_nanowell_run(run.local_output_dir):
        return 0
    dest = Path(run.local_output_dir) / NANOWELL_DIR
    pulled = 0
    for name in (f"{well}.csv", f"{well}_grid.json"):
        host, files, _bytes = fetcher._list_remote_glob(
            f"{fetcher.host.ssh_alias}:{run.remote_output_dir}/{NANOWELL_DIR}/{name}")
        if files:
            dest.mkdir(parents=True, exist_ok=True)
            pulled += fetcher._pull_listed(host, files, dest)
    return pulled
