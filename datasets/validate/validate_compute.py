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
— is read via the same two member-name readers the pipeline's own
incremental diff uses (``pipeline.incremental_keys.read_done_fov_keys_from_zip``
/ ``_from_folder``), which parse ``(fov, timepoint)`` positionally off a
container's member names. Nothing distinguishes a raw-input container from a
processed-output one to those two readers — they only care about filename
structure — so pointing them at ``in/<WELL>.zip`` instead of
``out/<WELL>_out.zip`` reads intent instead of done, with zero new code.
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
import re
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from datasets._common.concurrency import pick_workers
from pipeline.incremental_keys import (
    read_done_fov_keys_from_folder,
    read_done_fov_keys_from_zip,
)
from util.timepoint import parse_hours, sort_key as tp_sort_key
from util.aw_safe import swallow
from well_viewer.data.image_identity import load_schema
from well_viewer.domain.tp_match import tp_close
from datasets._common.dataset_paths import (
    DatasetPathsError,
    collect_available_schema_values,
    in_containers,
    out_folders,
    out_zips,
    resolve_dataset,
)

_TRAILING_WELL_RE = re.compile(r"([A-Ha-h])(\d{1,2})$")
_TIMEPOINT_HOURS_COLUMN = "timepoint_hours"
_FILENAME_COLUMN = "filename"
_OVERRIDES_FILENAME = "cell_overrides.json"


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

    @property
    def complete(self) -> bool:
        return not self.missing


def well_health(report: WellReport) -> str:
    """Classify a :class:`WellReport` into one of the four dataset-health
    states the plate map paints.

    Lives here rather than in the controller because both the live per-well
    progress callback and the final report render the same four states, and a
    well that flipped colour between the two would be the exact kind of silent
    disagreement this feature exists to surface:

    ``complete``
        every ``(fov, timepoint)`` pair in the input is accounted for and a
        readable measurements CSV exists.
    ``partial``
        some of the input's pairs are missing from the output.
    ``missing``
        nothing is missing from the input diff, but images exist with no
        usable measurements CSV behind them.
    ``no_output``
        the well has no output evidence at all.
    """
    if report.missing:
        return "partial"
    if report.csv_state == "ok":
        return "complete"
    if report.has_in_container or report.has_out_zip or report.has_out_folder:
        return "missing"
    return "no_output"


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
    stem = csv_path.stem
    match = _TRAILING_WELL_RE.search(stem)
    if not match:
        return None
    well = f"{match.group(1).upper()}{int(match.group(2)):02d}"
    prefix = stem[: match.start()].rstrip("_-")
    return prefix, well


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


def _well_intent(container: Path | None, *, schema_fields, sep: str) -> tuple[set[tuple[str, str]], str | None]:
    """``(fov, timepoint)`` pairs the well's raw input actually contains, read
    via the same member-name parser the pipeline's own incremental diff
    uses, pointed at ``in/`` instead of ``out/``. Returns
    ``(intent, unreadable_message)`` — the message is set only when the
    container itself can't be read at all."""
    if container is None:
        return set(), None
    members = _member_names(container)
    if members is None:
        return set(), f"{container.name} could not be read as a zip/folder."
    intent = (
        read_done_fov_keys_from_folder(container, schema=schema_fields, sep=sep)
        if container.is_dir()
        else read_done_fov_keys_from_zip(container, schema=schema_fields, sep=sep)
    )
    return intent, None


def _well_done(
    csvs: list[Path], out_zip: Path | None, out_folder: Path | None,
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
    incremental_done_fov_keys``'s own three-way union."""
    done: set[tuple[str, str]] = set()
    csv_state = "missing"
    if csvs:
        states = []
        for csv_path in csvs:
            rows, fieldnames, state = _read_csv_rows(csv_path)
            states.append(state)
            if state != "ok":
                continue
            if "fov" in fieldnames and "timepoint" in fieldnames:
                for row in rows:
                    done.add((str(row.get("fov", "")), str(row.get("timepoint", ""))))
        csv_state = "ok" if "ok" in states else ("empty" if "empty" in states else "unreadable")
    if out_zip is not None:
        done |= read_done_fov_keys_from_zip(out_zip, schema=schema_fields, sep=sep)
    if out_folder is not None:
        done |= read_done_fov_keys_from_folder(out_folder, schema=schema_fields, sep=sep)
    return done, csv_state


def _row_content_findings(well: str, container: Path, csvs: list[Path]) -> list[Finding]:
    """timepoint/timepoint_hours agreement + filename-in-archive presence,
    for a well whose CSV has already been confirmed to parse."""
    findings: list[Finding] = []
    members = _member_names(container) or set()
    for csv_path in csvs:
        rows, _fieldnames, state = _read_csv_rows(csv_path)
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


def _scan_one_well(
    well: str, *, in_map, zip_map, folder_map, csv_candidates, schema_fields, sep: str,
    fov_field_present: bool,
) -> tuple[WellReport, list[Finding]]:
    """Read-only per-well diff: intent (from the input container) vs. done
    (from the CSV/out-zip/out-folder). Pulled out of :func:`_scan_wells` so
    it can run on its own thread — nothing here touches another well's files
    or any shared state, only this well's own container/CSV reads."""
    findings: list[Finding] = []
    container = in_map.get(well)
    intent, unreadable_message = _well_intent(container, schema_fields=schema_fields, sep=sep)
    if unreadable_message is not None:
        findings.append(Finding(
            severity="error", code="unreadable_in_container", well=well,
            message=f"Well {well}: {unreadable_message}",
        ))

    csvs = csv_candidates.get(well, [])
    out_zip = zip_map.get(well)
    out_folder = folder_map.get(well)
    done, csv_state = _well_done(csvs, out_zip, out_folder, schema_fields=schema_fields, sep=sep)

    if container is None:
        missing: set[tuple[str, str]] = set()
    elif fov_field_present:
        missing = intent - done
    else:
        # No 'fov' field in the schema (single-FOV pipelines): fov_tp_from_name
        # can only return "" for every intent-side key, while a written CSV
        # row's own 'fov' column is separately normalised to "1"
        # (normalize_fov's single-FOV convention). Diffing the raw
        # (fov, timepoint) pairs as-is would report every well missing
        # everything the moment 'done' came from a CSV row. The previous fix
        # for that avoided the false positive by skipping the diff outright —
        # which meant a well missing every timepoint but one read as
        # identically "complete" to a well with all of them, since 'missing'
        # was unconditionally empty regardless of what was actually on disk.
        # 'fov' carries no real information for a schema that doesn't have
        # it, so diff on timepoint alone instead: correct, and it restores
        # the per-timepoint completeness check a single-FOV dataset needs.
        intent_tps = {tp for _fov, tp in intent}
        done_tps = {tp for _fov, tp in done}
        missing = {("", tp) for tp in (intent_tps - done_tps)}
    report = WellReport(
        well=well, missing=tuple(sorted(missing)), csv_state=csv_state,
        has_in_container=container is not None, has_out_zip=out_zip is not None,
        has_out_folder=out_folder is not None,
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
        findings.append(Finding(
            severity="warning", code="no_csv", well=well,
            message=(
                f"Well {well}: images exist but no measurements CSV was found "
                "(it may have segmented zero cells at every timepoint)."
            ),
        ))
    elif csv_state == "unreadable":
        findings.append(Finding(
            severity="error", code="unreadable_csv", well=well,
            message=f"Well {well}: its measurements CSV could not be parsed.",
        ))

    if csv_state == "ok" and container is not None:
        findings.extend(_row_content_findings(well, container, csvs))

    return report, findings


def _scan_wells(
    paths, *, schema_fields, sep: str, fov_field_present: bool,
    csv_candidates: dict[str, list[Path]], workers: int = 0,
    progress: ScanProgress | None = None,
) -> tuple[list[WellReport], list[Finding]]:
    in_map = in_containers(paths)
    zip_map = out_zips(paths)
    folder_map = out_folders(paths)
    all_wells = sorted(set(in_map) | set(csv_candidates) | set(zip_map) | set(folder_map))

    if progress is not None and progress.on_wells is not None:
        progress.on_wells(tuple(all_wells))

    def _scan(well: str) -> tuple[WellReport, list[Finding]]:
        result = _scan_one_well(
            well, in_map=in_map, zip_map=zip_map, folder_map=folder_map,
            csv_candidates=csv_candidates, schema_fields=schema_fields, sep=sep,
            fov_field_present=fov_field_present,
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

    well_reports = [report for report, _findings in results]
    findings: list[Finding] = [f for _report, well_findings in results for f in well_findings]

    if not fov_field_present:
        findings.insert(0, Finding(
            severity="info", code="no_fov_field",
            message=(
                "This dataset's filename schema has no 'fov' field — "
                "completeness below is checked by timepoint only, ignoring "
                "FOV."
            ),
        ))
    return well_reports, findings


def _fov_sort_key(value: str):
    hours = parse_hours(value)
    return (hours is None, hours or 0.0, value)


def _check_sidecar_availability(
    paths, info: dict, *, schema_fields, sep: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], list[Finding]]:
    findings: list[Finding] = []
    declared_tps = [str(t).strip() for t in info.get("available_timepoints", []) or [] if str(t).strip()]
    declared_fovs = [str(f).strip() for f in info.get("available_fovs", []) or [] if str(f).strip()]
    actual_tps = collect_available_schema_values(
        paths.in_dir, schema_fields=schema_fields, sep=sep,
        field_name="timepoint", sort_key=tp_sort_key,
    )
    actual_fovs = collect_available_schema_values(
        paths.in_dir, schema_fields=schema_fields, sep=sep,
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
    well_reports, well_findings = _scan_wells(
        paths, schema_fields=schema_fields, sep=sep,
        fov_field_present=fov_field_present, csv_candidates=csv_candidates,
        workers=workers, progress=progress,
    )
    stale_tps, missing_tps, stale_fovs, missing_fovs, availability_findings = (
        _check_sidecar_availability(paths, info, schema_fields=schema_fields, sep=sep)
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
