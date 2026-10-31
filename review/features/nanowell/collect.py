"""Read a nanowell-mode run's outputs into one table per dataset (tracker D1).

A nanowell run writes, beside the usual per-cell CSVs:

* ``nanowells/<well>.csv`` — one row per (timepoint, roster site): address, state, fractions, counts,
  confluence and per-channel site intensities (``pipeline/nanowell_task.SITE_COLUMNS``);
* ``nanowells/<well>_grid.json`` — per timepoint, how the well was located, with its status and reason;
* a ``nanowell`` block in ``pipeline_info.json`` carrying the scheme version.

The tables sit in a subdirectory because Review's loader reads every *top-level* ``*.csv`` as a well's cell
table (``review.core.load_controller.load_directory``); nothing here changes that.

**A scheme this version does not understand is refused, by name.** The scheme version states what the
columns and conventions mean; reading a later scheme as this one would draw numbers under the wrong meaning
with nothing to say so. A well whose grid record states another version is left out and named in
:attr:`NanowellData.problems`; a dataset whose sidecar does is refused whole.

**Time is each timepoint's one recorded start** (``in/acquisition.json``, tracker §3.0.1), in hours since the
earliest of them, so a rate over unevenly spaced acquisitions is per real hour. Only when the record cannot
give every timepoint a comparable start does the timepoint label stand in (``util.timepoint.parse_hours``),
and :attr:`NanowellData.time_source` says which was used — never a mixture of the two.

Qt-free.
"""

from __future__ import annotations

import functools
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from domain.nanowell_roster import roster_rule

log = logging.getLogger("review")

NANOWELL_DIR = "nanowells"
#: Scheme versions this reader understands (the pipeline's ``nanowell_task.SCHEME_VERSION`` when it was written).
SUPPORTED_SCHEMES = frozenset({1})

#: The states a site can be recorded in (pipeline ``nanowell_measure``), and the two Review adds for a timepoint
#: at which a nanowell has no row: the timepoint was not addressed, or it was not located at all.
MEASURED, EMPTY, OCCLUDED, CLIPPED = "measured", "empty", "occluded", "clipped"
UNADDRESSED, NOT_LOCATED = "unaddressed", "not_located"
#: A timepoint whose addresses the identity check found naming other nanowells than the well's reference timepoint
#: does (``pipeline.nanowell_identity``): its rows are kept out of every join over time, with the check's reason.
IDENTITY_CONTRADICTED = "identity_contradicted"
#: States whose contents metrics are defined (tracker §3.5).
MEASURABLE = frozenset({MEASURED, EMPTY})

_BOOL_COLUMNS = ("detected", "fused", "partially_visible", "count_disagreement")
_NUMERIC_COLUMNS = ("nw_q", "nw_r", "nw_ring", "well_x_um", "well_y_um", "radius_um", "imaged_fraction",
                    "visible_fraction", "focus", "n_cells", "confluence", "area_est_cells",
                    "level_offset", "level_gain", "segmenter_floor", "segmenter_contrast")


class NanowellSchemeError(ValueError):
    """The dataset states a nanowell scheme this version does not understand."""


@dataclass
class NanowellData:
    """Every well's nanowell rows, the grid records, and the time axis they are drawn against."""

    #: One row per (well, timepoint, roster site) with a ``well`` column; ``nw_q`` / ``nw_r`` are NaN on an
    #: unaddressed timepoint. ``time_h`` is the timepoint's time (see ``time_source``).
    sites: pd.DataFrame
    #: ``well -> {timepoint -> grid block}``, as the pipeline wrote it.
    grids: dict = field(default_factory=dict)
    #: ``timepoint -> hours``; a timepoint with no time is absent.
    times_h: dict = field(default_factory=dict)
    #: ``"recorded"`` (the acquisition record's starts), ``"label"`` (parsed timepoint labels), or ``None``.
    time_source: str | None = None
    #: Why the record's times were not used, when they were not.
    time_note: str = ""
    #: Fluorescence channels with site intensities (``<token>_integrated`` columns).
    channels: list = field(default_factory=list)
    #: The fluorescence channels the run read images of (the sidecar's ``fluor_tokens`` less the phase channel, and
    #: every channel with site intensities), spelled as the tables spell them where they have one: what the crops
    #: and the full-resolution montage can be drawn from besides the phase image.
    image_channels: list = field(default_factory=list)
    #: ``channel -> why`` for each of :attr:`image_channels` with no site intensities, so the page can say why a
    #: channel the run read offers no Brightness or Nanowell fluorescence rather than leaving it out without a word.
    unmeasured_channels: dict = field(default_factory=dict)
    #: Wells left out and why, as sentences.
    problems: list = field(default_factory=list)
    #: ``well -> the rule its roster was decided by`` (``domain.nanowell_roster``): a dataset can mix them.
    roster_rules: dict = field(default_factory=dict)
    #: ``well -> why`` for a well an incremental run analysed again over every timepoint (tracker X35a-R8).
    reanalysed: dict = field(default_factory=dict)
    #: ``well -> {timepoint -> (verdict, reason)}``: whether each timepoint's addresses name the same nanowells as
    #: the well's reference timepoint (``pipeline.nanowell_identity``; ``not_checked`` without a current sidecar).
    identity: dict = field(default_factory=dict)
    #: ``well -> {timepoint -> addresses}`` that a ``partial`` timepoint leaves out of every comparison over time.
    identity_excluded: dict = field(default_factory=dict)
    #: Where the run's files are, for the readers of images (crops, montages): the output folder holding
    #: ``nanowells/``, and the input folder holding ``<well>.zip`` (``None`` when it is not beside the data).
    out_dir: Path | None = None
    in_dir: Path | None = None
    #: ``timepoint -> {"pixel_size_um": {...}, "tile_size_px": {...}}`` from the sidecar's acquisition block.
    acquisition: dict = field(default_factory=dict)
    #: ``well -> the per-cell CSV holding its cells``, found while reading their brightness: where the Array view's
    #: Cells layer reads a well's cell positions (:func:`cell_positions`), one well at a time.
    cell_tables: dict = field(default_factory=dict)
    #: The filename schema, separator and phase token the run read its images with (for the crop reader).
    schema: str = ""
    separator: str = ""
    phase_token: str = ""
    #: Every well with a nanowell table in the folder, read or not: the tables are read only for the wells asked
    #: for (:func:`load_nanowell_data`), and this is what tells a well that was not read from one with no table.
    available: list = field(default_factory=list)

    @property
    def wells(self) -> list[str]:
        return sorted(self.sites["well"].unique()) if len(self.sites) else []

    def timepoints(self, well: str | None = None) -> list[str]:
        """Timepoints in time order (by ``time_h``, unknown times last, then by label)."""
        rows = self.sites if well is None else self.sites[self.sites["well"] == well]
        found = set(rows["timepoint"]) if len(rows) else set()
        if well is not None:
            found |= set(self.grids.get(well, {}))
        return self.order(found)

    def order(self, timepoints) -> list[str]:
        """*timepoints* in time order: by ``time_h``, timepoints without a time last, ties by label."""
        return sorted(timepoints, key=lambda tp: (tp not in self.times_h, self.times_h.get(tp, 0.0), tp))


def nanowell_dir(data_dir) -> Path | None:
    """The ``nanowells/`` folder for a loaded data dir (the ``out/`` folder or its parent), or ``None``."""
    data_dir = Path(data_dir)
    for candidate in (data_dir / NANOWELL_DIR, data_dir / "out" / NANOWELL_DIR):
        if candidate.is_dir():
            return candidate
    return None


def _sidecar(data_dir: Path) -> dict:
    from review.domain.viewer_state import resolve_pipeline_info_path

    path = resolve_pipeline_info_path(data_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Could not read %s: %s", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _bool(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip().str.lower()
    return text.map({"true": True, "false": False, "1": True, "0": False}).astype("object")


def _read_well_table(path: Path, well: str) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"timepoint": str, "state": str, "geometry": str, "owner": str},
                        keep_default_na=True)
    for column in _NUMERIC_COLUMNS:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in _BOOL_COLUMNS:
        if column in frame:
            frame[column] = _bool(frame[column])
    for column in frame.columns:
        if column.endswith("_integrated") or column.endswith("_mean_bg"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame.insert(0, "well", well)
    return frame


def _with_pattern_calibration(acquisition: dict, sidecar: dict, timepoints) -> dict:
    """A run placed from a scan pattern (no acquisition record, tracker P15) states its pixel size in its settings
    and has no acquisition block: give each timepoint that pixel size, and no tile size, which the run read from
    each image and did not record — Array QC then marks each tile's corner rather than guessing its outline."""
    settings = (sidecar.get("nanowell") or {}).get("settings") or {}
    pattern = settings.get("pattern") if isinstance(settings, dict) else None
    pixel = pattern.get("pixel_size_um") if isinstance(pattern, dict) else None
    if not isinstance(pixel, (int, float)) or isinstance(pixel, bool) or pixel <= 0:
        return acquisition
    for tp in timepoints:
        acquisition.setdefault(tp, {"pixel_size_um": {"x": float(pixel), "y": float(pixel)}, "tile_size_px": {}})
    return acquisition


def _record_candidates(data_dir: Path) -> list[Path]:
    from util.acquisition_record import RECORD_FILENAME

    return [data_dir / "in" / RECORD_FILENAME, data_dir.parent / "in" / RECORD_FILENAME]


def _start(text) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def recorded_hours(record: dict | None, timepoints) -> tuple[dict, str]:
    """``timepoint -> hours since the earliest start`` from the record, or ``({}, why not)``.

    Every timepoint must have a start, and the starts must be comparable (all with a timezone, or all
    without): a mixture cannot be ordered without inventing an offset, so none of them is used.
    """
    if record is None:
        return {}, "there is no acquisition record"
    entries = record.get("timepoints") or {}
    starts = {}
    for tp in timepoints:
        start = _start((entries.get(tp) or {}).get("started_at"))
        if start is None:
            return {}, f"the acquisition record has no start time for timepoint {tp!r}"
        starts[tp] = start
    if not starts:
        return {}, "there are no timepoints"
    if len({s.tzinfo is None for s in starts.values()}) > 1:
        return {}, "the record's start times mix clocks with and without a timezone"
    first = min(starts.values())
    return {tp: (s - first).total_seconds() / 3600.0 for tp, s in starts.items()}, ""


def label_hours(timepoints) -> dict:
    """``timepoint -> hours`` parsed from each label; a label that is not a time is absent."""
    from util.timepoint import parse_hours

    out = {}
    for tp in timepoints:
        hours = parse_hours(tp)
        if hours is not None and math.isfinite(hours):
            out[tp] = float(hours)
    return out


def _times(data_dir: Path, timepoints) -> tuple[dict, str | None, str]:
    from util.acquisition_record import AcquisitionRecordError, read_record_file

    record, why = None, "there is no acquisition record"
    for path in _record_candidates(data_dir):
        try:
            record = read_record_file(path)
        except AcquisitionRecordError as exc:
            record, why = None, f"the acquisition record cannot be read ({exc})"
            break
        if record is not None:
            break
    hours, reason = recorded_hours(record, timepoints) if record is not None else ({}, why)
    if hours:
        return hours, "recorded", ""
    labels = label_hours(timepoints)
    if labels and len(labels) == len(set(timepoints)):
        return labels, "label", f"Times are read from the timepoint labels because {reason}."
    # Never a partial axis: a growth rate over the timepoints whose labels happen to parse is a rate over a
    # different series from the one shown.
    return {}, None, (f"Times are unknown because {reason} and not every timepoint label is a time; "
                      "nothing measured against time is defined.")


def _cell_brightness(paths, well: str | None = None, tables: dict | None = None) -> pd.DataFrame | None:
    """Mean per-cell intensity per (well, timepoint, nanowell), one ``brightness_<token>`` column per
    ``<token>_mean_intensity`` column, from the per-cell CSVs at *paths*; only *well*'s rows when one is named.
    Which of the columns are fluorescence channels is decided when the wells are put together
    (:func:`_assemble`), from the site tables. *tables*, when given, is filled with ``well -> the CSV holding its
    cells`` (the first that does), for the Cells layer (:func:`cell_positions`).
    """
    frames = []
    for path in paths:
        try:
            header = pd.read_csv(path, nrows=0).columns
        except (OSError, ValueError, pd.errors.EmptyDataError):
            continue
        wanted = [c for c in ("well", "timepoint", "nw_q", "nw_r") if c in header]
        if len(wanted) < 4:
            continue                                       # not a nanowell-mode cell table
        intensity = [c for c in header if c.endswith("_mean_intensity")]
        frame = pd.read_csv(path, usecols=wanted + intensity, dtype={"timepoint": str, "well": str})
        if well is not None:
            frame = frame[frame["well"] == well]
        frames.append(frame)
        if tables is not None:
            for named in frame["well"].dropna().unique():
                tables.setdefault(str(named), path)
            if well is not None and Path(path).name.split(".csv")[0].endswith(f"_{well}"):
                # The well's own table with no rows is a run that measured it and counted no cell
                # (pipeline/nanowell_task.write_empty_cells_csv), not a well without a cell table.
                tables.setdefault(well, path)
    if not frames:
        return None
    cells = pd.concat(frames, ignore_index=True)
    cells = cells.dropna(subset=["nw_q", "nw_r"])
    if cells.empty:
        return None
    grouped = cells.groupby(["well", "timepoint", "nw_q", "nw_r"], sort=False)
    columns = [c for c in cells.columns if c.endswith("_mean_intensity")]
    out = grouped[columns].mean() if columns else grouped.size().to_frame("_n").drop(columns="_n")
    out.columns = [f"brightness_{c[: -len('_mean_intensity')]}" for c in columns]
    return out.reset_index()


def _cell_files_of(well: str, cell_files, wells) -> list[Path]:
    """The per-cell CSVs that can hold *well*'s cells: its own (``<prefix>_<well>.csv``, as the pipeline names
    them) and any that name no well of the dataset (a table made by hand or by merging, which may hold
    several). A file named for another well is not opened."""
    def named_well(path: Path) -> str | None:
        stem = path.name.split(".csv")[0]
        return next((w for w in wells if stem.endswith(f"_{w}")), None)

    return [p for p in cell_files if named_well(p) in (well, None)]


@dataclass
class WellPart:
    """One well's share of :class:`NanowellData`, read once and kept, so a well selected again is not read
    again: its site table (``None`` with :attr:`problem` saying why it was left out), its grid record's
    timepoints, roster rule and re-analysis note, and its cells' brightness."""
    well: str
    table: pd.DataFrame | None = None
    grid: dict = field(default_factory=dict)
    roster_rule: object = None
    reanalysed: str = ""
    problem: str = ""
    brightness: pd.DataFrame | None = None
    cell_table: Path | None = None          # the CSV holding its cells, for the Cells layer
    #: ``timepoint -> (verdict, reason)`` from the identity check's sidecar (``pipeline.nanowell_identity.verdicts``):
    #: ``not_checked`` for every timepoint when there is no current sidecar.
    identity: dict = field(default_factory=dict)
    #: ``timepoint -> addresses`` a ``partial`` timepoint leaves out (``pipeline.nanowell_identity.exclusions``).
    identity_excluded: dict = field(default_factory=dict)


def read_well_part(folder: Path, well: str, cell_files, wells) -> WellPart:
    """*well*'s table, grid record and cells' brightness, or a part whose :attr:`WellPart.problem` says why it
    cannot be used."""
    path = folder / f"{well}.csv"
    grid_path = folder / f"{well}_grid.json"
    try:
        grid = json.loads(grid_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return WellPart(well, problem=f"{well}: its grid record ({grid_path.name}) is missing, so its timepoints' "
                                      "status is unknown; the well is left out.")
    except (OSError, ValueError) as exc:
        return WellPart(well, problem=f"{well}: its grid record cannot be read ({exc}); the well is left out.")
    if grid.get("scheme_version") not in SUPPORTED_SCHEMES:
        return WellPart(well, problem=f"{well}: scheme version {grid.get('scheme_version')!r} is not one this "
                                      f"version reads ({sorted(SUPPORTED_SCHEMES)}); the well is left out.")
    try:
        table = _read_well_table(path, well)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        return WellPart(well, problem=f"{well}: its nanowell table cannot be read ({exc}); the well is left out.")
    repeated = table[table["nw_q"].notna()].duplicated(["timepoint", "nw_q", "nw_r"], keep=False)
    if repeated.any():
        first = table[table["nw_q"].notna()][repeated].iloc[0]
        return WellPart(well, problem=f"{well}: {int(repeated.sum())} rows repeat an address at one timepoint "
                                      f"(e.g. ({int(first['nw_q'])}, {int(first['nw_r'])}) at "
                                      f"{first['timepoint']}), so its nanowells cannot be followed over time; "
                                      "the well is left out.")
    tables: dict = {}
    brightness = _cell_brightness(_cell_files_of(well, cell_files, wells), well, tables)
    from pipeline.nanowell_identity import exclusions, verdicts

    return WellPart(well, table=table, grid=grid.get("timepoints") or {},
                    roster_rule=roster_rule(grid.get("array_radius")),
                    reanalysed=(grid.get("array_radius") or {}).get("reanalysed_because") or "",
                    brightness=brightness, cell_table=tables.get(well),
                    identity=verdicts(folder.parent, well, list(grid.get("timepoints") or {})),
                    identity_excluded=exclusions(folder.parent, well))


def _phase_token(sidecar: dict) -> str:
    """The run's phase channel token, lower-cased ("" when not stated).

    The pipeline writes site intensities for every channel it loads, the phase channel included; a phase
    image's "brightness" is not fluorescence, so it is not offered as a fluorescence channel.
    """
    options = sidecar.get("execution_options") if isinstance(sidecar.get("execution_options"), dict) else {}
    token = options.get("phase_token") or sidecar.get("phase_token") or ""
    return str(token).strip().lower()


def _image_channels(sidecar: dict, phase: str, channels: list, *, read_any: bool) -> tuple[list, dict]:
    """``(image channels, {channel: why it has no site intensities})``: every fluorescence channel the run read, in
    the sidecar's order, then any the tables carry that the sidecar does not name. A channel is spelled as the tables
    spell it when they carry it (the image reader matches names case-insensitively either way). The phase channel is
    never one: its images are what the crops show by default."""
    listed = sidecar.get("fluor_tokens")
    listed = [str(t).strip() for t in listed if str(t).strip()] if isinstance(listed, list) else []
    by_lower = {c.lower(): c for c in channels}
    out: list = []
    for token in [*listed, *channels]:
        spelled = by_lower.get(token.lower(), token)
        if token.lower() != phase and spelled.lower() not in {c.lower() for c in out}:
            out.append(spelled)
    unmeasured = {}
    if read_any:
        for token in out:
            if token.lower() not in by_lower:
                unmeasured[token] = (f"The run read {token} images, but the nanowell tables of the wells shown carry "
                                     f"no {token}_integrated column, so there is no {token} brightness or nanowell fluorescence "
                                     f"to draw. Re-run the wells with {token} among the fluorescence channels to "
                                     f"measure it.")
    return out, unmeasured


def nanowell_wells(folder: Path) -> list[str]:
    """Every well with a nanowell table in *folder* (``nanowells/<well>.csv``), in name order; nothing is read."""
    return [path.stem for path in sorted(folder.glob("*.csv"))]


def load_nanowell_data(data_dir, wells=None, parts: dict | None = None) -> NanowellData | None:
    """The dataset's nanowell tables, or ``None`` for a dataset that was not run in nanowell mode.

    Only the tables of *wells* are read (every well's when ``None``): a plate of a hundred wells is not read
    whole to draw the three selected. *parts* (``well -> WellPart``) holds wells already read, which are used
    as they are, and receives each well read now, so the caller can keep it and a well selected again is not
    read again. :attr:`NanowellData.available` lists every well with a table, read or not.

    Raises :class:`NanowellSchemeError` when the sidecar states a scheme this version does not understand.
    """
    from util.csv_gz import glob_csvs

    data_dir = Path(data_dir)
    folder = nanowell_dir(data_dir)
    if folder is None:
        return None
    sidecar = _sidecar(data_dir)
    block = sidecar.get("nanowell")
    if isinstance(block, dict) and block.get("scheme_version") not in SUPPORTED_SCHEMES:
        raise NanowellSchemeError(
            f"This dataset's nanowell tables use scheme version {block.get('scheme_version')!r}; this version of "
            f"All-Well reads {sorted(SUPPORTED_SCHEMES)}. Open it with the version that analysed it, or re-run "
            "the analysis.")
    available = nanowell_wells(folder)
    asked = available if wells is None else [w for w in available if w in set(wells)]
    parts = {} if parts is None else parts
    missing = [w for w in asked if w not in parts]
    if missing:
        # Listed once (a name glob, nothing opened) and shared by every well read now.
        cell_files = glob_csvs(folder.parent)
        for well in missing:
            parts[well] = read_well_part(folder, well, cell_files, available)
    return _assemble(data_dir, folder, sidecar, [parts[w] for w in asked], available)


def _assemble(data_dir: Path, folder: Path, sidecar: dict, parts: list, available: list) -> NanowellData:
    """The wells of *parts* (in name order) put together as one :class:`NanowellData`."""
    frames = [p.table for p in parts if p.table is not None]
    grids = {p.well: p.grid for p in parts if p.table is not None}
    roster_rules = {p.well: p.roster_rule for p in parts if p.table is not None}
    reanalysed = {p.well: p.reanalysed for p in parts if p.table is not None and p.reanalysed}
    identity = {p.well: dict(p.identity) for p in parts if p.table is not None}
    identity_excluded = {p.well: dict(p.identity_excluded) for p in parts
                         if p.table is not None and p.identity_excluded}
    problems = [p.problem for p in parts if p.problem]
    sites = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["well", "timepoint"])
    phase = _phase_token(sidecar)
    channels = sorted({c[: -len("_integrated")] for c in sites.columns if c.endswith("_integrated")}
                      - {c for c in (c[: -len("_integrated")] for c in sites.columns) if c.lower() == phase})
    image_channels, unmeasured = _image_channels(sidecar, phase, channels, read_any=bool(frames))
    timepoints = sorted(set(sites["timepoint"].dropna()) | {tp for g in grids.values() for tp in g})
    times_h, source, note = _times(data_dir, timepoints)
    keep = ["well", "timepoint", "nw_q", "nw_r"] + [f"brightness_{ch}" for ch in channels]
    found = [p.brightness for p in parts if p.table is not None and p.brightness is not None]
    brightness = pd.concat(found, ignore_index=True) if found else None
    cell_tables = {p.well: p.cell_table for p in parts if p.table is not None and p.cell_table is not None}
    if brightness is not None and len(sites):
        sites = sites.merge(brightness[[c for c in keep if c in brightness]],
                            on=["well", "timepoint", "nw_q", "nw_r"], how="left")
    for channel in channels:
        if f"brightness_{channel}" not in sites:
            sites[f"brightness_{channel}"] = np.nan
    sites["time_h"] = sites["timepoint"].map(times_h).astype(float)
    acquisition = dict(sidecar.get("acquisition")) if isinstance(sidecar.get("acquisition"), dict) else {}
    acquisition = _with_pattern_calibration(acquisition, sidecar, timepoints)
    in_dir = next((p.parent for p in _record_candidates(data_dir) if p.parent.is_dir()), None)
    options = sidecar.get("execution_options") if isinstance(sidecar.get("execution_options"), dict) else {}
    return NanowellData(sites=sites, grids=grids, times_h=times_h, time_source=source, time_note=note,
                        out_dir=folder.parent, in_dir=in_dir, acquisition=acquisition,
                        schema=str(options.get("filename_schema") or sidecar.get("schema") or ""),
                        separator=str(options.get("filename_sep") or sidecar.get("separator") or ""),
                        phase_token=str(options.get("phase_token") or sidecar.get("phase_token") or ""),
                        channels=channels, image_channels=image_channels, unmeasured_channels=unmeasured,
                        problems=problems, roster_rules=roster_rules,
                        reanalysed=reanalysed, available=available, cell_tables=cell_tables, identity=identity,
                        identity_excluded=identity_excluded)


#: The columns of a nanowell run's cell table the Cells layer reads.
CELL_POSITION_COLUMNS = ("well", "timepoint", "nw_q", "nw_r", "well_x_um", "well_y_um")
#: The columns that identify a cell (``cell_gate_compute.CELL_KEY``), read when the table has them, so a dot can be
#: matched to the analysis's inclusion of its cell.
CELL_IDENTITY_COLUMNS = ("fov", "nucleus_id")


def cell_positions(data: NanowellData, well: str, timepoint: str) -> pd.DataFrame | None:
    """Every counted cell of *well* at *timepoint*: its nanowell's address, its centre in the well frame (µm), and
    ``key``, its identity (``cell_gate_compute.CELL_KEY``; ``None`` throughout for a table without nucleus ids),
    from the run's cell table. ``None`` when the well has no cell table (a run that counted no cell, or one that
    could not count). The table is read whole once per file and kept while it is unchanged on disk, so stepping
    through a well's timepoints reads it once.

    These are the cells the *run* counted. Which of them the analysis keeps is the Cell Gating tally's
    (``cell_gate_compute.GateTally.left_out``), matched on ``key`` by whoever draws them."""
    from review.features.nanowell.cell_gate_compute import cell_keys

    path = data.cell_tables.get(well)
    if path is None:
        return None
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    frame = _read_cell_positions(str(path), stat.st_mtime_ns, stat.st_size)
    if frame is None:
        return None
    found = frame[(frame["well"] == well) & (frame["timepoint"] == timepoint)].copy()
    keys = cell_keys(well, found)
    found["key"] = keys if keys is not None else [None] * len(found)
    return found


@functools.lru_cache(maxsize=2)
def _read_cell_positions(path: str, _mtime_ns: int, _size: int) -> pd.DataFrame | None:
    """One cell table's positions and identities; the modification time and size are the cache key, so an edited
    file is read again."""
    wanted = set(CELL_POSITION_COLUMNS) | set(CELL_IDENTITY_COLUMNS)
    try:
        frame = pd.read_csv(path, usecols=lambda column: column in wanted,
                            dtype={"timepoint": str, "well": str, "fov": str})
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        log.warning("Nanowells: the cell table %s cannot be read for the Cells layer (%s)", path, exc)
        return None
    missing = [c for c in CELL_POSITION_COLUMNS if c not in frame.columns]
    if missing:
        log.warning("Nanowells: the cell table %s has no %s column(s) for the Cells layer", path, ", ".join(missing))
        return None
    return frame.dropna(subset=["well_x_um", "well_y_um"])
