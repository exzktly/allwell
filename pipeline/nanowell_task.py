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
import os
import logging
import time
import zipfile
from functools import partial
from pathlib import Path
from types import MappingProxyType

import numpy as np

from pipeline import nanowell_measure as nm
from pipeline import nanowell_reference as nref
from pipeline import nanowell_run as nr
from pipeline.run_report import withdraw_seal
from pipeline.nanowell_tiles import WellTiles, read_native
from util.csv_gz import existing_csv
from util.timepoint import parse_hours

log = logging.getLogger("allwell.nanowell")

NANOWELL_DIR = "nanowells"
#: The well's earliest empty-nanowell template, kept beside its tables for later incremental runs (tracker C16).
TEMPLATE_SUFFIX = "_template.npz"
#: Version of the on-disk scheme (columns, conventions) so Review can refuse one it does not understand.
SCHEME_VERSION = 1

#: The columns of ``nanowells/<well>.csv`` in the order they are written (§3.7). Per-channel site
#: intensities follow, as ``<token>_integrated`` and ``<token>_mean_bg``. ``level_offset`` / ``level_gain`` are the
#: ``(offset, gain)`` a site's pixels were levelled by before the template division; ``segmenter_floor`` /
#: ``segmenter_contrast`` are its interior's median and 1st-99th percentile spread on the 0-1 scale the segmenter
#: normalises its tile to (``nanowell_measure.segmenter_view``). All four are blank for a site not measured.
SITE_COLUMNS = ("timepoint", "nw_q", "nw_r", "nw_ring", "well_x_um", "well_y_um", "owner", "radius_um",
                "geometry", "detected", "fused", "imaged_fraction", "visible_fraction", "state",
                "partially_visible", "focus", "n_cells", "confluence", "area_est_cells", "count_disagreement",
                "level_offset", "level_gain", "segmenter_floor", "segmenter_contrast", "consistency")


class NanowellInputError(RuntimeError):
    """The inputs cannot be run in nanowell mode (no acquisition record, no tiles): named, never silent."""


def reference_well_first(shared_kwargs_template: dict, items) -> tuple[list, list]:
    """``(the empty reference well's item, the rest)`` of a run's ``(well, archive)`` *items*, for the reference to
    be measured before the wells read against it (``pipeline.nanowell_reference``); ``([], items)`` outside
    nanowell mode, with none named, or with it not among *items*."""
    if not shared_kwargs_template.get("_nanowell"):
        return [], list(items)
    return nref.reference_well_first(settings_from_kwargs(shared_kwargs_template), list(items))


def settings_from_kwargs(kwargs: dict) -> nr.NanowellSettings:
    spec = kwargs["_nanowell"]
    custom = (float(spec["pitch_um"]), float(spec["diameter_um"])) if spec["plate_format"] == nr.CUSTOM_FORMAT \
        else None
    return nr.NanowellSettings(spec["plate_format"], spec.get("margin_um"), spec.get("cell_diameter_um", 15.0),
                               spec.get("square_edge_deg", 0.0), spec.get("occluded_below", 0.5),
                               spec.get("clipped_below", 0.5), custom,
                               spec.get("geometry") or nr.GEOMETRY_HEX_CIRCLE,
                               spec.get("cellpose_model") or nr.LEGACY_CELLPOSE_MODEL,
                               spec.get("segment_on") or nr.LEGACY_SEGMENT_ON,
                               spec.get("segment_scope") or nr.LEGACY_SEGMENT_SCOPE,
                               spec.get("segment_scale") or nr.LEGACY_SEGMENT_SCALE,
                               spec.get("resize") or nr.LEGACY_RESIZE, spec.get("empty_well") or None)


def _prior_array(prior: dict | None) -> tuple[float | None, list | None]:
    """``(radius, roster)`` an earlier run of the well shared across its timepoints, for an incremental run to keep:
    a circle's radius, or an inferred array's addresses (tracker X35); ``(None, None)`` with no earlier run."""
    if prior is None:
        return None, None
    block = prior["grid"].get("array_radius", {})
    return block.get("radius_um"), (block.get("roster") or None)      # empty: the earlier run anchored nothing


def _run_with_prior(well_label, record, todo, prior, load_phase, settings, segmenter, channels, plate_rigid,
                    template_path, staging_dir, settings_key, scratch_dir, expected_shape, stored_placements=None,
                    manual_anchors=MappingProxyType({}), release=None, confluence_reference=None):
    """``run_well`` over *todo* on what an earlier run kept (*prior*, or ``None``): ``(outcome, fixed radius,
    fixed roster, kept template)``. *expected_shape* is a described grid's shape: a well short of a whole row or
    column is incomplete, which a record's positions alone could not show. *confluence_reference* is what the well's
    confluence is read against (:func:`_confluence_reference_for` when not given): the one its kept timepoints were
    checked against, so the timepoints it measures are read against the same."""
    if confluence_reference is None:
        confluence_reference = _confluence_reference_for(well_label, settings, template_path)
    fixed, fixed_roster = _prior_array(prior)
    saved = nm.load_template(template_path) if prior else None
    outcome = nr.run_well(well_label, record, todo, load_phase, settings, segmenter=segmenter, channels=channels,
                          fixed_radius_um=fixed, fixed_roster=fixed_roster, plate_rigid=plate_rigid,
                          reference=_reference_of(prior), kept_evidence=_kept_evidence(prior),
                          kept_layouts=_kept_layouts(prior),
                          checkpoint=None if staging_dir is None else TimepointCheckpoints(staging_dir, settings_key),
                          earliest_template=None if saved is None else saved[0],
                          spill_dir=Path(scratch_dir) / ".montages",     # removed with the scratch (X23)
                          expected_shape=expected_shape, stored_placements=stored_placements,
                          manual_anchors=manual_anchors, progress=lambda done, total: _log_steps(well_label, done, total),
                          site_progress=lambda tp, done, total: _log_sites(well_label, tp, done, total),
                          note=lambda text: log.info("Well %s — %s", well_label, text), release=release,
                          confluence_reference=confluence_reference)
    return outcome, fixed, fixed_roster, saved


def _confluence_reference_for(well_label, settings, template_path):
    """What *well_label*'s confluence is read against: itself when it is the empty reference well, else the
    reference saved in this output folder (``pipeline.nanowell_reference.load``), else nothing, with the reason."""
    if nref.same_well(well_label, nref.reference_well(settings)):
        return nref.OWN
    return nref.load(Path(template_path).parent.parent, settings)


def confluence_reference_current(output_dir, well_label, shared_kwargs_template) -> bool:
    """Whether a finished *well_label* in *output_dir* read its confluence against the empty reference as it is saved
    there now, at every timepoint it measured (``nanowell_reference.KEY_FIELD`` against ``Reference.key_at``); a
    well that did not may not be skipped as up to date (review tracker M7). True outside nanowell mode, with no
    reference named (naming one changes the settings key, which decides that), and for a well with no grid record,
    which the run's other checks decide. A timepoint recorded before the key existed has none, and is measured
    again."""
    if not shared_kwargs_template.get("_nanowell"):
        return True
    path = Path(output_dir) / NANOWELL_DIR / f"{well_label}_grid.json"
    try:
        grid = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    settings = settings_from_kwargs(shared_kwargs_template)
    if nref.reference_well(settings) is None:
        return True                 # nothing to be read against; naming one changes the settings key
    reference = _confluence_reference_for(well_label, settings, path)
    stale = [tp for tp, block in (grid.get("timepoints") or {}).items()
             if isinstance(block, dict) and block.get("status") == nr.OK
             and block.get(nref.KEY_FIELD) != reference.key_at(tp)]
    if stale:
        log.info("Well %s — its confluence at %s was read against another empty reference than the one saved now; "
                 "it is analysed again.", well_label, ", ".join(stale))
    return not stale


def _log_steps(well_label: str, done: int, total: int) -> None:
    """The well's progress, in the two lines Analyze's run reader turns into the well's dial on the plate
    (``services.pipeline_runner``: ``_RE_WELL_FOV_TOTAL`` / ``_RE_WELL_FOV_DONE``). A nanowell well is processed a
    timepoint at a time, never a field at a time, so without them it printed nothing the dial could follow."""
    if done == 0:
        # Two steps a timepoint, so say how many timepoints that is: "4 steps" read as four timepoints on a plate
        # with two. The count stays first, where the run reader's _RE_WELL_FOV_TOTAL looks for it.
        log.info("Well %s — %d step(s): its %d timepoint(s), each located, then measured", well_label, total,
                 total // 2)
    else:
        log.info("Well %s — step %d/%d done", well_label, done, total)


#: When each (well, timepoint)'s count started, by this process's clock, and Cellpose's own seconds then
#: (``pipeline.cellpose.CELLPOSE_SECONDS``): what :func:`_log_sites` times the rate and its breakdown from.
_SITES_STARTED: dict = {}


def _cellpose_seconds() -> dict:
    """Cellpose's own seconds so far in this process, or zeros where the module is not shipped (a feature-subset
    build): the count then reports its rate without the breakdown."""
    try:
        from pipeline.cellpose import CELLPOSE_SECONDS
    except ImportError:
        return {"network": 0.0, "masks": 0.0, "timed": 0.0}
    return dict(CELLPOSE_SECONDS)


def _log_sites(well_label: str, timepoint, done: int, total: int, *, clock=time.monotonic) -> None:
    """How many of a timepoint's nanowells are segmented so far, while it is measured: the measure step's own
    progress, which the run reader adds to the well's dial and names in its hover text
    (``services.pipeline_runner._RE_WELL_SITES``). After the first line each says how long a nanowell is taking and
    about how long the timepoint has left, so a slow count reads as slow rather than stuck, and, where Cellpose
    reported its own times, how much of a nanowell's time was its network and how much its masks — the rest is the
    pipeline's own measuring and Cellpose's resizing."""
    now = clock()
    if done == 0:
        _SITES_STARTED[(well_label, timepoint)] = (now, _cellpose_seconds())
        log.info("Well %s — %s: %d/%d nanowells segmented", well_label, timepoint, done, total)
        return
    started, cellpose_then = _SITES_STARTED.get((well_label, timepoint), (now, _cellpose_seconds()))
    elapsed = now - started
    each = elapsed / done
    timing = (f"in {_duration(elapsed)}, {each:.1f} s each" if done >= total
              else f"{each:.1f} s each, about {_duration(each * (total - done))} left")
    spent = {k: v - cellpose_then.get(k, 0.0) for k, v in _cellpose_seconds().items()}
    timed = spent.get("timed", 0.0)
    # Divided by the nanowells Cellpose timed, not by every one segmented: it times only a call of several images,
    # so a lone crop's call (a tile's last) or a whole-tile call adds nothing and read as a cheaper nanowell.
    if timed > 0 and (spent["network"] > 0 or spent["masks"] > 0):
        timing += (f"; per nanowell, Cellpose's network {spent['network'] / timed:.2f} s, "
                   f"its masks {spent['masks'] / timed:.2f} s")
    log.info("Well %s — %s: %d/%d nanowells segmented (%s)", well_label, timepoint, done, total, timing)


def _duration(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def stored_placements(folders, well_label: str) -> dict:
    """``timepoint -> [(folder, grid block), ...]`` from each of *folders*' ``nanowells/<well>_grid.json``, in order.

    These are only candidates: ``nanowell_run.stored_placement`` takes one only when it was registered from exactly
    this run's inputs (its ``registration_key``), so a folder of another plate, other images or another algorithm
    contributes nothing. A folder named twice, or without a readable grid record for the well, is skipped.
    """
    out: dict = {}
    seen = set()
    for folder in folders:
        if folder is None:
            continue
        folder = Path(folder)
        resolved = folder.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        path = folder / NANOWELL_DIR / f"{well_label}_grid.json"
        try:
            grid = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            log.warning("Well %s — the grid record %s could not be read, so its registration is not reused (%s).",
                        well_label, path, exc)
            continue
        for tp, block in (grid.get("timepoints") or {}).items() if isinstance(grid, dict) else ():
            if isinstance(block, dict):
                out.setdefault(tp, []).append((folder, block))
    return out


def _log_reused_registration(well_label: str, outcome: nr.WellOutcome, todo) -> None:
    """One line per well saying which of its timepoints took an earlier run's registration, and from where."""
    reused: dict = {}
    for tp in todo:
        folder = ((outcome.timepoints.get(tp) or {}).get("registration") or {}).get("reused_from")
        if folder:
            reused.setdefault(folder, []).append(tp)
    for folder, tps in reused.items():
        log.info("Well %s — registration reused from %s for %d of %d timepoint(s): %s.",
                 well_label, folder, len(tps), len(todo), ", ".join(tps))


def _kept_evidence(prior: dict | None) -> dict | None:
    """What an earlier run's kept rows show about a default well's rim (tracker X35a-R1): the sites where a nanowell
    was detected at some kept timepoint, and those whose whole disk a kept timepoint imaged. ``None`` without an
    earlier run, or for a well its run kept no roster for (the disk rule, or an inferred well's own)."""
    if prior is None or prior["grid"].get("array_radius", {}).get("roster_rule") != "evidence":
        return None
    detected, imaged = set(), set()
    for row in prior["sites"]:
        # A kept timepoint flagged a roster mismatch may be anchored a step off (review X35a-R2): its rows' addresses
        # name its neighbours' sites, so what they say was detected or imaged is said of the wrong nanowells.
        if not str(row.get("nw_q") or "").strip() or row.get("consistency") == "roster_mismatch":
            continue
        address = (int(float(row["nw_q"])), int(float(row["nw_r"])))
        if str(row.get("detected")).strip().lower() == "true":
            detected.add(address)
        fraction = str(row.get("imaged_fraction") or "").strip()      # blank: nothing recorded about imaging
        if fraction and float(fraction) >= 0.999:
            imaged.add(address)
    # How each kept timepoint was anchored: a new timepoint a lattice step off them is moved onto their frame only
    # when they outrank it (nanowell_run._settle_steps), so a new square never yields to a kept array centre.
    blocks = prior["grid"].get("timepoints", {})
    origins = {tp: blocks.get(tp, {}).get("origin_method") for tp in sorted(prior.get("kept", ()), key=_tp_rank)}
    return {"detected": detected, "imaged": imaged, "origins": origins}


def timepoint_order(record: dict, well: str) -> list[str]:
    """This well's timepoints in recorded time (hours when the key reads as one, else the key itself)."""
    from util.timepoint import parse_hours

    found = sorted({t["timepoint"] for t in record["tiles"] if t["well"] == well})

    def key(tp):
        hours = parse_hours(tp)
        return (0, hours, tp) if hours is not None else (1, 0.0, tp)

    return sorted(found, key=key)


def _read_tif(path):
    """A tile as float32, from a path or from an open binary file (a member read out of the well's archive in
    place, :class:`pipeline.zip_members.ZipMemberReader`)."""
    import tifffile

    return np.asarray(tifffile.imread(path if hasattr(path, "read") else str(path)), dtype=np.float32)


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


def timepoint_hours(token) -> float:
    """A timepoint token in hours, as the per-field path writes ``timepoint_hours``.

    The same rule as ``process_microscopy.parse_filename``: an empty token is ``0.0`` (a single-timepoint
    plate whose filenames carry none), a token :func:`util.timepoint.parse_hours` cannot read is ``nan``, and
    Review then falls back to the ``timepoint`` label for that row. Without this column every Review view that
    selects cells by ``timepoint_hours`` — the per-cell scatter among them — found no cell on a nanowell plate,
    while the views that parse the label went on drawing.
    """
    text = "" if token is None else str(token)
    if not text.strip():
        return 0.0
    hours = parse_hours(text)
    return float("nan") if hours is None else float(hours)


def with_timepoint_hours(row: dict) -> dict:
    """*row* with ``timepoint_hours`` beside ``timepoint``, derived from it when the row has none.

    An incremental run writes rows read back from the previous run's CSV beside this run's, in one table whose
    header is taken from its first row (``pipeline/io._write_single_csv``); a kept row from a CSV written before
    the column existed would otherwise either lack it or, ahead of a new row, make the writer refuse the new one.
    """
    if row.get("timepoint_hours") not in (None, ""):
        return row
    out = {}
    for key, value in row.items():
        if key == "timepoint_hours":
            continue
        out[key] = value
        if key == "timepoint":
            out["timepoint_hours"] = timepoint_hours(value)
    if "timepoint_hours" not in out:
        out["timepoint_hours"] = timepoint_hours(row.get("timepoint"))
    return out


def cell_records(outcome: nr.WellOutcome, well: str, fluor_tokens, px_by_tp: dict) -> list[dict]:
    """The per-cell rows the existing CSV schema carries, plus the ``nw_*`` columns (§3.7).

    ``fov`` is the owner tile. The new columns exist only in nanowell mode. Intensity columns are the
    cell's mean on each fluorescence channel, named as the existing quantification names them.
    """
    cells = outcome.cells
    n = len(cells.get("label", ()))
    # Each column is looked up once per well, not once per cell: the lookup used to pass ``[None] * n`` as the
    # default, which Python builds on every call whether or not the column exists, so a well of a million cells
    # built a million-long list for every cell and channel — hours after "step N/N", before the CSV was opened.
    means = [(f"{token}_mean_intensity", cells.get(f"mean_{token}")) for token in fluor_tokens]
    hours = {tp: timepoint_hours(tp) for tp in set(cells.get("timepoint", ()))}
    rows = []
    for k in range(n):
        tp = cells["timepoint"][k]
        row = {"well": well, "fov": cells["fov"][k], "timepoint": tp, "timepoint_hours": hours[tp],
               "nucleus_id": cells["label"][k],
               "area_px": cells["area_um2"][k] / px_by_tp[tp] ** 2}
        for column, values in means:
            row[column] = None if values is None else values[k]
        for name in ("nw_q", "nw_r", "nw_ring", "nw_label", "well_x_um", "well_y_um"):
            row[name] = cells[name][k]
        rows.append(row)
    return rows


def cell_columns(fluor_tokens) -> list[str]:
    """The per-cell CSV's columns, in the order :func:`cell_records` writes them."""
    return (["well", "fov", "timepoint", "timepoint_hours", "nucleus_id", "area_px"] + [f"{t}_mean_intensity" for t in fluor_tokens]
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
    payload = {"scheme_version": SCHEME_VERSION, "measure_version": nm.MEASURE_VERSION, "well": well,
               "settings": settings.key(),
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


def _prior_state(prior_dir, well_label, csv_prefix, settings_key, timepoints, record,
                 manual_anchors=None, anchors_digest: str = "", reference_key=None) -> dict | None:
    """What an earlier run of this well left, if it is still valid for this one: ``(grid, site rows, cell rows)``.

    Valid means: the settings that produced it are this run's (the same check the per-field path makes),
    the scheme version is understood, and each timepoint kept is ``ok`` with the record digest unchanged
    since, and the anchor set by hand for it (*manual_anchors*, ``timepoint -> position``) the one it was located
    with: a timepoint anchored by its fiducial, or by an earlier click, is recomputed when a click is set or moved.
    A timepoint the end-of-run pass anchored from the rest of the plate (``plate_rigid``) is kept only under the
    plate's anchors as they were then (*anchors_digest*): its origin was carried from another timepoint, or other
    wells, any of which a moved or cleared click may have changed. With *reference_key* (``timepoint -> key``,
    ``nanowell_reference.Reference.key_at``) a timepoint is kept only when its confluence was read against the
    empty reference as it is saved now: one measured while the reference was missing or since re-exported is
    measured again (review tracker M7).
    Anything else is recomputed, never reused. ``None`` when nothing can be reused.
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
    if grid.get("measure_version") != nm.MEASURE_VERSION:
        # Measured another way: kept beside this run's timepoints, the two would not be comparable numbers.
        return None
    if _has_a_step_off_timepoint(grid):
        return None
    kept = {tp for tp in timepoints
            if grid.get("timepoints", {}).get(tp, {}).get("status") == nr.OK
            and grid["timepoints"][tp].get("record_digest") == nr.record_digest(record, well_label, tp)
            and grid["timepoints"][tp].get("manual_anchor_um") == _anchor_list((manual_anchors or {}).get(tp))
            and (grid["timepoints"][tp].get("origin_method") != "plate_rigid"
                 or grid["timepoints"][tp].get("anchors_digest", "") == anchors_digest)
            and (reference_key is None or grid["timepoints"][tp].get(nref.KEY_FIELD) == reference_key(tp))}
    if not kept:
        return None
    sites_path = Path(prior_dir) / NANOWELL_DIR / f"{well_label}.csv"
    # A finished well's CSV is gzipped (``write_well_csv``): either spelling, plain winning (``util/csv_gz``).
    cells_path = existing_csv(Path(prior_dir) / f"{csv_prefix}_{well_label}.csv")
    sites = [r for r in _read_rows(sites_path) if r["timepoint"] in kept]
    cells = [with_timepoint_hours(r) for r in _read_rows(cells_path) if r["timepoint"] in kept]
    return {"grid": grid, "kept": kept, "sites": sites, "cells": cells,
            "order": {tp: k for k, tp in enumerate(timepoints)}}


def _has_a_step_off_timepoint(grid: dict) -> bool:
    """Whether a circular well's record holds a timepoint whose addresses disagree with its other timepoints'
    (``roster_mismatch``). Every timepoint of such a well is measured on one roster, so the mismatch means one
    timepoint's origin sat a lattice step off its others' — and the run cannot tell from the record alone whether the
    flagged timepoint is the wrong one or the right one outvoted (review: a square-anchored baseline was blamed when
    two later timepoints shared one wrong array centre). Reusing any of it would carry rows under neighbouring
    nanowells' addresses into every later run, so the well is analysed again over every timepoint, where
    ``nanowell_run._settle_steps`` decides the frame by source. An inferred well (no radius) is left alone: its
    timepoints each see their own array, and a mismatch there is what one of them saw."""
    if grid.get("array_radius", {}).get("radius_um") is None:
        return False
    return any(isinstance(block, dict) and block.get("consistency") == "roster_mismatch"
               for block in grid.get("timepoints", {}).values())


def _anchor_list(position) -> list | None:
    """An anchor as the grid block records it (``locate_timepoint``), so a stored one compares equal."""
    return None if position is None else [float(position[0]), float(position[1])]


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


def _kept_layouts(prior: dict | None) -> dict | None:
    """Each kept timepoint's registered tile positions (``{tile: (left, top)}``), for the new timepoints' tile layout
    to be checked against (``nanowell_run._agree_tile_layouts``); ``None`` without an earlier run."""
    if prior is None:
        return None
    out = {}
    for tp in prior["kept"]:
        tiles = (prior["grid"]["timepoints"].get(tp) or {}).get("tiles") or ()
        try:
            out[tp] = {t["tile"]: (float(t["left_um"]), float(t["top_um"])) for t in tiles}
        except (KeyError, TypeError, ValueError):
            continue                                   # a block written by hand or by an older run: no layout
    return out


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
                    "diameter_used_um": (block.get("geometry") or {}).get("diameter_used_um"),
                    "diameter_source": (block.get("geometry") or {}).get("diameter_source")}
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


def _reanalysis_reason(kept_radius: dict, radius: dict) -> str:
    """Why a well was analysed again over every timepoint, measured on the re-analysis's own roster.

    The count compares the roster the earlier run kept with the one the full re-analysis settled on, not with the
    incremental pass's estimate, so the grid record states what the rewrite actually changed (tracker X35a-R6).
    """
    kept = {tuple(map(int, a)) for a in kept_radius.get("roster") or ()}
    now = {tuple(map(int, a)) for a in radius.get("roster") or ()}
    return (f"a new timepoint changed the well's roster ({len(now - kept)} nanowell(s) added, "
            f"{len(kept - now)} removed), so every timepoint was analysed again and the earlier timepoints' rows "
            "rewritten")


def _well_manual_anchors(input_dir: Path, well_label: str) -> tuple[dict, str]:
    """``(timepoint -> position, plate digest)``: the anchors set by hand for this well (``util.nanowell_anchors``),
    and a digest of every well's, ``""`` without any — what a timepoint carried from the rest of the plate depends on.

    An anchors file that cannot be used fails the well with the file's problem stated, rather than running it as
    if no anchor had been set: the wells a person anchored would come out unaddressed with nothing to say why.
    """
    import hashlib

    from util.nanowell_anchors import AnchorsFileError, anchors_path, anchors_payload, read_anchors, well_anchors

    try:
        anchors = read_anchors(anchors_path(input_dir))
    except AnchorsFileError as exc:
        raise NanowellInputError(f"The nanowell anchors cannot be used: {exc}") from None
    found = well_anchors(anchors, well_label)
    if found:
        log.info("Well %s — anchor set by hand at %s.", well_label, ", ".join(sorted(found, key=_tp_rank)))
    digest = (hashlib.sha256(json.dumps(anchors_payload(anchors), sort_keys=True).encode()).hexdigest()[:16]
              if anchors else "")
    return found, digest


def _stamp_anchors(outcome, todo, digest: str) -> None:
    """Record on each timepoint this run located the plate's anchors as they were (``_prior_state`` keeps a
    ``plate_rigid`` timepoint only under the same digest)."""
    for tp in todo:
        if tp in outcome.timepoints:
            outcome.timepoints[tp]["anchors_digest"] = digest


def process_well_zip(well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                     csv_prefix, schema, sep, *, grouper, parse, scratch_dir, write_csv, segmenter=None,
                     incremental=False, prior_dir=None, settings_key="", staging_dir=None, plate_rigid=None):
    """Run one zipped well in nanowell mode; returns ``(records, failed_timepoints)`` like its counterpart.

    *grouper*, *parse*, *write_csv* are ``process_microscopy``'s ``find_image_groups_in_zip``,
    ``parse_filename`` and ``_write_single_csv``. The tiles are never unpacked: each is read out of the archive
    in place when a timepoint loads it (:class:`pipeline.zip_members.ZipMemberReader`, through
    :class:`pipeline.nanowell_tiles.WellTiles`, which keeps it until its timepoint is measured). Unpacking the whole
    well first — every tile of every timepoint, phase and fluorescence — held a remote batch silent for about ten
    minutes after ``Found N image group(s)`` on a cluster whose scratch is the networked home directory.
    *scratch_dir* now holds only the located montages (``.montages``) and the caller removes it. With
    *incremental*, timepoints an earlier run finished under the same settings and record are kept and only the
    rest are computed, onto the same roster.
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
    groups, _needed = grouper(zip_path, nuclear_token, fluor_tokens, scratch_dir, schema=schema, sep=sep)
    if not groups:
        raise NanowellInputError(f"No image groups found in {Path(zip_path).name}: "
                                 f"{no_groups_reason(zip_path, nuclear_token, fluor_tokens, schema, sep)}")
    index = _tile_index(groups, parse, schema, sep)
    described = record is None
    if described:
        record = _pattern_record(well_label, zip_path, index, pattern)
    timepoints = timepoint_order(record, well_label)
    output_dir = Path(output_dir)
    manual, digest = _well_manual_anchors(Path(zip_path).parent, well_label)
    # Read once: the kept timepoints are checked against it, the measured ones read against it, and each records
    # its key (``nanowell_reference.KEY_FIELD``), so all three are the same reference.
    reference = _confluence_reference_for(well_label, settings,
                                          Path(prior_dir or output_dir) / NANOWELL_DIR / f"{well_label}_grid.json")
    keyed = nref.reference_well(settings) is not None     # with none named, the settings key already says so
    prior = (_prior_state(prior_dir or output_dir, well_label, csv_prefix, settings_key, timepoints, record, manual,
                          digest, reference_key=reference.key_at if keyed else None) if incremental else None)
    todo = [tp for tp in timepoints if prior is None or tp not in prior["kept"]]
    if not todo:
        log.info("Well %s — up to date (no new timepoints).", well_label)
        return [], []

    # Read now, before this run rewrites the output folder's grid record: a folder named on the form first, then the
    # output folder itself, so re-running a plate in place with other segmentation settings keeps its registration.
    placements = stored_placements((shared_kwargs_template["_nanowell"].get("registration_from"), output_dir),
                                   well_label)

    def run(prior, todo):
        return _run_with_prior(well_label, record, todo, prior, load_phase, settings, segmenter, channels, plate_rigid,
                               Path(prior_dir or output_dir) / NANOWELL_DIR / template_path.name, staging_dir,
                               settings_key, scratch_dir, tuple(pattern["grid"]) if described else None, placements,
                               manual, tiles.release, reference)

    def load_phase(tp, tile) -> np.ndarray | None:
        found = index.get((tp, tile))
        return None if found is None else tiles.tile((tp, Path(found[0]).name), found[0], read_native)

    def channel_loader(position):
        def load(tp, tile) -> np.ndarray | None:
            found = index.get((tp, tile))
            if found is None or position >= len(found[1]):
                return None
            return tiles.tile((tp, Path(found[1][position]).name), found[1][position], read_native)
        return load

    channels = {t: channel_loader(k) for k, t in enumerate(fluor_tokens)}
    template_path = output_dir / NANOWELL_DIR / f"{well_label}{TEMPLATE_SUFFIX}"
    # Every tile is read once and kept until its timepoint is measured, as far as memory allows: a phase tile is read
    # to locate its timepoint and again to measure it, a fluorescence tile for its floor level and its intensities.
    # Sized by the timepoints this run reads: an incremental run that adds one timepoint to a long well estimates one.
    wanted = set(todo)
    tiles = WellTiles(zip_path, well_label, [name for (tp, _fov), (nuclear, fluors) in index.items() if tp in wanted
                                             for name in (nuclear, *fluors)])
    try:
        return _finish_well(well_label, output_dir, fluor_tokens, csv_prefix, settings, record, timepoints,
                            digest, prior, todo, run, staging_dir, write_csv, template_path,
                            reference if keyed else None)
    finally:
        log.info("Well %s — %s.", well_label, tiles.summary())
        tiles.close()


def _save_confluence_reference(output_dir, well_label, settings, outcome, sites, timepoints) -> None:
    """The empty reference well's part, once its counts are known: judge each timepoint empty or not by them, record
    the verdict on its blocks (and blank its own confluence where it was not empty), and save its templates for the
    wells measured after it (``pipeline.nanowell_reference``). A kept timepoint of an incremental run brings no
    template of its own, so the one saved before is carried over."""
    templates = {**nref.kept_templates(output_dir, well_label), **outcome.templates}
    rows: dict = {}
    for row in sites:
        rows.setdefault(row["timepoint"], []).append(row)
    verdicts = {tp: nref.verdict(rows[tp]) for tp in timepoints if tp in rows}
    nref.apply(sites, outcome.timepoints, well_label, verdicts)
    nref.save(output_dir, well_label, settings, {tp: (templates.get(tp), v) for tp, v in verdicts.items()})
    refused = {tp: v["status"] for tp, v in verdicts.items() if v["status"] != nref.EMPTY}
    if refused:
        log.warning("Well %s is the empty reference well, but it is not empty at %s: no well has a confluence there",
                    well_label, ", ".join(f"{tp} ({status})" for tp, status in refused.items()))
    log.info("Well %s — empty reference saved for %d timepoint(s); every other well's confluence is read against it.",
             well_label, len(verdicts) - len(refused))


def _finish_well(well_label, output_dir, fluor_tokens, csv_prefix, settings, record, timepoints, digest,
                 prior, todo, run, staging_dir, write_csv, template_path, reference=None):
    """:func:`process_well_zip`'s run and its writing, while the well's archive is open to read tiles from.
    *reference* is what the measured timepoints' confluence was read against; each records its key."""
    outcome, fixed, fixed_roster, saved = run(prior, todo)
    if prior is not None and (outcome.roster_change or outcome.frame_change):
        # An incremental run must give the roster a run over every timepoint would (tracker X35a-R1): a rim nanowell
        # hidden at the kept timepoints and seen at a new one belongs to all of them. Likewise a new timepoint whose
        # nanowells sit a lattice step from the kept ones': which frame is right is decided with every timepoint in
        # view (nanowell_run._settle_steps). So the well is analysed again from the start, and its grid record says why.
        kept_radius, frame_change = prior["grid"]["array_radius"], outcome.frame_change
        prior, todo = None, list(timepoints)
        outcome, fixed, fixed_roster, saved = run(prior, todo)
        reason = (f"{frame_change}, so every timepoint was analysed again and the earlier timepoints' rows rewritten"
                  if frame_change else _reanalysis_reason(kept_radius, outcome.radius))
        outcome.radius["reanalysed_because"] = reason
        log.warning("Well %s — %s.", well_label, reason)
    _log_reused_registration(well_label, outcome, todo)
    _stamp_anchors(outcome, todo, digest)
    if reference is not None:
        for tp in todo:
            if isinstance(outcome.timepoints.get(tp), dict):
                outcome.timepoints[tp][nref.KEY_FIELD] = reference.key_at(tp)
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
    if nref.same_well(well_label, nref.reference_well(settings)):
        _save_confluence_reference(output_dir, well_label, settings, outcome, all_sites, timepoints)
    all_cells = sorted((prior["cells"] if prior else []) + records, key=lambda r: rank[r["timepoint"]])
    withdraw_seal(output_dir, well_label)         # the tables and archive are rewritten from here
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
    from pipeline.nanowell_inspect import SUFFIX, write_sheet

    write_sheet(output_dir / NANOWELL_DIR / f"{well_label}{SUFFIX}", outcome.inspection)
    rank_of = {tp: k for k, tp in enumerate(timepoints)}
    if outcome.earliest_template is not None and (
            saved is None or rank_of.get(outcome.earliest_template[0], 0) < rank_of.get(saved[1], len(rank_of))):
        nm.save_template(template_path, outcome.earliest_template[1], outcome.earliest_template[0])
    elif saved is not None and not template_path.is_file():
        nm.save_template(template_path, saved[0], saved[1])           # a separate prior folder: carry it over
    if staging_dir is not None:
        _write_montages(outcome, well_label, staging_dir)
        update_montage_archive(output_dir / f"{well_label}_out.zip", staging_dir, well_label, todo)
        # Whether each address names the same nanowell at every timepoint, checked on what was just written
        # (NANOWELL_IDENTITY_TRACKER B1). Without a montage archive written here nothing is checked, and the well
        # reads as not checked rather than as a verdict on another run's montages.
        from pipeline.nanowell_identity import check_after_run

        check_after_run(output_dir, well_label, timepoints)
    # Failed means no rows were written for it. An unaddressed timepoint has rows (detected sites, no address) and
    # is not a failure of the run; it is a finding in the grid record, the completeness check and the end-of-run
    # summary, and the end-of-run pass may yet anchor it.
    failed = [tp for tp in todo if outcome.timepoints.get(tp, {}).get("status") in nr.TERMINAL_WITHOUT_ROWS]
    for tp in todo:
        block = outcome.timepoints.get(tp, {})
        if block.get("status") != nr.OK:
            log.warning("Well %s timepoint %s: %s (%s)", well_label, tp, block.get("status"), block.get("reason", ""))
    return records, failed


def no_groups_reason(zip_path, nuclear_token: str, fluor_tokens, schema, sep: str) -> str:
    """Why *zip_path* yields no image group, said from its member names the way the grouper reads them
    (``process_microscopy._collect_image_groups``): a group is an image whose channel field is *nuclear_token* (here
    the phase channel) with an image of every *fluor_tokens* channel named the same but for that field.

    "No image groups found" alone named no cause, and the usual causes are a schema that reads the channel from
    the wrong position (a concatenated plate's two prefixes were once taken for the channel) or a channel named
    differently from the form; each is told apart here by what the channel position actually holds."""
    from collections import Counter

    from util.image_exts import GROUP_IMAGE_EXTS

    fields = list(schema) if not isinstance(schema, str) else [f for f in schema.split(":") if f]
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = sorted({Path(n).name for n in zf.namelist()})
    except (OSError, zipfile.BadZipFile) as exc:
        return f"the archive cannot be read ({exc})"
    images = [n for n in names if not n.startswith(".") and Path(n).suffix.lower() in GROUP_IMAGE_EXTS]
    stems = [Path(n).stem for n in images]
    if not stems:
        return "it holds no images"
    if "channel" not in fields:
        return f"the filename schema ({':'.join(fields)}) has no channel field"
    at = fields.index("channel")
    held = Counter(parts[at] for parts in (s.split(sep) for s in stems) if len(parts) > at)
    shown = ", ".join(f"{value!r} ({n})" for value, n in held.most_common(6))
    where = f"position {at + 1} of the schema {':'.join(fields)} (separator {sep!r})"
    if nuclear_token not in held:
        return (f"no image has the phase channel {nuclear_token!r} at the channel's {where}; that position holds "
                f"{shown or 'nothing'}. Check the filename schema and the channel names on the form")
    present = set(images)
    for name in images:
        stem, suffix = Path(name).stem, Path(name).suffix
        parts = stem.split(sep)
        if len(parts) > at and parts[at] == nuclear_token:
            for token in fluor_tokens:
                # The grouper looks the partner up by its whole name, the phase image's extension included
                # (process_microscopy._derive_fluor_filename), so .tif beside .TIF is no pair.
                partner = sep.join([*parts[:at], token, *parts[at + 1:]]) + suffix
                if partner not in present:
                    return (f"the {nuclear_token} images have no {token} image named the same but for the channel "
                            f"(looked for {partner!r}, beside {stem!r}); the channel position holds {shown}")
    return f"the channel position ({where}) holds {shown}"


def run_zip_task(well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                 csv_prefix, schema, sep, settings_key, *, grouper, parse, segmenter=None, incremental=False,
                 incremental_prior_dir=None, plate_rigid=None, scratch=None, staging=None, reanchoring=False):
    """The whole lifecycle of one zipped well in nanowell mode (what ``process_well_zip_task`` does otherwise).

    Read the tiles from the archive in place (montages spill to *scratch*), run, write the three tables and the montage archive, record completeness (in
    timepoints, the unit of work here) and the settings that produced it. ``process_well_zip_task`` calls
    this from inside its own ``try``, so the failure trace, ``--force`` (which clears the staging directory
    and with it the resume checkpoints) and the removal of both directories are its own, exactly as for a
    per-field well; a caller without them (the end-of-run pass) gets directories of its own, removed after.
    """
    from pipeline.fov_checkpoint import _staging_dir_for, extract_dir_for
    from pipeline.io import remove_directory, write_well_csv
    from pipeline.run_report import seal_well_complete
    from pipeline.settings_record import record_well_settings

    output_dir = Path(output_dir)
    owned = scratch is None
    if owned:
        scratch = extract_dir_for(output_dir, well_label)
        staging = _staging_dir_for(output_dir, well_label, settings_key)
        for stale in (scratch,) + ((staging,) if not any(staging.glob(".fov_*.done")) else ()):
            if stale.exists():
                remove_directory(stale)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        records, failed = process_well_zip(
            well_label, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix,
            schema, sep, grouper=grouper, parse=parse, scratch_dir=scratch,
            write_csv=write_well_csv, segmenter=segmenter, incremental=incremental,
            prior_dir=incremental_prior_dir, settings_key=settings_key, staging_dir=staging, plate_rigid=plate_rigid)
        grid_path = output_dir / NANOWELL_DIR / f"{well_label}_grid.json"
        if not grid_path.is_file():
            return records, failed
        grid = json.loads(grid_path.read_text(encoding="utf-8"))
        total = len(grid["timepoints"])
        record_nanowell_completeness(output_dir, well_label, grid, failed)
        record_well_settings(output_dir, well_label, settings_key)
        seal_well_complete(output_dir, well_label)       # after the montage archive and the tables
        # "— done." exactly as a field-mode well says it: the remote Fetcher and Analyze's progress count a well
        # finished by that marker (``services/remote_wire._RE_WELL_DONE``), and a variant fetched nothing in-stream.
        # A re-anchoring re-run says something else: the well finished once already, and a second "done" read as a
        # repeated completion (roll-up review X31). Its results are pulled at verification from the grid records.
        if reanchoring:
            log.info("%s", reanchored_line(well_label, grid, plate_rigid, total, len(failed)))
        else:
            log.info("Well %s — done. %d timepoint(s), %d without rows.", well_label, total, len(failed))
        return records, failed
    finally:
        if owned:
            remove_directory(scratch)
            remove_directory(staging)


def record_nanowell_completeness(output_dir, well_label: str, grid: dict, failed) -> None:
    """Record a nanowell well's completeness from its grid record (*grid*), in timepoints, the unit of work here.

    ``groups_covered`` counts the timepoints addressed ``ok`` — Validate's and the INCOMPLETE summary's question.
    ``groups_reached`` counts those the run reached with any verdict, which is what decides whether the well is whole
    and so sealed (``pipeline.run_report.well_is_whole``): a timepoint left ``unaddressed`` is a finding the end-of-run
    re-anchoring may yet change, not work the well still owes. *failed* are the timepoints whose verdict wrote no rows.
    """
    from pipeline.run_report import write_well_report

    blocks = grid.get("timepoints") or {}
    write_well_report(output_dir, well_label, groups_total=len(blocks),
                      groups_covered=sum(1 for b in blocks.values() if b.get("status") == nr.OK),
                      groups_reached=sum(1 for b in blocks.values() if b.get("status")),
                      failed=failed)


def reanchored_line(well_label: str, grid: dict, asked, total: int, n_failed: int) -> str:
    """What a re-anchoring re-run of *well_label* says when it ends, from its grid record as written.

    Said apart: only a timepoint the plate's motion placed (``origin_method`` "plate_rigid", which
    ``services.remote_fetch_nanowells.reanchored_wells`` reads) makes the fetcher pull the well again, and a well
    that said "re-anchored" with none of the *asked* timepoints placed then waited on Analyze's plate for a pull
    that never came.
    """
    placed = [tp for tp in (asked or {}) if grid["timepoints"].get(tp, {}).get("origin_method") == "plate_rigid"]
    if placed:
        return f"Well {well_label} — re-anchored. {total} timepoint(s), {n_failed} without rows."
    return (f"Well {well_label} — re-anchoring placed none of {', '.join(sorted(asked or {}))}; "
            "its earlier result stands.")


def prepare_nanowell_run(options: dict, args, well_zips, refuse, input_dir, info_path) -> None:
    """Everything a nanowell run settles before any well is processed: refuse an input it cannot read
    (:func:`refuse_nanowell_without_zips`), record its blocks in the sidecar (:func:`record_nanowell_info`), and
    force the wells whose anchors changed since their last run (:func:`force_wells_with_new_anchors`)."""
    refuse_nanowell_without_zips(options, well_zips, refuse)
    record_nanowell_info(info_path, options, input_dir)
    force_wells_with_new_anchors(options, args, input_dir, Path(info_path).parent)


def force_wells_with_new_anchors(options: dict, args, input_dir, output_dir) -> None:
    """Add to ``args.force_wells`` every well whose output was made under other anchors than the ones set now
    (``util.nanowell_anchors.wells_with_unused_anchors``): a plain re-run would otherwise skip it as finished and
    never use an anchor set since. Nothing for a run that is not in nanowell mode, nor under ``--force`` (every well
    runs) or ``--incremental`` (no well is skipped up front, and the per-timepoint reuse is keyed on the anchors)."""
    from util.nanowell_anchors import wells_with_unused_anchors

    if not options or args.force or args.incremental:
        return
    changed = wells_with_unused_anchors(input_dir, output_dir)
    if not changed:
        return
    log.info("Anchors set by hand changed since the last run of %s; running %s again.", ", ".join(changed),
             "it" if len(changed) == 1 else "them")
    args.force_wells = [*(args.force_wells or []), *changed]


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
    # Where registration may be reused from is not a setting of the results: positions are reused only when
    # registering again would give exactly them. A path here would also make two plates' settings differ for Merge
    # Plates, which compares these blocks.
    settings = {k: v for k, v in options.items() if k != "registration_from"}
    block = {"nanowell": {"scheme_version": SCHEME_VERSION, "measure_version": nm.MEASURE_VERSION,
                          "settings": settings,
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
_INDEPENDENT = ("manual", "fiducial", "array_centre")


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


def awaits_final_pass(grid: dict) -> list[str]:
    """The timepoints of a well's grid record (*grid*) that the end-of-run re-anchoring could anchor: unaddressed,
    with stage positions, in a well anchored on its own evidence at another timepoint — the conditions of
    :func:`plan_reanchor` that one well's record can answer. The rest (the plate's fit) is only known at the end.

    The one test a batched remote run asks of a well twice: the batch's pipeline, to hold the well's archive for the
    final pass (:func:`hold_for_final_pass`), and the app, to keep its files on the remote until that pass has run
    (``services.remote_fetch_nanowells.release_batch``), so the two cannot disagree about a well.
    """
    well = grid.get("well")
    timepoints = grid.get("timepoints", {})
    grids = {well: grid}
    if not any(_origin(grids, well, tp) for tp in timepoints):
        return []
    return sorted((tp for tp, block in timepoints.items()
                   if block.get("status") == nr.UNADDRESSED and block.get("stage_offset_um") is not None),
                  key=_tp_rank)


def reanchor_only_run(options, args) -> bool:
    """Whether this invocation is a batched nanowell run's final pass (``--nanowell_reanchor_only``): it processes no
    well, and runs only the end-of-run re-anchoring over the archives the batches held."""
    return bool(options and getattr(args, "nanowell_reanchor_only", False))


def hold_dir_for(options, args) -> str | None:
    """Where this invocation holds wells for the final pass — none in the final pass itself, whose argv still names
    the folder, since the driver gives every batch the same argv."""
    return None if reanchor_only_run(options, args) else getattr(args, "nanowell_hold_dir", None)


def replaced_wells(options, args, wells: list[str]) -> list[str]:
    """The wells whose earlier coverage records this invocation replaces: every well it ran, but none in the final
    pass, which processes no well — a held well it cannot place keeps the record its batch wrote, and a re-run well's
    new record replaces it by itself."""
    return [] if reanchor_only_run(options, args) else wells


def hold_for_final_pass(output_dir, well_zips, hold_dir) -> list[str]:
    """Link into *hold_dir* the archive of every well of *well_zips* the end-of-run re-anchoring could anchor
    (:func:`awaits_final_pass`), for the run's final pass; returns those wells.

    A batched remote run deletes each batch's archives when the next batch is swapped in, and a well's anchors can
    come from any batch, so the re-anchoring has to wait for the last one; until then the archive is kept here. A
    hard link moves no bytes; a copy is the fallback where one cannot be made.
    """
    import shutil

    grids = _read_grids(output_dir)
    held = []
    for well, zip_path in well_zips:
        waiting = awaits_final_pass(grids.get(well, {}))
        if not waiting:
            continue
        hold_dir = Path(hold_dir)
        hold_dir.mkdir(parents=True, exist_ok=True)
        target = hold_dir / Path(zip_path).name
        try:
            target.unlink(missing_ok=True)
            os.link(zip_path, target)
        except OSError:
            shutil.copy2(zip_path, target)
        log.info("Well %s — held for the end-of-run re-anchoring (%s unaddressed).", well, ", ".join(waiting))
        held.append(well)
    return held


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


#: How far a well's nanowell diameter may sit from its plate's median before the run says so (tracker F11). The wells
#: of one plate share a mold, so they share a diameter; a fit on one tile reads it within a few tenths of a micrometre on
#: rendered plates, so 5 % is far outside measurement and points at the well — debris over the tile the diameter was
#: measured on, the wrong tile, or a well that is not this plate's.
PLATE_DIAMETER_TOLERANCE = 0.05


def plate_diameters(grids: dict[str, dict]) -> dict:
    """``plate -> {"median_um", "wells", "outliers"}``: each well's diameter against its plate's median (tracker F11).

    *grids* are grid records (:func:`_read_grids`). A well's diameter is the one its sites were measured at
    (``array_radius.diameter_used_um``); a well without one, or whose diameter is the format's because none could be
    measured (``diameter_source`` ``format``) or only roughly (``rough``, about a tenth small), is left out — counted, the format's value pulled the median toward
    itself and flagged the wells that were measured (review F20b) — and a plate of fewer than three wells has
    no median worth comparing against, so it lists its wells and flags none. ``outliers`` maps a well to its
    signed fraction off the median.
    """
    out = {}
    for plate, members in plates_of(grids).items():
        wells = {well: float(d) for well, grid in members.items()
                 if (d := (grid.get("array_radius") or {}).get("diameter_used_um")) is not None
                 and (grid.get("array_radius") or {}).get("diameter_source") not in ("format", "rough")}
        if not wells:
            continue
        median = float(np.median(list(wells.values()))) if len(wells) >= 3 else None
        outliers = {} if median is None else {
            well: d / median - 1.0 for well, d in sorted(wells.items()) if abs(d / median - 1.0) > PLATE_DIAMETER_TOLERANCE}
        out[plate] = {"median_um": median, "wells": dict(sorted(wells.items())), "outliers": outliers}
    return out


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


def _reanchor_well(job, *, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix, schema, sep,
                   settings_key, grouper, parse, segmenter):
    """One well's re-run with the plate's prediction; ``(well, timepoints, error)``, *error* ``""`` when it ran.

    A module-level function, so a worker process can be handed it. A failure is caught here, in the worker: one
    well's re-run must not cost the rest, nor the finished run's records.
    """
    well, zip_path, by_tp = job
    log.info("Well %s — re-anchoring %s from the plate's other wells.", well, ", ".join(sorted(by_tp)))
    try:
        run_zip_task(well, zip_path, output_dir, shared_kwargs_template, nuclear_token, fluor_tokens,
                     csv_prefix, schema, sep, settings_key, grouper=grouper, parse=parse, segmenter=segmenter,
                     incremental=True, incremental_prior_dir=output_dir, plate_rigid=by_tp, reanchoring=True)
    except Exception as exc:
        log.error("Well %s — re-anchoring failed (%s: %s); its timepoints stay unaddressed.", well,
                  type(exc).__name__, exc, exc_info=True)
        return well, by_tp, f"{type(exc).__name__}: {exc}"
    return well, by_tp, ""


def _each_reanchored(rerun, jobs, pool):
    """*rerun* over *jobs*, each well in a worker of *pool* (``pool()`` gives an executor) when there is one, so the
    node's cores share the pass rather than one segmenting while the rest wait.

    A worker that dies (``BrokenProcessPool``) fails its own well, with the reason, not the whole pass: the wells
    that finished keep what they did.
    """
    if pool is None or len(jobs) < 2:
        return [rerun(job) for job in jobs]
    results = []
    with pool() as executor:
        futures = [executor.submit(rerun, job) for job in jobs]
        for (well, _zip, by_tp), future in zip(jobs, futures):
            try:
                results.append(future.result())
            except Exception as exc:
                log.error("Well %s — re-anchoring failed (%s: %s); its timepoints stay unaddressed.", well,
                          type(exc).__name__, exc)
                results.append((well, by_tp, f"{type(exc).__name__}: {exc}"))
    return results


def reanchor_plate(output_dir, well_zips, shared_kwargs_template, nuclear_token, fluor_tokens, csv_prefix, schema,
                   sep, settings_key, *, grouper, parse, segmenter=None, pool=None) -> dict:
    """Re-run the unaddressed (well, timepoint)s the rest of the plate can anchor; report what it did.

    Only those wells' rows for those timepoints are rewritten (an incremental run with the prediction
    handed in; every other timepoint is kept as it is). Returns the counts the sidecar records:
    ``anchored`` (now addressed), ``remaining`` (still unaddressed) and why for each. With *pool* (a callable
    returning an executor) the wells are re-run in parallel.
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
    jobs = []
    for well, by_tp in predictions.items():
        if well not in zips:
            reasons.update({(well, tp): "this well is not part of this run, so it was not re-run" for tp in by_tp})
            continue
        jobs.append((well, zips[well], by_tp))
    rerun = partial(_reanchor_well, output_dir=output_dir, shared_kwargs_template=shared_kwargs_template,
                    nuclear_token=nuclear_token, fluor_tokens=fluor_tokens, csv_prefix=csv_prefix, schema=schema,
                    sep=sep, settings_key=settings_key, grouper=grouper, parse=parse, segmenter=segmenter)
    for well, by_tp, error in _each_reanchored(rerun, jobs, pool):
        if error:
            reasons.update({(well, tp): f"the re-run failed: {error}" for tp in by_tp})
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
                     schema, sep, settings_key, *, grouper, parse, pool=None, hold_dir=None) -> None:
    """What nanowell mode does after every well has run; a no-op when the mode is off (the caller does not branch).

    With *hold_dir* (one batch of a batched remote run) the re-anchoring is left to the run's final pass and the
    wells it could anchor are held for it (:func:`hold_for_final_pass`); the plate's records are written by that
    pass. Otherwise the pass runs here, its wells in parallel on *pool*.
    """
    if not options:
        return
    if hold_dir:
        held = hold_for_final_pass(output_dir, well_zips, hold_dir)
        log.info("Re-anchoring left to the end of the run: %d well(s) of this batch held for it.", len(held))
        return
    try:
        summary = reanchor_plate(output_dir, well_zips, shared_kwargs_template, nuclear_token, fluor_tokens,
                                 csv_prefix, schema, sep, settings_key, grouper=grouper, parse=parse, pool=pool)
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
    _check_plate_diameters(output_dir, settings_from_kwargs(shared_kwargs_template).key())
    from pipeline.nanowell_identity import stage_check_after_run

    stage_check_after_run(output_dir)


def _check_plate_diameters(output_dir, settings: dict) -> None:
    """Compare every well's nanowell diameter with its plate's median, log the wells far from it, and record the
    comparison in ``pipeline_info.json`` as ``nanowell.plate_diameters`` (tracker F11). Only wells analysed under
    *settings* (a ``NanowellSettings.key()``) take part, as in the re-anchoring pass."""
    from pipeline.io import _safe_atomic_text_write
    from pipeline.run_report import merge_into_pipeline_info

    plates = plate_diameters(_read_grids(Path(output_dir), settings))
    for plate, found in plates.items():
        for well, off in found["outliers"].items():
            log.warning("Well %s: its nanowells measure %.1f um, %+.0f %% off the %.1f um the plate%s's other wells "
                        "share; check the tile its diameter was measured on.", well, found["wells"][well], off * 100,
                        found["median_um"], f" {plate}" if plate else "")
    info_path = Path(output_dir) / "pipeline_info.json"
    try:
        existing = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
        block = dict(existing.get("nanowell", {}))
        block["plate_diameters"] = plates
        merge_into_pipeline_info(info_path, {"nanowell": block}, atomic_writer=_safe_atomic_text_write)
    except (OSError, ValueError) as exc:
        log.warning("Could not record the plate's nanowell diameters in %s: %s", info_path, exc)


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
