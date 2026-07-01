"""Deterministic miniature dataset for headless ``WellViewerApp`` tests.

A real analysis output directory is a folder of per-well measurement CSVs
plus a ``pipeline_info.json`` sidecar. Rather than commit four near-identical
CSV files (and keep them in sync by hand), this module *generates* the dataset
into a caller-supplied directory. One generator, no duplicated fixture data.

The numbers are a closed-form function of (well, timepoint, cell) so tests can
assert exact values — see ``cell_gfp`` / ``cell_mcherry`` below.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

WELLS = ["A01", "A02", "B01", "B02"]
TIMEPOINTS = [("0h", 0.0), ("24h", 24.0), ("48h", 48.0)]
CELLS_PER_TP = 6
FLUOR_CHANNELS = ["gfp", "mcherry"]
NUCLEAR_TOKEN = "dapi"

_COLUMNS = [
    "well", "fov", "timepoint", "timepoint_hours", "channel",
    "nucleus_id", "area_px",
    "gfp_mean_intensity", "mcherry_mean_intensity", "dapi_mean_intensity",
]

# Optional per-cell shape-feature columns (emitted when ``with_shape=True``),
# mirroring the catalog in ``shape_features_core``. Includes the always-on
# centroids so the dataset matches a real shape-features pipeline run.
_SHAPE_COLUMNS = [
    "shape_area", "shape_eccentricity", "shape_solidity",
    "shape_centroid_row", "shape_centroid_col",
]


def _well_index(well: str) -> int:
    return WELLS.index(well)


def cell_gfp(well: str, tp_hours: float, cell: int) -> float:
    """Closed-form GFP value for one cell — deterministic, assertable."""
    return 100.0 + _well_index(well) * 10.0 + tp_hours * 2.0 + cell * 1.0


def cell_mcherry(well: str, tp_hours: float, cell: int) -> float:
    return 50.0 + _well_index(well) * 5.0 + tp_hours * 1.0 + cell * 0.5


def cell_dapi(well: str, tp_hours: float, cell: int) -> float:
    return 300.0 + _well_index(well) * 2.0 + cell * 1.0


def cell_area_px(cell: int) -> float:
    return 200.0 + cell * 10.0


def _rows_for_well(well: str, *, with_shape: bool = False, n_fovs: int = 1) -> list[dict]:
    rows: list[dict] = []
    for tp_label, tp_hours in TIMEPOINTS:
        for fov in range(1, n_fovs + 1):
            for cell in range(1, CELLS_PER_TP + 1):
                # A per-FOV offset so per-FOV means differ (finite p when FOVs
                # are the replicates) while staying a closed-form function.
                fov_bump = (fov - 1) * 3.0
                row = {
                    "well": well,
                    "fov": str(fov),
                    "timepoint": tp_label,
                    "timepoint_hours": tp_hours,
                    "channel": NUCLEAR_TOKEN,
                    "nucleus_id": cell,
                    "area_px": cell_area_px(cell),
                    "gfp_mean_intensity": round(cell_gfp(well, tp_hours, cell) + fov_bump, 4),
                    "mcherry_mean_intensity": round(cell_mcherry(well, tp_hours, cell) + fov_bump, 4),
                    "dapi_mean_intensity": round(cell_dapi(well, tp_hours, cell), 4),
                }
                if with_shape:
                    row.update({
                        "shape_area": round(150.0 + cell * 5.0, 4),
                        "shape_eccentricity": round(0.1 * cell, 4),
                        "shape_solidity": 0.9,
                        "shape_centroid_row": float(cell * 2),
                        "shape_centroid_col": float(cell * 3),
                    })
                rows.append(row)
    return rows


def build_mini_dataset(dest: Path, *, with_shape: bool = False, n_fovs: int = 1) -> Path:
    """Materialise the mini dataset into *dest* and return it.

    Writes one ``mini_<WELL>.csv`` per well (the trailing well token is what
    ``load_controller._extract_well_token`` keys on) plus ``pipeline_info.json``.
    When *with_shape* is true the per-cell shape-feature columns
    (:data:`_SHAPE_COLUMNS`) are appended, matching a shape-features pipeline run.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    columns = _COLUMNS + (_SHAPE_COLUMNS if with_shape else [])
    for well in WELLS:
        path = dest / f"mini_{well}.csv"
        with path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            writer.writerows(_rows_for_well(well, with_shape=with_shape, n_fovs=n_fovs))

    pipeline_info = {
        "schema": "mini",
        "separator": "_",
        "fluor_tokens": FLUOR_CHANNELS,
        "nuclear_token": NUCLEAR_TOKEN,
        "available_timepoints": [label for label, _ in TIMEPOINTS],
        "fov_index": -1,
        "tp_index": 2,
    }
    (dest / "pipeline_info.json").write_text(json.dumps(pipeline_info, indent=2))
    return dest


if __name__ == "__main__":
    import sys
    out = build_mini_dataset(Path(sys.argv[1] if len(sys.argv) > 1 else "mini_dataset"))
    print(f"wrote mini dataset to {out}")
