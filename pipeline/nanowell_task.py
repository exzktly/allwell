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


def cell_columns(fluor_tokens) -> list[str]:
    """The per-cell CSV's columns, in the order :func:`cell_records` writes them."""
    return (["well", "fov", "timepoint", "nucleus_id", "area_px"] + [f"{t}_mean_intensity" for t in fluor_tokens]
            + ["nw_q", "nw_r", "nw_ring", "nw_label", "well_x_um", "well_y_um"])


def write_empty_cells_csv(path: Path, fluor_tokens) -> None:
    """A per-cell CSV with its header and no rows, for a well none of whose nanowells holds a cell.

    The well is whole, and the rest of the app (the remote fetch's check that a finished well has its CSV,
    the skip of wells already done, Review's loader) reads a missing CSV as a well that did not finish.
    An empty one says "measured, found no cells".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.part")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(cell_columns(fluor_tokens))
    tmp.replace(path)


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


def _prior_state(prior_dir, well_label, csv_prefix, settings_key, timepoints, record) -> dict | None:
    """What an earlier run of this well left, if it is still valid for this one: ``(grid, site rows, cell rows)``.

    Valid means: the settings that produced it are this run's (the same check the per-field path makes),
    the scheme version is understood, and each timepoint kept is ``ok`` with the record digest unchanged
    since. Anything else is recomputed, never reused. ``None`` when nothing can be reused.
    """
    from pipeline.settings_record import well_settings_allow_reuse

    grid_path = Path(prior_dir) / NANOWELL_DIR / f"{well_label}_grid.json"
    if not grid_path.is_file() or not well_settings_allow_reuse(prior_dir, well_label, settings_key):
        return None
    try:
        grid = json.loads(grid_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if grid.get("scheme_version") != SCHEME_VERSION:
        return None
    kept = {tp for tp in timepoints
            if grid.get("timepoints", {}).get(tp, {}).get("status") == nr.OK
            and grid["timepoints"][tp].get("record_digest") == nr.record_digest(record, well_label, tp)}
    if not kept:
        return None
    sites_path = Path(prior_dir) / NANOWELL_DIR / f"{well_label}.csv"
    cells_path = Path(prior_dir) / f"{csv_prefix}_{well_label}.csv"
    sites = [r for r in _read_rows(sites_path) if r["timepoint"] in kept]
    cells = [r for r in _read_rows(cells_path) if r["timepoint"] in kept]
    return {"grid": grid, "kept": kept, "sites": sites, "cells": cells}


class TimepointCheckpoints:
    """Per-(well, timepoint) resume checkpoints in the well's staging directory (tracker P7).

    The same atomic record-then-marker files the per-field path writes (``.fov_<id>.done``, so the
    scratch sweep that keeps resumable directories keeps these too), under the id ``nw_<timepoint>``.
    A checkpoint is only taken back under the key it was written with: the roster radius and the record
    digest. The settings key is the staging directory's name, so a run under other settings never
    reaches it. A template carried from an earlier timepoint (``earliest``) is not checkpointed, so a
    resumed run may build its own where an uninterrupted one would have reused the first; both are
    recorded in the timepoint's ``template`` block.
    """

    def __init__(self, staging: Path, settings_key: str):
        self.staging, self.settings_key = Path(staging), settings_key

    def save(self, timepoint: str, key: dict, measured: nr.Measured) -> None:
        from pipeline.fov_checkpoint import _save_fov_checkpoint

        payload = {"sites": measured.sites, "cells": measured.cells, "notes": measured.notes,
                   "template": {k: v for k, v in measured.template.items() if k != "_object"}}
        self.staging.mkdir(parents=True, exist_ok=True)
        _save_fov_checkpoint(self.staging, f"nw_{timepoint}", [json.loads(json.dumps(payload, default=_json_default))],
                             self.settings_key, quality=key)

    def load(self, timepoint: str, key: dict) -> nr.Measured | None:
        from pipeline.fov_checkpoint import _load_fov_checkpoint

        found = _load_fov_checkpoint(self.staging, f"nw_{timepoint}", self.settings_key)
        if found is None or found[1] != json.loads(json.dumps(key, default=_json_default)) or len(found[0]) != 1:
            return None
        payload = found[0][0]
        return nr.Measured(timepoint, payload["sites"], payload["cells"], payload["template"], payload["notes"])


def _read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_montages(outcome: nr.WellOutcome, well_label: str, staging: Path) -> None:
    """One PNG per located timepoint (role ``montage``) so the well has an archive to look at."""
    from skimage.io import imsave

    staging.mkdir(parents=True, exist_ok=True)
    for tp, located in outcome.located.items():
        if located.montage is not None:
            imsave(str(staging / f"{well_label}_{tp}_montage.png"), located.montage.image, check_contrast=False)


def process_well_zip(well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                     csv_prefix, schema, sep, *, grouper, parse, extract, scratch_dir, write_csv, segmenter=None,
                     incremental=False, prior_dir=None, settings_key="", staging_dir=None, plate_rigid=None):
    """Run one zipped well in nanowell mode; returns ``(records, failed_timepoints)`` like its counterpart.

    *grouper*, *parse*, *extract*, *write_csv* are ``process_microscopy``'s ``find_image_groups_in_zip``,
    ``parse_filename``, ``extract_zip`` and ``_write_single_csv``; *scratch_dir* is where this well's
    members are extracted (the caller removes it). With *incremental*, timepoints an earlier run finished
    under the same settings and record are kept and only the rest are computed, onto the same roster.
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
    index = _tile_index(groups, parse, schema, sep)
    timepoints = timepoint_order(record, well_label)
    output_dir = Path(output_dir)
    prior = (_prior_state(prior_dir or output_dir, well_label, csv_prefix, settings_key, timepoints, record)
             if incremental else None)
    todo = [tp for tp in timepoints if prior is None or tp not in prior["kept"]]
    if not todo:
        log.info("Well %s — up to date (no new timepoints).", well_label)
        return [], []
    wanted = {Path(p).name for (tp, _), (nuclear, fluors) in index.items() if tp in todo for p in [nuclear, *fluors]}
    extract(zip_path, scratch_dir, members_to_extract=wanted & set(needed))

    def load_phase(tp, tile) -> np.ndarray | None:
        found = index.get((tp, tile))
        return None if found is None else _read_tif(found[0])

    def channel_loader(token):
        def load(tp, tile) -> np.ndarray | None:
            found = index.get((tp, tile))
            if found is None:
                return None
            for path in found[1]:
                if token.lower() in Path(path).stem.lower():
                    return _read_tif(path)
            return None
        return load

    channels = {t: channel_loader(t) for t in fluor_tokens}
    fixed = None if prior is None else prior["grid"].get("array_radius", {}).get("radius_um")
    outcome = nr.run_well(well_label, record, todo, load_phase, settings, segmenter=segmenter, channels=channels,
                          fixed_radius_um=fixed, plate_rigid=plate_rigid,
                          checkpoint=None if staging_dir is None else TimepointCheckpoints(staging_dir, settings_key))
    px_by_tp = {tp: float(record["timepoints"][tp]["pixel_size_um"]["x"]) for tp in todo
                if tp in record["timepoints"]}
    records = cell_records(outcome, well_label, fluor_tokens, px_by_tp)
    if prior is not None:
        outcome.timepoints = {**{tp: prior["grid"]["timepoints"][tp] for tp in prior["kept"]}, **outcome.timepoints}
        outcome.radius = prior["grid"].get("array_radius", outcome.radius) if fixed is not None else outcome.radius
    rank = {tp: k for k, tp in enumerate(timepoints)}
    all_sites = sorted((prior["sites"] if prior else []) + outcome.sites, key=lambda r: rank[r["timepoint"]])
    all_cells = sorted((prior["cells"] if prior else []) + records, key=lambda r: rank[r["timepoint"]])
    if all_cells:
        write_csv(all_cells, output_dir / f"{csv_prefix}_{well_label}.csv")
    else:
        write_empty_cells_csv(output_dir / f"{csv_prefix}_{well_label}.csv", fluor_tokens)
    write_sites_csv(output_dir / NANOWELL_DIR / f"{well_label}.csv", all_sites, fluor_tokens)
    write_grid_json(output_dir / NANOWELL_DIR / f"{well_label}_grid.json", well_label, outcome, settings)
    if staging_dir is not None:
        _write_montages(outcome, well_label, staging_dir)
    failed = [tp for tp in todo if outcome.timepoints.get(tp, {}).get("status") != nr.OK]
    for tp in failed:
        block = outcome.timepoints.get(tp, {})
        log.warning("Well %s timepoint %s: %s (%s)", well_label, tp, block.get("status"), block.get("reason", ""))
    return records, failed


def run_zip_task(well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                 csv_prefix, schema, sep, settings_key, *, grouper, parse, segmenter=None, incremental=False,
                 incremental_prior_dir=None, plate_rigid=None):
    """The whole lifecycle of one zipped well in nanowell mode (what ``process_well_zip_task`` does otherwise).

    Extract to scratch, run, write the three tables and the montage archive, record completeness (in
    timepoints, the unit of work here) and the settings that produced it, remove the scratch. A failure
    writes the usual worker trace and re-raises, so the well is reported failed exactly as in the
    per-field path.
    """
    from pipeline.fov_checkpoint import _staging_dir_for
    from pipeline.io import (
        _write_single_csv, compress_images_to_zip, extract_zip, merge_images_into_zip, remove_directory,
        scratch_base_for,
    )
    from pipeline.run_report import write_well_report
    from pipeline.settings_record import record_well_settings

    output_dir = Path(output_dir)
    scratch = scratch_base_for(output_dir) / f"_tmp_extract_{well_label}"
    staging = _staging_dir_for(output_dir, well_label, settings_key)
    if scratch.exists():
        remove_directory(scratch)
    if staging.exists() and not any(staging.glob(".fov_*.done")):
        remove_directory(staging)               # no checkpoint to resume from: start clean
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        records, failed = process_well_zip(
            well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
            schema, sep, grouper=grouper, parse=parse, extract=extract_zip, scratch_dir=scratch,
            write_csv=_write_single_csv, segmenter=segmenter, incremental=incremental,
            prior_dir=incremental_prior_dir, settings_key=settings_key, staging_dir=staging, plate_rigid=plate_rigid)
        if not (output_dir / NANOWELL_DIR / f"{well_label}_grid.json").is_file():
            return records, failed
        grid = json.loads((output_dir / NANOWELL_DIR / f"{well_label}_grid.json").read_text(encoding="utf-8"))
        out_zip = output_dir / f"{well_label}_out.zip"
        if staging.is_dir():
            if out_zip.exists():
                merge_images_into_zip(out_zip, staging)
            else:
                compress_images_to_zip(staging, out_zip)
        total = len(grid["timepoints"])
        write_well_report(output_dir, well_label, groups_total=total,
                          groups_covered=sum(1 for b in grid["timepoints"].values() if b.get("status") == nr.OK),
                          failed=failed)
        record_well_settings(output_dir, well_label, settings_key)
        log.info("Well %s — done (%d timepoint(s), %d not ok).", well_label, total, len(failed))
        return records, failed
    finally:
        remove_directory(scratch)
        remove_directory(staging)


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

    if not options:
        return
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


def refuse_nanowell_without_zips(options, well_zips, error) -> None:
    """Nanowell mode reads each well from ``in/<well>.zip``; a folder-mode plate is refused by name (*error* exits)."""
    if options and not well_zips:
        error("--nanowell_format needs the wells as zips (in/<well>.zip); no well zips were found.")


# ── the end-of-run pass: addresses from the rest of the plate (tracker P3) ───
#: Methods that anchored a timepoint on its own evidence; a plate-rigid anchor is not evidence for another well.
_INDEPENDENT = ("fiducial", "array_centre")


def _read_grids(output_dir: Path) -> dict[str, dict]:
    grids = {}
    for path in sorted((Path(output_dir) / NANOWELL_DIR).glob("*_grid.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("scheme_version") == SCHEME_VERSION:
            grids[payload["well"]] = payload
    return grids


def _origin(grids: dict, well: str, timepoint: str) -> tuple | None:
    block = grids.get(well, {}).get("timepoints", {}).get(timepoint, {})
    if block.get("status") == nr.OK and block.get("origin_method") in _INDEPENDENT and block.get("origin_stage_um"):
        return tuple(block["origin_stage_um"])
    return None


def plan_reanchor(grids: dict[str, dict]) -> tuple[dict, dict]:
    """``(predictions, reasons)`` for every unaddressed (well, timepoint) the rest of the plate can anchor.

    The plate is taken out between timepoints and put back moved, but it moves as one rigid object, so
    the wells anchored on their own evidence at both a reference timepoint and this one fix a rotation
    and translation (``fit_plate_rigid``); the target's origin at the reference, carried through it, is
    where its origin is now. ``predictions[well][timepoint]`` is an ``OriginPrediction`` in that
    well's montage frame, ready for ``resolve_origin``. Whatever cannot be predicted is in *reasons*
    with why: no stage positions, no reference origin, fewer than three other wells, an imprecise fit.
    """
    from pipeline.nanowell_lattice import OriginPrediction, fit_plate_rigid, predict_origin

    predictions: dict[str, dict] = {}
    reasons: dict[tuple[str, str], str] = {}
    for well, grid in grids.items():
        for tp, block in grid["timepoints"].items():
            if block.get("status") != nr.UNADDRESSED:
                continue
            key = (well, tp)
            if block.get("stage_offset_um") is None:
                reasons[key] = "the record has no stage position for every tile, so wells cannot be compared"
                continue
            reference = next((r for r in grid["timepoints"] if r != tp and _origin(grids, well, r)), None)
            if reference is None:
                reasons[key] = "this well was never anchored on its own at another timepoint"
                continue
            others = [w for w in grids if w != well and _origin(grids, w, reference) and _origin(grids, w, tp)]
            fit = fit_plate_rigid({w: _origin(grids, w, reference) for w in others},
                                  {w: _origin(grids, w, tp) for w in others})
            if not fit.ok:
                reasons[key] = f"the plate's motion could not be fitted from {len(others)} other well(s): {fit.reason}"
                continue
            stage = predict_origin(fit, _origin(grids, well, reference))
            if not stage.ok:
                reasons[key] = stage.reason
                continue
            offset, montage_origin = block["stage_offset_um"], block["montage_origin_um"]
            position = (stage.position[0] - offset[0] - montage_origin[0],
                        stage.position[1] - offset[1] - montage_origin[1])
            predictions.setdefault(well, {})[tp] = OriginPrediction(
                True, "", position, stage.uncertainty, stage.uncertainty_with_site)
    return predictions, reasons


def reanchor_plate(output_dir, well_zips, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix, schema,
                   sep, settings_key, *, grouper, parse, segmenter=None) -> dict:
    """Re-run the unaddressed (well, timepoint)s the rest of the plate can anchor; report what it did.

    Only those wells' rows for those timepoints are rewritten (an incremental run with the prediction
    handed in; every other timepoint is kept as it is). Returns the counts the sidecar records:
    ``anchored`` (now addressed), ``remaining`` (still unaddressed) and why for each.
    """
    output_dir = Path(output_dir)
    grids = _read_grids(output_dir)
    predictions, reasons = plan_reanchor(grids)
    zips = dict(well_zips)
    for well, by_tp in predictions.items():
        if well not in zips:
            continue
        log.info("Well %s — re-anchoring %s from the plate's other wells.", well, ", ".join(sorted(by_tp)))
        run_zip_task(well, zips[well], output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
                     schema, sep, settings_key, grouper=grouper, parse=parse, segmenter=segmenter, incremental=True,
                     incremental_prior_dir=output_dir, plate_rigid=by_tp)
    after = _read_grids(output_dir)
    anchored, remaining = [], {}
    for well, grid in after.items():
        for tp, block in grid["timepoints"].items():
            was = grids.get(well, {}).get("timepoints", {}).get(tp, {}).get("status")
            if was != nr.UNADDRESSED:
                continue
            if block.get("status") == nr.OK:
                anchored.append(f"{well}/{tp}")
            else:
                remaining[f"{well}/{tp}"] = reasons.get((well, tp)) or block.get("reason", "")
    return {"anchored": sorted(anchored), "remaining": remaining}


def nanowell_endgame(options, output_dir, well_zips, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
                     schema, sep, settings_key, *, grouper, parse) -> None:
    """What nanowell mode does after every well has run; a no-op when the mode is off (the caller does not branch)."""
    if not options:
        return
    summary = reanchor_plate(output_dir, well_zips, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
                             schema, sep, settings_key, grouper=grouper, parse=parse)
    if summary["anchored"] or summary["remaining"]:
        log.info("Re-anchoring: %d (well, timepoint) anchored from the plate, %d still unaddressed.",
                 len(summary["anchored"]), len(summary["remaining"]))
        for key, why in summary["remaining"].items():
            log.warning("Unaddressed %s: %s", key, why)
    _record_reanchor(Path(output_dir) / "pipeline_info.json", summary)


def _record_reanchor(info_path: Path, summary: dict) -> None:
    from pipeline.io import _safe_atomic_text_write
    from pipeline.run_report import merge_into_pipeline_info

    try:
        existing = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
        block = dict(existing.get("nanowell", {}))
        block["reanchor"] = summary
        merge_into_pipeline_info(info_path, {"nanowell": block}, atomic_writer=_safe_atomic_text_write)
    except (OSError, ValueError) as exc:
        log.warning("Could not record the re-anchoring summary in %s: %s", info_path, exc)
