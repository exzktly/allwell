"""Give a remote nanowell run the rest of the plate's grid records, as a local run has them.

A nanowell timepoint that has no origin of its own — no central square accepted, no anchor set by hand at that
timepoint — can still be addressed at the end of the run from the plate's rigid motion between timepoints
(``pipeline.nanowell_task.reanchor_plate``). That fit reads every well's grid record under ``<output>/nanowells/``,
not only the wells this invocation ran: a well re-run alone is predicted from the others' origins.

Run on this computer, a re-run of a few wells finds the rest of the plate's records in its output folder, because
that is where the first run left them. A remote run does not: its output directory on the host is removed once its
wells have been fetched (*Clean up remote*), so a later run of a few wells — *Re-run anchored wells…*, QC Triage's
*Re-run wells…* — finds no other well there, and every timepoint those wells could only get from the plate came back
unaddressed: "No accepted fiducial, array centre, or precise plate-rigid prediction". The reported case was an anchor
set by hand at a well's second timepoint: that timepoint was addressed from it, and the first, which the plate's
motion would have carried it to, was not.

So before the pipeline starts, the grid records the local output folder holds for every well **not** in this run
are copied into the remote output directory's ``nanowells/``. The wells this run computes are left out: their
records are about to be rewritten, and a forced well must not find its previous attempt's record standing in for
it. The re-anchoring pass already fits only records made under this run's settings and re-runs only wells of this
run, so the copied records are read and never rewritten; the verification fetch that pulls ``nanowells/`` back
brings them back unchanged. Each is a few kilobytes.

The plate's empty confluence reference (``nanowells/_confluence_reference/``, ``pipeline.nanowell_reference``) is
sent for the same reason. Every well's confluence is read against it, and it is measured only when its own well
runs, so a remote run that does not compute that well — a re-run of a few wells, or a resume whose reference
finished earlier — found no reference on the host, and every well it ran came back with no confluence, which the
fetch then wrote over the values this computer already had. It is sent when, and only when, its well is not among
the wells this run computes: a run that measures the reference again must not read the previous one in its place.
The pipeline reads a reference only under the run's own settings, so one measured under others is refused there.

Best-effort: a copy that fails is said in the run log and costs only what the run cost before — the timepoints the
plate would have addressed stay unaddressed and say why — never the run itself.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from util.aw_safe import swallow
from util.well_token import canonical_well_label

NANOWELL_DIR = "nanowells"
GRID_SUFFIX = "_grid.json"
#: ``pipeline.nanowell_reference.DIR_NAME`` and its index, named here so this module needs nothing of the pipeline;
#: ``tests/test_a_remote_nanowell_rerun_has_the_plate_and_uses_new_anchors.py`` holds the two equal.
REFERENCE_DIR = "_confluence_reference"
REFERENCE_INDEX = "reference.json"


def plate_grid_records(local_output_dir, run_wells: Iterable[str]) -> list[Path]:
    """The grid records in *local_output_dir*'s ``nanowells/`` of every well not in *run_wells*, sorted."""
    folder = Path(local_output_dir) / NANOWELL_DIR
    if not folder.is_dir():
        return []
    running = {canonical_well_label(w) or w for w in run_wells}
    out = []
    for path in sorted(folder.glob(f"*{GRID_SUFFIX}")):
        well = canonical_well_label(path.name[:-len(GRID_SUFFIX)])
        if well is not None and well not in running:
            out.append(path)
    return out


def seed_plate_grids(cache, run, run_wells: Iterable[str], log: Callable[[str], None]) -> int:
    """Copy the other wells' grid records into *run*'s remote ``nanowells/``; return how many were sent.

    *cache* is the runner's ``DatasetCache`` (its host and ssh helper), *run* the ``_RemoteRun`` just prepared.
    Nothing for a run that is not in nanowell mode, or when the local folder has no other well's record."""
    if not getattr(run, "nanowell", False):
        return 0
    files = plate_grid_records(run.local_output_dir, run_wells)
    if not files:
        return 0
    from services.remote_config import resolve_rsync_binary, to_rsync_path
    from services.remote_transport import _rsync_ssh_transport, _rsync_with_retry

    dest_dir = f"{run.remote_output_dir}/{NANOWELL_DIR}"
    sent = 0
    with swallow("copy the plate's nanowell grid records to the remote"):
        cache._ssh_run(["mkdir", "-p", dest_dir], check=True)
        # ``--update``: a record already on the host and newer (a re-attached directory an earlier attempt of
        # this run wrote) is kept rather than replaced by the older local copy.
        cmd = [resolve_rsync_binary(), *_rsync_ssh_transport(cache.host), "-a", "--update",
               *(to_rsync_path(p) for p in files), f"{cache.host.ssh_alias}:{dest_dir}/"]
        result = _rsync_with_retry(cmd, log_fn=log, timeout=120)
        if result.returncode == 0:
            sent = len(files)
    if sent:
        log(f"[remote] nanowell: sent {sent} other well(s)' grid records, so timepoints without an origin of their "
            "own can be addressed from the rest of the plate\n")
    else:
        log("[remote] nanowell: the other wells' grid records could not be sent; timepoints that only the rest of "
            "the plate could address will stay unaddressed\n")
    return sent


def confluence_reference_to_send(local_output_dir, run_wells: Iterable[str]) -> Path | None:
    """*local_output_dir*'s saved confluence reference folder, when the well it was measured on is not in
    *run_wells*; ``None`` when there is none, its index is unreadable, or this run measures that well itself."""
    import json

    folder = Path(local_output_dir) / NANOWELL_DIR / REFERENCE_DIR
    try:
        well = json.loads((folder / REFERENCE_INDEX).read_text()).get("well")
    except (OSError, ValueError, AttributeError):
        return None
    well = canonical_well_label(well or "") or (str(well).strip().upper() if well else None)
    if not well or well in {canonical_well_label(w) or str(w).upper() for w in run_wells}:
        return None
    return folder


def seed_confluence_reference(cache, run, run_wells: Iterable[str], log: Callable[[str], None]) -> bool:
    """Copy the plate's saved confluence reference into *run*'s remote ``nanowells/`` when this run does not
    measure the reference well itself (see the module docstring); return whether it was sent."""
    if not getattr(run, "nanowell", False):
        return False
    folder = confluence_reference_to_send(run.local_output_dir, run_wells)
    if folder is None:
        return False
    from services.remote_config import resolve_rsync_binary, to_rsync_path
    from services.remote_transport import _rsync_ssh_transport, _rsync_with_retry

    dest_dir = f"{run.remote_output_dir}/{NANOWELL_DIR}"
    sent = False
    with swallow("copy the plate's confluence reference to the remote"):
        cache._ssh_run(["mkdir", "-p", dest_dir], check=True)
        cmd = [resolve_rsync_binary(), *_rsync_ssh_transport(cache.host), "-a", "--update",
               to_rsync_path(folder), f"{cache.host.ssh_alias}:{dest_dir}/"]
        sent = _rsync_with_retry(cmd, log_fn=log, timeout=120).returncode == 0
    if sent:
        log("[remote] nanowell: sent the plate's empty confluence reference, so the wells of this run read their "
            "confluence against it\n")
    else:
        log("[remote] nanowell: the plate's empty confluence reference could not be sent; the wells of this run "
            "will come back with no confluence\n")
    return sent
