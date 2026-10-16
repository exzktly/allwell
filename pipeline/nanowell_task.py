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
import zipfile
from pathlib import Path

import numpy as np

from pipeline import nanowell_measure as nm
from pipeline import nanowell_run as nr
from util.csv_gz import existing_csv

log = logging.getLogger("allwell.nanowell")

NANOWELL_DIR = "nanowells"
#: The well's earliest empty-nanowell template, kept beside its tables for later incremental runs (tracker C16).
TEMPLATE_SUFFIX = "_template.npz"
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
    custom = (float(spec["pitch_um"]), float(spec["diameter_um"])) if spec["plate_format"] == nr.CUSTOM_FORMAT \
        else None
    return nr.NanowellSettings(spec["plate_format"], spec.get("margin_um"), spec.get("cell_diameter_um", 15.0),
                               spec.get("square_edge_deg", 0.0), spec.get("occluded_below", 0.5),
                               spec.get("clipped_below", 0.5), custom,
                               spec.get("geometry") or nr.GEOMETRY_HEX_CIRCLE)


def _prior_array(prior: dict | None) -> tuple[float | None, list | None]:
    """``(radius, roster)`` an earlier run of the well shared across its timepoints, for an incremental run to keep:
    a circle's radius, or an inferred array's addresses (tracker X35); ``(None, None)`` with no earlier run."""
    if prior is None:
        return None, None
    block = prior["grid"].get("array_radius", {})
    return block.get("radius_um"), (block.get("roster") or None)      # empty: the earlier run anchored nothing


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


def _placing_record(record: dict | None, well: str, pattern: dict | None) -> dict | None:
    """*record*, or ``None`` when it cannot place *well* and a grid is described to place it instead.

    An OME or ND2 conversion records stage positions only, and any converter may lack a calibration; the
    described grid then places such a well (``nanowell_pattern_record.unplaced_wells``). Without a grid the well
    is refused naming what the record lacks, rather than refused tile by tile as an unplaceable grid.
    """
    from pipeline.nanowell_pattern_record import unplaced_wells

    if record is None or well not in unplaced_wells(record):
        return record
    if pattern:
        log.info("Well %s: the acquisition record has no mosaic position or no pixel size for it, so it is "
                 "placed from the described grid.", well)
        return None
    raise NanowellInputError(
        f"The acquisition record does not say where {well}'s tiles sit in the image frame: a tile has no mosaic "
        "position (a stage position alone cannot be placed while the stage axes' direction is unknown), or a "
        "timepoint has no pixel size. Describe the tile grid instead: --nanowell_grid, --nanowell_scan, "
        "--nanowell_overlap_percent and --nanowell_pixel_size_um")


def _pattern_record(well: str, zip_path, index: dict, pattern: dict) -> dict:
    """The in-memory record for a dataset with none, from the grid the run was given (tracker P15).

    Each timepoint's tile size is read from the header of one of its phase images in the archive, so a
    timepoint imaged at another size is placed with its own.
    """
    import io

    import tifffile

    from pipeline.nanowell_pattern_record import record_from_pattern

    fields: dict[str, list] = {}
    first: dict[str, str] = {}
    for (tp, fov), (nuclear, _fluors) in sorted(index.items()):
        fields.setdefault(tp, []).append(fov)
        first.setdefault(tp, Path(nuclear).name)
    sizes = {}
    with zipfile.ZipFile(zip_path) as zf:
        members = {Path(name).name: name for name in zf.namelist()}
        for tp, name in first.items():
            with tifffile.TiffFile(io.BytesIO(zf.read(members[name]))) as tif:
                height, width = tif.pages[0].shape[:2]
            sizes[tp] = (width, height)
    log.info("Well %s has no acquisition record: tiles placed from a %s grid, scan %s, %s%% overlap, %s um per "
             "pixel, fields in the order of their names", well, "x".join(map(str, pattern["grid"])),
             pattern["scan"], pattern["overlap_percent"], pattern["pixel_size_um"])
    return record_from_pattern(well, fields, sizes, pattern)


def _tile_index(groups, parse, schema, sep) -> dict:
    """``(timepoint, fov) -> (nuclear path, [fluor paths])`` from the zip's image groups.

    The fluor paths are in the order of the fluorescence tokens the grouper was given, which is how the
    per-field path pairs a token with a file; a token is never matched by looking for it in a filename (an
    experiment called ``Cy5run`` would answer to ``Cy5``).
    """
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
    plate = _plate_of(path)
    if plate:                       # a well Merge Plates brought from another plate stays that plate's
        payload["plate"] = plate
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
    tmp.replace(path)


def _plate_of(grid_path: Path) -> str | None:
    """The ``plate`` an existing grid record names (Merge Plates writes it), so a re-run keeps it."""
    try:
        plate = json.loads(Path(grid_path).read_text(encoding="utf-8")).get("plate")
    except (OSError, ValueError, AttributeError):
        return None
    return plate if isinstance(plate, str) and plate else None


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
    # A finished well's CSV is gzipped (``write_well_csv``): either spelling, plain winning (``util/csv_gz``).
    cells_path = existing_csv(Path(prior_dir) / f"{csv_prefix}_{well_label}.csv")
    sites = [r for r in _read_rows(sites_path) if r["timepoint"] in kept]
    cells = [r for r in _read_rows(cells_path) if r["timepoint"] in kept]
    return {"grid": grid, "kept": kept, "sites": sites, "cells": cells,
            "order": {tp: k for k, tp in enumerate(timepoints)}}


class TimepointCheckpoints:
    """Per-(well, timepoint) resume checkpoints in the well's staging directory (tracker P7).

    The same atomic record-then-marker files the per-field path writes (``.fov_<id>.done``, so the
    scratch sweep that keeps resumable directories keeps these too), under the id ``nw_<timepoint>``.
    A checkpoint is only taken back under the key it was written with: the roster radius and the record
    digest. The settings key is the staging directory's name, so a run under other settings never
    reaches it. A timepoint's own template is kept beside its checkpoint (``nw_<timepoint>.template.npz``), so
    a resumed run still has the earliest one to fall back to (tracker C16).
    """

    def __init__(self, staging: Path, settings_key: str):
        self.staging, self.settings_key = Path(staging), settings_key

    def save(self, timepoint: str, key: dict, measured: nr.Measured) -> None:
        from pipeline.fov_checkpoint import _save_fov_checkpoint

        payload = {"sites": measured.sites, "cells": measured.cells, "notes": measured.notes,
                   "template": {k: v for k, v in measured.template.items() if k != "_object"}}
        self.staging.mkdir(parents=True, exist_ok=True)
        template = measured.template.get("_object")
        if template is not None and measured.template.get("source") != "earliest":
            nm.save_template(self._template_path(timepoint), template, timepoint)
        _save_fov_checkpoint(self.staging, f"nw_{timepoint}", [json.loads(json.dumps(payload, default=_json_default))],
                             self.settings_key, quality=key)

    def load(self, timepoint: str, key: dict) -> nr.Measured | None:
        from pipeline.fov_checkpoint import _load_fov_checkpoint

        found = _load_fov_checkpoint(self.staging, f"nw_{timepoint}", self.settings_key)
        if found is None or found[1] != json.loads(json.dumps(key, default=_json_default)) or len(found[0]) != 1:
            return None
        payload = found[0][0]
        template = dict(payload["template"])
        kept = nm.load_template(self._template_path(timepoint))
        if kept is not None:
            template["_object"] = kept[0]
        return nr.Measured(timepoint, payload["sites"], payload["cells"], template, payload["notes"])

    def _template_path(self, timepoint: str) -> Path:
        return self.staging / f"nw_{timepoint}.template.npz"


def _reference_of(prior: dict | None) -> dict | None:
    """The first kept timepoint's lattice, for the consistency check of the ones added to it."""
    if prior is None:
        return None
    blocks = prior["grid"]["timepoints"]
    for tp in sorted(prior["kept"], key=lambda t: prior["order"].get(t, 0)):
        block = blocks[tp]
        if all(block.get(k) is not None for k in ("pitch_um", "angle_deg", "roster_size")):
            # An inferred well's kept lattice kind and diameter too: the new timepoints are refused if of another
            # kind, and measured at the same diameter (review X35-R13, R14).
            return {"timepoint": tp, "pitch_um": block["pitch_um"], "angle_deg": block["angle_deg"],
                    "roster_size": block["roster_size"], "lattice_kind": block.get("lattice_kind"),
                    "diameter_used_um": (block.get("geometry") or {}).get("diameter_used_um")}
    return None


def _read_rows(path: Path | None) -> list[dict]:
    """The rows of a ``.csv`` or ``.csv.gz``; none when there is no file."""
    from util.csv_gz import open_csv_text

    if path is None or not Path(path).is_file():
        return []
    with open_csv_text(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write_montages(outcome: nr.WellOutcome, well_label: str, staging: Path) -> None:
    """One PNG per located timepoint (role ``montage``) so the well has an archive to look at."""
    from skimage.io import imsave

    staging.mkdir(parents=True, exist_ok=True)
    for tp, located in outcome.located.items():
        if located.montage is not None:
            imsave(str(staging / f"{well_label}_{tp}_montage.png"), located.montage.image, check_contrast=False)


def update_montage_archive(out_zip: Path, staging: Path, well_label: str, recomputed) -> None:
    """Put this run's montages in ``<well>_out.zip``, replacing the ones of the timepoints it recomputed.

    A recomputed timepoint's old montage is dropped first, even when the timepoint now produces none (its
    images went missing): ``merge_images_into_zip`` keeps an existing member over a new one of the same
    name, which would leave the picture of a placement the rows no longer describe.
    """
    from pipeline.io import compress_images_to_zip, merge_images_into_zip

    stale = {f"{well_label}_{tp}_montage.png" for tp in recomputed}
    if out_zip.exists():
        with zipfile.ZipFile(out_zip) as zf:
            keep = [(i, zf.read(i.filename)) for i in zf.infolist() if i.filename not in stale]
        tmp = out_zip.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for info, data in keep:
                zf.writestr(info, data)
        tmp.replace(out_zip)
    if any(staging.glob("*_montage.png")):
        if out_zip.exists():
            merge_images_into_zip(out_zip, staging)
        else:
            compress_images_to_zip(staging, out_zip)


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
    pattern = shared_kwargs_template["_nanowell"].get("pattern")
    record = _placing_record(read_record_file(Path(zip_path).parent / "acquisition.json"), well_label, pattern)
    if record is None and not pattern:
        raise NanowellInputError(
            f"Nanowell mode needs the acquisition record ({Path(zip_path).parent / 'acquisition.json'}), which "
            "tells the run where each tile sits in the well; it is written when a CZI/ND2/OME dataset is converted. "
            "For a dataset converted without one, give the grid instead: --nanowell_grid, --nanowell_scan, "
            "--nanowell_overlap_percent and --nanowell_pixel_size_um")
    groups, needed = grouper(zip_path, nuclear_token, fluor_tokens, scratch_dir, schema=schema, sep=sep)
    if not groups:
        raise NanowellInputError(f"No image groups found in {Path(zip_path).name}")
    index = _tile_index(groups, parse, schema, sep)
    described = record is None
    if described:
        record = _pattern_record(well_label, zip_path, index, pattern)
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

    def channel_loader(position):
        def load(tp, tile) -> np.ndarray | None:
            found = index.get((tp, tile))
            if found is None or position >= len(found[1]):
                return None
            return _read_tif(found[1][position])
        return load

    channels = {t: channel_loader(k) for k, t in enumerate(fluor_tokens)}
    fixed, fixed_roster = _prior_array(prior)
    template_path = output_dir / NANOWELL_DIR / f"{well_label}{TEMPLATE_SUFFIX}"
    saved = nm.load_template(Path(prior_dir or output_dir) / NANOWELL_DIR / template_path.name) if prior else None
    outcome = nr.run_well(well_label, record, todo, load_phase, settings, segmenter=segmenter, channels=channels,
                          fixed_radius_um=fixed, fixed_roster=fixed_roster, plate_rigid=plate_rigid,
                          reference=_reference_of(prior),
                          checkpoint=None if staging_dir is None else TimepointCheckpoints(staging_dir, settings_key),
                          earliest_template=None if saved is None else saved[0],
                          spill_dir=Path(scratch_dir) / ".montages",     # removed with the scratch (X23)
                          # A described grid states its shape, so a well short of a whole row or column is
                          # incomplete; a record's positions alone could not show the line that is missing.
                          expected_shape=tuple(pattern["grid"]) if described else None)
    px_by_tp = {tp: float(record["timepoints"][tp]["pixel_size_um"]["x"]) for tp in todo
                if tp in record["timepoints"]}
    records = cell_records(outcome, well_label, fluor_tokens, px_by_tp)
    if prior is not None:
        outcome.timepoints = {**{tp: prior["grid"]["timepoints"][tp] for tp in prior["kept"]}, **outcome.timepoints}
        outcome.radius = (prior["grid"].get("array_radius", outcome.radius)
                          if fixed is not None or fixed_roster is not None else outcome.radius)
        # The earliest template may be a kept timepoint's: compare every timepoint against it, in time order.
        nr.flag_template_drift(outcome.timepoints, timepoints)
    rank = {tp: k for k, tp in enumerate(timepoints)}
    all_sites = sorted((prior["sites"] if prior else []) + outcome.sites, key=lambda r: rank[r["timepoint"]])
    all_cells = sorted((prior["cells"] if prior else []) + records, key=lambda r: rank[r["timepoint"]])
    if all_cells:
        write_csv(all_cells, output_dir / f"{csv_prefix}_{well_label}.csv")
    else:
        from pipeline.io import compress_well_csv

        # Gzipped like any finished well's CSV (``write_well_csv``), so a well with no cells is not the one plain
        # file in a folder of ``.csv.gz``.
        write_empty_cells_csv(output_dir / f"{csv_prefix}_{well_label}.csv", fluor_tokens)
        compress_well_csv(output_dir / f"{csv_prefix}_{well_label}.csv")
    write_sites_csv(output_dir / NANOWELL_DIR / f"{well_label}.csv", all_sites, fluor_tokens)
    write_grid_json(output_dir / NANOWELL_DIR / f"{well_label}_grid.json", well_label, outcome, settings)
    rank_of = {tp: k for k, tp in enumerate(timepoints)}
    if outcome.earliest_template is not None and (
            saved is None or rank_of.get(outcome.earliest_template[0], 0) < rank_of.get(saved[1], len(rank_of))):
        nm.save_template(template_path, outcome.earliest_template[1], outcome.earliest_template[0])
    elif saved is not None and not template_path.is_file():
        nm.save_template(template_path, saved[0], saved[1])           # a separate prior folder: carry it over
    if staging_dir is not None:
        _write_montages(outcome, well_label, staging_dir)
        update_montage_archive(output_dir / f"{well_label}_out.zip", staging_dir, well_label, todo)
    # Failed means no rows were written for it. An unaddressed timepoint has rows (detected sites, no address) and
    # is not a failure of the run; it is a finding in the grid record, the completeness check and the end-of-run
    # summary, and the end-of-run pass may yet anchor it.
    failed = [tp for tp in todo if outcome.timepoints.get(tp, {}).get("status") in nr.TERMINAL_WITHOUT_ROWS]
    for tp in todo:
        block = outcome.timepoints.get(tp, {})
        if block.get("status") != nr.OK:
            log.warning("Well %s timepoint %s: %s (%s)", well_label, tp, block.get("status"), block.get("reason", ""))
    return records, failed


def run_zip_task(well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                 csv_prefix, schema, sep, settings_key, *, grouper, parse, segmenter=None, incremental=False,
                 incremental_prior_dir=None, plate_rigid=None, scratch=None, staging=None, reanchoring=False):
    """The whole lifecycle of one zipped well in nanowell mode (what ``process_well_zip_task`` does otherwise).

    Extract to *scratch*, run, write the three tables and the montage archive, record completeness (in
    timepoints, the unit of work here) and the settings that produced it. ``process_well_zip_task`` calls
    this from inside its own ``try``, so the failure trace, ``--force`` (which clears the staging directory
    and with it the resume checkpoints) and the removal of both directories are its own, exactly as for a
    per-field well; a caller without them (the end-of-run pass) gets directories of its own, removed after.
    """
    from pipeline.fov_checkpoint import _staging_dir_for
    from pipeline.io import extract_zip, remove_directory, scratch_base_for, write_well_csv
    from pipeline.run_report import write_well_report
    from pipeline.settings_record import record_well_settings

    output_dir = Path(output_dir)
    owned = scratch is None
    if owned:
        scratch = scratch_base_for(output_dir) / f"_tmp_extract_{well_label}"
        staging = _staging_dir_for(output_dir, well_label, settings_key)
        for stale in (scratch,) + ((staging,) if not any(staging.glob(".fov_*.done")) else ()):
            if stale.exists():
                remove_directory(stale)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        records, failed = process_well_zip(
            well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
            schema, sep, grouper=grouper, parse=parse, extract=extract_zip, scratch_dir=scratch,
            write_csv=write_well_csv, segmenter=segmenter, incremental=incremental,
            prior_dir=incremental_prior_dir, settings_key=settings_key, staging_dir=staging, plate_rigid=plate_rigid)
        grid_path = output_dir / NANOWELL_DIR / f"{well_label}_grid.json"
        if not grid_path.is_file():
            return records, failed
        grid = json.loads(grid_path.read_text(encoding="utf-8"))
        total = len(grid["timepoints"])
        write_well_report(output_dir, well_label, groups_total=total,
                          groups_covered=sum(1 for b in grid["timepoints"].values() if b.get("status") == nr.OK),
                          failed=failed)
        record_well_settings(output_dir, well_label, settings_key)
        # "— done." exactly as a field-mode well says it: the remote Fetcher and Analyze's progress count a well
        # finished by that marker (``services/remote_wire._RE_WELL_DONE``), and a variant fetched nothing in-stream.
        # A re-anchoring re-run says something else: the well finished once already, and a second "done" read as a
        # repeated completion (roll-up review X31). Its results are pulled at verification from the grid records.
        if reanchoring:
            log.info("Well %s — re-anchored. %d timepoint(s), %d without rows.", well_label, total, len(failed))
        else:
            log.info("Well %s — done. %d timepoint(s), %d without rows.", well_label, total, len(failed))
        return records, failed
    finally:
        if owned:
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
                                          "address": "axial (q, r) on the lattice the grid record's lattice_kind "
                                                     "names (hex or square); (0, 0) is the central square, or "
                                                     "the array's centre"}}}
    try:
        record = read_record_file(Path(input_dir) / "acquisition.json")
    except Exception as exc:                        # an unreadable record fails the wells, not the sidecar
        block["nanowell"]["acquisition_unreadable"] = f"{type(exc).__name__}: {exc}"
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


def _tp_rank(token: str):
    from util.timepoint import parse_hours

    hours = parse_hours(token)
    return (0, hours, token) if hours is not None else (1, 0.0, token)


def _read_grids(output_dir: Path, settings: dict | None = None) -> dict[str, dict]:
    """Every readable grid record under ``nanowells/``, only those made under *settings* when they are given.

    The plate's motion is fitted from the wells' origins, so a well analysed under other settings (another
    plate format, say) must not take part in it.
    """
    grids = {}
    for path in sorted((Path(output_dir) / NANOWELL_DIR).glob("*_grid.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("scheme_version") != SCHEME_VERSION:
            continue
        if settings is not None and payload.get("settings") != settings:
            continue
        grids[payload["well"]] = payload
    return grids


def _origin(grids: dict, well: str, timepoint: str) -> tuple | None:
    block = grids.get(well, {}).get("timepoints", {}).get(timepoint, {})
    if block.get("status") == nr.OK and block.get("origin_method") in _INDEPENDENT and block.get("origin_stage_um"):
        return tuple(block["origin_stage_um"])
    return None


def plates_of(grids: dict[str, dict]) -> dict:
    """``plate -> {well: grid}``: the wells of each physical plate (one, unless Merge Plates combined two)."""
    plates: dict = {}
    for well, grid in grids.items():
        plates.setdefault(grid.get("plate") or "", {})[well] = grid
    return plates


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
            reference = next((r for r in sorted(grid["timepoints"], key=_tp_rank)
                              if r != tp and _origin(grids, well, r)), None)
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
    here = settings_from_kwargs(shared_kwargs_template).key()
    grids = _read_grids(output_dir, here)
    # One rigid motion per physical plate: a dataset Merge Plates made holds two, whose stage frames and motions
    # are unrelated, so each well's grid record names its plate and the fit never mixes them.
    predictions, reasons = {}, {}
    for members in plates_of(grids).values():
        found, why = plan_reanchor(members)
        predictions.update(found)
        reasons.update(why)
    zips = dict(well_zips)
    for well, by_tp in predictions.items():
        if well not in zips:
            reasons.update({(well, tp): "this well is not part of this run, so it was not re-run" for tp in by_tp})
            continue
        log.info("Well %s — re-anchoring %s from the plate's other wells.", well, ", ".join(sorted(by_tp)))
        try:
            run_zip_task(well, zips[well], output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                         csv_prefix, schema, sep, settings_key, grouper=grouper, parse=parse, segmenter=segmenter,
                         incremental=True, incremental_prior_dir=output_dir, plate_rigid=by_tp, reanchoring=True)
        except Exception as exc:          # one well's re-run must not cost the rest, or the finished run's records
            log.error("Well %s — re-anchoring failed (%s: %s); its timepoints stay unaddressed.", well,
                      type(exc).__name__, exc, exc_info=True)
            reasons.update({(well, tp): f"the re-run failed: {type(exc).__name__}: {exc}" for tp in by_tp})
    after = _read_grids(output_dir, here)
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
    # The wells whose grid records the plate fit read: every well in the output folder, which in a batched remote
    # run is this batch's and every earlier batch's. Only this run's wells can be re-run (X19, X32).
    return {"anchored": sorted(anchored), "remaining": remaining, "wells_considered": sorted(grids)}


def nanowell_endgame(options, output_dir, well_zips, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
                     schema, sep, settings_key, *, grouper, parse) -> None:
    """What nanowell mode does after every well has run; a no-op when the mode is off (the caller does not branch)."""
    if not options:
        return
    try:
        summary = reanchor_plate(output_dir, well_zips, shared_kwargs_template, nuclear_token, fluor_tokens,
                                 csv_prefix, schema, sep, settings_key, grouper=grouper, parse=parse)
    except Exception as exc:               # the run is finished; a pass that cannot describe the plate says so
        log.error("Re-anchoring could not run (%s: %s).", type(exc).__name__, exc, exc_info=True)
        summary = {"anchored": [], "remaining": {}, "error": f"{type(exc).__name__}: {exc}"}
    if summary["anchored"] or summary["remaining"]:
        log.info("Re-anchoring: %d (well, timepoint) anchored from the plate, %d still unaddressed; the plate fit "
                 "read the grid records of %d well(s) in the output folder.", len(summary["anchored"]),
                 len(summary["remaining"]), len(summary.get("wells_considered", ())))
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
