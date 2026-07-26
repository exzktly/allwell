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
from collections.abc import Callable

import numpy as np
import pandas as pd

from well_viewer.data.image_identity import (
    CellKey, cell_keys_for_dataframe,
    normalize_fov, normalize_nucleus_id, normalize_tp, normalize_well,
)
from well_viewer.persistence._io import atomic_write_text
from well_viewer.features.preview.controller import read_member_bytes
from well_viewer.features.smfish.controller import (
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
            # Build the counts dict's keys via CellKey so the producer
            # side matches the consumer side (CSV lookup) byte-for-byte:
            # same well/fov/tp/nid normalisation, no possibility of drift.
            # Normalise the filename-derived tokens via the same rules
            # CellKey.from_row uses on the CSV side — that's the whole
            # point of the unified module: producer and consumer keys
            # go through identical normalisation, no possibility of drift.
            fov_n = normalize_fov(key[0])
            tp_n = normalize_tp(key[1])
            for nid in np.nonzero(hit_counts)[0]:
                k = CellKey(
                    well=normalize_well(well),
                    fov=fov_n,
                    tp=tp_n,
                    nid=normalize_nucleus_id(int(nid)),
                )
                counts[k.as_tuple()] = int(hit_counts[nid])
    return counts


def _resolve_csv_path(
    well: str,
    well_to_csv: dict[str, Path] | None,
    out_dir: Path,
) -> Path | None:
    """Resolve the per-well CSV path for *well*. Prefers the explicit
    ``well_to_csv`` map (the same one the app's loader populated) so a
    silent glob mismatch can't drop wells whose CSVs are named
    ``<well>.csv`` rather than ``<prefix>_<well>.csv``. Falls back to
    the legacy ``*_{well}.csv`` glob only when the map is unavailable."""
    if well_to_csv is not None:
        path = well_to_csv.get(well)
        if path is None:
            return None
        return Path(path)
    csv_matches = list(out_dir.glob(f"*_{well}.csv"))
    if not csv_matches:
        return None
    if len(csv_matches) > 1:
        logger.warning(
            "Multiple CSV candidates for well %s in %s: %s — skipping to "
            "avoid writing to the wrong file. Resolve the ambiguity by "
            "removing or renaming duplicates.",
            well, out_dir, [p.name for p in csv_matches],
        )
        return None
    return csv_matches[0]


def _drop_case_equivalent_columns(df: pd.DataFrame, target: str) -> pd.DataFrame:
    """Remove any column whose lowercase form equals *target* before the
    writer reassigns *target*. Without this an upstream CSV that used
    e.g. ``W3Cy5_smfish_count`` would survive alongside the freshly
    written ``w3cy5_smfish_count`` — readers that case-fold then hit a
    duplicate-column collision, and case-sensitive readers keep seeing
    the stale pre-apply counts."""
    target_lc = target.lower()
    drop = [c for c in df.columns
            if str(c).lower() == target_lc and c != target]
    if drop:
        df = df.drop(columns=drop)
    return df


def _write_counts_to_csvs(
    out_dir: Path,
    well_to_zip: dict[str, Path],
    counts: dict[tuple[str, str, str, str], int],
    column: str,
    well_to_csv: dict[str, Path] | None = None,
    should_abort: Callable[[], bool] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> int:
    """Write the counts column into each well's CSV.

    ``should_abort`` (when supplied) is re-checked at the top of every
    iteration so a dataset swap / cancel that lands *mid-loop* stops further
    writes into the now-wrong dataset (audit E19: the identity check only ran
    once, before the loop). Returns the number of wells actually written.

    ``progress_cb`` receives ``(wells_visited, total_wells)`` after each
    iteration so the caller can drive a determinate progress bar. It counts
    wells *visited*, not wells written — a skipped or unresolvable CSV still
    advances the bar, because the bar tracks how far through the loop we are.
    """
    ordered = sorted(well_to_zip)
    total = len(ordered)
    if progress_cb is not None:
        progress_cb(0, total)
    written = 0
    for visited, well in enumerate(ordered, 1):
        if should_abort is not None and should_abort():
            logger.warning(
                "smFISH apply-global: dataset changed or run cancelled mid-write "
                "after %d well(s); stopping to avoid writing into the wrong "
                "dataset.", written,
            )
            break
        # The bar advances on every exit from this iteration — including the
        # `continue`s below — so a run with unresolvable or locked CSVs still
        # finishes with a full bar instead of one stuck short of its maximum.
        try:
            csv_path = _resolve_csv_path(well, well_to_csv, out_dir)
            if csv_path is None:
                continue
            df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
            df = _drop_case_equivalent_columns(df, column)

            # Build lookup keys via the single source of truth — CellKey applies
            # the same well/fov/tp/nid normalisation chain the producer side
            # uses, so the (well, fov, tp, nid) tuples in `counts` and the CSV
            # lookup keys are constructed by the same code path.
            cell_keys = cell_keys_for_dataframe(df, default_well=well)
            keys = [ck.as_tuple() for ck in cell_keys]
            df[column] = [str(counts.get(k, 0)) for k in keys]

            # Diagnostic: when every lookup misses we'd otherwise silently
            # write a column of zeros and only the file mtime would change.
            # Surface this so the next drift mode (e.g. nucleus_id format,
            # tp parser drift) reports itself without code-reading.
            matched = sum(1 for k in keys if k in counts)
            if matched == 0 and len(keys) > 0 and len(counts) > 0:
                sample_csv = keys[:3]
                sample_counts = list(counts.keys())[:3]
                logger.warning(
                    "smFISH apply-global: %s wrote 0 matched rows out of %d for "
                    "column %r. Sample CSV lookup keys=%s ; sample counts keys=%s "
                    "— likely a (fov, timepoint, nucleus_id) format drift between "
                    "the per-well CSV and the *_out.zip filenames.",
                    csv_path.name, len(keys), column, sample_csv, sample_counts,
                )

            # Atomic write — a concurrent Review-CSV read or a crash mid-write
            # must not be able to observe a truncated CSV. PermissionError
            # is wrapped per-well so one locked file (Excel, AV, OneDrive)
            # doesn't kill the rest of the run; the user still gets a clear
            # WARN naming the well + the underlying cause.
            try:
                atomic_write_text(csv_path, df.to_csv(index=False))
            except PermissionError as exc:
                logger.warning(
                    "smFISH apply-global: could not write %s — %s. The "
                    "atomic-write helper retried for ~3s and the file is "
                    "still locked; likely Excel / a viewer has the CSV open, "
                    "or antivirus is mid-scan. Other wells will still be "
                    "written. Close the file and retry Apply Global Threshold.",
                    csv_path, exc,
                )
                continue
            except OSError as exc:
                logger.warning(
                    "smFISH apply-global: write failed for %s — %s. "
                    "Skipping this well; other wells will still be written.",
                    csv_path, exc,
                )
                continue
            written += 1
        finally:
            if progress_cb is not None:
                progress_cb(visited, total)
    return written


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
    progress_cb: Callable[[int, int], None] | None = None,
    after_csv_cb: Callable[[], None] | None = None,
    cancel_event: threading.Event | None = None,
    expected_data_dir: Path | None = None,
    well_to_csv: dict[str, Path] | None = None,
) -> threading.Thread:
    """Launch the apply-to-all worker on a daemon thread.

    ``progress_cb`` receives ``(done, total)`` for a determinate progress
    bar: once per well as the counting pass completes, then a second pass
    over the CSV-write loop (which re-reports from ``0`` against the same
    total). Like every other callback here it fires on the worker thread,
    so the caller must marshal it onto the GUI thread before it touches a
    widget.

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
            # Terminal signal, not just a status line: ``done_cb`` is what
            # clears the caller's busy flag and hides the progress bar. A bare
            # ``status_cb`` + return left the tab believing a run was still in
            # flight, so every later click was rejected as "already running".
            done_cb("Select channel and ensure one well is selected.")
            return
        counts: dict[tuple[str, str, str, str], int] = {}
        wells = sorted(well_to_zip.items())
        if wells:
            max_workers = min(8, len(wells))
            status_cb(
                f"Applying global threshold across {len(wells)} wells using {max_workers} workers..."
            )
            completed = 0
            if progress_cb is not None:
                progress_cb(0, len(wells))
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
                    except Exception as exc:
                        logger.exception("smFISH global threshold failed for %s: %s", well, exc)
                    status_cb(f"Processed {well} ({completed}/{len(wells)})...")
                    if progress_cb is not None:
                        progress_cb(completed, len(wells))
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

        # Re-check per-well inside the write loop: a dataset swap or cancel
        # that lands *after* the guard above but *during* the (potentially
        # slow, many-well) write loop would otherwise keep writing late wells
        # into the swapped dataset (audit E19 TOCTOU).
        def _abort_write() -> bool:
            return _cancelled() or expected != out_dir or not out_dir.exists()

        status_cb(f"Writing counts into {len(well_to_zip)} well CSV(s)...")
        _write_counts_to_csvs(
            out_dir, well_to_zip, counts, column, well_to_csv,
            should_abort=_abort_write,
            progress_cb=progress_cb,
        )
        if after_csv_cb is not None:
            after_csv_cb()
        done_cb("Apply to All complete. Line/Bar plots refreshed.")

    def _run_guarded() -> None:
        # A raise anywhere in _run (e.g. a pd.read_csv ParserError in
        # _write_counts_to_csvs) previously killed the daemon thread with
        # done_cb never firing, so "Apply Global Threshold" hung forever
        # (audit E8). Route any unhandled failure through done_cb so the UI
        # always gets a terminal signal.
        try:
            _run()
        except Exception as exc:
            logger.exception("smFISH Apply to All worker crashed")
            done_cb(f"Apply to All failed: {exc}")

    thread = threading.Thread(target=_run_guarded, daemon=True)
    thread.start()
    return thread
