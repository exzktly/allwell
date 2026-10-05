"""Every problem the app already knows about a plate, as one list of rows.

QC triage does not measure anything. Each row is read from a record some other
part of the app has already written, and each says which record it came from:

* the completeness record (``pipeline_info.json::run_check``, written by the
  pipeline at the end of a run and by Validate & Repair) — wells that are
  partial, whose CSV is missing, that were staged and never processed, the
  field groups the run recorded as failed, the field groups it found missing,
  and the dataset-wide findings;
* the traceback files the run left in ``errors/``, restricted to those written
  since the recorded run began, because that folder is never cleared and an
  older run's failure is not this run's;
* the run's own field-quality verdicts (``pipeline_info.json::field_quality``,
  scored by :mod:`review.domain.field_quality_scores`).

Qt-free and pure over ``ctx.state`` plus one directory listing, so the whole
table is exercised in the fast lane.

**A source that could not be read is a row, never an absence.** A dataset with
no completeness record, a check that was switched off or stopped part-way, a
plate whose field quality could not be scored: each produces a row saying so.
An empty table must mean "nothing is known to be wrong", and it can only mean
that if every source that could have spoken did.

**What each row offers follows from where the problem arose.** A processing
problem (a field group missing or failed) is fixed by running the pipeline
again over that well, so its row offers a rerun. An acquisition problem (a
field the run measured as out of focus or unlike the rest of the plate) has
data that exists and is suspect, so its row offers to exclude the field. A
missing field has no rows to exclude, and re-running a well whose pictures are
blurred produces the same blurred numbers, so neither offers the other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from domain.image_identity import PipelineSchema, load_schema, parse_image_filename
from review.data.field_exclusions import excluded_fields, field_key
from util.well_token import canonical_well_label

from .. import collect as hc

_logger = logging.getLogger("review")

#: Where a problem arose, in the user's words. A rerun answers the second; an
#: exclusion answers the first.
STAGE_ACQUISITION = "acquisition"
STAGE_PROCESSING = "processing"

KIND_RECORD = "record"
KIND_FINDING = "finding"
KIND_NO_CSV = "no_measurements"
KIND_UNSTARTED = "never_processed"
KIND_FAILED = "failed_group"
KIND_MISSING = "missing_group"
KIND_MISSING_MORE = "missing_unlisted"
KIND_ERROR_FILE = "error_file"
KIND_FIELD_QUALITY = "field_quality"

#: Decision states, in the order a row's state is resolved.
STATE_EXCLUDED = "excluded"
STATE_ACCEPTED = "accepted"
STATE_OPEN = "open"


@dataclass(frozen=True)
class TriageRow:
    """One finding. ``key`` is stable across sessions and is what a decision
    is stored under, so it is built from the finding's identity alone — never
    from a count or a sentence that can change while the problem does not."""

    key: str
    kind: str
    stage: str
    severity: str
    well: str
    fov: str
    timepoint: str
    detail: str
    can_rerun: bool = False
    can_exclude: bool = False
    #: Why a field the user might expect to exclude cannot be, or "".
    exclude_refusal: str = ""


def _key(kind: str, well: str = "", fov: str = "", tp: str = "", extra: str = "") -> str:
    return "|".join((kind, well, fov, tp, extra))


def _record(ctx) -> dict:
    info = getattr(ctx.state.dataset, "pipeline_info", None) or {}
    block = info.get("run_check")
    return block if isinstance(block, dict) else {}


def _record_rows(ctx) -> list[TriageRow]:
    state = hc.record_state(ctx)
    if state == "ok":
        return []
    return [TriageRow(
        key=_key(KIND_RECORD, extra=state), kind=KIND_RECORD,
        stage=STAGE_PROCESSING, severity="info" if state == "skipped" else "warn",
        well="", fov="", timepoint="",
        detail=hc.record_reason(ctx).capitalize()
        + " — Home → Overview offers to check it now.")]


def _schema(ctx) -> PipelineSchema | None:
    try:
        return load_schema(getattr(ctx.state.dataset, "pipeline_info", None) or {})
    except (ValueError, TypeError, KeyError):
        _logger.warning("triage: the sidecar's schema could not be read; failed "
                        "field groups are listed by file name only")
        return None


def _failed_address(name: str, schema) -> tuple[str, str]:
    if schema is None:
        return "", ""
    parsed = parse_image_filename(name, schema=schema)
    return parsed.fields.get("fov", "") or "", parsed.fields.get("timepoint", "") or ""


def _well_rows(ctx) -> list[TriageRow]:
    wells = _record(ctx).get("wells")
    if not isinstance(wells, dict):
        return []
    schema = _schema(ctx)
    rows: list[TriageRow] = []
    for well in sorted(wells):
        rec = wells[well]
        if not isinstance(rec, dict):
            continue
        health = str(rec.get("health") or "")
        if health == "missing":
            rows.append(TriageRow(
                key=_key(KIND_NO_CSV, well), kind=KIND_NO_CSV,
                stage=STAGE_PROCESSING, severity="warn", well=well, fov="",
                timepoint="", can_rerun=True,
                detail="Output images exist with no readable measurements CSV; "
                       "an ordinary re-run skips this well, a forced one rebuilds it."))
        elif health == "partial" and not int(rec.get("covered_any") or 0):
            rows.append(TriageRow(
                key=_key(KIND_UNSTARTED, well), kind=KIND_UNSTARTED,
                stage=STAGE_PROCESSING, severity="warn", well=well, fov="",
                timepoint="", can_rerun=True,
                detail="Input is present and nothing was written for it."))
            continue
        for name in rec.get("failed") or []:
            fov, tp = _failed_address(str(name), schema)
            rows.append(TriageRow(
                key=_key(KIND_FAILED, well, extra=str(name)), kind=KIND_FAILED,
                stage=STAGE_PROCESSING, severity="danger", well=well, fov=fov,
                timepoint=tp, can_rerun=True,
                detail=f"The run recorded this field group as failed ({name})."))
        listed = [p for p in (rec.get("missing") or [])
                  if isinstance(p, (list, tuple)) and len(p) == 2]
        for fov, tp in listed:
            rows.append(TriageRow(
                key=_key(KIND_MISSING, well, str(fov), str(tp)), kind=KIND_MISSING,
                stage=STAGE_PROCESSING, severity="warn", well=well, fov=str(fov),
                timepoint=str(tp), can_rerun=True,
                detail="In the input, absent from the measurements CSV."))
        unlisted = int(rec.get("missing_count") or 0) - len(listed)
        if unlisted > 0:
            # The record keeps only the first few missing groups per well. The
            # rest are counted, and a table that showed only the listed ones
            # would understate the well's shortfall with nothing saying so.
            rows.append(TriageRow(
                key=_key(KIND_MISSING_MORE, well), kind=KIND_MISSING_MORE,
                stage=STAGE_PROCESSING, severity="warn", well=well, fov="",
                timepoint="", can_rerun=True,
                detail=f"{unlisted} more missing field group(s) not listed — the "
                       f"completeness record names the first {len(listed)}."))
    return rows


def _finding_rows(ctx) -> list[TriageRow]:
    rows = []
    for finding in _record(ctx).get("findings") or []:
        if not isinstance(finding, dict):
            continue
        code = str(finding.get("code") or "finding")
        well = str(finding.get("well") or "")
        rows.append(TriageRow(
            key=_key(KIND_FINDING, well, extra=code), kind=KIND_FINDING,
            stage=STAGE_PROCESSING, severity=str(finding.get("severity") or "warn"),
            well=well, fov="", timepoint="",
            detail=str(finding.get("message") or code.replace("_", " "))))
    return rows


def _started_at(ctx) -> float | None:
    text = _record(ctx).get("started_at")
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def output_dir(ctx) -> Path | None:
    """The directory the run wrote into: the one holding its sidecar."""
    data_dir = getattr(ctx.state.dataset, "data_dir", None)
    if not data_dir:
        return None
    from review.domain.viewer_state import resolve_pipeline_info_path
    return resolve_pipeline_info_path(Path(data_dir)).parent


def _error_rows(ctx) -> list[TriageRow]:
    from pipeline.completeness import error_files_since

    out = output_dir(ctx)
    if out is None:
        return []
    since = _started_at(ctx)
    rows = []
    for err in error_files_since(out, since):
        well = canonical_well_label(err.path.stem.split("_")[0]) or ""
        when = "" if since is not None else (
            " The completeness record gives no start time, so this may be "
            "from an earlier run.")
        rows.append(TriageRow(
            key=_key(KIND_ERROR_FILE, well, extra=err.path.name),
            kind=KIND_ERROR_FILE, stage=STAGE_PROCESSING, severity="danger",
            well=well, fov="", timepoint="", can_rerun=bool(well),
            detail=f"{err.exception_type or 'Traceback'} — errors/{err.path.name}.{when}"))
    return rows


def _field_quality_rows(ctx) -> list[TriageRow]:
    from pipeline.field_quality import records_for_scoring
    from review.data.field_quality_cache import field_quality_block, untrusted_wells
    from review.domain.field_quality_scores import score_fields, summarise

    block = field_quality_block(ctx)
    if not block:
        return []
    scored = records_for_scoring(block)
    summary = summarise(scored)
    if summary.declined:
        return [TriageRow(
            key=_key(KIND_FIELD_QUALITY, extra="declined"), kind=KIND_FIELD_QUALITY,
            stage=STAGE_ACQUISITION, severity="warn", well="", fov="",
            timepoint="", detail=f"Field quality could not be scored: {summary.declined}")]
    untrusted = set(untrusted_wells(ctx))
    rows = []
    for verdict, rec in zip(score_fields(scored), scored, strict=True):
        if not verdict.flagged:
            continue
        well, fov, tp = field_key(rec.get("well"), rec.get("fov"), rec.get("timepoint"))
        refusal = ("This well's FOV labels do not identify its fields, so no "
                   "single field can be excluded." if well in untrusted else "")
        rows.append(TriageRow(
            key=_key(KIND_FIELD_QUALITY, well, fov, tp), kind=KIND_FIELD_QUALITY,
            stage=STAGE_ACQUISITION, severity="warn", well=well, fov=fov,
            timepoint=tp, can_exclude=not refusal, exclude_refusal=refusal,
            detail=f"Flagged by the run: {verdict.describe()}."))
    return rows


def triage_rows(ctx) -> list[TriageRow]:
    """Every row, dataset-wide ones first, then by well."""
    if not hc.has_dataset(ctx):
        return []
    rows = (_record_rows(ctx) + _finding_rows(ctx) + _well_rows(ctx)
            + _error_rows(ctx) + _field_quality_rows(ctx))
    return sorted(rows, key=lambda r: (bool(r.well), r.well, r.fov, r.timepoint, r.kind))


def row_state(ctx, row: TriageRow) -> str:
    """The row's decision: excluded (from the exclusion itself), accepted, or open."""
    if row.can_exclude and field_key(row.well, row.fov, row.timepoint) in excluded_fields(ctx):
        return STATE_EXCLUDED
    decisions = getattr(ctx.state.dataset, "triage_decisions", None) or {}
    if (decisions.get(row.key) or {}).get("state") == STATE_ACCEPTED:
        return STATE_ACCEPTED
    return STATE_OPEN


def rerun_wells(rows) -> list[str]:
    """The wells a rerun of *rows* should be limited to and forced on."""
    return sorted({r.well for r in rows if r.can_rerun and r.well})


def summary(ctx, rows) -> str:
    """One line over the table: how many, how many still open, by stage."""
    if not hc.has_dataset(ctx):
        return "No dataset open."
    if not rows:
        return "Nothing needs attention: every record this dataset carries is clean."
    open_rows = [r for r in rows if row_state(ctx, r) == STATE_OPEN]
    by_stage = {}
    for r in open_rows:
        by_stage[r.stage] = by_stage.get(r.stage, 0) + 1
    parts = ", ".join(f"{n} {stage}" for stage, n in sorted(by_stage.items()))
    return (f"{len(rows)} finding(s), {len(open_rows)} open"
            + (f" ({parts})." if parts else "."))
