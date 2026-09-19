"""One well's field-quality record on disk, and the block it is collected into.

Split out of :mod:`pipeline.field_quality` — which re-exports every name here,
so no call site moved — because **this half must not drag numpy and scipy into
the remote client**. ``services/remote_runner.py`` merges this block across a
fetch, and a guard
(``tests/test_incremental_keys_shared.py::test_remote_runner_stays_imaging_stack_free``)
walks that module's import graph and fails if the imaging stack appears in it:
the remote client runs on the user's own machine and is sent to hosts where
the pipeline's dependencies are not installed.

The split falls where the subject changes anyway. Beside this,
``field_quality`` measures pixels; here nothing is measured at all. This is
bookkeeping — one small JSON per well, written atomically as the well
finishes, collected by the orchestrator into a named block inside
``pipeline_info.json``, then removed. Process-safe by construction (one file
per well, never appended to), so it works identically for the local worker
pool, folder mode and remote runs.

Deliberately **not** folded into ``run_report``. Coverage accounting and
quality measurement answer different questions; ``run_report``'s payload is
already merged across a remote fetch by a function with its own semantics; and
a reader asking "was this well complete?" should not have to step over six
float metrics per field to find out.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pipeline.run_report import merge_into_pipeline_info
from util.json_write import write_json_atomic

#: Hidden so it never looks like dataset output; removed once collected.
FIELD_QUALITY_DIRNAME = ".aw_field_quality"

#: The key the collected block takes inside ``pipeline_info.json``.
FIELD_QUALITY_BLOCK = "field_quality"


def _quality_dir(output_dir) -> Path:
    return Path(output_dir) / FIELD_QUALITY_DIRNAME


def write_well_field_quality(output_dir, well_label: str, *, fields: list[dict],
                             measured_of: int | None = None) -> Path | None:
    """Record one well's per-field metrics. Best-effort: never fails the run.

    *fields* is one mapping per FOV-group measured, each carrying whatever
    addresses it (``fov``, ``timepoint``) plus the metric values.

    ``measured_of`` is how many FOV-groups the well *has*, against
    ``len(fields)`` measured. **The gap is the whole of what a resumed or
    incremental run must not hide.** ``_process_or_resume`` returns cached
    records without calling ``process_image_group`` at all, and
    ``well_skip.incremental_for_well`` skips whole wells, so a block covering
    only what was re-processed is a biased subset — and scored as though it
    covered the plate, every field's robust z is wrong with nothing in the
    artifact saying so.
    """
    payload = {
        "well": str(well_label),
        "fields": list(fields),
        "measured": len(fields),
        "measured_of": (None if measured_of is None else int(measured_of)),
    }
    try:
        d = _quality_dir(output_dir)
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


def well_is_partial(record: dict) -> bool:
    """Whether a well's record covers fewer fields than the well holds."""
    total = record.get("measured_of")
    if total is None:
        return False
    return int(record.get("measured", 0)) < int(total)


def collect_field_quality(output_dir) -> dict:
    """Merge the per-well files into one block.

    ``partial_wells`` is the list a consumer needs in order to say "this plate
    was only partly measured" without re-deriving it — and it is the difference
    between a scorer that knows its sample is biased and one that does not.
    """
    d = _quality_dir(output_dir)
    wells: dict[str, dict] = {}
    if d.is_dir():
        for path in sorted(d.glob("*.json")):
            try:
                rec = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            wells[str(rec.get("well") or path.stem)] = rec
    partial = sorted(w for w, r in wells.items() if well_is_partial(r))
    return {
        "wells": wells,
        "partial_wells": partial,
        "measured": sum(int(r.get("measured", 0)) for r in wells.values()),
        "measured_of": sum(int(r.get("measured_of") or 0)
                           for r in wells.values()) or None,
    }


def clear_field_quality(output_dir) -> None:
    """Drop the staging directory once its contents are in ``pipeline_info``."""
    d = _quality_dir(output_dir)
    try:
        for path in d.glob("*"):
            path.unlink(missing_ok=True)
        d.rmdir()
    except OSError:
        pass


def summarize(block: dict | None) -> str:
    """One human line about a collected block, or ``""`` when there is none.

    The single composition of "what did field quality find", so the end-of-run
    log and the Analyze tab's pre-flight strip cannot say different things
    about the same folder — the pre-flight **quotes** this rather than
    deriving its own sentence from the same numbers (CLAUDE.md §0.3: report,
    do not decide; and a second derivation is a second chance to describe
    something that did not happen).

    Empty for an absent block, because a plate nobody measured and a plate
    measured and found clean must not read alike and a blank line is how the
    caller says nothing rather than the wrong thing.

    Qt-free and ``review``-free: it counts what the block records and does not
    score anything. Scoring is :mod:`review.domain.field_quality_scores`'
    job, needs the whole plate, and is the Review side's to run.
    """
    if not isinstance(block, dict) or not block.get("wells"):
        return ""
    wells = block["wells"]
    partial = list(block.get("partial_wells") or ())
    measured = int(block.get("measured", 0))
    total = block.get("measured_of")
    if partial:
        shown = ", ".join(partial[:8]) + ("…" if len(partial) > 8 else "")
        return (
            f"FIELD QUALITY PARTIAL: {measured:,} of "
            f"{int(total or 0):,} field(s) measured across {len(wells):,} "
            f"well(s); {len(partial)} well(s) only partly measured ({shown}). "
            f"A plate-wide comparison over this block would be drawn from a "
            f"biased sample, so Review will decline to score it — re-run "
            f"those wells with --force to complete it."
        )
    return (f"Field quality: {measured:,} field(s) measured across "
            f"{len(wells):,} well(s).")


def finalize_field_quality(output_dir, info_path, *, atomic_writer, log) -> None:
    """Fold the per-well quality files into ``pipeline_info.json``.

    Called once at the end of a run, mirroring
    :func:`pipeline.run_report.finalize_run_report` — same staging directory
    shape, same atomic merge, same best-effort posture. A run must not fail
    because it could not describe itself, and a plate with no block at all is
    correctly read by the Review side as "analysed before field quality was
    recorded" rather than as a plate measured and found clean.

    The partial count is logged rather than left for a reader to derive,
    because it is the one fact that decides whether the block can be scored at
    all: a resumed or incremental run measures only the fields it
    re-processed, so a plate-wide comparison drawn from it is biased, and the
    scorer refuses rather than reporting a number.
    """
    try:
        block = collect_field_quality(output_dir)
        if block.get("wells"):
            merge_into_pipeline_info(
                info_path, {FIELD_QUALITY_BLOCK: block},
                atomic_writer=atomic_writer)
            message = summarize(block)
            if block.get("partial_wells"):
                log.warning("%s", message)
            else:
                log.info("%s", message)
        clear_field_quality(output_dir)
    except Exception as exc:
        log.warning("Could not record field quality: %s", exc)


# ── Remote fetch: the sidecar is overwritten, the block must not be ─────────

def read_field_quality_block(info_path) -> dict | None:
    """The ``field_quality`` block currently in *info_path*, or ``None``."""
    path = Path(info_path)
    if not path.exists():
        return None
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    block = info.get(FIELD_QUALITY_BLOCK) if isinstance(info, dict) else None
    return block if isinstance(block, dict) else None


def merge_fetched_field_quality(info_path, prior_block: dict | None) -> bool:
    """Fold *prior_block* back into the sidecar a remote fetch just overwrote.

    The same defect :func:`pipeline.run_report.merge_fetched_run_report`
    exists for, one block over, and worse here. A remote output directory
    starts empty, so the sidecar a remote run writes covers only the wells
    that run processed — on an incremental run, only the ones the upload split
    sent. Pulling that file over the local one replaces a whole plate's
    measurements with a handful.

    For coverage records that loses history. For field quality it produces a
    **wrong number**: the acquisition metrics are scored as robust z against
    the plate, so a block silently reduced to the eight wells a remote
    incremental run touched gives every one of those wells a z computed
    against its own eight-well median. Nothing in the artifact would say so,
    and the shortfall accounting cannot catch it either — each of those wells
    is individually complete, so ``partial_wells`` stays empty and the scorer
    proceeds.

    Fetched records win per well (they are this run's, and newer); prior
    records survive for every well the fetched block does not mention. The
    derived fields are recomputed from the merged map rather than merged
    themselves, so they cannot disagree with the wells they summarise.
    Returns True iff the file was rewritten.
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
    fetched = info.get(FIELD_QUALITY_BLOCK)
    fetched_wells = fetched.get("wells") if isinstance(fetched, dict) else None
    prior_wells = prior_block.get("wells")
    if not isinstance(prior_wells, dict):
        return False
    merged = dict(prior_wells)
    if isinstance(fetched_wells, dict):
        merged.update(fetched_wells)
    if merged == fetched_wells:
        return False
    info[FIELD_QUALITY_BLOCK] = {
        "wells": merged,
        "partial_wells": sorted(w for w, r in merged.items()
                                if well_is_partial(r)),
        "measured": sum(int((r or {}).get("measured", 0))
                        for r in merged.values()),
        "measured_of": sum(int((r or {}).get("measured_of") or 0)
                           for r in merged.values()) or None,
    }
    try:
        # ``write_json_atomic``, not ``write_text``: this is a live sidecar a
        # crash mid-write would leave truncated, and the file also carries the
        # user's own cell-gating thresholds and sample definitions. A guard
        # (``tests/test_dataset_tool_integrity.py``) holds every writer of a
        # live JSON to it, and caught this one.
        write_json_atomic(path, info)
    except OSError:
        return False
    return True


def records_for_scoring(block: dict | None) -> list[dict]:
    """Flatten a collected block into the rows the Review-side scorer takes.

    Each row carries its well and timepoint, because those are the axes the
    scorer groups on, and a ``partial_plate`` flag drawn from
    ``partial_wells`` — so a scorer handed a biased subset can say so rather
    than reporting a plate median it has no right to.
    """
    if not isinstance(block, dict):
        return []
    partial = set(block.get("partial_wells") or ())
    rows: list[dict] = []
    for well, rec in (block.get("wells") or {}).items():
        for fld in rec.get("fields") or ():
            row = dict(fld)
            row.setdefault("well", well)
            row["key"] = (well, fld.get("fov"), fld.get("timepoint"))
            row["partial_plate"] = well in partial
            rows.append(row)
    return rows
