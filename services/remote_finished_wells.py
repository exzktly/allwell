"""Which wells a remote run need not send, because they are already finished here under the same settings.

A run on this computer skips a well whose output exists and was made under the run's settings
(``process_microscopy._run_well_worker_pool``), unless ``--force`` or ``--force_wells`` names it. A remote run had no
such step: staging sent every well, the host's output directory had been cleaned after the previous run's download
(*Clean up remote*), and so a plain re-run of a finished plate uploaded and recomputed all of it. Only an incremental
run left finished wells out — and a remote nanowell run cannot be incremental.

This is that step for a remote run, asked once, before staging, with the pipeline's own answers:

* **the settings** are the key ``pipeline.cli_args.settings_key_from_argv`` derives from the run's argv plus the
  flags the host adds (``RemoteRunner._host_argv_extras``) — the key the host's pipeline records in
  ``.aw_settings/<well>.json``, which the Fetcher brings back — compared by ``well_settings_allow_reuse``;
* **the output** is the well's per-cell CSV, present and not empty. Not ``<well>_out.zip`` as the local check also
  asks: a fetched well keeps that archive only in a zip-layout, full-output fetch (a folder layout extracts it, a
  CSV-only fetch never brings it), so requiring it would recompute every such well on every re-run;
* **a forced well is never finished**, nor is any well under ``--force``, and an incremental run decides per field
  in staging instead, so it gets nothing from here;
* **a nanowell well is finished only when its tables cover every timepoint its archive holds** — its grid record
  (``nanowells/<well>_grid.json``) names each of them, and its completeness record, if the sidecar has one, says it
  is whole. A remote nanowell run cannot add a well's missing timepoints to its earlier tables (the host never has
  them), so a well short of any — left part way by a run that stopped, or given new timepoints since — is not
  finished: it is sent whole and analysed again in full, and the runner forces it (``nanowell_forced``) so an
  earlier attempt's output on the host cannot stand in for it;
* **an empty-well blank is never left out**: it is the input the correction of every other well is built from,
  which staging never drops either. A plate whose only unfinished archive is its measured blank ends before upload
  (``PipelineRunner._nothing_left_to_upload``).

Anything that cannot be decided — an argv that does not parse — answers "nothing is finished": the cost of a wrong
"no" is a recomputed well, the cost of a wrong "yes" is a stale one reported as current.
"""

from __future__ import annotations

import dataclasses
import json
import zipfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from util.well_token import canonical_well_label


def finished_here(output_dir: Path, pipeline_argv: Sequence[str], wells: Iterable[str], *,
                  extras: Sequence[str] = (), keep: Iterable[str] = ()) -> list[str]:
    """The *wells* (canonical labels, in the order given) finished in *output_dir* under the run *pipeline_argv*
    (the local argv, launcher and all) describes, with the host's *extras* appended; *keep* are never finished."""
    from pipeline.cli_args import build_parser, settings_key_from_argv
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
    key = settings_key_from_argv([*flags, *(str(e) for e in extras)])
    if not key:
        return []
    never = _canonical_force_wells(args.force_wells) | {canonical_well_label(w) or w for w in keep}
    prefix = args.csv_prefix or "measurements"
    covered = _nanowell_coverage(output_dir, args) if args.nanowell_format else None
    out = []
    for well in wells:
        label = canonical_well_label(well) or well
        if label in never or label in out:
            continue
        csv = existing_csv(Path(output_dir) / f"{prefix}_{label}.csv")
        if (csv is not None and csv.stat().st_size > 0 and well_settings_allow_reuse(output_dir, label, key)
                and (covered is None or covered(label))):
            out.append(label)
    return out


def nanowell_forced(output_dir: Path, pipeline_argv: Sequence[str], sent: Iterable[str]) -> list[str]:
    """The *sent* wells of a nanowell run that already have a per-cell CSV in *output_dir*, which it must force.

    Such a well is being sent because its output here is not finished (``finished_here``): short of a timepoint, or
    made under other settings, or named in Force wells. The host's own skip asks only whether output exists under
    these settings, and an earlier attempt's partial output may be in the directory this run is given, so without
    the force the host could skip it and the run would fetch the same partial tables back as current. ``[]`` for
    any other run, whose host-side skip reads the same settings record this side does."""
    from pipeline.cli_args import build_parser
    from util.csv_gz import existing_csv

    try:
        args = build_parser().parse_known_args(_flags(pipeline_argv))[0]
    except SystemExit:
        return []
    if not args.nanowell_format or args.force:
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
    """``covered(well)`` for a nanowell run: whether the well's grid record names every timepoint of its archive
    in ``--input_dir`` and its completeness record (``pipeline_info.json`` ``run_report``), when there is one, says
    it is whole. Anything unreadable is not covered."""
    from pipeline.incremental_keys import fov_keys_from_member_names
    from pipeline.run_report import read_run_report_block, well_is_whole
    from util.well_token import well_archives

    out = Path(output_dir)
    archives = {canonical_well_label(p.stem): p for p in well_archives(_wells_directory(Path(args.input_dir)))}
    records = ((read_run_report_block(out / "pipeline_info.json") or {}).get("wells") or {})

    def covered(well: str) -> bool:
        archive = archives.get(well)
        record = records.get(well)
        if archive is None or (record is not None and not well_is_whole(record)):
            return False
        try:
            grid = json.loads((out / "nanowells" / f"{well}_grid.json").read_text())
            with zipfile.ZipFile(archive) as zf:
                names = [Path(n).name for n in zf.namelist()]
        except (OSError, ValueError, zipfile.BadZipFile):
            return False
        wanted = {tp for _fov, tp in fov_keys_from_member_names(
            names, schema=args.filename_schema, sep=args.filename_sep, skip_hidden=True, images_only=True)}
        done = set((grid.get("timepoints") or {}) if isinstance(grid, dict) else ())
        return bool(wanted) and wanted <= done

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
