"""Tracking post-pass worker — engine-agnostic CSV write-back (§D / PR5b).

A whole-well post-pass (unlike segmentation, which is per-FOV): each well's
per-cell CSV is read, tracked across timepoints by the selected engine adapter,
and the additive ``track_id`` / ``track_parent_id`` / ``tree_id`` columns are
written back atomically; the lineage graph is persisted to ``tracks.json``.

:func:`write_tracks_to_csvs` is the Qt-free core (used by tests, the GUI worker,
and a headless ``--track`` path). :func:`track_wells_async` is the daemon-thread
launcher that mirrors the smFISH worker's status/done/after_csv + cancel contract.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Dict, Optional

import pandas as pd

from well_viewer.persistence._io import atomic_write_text
from well_viewer.persistence.tracking import save_tracks
from .engines import DEFAULT_ENGINE, EngineParams
from .model import TRACK_COLUMNS
from .orchestrator import track_well

logger = logging.getLogger("well_viewer")


def _well_fingerprint(out_dir: Path, well: str) -> str:
    zip_path = out_dir / f"{well}_out.zip"
    try:
        return f"{zip_path.stat().st_mtime_ns}"
    except OSError:
        return ""


def _drop_case_equivalent_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop any pre-existing track columns (case-insensitive) before rewriting,
    so a re-run doesn't leave a stale duplicate-cased column behind."""
    targets = {c.lower() for c in TRACK_COLUMNS}
    drop = [c for c in df.columns if c.lower() in targets]
    return df.drop(columns=drop) if drop else df


def write_tracks_to_csvs(
    out_dir: Path,
    well_to_csv: Dict[str, Path],
    *,
    engine: str = DEFAULT_ENGINE,
    params: Optional[EngineParams] = None,
    status_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, dict]:
    """Track every well in *well_to_csv* and rewrite its CSV with the track
    columns. Returns ``{well: tracks_json_payload}`` (also written to
    ``tracks.json``). Qt-free.
    """
    out_dir = Path(out_dir)
    params = params or EngineParams()
    tracks_by_well: Dict[str, dict] = {}

    for well in sorted(well_to_csv):
        csv_path = Path(well_to_csv[well])
        if not csv_path.exists():
            continue
        try:
            df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
        except OSError as exc:
            logger.warning("Tracking: could not read %s — %s; skipping.", csv_path, exc)
            continue
        if df.empty:
            continue
        df = _drop_case_equivalent_columns(df)
        try:
            df, payload = track_well(
                df, well, engine=engine, params=params,
                fingerprints={well: _well_fingerprint(out_dir, well)},
            )
        except KeyError as exc:
            # Missing centroid columns (pre-§C dataset) — skip with a clear note.
            logger.warning("Tracking: %s lacks centroid columns (%s); skipping.", well, exc)
            continue
        n_tracks = int(df["track_id"].nunique()) - (1 if (df["track_id"] == -1).any() else 0)
        try:
            atomic_write_text(csv_path, df.to_csv(index=False))
        except OSError as exc:
            logger.warning("Tracking: write failed for %s — %s; skipping.", csv_path, exc)
            continue
        tracks_by_well[well] = payload
        if status_cb is not None:
            status_cb(f"Tracked {well}: {n_tracks} track(s).")

    if tracks_by_well:
        save_tracks(out_dir, tracks_by_well)
    return tracks_by_well


def track_wells_async(
    *,
    out_dir: Path,
    well_to_csv: Dict[str, Path],
    engine: str = DEFAULT_ENGINE,
    params: Optional[EngineParams] = None,
    status_cb: Callable[[str], None],
    done_cb: Callable[[str], None],
    after_csv_cb: Optional[Callable[[], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    expected_data_dir: Optional[Path] = None,
) -> threading.Thread:
    """Launch the tracking post-pass on a daemon thread (smFISH worker contract).

    ``cancel_event`` aborts before the write phase; ``expected_data_dir`` guards
    against a dataset swap mid-run (refuse to write the wrong dataset's CSVs).
    """
    out_dir = Path(out_dir)
    expected = Path(expected_data_dir) if expected_data_dir is not None else out_dir

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def _run() -> None:
        wells = sorted(well_to_csv)
        if not wells:
            done_cb("Tracking: no wells to process.")
            return
        status_cb(f"Tracking {len(wells)} well(s) with the {engine} engine…")
        if _cancelled():
            done_cb("Tracking cancelled.")
            return
        if expected != out_dir or not out_dir.exists():
            done_cb("Tracking aborted: target directory changed since the run started.")
            return
        try:
            tracks = write_tracks_to_csvs(
                out_dir, well_to_csv, engine=engine, params=params, status_cb=status_cb)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Tracking post-pass failed: %s", exc)
            done_cb(f"Tracking failed: {exc}")
            return
        if after_csv_cb is not None:
            after_csv_cb()
        done_cb(f"Tracking complete — {len(tracks)} well(s) tracked. Plots refreshed.")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
