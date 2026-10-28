"""What verification asks of each well before a remote run may count as finished.

Split out of ``services/remote_fetch.py`` (line-budgeted) so the per-well question has one definition that both
the verify pass and the end-of-run retry ask: the retry may pull a well's settings marker and call it recovered only
when the very check verification will make next passes for it.

**The question is "did this run replace it", not "is a file there".** A well this run undertook to rewrite — every
well of a forced run, and otherwise each well that announced completion or was forced by name — has a local copy
from the run before, and that copy is present, non-empty and whole. Existence therefore proves nothing for it: its
CSV and its ``<well>_out.zip`` must each differ from the fingerprint taken before the run started
(:func:`snapshot_local_artifacts`). The archive half of that has been checked for a while; the CSV half was not,
so a well whose CSV listing or pull failed kept yesterday's rows, was stamped with today's settings marker, and was
never recomputed again (tracker row A2).

An incremental run is the one exception for the CSV: its fetch *merges* new rows into the local CSV, and a well
whose new fields held no cells adds none, so an unchanged CSV is a legitimate outcome there. Its merge failures are
recorded instead, in ``_RemoteRun.fetch_shortfalls``, which this check also reads.
"""

from __future__ import annotations

from pathlib import Path

from util.csv_gz import glob_csvs as _glob_csvs


def snapshot_local_artifacts(local_output_dir: Path) -> dict:
    """Fingerprint the output artefacts already on disk, before the run.

    ``(size, mtime_ns)`` per path. Cheap — one ``stat`` per file, no reads — and
    it is the only way verify can tell "this well's output was replaced" from
    "this well's output is still the one from yesterday", which are otherwise
    the same observation: a file that exists and is non-empty.
    """
    out: dict = {}
    try:
        entries = list(Path(local_output_dir).glob("*"))
    except OSError:
        return out
    for path in entries:
        try:
            if path.is_file():
                st = path.stat()
                out[path.name] = (st.st_size, st.st_mtime_ns)
        except OSError:
            continue
    return out


def artifact_changed(run, path: Path) -> bool:
    """Whether *path* differs from its pre-run fingerprint.

    A file absent from the snapshot is new, so it changed. Anything that cannot
    be stat'd now is reported as changed rather than failing the run on an
    unreadable file — this check exists to catch output that was silently *not*
    written, not to invent new ways for a good run to fail.
    """
    before = (run.pre_run_artifacts or {}).get(path.name)
    if before is None:
        return True
    try:
        st = path.stat()
    except OSError:
        return True
    return (st.st_size, st.st_mtime_ns) != before


def wells_to_replace(run, all_wells) -> set[str]:
    """The wells this run undertook to rewrite. A forced run promised all of them; otherwise only the wells that
    announced completion or were forced by name produced anything new, and a well legitimately left alone must not
    be failed for keeping its old output."""
    return set(all_wells) if run.force else (set(run.force_wells) | set(run.expected_wells))


def well_shortfall(run, well: str, *, replace: bool) -> list[str]:
    """Verification's messages for one well — empty when its local output is complete (and, with *replace*, this
    run's). Every message names the well, so ``remote_fetch._WELL_IN_MESSAGE`` reads it back for the retry."""
    from services.remote_fetch_nanowells import missing_nanowell_tables
    from services.remote_run_state import OutputFilter

    out_dir = Path(run.local_output_dir)
    csvs = _glob_csvs(out_dir, f"*_{well}")
    if not csvs:
        return [f"missing CSV for {well}"]
    if any(p.stat().st_size == 0 for p in csvs):
        return [f"empty CSV for {well}"]
    missing: list[str] = []
    if replace and not run.incremental and not any(artifact_changed(run, p) for p in csvs):
        missing.append(f"the CSV for {well} was not replaced (same size and timestamp as before the run)")
    if run.output_filter is OutputFilter.FULL:
        out_zip = out_dir / f"{well}_out.zip"
        if not out_zip.exists() or out_zip.stat().st_size == 0:
            missing.append(f"missing {well}_out.zip locally")
        elif replace and not artifact_changed(run, out_zip):
            # Byte-size and mtime both unchanged means nothing was written here, and a run that reports success
            # over that is the failure mode this check exists for — hours of remote compute discarded with nothing
            # on screen saying so.
            missing.append(f"{well}_out.zip was not replaced (same size and timestamp as before the run)")
    reason = (getattr(run, "fetch_shortfalls", None) or {}).get(well)
    if reason:
        missing.append(f"{well}: {reason}")
    missing.extend(missing_nanowell_tables(run, well))
    return missing


def partial_wells(block: dict | None, scope) -> list[str]:
    """The wells of *scope* the client-side completeness record calls partial — output that covers fewer
    FOV-groups than the input holds, while its CSV and archive look whole (tracker row B4).

    A nanowell timepoint the run *reached* and recorded a verdict for without addressing it (too crowded, no
    grid) is counted missing by the record but is the run's answer for it, not a gap in the output: it is listed
    in ``not_addressed``, and only what is missing beyond those counts here. Failing every nanowell run with an
    unaddressed timepoint would keep each one on the remote for ever.
    """
    wells = (block or {}).get("wells") or {}
    scope = set(scope)

    def _short(rec: dict) -> bool:
        if rec.get("health") != "partial":
            return False
        missing, answered = int(rec.get("missing_count") or 0), int(rec.get("not_addressed_count") or 0)
        return missing > answered or not missing        # a partial record with no count is taken at its word

    return sorted(w for w, rec in wells.items() if w in scope and isinstance(rec, dict) and _short(rec))
