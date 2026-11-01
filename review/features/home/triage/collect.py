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
  scored by :mod:`review.domain.field_quality_scores`);
* for a nanowell well, the timepoints the completeness record lists as
  located but not addressed (``not_addressed``), each with the reason its grid
  record (``nanowells/<well>_grid.json``) gives.

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
blurred produces the same blurred numbers, so neither offers the other. A
nanowell timepoint left unaddressed (no central square accepted) is fixed by
neither on its own: re-running finds the same nothing, so its row offers *Set
anchor…* — the square clicked by hand on the Array view — and the rerun that
then uses the anchor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from domain.image_identity import PipelineSchema, load_schema, parse_image_filename
from review.data.field_exclusions import excluded_fields, field_key

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
KIND_NO_ARCHIVE = "no_output_archive"
KIND_PARTIAL = "partial_well"
KIND_UNREADABLE = "unreadable_wells"
KIND_EXCLUDED = "excluded_field"
KIND_UNADDRESSED = "nanowell_not_addressed"

#: The one grid status an anchor set by hand can fix (``nanowell_run.UNADDRESSED``). The other statuses a
#: not-addressed timepoint can carry (a refused orientation, no lattice) fail before an origin is chosen.
ANCHORABLE_STATUS = "unaddressed"

#: The row a well in each needs-attention state gets beyond its per-group rows,
#: keyed by ``pipeline.completeness.HEALTH_STATES``. ``tests/test_qc_triage.py``
#: holds every state in ``NEEDS_ATTENTION`` to a row, so a state added to the
#: completeness record cannot pass through this table unreported. ``partial``
#: is handled by the per-group rows below it (and by ``KIND_PARTIAL`` when the
#: record lists none).
WELL_STATE_ROWS: dict[str, tuple[str, str]] = {
    "missing": (KIND_NO_CSV,
                "Output images exist with no readable measurements CSV; an "
                "ordinary re-run skips this well, a forced one rebuilds it."),
    "csv_only": (KIND_NO_ARCHIVE,
                 "The measurements CSV is here but the output archive is not, "
                 "so its images cannot be reviewed; a forced re-run rebuilds "
                 "both."),
}

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
    #: A nanowell timepoint an anchor set by hand can address (*Set anchor…*).
    can_anchor: bool = False


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


def _grid_reasons(ctx, well: str) -> dict[str, str]:
    """``timepoint -> reason`` from *well*'s nanowell grid record, or ``{}`` when it cannot be read (the row then
    says the grid record gives no reason)."""
    import json

    out = output_dir(ctx)
    if out is None:
        return {}
    try:
        blocks = json.loads((out / "nanowells" / f"{well}_grid.json").read_text(encoding="utf-8")).get("timepoints")
    except (OSError, ValueError, AttributeError):
        return {}
    if not isinstance(blocks, dict):
        return {}
    return {str(tp): str(b.get("reason") or "") for tp, b in blocks.items() if isinstance(b, dict)}


def _not_addressed_rows(ctx, well: str, rec: dict) -> tuple[list[TriageRow], set[str]]:
    """One row per timepoint the run located but could not address, and those timepoints.

    They are in the record's ``missing`` too (an incremental run redoes them), where they would read as "absent
    from the measurements CSV" — true, and no help: the run reached them and said why it stopped. Its reason is
    what the row says, and an unaddressed one offers the anchor that addresses it."""
    pairs = [p for p in (rec.get("not_addressed") or []) if isinstance(p, (list, tuple)) and len(p) == 2]
    if not pairs:
        return [], set()
    reasons = _grid_reasons(ctx, well)
    rows = []
    for tp, status in pairs:
        tp, status = str(tp), str(status or "")
        anchorable = status == ANCHORABLE_STATUS
        reason = reasons.get(tp, "").strip().rstrip(".")
        why = reason or "the grid record gives no reason"
        then = ("Set an anchor on its central square by hand (Set anchor…), then re-run the well." if anchorable
                else "An anchor set by hand cannot change this; re-running the well redoes it.")
        rows.append(TriageRow(
            key=_key(KIND_UNADDRESSED, well, "", tp), kind=KIND_UNADDRESSED,
            stage=STAGE_PROCESSING, severity="warn", well=well, fov="", timepoint=tp,
            can_rerun=True, can_anchor=anchorable,
            detail=f"Nanowells not addressed ({status.replace('_', ' ') or 'no status'}): {why}. {then}"))
    return rows, {str(tp) for tp, _status in pairs}


def _well_rows(ctx) -> list[TriageRow]:
    wells = _record(ctx).get("wells")
    if not isinstance(wells, dict):
        return []
    schema = _schema(ctx)
    ended_early = hc.run_ended_early(ctx)
    rows: list[TriageRow] = []
    for well in sorted(wells):
        rec = wells[well]
        if not isinstance(rec, dict):
            continue
        health = str(rec.get("health") or "")
        if health in WELL_STATE_ROWS:
            kind, detail = WELL_STATE_ROWS[health]
            rows.append(TriageRow(
                key=_key(kind, well), kind=kind, stage=STAGE_PROCESSING,
                severity="warn", well=well, fov="", timepoint="",
                can_rerun=True, detail=detail))
        elif (health == "partial" and not int(rec.get("covered_any") or 0)
              and not int(rec.get("not_addressed_count") or 0)):
            # A nanowell well whose every timepoint was located but none addressed also covers nothing, and it was
            # processed: its rows below say why, as Home's plate calls it partial rather than never processed.
            rows.append(TriageRow(
                key=_key(KIND_UNSTARTED, well), kind=KIND_UNSTARTED,
                stage=STAGE_PROCESSING, severity="warn", well=well, fov="",
                timepoint="", can_rerun=True,
                detail=("The run ended before it reached this well: its input is present and nothing was "
                        "written for it." if ended_early else
                        "Input is present and nothing was written for it.")))
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
        located, reached = _not_addressed_rows(ctx, well, rec)
        rows.extend(located)
        for fov, tp in listed:
            if not str(fov) and str(tp) in reached:
                continue                     # said above, with the run's reason
            rows.append(TriageRow(
                key=_key(KIND_MISSING, well, str(fov), str(tp)), kind=KIND_MISSING,
                stage=STAGE_PROCESSING, severity="warn", well=well, fov=str(fov),
                timepoint=str(tp), can_rerun=True,
                detail="In the input, absent from the measurements CSV."))
        unlisted = int(rec.get("missing_count") or 0) - len(listed)
        if health == "partial" and not listed and unlisted <= 0 and not rec.get("failed"):
            # Partial with nothing itemised: still a row, never an absence.
            rows.append(TriageRow(
                key=_key(KIND_PARTIAL, well), kind=KIND_PARTIAL,
                stage=STAGE_PROCESSING, severity="warn", well=well, fov="",
                timepoint="", can_rerun=True,
                detail="The completeness record calls this well partial without "
                       "naming the missing field groups."))
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
    totals = _record(ctx).get("totals") or {}
    unreadable = int(totals.get("unreadable") or 0) if isinstance(totals, dict) else 0
    if unreadable:
        rows.append(TriageRow(
            key=_key(KIND_UNREADABLE), kind=KIND_UNREADABLE,
            stage=STAGE_PROCESSING, severity="warn", well="", fov="",
            timepoint="",
            detail=f"{unreadable} well(s) could not be read by the completeness "
                   f"check, so nothing is known about them — Validate & Repair "
                   f"can look again."))
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
    return _iso_time(_record(ctx).get("started_at"))


def _iso_time(text) -> float | None:
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


def _whole_since(ctx) -> dict[str, float]:
    """``{well: time}`` for every well the completeness record found whole, with nothing failed.

    The time is the start of the run that wrote the record (its finish, for a
    Validate record, which has no run): a failure record from before then
    belongs to an earlier run and the well has been found whole since, so it no
    longer stands. One written *during* the run is never answered this way — a
    forced re-run that failed leaves the earlier, whole output in place, and the
    check finding that output whole says nothing about the attempt that failed.
    """
    record = _record(ctx)
    since = _iso_time(record.get("started_at")) or _iso_time(record.get("finished_at"))
    wells = record.get("wells")
    if since is None or not isinstance(wells, dict):
        return {}
    return {str(well): since for well, rec in wells.items()
            if isinstance(rec, dict) and rec.get("health") == "complete" and not rec.get("failed")}


def _error_rows(ctx) -> list[TriageRow]:
    from pipeline.error_records import current_records

    out = output_dir(ctx)
    if out is None:
        return []
    since = _started_at(ctx)
    rows = []
    for err in current_records(out, whole_since=_whole_since(ctx)):
        if since is None:
            when = (" The completeness record gives no start time, so this may be "
                    "from an earlier run.")
        elif err.mtime < since:
            when = (" Written by an earlier run; the well has not been processed "
                    "whole since.")
        else:
            when = ""
        name = err.path.name
        rows.append(TriageRow(
            key=_key(KIND_ERROR_FILE, err.well, extra=name),
            kind=KIND_ERROR_FILE, stage=STAGE_PROCESSING, severity="danger",
            well=err.well, fov="", timepoint="", can_rerun=True,
            detail=f"{err.exception_type or 'Traceback'} — errors/{name}.{when}"))
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


def _exclusion_rows(ctx, flagged_rows) -> list[TriageRow]:
    """One row for every exclusion no flagged-field row already shows.

    An exclusion outlives the finding that prompted it — a rerun unflags the
    field, a sidecar arrives without its field-quality block, the scorer
    declines — and the layer goes on removing that field's cells. Without a row
    of its own it would remove them with nothing on screen saying so, and with
    no Reopen to put them back.
    """
    from review.data.field_quality_cache import untrusted_wells

    shown = {field_key(r.well, r.fov, r.timepoint) for r in flagged_rows}
    untrusted = set(untrusted_wells(ctx))
    rows = []
    for (well, fov, tp), meta in sorted(excluded_fields(ctx).items()):
        if (well, fov, tp) in shown:
            continue
        reason = str((meta or {}).get("reason") or "")
        refusal = ("This well's FOV labels do not identify its fields, so this "
                   "exclusion is not applied." if well in untrusted else "")
        rows.append(TriageRow(
            key=_key(KIND_EXCLUDED, well, fov, tp), kind=KIND_EXCLUDED,
            stage=STAGE_ACQUISITION, severity="info", well=well, fov=fov,
            timepoint=tp, can_exclude=not refusal, exclude_refusal=refusal,
            detail=(f"Excluded by hand{f' ({reason})' if reason else ''}; the "
                    f"run's records no longer flag this field.")))
    return rows


def triage_rows(ctx) -> list[TriageRow]:
    """Every row, dataset-wide ones first, then by well."""
    if not hc.has_dataset(ctx):
        return []
    flagged = _field_quality_rows(ctx)
    rows = (_record_rows(ctx) + _finding_rows(ctx) + _well_rows(ctx)
            + _error_rows(ctx) + flagged + _exclusion_rows(ctx, flagged))
    return sorted(rows, key=lambda r: (bool(r.well), r.well, r.fov, r.timepoint, r.kind))


def row_state(ctx, row: TriageRow) -> str:
    """The row's decision: excluded (from the exclusion itself), accepted, or open."""
    if (row.kind in (KIND_FIELD_QUALITY, KIND_EXCLUDED)
            and field_key(row.well, row.fov, row.timepoint) in excluded_fields(ctx)):
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
    excluded = len(excluded_fields(ctx))
    return (f"{len(rows)} finding(s), {len(open_rows)} open"
            + (f" ({parts})." if parts else ".")
            + (f" {excluded} field(s) excluded by hand." if excluded else ""))
