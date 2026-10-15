"""What the Datasets tools do with a dataset's ``out/nanowells/`` tables (tracker P10).

A nanowell analysis leaves, beside the usual per-well CSV and archive, ``out/nanowells/<well>.csv`` (one
row per site and timepoint) and ``<well>_grid.json`` (the registration, lattice and status of each
timepoint). A tool that rewrites a dataset's wells, timepoints or fields and does not know about them
would write a destination whose cells have no nanowells behind them: the tables silently dropped, the
rest looking whole. So Subset, Merge Plates and Concatenate **refuse** such a dataset by name until each
learns to carry the tables (tracker P10b-d), and Validate checks the tables against the archives.

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
            f"{label} holds nanowell results ({nanowell_dir(paths.out_dir)}), and {action} cannot carry them "
            "yet: the destination would have cells with no nanowells behind them. Run Analyze again on the "
            "combined raw input, or choose a dataset without nanowell results.")


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
