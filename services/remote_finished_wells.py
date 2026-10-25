"""Which wells a remote run need not send, because they are already finished here under the same settings.

A run on this computer skips a well whose output exists and was made under the run's settings
(``process_microscopy._run_well_worker_pool``), unless ``--force`` or ``--force_wells`` names it. A remote run had no
such step: staging sent every well, the host's output directory had been cleaned after the previous run's download
(*Clean up remote*), and so a plain re-run of a finished plate uploaded and recomputed all of it. Only an incremental
run left finished wells out — and a remote nanowell run cannot be incremental.

This is that step for a remote run, asked once, before staging, with the pipeline's own answers:

* **the settings** are the key ``pipeline.cli_args.settings_key_from_argv`` derives from the run's argv plus the
  flags the host adds (``RemoteRunner._host_argv_extras``) — the key the host's pipeline records in
  ``.aw_settings/<well>.json``, which the Fetcher brings back — compared by ``well_settings_allow_reuse``, with and
  without ``--no_auto_threshold`` (``_key_either_way``: it changes no well's own output, and a batched host, or one
  sent part of a plate, is always given it);
* **the output** is the well's per-cell CSV, present and not empty. Not ``<well>_out.zip`` as the local check also
  asks: a fetched well keeps that archive only in a zip-layout, full-output fetch (a folder layout extracts it, a
  CSV-only fetch never brings it), so requiring it would recompute every such well on every re-run;
* **the well is whole** by its completeness record in the sidecar's ``run_report``, when there is one: a well wound
  down part way, or with failed groups, writes a CSV and a settings record too;
* **a forced well is never finished**, nor is any well under ``--force``, and an incremental run decides per field
  in staging instead, so it gets nothing from here;
* **a nanowell well is finished only when its tables cover every timepoint its archive holds** — each finished
  ``ok`` in its grid record (``pipeline.completeness.nanowell_coverage``). A remote nanowell run cannot add a well's
  missing timepoints to its earlier tables (the host never has them), so a well short of any — left part way by a
  run that stopped, or given new timepoints since — is not finished: it is sent whole and analysed again in full,
  and the runner forces it (``nanowell_forced``) so an earlier attempt's output on the host cannot stand in for it;
* **an empty-well blank is never left out**: it is the input the correction of every other well is built from,
  which staging never drops either. A plate whose only unfinished archive is its finished blank ends before upload
  (``PipelineRunner._nothing_left_to_upload``).

Anything that cannot be decided — an argv that does not parse — answers "nothing is finished": the cost of a wrong
"no" is a recomputed well, the cost of a wrong "yes" is a stale one reported as current.
"""

from __future__ import annotations

import dataclasses
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from util.well_token import canonical_well_label


def finished_here(output_dir: Path, pipeline_argv: Sequence[str], wells: Iterable[str], *,
                  extras: Sequence[str] = (), keep: Iterable[str] = ()) -> list[str]:
    """The *wells* (canonical labels, in the order given) finished in *output_dir* under the run *pipeline_argv*
    (the local argv, launcher and all) describes, with the host's *extras* appended; *keep* are never finished."""
    from pipeline.cli_args import build_parser
    from pipeline.settings_record import well_settings_allow_reuse
    from pipeline.well_skip import _canonical_force_wells
    from util.csv_gz import existing_csv

    flags = _flags(pipeline_argv)
    try:
        args = build_parser().parse_known_args(flags)[0]
    except SystemExit:
        return []
    if args.force or args.incremental:
        return []
    key = _key_either_way([*flags, *(str(e) for e in extras)])
    if not key:
        return []
    never = _canonical_force_wells(args.force_wells) | {canonical_well_label(w) or w for w in keep}
    prefix = args.csv_prefix or "measurements"
    records = _completeness_records(output_dir)
    covered = _nanowell_coverage(output_dir, args) if args.nanowell_format else None
    out = []
    for well in wells:
        label = canonical_well_label(well) or well
        if label in never or label in out:
            continue
        csv = existing_csv(Path(output_dir) / f"{prefix}_{label}.csv")
        if (csv is not None and csv.stat().st_size > 0 and well_settings_allow_reuse(output_dir, label, key)
                and _whole(records.get(label)) and (covered is None or covered(label))):
            out.append(label)
    return out


#: The one keyed option that cannot change a well's own output: it only switches the plate-level cell-gating pass
#: (``pipeline/auto_threshold_pass.py``, its only reader — ``tests/test_a_remote_rerun_skips_finished_wells.py``
#: holds that). A batched run's host is always given it (``remote_commands.batch_pipeline_argv``), and so is a
#: single-shot run that sends part of a plate, so the host's records carry it whatever the user chose; a well is
#: finished under the run's settings with it either way.
_OUTPUT_NEUTRAL_FLAG = "--no_auto_threshold"


def _key_either_way(flags: list[str]) -> str:
    """The settings key of *flags*, with the digests of the same run with and without ``--no_auto_threshold``
    joined as aliases (``util.settings_digest.KEY_ALIAS_SEP``), or ``""`` when either cannot be derived."""
    from pipeline.cli_args import settings_key_from_argv
    from util.settings_digest import KEY_ALIAS_SEP

    plain = [f for f in flags if f != _OUTPUT_NEUTRAL_FLAG]
    keys = [settings_key_from_argv(plain), settings_key_from_argv([*plain, _OUTPUT_NEUTRAL_FLAG])]
    return KEY_ALIAS_SEP.join(keys) if all(keys) else ""


def _completeness_records(output_dir: Path) -> dict:
    """``{well: record}`` from the sidecar's ``run_report`` block — the completeness the pipeline recorded, merged
    back after every remote fetch."""
    from pipeline.run_report import read_run_report_block

    wells = (read_run_report_block(Path(output_dir) / "pipeline_info.json") or {}).get("wells")
    return wells if isinstance(wells, dict) else {}


def _whole(record) -> bool:
    """A well with a completeness record is finished only when it says the well is whole: one wound down part way,
    or with failed groups, writes a CSV and a settings record too. No record (an older run) decides nothing."""
    from pipeline.run_report import well_is_whole

    return record is None or well_is_whole(record)


def nanowell_forced(output_dir: Path, pipeline_argv: Sequence[str], sent: Iterable[str]) -> list[str]:
    """The *sent* wells of a nanowell run that already have a per-cell CSV in *output_dir*, which it must force.

    Such a well is being sent because its output here is not finished (``finished_here``): short of a timepoint, made
    under other settings, or named in Force wells. A remote nanowell run resumes well by well
    (``analyze.run_spec._nanowell_remote_resume_by_well``) rather than incrementally, and the host's own skip asks only
    whether output exists under these settings, so an earlier attempt's partial tables in the directory this run is
    given could otherwise be kept and fetched back as current.

    Only a nanowell run: any other run that resumes is made incremental, which never comes here, and a forced well
    changes the argv the run's address is derived from, so forcing every re-sent well would cost a plain re-run the
    full cache hit (the host holding every well already) it is entitled to. ``[]`` under ``--force``."""
    from pipeline.cli_args import build_parser
    from util.csv_gz import existing_csv

    try:
        args = build_parser().parse_known_args(_flags(pipeline_argv))[0]
    except SystemExit:
        return []
    if not args.nanowell_format or args.force or args.incremental:
        return []
    prefix = args.csv_prefix or "measurements"
    return sorted({label for w in sent if (label := canonical_well_label(w) or w)
                   and existing_csv(Path(output_dir) / f"{prefix}_{label}.csv") is not None})


def argv_forcing(pipeline_argv: Sequence[str], wells: Iterable[str]) -> list[str]:
    """*pipeline_argv* with *wells* added to its ``--force_wells`` (one list, the earlier names kept first)."""
    wells = list(wells)
    if not wells:
        return list(pipeline_argv)
    out: list[str] = []
    named: list[str] = []
    it = iter(pipeline_argv)
    for arg in it:
        if arg == "--force_wells":
            for value in it:
                if str(value).startswith("--"):
                    out.append(value)
                    break
                named.append(str(value))
            continue
        out.append(arg)
    named += [w for w in wells if w not in named]
    return [*out, "--force_wells", *named]


def _nanowell_coverage(output_dir: Path, args):
    """``covered(well)`` for a nanowell run: whether every timepoint of the well's archive in ``--input_dir`` is
    finished ``ok`` in its grid record — the rule the Validate tool and the end-of-run scan use
    (``pipeline.completeness.nanowell_coverage``). Anything unreadable is not covered."""
    from pipeline.completeness import nanowell_coverage
    from util.well_token import well_archives

    out = Path(output_dir)
    archives = {canonical_well_label(p.stem): p for p in well_archives(_wells_directory(Path(args.input_dir)))}

    def covered(well: str) -> bool:
        archive = archives.get(well)
        if archive is None:
            return False
        try:
            with zipfile.ZipFile(archive) as zf:
                members = [Path(n).name for n in zf.namelist()]
        except (OSError, zipfile.BadZipFile):
            return False
        cov = nanowell_coverage(well, in_members=members, grid_path=out / "nanowells" / f"{well}_grid.json",
                                sites_path=out / "nanowells" / f"{well}.csv", has_out_zip=True,
                                schema=args.filename_schema, sep=args.filename_sep, skip_hidden=True)
        return cov.intent > 0 and not cov.missing

    return covered


def _wells_directory(input_dir: Path) -> Path:
    """Where a nanowell plate's archives are: ``<input>/in`` when that exists, else the input itself."""
    return input_dir / "in" if (input_dir / "in").is_dir() else input_dir


def wells_with_csvs(output_dir: Path, csv_prefix: str = "measurements") -> list[str]:
    """Every well with a per-cell CSV (plain or gzipped) in *output_dir*, as canonical labels, sorted."""
    head = f"{csv_prefix}_"
    found = set()
    for path in Path(output_dir).glob(f"{head}*.csv*"):
        stem = path.name[len(head):].removesuffix(".gz").removesuffix(".csv")
        label = canonical_well_label(stem)
        if label is not None and path.name.endswith((".csv", ".csv.gz")):
            found.add(label)
    return sorted(found)


def drop_finished(zipped, finished: Iterable[str]):
    """*zipped* (``ZippedInput``) without the archives of *finished* wells, which join its ``up_to_date_wells`` —
    the runner marks those complete on the plate and says so in the log."""
    finished = set(finished)
    if not finished:
        return zipped
    kept = tuple(p for p in zipped.well_zips if (canonical_well_label(Path(p).stem) or Path(p).stem) not in finished)
    dropped = [w for w in sorted(finished) if w not in zipped.up_to_date_wells]
    return dataclasses.replace(zipped, well_zips=kept, up_to_date_wells=(*zipped.up_to_date_wells, *dropped))


def _flags(pipeline_argv: Sequence[str]) -> list[str]:
    """*pipeline_argv* from its first flag (the interpreter and script, or a bundle's sentinel, dropped), with the
    two path flags the parser requires supplied when absent. Neither is in the settings key
    (``util.settings_digest.NOT_KEYED_ON``), so a placeholder changes no answer; without it an argv that names them
    elsewhere would parse to nothing and every well would quietly be sent again."""
    flags: list[str] = []
    for n, arg in enumerate(pipeline_argv):
        if str(arg).startswith("--") and str(arg) != "--run-pipeline":
            flags = [str(a) for a in pipeline_argv[n:]]
            break
    for required in ("--input_dir", "--output_dir"):
        if not any(f == required or f.startswith(required + "=") for f in flags):
            flags = [required, ".", *flags]
    return flags


#: What a well's settings marker says when the one its fetched output came with could not be brought back: a digest
#: no settings key contains, so the well is never reused on the strength of a marker from an earlier computation.
UNKNOWN_SETTINGS = "unknown-fetch-failed"


def pull_settings_marker(pull, host, remote_path: str, dest: Path, **kwargs) -> bool:
    """Bring a well's ``.aw_settings`` marker back beside its freshly fetched output with *pull*
    (``remote_transport.pull_small_file``, passed in by the caller so a test's stand-in for it is the one used).

    A pull that fails leaves the destination as it was — the marker of whatever computation produced the well's
    *previous* output here — and a missing one reads as "made before the record existed", which is reused. Either
    way the new output would be judged by settings it was not made under, and ``finished_here`` would then skip it
    as current for those. So a failed pull writes :data:`UNKNOWN_SETTINGS` instead: the well is recomputed by the
    next run that asks, never mistaken for one made under its settings."""
    from util.json_write import write_json_atomic

    if pull(host, remote_path, dest, **kwargs):
        return True
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(dest, {"well": dest.stem, "digest": UNKNOWN_SETTINGS})
    except OSError:
        dest.unlink(missing_ok=True)
    return False

