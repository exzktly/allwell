"""Background worker that applies a global smFISH threshold across all wells.

Runs in a daemon thread; never touches Qt directly. Status updates are
delivered through the ``status_cb`` and ``done_cb`` callables (the smFISH
tab builder hands in functions that emit Qt signals).
"""

from __future__ import annotations

import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from well_viewer.persistence._io import atomic_write_text
from well_viewer.features.preview.controller import read_member_bytes
from well_viewer.features.smfish.controller import (
    SmfishImgRef,
    normalize_id,
    normalize_well_token,
    scan_well_zip,
)


logger = logging.getLogger("smfish_tab")


def _process_well(
    *,
    well: str,
    zip_path: Path,
    channel: str,
    threshold: float,
    classifier,
    fov_tp_extractor,
) -> dict[tuple[str, str, str, str], int]:
    """Compute per-cell smFISH spot counts for one well."""
    smfish, mask = scan_well_zip(
        zip_path=zip_path,
        channel=channel,
        classifier=classifier,
        fov_tp_extractor=fov_tp_extractor,
    )
    from tifffile import imread

    counts: dict[tuple[str, str, str, str], int] = {}
    for key in sorted(set(smfish).intersection(mask)):
        sm_ref = smfish[key]
        mk_ref = mask[key]
        sm_raw = read_member_bytes(zip_path=sm_ref.zip_path, member=sm_ref.zip_member, logger=logger)
        mk_raw = read_member_bytes(zip_path=mk_ref.zip_path, member=mk_ref.zip_member, logger=logger)
        if sm_raw is None or mk_raw is None:
            continue
        log_img = imread(io.BytesIO(sm_raw)).astype(np.float32)
        labels = imread(io.BytesIO(mk_raw))
        hits = labels[(labels > 0) & (log_img > threshold)].astype(np.int64, copy=False)
        if hits.size:
            hit_counts = np.bincount(hits)
            for nid in np.nonzero(hit_counts)[0]:
                counts[(well, key[0], key[1], str(int(nid)))] = int(hit_counts[nid])
    return counts


def _write_counts_to_csvs(
    out_dir: Path,
    well_to_zip: dict[str, Path],
    counts: dict[tuple[str, str, str, str], int],
    column: str,
) -> None:
    for well in sorted(well_to_zip):
        csv_matches = list(out_dir.glob(f"*_{well}.csv"))
        if not csv_matches:
            continue
        if len(csv_matches) > 1:
            logger.warning(
                "Multiple CSV candidates for well %s in %s: %s — skipping to "
                "avoid writing to the wrong file. Resolve the ambiguity by "
                "removing or renaming duplicates.",
                well, out_dir, [p.name for p in csv_matches],
            )
            continue
        csv_path = csv_matches[0]
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)

        well_series = (df["well"] if "well" in df.columns
                       else pd.Series([""] * len(df), index=df.index)).fillna("")
        r_well = well_series.where(well_series != "", well).map(normalize_well_token)
        fov_raw = (df.get("fov", df.get("FOV", pd.Series([""] * len(df), index=df.index)))
                   .fillna("").astype(str))
        fov = fov_raw.map(normalize_id)
        tp_raw_cols = [c for c in ("timepoint", "tp", "time") if c in df.columns]
        if tp_raw_cols:
            tp_raw = df[tp_raw_cols[0]]
            for c in tp_raw_cols[1:]:
                tp_raw = tp_raw.where(tp_raw.fillna("") != "", df[c])
        else:
            tp_raw = pd.Series([""] * len(df), index=df.index)
        tp = tp_raw.fillna("").astype(str).map(normalize_id)
        nid = ((df["nucleus_id"] if "nucleus_id" in df.columns
                else pd.Series([""] * len(df), index=df.index))
               .fillna("").astype(str).str.strip())

        keys = list(zip(r_well, fov, tp, nid))
        df[column] = [str(counts.get(k, 0)) for k in keys]

        # Atomic write — a concurrent Review-CSV read or a crash mid-write
        # must not be able to observe a truncated CSV.
        atomic_write_text(csv_path, df.to_csv(index=False))


def apply_global_threshold_async(
    *,
    out_dir: Path,
    well_to_zip: dict[str, Path],
    channel: str,
    threshold: float,
    classifier,
    fov_tp_extractor,
    status_cb: Callable[[str], None],
    done_cb: Callable[[str], None],
    after_csv_cb: Callable[[], None] | None = None,
    cancel_event: threading.Event | None = None,
    expected_data_dir: Path | None = None,
) -> threading.Thread:
    """Launch the apply-to-all worker on a daemon thread.

    ``cancel_event`` (when supplied) is polled between wells and before
    the write loop so a Cancel button / dataset-swap / app-shutdown can
    abort the in-flight run before it overwrites CSVs.

    ``expected_data_dir`` is the data directory the request was issued
    against. If, by the time the writer reaches the CSV phase, the
    caller has swapped to a different dataset, we refuse to write —
    the wrong dataset's CSVs would otherwise receive these counts.
    """

    column = f"{channel}_smfish_count"
    out_dir = Path(out_dir)
    expected = Path(expected_data_dir) if expected_data_dir is not None else out_dir

    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def _run() -> None:
        if not channel:
            status_cb("Select channel and ensure one well is selected.")
            return
        counts: dict[tuple[str, str, str, str], int] = {}
        wells = sorted(well_to_zip.items())
        if wells:
            max_workers = min(8, len(wells))
            status_cb(
                f"Applying global threshold across {len(wells)} wells using {max_workers} workers..."
            )
            completed = 0
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_well = {
                    executor.submit(
                        _process_well,
                        well=well,
                        zip_path=zip_path,
                        channel=channel,
                        threshold=threshold,
                        classifier=classifier,
                        fov_tp_extractor=fov_tp_extractor,
                    ): well
                    for well, zip_path in wells
                }
                for future in as_completed(future_to_well):
                    well = future_to_well[future]
                    completed += 1
                    try:
                        counts.update(future.result())
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("smFISH global threshold failed for %s: %s", well, exc)
                    status_cb(f"Processed {well} ({completed}/{len(wells)})...")
                    if _cancelled():
                        status_cb("Apply to All cancelled.")
                        done_cb("Apply to All cancelled before CSV write.")
                        return

        if _cancelled():
            status_cb("Apply to All cancelled.")
            done_cb("Apply to All cancelled before CSV write.")
            return
        # Verify the data directory hasn't changed under us. The
        # writer rewrites per-well CSVs in `out_dir`; if the caller
        # swapped to a different dataset while the worker was
        # running, those CSVs belong to the wrong dataset.
        if expected != out_dir or not out_dir.exists():
            done_cb(
                "Apply to All aborted: target directory changed since "
                "the run was started."
            )
            return
        _write_counts_to_csvs(out_dir, well_to_zip, counts, column)
        if after_csv_cb is not None:
            after_csv_cb()
        done_cb("Apply to All complete. Line/Bar plots refreshed.")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
