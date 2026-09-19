"""Per-well completeness accounting for a pipeline run.

A run that loses FOV-groups still writes a per-well CSV and ``<well>_out.zip``
from the groups that succeeded, and on disk that output is indistinguishable
from a well that processed cleanly. Nothing recorded how many FOV-groups a well
was *expected* to yield versus how many it did, so no consumer downstream could
tell a six-field well from the same well with three fields lost to write errors.
The first visible symptom was an absent error bar — several layers, and often
weeks, away from the cause.

This module is that missing record. Each well worker drops a small JSON file as
it finishes; the orchestrator collects them into a ``run_report`` block inside
``pipeline_info.json``, which the viewer reads at dataset load and reports.

Stdlib-only and process-safe by construction (one file per well, written
atomically, never appended to), so it works the same for the local worker pool,
folder mode, and remote runs.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from util.json_write import write_json_atomic

# Hidden so it never looks like dataset output; removed once collected.
RUN_REPORT_DIRNAME = ".aw_run_report"

#: The key this block takes inside ``pipeline_info.json``. Declared rather than
#: spelled at the call site so ``tests/test_sidecar_blocks_are_merge_rostered.py``
#: can see it: a block nobody remembered to add to
#: ``datasets.merge_plates.merge_plates_compute._UNMERGEABLE_BLOCKS`` is silently
#: dropped by a plate merge, with no warning, because that roster is written by
#: hand.
RUN_REPORT_BLOCK = "run_report"


def _report_dir(output_dir: Path | str) -> Path:
    return Path(output_dir) / RUN_REPORT_DIRNAME


def write_well_report(
    output_dir: Path | str,
    well_label: str,
    *,
    groups_total: int,
    groups_covered: int,
    failed: list[str] | None = None,
    aborted_reason: str = "",
    not_attempted: tuple[str, ...] | list[str] = (),
) -> Path | None:
    """Record one well's completeness. Best-effort: never fails the run.

    ``groups_total`` is how many FOV-groups the well's *input* holds;
    ``groups_covered`` is how many distinct ``(fov, timepoint)`` pairs its
    output CSV actually carries once this run finished — ground truth read back
    from the artifact rather than inferred from what the run believed it did, so
    it stays correct across incremental runs and resumed checkpoints alike.
    """
    payload = {
        "well": str(well_label),
        "groups_total": int(groups_total),
        "groups_covered": int(groups_covered),
        "failed": sorted(failed or []),
        "aborted_reason": str(aborted_reason or ""),
        # Kept apart from `failed`: these were never attempted, so there is no
        # traceback in `errors/` for any of them and never will be. Folding
        # them in made the PARTIAL warning name casualties whose evidence does
        # not exist, and sent a user following the §7 diagnosis flow looking
        # for twenty tracebacks when three had been written.
        "not_attempted": sorted(not_attempted or ()),
    }
    try:
        d = _report_dir(output_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{well_label}.json"
        fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".tmp_", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path
    except OSError:
        # A run that cannot write its own bookkeeping must still produce data.
        return None


def collect_run_report(output_dir: Path | str) -> dict:
    """Merge the per-well files into one report block.

    ``wells`` maps well → its record; ``incomplete`` lists the wells whose
    output covers fewer FOV-groups than their input holds — the list a consumer
    needs in order to say "this well is partial" without re-deriving anything.
    """
    d = _report_dir(output_dir)
    wells: dict[str, dict] = {}
    if d.is_dir():
        for path in sorted(d.glob("*.json")):
            try:
                rec = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            well = str(rec.get("well") or path.stem)
            wells[well] = rec
    incomplete = sorted(w for w, r in wells.items() if is_incomplete(r))
    empty = sorted(w for w, r in wells.items() if found_nothing(r))
    return {
        "wells": wells,
        "incomplete_wells": incomplete,
        "empty_wells": empty,
        "groups_total": sum(int(r.get("groups_total", 0)) for r in wells.values()),
        "groups_covered": sum(int(r.get("groups_covered", 0)) for r in wells.values()),
    }


def clear_run_report(output_dir: Path | str) -> None:
    """Drop the staging directory once its contents are in ``pipeline_info``."""
    d = _report_dir(output_dir)
    try:
        for path in d.glob("*"):
            path.unlink(missing_ok=True)
        d.rmdir()
    except OSError:
        pass


def _int(rec, key: str) -> int:
    return int((rec or {}).get(key, 0) or 0)


def is_incomplete(rec) -> bool:
    """A well that covers fewer FOV-groups than its input holds."""
    return _int(rec, "groups_total") > _int(rec, "groups_covered")


def found_nothing(rec) -> bool:
    """A well whose input yielded **no** FOV-groups at all.

    Recorded as ``groups_total=0, groups_covered=0``, which passes
    :func:`is_incomplete` — ``0 > 0`` is false — so such a well was counted
    among the finished ones and the plate summarised as whole. It is the
    opposite: nothing about it was computed. The cause is upstream and always
    worth naming, because it is never the pipeline having done its job: a
    filename schema that does not decompose the plate, a separator that groups
    nothing, an archive whose members are all sidecars, or an empty well
    folder.

    Kept apart from ``incomplete`` rather than folded into it: "covered 8 of
    12" and "covered nothing, and here is why that is not a number" need
    different sentences and different remedies. Re-running with
    ``--incremental`` recovers the first and cannot touch the second.
    """
    return _int(rec, "groups_total") == 0


def summarize(report: dict) -> str:
    """One human line for the end-of-run log, or ``""`` when nothing is missing."""
    incomplete = report.get("incomplete_wells") or []
    empty = report.get("empty_wells") or []
    if not incomplete and not empty:
        return ""
    wells = report.get("wells") or {}
    lines: list[str] = []

    if empty:
        shown = ", ".join(empty[:8]) + ("…" if len(empty) > 8 else "")
        lines.append(
            f"NO INPUT GROUPS: {len(empty)} well(s) yielded no FOV-groups at "
            f"all ({shown}). Nothing was computed for them, and they were "
            f"previously counted among the finished wells because "
            f"groups_total and groups_covered were both zero. This is almost "
            f"always the filename schema or separator not decomposing the "
            f"plate, or an archive holding no images — --incremental cannot "
            f"recover it."
        )

    if incomplete:
        missing = sum(
            int(wells[w].get("groups_total", 0)) - int(wells[w].get("groups_covered", 0))
            for w in incomplete if w in wells
        )
        shown = ", ".join(incomplete[:8]) + ("…" if len(incomplete) > 8 else "")
        lines.append(
            f"INCOMPLETE: {len(incomplete)} well(s) cover fewer FOV-groups than "
            f"their input holds — {missing} group(s) missing ({shown}). Their "
            f"CSV / out.zip exist and look complete; per-field statistics for "
            f"them will be wrong or undefined. Re-run with --incremental to "
            f"recover the missing groups."
        )

    return "\n".join(lines)


# ── The three call sites the pipeline uses ──────────────────────────────────


def record_well_completeness(
    output_dir,
    well_label: str,
    groups_in_input: int,
    csv_path,
    failed,
    *,
    aborted_reason: str = "",
    not_attempted: tuple[str, ...] | list[str] = (),
) -> None:
    """Record how much of this well's input its output actually covers.

    Coverage is read back from the finished CSV — the distinct ``(fov,
    timepoint)`` pairs it carries — rather than counted from what the run
    believed it processed. That keeps the number honest across incremental runs,
    resumed per-FOV checkpoints, and groups this run never attempted because the
    storage gave out: all of them show up as the gap between the input's group
    count and the output's.

    *aborted_reason* and *not_attempted* carry why a well stopped early and
    which groups it never tried. Both were computed by the worker and dropped
    on the floor here: `run_report` declared `aborted_reason`, serialised it
    and read it back, and no production caller could set a non-empty value —
    a documented, serialised, read field that no branch could populate
    (CLAUDE.md §7.7).
    """
    from pipeline.incremental_keys import read_done_fov_keys

    covered = 0
    if csv_path is not None:
        try:
            covered = len(read_done_fov_keys(csv_path))
        except Exception:
            covered = 0
    write_well_report(
        output_dir, well_label,
        groups_total=int(groups_in_input),
        groups_covered=int(covered),
        failed=list(failed or []),
        aborted_reason=aborted_reason,
        not_attempted=tuple(not_attempted or ()),
    )


def merge_into_pipeline_info(info_path, block: dict, *, atomic_writer) -> None:
    """Merge *block* into an existing ``pipeline_info.json`` (atomic).

    The sidecar is written before any image work so the viewer has schema
    metadata even if the run dies partway; anything only knowable at the END of
    a run — the per-well completeness record — is merged in here rather than
    rewriting the file from scratch, so a failed merge can never cost the
    metadata that was already safely on disk. *atomic_writer* is
    ``pipeline.io._safe_atomic_text_write``, passed in so this module does not
    import the imaging stack.
    """
    path = Path(info_path)
    try:
        existing = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    existing.update(block)
    with atomic_writer(path) as fh:
        json.dump(existing, fh, indent=2)


def finalize_run_report(output_dir, info_path, *, atomic_writer, log) -> None:
    """Fold the per-well records into ``pipeline_info.json`` and log the gap.

    Called once at the end of a run. Best-effort throughout: a run must not fail
    because it could not describe itself.
    """
    try:
        report = collect_run_report(output_dir)
        if report.get("wells"):
            merge_into_pipeline_info(
                info_path, {"run_report": report}, atomic_writer=atomic_writer)
            message = summarize(report)
            if message:
                log.warning("%s", message)
            else:
                log.info("All %d well(s) cover every FOV-group in their input.",
                         len(report["wells"]))
        clear_run_report(output_dir)
    except Exception as exc:
        log.warning("Could not record run completeness: %s", exc)


# ── Remote fetch: the sidecar is overwritten, the record must not be ────────

def read_run_report_block(info_path) -> dict | None:
    """The ``run_report`` block currently in *info_path*, or ``None``."""
    path = Path(info_path)
    if not path.exists():
        return None
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    block = info.get("run_report") if isinstance(info, dict) else None
    return block if isinstance(block, dict) else None


def merge_fetched_run_report(info_path, prior_block: dict | None) -> bool:
    """Fold *prior_block* back into the sidecar a remote fetch just overwrote.

    A remote run's output dir starts empty, so the sidecar it writes describes
    only the wells that run processed — on an incremental run, only the ones the
    upload split sent. Pulling that file over the local one therefore replaces a
    whole plate's completeness records with a handful, and every well the run
    skipped silently loses the record that said whether its empty output was a
    measurement or a gap. This restores them.

    Fetched records win per well (they are this run's, and newer); prior records
    survive for every well the fetched block does not mention. The derived
    fields are recomputed rather than merged, so they cannot disagree with the
    ``wells`` map they summarise. Returns True iff the file was rewritten.
    """
    if not prior_block:
        return False
    path = Path(info_path)
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(info, dict):
        return False
    fetched = info.get("run_report")
    fetched_wells = fetched.get("wells") if isinstance(fetched, dict) else None
    prior_wells = prior_block.get("wells")
    if not isinstance(prior_wells, dict):
        return False
    merged = dict(prior_wells)
    if isinstance(fetched_wells, dict):
        merged.update(fetched_wells)
    if merged == fetched_wells:
        return False
    info["run_report"] = {
        "wells": merged,
        "incomplete_wells": sorted(
            w for w, r in merged.items() if is_incomplete(r)),
        "empty_wells": sorted(
            w for w, r in merged.items() if found_nothing(r)),
        "groups_total": sum(int((r or {}).get("groups_total", 0)) for r in merged.values()),
        "groups_covered": sum(int((r or {}).get("groups_covered", 0)) for r in merged.values()),
    }
    try:
        write_json_atomic(path, info)
    except OSError:
        return False
    return True
