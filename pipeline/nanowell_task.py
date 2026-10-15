"""A nanowell well, start to finish: the replacement for ``process_well_zip_task`` in nanowell mode.

``process_microscopy.process_well_zip_task`` hands a well here when the run has nanowell settings.
The surrounding lifecycle is the one every well has: extract what is needed to a scratch directory,
run, write the per-cell CSV, record completeness and settings, remove the scratch. What is different
is the middle: the unit of work is a (well, timepoint) with every tile of its grid together
(``nanowell_run.run_well``), and the outputs are three tables, not one.

Qt-free, and it must not import ``process_microscopy`` (the arrow runs the other way): the two
helpers that module owns, ``find_image_groups_in_zip`` and ``parse_filename``, are passed in.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import numpy as np

from pipeline import nanowell_run as nr

log = logging.getLogger("allwell.nanowell")

NANOWELL_DIR = "nanowells"
#: Version of the on-disk scheme (columns, conventions) so Review can refuse one it does not understand.
SCHEME_VERSION = 1

#: The columns of ``nanowells/<well>.csv`` in the order they are written (§3.7). Per-channel site
#: intensities follow, as ``<token>_integrated`` and ``<token>_mean_bg``.
SITE_COLUMNS = ("timepoint", "nw_q", "nw_r", "nw_ring", "well_x_um", "well_y_um", "owner", "radius_um",
                "geometry", "detected", "fused", "imaged_fraction", "visible_fraction", "state",
                "partially_visible", "focus", "n_cells", "confluence", "area_est_cells", "count_disagreement",
                "consistency")


class NanowellInputError(RuntimeError):
    """The inputs cannot be run in nanowell mode (no acquisition record, no tiles): named, never silent."""


def settings_from_kwargs(kwargs: dict) -> nr.NanowellSettings:
    spec = kwargs["_nanowell"]
    return nr.NanowellSettings(spec["plate_format"], spec.get("margin_um"), spec.get("cell_diameter_um", 15.0),
                               spec.get("square_edge_deg", 0.0))


def timepoint_order(record: dict, well: str) -> list[str]:
    """This well's timepoints in recorded time (hours when the key reads as one, else the key itself)."""
    from util.timepoint import parse_hours

    found = sorted({t["timepoint"] for t in record["tiles"] if t["well"] == well})

    def key(tp):
        hours = parse_hours(tp)
        return (0, hours, tp) if hours is not None else (1, 0.0, tp)

    return sorted(found, key=key)


def _read_tif(path: Path):
    import tifffile

    return np.asarray(tifffile.imread(str(path)), dtype=np.float32)


def _tile_index(groups, parse, schema, sep) -> dict:
    """``(timepoint, fov) -> (nuclear path, {fluor token: path})`` from the zip's image groups."""
    index = {}
    for nuclear, fluors in groups:
        meta = parse(nuclear, schema=schema, sep=sep)
        index[(meta["timepoint"], meta["fov"])] = (nuclear, fluors)
    return index


def cell_records(outcome: nr.WellOutcome, well: str, fluor_tokens, px_by_tp: dict) -> list[dict]:
    """The per-cell rows the existing CSV schema carries, plus the ``nw_*`` columns (§3.7).

    ``fov`` is the owner tile. The new columns exist only in nanowell mode. Intensity columns are the
    cell's mean on each fluorescence channel, named as the existing quantification names them.
    """
    cells = outcome.cells
    n = len(cells.get("label", ()))
    rows = []
    for k in range(n):
        tp = cells["timepoint"][k]
        row = {"well": well, "fov": cells["fov"][k], "timepoint": tp, "nucleus_id": cells["label"][k],
               "area_px": cells["area_um2"][k] / px_by_tp[tp] ** 2}
        for token in fluor_tokens:
            value = cells.get(f"mean_{token}", [None] * n)[k]
            row[f"{token}_mean_intensity"] = value
        for name in ("nw_q", "nw_r", "nw_ring", "nw_label", "well_x_um", "well_y_um"):
            row[name] = cells[name][k]
        rows.append(row)
    return rows


def write_sites_csv(path: Path, rows: list[dict], fluor_tokens) -> None:
    columns = list(SITE_COLUMNS) + [f"{t}_{kind}" for t in fluor_tokens for kind in ("integrated", "mean_bg")]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.part")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def write_grid_json(path: Path, well: str, outcome: nr.WellOutcome, settings: nr.NanowellSettings) -> None:
    payload = {"scheme_version": SCHEME_VERSION, "well": well, "settings": settings.key(),
               "array_radius": outcome.radius, "timepoints": outcome.timepoints}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
    tmp.replace(path)


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def process_well_zip(well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                     csv_prefix, schema, sep, *, grouper, parse, extract, scratch_dir, write_csv, segmenter=None):
    """Run one zipped well in nanowell mode; returns ``(records, failed_timepoints)`` like its counterpart.

    *grouper*, *parse*, *extract*, *write_csv* are ``process_microscopy``'s ``find_image_groups_in_zip``,
    ``parse_filename``, ``extract_zip`` and ``_write_single_csv``; *scratch_dir* is where this well's
    members are extracted (the caller removes it).
    """
    from util.acquisition_record import read_record_file

    settings = settings_from_kwargs(shared_kwargs_template)
    record = read_record_file(Path(zip_path).parent / "acquisition.json")
    if record is None:
        raise NanowellInputError(
            f"Nanowell mode needs the acquisition record ({Path(zip_path).parent / 'acquisition.json'}), which "
            "tells the run where each tile sits in the well; it is written when a CZI/ND2/OME dataset is converted")
    groups, needed = grouper(zip_path, nuclear_token, fluor_tokens, scratch_dir, schema=schema, sep=sep)
    if not groups:
        raise NanowellInputError(f"No image groups found in {Path(zip_path).name}")
    extract(zip_path, scratch_dir, members_to_extract=needed)
    index = _tile_index(groups, parse, schema, sep)
    timepoints = timepoint_order(record, well_label)

    def load_phase(tp, tile):
        found = index.get((tp, tile))
        return None if found is None else _read_tif(found[0])

    def channel_loader(token):
        def load(tp, tile):
            found = index.get((tp, tile))
            if found is None:
                return None
            for path in found[1]:
                if token.lower() in Path(path).stem.lower():
                    return _read_tif(path)
            return None
        return load

    channels = {t: channel_loader(t) for t in fluor_tokens}
    outcome = nr.run_well(well_label, record, timepoints, load_phase, settings, segmenter=segmenter,
                          channels=channels)
    px_by_tp = {tp: float(record["timepoints"][tp]["pixel_size_um"]["x"]) for tp in timepoints
                if tp in record["timepoints"]}
    records = cell_records(outcome, well_label, fluor_tokens, px_by_tp)
    output_dir = Path(output_dir)
    safe = well_label
    if records:
        write_csv(records, output_dir / f"{csv_prefix}_{safe}.csv")
    write_sites_csv(output_dir / NANOWELL_DIR / f"{safe}.csv", outcome.sites, fluor_tokens)
    write_grid_json(output_dir / NANOWELL_DIR / f"{safe}_grid.json", well_label, outcome, settings)
    failed = [tp for tp in timepoints if outcome.timepoints.get(tp, {}).get("status") != nr.OK]
    for tp in failed:
        block = outcome.timepoints.get(tp, {})
        log.warning("Well %s timepoint %s: %s (%s)", well_label, tp, block.get("status"), block.get("reason", ""))
    return records, failed


def run_zip_task(well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                 csv_prefix, schema, sep, settings_key, *, grouper, parse, segmenter=None):
    """The whole lifecycle of one zipped well in nanowell mode (what ``process_well_zip_task`` does otherwise).

    Extract to scratch, run, write the three tables, record completeness (in timepoints, the unit of
    work here) and the settings that produced it, remove the scratch. A failure writes the usual
    worker trace and re-raises, so the well is reported failed exactly as in the per-field path.
    """
    from pipeline.io import _write_single_csv, extract_zip, remove_directory, scratch_base_for
    from pipeline.run_report import write_well_report
    from pipeline.settings_record import record_well_settings

    output_dir = Path(output_dir)
    scratch = scratch_base_for(output_dir) / f"_tmp_extract_{well_label}"
    if scratch.exists():
        remove_directory(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        records, failed = process_well_zip(
            well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
            schema, sep, grouper=grouper, parse=parse, extract=extract_zip, scratch_dir=scratch,
            write_csv=_write_single_csv, segmenter=segmenter)
        grid = json.loads((output_dir / NANOWELL_DIR / f"{well_label}_grid.json").read_text(encoding="utf-8"))
        total = len(grid["timepoints"])
        write_well_report(output_dir, well_label, groups_total=total, groups_covered=total - len(failed),
                          failed=failed)
        record_well_settings(output_dir, well_label, settings_key)
        log.info("Well %s — done (%d timepoint(s), %d not ok).", well_label, total, len(failed))
        return records, failed
    finally:
        remove_directory(scratch)


def record_nanowell_info(info_path, options: dict, input_dir) -> None:
    """Add the ``nanowell`` and ``acquisition`` blocks to ``pipeline_info.json``.

    ``nanowell`` carries the settings, the conventions every address and angle is stated in (east axis,
    counter-clockwise, mirror recorded per timepoint) and the scheme version, so Review can refuse a
    scheme it does not understand. ``acquisition`` carries the pixel size and tile size per timepoint so
    Review can draw in micrometres. Best-effort, like the other blocks: a run must not fail to describe itself.
    """
    from pipeline.io import _safe_atomic_text_write
    from pipeline.run_report import merge_into_pipeline_info
    from util.acquisition_record import read_record_file

    block = {"nanowell": {"scheme_version": SCHEME_VERSION, "settings": dict(options),
                          "conventions": {"origin": "top-left of the well frame, y down",
                                          "angle": "counter-clockwise on screen, degrees, from image east",
                                          "mirror": "recorded per timepoint in nanowells/<well>_grid.json",
                                          "address": "axial (q, r); (0, 0) is the central square"}}}
    try:
        record = read_record_file(Path(input_dir) / "acquisition.json")
    except Exception as exc:                        # an unreadable record fails the wells, not the sidecar
        block["nanowell"]["acquisition_error"] = f"{type(exc).__name__}: {exc}"
        record = None
    if record is not None:
        block["acquisition"] = {tp: {"pixel_size_um": meta["pixel_size_um"], "tile_size_px": meta["tile_size_px"]}
                                for tp, meta in record.get("timepoints", {}).items()}
    try:
        merge_into_pipeline_info(info_path, block, atomic_writer=_safe_atomic_text_write)
    except OSError as exc:
        log.warning("Could not record the nanowell blocks in %s: %s", info_path, exc)
