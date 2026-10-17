"""What opening a dataset needs from each well, without holding its rows.

Opening used to read every well's full frame into the row cache, because the
open itself needs a few facts from every well: which timepoints and fields of
view exist (the menus every tab offers), the timepoint and FOV tokens the image
grids list when ``pipeline_info.json`` names none, which columns exist (focus,
channel detection), and whether the well's FOV labels can be trusted. With the
row cache bounded (``review/data/row_cache.py``), a plate larger than the budget
cannot be read whole to answer those, so they are kept here instead — a
:class:`WellSummary` per well, a few hundred bytes.

A summary is computed from the well's own parse and stored beside the Parquet
cache as ``<well>.summary.json``, under the same source fingerprint
(:func:`parquet_cache.source_fingerprint`, which carries the parser version) and
:data:`SUMMARY_VERSION`. A rewritten CSV or a changed parser therefore
recomputes it, exactly as it re-parses the frame. On a miss with the Parquet
cache present, only the columns a summary reads are loaded; with neither, the
CSV is parsed (which also fills the Parquet cache, so the parse is paid once).

The fields are the ones the open's readers used to take from the cached frames,
computed the same way: ``DatasetState.rebuild_timepoints`` / ``rebuild_fovs``,
``dataset_dims.cache_column_tokens`` and ``fov_integrity.check_fov_labels``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from domain.text_columns import text_column, timepoint_text
from review.data.image_resolver import normalize_numeric_token
from review.domain.fov_integrity import FovIntegrity, check_fov_labels

_logger = logging.getLogger("review")

#: Bumped whenever :func:`summarize_frame` would describe the same frame differently.
#: 2: ``nucleus_id`` joined :data:`SUMMARY_COLUMNS`; a version-1 summary taken from
#: the column subset judged the FOV labels without it (see below).
SUMMARY_VERSION = 2

#: The columns a summary reads, for a subset read of the Parquet cache: what the
#: timepoint and FOV lists read, and **everything** ``fov_integrity.
#: check_fov_labels`` reads — ``filename`` for its first signal, ``nucleus_id``
#: for its second (one nucleus id appearing twice in a field and timepoint). A
#: verdict judged on fewer columns answers "trusted" for a well that is not, and
#: the summary stores that under the CSV's fingerprint; ``tests/
#: test_opening_reads_what_fits.py`` holds the subset read to the full frame's
#: answer on a well only the second signal catches.
SUMMARY_COLUMNS = ("timepoint_hours", "timepoint", "fov", "filename", "nucleus_id")

#: Columns the projectors add to a loaded frame. A summary describes the parse,
#: so a summary taken from a projected frame leaves them out.
PROJECTED_COLUMNS = frozenset({
    "QC_Pass", "Crop_Pass", "Gate_Pass", "Gate_On", "Field_Pass", "Focus_Pass", "Border_Pass",
    "Excluded_Field_Pass",
})


@dataclass(frozen=True)
class WellSummary:
    """One well's open-time facts. See the module docstring."""

    n_rows: int = 0
    columns: tuple[str, ...] = ()
    #: Numeric timepoints in hours: ``timepoint_hours``, else the ``timepoint``
    #: label parsed, exactly as ``DatasetState.rebuild_timepoints`` reads them.
    timepoints: tuple[float, ...] = ()
    #: Normalised FOV tokens, in order of first appearance.
    fovs: tuple[str, ...] = ()
    #: Normalised ``timepoint`` label tokens, in order of first appearance.
    timepoint_tokens: tuple[str, ...] = ()
    fov_verdict: FovIntegrity = field(default_factory=lambda: FovIntegrity(trusted=True))

    def tokens(self, column: str) -> tuple[str, ...]:
        """The normalised tokens of ``fov`` or ``timepoint``, in order of appearance."""
        if column == "fov":
            return self.fovs
        if column == "timepoint":
            return self.timepoint_tokens
        raise KeyError(column)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> WellSummary:
        return cls(
            n_rows=int(data["n_rows"]),
            columns=tuple(data["columns"]),
            timepoints=tuple(float(t) for t in data["timepoints"]),
            fovs=tuple(data["fovs"]),
            timepoint_tokens=tuple(data["timepoint_tokens"]),
            fov_verdict=FovIntegrity(**data["fov_verdict"]),
        )


def _ordered_tokens(values) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        token = normalize_numeric_token(value)
        if token:
            seen.setdefault(token, None)
    return tuple(seen)


def summarize_frame(df: pd.DataFrame, columns=None) -> WellSummary:
    """A :class:`WellSummary` of *df*; *columns* names the full frame's columns when *df* is a subset."""
    from review.data.data_loading import parse_timepoint_hours

    if df is None:
        return WellSummary()
    names = tuple(c for c in (columns if columns is not None else df.columns) if c not in PROJECTED_COLUMNS)
    timepoints: set[float] = set()
    if len(df):
        if "timepoint_hours" in df.columns:
            tp_num = pd.to_numeric(df["timepoint_hours"], errors="coerce")
            timepoints.update(float(t) for t in tp_num.dropna().unique())
            missing = tp_num.isna()
        else:
            missing = pd.Series(True, index=df.index)
        if "timepoint" in df.columns and missing.any():
            for label in timepoint_text(df.loc[missing]).unique():
                parsed = parse_timepoint_hours(label)
                if parsed is not None:
                    timepoints.add(parsed)
    fovs = _ordered_tokens(text_column(df, "fov").unique()) if "fov" in df.columns and len(df) else ()
    tp_tokens = _ordered_tokens(df["timepoint"].dropna().unique()) if "timepoint" in df.columns else ()
    return WellSummary(
        n_rows=int(len(df)),
        columns=names,
        timepoints=tuple(sorted(timepoints)),
        fovs=fovs,
        timepoint_tokens=tp_tokens,
        fov_verdict=check_fov_labels(df),
    )


def summary_path(csv_path: Path) -> Path:
    from review.data.parquet_cache import cache_path

    return cache_path(csv_path).with_suffix(".summary.json")


def _stamp(fingerprint: str) -> str:
    return f"{fingerprint}:s{SUMMARY_VERSION}"


def read_summary(csv_path: Path) -> WellSummary | None:
    """The stored summary for *csv_path* if it still describes the file, else ``None``."""
    from review.data.parquet_cache import source_fingerprint

    fingerprint = source_fingerprint(csv_path)
    if fingerprint is None:
        return None
    try:
        data = json.loads(summary_path(csv_path).read_text())
        if not isinstance(data, dict) or data.get("fingerprint") != _stamp(fingerprint):
            return None
        return WellSummary.from_json(data["summary"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Any unreadable summary is a miss, recomputed — never a failed open.
        return None


def write_summary(csv_path: Path, summary: WellSummary, fingerprint: str | None) -> bool:
    """Store *summary* for *csv_path*, stamped with *fingerprint* (taken before the read).

    Declines, like ``parquet_cache.write_cache``, when the file has changed
    since *fingerprint* was taken: a summary of the old contents stamped with
    the new file's identity would be served as current for ever. A failure to
    write costs a recompute next open and is never raised.
    """
    from review.data.parquet_cache import source_fingerprint

    current = source_fingerprint(csv_path)
    if fingerprint is None or current != fingerprint:
        return False
    path = summary_path(csv_path)
    tmp = path.with_name(f"__aw_tmp_{path.name}.pid{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"fingerprint": _stamp(fingerprint), "summary": summary.to_json()}))
        os.replace(tmp, path)
        return True
    except OSError as exc:
        _logger.debug("could not store the summary of %s (%s); it is recomputed on the next open",
                      Path(csv_path).name, exc)
        try:
            tmp.unlink()
        except OSError:
            _logger.debug("no staging file to remove for %s", Path(csv_path).name)
        return False


def well_summary(csv_path: Path) -> WellSummary:
    """*csv_path*'s summary: stored, else from a column read of the Parquet cache, else from a parse."""
    from review.data.data_loading import read_well_frame
    from review.data.parquet_cache import cached_column_names, read_cached, source_fingerprint

    stored = read_summary(csv_path)
    if stored is not None:
        return stored
    fingerprint = source_fingerprint(csv_path)
    names = cached_column_names(csv_path)
    subset = read_cached(csv_path, columns=list(SUMMARY_COLUMNS)) if names is not None else None
    if subset is not None:
        summary = summarize_frame(subset, columns=names)
    else:
        summary = summarize_frame(read_well_frame(Path(csv_path)))
    write_summary(csv_path, summary, fingerprint)
    return summary


def well_summaries(dataset) -> list[WellSummary]:
    """A summary for every well of *dataset*: the stored one, else one of its held frame.

    Every well, not every held frame: the row cache is bounded, so the plate's
    timepoints and FOVs come from the summaries ``WellRepository.open_well``
    records. A well that reached the cache some other way — a test seeding it
    with frames it built — is summarised from its frame, so nothing held is
    missed. Reads ``well_paths`` / ``summaries`` defensively, for the hand-built
    dataset namespaces the pure-logic tests pass.
    """
    stored = getattr(dataset, "summaries", None) or {}
    cache = getattr(dataset, "cache", None) or {}
    out: list[WellSummary] = []
    for label in dict.fromkeys([*(getattr(dataset, "well_paths", None) or {}), *cache.keys()]):
        summary = stored.get(label)
        if summary is None:
            frame = cache.get(label) if label in cache else None
            if frame is None:
                continue
            summary = summarize_frame(frame)
        out.append(summary)
    return out
