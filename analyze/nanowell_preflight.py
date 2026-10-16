"""What can be checked about a nanowell run before it starts, without reading an image (tracker A3).

Qt-free. Two kinds of finding, kept apart because they are answered differently: a **problem** stops the run
(no acquisition record, no well archives, a tile the record names that the archive does not hold, a grid that
cannot be placed), a **warning** is said and the run goes on (an overlap smaller than a nanowell, which fuses
the nanowells on the seams: they are measured from a blended crop, with an undefined count, not skipped).
"""

from __future__ import annotations

import math
from pathlib import Path

from analyze.domain.nanowell_fields import PLATE_FORMAT_CHOICES

#: Findings listed per kind before the rest are summarised: a plate of 96 wells with the same fault says it once.
MAX_LISTED = 5


def wells_directory(raw: str) -> Path:
    """Where the well archives are: ``<raw>/in`` when that exists, else *raw* itself (the folder may be ``in``)."""
    root = Path(raw)
    return root / "in" if (root / "in").is_dir() else root


def _pitch_and_diameter(plate_format: str, opts: dict | None = None) -> tuple[float, float]:
    """The format's nominal (pitch, diameter); a custom format's from the form's fields (*opts*)."""
    from pipeline.nanowell_run import CUSTOM_FORMAT, PLATE_FORMATS

    if plate_format == CUSTOM_FORMAT:
        return float(str(opts["nanowell_pitch_um"])), float(str(opts["nanowell_diameter_um"]))
    return PLATE_FORMATS[plate_format]


def seam_site_estimate(plan, plate_format: str, margin_um: float | None, *, phases: int = 8,
                       opts: dict | None = None) -> tuple[int, int]:
    """``(sites on the seams, sites in all)``, expected, for a hexagonal lattice laid over the tiles of *plan*.

    Which sites a run fuses depends on where the array sits, which only detection knows, and one placement
    of the lattice can land entirely between the seams' bands (a band is a fraction of a pitch wide). So the
    lattice of the format's pitch is laid over the union of the tiles at *phases* different offsets, each
    asked of ``ownership_report`` (the code the run itself uses), and the mean count is returned, rounded.
    """
    import numpy as np

    from pipeline.tile_grid import ownership_report

    pitch, diameter = _pitch_and_diameter(plate_format, opts)
    margin = diameter / 10 if margin_um is None else margin_um
    rects = [p.rectangle() for p in plan.placements]
    left, top = min(r.left for r in rects), min(r.top for r in rects)
    right, bottom = max(r.right for r in rects), max(r.bottom for r in rects)
    step_y = pitch * math.sqrt(3) / 2
    fused, totals = [], []
    for k in range(phases):
        shift_x, shift_y = pitch * k / phases, step_y * ((k * 3) % phases) / phases
        points = []
        for j, y in enumerate(np.arange(top + diameter + shift_y, bottom - diameter, step_y)):
            offset = pitch / 2 if j % 2 else 0.0
            points += [(x, y) for x in np.arange(left + diameter + offset + shift_x, right - diameter, pitch)]
        if points:
            fused.append(len(ownership_report(plan, np.array(points), diameter, margin).fused_indices))
            totals.append(len(points))
    if not totals:
        return 0, 0
    return int(round(float(np.mean(fused)))), int(round(float(np.mean(totals))))


def nanowell_input_report(raw: str, opts: dict) -> tuple[list[str], list[str]]:
    """``(problems, warnings)`` for the dataset at *raw* under the form's nanowell *opts*."""
    from util.well_token import canonical_well_label, well_archives
    from pipeline.tile_grid import OK, place_from_record, tiles_from_record
    from util.acquisition_record import AcquisitionRecordError, read_record_file, validate_record_paths

    problems: list[str] = []
    warnings: list[str] = []
    if not raw:
        return ["No input folder selected."], warnings
    folder = wells_directory(raw)
    # The run's own enumeration (roll-up review X8): a `._A01.zip` sidecar is not a well, `A01.ZIP` is one, and
    # `a1.zip` is well A01 — which the record names it as.
    found = [(canonical_well_label(p.stem), p) for p in well_archives(folder)]
    zips = [path for _well, path in found]
    well_of = {path: well for well, path in found}
    if not zips:
        return [f"Nanowell mode reads each well from a zip ({folder}{'' if folder.is_dir() else ' does not exist'}): "
                "no well archives were found."], warnings
    record_path = folder / "acquisition.json"
    try:
        record = read_record_file(record_path)
    except AcquisitionRecordError as exc:
        return [f"The acquisition record cannot be used: {exc}"], warnings
    if record is None:
        if not str(opts.get("nanowell_grid") or "").strip():
            return [f"There is no acquisition record ({record_path}). Nanowell mode needs it to place the tiles in "
                    "each well; the CZI, ND2 and OME converters write it. For a dataset converted before they did, "
                    "describe the tile grid instead (Tile grid, Scan order, Tile overlap, Pixel size)."], warnings
        return _pattern_report(zips, opts)
    unplaced = _unplaced(record)
    described = bool(str(opts.get("nanowell_grid") or "").strip())
    if unplaced and not described:
        return _listed("The acquisition record does not say where the tiles sit in the image frame (no mosaic "
                       "position, or no pixel size), so describe the tile grid instead (Tile grid, Scan order, Tile "
                       "overlap, Pixel size)", unplaced), warnings
    if unplaced:
        problems, warnings = _pattern_report(
            [z for z in zips if well_of[z] in unplaced], opts,
            lead=f"The acquisition record cannot place {len(unplaced)} well(s) (no mosaic position, or no pixel "
                 "size)")
        record = {**record, "tiles": [t for t in record["tiles"] if t["well"] not in unplaced]}
        zips = [z for z in zips if well_of[z] not in unplaced]
        if not record["tiles"]:
            return problems, warnings
    schema = str(opts.get("filename_schema") or "")
    sep = str(opts.get("filename_sep") or "_")
    if schema:
        issues = validate_record_paths(zips, record, schema=schema, sep=sep).issues
        problems += _listed("A tile the record names is not in the archives", issues)
    problems += _grid_problems(record, place_from_record, tiles_from_record, OK)
    warnings += _overlap_warnings(record, opts, place_from_record, tiles_from_record, OK)
    return problems, warnings


def _pattern_report(zips, opts: dict, *, lead: str = "There is no acquisition record") -> tuple[list[str], list[str]]:
    """No record, a tile grid given (tracker P15): every timepoint of every well must fit the grid's cells.

    Read from the archives' member names, the way the run groups them; the images are not opened, so the tile
    size and the overlap's sufficiency are the run's to find. Said once, as a warning: the placement is the
    user's description, not a record, and a wrong scan order puts tiles in the wrong cells.
    """
    import zipfile

    from analyze.domain.nanowell_fields import parse_grid
    from pipeline.incremental_keys import fov_keys_from_member_names

    rows, cols = parse_grid(str(opts.get("nanowell_grid"))) or (0, 0)
    schema = str(opts.get("filename_schema") or "")
    sep = str(opts.get("filename_sep") or "_")
    phase = str(opts.get("phase_token") or "").strip().lower()
    warnings = [f"{lead}: the tiles will be placed from the {rows} x {cols} grid in "
                f"{opts.get('nanowell_scan') or 'top-left-row'} order, fields in the order of their names. Use Test "
                "detection to check the addresses land before a plate-wide run."]
    if not schema:
        return [], warnings
    over = []
    for path in zips:
        try:
            with zipfile.ZipFile(path) as zf:
                names = [Path(n).name for n in zf.namelist()]
        except (OSError, zipfile.BadZipFile) as exc:
            over.append(f"{path.name} cannot be read ({exc})")
            continue
        if phase:
            names = [n for n in names if _channel_of(n, schema, sep) == phase]
        fields: dict[str, set] = {}
        for fov, tp in fov_keys_from_member_names(names, schema=schema, sep=sep, skip_hidden=True,
                                                   images_only=True):
            fields.setdefault(tp, set()).add(fov)
        over += [f"{path.stem} at {tp}: {len(f)} fields for a {rows} x {cols} grid" for tp, f in sorted(fields.items())
                 if len(f) > rows * cols]
    return _listed("A well has more fields than the tile grid holds", over), warnings


def _channel_of(name: str, schema: str, sep: str) -> str | None:
    """The channel token of member *name* by the schema, lower-cased, read from the name without its extension
    (a channel last in the name would otherwise carry ``.tif``); ``None`` when the name is too short for it."""
    fields = [f for f in schema.split(":") if f]
    parts = name.rsplit(".", 1)[0].split(sep)
    if "channel" not in fields or len(parts) < len(fields):
        return None
    return parts[fields.index("channel")].lower()


def _unplaced(record: dict) -> list[str]:
    from pipeline.nanowell_pattern_record import unplaced_wells

    return unplaced_wells(record)


def _listed(lead: str, items) -> list[str]:
    items = list(items)
    shown = [f"{lead}: {item}" for item in items[:MAX_LISTED]]
    if len(items) > MAX_LISTED:
        shown.append(f"{lead}: and {len(items) - MAX_LISTED} more.")
    return shown


def _plans(record: dict, place_from_record, tiles_from_record):
    """Each (well, timepoint)'s placed grid, found with one pass over the record's tiles (not one per pair)."""
    by_key: dict[tuple, list] = {}
    for tile in record.get("tiles", []):
        by_key.setdefault((tile["well"], tile["timepoint"]), []).append(tile)
    for key, tiles_of_key in by_key.items():
        found = tiles_from_record({"timepoints": record.get("timepoints", {}), "tiles": tiles_of_key}, *key)
        if found is not None:
            tiles, tile_size, pixel_size, overlap = found
            yield key, place_from_record(tiles, tile_size_px=tile_size, pixel_size_um=pixel_size,
                                         declared_overlap_percent=overlap)


def _grid_problems(record, place_from_record, tiles_from_record, ok) -> list[str]:
    bad = [f"{well} at {tp}: {plan.reason}" for (well, tp), plan in _plans(record, place_from_record, tiles_from_record)
           if plan.status != ok]
    return _listed("The tiles of a well cannot be placed", bad)


def _overlap_warnings(record, opts, place_from_record, tiles_from_record, ok) -> list[str]:
    plate_format = str(opts.get("nanowell_format") or "")
    if plate_format not in dict(PLATE_FORMAT_CHOICES):
        return []
    margin_text = str(opts.get("nanowell_margin_um") or "").strip()
    try:
        margin = float(margin_text) if margin_text else None
    except ValueError:
        return []
    _pitch, diameter = _pitch_and_diameter(plate_format, opts)
    needed = diameter + 2 * (diameter / 10 if margin is None else margin)
    out = []
    estimates = 0
    for (well, tp), plan in _plans(record, place_from_record, tiles_from_record):
        if plan.status != ok:
            continue
        short = [o for o in plan.overlap_um if o is not None and o < needed]
        if not short:
            continue
        if estimates >= MAX_LISTED:
            out.append(f"{well} at {tp}: the tiles overlap by {min(short):.0f} µm, less than the {needed:.0f} µm needed.")
            continue
        estimates += 1                      # the estimate costs ~20 ms; only the ones that will be listed are made
        seams, total = seam_site_estimate(plan, plate_format, margin, opts=opts)
        out.append(f"{well} at {tp}: the tiles overlap by {min(short):.0f} µm, less than the {needed:.0f} µm a "
                   f"{diameter:.0f} µm nanowell needs to lie inside one tile with its margin: an array filling the tiles "
                   f"would have about {seams} of its {total} nanowells on a seam, each measured from a blended crop "
                   f"with no cell count (fewer if the array is smaller than the tiles).")
    return _listed("Overlap", out) if out else []
