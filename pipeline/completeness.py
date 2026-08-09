"""The single definition of "is this well complete", shared by the pipeline's
end-of-run check and the Datasets **Validate & Repair** tool.

Two callers need the same answer at two different moments. The pipeline asks it
at the end of a run, about the plate it has just written, and records the answer
into ``pipeline_info.json`` so the Review side can report completeness without
re-deriving it. Validate asks it on demand, about any dataset, and layers
findings, run-record evidence and repair on top. If each spelled the rule
itself, the two would drift and a well would read complete in one surface and
partial in the other, with neither looking wrong — so the rule lives here and
both import it.

The module is in two halves, and the split matters.

**The predicate** — everything up to ``coverage_for_well`` — is what both
callers share. Those functions take data the caller already holds (member
names, parsed CSV rows) and open exactly one thing between them: the well's
output container, through :mod:`pipeline.incremental_keys`. That constraint is
what lets Validate delegate without changing how many times it touches the
disk, which its own tests pin as an exact number.

**The scan** — ``scan_run`` and its helpers — belongs to the pipeline alone. It
walks a directory the run has just written and produces the ``run_check`` block
the sidecar carries. Validate has its own scan, which parallelises across wells
and layers findings, run-record evidence and repair on top; it does not call
this one. Two scans, shaped by what each caller needs, over one predicate.

Stdlib plus :mod:`pipeline.incremental_keys` only. No Qt, no imaging stack, no
pandas — the module has to import cleanly inside a pipeline run on a remote host
where the GUI is not installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline.incremental_keys import (
    fov_keys_from_member_names,
    read_done_fov_keys_from_folder,
    read_done_fov_keys_from_zip,
)

#: The four states a well can be in, in the order a plate legend reads them.
#: Every surface that paints or labels well health imports this rather than
#: writing its own copy — ``tests/test_no_second_completeness_predicate.py``
#: fails on a second literal roster anywhere in the repo.
HEALTH_STATES = ("complete", "partial", "missing", "no_output")

#: The CSV columns a ``(fov, timepoint)`` key is read out of.
_FOV_COLUMN = "fov"
_TIMEPOINT_COLUMN = "timepoint"


@dataclass(frozen=True)
class WellCoverage:
    """How much of one well's input its output actually accounts for.

    Two "covered" counts, deliberately, because they answer different
    questions. *covered_any* unions the measurements CSV with the output
    archive, which is what an incremental re-run would treat as already done —
    so it is the honest denominator for "how much would re-running recover".
    *covered_csv* counts only the CSV, which is the file every plot and every
    statistic actually reads. A well whose archive is whole but whose CSV is
    empty has full ``covered_any`` and zero ``covered_csv``, and that gap is
    precisely the state a coverage figure derived from either number alone
    would hide.
    """

    well: str
    intent: int
    covered_csv: int
    covered_any: int
    missing: tuple[tuple[str, str], ...]
    csv_state: str
    has_in_container: bool
    has_out_zip: bool
    has_out_folder: bool
    failed: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing


def csv_keys_from_rows(rows, fieldnames) -> set[tuple[str, str]]:
    """The ``(fov, timepoint)`` pairs a per-well CSV's rows record.

    Values are taken verbatim from the ``fov`` / ``timepoint`` columns — the
    exact strings ``parse_filename`` produced when the rows were written — so a
    key compares equal to one parsed from a filename without re-normalisation,
    the same discipline :func:`pipeline.incremental_keys.read_done_fov_keys`
    applies when reading the file itself. A CSV missing either column
    contributes nothing rather than a set of half-empty keys.
    """
    if _FOV_COLUMN not in fieldnames or _TIMEPOINT_COLUMN not in fieldnames:
        return set()
    return {
        (str(row.get(_FOV_COLUMN, "")), str(row.get(_TIMEPOINT_COLUMN, "")))
        for row in rows
    }


def reduce_csv_state(states) -> str:
    """Collapse the per-file states of a well's candidate CSVs into one.

    A well can have more than one CSV matching its token, and they need not
    agree. One readable file makes the well readable, so ``ok`` wins outright;
    failing that, ``empty`` (a file that parsed and held no rows) is a better
    description than ``unreadable`` (a file that did not parse at all), because
    only the second is a defect. No files at all is the caller's ``missing``.
    """
    states = list(states)
    if not states:
        return "missing"
    if "ok" in states:
        return "ok"
    return "empty" if "empty" in states else "unreadable"


def done_keys(
    *, csv_keys: set[tuple[str, str]], out_zip: Path | None,
    out_folder: Path | None, schema, sep: str,
) -> set[tuple[str, str]]:
    """Every ``(fov, timepoint)`` pair a well has recorded, from all three
    possible sources.

    A dataset is written either zipped or folder-mode, never both, so only one
    of *out_zip* / *out_folder* is ever non-``None`` in practice — but both are
    checked here rather than the caller picking one, so this function alone
    decides what "done" means, matching
    ``pipeline.incremental_keys.incremental_done_fov_keys``'s own three-way
    union. Image evidence counts even when the CSV does not: an archive holding
    a finished FOV-group is a group an incremental run will not redo.

    *csv_keys* arrives already extracted (see :func:`csv_keys_from_rows`), so
    this opens at most the output container and never reads a CSV.
    """
    done = set(csv_keys)
    if out_zip is not None:
        done |= read_done_fov_keys_from_zip(out_zip, schema=schema, sep=sep)
    if out_folder is not None:
        done |= read_done_fov_keys_from_folder(out_folder, schema=schema, sep=sep)
    return done


def missing_keys(intent, done, *, fov_field_present: bool) -> set[tuple[str, str]]:
    """What the input holds and the output does not.

    With a ``fov`` field in the schema this is the plain set difference. Without
    one (single-FOV pipelines) it cannot be: ``fov_tp_from_name`` can only
    return ``""`` for every intent-side key, while a written CSV row's own
    ``fov`` column is separately normalised to ``"1"`` by ``normalize_fov``'s
    single-FOV convention, so diffing the raw pairs would report every well as
    missing everything the moment *done* came from a CSV row. Skipping the diff
    outright is worse still — it made a well missing every timepoint but one
    read as identically complete to a well with all of them. ``fov`` carries no
    information for a schema that does not have it, so the diff runs on
    timepoint alone: correct, and it keeps the per-timepoint completeness check
    a single-FOV dataset needs.
    """
    if fov_field_present:
        return set(intent) - set(done)
    intent_tps = {tp for _fov, tp in intent}
    done_tps = {tp for _fov, tp in done}
    return {("", tp) for tp in (intent_tps - done_tps)}


def health(
    *, missing, csv_state: str, has_in_container: bool, has_out_zip: bool,
    has_out_folder: bool,
) -> str:
    """Classify one well into one of :data:`HEALTH_STATES`.

    Takes the five primitives rather than a record type, because its two
    callers hold different records — Validate a ``WellReport``, the pipeline a
    :class:`WellCoverage` — and a shared classifier that demanded one of them
    would force the other to build a throwaway.

    ``complete``
        every ``(fov, timepoint)`` pair in the input is accounted for and a
        readable measurements CSV exists.
    ``partial``
        some of the input's pairs are missing from the output.
    ``missing``
        nothing is missing from the input diff, but images exist with no usable
        measurements CSV behind them.
    ``no_output``
        the well has no evidence at all.
    """
    if missing:
        return "partial"
    if csv_state == "ok":
        return "complete"
    if has_in_container or has_out_zip or has_out_folder:
        return "missing"
    return "no_output"


def coverage_for_well(
    well: str, *, in_members, csv_keys, csv_state: str, out_zip: Path | None,
    out_folder: Path | None, schema, sep: str, fov_field_present: bool,
    skip_hidden: bool = False, failed=(),
) -> WellCoverage:
    """Assemble one well's :class:`WellCoverage` from data the caller already
    holds.

    *in_members* is the input container's member names, or ``None`` when the
    container is absent or could not be read — both of which yield an empty
    intent, so a well whose input cannot be listed is never reported as missing
    output it was never asked to produce. The caller is expected to have said
    something about the unreadable case already; silence here is deliberate,
    not an oversight.

    *skip_hidden* follows the container's kind, and the caller passes it because
    only the caller knows: a folder can hold dotfiles the filesystem put there,
    a zip's members are whatever was written into it.

    This opens the output container and nothing else. Keeping input listing and
    CSV parsing on the caller's side is what lets Validate delegate to this
    module without changing its per-well read count, which
    ``tests/test_validate_compute.py`` pins exactly.
    """
    if in_members is None:
        intent: set[tuple[str, str]] = set()
    else:
        intent = fov_keys_from_member_names(
            in_members, schema=schema, sep=sep, skip_hidden=skip_hidden)
    done = done_keys(
        csv_keys=csv_keys, out_zip=out_zip, out_folder=out_folder,
        schema=schema, sep=sep)
    missing = missing_keys(intent, done, fov_field_present=fov_field_present)
    return WellCoverage(
        well=well,
        intent=len(intent),
        covered_csv=len(csv_keys),
        covered_any=len(done),
        missing=tuple(sorted(missing)),
        csv_state=csv_state,
        has_in_container=in_members is not None,
        has_out_zip=out_zip is not None,
        has_out_folder=out_folder is not None,
        failed=tuple(failed),
    )


# ── the end-of-run scan ─────────────────────────────────────────────────────
#
# Everything above is the rule, shared with the Datasets Validate tool.
# Everything below is the pipeline's own scan: it walks a directory the run has
# just written and produces the ``run_check`` block the sidecar carries and the
# Review side reports. Validate does not use it — its scan parallelises across
# wells, layers findings and run-record evidence on top, and pins its read
# counts with a test — but both go through the same predicate, so they cannot
# disagree about whether a given well is complete.

#: How many missing ``(fov, timepoint)`` pairs a well records before the list
#: is truncated. The count is kept whole in ``missing_count``; this bounds only
#: what a badly-broken plate can do to the size of the sidecar.
MAX_MISSING_PER_WELL = 20

#: Traceback files read to name their exception type. The *count* is never
#: capped — every file is counted — but on a plate that failed wholesale there
#: is nothing to learn from the ten-thousandth one that the first hundred did
#: not already say.
_MAX_ERROR_FILES_TYPED = 200

_ERRORS_DIRNAME = "errors"
_EXCEPTION_TYPE_PREFIX = "Exception type:"


def _member_names(container: Path):
    """Every member/file basename in a well's input container, or ``None`` if
    it cannot be read.

    ``None`` is "there is nothing here to compare against" — an absent
    container, a corrupt archive, a zip with no members — and it is distinct
    from an empty set, which would claim the container was read and found to
    hold nothing.
    """
    import zipfile

    if container is None:
        return None
    try:
        if container.is_dir():
            names = {p.name for p in container.iterdir() if p.is_file()}
        else:
            with zipfile.ZipFile(container) as zf:
                names = {Path(n).name for n in zf.namelist() if not n.endswith("/")}
    except (OSError, zipfile.BadZipFile):
        return None
    return names or None


def csv_keys_and_state(path: Path) -> tuple[set[tuple[str, str]], str]:
    """A per-well CSV's ``(fov, timepoint)`` keys and how readable it was.

    The Validate scan has its own reader because it needs the *rows* — its
    row-content findings compare each row's ``timepoint_hours`` and
    ``filename`` against the archive. The pipeline only needs the keys, and
    holding every row of a plate's worth of CSVs in memory to throw them away
    would be a needless cost at the end of a run that has just finished doing
    real work. Two readers with different appetites, both feeding the same two
    rules — :func:`csv_keys_from_rows` and :func:`reduce_csv_state` — so what
    they *mean* by a key or by "empty" cannot drift.
    """
    import csv

    if not path.exists():
        return set(), "missing"
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames:
                return set(), "unreadable"
            keys = csv_keys_from_rows(reader, fieldnames)
    except (OSError, UnicodeDecodeError, csv.Error):
        return set(), "unreadable"
    if not keys:
        # No keys can mean no rows, or rows without the two columns. Both are
        # "this file told us nothing", which is `empty` — a readable file that
        # holds no measurements, not a defect in the file itself.
        return set(), "empty"
    return keys, "ok"


def errors_since(output_dir: Path, since_ts: float | None) -> tuple[int, dict[str, int]]:
    """Count the run's own traceback files, and name their exception types.

    ``<output_dir>/errors/`` is never cleared between runs and its filenames
    carry a pid but no run id, so a clean re-run into the same output dir would
    otherwise report the *previous* run's failures as its own. Filtering by
    modification time against the run's start is what keeps a block called
    ``run_check`` describing this run. Pass ``None`` to count everything, which
    is what a caller with no start time should do — an over-count that says so
    beats a silent under-count.

    The type comes from the ``Exception type:`` header the pipeline writes into
    every trace, not from parsing the traceback body: the writer already knows
    the answer, and a regex over a traceback finds whatever exception names the
    *source lines* happen to mention.
    """
    err_dir = Path(output_dir) / _ERRORS_DIRNAME
    try:
        entries = sorted(err_dir.glob("*.txt"))
    except OSError:
        return 0, {}
    count = 0
    by_type: dict[str, int] = {}
    for path in entries:
        try:
            if since_ts is not None and path.stat().st_mtime < since_ts:
                continue
        except OSError:
            continue
        count += 1
        if count > _MAX_ERROR_FILES_TYPED:
            continue
        try:
            with path.open() as handle:
                for line in handle:
                    if line.startswith(_EXCEPTION_TYPE_PREFIX):
                        name = line[len(_EXCEPTION_TYPE_PREFIX):].strip()
                        if name:
                            by_type[name] = by_type.get(name, 0) + 1
                        break
        except (OSError, UnicodeDecodeError):
            continue
    return count, by_type


def wells_with_output(output_dir: Path, csv_prefix: str) -> set[str]:
    """Wells the output directory holds evidence for, whatever the input says.

    A dataset can outlive its inputs — staged, run, and the ``in/`` folder
    cleaned away — and a scan that enumerated only from the input would then
    report a finished plate as having no wells at all. Every well token here
    goes through the shared ``canonical_well_label`` parser, so ``a1``, ``A1``
    and ``A01`` land on one key rather than three.
    """
    from util.well_token import canonical_well_label

    found: set[str] = set()
    directory = Path(output_dir)
    try:
        entries = list(directory.iterdir())
    except OSError:
        return found
    prefix = f"{csv_prefix}_"
    for path in entries:
        stem = path.name
        label = None
        if stem.endswith("_out.zip"):
            label = canonical_well_label(stem[: -len("_out.zip")])
        elif path.is_dir() and stem != _ERRORS_DIRNAME:
            label = canonical_well_label(stem)
        elif stem.startswith(prefix) and stem.endswith(".csv"):
            label = canonical_well_label(stem[len(prefix): -len(".csv")])
        if label:
            found.add(label)
    return found


def _csv_prefixes_present(output_dir: Path) -> list[str]:
    """Every distinct ``<prefix>`` among ``<prefix>_<well>.csv`` in the output.

    More than one means two runs wrote into the same folder under different
    ``--csv_prefix`` values, which leaves a plate whose wells are split across
    two sets of measurements files. Nothing downstream reads both.
    """
    from util.well_token import canonical_well_label

    prefixes: set[str] = set()
    try:
        entries = sorted(Path(output_dir).glob("*.csv"))
    except OSError:
        return []
    for path in entries:
        head, sep_, tail = path.stem.rpartition("_")
        if sep_ and head and canonical_well_label(tail):
            prefixes.add(head)
    return sorted(prefixes)


def scan_run(
    *, in_containers, output_dir, schema, sep: str, csv_prefix: str,
    fov_field_present: bool, started_at: float | None = None,
    finished_at: str = "", started_at_iso: str = "", duration_s: float | None = None,
    host: str = "", mode: str = "", failed_by_well=None, suspended: bool = False,
    max_missing_per_well: int = MAX_MISSING_PER_WELL,
) -> dict:
    """Build the ``run_check`` block for a finished run.

    *in_containers* is the ``{well: path}`` map the run itself resolved, passed
    in rather than re-derived. The run has already walked its input directory,
    already logged what it skipped, and already decided what counts as a well —
    asking a second time would answer the same question with a second
    implementation, and would repeat the warnings the first walk emitted.

    Wells are the union of that map with whatever the output holds, so a
    dataset whose inputs were cleaned away after the run still reports its
    plate. Every well is recorded, including ones this run skipped because they
    were already complete: the record is an inventory of the dataset, not a log
    of what this invocation touched. That distinction is the whole reason it
    cannot be derived from ``run_report``, whose per-well entries only cover
    wells a run actually processed.

    Never raises. A well whose files cannot be read is recorded as unreadable
    and the scan continues; a failure that takes down the sweep itself sets
    ``partial_scan``, so a truncated record can never be mistaken for a clean
    plate.
    """
    from pipeline.well_skip import csv_path_for_well, out_folder_path, out_zip_path

    out_dir = Path(output_dir)
    in_map = {str(k): Path(v) for k, v in dict(in_containers or {}).items()}
    failed_map = {str(k): tuple(v or ()) for k, v in dict(failed_by_well or {}).items()}

    wells = sorted(set(in_map) | wells_with_output(out_dir, csv_prefix))
    records: dict[str, dict] = {}
    partial_scan = False

    for well in wells:
        try:
            container = in_map.get(well)
            out_zip = out_zip_path(out_dir, well)
            out_folder = out_folder_path(out_dir, well)
            csv_keys, csv_state = csv_keys_and_state(
                csv_path_for_well(out_dir, well, csv_prefix))
            cov = coverage_for_well(
                well,
                in_members=_member_names(container),
                csv_keys=csv_keys,
                csv_state=csv_state,
                out_zip=out_zip if out_zip.exists() else None,
                out_folder=out_folder if out_folder.is_dir() else None,
                schema=schema, sep=sep, fov_field_present=fov_field_present,
                skip_hidden=bool(container is not None and container.is_dir()),
                failed=failed_map.get(well, ()),
            )
        except Exception:
            # One unreadable well must not cost the record for the other 95.
            partial_scan = True
            continue
        records[well] = well_record(cov, max_missing=max_missing_per_well)

    error_count, error_types = errors_since(out_dir, started_at)
    findings = _scan_findings(out_dir, csv_prefix, records)

    return build_block(
        records,
        finished_at=finished_at, started_at_iso=started_at_iso,
        duration_s=duration_s, host=host, mode=mode, schema=schema, sep=sep,
        csv_prefix=csv_prefix, suspended=suspended, partial_scan=partial_scan,
        errors={"files": error_count, "by_type": error_types},
        findings=findings,
    )


#: What produced a record. ``"run"`` is the pipeline's own end-of-run check;
#: ``"validate"`` is the Datasets Validate tool, which measures the same thing
#: about a dataset nobody is currently running. Home reports which, because the
#: two answer subtly different questions: one describes the run that just
#: finished, the other describes the folder as it stands today.
RECORD_SOURCES = ("run", "validate")


def well_record(cov: WellCoverage, *, max_missing: int = MAX_MISSING_PER_WELL) -> dict:
    """One well's entry in the ``run_check`` block.

    ``health`` is stored rather than derived so that every surface reporting the
    record — Home's plate, its health card, the Analyze summary line — reads the
    same classification instead of each re-running the predicate against a
    partially-serialised copy of the evidence.
    """
    state = health(
        missing=cov.missing, csv_state=cov.csv_state,
        has_in_container=cov.has_in_container, has_out_zip=cov.has_out_zip,
        has_out_folder=cov.has_out_folder)
    return {
        "intent": cov.intent,
        "covered_csv": cov.covered_csv,
        "covered_any": cov.covered_any,
        "missing_count": len(cov.missing),
        "missing": [list(pair) for pair in cov.missing[:max_missing]],
        "csv_state": cov.csv_state,
        "has_in": cov.has_in_container,
        "has_out_zip": cov.has_out_zip,
        "has_out_folder": cov.has_out_folder,
        "failed": list(cov.failed),
        "health": state,
    }


def build_block(
    records: dict, *, finished_at: str = "", started_at_iso: str = "",
    duration_s: float | None = None, host: str = "", mode: str = "",
    schema=(), sep: str = "", csv_prefix: str = "", suspended: bool = False,
    partial_scan: bool = False, errors: dict | None = None,
    findings=None, source: str = "run",
) -> dict:
    """Assemble a ``run_check`` block from per-well records.

    Split out of :func:`scan_run` because the Datasets Validate tool produces
    the same record from its own, more thorough scan. Both go through here, so
    the block cannot acquire two shapes — a reader that learns to render one
    renders the other, and the totals are summed in one place rather than
    twice from two loops that could drift.
    """
    totals = dict.fromkeys(HEALTH_STATES, 0)
    for rec in records.values():
        state = rec.get("health")
        if state in totals:
            totals[state] += 1
    return {
        "version": 1,
        "source": source,
        "finished_at": finished_at,
        "started_at": started_at_iso,
        "duration_s": duration_s,
        "host": host,
        "mode": mode,
        "schema": ":".join(schema) if isinstance(schema, (list, tuple)) else str(schema or ""),
        "separator": sep,
        "csv_prefix": csv_prefix,
        "skipped": False,
        "skipped_reason": "",
        "suspended": bool(suspended),
        "partial_scan": bool(partial_scan),
        "wells": records,
        "totals": {
            "wells": len(records),
            "complete": totals["complete"],
            "partial": totals["partial"],
            "missing_csv": totals["missing"],
            "no_output": totals["no_output"],
            "intent": sum(int(r.get("intent") or 0) for r in records.values()),
            "covered_csv": sum(int(r.get("covered_csv") or 0) for r in records.values()),
            "covered_any": sum(int(r.get("covered_any") or 0) for r in records.values()),
        },
        "errors": dict(errors or {"files": 0, "by_type": {}}),
        "findings": list(findings or []),
    }


def _scan_findings(out_dir: Path, csv_prefix: str, records: dict) -> list[dict]:
    """The cheap dataset-wide problems visible from names already in hand.

    Deliberately not the full sweep the Datasets Validate tool performs. Codes
    are shared with ``datasets.validate.validate_compute.Finding`` so the two
    surfaces name the same defect with the same word.
    """
    findings: list[dict] = []
    prefixes = _csv_prefixes_present(out_dir)
    if len(prefixes) > 1:
        findings.append({
            "severity": "warning", "code": "mixed_csv_prefix", "well": "",
            "message": (
                f"This output folder holds measurements CSVs under "
                f"{len(prefixes)} different prefixes ({', '.join(prefixes)}). "
                f"Only '{csv_prefix}' was written by this run — the rest came "
                "from a run with a different --csv_prefix, and nothing "
                "downstream reads both sets."
            ),
        })
    orphans = sorted(
        well for well, rec in records.items()
        if rec["csv_state"] == "ok" and not rec["has_in"]
        and not rec["has_out_zip"] and not rec["has_out_folder"]
    )
    if orphans:
        shown = ", ".join(orphans[:8]) + (" …" if len(orphans) > 8 else "")
        findings.append({
            "severity": "warning", "code": "phantom_csv", "well": "",
            "message": (
                f"{len(orphans)} measurements CSV(s) name a well with no input "
                f"and no images behind it ({shown}). They are most likely left "
                "over from an earlier dataset written into this folder."
            ),
        })
    return findings


# ── the run-facing entry point ──────────────────────────────────────────────

def utc_iso(epoch: float | None) -> str:
    """An epoch timestamp as a UTC ISO-8601 string, or ``""`` for ``None``.

    The record is read months later, often on a different machine in a
    different timezone, so it carries UTC rather than local time.

    Public because the Datasets Validate tool stamps its own record with it
    (:mod:`datasets.validate.record`), and a second formatter would let two
    records of the same shape carry two timestamp spellings.
    """
    if epoch is None:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def failed_groups_by_well(info_path) -> dict:
    """Which FOV-groups the run recorded as failed, per well.

    Read from the ``run_report`` block the run has just merged. That block is
    the wrong thing to build an *inventory* from — it names only the wells this
    invocation processed — but naming failures is exactly what it is for, and a
    well it does not mention simply had none to report.
    """
    import json

    try:
        report = json.loads(Path(info_path).read_text()).get("run_report") or {}
        wells = report.get("wells") or {}
        return {well: list(rec.get("failed") or [])
                for well, rec in wells.items() if rec.get("failed")}
    except (OSError, ValueError, AttributeError):
        return {}


def log_summary(block: dict, *, log) -> None:
    """One line saying whether the plate covers its input.

    A suspended run is not a broken plate. It stops taking on new FOV-groups
    and exits through the normal end-of-well path, so most of its wells are
    genuinely partial — shouting INCOMPLETE at someone who just pressed stop
    describes their own action back at them as a fault, and buries the case
    where the wording means something.
    """
    totals = block.get("totals") or {}
    n_wells = int(totals.get("wells") or 0)
    partial = int(totals.get("partial") or 0)
    missing_groups = int(totals.get("intent") or 0) - int(totals.get("covered_any") or 0)
    if block.get("suspended"):
        log.info(
            "Completeness: run was suspended — %d of %d well(s) cover every "
            "FOV-group in their input. Re-run with --incremental to finish.",
            n_wells - partial, n_wells)
        return
    if partial:
        names = sorted(w for w, rec in (block.get("wells") or {}).items()
                       if rec.get("health") == "partial")
        shown = ", ".join(names[:8]) + (" …" if len(names) > 8 else "")
        log.warning(
            "INCOMPLETE: %d well(s) cover fewer FOV-groups than their input "
            "holds — %d group(s) missing (%s). Their CSV / out.zip exist and "
            "look complete; per-field statistics for them will be wrong or "
            "undefined. Re-run with --incremental to recover the missing groups.",
            partial, missing_groups, shown)
        return
    log.info("Completeness: all %d well(s) cover every FOV-group in their input.",
             n_wells)


def record_run_check(
    *, info_path, output_dir, in_containers, schema, sep: str, csv_prefix: str,
    started_at: float | None, mode: str, atomic_writer, log,
    skip: bool = False, suspended: bool = False, failed_by_well=None,
) -> dict:
    """Read back what a run wrote, and record whether it covers its input.

    A run that loses FOV-groups still writes a per-well CSV and an output
    archive, and on disk that output is shaped exactly like a whole well's —
    which is the reason ``pipeline/run_report.py`` exists. This is the other
    half of the same idea, and it differs in the two ways that matter for a
    reader coming to the dataset later.

    It covers **every** well, not the ones this invocation processed. An
    incremental run that adds a timepoint touches a handful of wells and skips
    the rest, so the run report it leaves behind names only those; a page
    reporting completeness from it would describe a finished plate as three
    wells.

    And it is written **at the end**, which makes its presence meaningful in
    its own right. ``pipeline_info.json`` is written before any image work, so
    that file existing says nothing about whether the run got anywhere; a
    ``run_check`` block in it says the run reached its end.

    Never raises, and never changes the run's exit code. A shortfall is
    recorded and warned about; whether the *run* failed is a separate question,
    already answered by the per-well failure list. Cost is one input-container
    listing, one output-container listing and one CSV read per well, all
    page-cache-warm because the run has just written them.

    *atomic_writer* is injected — ``pipeline.io._safe_atomic_text_write``, the
    same shape ``run_report.merge_into_pipeline_info`` takes — so this module
    stays free of the imaging stack and keeps importing cheaply on a remote
    host.
    """
    from pipeline.run_report import merge_into_pipeline_info

    finished_at = utc_iso(_now())
    if skip:
        # A stub, not silence. "The check was turned off" and "this dataset
        # predates the check" need different things from whoever reads the
        # record, and a reader that cannot tell them apart offers the wrong one.
        log.info("Completeness check skipped (--no_run_check).")
        block = {
            "version": 1, "skipped": True, "skipped_reason": "--no_run_check",
            "finished_at": finished_at, "wells": {}, "totals": {}, "findings": [],
        }
        try:
            merge_into_pipeline_info(
                info_path, {"run_check": block}, atomic_writer=atomic_writer)
        except Exception as exc:
            log.warning("Could not record the skipped completeness check: %s", exc)
        return block

    try:
        import socket

        log.info("Completeness: checking %d well(s)…", len(in_containers or {}))
        block = scan_run(
            in_containers=in_containers,
            output_dir=output_dir,
            schema=schema,
            sep=sep,
            csv_prefix=csv_prefix,
            fov_field_present="fov" in list(schema),
            started_at=started_at,
            finished_at=finished_at,
            started_at_iso=utc_iso(started_at),
            duration_s=(None if started_at is None
                        else round(_now() - started_at, 1)),
            host=socket.gethostname(),
            mode=mode,
            failed_by_well=(failed_groups_by_well(info_path)
                            if failed_by_well is None else failed_by_well),
            suspended=suspended,
        )
        merge_into_pipeline_info(
            info_path, {"run_check": block}, atomic_writer=atomic_writer)
        log_summary(block, log=log)
        return block
    except Exception as exc:
        log.warning("Completeness check failed: %s", exc)
        return {}


def _now() -> float:
    import time

    return time.time()
