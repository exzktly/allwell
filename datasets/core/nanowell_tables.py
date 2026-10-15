"""What the Datasets tools do with a dataset's ``out/nanowells/`` tables (tracker P10).

A nanowell analysis leaves, beside the usual per-well CSV and archive, ``out/nanowells/<well>.csv`` (one
row per site and timepoint) and ``<well>_grid.json`` (the registration, lattice and status of each
timepoint). A tool that rewrites a dataset's wells, timepoints or fields and does not know about them
would write a destination whose cells have no nanowells behind them: the tables silently dropped, the
rest looking whole. So each tool either carries them or refuses: Subset carries the tables of the wells
and timepoints it keeps (:func:`carry_nanowell_tables`) and refuses a field-level selection, which the
tables are not keyed on; Merge Plates carries them with the wells remapped; Concatenate refuses, because
two analyses' addresses cannot be joined without re-anchoring — the supported route is to concatenate the
raw input and analyse once (tracker P10b-d). Validate checks the tables against the archives.

Qt-free. The refusal takes the calling tool's own error class, so each keeps its user-facing voice.
"""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

from pipeline.incremental_keys import fov_keys_from_member_names

NANOWELL_DIRNAME = "nanowells"
SCHEME_VERSION = 1


def nanowell_dir(out_dir) -> Path:
    return Path(out_dir) / NANOWELL_DIRNAME


def has_nanowell_tables(paths) -> bool:
    """Whether the dataset's ``out/`` holds any nanowell table."""
    folder = nanowell_dir(paths.out_dir)
    return folder.is_dir() and any(folder.glob("*_grid.json"))


def refuse_nanowell_tables(paths, label: str, error_cls, action: str) -> None:
    """Raise *error_cls* when *paths* carries nanowell tables this tool cannot yet carry through *action*."""
    if has_nanowell_tables(paths):
        raise error_cls(
            f"{label} holds nanowell results ({nanowell_dir(paths.out_dir)}), and {action} cannot carry them: "
            "each analysis addressed its own nanowells, and two analyses' addresses are not joined without "
            "re-anchoring, so the destination would have cells with no nanowells behind them. Concatenate the "
            "raw input and run Analyze once on the result, or choose a dataset without nanowell results.")


def sites_table(folder: Path, well: str) -> Path | None:
    """The well's sites table, ``<well>.csv`` or ``<well>.csv.gz`` (the plain file wins, as Review reads it)."""
    for name in (f"{well}.csv", f"{well}.csv.gz"):
        if (Path(folder) / name).is_file():
            return Path(folder) / name
    return None


def _open_text(path: Path, mode: str):
    import gzip

    return gzip.open(path, mode + "t", newline="", encoding="utf-8") if path.suffix == ".gz" else \
        path.open(mode, newline="", encoding="utf-8")


def carry_nanowell_tables(src_out, dest_out, well_map: dict, *, keep_timepoint=None) -> dict[str, int]:
    """Copy each source well's nanowell tables to its destination well; ``{dest well: rows written}``.

    *well_map* maps a source well to its destination well, both canonical (``A01``, as the tools' well maps
    are), so a table written under an unpadded spelling (``A1_grid.json``) is still found; a source well the
    map does not name is not carried. The grid record's ``well`` is rewritten to the destination well, and with *keep_timepoint* (a predicate on a timepoint token) only the timepoints it
    keeps survive, in the sites table and in the grid record alike, so the two always name the same
    timepoints. A source well with no grid record has no tables and is skipped; one whose kept timepoints are
    none writes nothing rather than an empty table that would read as an analysis that found nothing. Each
    file is written beside its destination and renamed into place.
    """
    import os

    from util.well_token import canonical_well_label

    src, dest = nanowell_dir(src_out), nanowell_dir(dest_out)
    written: dict[str, int] = {}
    for grid_path in sorted(src.glob("*_grid.json")) if src.is_dir() else []:
        src_well = grid_path.name[: -len("_grid.json")]
        dest_well = well_map.get(canonical_well_label(src_well) or src_well)
        table = sites_table(src, src_well)
        if dest_well is None or table is None:
            continue
        grid, problem = _read_json(grid_path)
        if problem:
            raise ValueError(f"The nanowell grid record of {src_well} cannot be carried: {problem}")
        blocks = grid.get("timepoints", {})
        kept = {tp for tp in blocks if keep_timepoint is None or keep_timepoint(tp)}
        with _open_text(table, "r") as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            rows = [row for row in reader if keep_timepoint is None or keep_timepoint(row.get("timepoint", ""))]
        if not kept:
            continue
        dest.mkdir(parents=True, exist_ok=True)
        grid = dict(grid, well=dest_well, timepoints={tp: b for tp, b in blocks.items() if tp in kept})
        out_table = dest / (dest_well + table.name[len(src_well):])
        tmp = out_table.with_name(out_table.name + ".tmp")
        with _open_text(tmp, "w") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, out_table)
        out_grid = dest / f"{dest_well}_grid.json"
        tmp = out_grid.with_name(out_grid.name + ".tmp")
        tmp.write_text(json.dumps(grid, indent=1), encoding="utf-8")
        os.replace(tmp, out_grid)
        written[dest_well] = len(rows)
    return written


def nanowell_block_conflict(info1: dict, info2: dict) -> str | None:
    """Why two datasets' nanowell analyses cannot share one plate, or ``None`` when they can.

    The sidecar's ``nanowell`` block names the scheme and the settings the tables were computed under; a plate
    holding wells analysed under two of them would draw one figure from two analyses with nothing to say so.
    A dataset without the block has no tables to disagree with.
    """
    first, second = info1.get("nanowell"), info2.get("nanowell")
    if not first or not second:
        return None
    for key, label in (("scheme_version", "table scheme"), ("settings", "nanowell settings")):
        if first.get(key) != second.get(key):
            return f"the two datasets' nanowell results were computed under different {label}"
    return None


def _input_timepoints(zip_path: Path, schema, sep: str) -> set[str] | None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [Path(n).name for n in zf.namelist()]
    except (OSError, zipfile.BadZipFile):
        return None
    return {tp for _fov, tp in fov_keys_from_member_names(names, schema=schema, sep=sep, skip_hidden=False,
                                                         images_only=True)}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, f"{path.name} is unreadable ({type(exc).__name__}: {exc})"


def _sites_timepoints(path: Path) -> set[str] | None:
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return {row["timepoint"] for row in csv.DictReader(fh)}
    except (OSError, KeyError):
        return None


def nanowell_findings(paths, in_zips: dict, schema, sep: str) -> list[tuple[str, str, str]]:
    """``(severity, code, message)`` for the nanowell tables against each other and the input archives.

    *in_zips* maps a well to its input zip. A dataset without nanowell tables yields nothing. Checks, per
    well: the grid record reads and has a scheme version this code understands; the sites table exists and
    holds rows only for timepoints the grid names; every ``ok`` timepoint has rows; and the grid's
    timepoints are the input archive's (a timepoint the archive has and the grid lacks is a timepoint the
    analysis never covered; one the grid has and the archive lacks is a table from other images).
    """
    folder = nanowell_dir(paths.out_dir)
    if not folder.is_dir():
        return []
    findings: list[tuple[str, str, str]] = []
    grids = sorted(folder.glob("*_grid.json"))
    if not grids:
        return [("error", "nanowell_grid_missing", f"{folder} holds no *_grid.json record")]
    for grid_path in grids:
        well = grid_path.name[: -len("_grid.json")]
        grid, problem = _read_json(grid_path)
        if problem:
            findings.append(("error", "nanowell_grid_unreadable", problem))
            continue
        if grid.get("scheme_version") != SCHEME_VERSION:
            findings.append(("error", "nanowell_scheme", f"{well}: scheme version {grid.get('scheme_version')!r}; "
                                                          f"this version reads {SCHEME_VERSION}"))
            continue
        blocks = grid.get("timepoints", {})
        sites = _sites_timepoints(folder / f"{well}.csv")
        if sites is None:
            findings.append(("error", "nanowell_table_missing", f"{well}: {well}.csv is missing or has no timepoint column"))
            continue
        for tp in sorted(sites - set(blocks)):
            findings.append(("error", "nanowell_table_mismatch", f"{well}: rows for timepoint {tp!r}, which the grid record does not name"))
        for tp, block in sorted(blocks.items()):
            if block.get("status") == "ok" and tp not in sites:
                findings.append(("error", "nanowell_table_mismatch", f"{well}: timepoint {tp!r} is ok in the grid record and has no rows"))
        archive = _input_timepoints(in_zips[well], schema, sep) if well in in_zips else None
        if archive is not None:
            for tp in sorted(archive - set(blocks)):
                findings.append(("warning", "nanowell_timepoint_missing", f"{well}: input timepoint {tp!r} was never analysed"))
            for tp in sorted(set(blocks) - archive):
                findings.append(("error", "nanowell_timepoint_unknown", f"{well}: the tables name timepoint {tp!r}, which the input archive does not hold"))
    if not findings:
        findings.append(("info", "nanowell_present", "Nanowell tables match the grid record and the input archives"))
    return findings
