"""Write a finished Validate scan back as the dataset's completeness record.

Every pipeline run ends by recording what it produced into
``pipeline_info.json``'s ``run_check`` block, and Review's Home page reports
that record rather than scanning for itself. Two kinds of dataset therefore have
no record to report: one produced before the end-of-run check existed, and one
whose run was launched with the check turned off. For both, Home's only offered
action is to open the Datasets Validate tool — which measures exactly the same
thing, more thoroughly, and until this module existed threw the answer away when
its window closed.

So a completed scan writes its own record. The block is assembled by
:func:`pipeline.completeness.build_block`, the same function the pipeline's
end-of-run check goes through, from per-well entries built by
:func:`pipeline.completeness.well_record` out of the same
:class:`~pipeline.completeness.WellCoverage` shape — there is one record format,
not a pipeline one and a Validate one that drift.

What differs is ``source``, and it is not cosmetic. A ``"run"`` record describes
the run that had just finished; a ``"validate"`` record describes the folder as
it stood when someone looked at it, possibly months later and after hand-edits.
Home says which, because a user reading "94 of 96 wells complete" is entitled to
know whether that sentence is about a run or about today's disk.

**Only a whole scan is recorded.** A cancelled scan covers an arbitrary prefix
of the plate, and a record built from one would paint every unscanned well as
though it had been measured and found absent — the exact silent-substitution
failure the record exists to prevent.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

from pipeline.completeness import (
    WellCoverage,
    build_block,
    errors_since,
    failed_groups_by_well,
    utc_iso,
    well_record,
)
from datasets._common.dataset_paths import resolve_dataset
from datasets.validate.validate_compute import DatasetReport, WellReport

_INFO_FILENAME = "pipeline_info.json"


def _coverage(report: WellReport, failed: tuple[str, ...]) -> WellCoverage:
    """A scan's per-well result in the shape the record builder takes.

    Every field is carried across rather than recomputed: the scan has already
    opened this well's input container and parsed its CSV, and asking a second
    time is both the cost the whole design avoids and a way for the two answers
    to disagree.
    """
    return WellCoverage(
        well=report.well,
        intent=report.intent,
        covered_csv=report.covered_csv,
        covered_any=report.covered_any,
        missing=report.missing,
        csv_state=report.csv_state,
        has_in_container=report.has_in_container,
        has_out_zip=report.has_out_zip,
        has_out_folder=report.has_out_folder,
        failed=failed,
    )


def _findings_as_dicts(report: DatasetReport) -> list[dict]:
    """Validate's findings in the record's own JSON shape.

    The ``code`` strings are already shared with the pipeline's end-of-run
    findings, so Home renders both through one path.
    """
    return [
        {"severity": f.severity, "code": f.code, "message": f.message, "well": f.well}
        for f in report.findings
    ]


def block_from_report(
    report: DatasetReport, *, checked_at: str = "", host: str = "",
) -> dict:
    """Build the ``run_check`` block a finished :class:`DatasetReport` implies.

    Pure: reads the dataset's sidecar for the schema the scan ran against and
    its ``errors/`` directory for the tracebacks present, and writes nothing.

    The error count is deliberately unfiltered. The pipeline's own check knows
    when its run started and counts only tracebacks newer than that; a scan run
    later has no such boundary, so it counts every traceback in the folder and
    the ``source`` field is what tells a reader the number means "present" and
    not "produced by the last run".

    *checked_at* defaults to now, formatted by the same helper the pipeline's
    record uses.
    """
    paths = resolve_dataset(report.root, label="dataset")
    info_path = paths.out_dir / _INFO_FILENAME
    try:
        info = json.loads(info_path.read_text())
    except (OSError, ValueError):
        info = {}
    failed_map = failed_groups_by_well(info_path)

    records = {
        w.well: well_record(_coverage(w, tuple(failed_map.get(w.well, ()))))
        for w in report.wells
    }
    error_count, error_types = errors_since(paths.out_dir, None)
    return build_block(
        records,
        source="validate",
        finished_at=checked_at or utc_iso(time.time()),
        host=host or socket.gethostname(),
        mode="folder" if any(w.has_out_folder for w in report.wells) else "zip",
        schema=list(info.get("schema_fields") or ()),
        sep=str(info.get("separator") or ""),
        csv_prefix=report.csv_prefixes[0] if len(report.csv_prefixes) == 1 else "",
        errors={"files": error_count, "by_type": error_types},
        findings=_findings_as_dicts(report),
    )


def write_record(report: DatasetReport, *, checked_at: str = "", host: str = "") -> dict:
    """Record *report* into the dataset's ``pipeline_info.json`` and return the
    block written.

    Read-modify-writes the loaded sidecar, replacing only ``run_check`` — the
    idiom :mod:`datasets.validate.apply` uses for the availability lists, and
    for the same reason: a from-scratch rebuild would have to name every key it
    means to preserve, and would silently drop the next one somebody adds.

    Raises :class:`OSError` / :class:`ValueError` if the sidecar cannot be read
    or written. The caller decides what a failure to record means; from here it
    is not something to swallow, because a scan that reports success while
    having written nothing is precisely how Home ends up empty after the user
    did what it asked.
    """
    paths = resolve_dataset(report.root, label="dataset")
    info_path = paths.out_dir / _INFO_FILENAME
    info = json.loads(info_path.read_text())
    if not isinstance(info, dict):
        raise ValueError(f"{info_path} is not a JSON object.")
    block = block_from_report(report, checked_at=checked_at, host=host)
    info["run_check"] = block
    tmp = info_path.with_name(info_path.name + ".__aw_tmp")
    tmp.write_text(json.dumps(info, indent=2))
    Path(tmp).replace(info_path)
    return block
