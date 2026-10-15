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

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("review")

NANOWELL_DIR = "nanowells"
#: Scheme versions this reader understands (the pipeline's ``nanowell_task.SCHEME_VERSION`` when it was written).
SUPPORTED_SCHEMES = frozenset({1})

#: The states a site can be recorded in (pipeline ``nanowell_measure``), and the two Review adds for a timepoint
#: at which a nanowell has no row: the timepoint was not addressed, or it was not located at all.
MEASURED, EMPTY, OCCLUDED, CLIPPED = "measured", "empty", "occluded", "clipped"
UNADDRESSED, NOT_LOCATED = "unaddressed", "not_located"
#: States whose contents metrics are defined (tracker §3.5).
MEASURABLE = frozenset({MEASURED, EMPTY})

_BOOL_COLUMNS = ("detected", "fused", "partially_visible", "count_disagreement")
_NUMERIC_COLUMNS = ("nw_q", "nw_r", "nw_ring", "well_x_um", "well_y_um", "radius_um", "imaged_fraction",
                    "visible_fraction", "focus", "n_cells", "confluence", "area_est_cells")


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
    #: Wells left out and why, as sentences.
    problems: list = field(default_factory=list)

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


def _cell_csvs(folder: Path) -> list[Path]:
    """The folder's per-cell CSVs in either spelling, ``x.csv`` or ``x.csv.gz``, one path per logical file.

    A finished well's CSV may be gzipped; when both spellings of one file exist the plain one wins, the rule
    ``util/csv_gz`` states for every reader (a gzip is only ever made from a plain file, so a plain file
    beside one is the newer or the same).
    """
    plain = {p.name: p for p in folder.glob("*.csv")}
    gzipped = {p.name[: -len(".gz")]: p for p in folder.glob("*.csv.gz")}
    return [plain.get(name) or gzipped[name] for name in sorted(set(plain) | set(gzipped))]


def _cell_brightness(cell_dir: Path, channels) -> pd.DataFrame | None:
    """Mean per-cell intensity per (well, timepoint, nanowell, channel), from the per-cell CSVs in *cell_dir*.

    *cell_dir* is the folder holding ``nanowells/`` — the run's output folder — whichever of it and its
    parent was loaded.
    """
    frames = []
    for path in _cell_csvs(Path(cell_dir)):
        try:
            header = pd.read_csv(path, nrows=0).columns
        except (OSError, ValueError, pd.errors.EmptyDataError):
            continue
        wanted = [c for c in ("well", "timepoint", "nw_q", "nw_r") if c in header]
        if len(wanted) < 4:
            continue                                       # not a nanowell-mode cell table
        intensity = [f"{t}_mean_intensity" for t in channels if f"{t}_mean_intensity" in header]
        frame = pd.read_csv(path, usecols=wanted + intensity, dtype={"timepoint": str, "well": str})
        frames.append(frame)
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


def _phase_token(sidecar: dict) -> str:
    """The run's phase channel token, lower-cased ("" when not stated).

    The pipeline writes site intensities for every channel it loads, the phase channel included; a phase
    image's "brightness" is not fluorescence, so it is not offered as a fluorescence channel.
    """
    options = sidecar.get("execution_options") if isinstance(sidecar.get("execution_options"), dict) else {}
    token = options.get("phase_token") or sidecar.get("phase_token") or ""
    return str(token).strip().lower()


def load_nanowell_data(data_dir) -> NanowellData | None:
    """The dataset's nanowell tables, or ``None`` for a dataset that was not run in nanowell mode.

    Raises :class:`NanowellSchemeError` when the sidecar states a scheme this version does not understand.
    """
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
    frames, grids, problems = [], {}, []
    for path in sorted(folder.glob("*.csv")):
        well = path.stem
        grid_path = folder / f"{well}_grid.json"
        try:
            grid = json.loads(grid_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            problems.append(f"{well}: its grid record ({grid_path.name}) is missing, so its timepoints' "
                            "status is unknown; the well is left out.")
            continue
        except (OSError, ValueError) as exc:
            problems.append(f"{well}: its grid record cannot be read ({exc}); the well is left out.")
            continue
        if grid.get("scheme_version") not in SUPPORTED_SCHEMES:
            problems.append(f"{well}: scheme version {grid.get('scheme_version')!r} is not one this version "
                            f"reads ({sorted(SUPPORTED_SCHEMES)}); the well is left out.")
            continue
        try:
            table = _read_well_table(path, well)
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            problems.append(f"{well}: its nanowell table cannot be read ({exc}); the well is left out.")
            continue
        repeated = table[table["nw_q"].notna()].duplicated(["timepoint", "nw_q", "nw_r"], keep=False)
        if repeated.any():
            first = table[table["nw_q"].notna()][repeated].iloc[0]
            problems.append(f"{well}: {int(repeated.sum())} rows repeat an address at one timepoint (e.g. "
                            f"({int(first['nw_q'])}, {int(first['nw_r'])}) at {first['timepoint']}), so its "
                            "nanowells cannot be followed over time; the well is left out.")
            continue
        frames.append(table)
        grids[well] = grid.get("timepoints") or {}
    sites = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["well", "timepoint"])
    phase = _phase_token(sidecar)
    channels = sorted({c[: -len("_integrated")] for c in sites.columns if c.endswith("_integrated")}
                      - {c for c in (c[: -len("_integrated")] for c in sites.columns) if c.lower() == phase})
    timepoints = sorted(set(sites["timepoint"].dropna()) | {tp for g in grids.values() for tp in g})
    times_h, source, note = _times(data_dir, timepoints)
    brightness = _cell_brightness(folder.parent, channels)
    if brightness is not None and len(sites):
        sites = sites.merge(brightness, on=["well", "timepoint", "nw_q", "nw_r"], how="left")
    for channel in channels:
        if f"brightness_{channel}" not in sites:
            sites[f"brightness_{channel}"] = np.nan
    sites["time_h"] = sites["timepoint"].map(times_h).astype(float)
    return NanowellData(sites=sites, grids=grids, times_h=times_h, time_source=source, time_note=note,
                        channels=channels, problems=problems)
