"""Open a synthetic plate of large wells and record the time and peak memory it costs.

The report behind the large-dataset tracker (in the git history of
``_Docs/Markdowns/LARGE_DATASET_TRACKER.md``): a plate of 300 MB
well CSVs (about 1.1M cells each) died on open with a ``MemoryError``. This
measures that case directly rather than asserting it fixed — the open, through
the real ``ReviewApp._load_path``, and then the per-well aggregation a line plot
over every well performs (``stats_compute.aggregate_well_cached``), twice, so the
second pass shows what the memo saves.

Each run is its own process, because peak resident memory (``ru_maxrss``) is a
high-water mark that never falls: two measurements in one process report the
first one's peak as the second's. Run it once per configuration::

    python tests/perf/bench_large_plate.py --dir /tmp/plate --wells 24 --rows 1104480
    ALLWELL_ROW_CACHE_MB=2048 python tests/perf/bench_large_plate.py --dir /tmp/plate --wells 24

The plate is written once, on first use: one well CSV, hard-linked under each
well's name, so a plate of 24 large wells costs one CSV on disk (plus the Parquet
cache and summaries each well gets on first open). Not part of any test lane —
it takes minutes and gigabytes on purpose.

Prints one JSON line: wells, rows per well, the budget, how many wells the open
held, and seconds and peak MiB at each stage.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Measurement columns beyond the two fluorescence channels, to reach the
#: report's width (6 text + 22 numeric columns).
_EXTRA_NUMERIC = 18


def _peak_mib() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (2**20 if sys.platform == "darwin" else 2**10)


def _write_well(path: Path, rows: int) -> None:
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(0)
    fov = rng.integers(1, 26, rows).astype(str)
    hours = rng.choice(np.arange(0, 72, 6), rows)
    frame = pd.DataFrame({
        "experiment": "bench", "well": "A01", "fov": fov,
        "timepoint": [f"{h}h" for h in hours], "channel": "dapi",
        "filename": [f"bench_A01_f{f}_t{h}h.tif" for f, h in zip(fov, hours)],
        "nucleus_id": np.arange(1, rows + 1),
        "timepoint_hours": hours.astype(float),
        "area_px": rng.uniform(50, 400, rows),
        "gfp_mean_intensity": rng.lognormal(5, 1, rows),
        "mcherry_mean_intensity": rng.lognormal(4, 1, rows),
    })
    for i in range(_EXTRA_NUMERIC):
        frame[f"m{i}"] = rng.normal(size=rows)
    frame.to_csv(path, index=False)


def _well_names(n: int) -> list[str]:
    return [f"{row}{col:02d}" for row in "ABCDEFGH" for col in range(1, 13)][:n]


def build_plate(directory: Path, wells: int, rows: int) -> None:
    """Write the plate if it is not there: one CSV, hard-linked per well."""
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "_source_well.csv.data"
    if not source.exists():
        _write_well(source, rows)
    for well in _well_names(wells):
        target = directory / f"bench_{well}.csv"
        if not target.exists():
            os.link(source, target)
    info = directory / "pipeline_info.json"
    if not info.exists():
        info.write_text(json.dumps({"schema": "bench", "separator": "_", "fluor_tokens": ["gfp", "mcherry"],
                                    "nuclear_token": "dapi"}))


def run(directory: Path) -> dict:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from review.domain.stats_compute import aggregate_well_cached
    from review.runtime_app import ReviewApp

    QApplication.instance() or QApplication([])
    app = ReviewApp()
    out: dict = {}
    started = time.perf_counter()
    app._load_path(directory)
    out["open_s"] = round(time.perf_counter() - started, 1)
    out["open_peak_mib"] = round(_peak_mib())
    dataset = app._app_state.dataset
    out["wells"] = len(dataset.well_paths)
    out["held_after_open"] = len(dataset.cache)
    out["budget_mib"] = round(getattr(dataset.cache, "budget_bytes", 0) / 2**20)
    ctx = app._ctx
    for stage in ("plot_first", "plot_again"):
        started = time.perf_counter()
        for label in list(dataset.well_paths):
            aggregate_well_cached(ctx, label, threshold=100.0, use_sem=True,
                                  val_col="gfp_mean_intensity", fluor_gates={})
        out[f"{stage}_s"] = round(time.perf_counter() - started, 1)
        out[f"{stage}_peak_mib"] = round(_peak_mib())
    out["held_after_plot"] = len(dataset.cache)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--wells", type=int, default=24)
    parser.add_argument("--rows", type=int, default=1_104_480)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    build_plate(args.dir, args.wells, args.rows)
    if args.build_only:
        return 0
    result = {"rows_per_well": args.rows, **run(args.dir)}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
