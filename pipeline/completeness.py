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

What is *not* here is the scan. Deciding which wells exist, listing containers,
parsing CSVs and building a report are the callers' own business, and they do
them very differently: Validate parallelises across wells and pins its read
counts with a test, while the pipeline walks a directory it has just written.
The functions below take data the caller already holds — member names, parsed
CSV rows — and open exactly one thing between them: the well's output container,
through :mod:`pipeline.incremental_keys`. That constraint is what lets Validate
delegate without changing how many times it touches the disk.

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
