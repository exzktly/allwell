"""Pure diagnostic scan for the Validate & Repair Dataset feature.

Answers the question a partially-completed Analyze run leaves unanswered from
the GUI: which ``(well, fov, timepoint)`` work is actually done, versus what
``pipeline_info.json`` — written *before* any image work
(``process_microscopy.py``'s ``write_pipeline_info``) — claims. A crashed run
leaves the sidecar advertising the full plate while ``out/`` holds a fraction;
a plain re-run then treats a partially-done well as complete
(``output_exists_for_well``), so the only way to recover the missing work is
to know exactly which ``(fov, timepoint)`` pairs are still outstanding.

**Done-set discipline.** *Intent* — what a well's raw input actually contains
— is read via the same member-name parser the pipeline's own incremental diff
uses (``pipeline.incremental_keys.fov_keys_from_member_names``, which
``read_done_fov_keys_from_zip`` / ``_from_folder`` both delegate to after
listing their container), which parses ``(fov, timepoint)`` positionally off a
container's member names. Nothing distinguishes a raw-input container from a
processed-output one to that parser — it only cares about filename structure —
so pointing it at ``in/<WELL>.zip``'s members instead of
``out/<WELL>_out.zip``'s reads intent instead of done, with zero new code.

**Read discipline.** Each artefact is read exactly once per scan:
:func:`_scan_one_well` lists its input container once and parses each candidate
CSV once, then passes both to every consumer. The per-well work is pure-Python
parsing and therefore GIL-bound, so the thread pool below cannot overlap it on a
warm local disk (it only pays off when a container open costs a round trip, as
on a network mount) — doing less work is the only thing that makes a local scan
faster, and ``test_scan_reads_each_container_once_and_each_csv_once`` holds the
budget at two container opens and one CSV parse per well.
*Done* is read the same way ``pipeline.incremental_keys.incremental_done_fov_keys``
composes it (CSV rows ∪ out-zip members ∪ output folder), but **on the real
paths already in hand** rather than by reconstructing
``f"{csv_prefix}_{safe_well}.csv"`` — that reconstruction is unsafe here:
``csv_prefix`` is a CLI arg never recorded in ``pipeline_info.json``, and
``_safe_well`` only strips path separators, so an unpadded on-disk CSV
(``measurements_A1.csv``) would reconstruct as a nonexistent
``measurements_A01.csv`` and silently report "0 FOVs done" — the exact false
alarm this feature exists to prevent.
"""

from __future__ import annotations

import csv
import json
import math
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from datasets._common.concurrency import pick_workers
from pipeline.completeness import (
    csv_keys_from_rows,
    done_keys,
    health as _health,
    missing_keys,
    reduce_csv_state,
)
from pipeline.incremental_keys import fov_keys_from_member_names
from util.timepoint import parse_hours, sort_key as tp_sort_key
from util.aw_safe import swallow
from util.well_token import canonical_well_label
from well_viewer.data.image_identity import load_schema
from well_viewer.domain.tp_match import tp_close
from datasets._common.dataset_paths import (
    DatasetPathsError,
    collect_input_image_stems,
    in_containers,
    out_folders,
    out_zips,
    resolve_dataset,
    schema_values_from_stems,
    split_stem_well,
)

_TIMEPOINT_HOURS_COLUMN = "timepoint_hours"
_FILENAME_COLUMN = "filename"
_OVERRIDES_FILENAME = "cell_overrides.json"
_ERRORS_DIRNAME = "errors"


class ValidateError(Exception):
    """The dataset can't be read at all. The message is user-facing."""


@dataclass(frozen=True)
class Finding:
    severity: str          # "error" | "warning" | "info"
    code: str
    message: str
    well: str = ""


@dataclass(frozen=True)
class WellReport:
    well: str
    missing: tuple[tuple[str, str], ...]   # (fov, timepoint) pairs in input but not done
    csv_state: str          # "ok" | "missing" | "empty" | "unreadable"
    has_in_container: bool
    has_out_zip: bool
    has_out_folder: bool = False
    #: The run recorded this well as having segmented zero cells everywhere —
    #: its empty output is a measurement, not a gap, so it must not be swept
    #: into a forced reprocess that would produce the same empty result.
    zero_cell_recorded: bool = False
    #: The run journal recorded this well's output as produced remotely and
    #: never transferred. Like ``zero_cell_recorded`` this is an *exemption from
    #: forcing*, not a health state: the well needs a download, and re-running
    #: the segmentation would spend hours reproducing output that already
    #: exists on the far side.
    not_fetched_recorded: bool = False
    #: Coverage sizes, carried so a finished scan can be written back as the
    #: dataset's completeness record without a second pass over the files.
    #: ``missing`` alone cannot reconstruct them: it says how much is absent,
    #: not how much was asked for, and a plate map needs both to draw a
    #: fraction. See :mod:`datasets.validate.record`.
    intent: int = 0
    covered_csv: int = 0
    covered_any: int = 0

    @property
    def complete(self) -> bool:
        return not self.missing


def well_health(report: WellReport) -> str:
    """Classify a :class:`WellReport` into one of the four dataset-health
    states the plate map paints.

    The rule itself is ``pipeline.completeness.health``, shared with the
    pipeline's own end-of-run check so a well cannot read complete on one
    surface and partial on another. This wrapper exists because the two callers
    hold different records: everything in this module already has a
    ``WellReport`` in hand, and both the live per-well progress callback and the
    final report go through here.
    """
    return _health(
        missing=report.missing,
        csv_state=report.csv_state,
        has_in_container=report.has_in_container,
        has_out_zip=report.has_out_zip,
        has_out_folder=report.has_out_folder,
    )


def wells_needing_force(report: "DatasetReport") -> tuple[str, ...]:
    """Wells an incremental run would skip forever, so it must be told to force.

    A well with processed images and no measurements CSV has no *missing*
    ``(fov, timepoint)`` pairs — its output archive's members already cover
    everything its input holds — so ``incremental_done_fov_keys`` calls it done
    and the run skips it. That is exactly the well whose CSV needs
    regenerating, and only a per-well force
    (``process_microscopy --force_wells``) will do it.

    Wells that are merely *partial* are deliberately not here: an incremental
    run does fix those, at a fraction of the cost of re-segmenting them whole.

    Two kinds of well are exempt, and both for the same reason — the run's own
    records say a forced reprocess is the wrong remedy. One segmented zero cells
    and would produce the same empty result again. The other was **produced on a
    remote host and never transferred**: its output exists, the gap is the
    download, and re-segmenting the plate to recover a failed transfer is hours
    of compute for nothing. Leaving that well on this list would have the tool
    print "this needs a fetch, not a reprocess" in its findings and then hand
    the very same well to the button that reprocesses it.
    """
    return tuple(
        w.well for w in report.wells
        if well_health(w) == "missing"
        and not w.zero_cell_recorded
        and not w.not_fetched_recorded
    )


@dataclass(frozen=True)
class ScanProgress:
    """Optional callbacks that follow a :func:`build_report` scan while it runs.

    A scan over a full plate opens every well's input archive and reads every
    measurements CSV, which takes long enough that a UI with no progress
    channel sits visibly still for the whole run. Both callbacks are plain
    Python callables so this module stays Qt-free.

    ``on_wells`` fires once, before any well is scanned, with the complete set
    of wells the scan will cover — that is the scan's real scope, which a
    caller can only otherwise guess at.

    ``on_well_done`` fires once per well as that well's diff finishes.

    **Threading.** ``on_wells`` is called on the calling thread;
    ``on_well_done`` is called on whichever pool thread scanned that well, in
    completion order (which is *not* the report's own well order). A caller
    that touches UI state from it must marshal the call onto its own thread —
    ``ValidateWorker`` does this by making both callbacks bare Qt signal
    emissions, which are queued to the GUI thread.
    """

    on_wells: Callable[[tuple[str, ...]], None] | None = None
    on_well_done: Callable[[WellReport], None] | None = None


@dataclass(frozen=True)
class DatasetReport:
    root: Path
    wells: tuple[WellReport, ...] = ()
    findings: tuple[Finding, ...] = ()
    csv_prefixes: tuple[str, ...] = ()
    fov_field_present: bool = True
    stale_available_timepoints: tuple[str, ...] = ()
    missing_available_timepoints: tuple[str, ...] = ()
    stale_available_fovs: tuple[str, ...] = ()
    missing_available_fovs: tuple[str, ...] = ()
    stale_override_count: int = 0

    @property
    def is_clean(self) -> bool:
        return not any(f.severity == "error" for f in self.findings) and all(
            w.complete for w in self.wells
        )

    @property
    def repairable(self) -> bool:
        return bool(
            self.stale_available_timepoints or self.missing_available_timepoints
            or self.stale_available_fovs or self.missing_available_fovs
            or self.stale_override_count
        )


def _prefix_and_well(csv_path: Path) -> tuple[str, str] | None:
    """Split a measurements-CSV stem into ``(prefix, canonical_well)``, or
    ``None`` if the stem doesn't end in a well-like token at all."""
    return split_stem_well(csv_path.stem)


def _csv_header(path: Path) -> list[str] | None:
    """The CSV's header row, or ``None`` if the file can't even be opened or
    decoded as text (distinct from a header that's readable but lacks the
    expected column — see the phantom-CSV check in :func:`build_report`,
    which must not mistake "genuinely corrupt measurements CSV" for "an
    unrelated file that happens to end in a well token")."""
    try:
        with path.open(newline="") as handle:
            return next(csv.reader(handle), [])
    except (OSError, UnicodeDecodeError, csv.Error):
        return None


def _read_csv_rows(path: Path) -> tuple[list[dict], list[str], str]:
    """Return ``(rows, fieldnames, state)``. *state* distinguishes "CSV
    absent" (checked by the caller before this is called) from "present but
    zero rows" and "present but unparseable" — ``read_done_fov_keys`` alone
    can't tell those apart, and the distinction is the point of this feature.
    """
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames:
                return [], [], "unreadable"
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error):
        return [], [], "unreadable"
    if not rows:
        return [], fieldnames, "empty"
    return rows, fieldnames, "ok"


def _member_names(container: Path) -> set[str] | None:
    """Every member/file name in *container*, or ``None`` if it can't be read
    (zero-member/corrupt zip)."""
    if container.is_dir():
        return {p.name for p in container.iterdir() if p.is_file()}
    try:
        with zipfile.ZipFile(container) as zf:
            names = {Path(n).name for n in zf.namelist() if not n.endswith("/")}
    except (OSError, zipfile.BadZipFile):
        return None
    return names if names else None


@dataclass(frozen=True)
class WellEvidence:
    """What the run itself recorded about a well, beyond what its files show.

    CLAUDE.md's first debugging rule for a wrong number here is to read the
    run's own failure records before reading any application code, and this is
    that evidence: ``pipeline_info.json``'s ``run_report`` block (written by
    ``pipeline.run_report.finalize_run_report`` at the end of a run) and the
    per-failure tracebacks under ``out/errors/``.

    It is what separates the two datasets that look identical on disk — a well
    that legitimately segmented zero cells at every timepoint, and a well whose
    measurements CSV was lost. Both have images and no CSV. Only one of them is
    a defect, and only one of them is worth re-segmenting.

    A third source joins those two: ``services.run_journal``, which records
    what a run did to each well *as it happened* rather than at the end. It is
    the only one that survives a run dying mid-way, and the only one that can
    say a well's output was produced on a remote host and never made it here —
    which on disk is indistinguishable from a well that was never run.
    """

    groups_total: int | None = None
    groups_covered: int | None = None
    recorded_failures: tuple[str, ...] = ()
    error_files: tuple[str, ...] = ()
    #: What the run journal last recorded for this well, or ``""``. One of
    #: ``services.run_journal.WELL_STATES`` — never re-spelled here, so this
    #: cannot drift from the vocabulary the runner writes.
    journal_state: str = ""
    journal_reason: str = ""

    @property
    def stranded_on_remote(self) -> bool:
        """The run produced this well's output and it never arrived locally.

        The one thing no artefact can report, because the evidence for it is
        the *absence* of a file. Worth its own answer: the remedy is to fetch,
        not to re-segment, and re-segmenting a plate to recover a transfer that
        failed is hours of compute for nothing.
        """
        return self.journal_state == "not-fetched"

    @property
    def recorded(self) -> bool:
        """True when the run finished and wrote a record for this well."""
        return self.groups_total is not None

    @property
    def segmented_zero_cells(self) -> bool:
        """The run completed this well and it produced no rows anywhere.

        ``groups_covered`` is read back from the finished CSV, so zero coverage
        across a well the run *did* attempt in full, with nothing failed and no
        traceback on disk, is a measurement — this well had no cells — not a
        gap. Re-segmenting it would produce the same empty result.
        """
        return (
            self.recorded
            and (self.groups_total or 0) > 0
            and self.groups_covered == 0
            and not self.recorded_failures
            and not self.error_files
        )


def _read_run_report(info: dict) -> dict[str, WellEvidence]:
    """The sidecar's ``run_report`` block, keyed by canonical well."""
    block = info.get("run_report")
    if not isinstance(block, dict):
        return {}
    wells = block.get("wells")
    if not isinstance(wells, dict):
        return {}
    out: dict[str, WellEvidence] = {}
    for raw_well, rec in wells.items():
        if not isinstance(rec, dict):
            continue
        well = canonical_well_label(str(raw_well)) or str(raw_well)
        failed = rec.get("failed")
        out[well] = WellEvidence(
            groups_total=_as_int(rec.get("groups_total")),
            groups_covered=_as_int(rec.get("groups_covered")),
            recorded_failures=tuple(
                str(f) for f in (failed if isinstance(failed, list) else [])),
        )
    return out


def _read_run_journal(paths) -> dict[str, tuple[str, str]]:
    """The run journal's per-well states, keyed by canonical well.

    Read from the output directory, where the runner writes it — not from the
    sidecar, which a remote fetch overwrites wholesale. A dataset with no
    journal (every dataset produced before it existed, and every one produced
    by the CLI) yields ``{}`` and the rest of the scan is unaffected: this
    evidence adds answers, it never withholds any.
    """
    from services.run_journal import read_journal

    journal = read_journal(paths.out_dir)
    if journal is None:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for raw_well, record in journal.wells.items():
        well = canonical_well_label(str(raw_well)) or str(raw_well)
        out[well] = (record.state, record.reason)
    return out


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scan_error_files(paths) -> dict[str, tuple[str, ...]]:
    """Per-well traceback files the run left under ``out/errors/``.

    Named ``<well>_<fov>_pid<N>.txt`` (or ``<well>_pid<N>.txt`` when a whole
    well failed), so the well is the leading token up to the first separator
    that yields a well label.
    """
    err_dir = paths.out_dir / _ERRORS_DIRNAME
    found: dict[str, list[str]] = {}
    if not err_dir.is_dir():
        return {}
    with swallow(f"scan {err_dir}"):
        for path in sorted(err_dir.glob("*.txt")):
            well = canonical_well_label(path.stem.split("_")[0])
            if well:
                found.setdefault(well, []).append(path.name)
    return {well: tuple(names) for well, names in found.items()}


def _read_sidecar(paths) -> dict:
    info_path = paths.out_dir / "pipeline_info.json"
    if not info_path.exists():
        raise ValidateError(
            f"No pipeline_info.json in {paths.out_dir} — this doesn't look like "
            "an Analyze output folder."
        )
    try:
        info = json.loads(info_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidateError(f"Could not read pipeline_info.json ({exc}).") from exc
    if not isinstance(info, dict):
        raise ValidateError("pipeline_info.json is not a JSON object.")
    return info


def read_dataset_settings(root: Path | str) -> tuple[dict, str]:
    """The dataset's own recorded ``pipeline_info``, for pre-filling a run.

    Returns ``(info, problem)``. *problem* is a short user-facing sentence when
    the sidecar can't be read at all, with *info* empty in that case. Never
    raises: the caller's actual job — handing this dataset to Analyze so a
    partial run can be finished — still goes ahead without the settings; it
    just has to say out loud that it did, since a run reconfigured by hand
    against the wrong ``csv_prefix`` or schema reads its already-done set from
    files that don't exist.
    """
    try:
        return _read_sidecar(resolve_dataset(root, label="dataset")), ""
    except (ValidateError, DatasetPathsError) as exc:
        return {}, str(exc)


def _classify_csvs(paths) -> tuple[dict[str, list[Path]], set[str], list[Finding]]:
    """Glob ``out/*.csv`` directly (never through ``out_csvs()``, which
    plainly overwrites on a duplicate well key) and split it into
    ``(candidates, prefixes, findings)`` — candidates keyed by well, with a
    phantom-CSV finding for anything that looks like a well CSV by name but
    isn't one by content."""
    findings: list[Finding] = []
    candidates: dict[str, list[Path]] = {}
    prefixes: set[str] = set()
    for csv_path in sorted(paths.out_dir.glob("*.csv")):
        parsed = _prefix_and_well(csv_path)
        if parsed is None:
            continue
        prefix, well = parsed
        header = _csv_header(csv_path)
        # A header that reads fine but lacks the expected column is an
        # unrelated file, not a broken measurements CSV — phantom. A header
        # that can't be read at all (None) is a genuine candidate that
        # _read_csv_rows will classify "unreadable"; conflating the two would
        # misreport a corrupt measurements CSV as an unrelated file.
        if header is not None and _TIMEPOINT_HOURS_COLUMN not in header:
            findings.append(Finding(
                severity="warning", code="phantom_csv", well=well,
                message=(
                    f"{csv_path.name} looks like a well CSV by name but has no "
                    f"'{_TIMEPOINT_HOURS_COLUMN}' column — probably an unrelated "
                    "file, not a measurements CSV. It is not counted as this "
                    "well's data."
                ),
            ))
            continue
        candidates.setdefault(well, []).append(csv_path)
        prefixes.add(prefix)

    if len(prefixes) > 1:
        findings.append(Finding(
            severity="warning", code="mixed_csv_prefix",
            message=(
                f"out/ holds measurements CSVs from {len(prefixes)} different "
                f"--csv_prefix runs: {sorted(prefixes)}. Wells may be double-"
                "counted or mismatched between runs."
            ),
        ))
    return candidates, prefixes, findings


def _well_intent(
    container: Path | None, members: set[str] | None, *, schema_fields, sep: str,
) -> tuple[set[tuple[str, str]], str | None]:
    """``(fov, timepoint)`` pairs the well's raw input actually contains, read
    via the same member-name parser the pipeline's own incremental diff
    uses, pointed at ``in/`` instead of ``out/``. Returns
    ``(intent, unreadable_message)`` — the message is set only when the
    container itself can't be read at all.

    *members* is the container's already-listed member names. Deriving the keys
    from those, through the readers' own
    ``incremental_keys.fov_keys_from_member_names``, is the same computation the
    two ``read_done_fov_keys_from_*`` entry points perform after listing — this
    just doesn't list the container a second time to get there. The folder form
    keeps its dotfile filter (``skip_hidden``); the zip form never had one.
    """
    if container is None:
        return set(), None
    if members is None:
        return set(), f"{container.name} could not be read as a zip/folder."
    # `images_only=True`, exactly as `pipeline.completeness.coverage_for_well`
    # passes it. This is an *intent* count — how many FOV-groups the user's
    # input holds — and without it an AppleDouble twin parses one schema slot
    # early, contributes a key nothing in `done` can ever match, and the well
    # reads `partial` for ever. Converting `coverage_for_well` and not this
    # left the two surfaces disagreeing about the same well: Review Home said
    # complete, Datasets → Validate said partial, and ARCHITECTURE claims in
    # writing that cannot happen.
    intent = fov_keys_from_member_names(
        members, schema=schema_fields, sep=sep, skip_hidden=container.is_dir(),
        images_only=True)
    return intent, None


def _well_done(
    parsed_csvs: dict[Path, tuple[list[dict], list[str], str]],
    out_zip: Path | None, out_folder: Path | None,
    *, schema_fields, sep: str,
) -> tuple[set[tuple[str, str]], str]:
    """``(fov, timepoint)`` pairs already recorded for a well, plus the
    three-way CSV state (``ok`` / ``empty`` / ``unreadable`` / ``missing``)
    that ``read_done_fov_keys`` alone can't distinguish. Unions the CSV rows
    with *both* possible image-evidence sources — a dataset is written
    either zipped or folder-mode, never both, so only one of ``out_zip`` /
    ``out_folder`` is ever non-None in practice, but checking both here
    (rather than the caller picking one) means this function alone decides
    what "done" means, matching ``pipeline.incremental_keys.
    incremental_done_fov_keys``'s own three-way union.

    *parsed_csvs* maps each of the well's candidate CSVs to its already-parsed
    ``(rows, fieldnames, state)``. The scan parses each CSV exactly once and
    shares the result with :func:`_row_content_findings`, which used to re-parse
    every one of them — on a real plate that duplicate pass was the single
    largest cost in the whole scan.

    The CSV's own keys come back alongside the union because the two differ in a
    way that matters: a well whose output archive is full and whose CSV is empty
    has nothing missing, and only the CSV-only count says that the measurements
    the plots read are not there."""
    csv_keys: set[tuple[str, str]] = set()
    for rows, fieldnames, state in parsed_csvs.values():
        if state == "ok":
            csv_keys |= csv_keys_from_rows(rows, fieldnames)
    csv_state = reduce_csv_state(state for _rows, _fn, state in parsed_csvs.values())
    done = done_keys(
        csv_keys=csv_keys, out_zip=out_zip, out_folder=out_folder,
        schema=schema_fields, sep=sep)
    return done, csv_state, csv_keys


def _row_content_findings(
    well: str, container: Path, members: set[str] | None,
    parsed_csvs: dict[Path, tuple[list[dict], list[str], str]],
) -> list[Finding]:
    """timepoint/timepoint_hours agreement + filename-in-archive presence,
    for a well whose CSV has already been confirmed to parse.

    Takes the container's members and the parsed CSV rows rather than reading
    either again — both were read moments earlier by the same well's scan."""
    findings: list[Finding] = []
    members = members or set()
    for rows, _fieldnames, state in parsed_csvs.values():
        if state != "ok":
            continue
        for row in rows:
            tp = row.get("timepoint")
            hours_raw = row.get(_TIMEPOINT_HOURS_COLUMN)
            if tp is not None and hours_raw not in (None, ""):
                try:
                    hours = float(hours_raw)
                except (TypeError, ValueError):
                    hours = None
                parsed_tp = parse_hours(tp)
                if (
                    hours is not None and math.isfinite(hours)
                    and parsed_tp is not None and math.isfinite(parsed_tp)
                    and not tp_close(parsed_tp, hours)
                ):
                    findings.append(Finding(
                        severity="error", code="tp_hours_mismatch", well=well,
                        message=(
                            f"Well {well}: row's timepoint {tp!r} doesn't match "
                            f"its timepoint_hours {hours_raw!r}."
                        ),
                    ))
            fname = row.get(_FILENAME_COLUMN)
            if fname and fname not in members:
                findings.append(Finding(
                    severity="error", code="filename_not_in_input", well=well,
                    message=(
                        f"Well {well}: row references {fname!r}, which isn't "
                        f"in {container.name}."
                    ),
                ))
    return findings


def _no_csv_finding(well: str, evidence: WellEvidence) -> Finding:
    """Why this well has images and no measurements CSV, and what fixes it.

    The four answers are genuinely different and were once one hedged sentence
    ("it may have segmented zero cells at every timepoint"), which told a user
    neither what happened nor what to do:

    * the run recorded the well, covered none of its FOV-groups, failed
      nothing and left no traceback — it segmented zero cells, which is a
      measurement. Reprocessing produces the same empty result, so this is
      ``info`` and the well is left off the force list.
    * the run recorded failures, or left tracebacks under ``out/errors/`` —
      a real gap, with the run's own records naming it. ``error``.
    * the run journal recorded the output as produced on a remote host and
      never transferred — the compute is done and the gap is the download, so
      the remedy is to fetch rather than to re-segment. ``error``.
    * no record at all (the run died before writing one, or predates the
      block) — unknown, so it is reported as a gap rather than assumed
      benign, since assuming benign is how a missing CSV goes unnoticed.

    **``no_csv_failed`` and ``no_csv`` need a forced reprocess**, because an
    incremental run counts this well's own output images as already done and
    reports it up to date, so it would skip the well forever. The other two do
    not, and both are held off ``wells_needing_force`` for that reason — one
    would reproduce the same empty result, the other would spend hours
    recomputing output that already exists on the far side.
    """
    forced_remedy = (
        "An incremental run counts this well's own output images as already "
        "done and reports it up to date, so only a forced reprocess of this "
        "well regenerates the CSV."
    )
    if evidence.segmented_zero_cells:
        return Finding(
            severity="info", code="no_csv_zero_cells", well=well,
            message=(
                f"Well {well}: no measurements CSV, and the run's own record "
                f"says why — it processed all {evidence.groups_total} "
                "FOV-group(s) and segmented zero cells in every one. Nothing "
                "to recover; reprocessing would produce the same empty result."
            ),
        )
    if evidence.recorded_failures or evidence.error_files:
        detail = []
        if evidence.recorded_failures:
            detail.append(
                f"{len(evidence.recorded_failures)} FOV-group(s) recorded as "
                f"failed (e.g. {', '.join(evidence.recorded_failures[:3])})")
        if evidence.error_files:
            detail.append(
                f"{len(evidence.error_files)} traceback(s) under out/errors/ "
                f"(e.g. {', '.join(evidence.error_files[:3])})")
        return Finding(
            severity="error", code="no_csv_failed", well=well,
            message=(
                f"Well {well}: images exist but no measurements CSV, and the "
                f"run recorded why — {'; '.join(detail)}. Read those before "
                f"re-running. {forced_remedy}"
            ),
        )
    if evidence.stranded_on_remote:
        detail = f" ({evidence.journal_reason})" if evidence.journal_reason else ""
        return Finding(
            severity="error", code="no_csv_not_fetched", well=well,
            message=(
                f"Well {well}: images exist but no measurements CSV, and the "
                f"run journal says the output was produced and never reached "
                f"this machine{detail}. Re-run this input against the same "
                f"remote — the compute is already done, and what is missing is "
                f"the transfer, so a forced reprocess would repeat hours of "
                f"work to recover a download."
            ),
        )
    return Finding(
        severity="warning", code="no_csv", well=well,
        message=(
            f"Well {well}: images exist but no measurements CSV was found, and "
            "the run left no completeness record for it — so whether it "
            "segmented zero cells or lost its CSV cannot be told from disk. "
            f"{forced_remedy}"
        ),
    )


def _scan_one_well(
    well: str, *, in_map, zip_map, folder_map, csv_candidates, schema_fields, sep: str,
    fov_field_present: bool, evidence_map: dict[str, WellEvidence] | None = None,
) -> tuple[WellReport, list[Finding], set[str] | None]:
    """Read-only per-well diff: intent (from the input container) vs. done
    (from the CSV/out-zip/out-folder). Pulled out of :func:`_scan_wells` so
    it can run on its own thread — nothing here touches another well's files
    or any shared state, only this well's own container/CSV reads.

    Every read this well needs happens **here, once**: the input container is
    listed once and each candidate CSV parsed once, and both results are then
    handed to the three consumers below rather than each re-opening what it
    wants. Returns the container's member names alongside the report so the
    caller's own sidecar-availability check can reuse them too.
    """
    findings: list[Finding] = []
    evidence = (evidence_map or {}).get(well, WellEvidence())
    container = in_map.get(well)
    members = _member_names(container) if container is not None else None
    csvs = csv_candidates.get(well, [])
    parsed_csvs = {path: _read_csv_rows(path) for path in csvs}

    intent, unreadable_message = _well_intent(
        container, members, schema_fields=schema_fields, sep=sep)
    if unreadable_message is not None:
        findings.append(Finding(
            severity="error", code="unreadable_in_container", well=well,
            message=f"Well {well}: {unreadable_message}",
        ))

    out_zip = zip_map.get(well)
    out_folder = folder_map.get(well)
    done, csv_state, csv_keys = _well_done(
        parsed_csvs, out_zip, out_folder, schema_fields=schema_fields, sep=sep)

    # No container means no intent, so the diff below is empty either way; the
    # explicit branch keeps that readable rather than leaving it to follow from
    # _well_intent's return. The rule itself — including the single-FOV
    # timepoint-only fallback — is pipeline.completeness.missing_keys, shared
    # with the pipeline's end-of-run check.
    if container is None:
        missing: set[tuple[str, str]] = set()
    else:
        missing = missing_keys(intent, done, fov_field_present=fov_field_present)
    report = WellReport(
        well=well, missing=tuple(sorted(missing)), csv_state=csv_state,
        has_in_container=container is not None, has_out_zip=out_zip is not None,
        has_out_folder=out_folder is not None,
        zero_cell_recorded=evidence.segmented_zero_cells,
        not_fetched_recorded=evidence.stranded_on_remote,
        intent=len(intent), covered_csv=len(csv_keys), covered_any=len(done),
    )

    # A well with real recorded output (done) should have every one of its
    # own (fov, timepoint) keys show up in intent too -- done describes
    # finished *portions of the input*, so it's a subset by construction
    # when both sides are parsed against the schema that actually matches
    # this dataset's filenames. If most of done's own keys are absent from
    # intent, that's not "most of this well is unfinished" -- it's a sign
    # pipeline_info.json's schema_fields/separator don't actually match how
    # this well's raw filenames are structured, so intent's positional
    # parse is reading the wrong tokens as fov/timepoint. Left undetected,
    # that silently turns into a wall of individually-wrong missing_fov_tp
    # findings for a well that may well be genuinely complete.
    if container is not None and len(done) >= 4:
        unmatched_done = done - intent
        if len(unmatched_done) >= 0.5 * len(done):
            findings.append(Finding(
                severity="error", code="schema_mismatch_suspected", well=well,
                message=(
                    f"Well {well}: {len(unmatched_done)} of {len(done)} "
                    "recorded (fov, timepoint) pair(s) don't match anything "
                    "parsed from the raw input filenames, even though this "
                    "well has real recorded output. This usually means "
                    "pipeline_info.json's 'schema_fields'/'separator' don't "
                    "actually match how this dataset's images are named — "
                    "compare them against a real filename in this well's "
                    "in/ archive. The missing-pair count below is unreliable "
                    "until that's fixed."
                ),
            ))

    if missing:
        if fov_field_present:
            sample = sorted(missing)[:5]
            findings.append(Finding(
                severity="error", code="missing_fov_tp", well=well,
                message=(
                    f"Well {well}: {len(missing)} (fov, timepoint) pair(s) "
                    f"present in the input but not in the output — e.g. {sample}."
                ),
            ))
        else:
            sample = sorted(tp for _fov, tp in missing)[:5]
            findings.append(Finding(
                severity="error", code="missing_fov_tp", well=well,
                message=(
                    f"Well {well}: {len(missing)} timepoint(s) present in the "
                    f"input but not in the output — e.g. {sample}."
                ),
            ))
    if csv_state == "missing" and (container is not None or out_zip is not None or out_folder is not None):
        findings.append(_no_csv_finding(well, evidence))
    elif csv_state == "unreadable":
        findings.append(Finding(
            severity="error", code="unreadable_csv", well=well,
            message=f"Well {well}: its measurements CSV could not be parsed.",
        ))

    if csv_state != "missing" and (evidence.recorded_failures or evidence.error_files):
        # The run wrote down what went wrong; a scan that re-derives everything
        # from the files and never mentions those records makes the user hunt
        # for a cause the software already has on disk. A well can hold a
        # perfectly readable CSV and still have lost FOV-groups to failures.
        bits = []
        if evidence.recorded_failures:
            bits.append(f"{len(evidence.recorded_failures)} FOV-group(s) failed")
        if evidence.error_files:
            bits.append(
                f"{len(evidence.error_files)} traceback(s) in out/errors/ "
                f"(e.g. {evidence.error_files[0]})")
        findings.append(Finding(
            severity="error", code="recorded_failures", well=well,
            message=(
                f"Well {well}: the run recorded failures for this well — "
                f"{'; '.join(bits)}. Read them before re-running; they usually "
                "name the cause outright."
            ),
        ))

    if csv_state == "ok" and container is not None:
        findings.extend(_row_content_findings(well, container, members, parsed_csvs))

    return report, findings, members


def _scan_wells(
    paths, *, schema_fields, sep: str, fov_field_present: bool,
    csv_candidates: dict[str, list[Path]], workers: int = 0,
    progress: ScanProgress | None = None,
    evidence_map: dict[str, WellEvidence] | None = None,
) -> tuple[list[WellReport], list[Finding], dict[Path, set[str]]]:
    """Diff every well, returning ``(reports, findings, members_by_container)``.

    The third value is each input container's member names as this pass read
    them, so :func:`_check_sidecar_availability` can answer its own question
    from names already in memory instead of re-listing the whole input folder.
    """
    in_map = in_containers(paths)
    zip_map = out_zips(paths)
    folder_map = out_folders(paths)
    all_wells = sorted(set(in_map) | set(csv_candidates) | set(zip_map) | set(folder_map))

    if progress is not None and progress.on_wells is not None:
        progress.on_wells(tuple(all_wells))

    def _scan(well: str) -> tuple[WellReport, list[Finding], set[str] | None]:
        result = _scan_one_well(
            well, in_map=in_map, zip_map=zip_map, folder_map=folder_map,
            csv_candidates=csv_candidates, schema_fields=schema_fields, sep=sep,
            fov_field_present=fov_field_present, evidence_map=evidence_map,
        )
        if progress is not None and progress.on_well_done is not None:
            progress.on_well_done(result[0])
        return result

    # Read-only per well (no cancellation, no destination file), so there is
    # still no lock to take: the only shared state is the caller's own
    # progress callback, which is documented as being called from these
    # threads and does nothing but hand the result off. .map() (not
    # as_completed) hands results back in the same order they were submitted
    # regardless of which well's thread finishes first, so the report's
    # well/finding ordering is identical to the old sequential loop's even
    # though on_well_done fires in completion order.
    with ThreadPoolExecutor(max_workers=pick_workers(workers, len(all_wells))) as ex:
        results = list(ex.map(_scan, all_wells))

    well_reports = [report for report, _findings, _members in results]
    findings: list[Finding] = [
        f for _report, well_findings, _members in results for f in well_findings
    ]
    members_by_container: dict[Path, set[str]] = {
        in_map[well]: members
        for well, (_report, _findings, members) in zip(all_wells, results)
        if members and well in in_map
    }

    if not fov_field_present:
        findings.insert(0, Finding(
            severity="info", code="no_fov_field",
            message=(
                "This dataset's filename schema has no 'fov' field — "
                "completeness below is checked by timepoint only, ignoring "
                "FOV."
            ),
        ))
    return well_reports, findings, members_by_container


def _fov_sort_key(value: str):
    hours = parse_hours(value)
    return (hours is None, hours or 0.0, value)


def _check_sidecar_availability(
    paths, info: dict, *, schema_fields, sep: str,
    known_members: dict[Path, set[str]] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], list[Finding]]:
    """Compare the sidecar's declared availability against what ``in/`` holds.

    *known_members* is the member-name map the per-well scan just built. The
    input folder is walked **once** here, reusing those names rather than
    re-opening each archive, and both fields are then extracted from that one
    set of stems — this used to be two full serial walks of the whole input
    folder, on top of the per-well pass that had already read every archive.
    """
    findings: list[Finding] = []
    declared_tps = [str(t).strip() for t in info.get("available_timepoints", []) or [] if str(t).strip()]
    declared_fovs = [str(f).strip() for f in info.get("available_fovs", []) or [] if str(f).strip()]
    stems = collect_input_image_stems(paths.in_dir, known_zip_members=known_members)
    actual_tps = schema_values_from_stems(
        stems, schema_fields=schema_fields, sep=sep,
        field_name="timepoint", sort_key=tp_sort_key,
    )
    actual_fovs = schema_values_from_stems(
        stems, schema_fields=schema_fields, sep=sep,
        field_name="fov", sort_key=_fov_sort_key,
    )
    stale_tps = tuple(sorted(set(declared_tps) - set(actual_tps)))
    missing_tps = tuple(sorted(set(actual_tps) - set(declared_tps)))
    stale_fovs = tuple(sorted(set(declared_fovs) - set(actual_fovs)))
    missing_fovs = tuple(sorted(set(actual_fovs) - set(declared_fovs)))
    if stale_tps or missing_tps:
        findings.append(Finding(
            severity="warning", code="sidecar_timepoints_drift",
            message=(
                f"pipeline_info.json's available_timepoints disagrees with what's "
                f"on disk — stale (listed but absent): {list(stale_tps)}; "
                f"missing (on disk but unlisted): {list(missing_tps)}."
            ),
        ))
    if stale_fovs or missing_fovs:
        findings.append(Finding(
            severity="warning", code="sidecar_fovs_drift",
            message=(
                f"pipeline_info.json's available_fovs disagrees with what's on "
                f"disk — stale: {list(stale_fovs)}; missing: {list(missing_fovs)}."
            ),
        ))
    return stale_tps, missing_tps, stale_fovs, missing_fovs, findings


def _check_stale_overrides(paths, zip_map: dict[str, Path]) -> tuple[int, list[Finding]]:
    stale_count = 0
    overrides_path = paths.out_dir / _OVERRIDES_FILENAME
    if overrides_path.exists():
        data = None
        with swallow(f"read {overrides_path}"):
            data = json.loads(overrides_path.read_text())
        if isinstance(data, dict):
            for entry in data.get("overrides") or []:
                if not isinstance(entry, dict):
                    continue
                well = str(entry.get("well", "")).strip()
                stored_fp = str(entry.get("seg_fp", "")).strip()
                zip_path = zip_map.get(well)
                if not stored_fp or zip_path is None:
                    continue
                with swallow(f"stat {zip_path}"):
                    current_fp = f"{zip_path.stat().st_mtime_ns}"
                    if current_fp != stored_fp:
                        stale_count += 1
    findings = []
    if stale_count:
        findings.append(Finding(
            severity="info", code="stale_overrides",
            message=(
                f"{stale_count} cell override(s) reference a segmentation "
                "that no longer matches the current out-zip — they'll be "
                "dropped on next load regardless."
            ),
        ))
    return stale_count, findings


def build_report(
    root: Path | str, *, workers: int = 0, progress: ScanProgress | None = None,
) -> DatasetReport:
    """Scan a dataset folder and report what Analyze did and did not finish.

    Read-only. Raises :class:`ValidateError` only when the folder isn't a
    dataset at all or its sidecar can't be read — every other problem is
    reported as a :class:`Finding` rather than an exception, since the whole
    point is to describe a broken dataset rather than refuse to look at it.

    The per-well diff runs in parallel — *workers* threads (0 = auto, see
    :func:`datasets._common.concurrency.pick_workers`). Pass a
    :class:`ScanProgress` to follow the scan as it runs rather than only
    seeing its finished report; a full plate takes long enough that a caller
    with no progress channel has nothing to show for the duration.
    """
    try:
        paths = resolve_dataset(root, label="dataset")
    except DatasetPathsError as exc:
        raise ValidateError(str(exc)) from exc

    info = _read_sidecar(paths)
    schema = load_schema(info)
    schema_fields = list(schema.fields)
    sep = schema.sep
    fov_field_present = schema.has_fov

    csv_candidates, prefixes, csv_findings = _classify_csvs(paths)
    # The run's own records, read before anything is inferred from the files
    # themselves — they are the only thing that can say whether an empty well
    # is empty because it had no cells or because its CSV went missing.
    evidence_map = _read_run_report(info)
    for well, names in _scan_error_files(paths).items():
        prior = evidence_map.get(well, WellEvidence())
        evidence_map[well] = replace(prior, error_files=names)
    for well, (state, reason) in _read_run_journal(paths).items():
        prior = evidence_map.get(well, WellEvidence())
        evidence_map[well] = replace(
            prior, journal_state=state, journal_reason=reason)
    well_reports, well_findings, members_by_container = _scan_wells(
        paths, schema_fields=schema_fields, sep=sep,
        fov_field_present=fov_field_present, csv_candidates=csv_candidates,
        workers=workers, progress=progress, evidence_map=evidence_map,
    )
    stale_tps, missing_tps, stale_fovs, missing_fovs, availability_findings = (
        _check_sidecar_availability(
            paths, info, schema_fields=schema_fields, sep=sep,
            known_members=members_by_container)
    )
    stale_override_count, override_findings = _check_stale_overrides(paths, out_zips(paths))

    findings = [*csv_findings, *well_findings, *availability_findings, *override_findings]

    return DatasetReport(
        root=paths.root,
        wells=tuple(well_reports),
        findings=tuple(findings),
        csv_prefixes=tuple(sorted(prefixes)),
        fov_field_present=fov_field_present,
        stale_available_timepoints=stale_tps,
        missing_available_timepoints=missing_tps,
        stale_available_fovs=stale_fovs,
        missing_available_fovs=missing_fovs,
        stale_override_count=stale_override_count,
    )
