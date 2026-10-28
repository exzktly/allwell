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
import re
import logging
import os
import tempfile
from pathlib import Path

from util.aw_safe import swallow, swallowed
from util.json_write import write_json_atomic

_log = logging.getLogger(__name__)

# Hidden so it never looks like dataset output; removed once collected.
RUN_REPORT_DIRNAME = ".aw_run_report"

#: The key this block takes inside ``pipeline_info.json``. Declared rather than
#: spelled at the call site so ``tests/test_sidecar_blocks_are_merge_rostered.py``
#: can see it: a block nobody remembered to add to
#: ``datasets.merge_plates.merge_plates_compute._UNMERGEABLE_BLOCKS`` is silently
#: dropped by a plate merge, with no warning, because that roster is written by
#: hand.
RUN_REPORT_BLOCK = "run_report"


#: Where a well's **completion seal** lives: ``<output_dir>/.aw_complete/<well>.json``,
#: present only while the well's ``<well>_out.zip`` and CSV beside it cover every
#: FOV-group its input holds. Unlike :data:`RUN_REPORT_DIRNAME` it is never
#: cleared at the start or end of a run — it is read by a *later* run, which is
#: the point: a remote resume adopts a well left on the remote only on this
#: proof (``services/remote_batch_resume.py``). An ``out.zip`` alone proves
#: nothing, because a well that failed some groups, aborted, or was suspended
#: part way writes one too, from the groups it finished.
COMPLETE_DIRNAME = ".aw_complete"


def _report_dir(output_dir: Path | str) -> Path:
    return Path(output_dir) / RUN_REPORT_DIRNAME


def complete_seal_path(output_dir: Path | str, well_label: str) -> Path:
    """The completion seal :func:`seal_well_complete` writes for *well_label*."""
    return Path(output_dir) / COMPLETE_DIRNAME / f"{well_label}.json"


#: Beside a seal, never a ``.json``: what :func:`withdraw_seal` leaves until the well is sealed again.
WITHDRAWN_SUFFIX = ".withdrawn"


def withdrawn_note_path(output_dir: Path | str, well_label: str) -> Path:
    """The note :func:`withdraw_seal` leaves while *well_label*'s output is being rewritten, or was left part way."""
    return Path(output_dir) / COMPLETE_DIRNAME / f"{well_label}{WITHDRAWN_SUFFIX}"


# ── "Is this well finished?" — the one definition ──────────────────────────
#
# Every consumer that decides a well need not be run, sent or adopted again asks it here: the host's own skip
# (`pipeline.well_skip.finished_output`), the remote pre-flight and the batched resume's adoption
# (`services.remote_finished_wells.RemoteOutputs`), the pre-staging download (`services.remote_recover`).
# `tests/test_one_definition_of_a_finished_well.py` holds that no other module lists `*_out.zip` to decide it.

def well_of_out_zip(name: str) -> str | None:
    """The canonical well a ``<well>_out.zip`` file name belongs to, or ``None`` for any other name."""
    from util.well_token import canonical_well_label

    name = str(name).rsplit("/", 1)[-1]
    if not name.endswith("_out.zip"):
        return None
    return canonical_well_label(name[: -len("_out.zip")])


def well_of_seal(name: str) -> str | None:
    """The canonical well a completion seal's file name (``<well>.json``) belongs to, or ``None``."""
    from util.well_token import canonical_well_label

    name = str(name).rsplit("/", 1)[-1]
    return canonical_well_label(name[: -len(".json")]) if name.endswith(".json") else None


def finished_wells(with_output, sealed) -> frozenset[str]:
    """**The** answer to "is this well finished": its output is there *and* its completion seal is beside it.

    *with_output* are the wells an output directory holds a ``<well>_out.zip`` for, *sealed* the wells it holds a
    seal for, both as canonical labels. An archive alone proves nothing — a well that failed some groups, was wound
    down part way, or died mid-rewrite writes one too — and a seal alone has nothing to vouch for. Either listing
    ``None`` means it could not be read, and then **nothing** is finished: the cost of a wrong "no" is a recomputed
    well, the cost of a wrong "yes" is a stale one reported as current.
    """
    if with_output is None or sealed is None:
        return frozenset()
    return frozenset(w for w in with_output if w and w in set(sealed))


def sealed_wells(output_dir: Path | str) -> frozenset[str] | None:
    """The wells *output_dir* holds a completion seal for, canonical; ``None`` when the folder cannot be read."""
    folder = Path(output_dir) / COMPLETE_DIRNAME
    try:
        if not folder.is_dir():
            return frozenset()
        return frozenset(w for p in folder.iterdir() if (w := well_of_seal(p.name)))
    except OSError:
        return None


def recorded_completeness(output_dir: Path | str) -> dict:
    """``{well: record}`` — every completeness record *output_dir* holds from earlier runs: the sidecar's
    ``run_report`` block, overlaid with any per-well file a run that died before folding them in left behind.

    Read by ``process_microscopy.main`` **before** it rewrites the sidecar and clears the per-well files, for
    :func:`finished_on_this_host`. Unreadable parts read as no record.
    """
    block = read_run_report_block(Path(output_dir) / "pipeline_info.json") or {}
    records = dict(block.get("wells") or {}) if isinstance(block.get("wells"), dict) else {}
    records.update(collect_run_report(output_dir)["wells"])
    return records


def finished_on_this_host(output_dir: Path | str, well_label: str, *, records: dict | None = None) -> bool:
    """Whether a well whose output exists in *output_dir* is finished, for the pipeline's own skip.

    :func:`finished_wells` first: sealed is finished. An unsealed well is finished only when nothing says a build
    that seals ever touched it and nothing says it ended short — the output of a build from before the seal,
    which every plate made before it holds, and which the run re-used before there was a seal to ask:

    * a withdrawal note (:func:`withdraw_seal`) means a sealing build began rewriting the well and never sealed it:
      it was wound down part way, failed groups, or died mid-rewrite — **not finished**;
    * a completeness record in *records* (:func:`recorded_completeness`) that is not whole means the same, for the
      builds that wrote records but left no note — **not finished**;
    * neither: a well an earlier build finished. Finished, as it always was.

    Only the host has those two pieces of evidence beside the output, which is why only this caller grandfathers;
    the app's readers of a remote directory (:func:`finished_wells` alone) treat every unsealed well as unfinished.
    """
    from util.well_token import canonical_well_label

    label = canonical_well_label(str(well_label)) or str(well_label)
    sealed = sealed_wells(output_dir)
    if label in finished_wells({label}, sealed):
        return True
    if sealed is None or any(withdrawn_note_path(output_dir, w).exists() for w in {str(well_label), label}):
        return False
    record = (records or {}).get(str(well_label)) or (records or {}).get(label)
    return record is None or well_is_whole(record)


class SealNotWithdrawnError(RuntimeError):
    """A well's completion seal could not be removed, so its output must not be rewritten.

    Deliberately not an :class:`OSError`: the well paths catch ``OSError`` around
    their best-effort bookkeeping, and this failure must reach the well's own
    failure handling (trace in ``errors/``, the well reported failed) instead.
    """


def withdraw_seal(output_dir: Path | str, well_label: str) -> None:
    """Withdraw *well_label*'s completion seal before its output is rewritten.

    Every well path calls this immediately before it writes its CSV and
    ``<well>_out.zip`` (and :func:`write_well_report` calls it again), so a run
    that dies part way through the rewrite leaves output with **no** seal —
    which a resume reads as "run it again" — rather than an older seal vouching
    for output this run never finished.

    A seal that cannot be withdrawn is the one case that matters: the well's
    earlier output and its seal still agree, and the rewrite is what would break
    that. So the failure is logged and raised as :class:`SealNotWithdrawnError`;
    the well fails before touching anything, and its earlier output stays as it
    was, still sealed and still whole.

    It also leaves a **withdrawal note** (:func:`withdrawn_note_path`) until the
    well is sealed again, so the output it leaves behind is marked as written by
    a build that seals: the host's own skip (:func:`finished_on_this_host`) can
    then tell a part-way well from one a build before the seal finished, which
    otherwise look the same — output, no seal. A note that cannot be written is
    logged and the rewrite goes on; the well's completeness record still says
    whether it ended whole.
    """
    path = complete_seal_path(output_dir, well_label)
    note = withdrawn_note_path(output_dir, well_label)
    try:
        note.parent.mkdir(parents=True, exist_ok=True)
        note.touch()
    except OSError:
        swallowed(f"Well {well_label}: could not leave the note that its completion seal is withdrawn ({note})")
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        if not os.path.lexists(path):
            # Nothing there to vouch for anything — e.g. an output "directory"
            # that is a file, where no seal can ever have been written.
            return
        _log.error("Well %s: could not withdraw its completion seal %s (%s: %s); not rewriting the well, "
                   "so its earlier output and seal are left as they were.",
                   well_label, path, type(exc).__name__, exc)
        raise SealNotWithdrawnError(
            f"Well {well_label}: could not withdraw its completion seal {path} ({type(exc).__name__}: {exc}); "
            "the well was not rewritten") from exc


#: Output files that name the well they belong to: ``<well>_out.zip``, ``<prefix>_<well>.csv[.gz]`` and a nanowell
#: well's ``nanowells/<well>_grid.json``, each read as the well token it carries.
_WELL_OUTPUT_PATTERNS = (
    (".", re.compile(r"^(?P<well>[^_/]+)_out\.zip$")),
    (".", re.compile(r"^.+_(?P<well>[A-Za-z]{1,2}\d{1,3})\.csv(?:\.gz)?$")),
    ("nanowells", re.compile(r"^(?P<well>[^_/]+)_grid\.json$")),
)


def wells_left_part_way(output_dir: Path | str, wells) -> list[str]:
    """The wells among *wells* whose output in *output_dir* exists without a completion seal, sorted.

    The one thing an unfinished run can leave that only ``--incremental`` recovers: a well that wrote some output
    and was not sealed whole (:func:`seal_well_complete`), which a plain run would otherwise skip as complete. A
    well with no output has nothing to resume, and a sealed one is whole, so for those an incremental run and a
    plain one do the same thing. Wells are compared canonically (``A1`` is ``A01``). An unreadable folder answers
    with every well, so the caller stays on the safe side.
    """
    from util.well_token import canonical_well_label

    wanted = {canonical_well_label(str(w)) or str(w) for w in wells}
    root = Path(output_dir)
    sealed = sealed_wells(root)
    if sealed is None:
        return sorted(wanted)
    try:
        written = set()
        for sub, pattern in _WELL_OUTPUT_PATTERNS:
            folder = root / sub
            if not folder.is_dir():
                continue
            for entry in folder.iterdir():
                found = pattern.match(entry.name)
                if found and entry.is_file() and entry.stat().st_size > 0:
                    written.add(canonical_well_label(found.group("well")))
    except OSError:
        return sorted(wanted)
    finished = finished_wells(written, sealed)
    return sorted(w for w in wanted if w in written and w not in finished)


def recorded_well_report(output_dir: Path | str, well_label: str) -> dict | None:
    """The completeness record this run wrote for *well_label*, or ``None``."""
    try:
        rec = json.loads((_report_dir(output_dir) / f"{well_label}.json").read_text())
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def well_is_whole(rec) -> bool:
    """Whether a completeness record describes a well whose output covers its whole input.

    Not merely "not incomplete": a well with groups that failed or were never
    attempted is not whole even when the coverage arithmetic happens to agree,
    and a well whose input held nothing (:func:`found_nothing`) has nothing to
    vouch for.

    **A nanowell record is counted in timepoints and carries** ``groups_reached``
    (:func:`pipeline.nanowell_task.record_nanowell_completeness`). There the
    question is whether the run *reached* every timepoint of the well's input —
    gave each a verdict in its grid record — not whether every verdict was
    ``ok``: an ``unaddressed`` or ``no_lattice`` timepoint is the run's finding
    about those images under these settings, which running the well again,
    alone, cannot change (only an anchor set since can, and
    ``util.nanowell_anchors.wells_with_unused_anchors`` asks that). Its
    ``failed`` list names the timepoints whose verdict wrote no rows — findings
    the end-of-run summary reports, not failures of the run — so it does not
    bar the well. ``groups_covered`` stays the ``ok``-only count Validate and the
    INCOMPLETE summary read. Gating the seal on that count left every well with
    one unaddressed timepoint unsealed and unfinished for ever: each re-run or
    resume sent it again alone and forced, and alone it lost the plate the
    end-of-run re-anchoring fits its addresses from.
    """
    if not isinstance(rec, dict) or found_nothing(rec):
        return False
    if rec.get("not_attempted") or rec.get("aborted_reason"):
        return False
    if "groups_reached" in rec:
        return _int(rec, "groups_reached") >= _int(rec, "groups_total")
    return not is_incomplete(rec) and not rec.get("failed")


def seal_well_complete(output_dir: Path | str, well_label: str) -> Path | None:
    """Seal *well_label*'s output as covering its whole input, if this run's record says so.

    **Call it after both artefacts are on disk** — the same place
    ``record_well_settings`` is called, past every short circuit — because the
    seal's whole meaning is "the out.zip and CSV beside this are the well's
    complete output". It reads the record :func:`write_well_report` just wrote
    rather than taking counts from the caller, so the three well paths (zip,
    folder, nanowell) cannot each decide "complete" differently. A well whose
    record is missing, partial or failed gets no seal, which a resume reads as
    "run it again": costing compute, never a stale well reported as finished.
    """
    if not well_is_whole(recorded_well_report(output_dir, well_label)):
        return None
    path = complete_seal_path(output_dir, well_label)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, {"well": str(well_label)})
    except OSError:
        return None
    from util.well_token import canonical_well_label

    with swallow(f"Well {well_label}: clearing the note that its completion seal was withdrawn"):
        for spelling in {str(well_label), canonical_well_label(str(well_label)) or str(well_label)}:
            withdrawn_note_path(output_dir, spelling).unlink(missing_ok=True)
    return path


def write_well_report(
    output_dir: Path | str,
    well_label: str,
    *,
    groups_total: int,
    groups_covered: int,
    failed: list[str] | None = None,
    aborted_reason: str = "",
    not_attempted: tuple[str, ...] | list[str] = (),
    groups_reached: int | None = None,
) -> Path | None:
    """Record one well's completeness. Best-effort, with one exception.

    A record that cannot be written is dropped (a run that cannot write its
    own bookkeeping must still produce data). A completion seal that cannot be
    withdrawn is not: :func:`withdraw_seal` raises, because the well's output
    is being rewritten under it.

    ``groups_total`` is how many FOV-groups the well's *input* holds;
    ``groups_covered`` is how many distinct ``(fov, timepoint)`` pairs its
    output CSV actually carries once this run finished — ground truth read back
    from the artifact rather than inferred from what the run believed it did, so
    it stays correct across incremental runs and resumed checkpoints alike.

    ``groups_reached``, given only for a nanowell well (counted in timepoints),
    is how many of them the run reached with a verdict; :func:`well_is_whole`
    then asks it rather than ``groups_covered``.
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
    if groups_reached is not None:
        payload["groups_reached"] = int(groups_reached)
    withdraw_seal(output_dir, well_label)
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
    return block_of(wells)


def block_of(wells: dict) -> dict:
    """The report block for a ``wells`` map, derived fields computed from it.

    The one place the derived fields are built, so a block assembled from this
    run's files, one merged with a prior run's records, and one restored after
    a remote fetch cannot summarise their ``wells`` map differently.
    """
    return {
        "wells": wells,
        "incomplete_wells": sorted(w for w, r in wells.items() if is_incomplete(r)),
        "empty_wells": sorted(w for w, r in wells.items() if found_nothing(r)),
        "groups_total": sum(int((r or {}).get("groups_total", 0)) for r in wells.values()),
        "groups_covered": sum(int((r or {}).get("groups_covered", 0)) for r in wells.values()),
    }


def clear_run_report(output_dir: Path | str) -> None:
    """Drop the staging directory once its contents are in ``pipeline_info``."""
    d = _report_dir(output_dir)
    try:
        for path in d.glob("*"):
            path.unlink(missing_ok=True)
        d.rmdir()
    except OSError:
        swallowed("the run-report staging directory could not be removed")


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


def finalize_run_report(output_dir, info_path, *, atomic_writer, log,
                        prior_block: dict | None = None,
                        ran_wells=()) -> None:
    """Fold the per-well records into ``pipeline_info.json`` and log the gap.

    Called once at the end of a run. Best-effort throughout: a run must not fail
    because it could not describe itself.

    *prior_block* is the block a ``--only_wells`` run found in the sidecar
    before rewriting it. That run processes a subset of the plate into a
    directory holding the rest, so its own records describe only the subset;
    written alone they would replace the whole plate's record with a handful,
    and every well outside the subset would lose the record that says whether
    its output is whole. The prior records survive only for wells outside
    *ran_wells* (the wells this run processed): a well it ran and that died
    before writing its own record must end up with **no** record rather than
    inherit the previous run's — see ``pipeline.sidecar.USER_SIDE_BLOCKS`` on
    why a stale description is worse than a missing one.
    """
    try:
        report = collect_run_report(output_dir)
        prior_wells = (prior_block or {}).get("wells")
        if isinstance(prior_wells, dict):
            ran = set(ran_wells)
            prior_wells = {w: r for w, r in prior_wells.items() if w not in ran}
            # Even when this run recorded nothing: the sidecar rewrite at its
            # start has already dropped the block, so skipping the write here
            # would lose the prior records rather than keep them.
            report = block_of({**prior_wells, **report["wells"]})
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
    info["run_report"] = block_of(merged)
    try:
        write_json_atomic(path, info)
    except OSError:
        return False
    return True
