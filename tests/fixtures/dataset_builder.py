"""Synthetic in/out dataset builder shared by the Datasets-pane feature tests.

Extracted from ``tests/test_concatenate_compute.py``'s local ``_make_dataset``
(Concatenate, Validate, Subset, Import loose and Merge plates all need the same
shape: per-well ``in/<WELL>.zip`` inputs, ``out/<WELL>_out.zip`` QC archives, a
``measurements_<WELL>.csv`` per well, and an ``out/pipeline_info.json``
sidecar). Fixture CSVs use the **real** column shape ``process_microscopy.py``'s
``parse_filename`` actually writes (``filename, experiment, channel, well, fov,
timepoint, timepoint_hours, nucleus_id, area_px``) — a thinner fixture would
exercise a CSV shape that never occurs on disk and would hide a
``timepoint_hours``/``filename`` staleness bug.

Not to be confused with ``tests/fixtures/mini_dataset.py``, which fabricates a
*flat* layout (no ``in``/``out`` split, no zips) for Review/``WellViewerApp``
tests — that shape is not a dataset Concatenate/Validate/Subset/Import
loose/Merge plates can open.
"""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

from util.timepoint import parse_hours

BASE_INFO = {
    "schema": "experiment:channel:well:fov:timepoint",
    "separator": "_",
    "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
    "well_index": 2,
    "channel_index": 1,
    "fov_index": 3,
    "tp_index": 4,
    "nuclear_token": "nir",
    "segmentation_method": "stardist_nuclei",
    "fluor_tokens": ["nir", "gfp"],
    "smfish_tokens": [],
    "shape_features": [],
}

ROW_COLUMNS = [
    "filename", "experiment", "channel", "well", "fov",
    "timepoint", "timepoint_hours", "nucleus_id", "area_px",
]


def build_dataset(
    root: Path,
    *,
    wells,
    timepoints,
    exp: str = "exp",
    csv_prefix: str = "measurements",
    fovs=("F001",),
    info_extra=None,
    columns=ROW_COLUMNS,
) -> Path:
    """Build a dataset (``in/`` zips, ``out/`` QC zips + measurements CSV +
    sidecar) whose CSV matches the real pipeline's column shape.

    ``csv_prefix`` and ``fovs`` are the two knobs a healthy-dataset fixture
    needs beyond what Concatenate ever exercised: Validate needs a
    non-default ``--csv_prefix`` dataset to test its mixed-prefix finding, and
    Subset needs more than one FOV to filter on.
    """
    in_dir = root / "in"
    out_dir = root / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    info = dict(BASE_INFO)
    info["available_timepoints"] = list(timepoints)
    info["available_fovs"] = list(fovs)
    if info_extra:
        info.update(info_extra)
    (out_dir / "pipeline_info.json").write_text(json.dumps(info))

    for well in wells:
        with zipfile.ZipFile(in_dir / f"{well}.zip", "w") as zf:
            for fov in fovs:
                for tp in timepoints:
                    zf.writestr(f"{exp}_GFP_{well}_{fov}_{tp}.tif", b"img")
        with zipfile.ZipFile(out_dir / f"{well}_out.zip", "w") as zf:
            for fov in fovs:
                for tp in timepoints:
                    zf.writestr(f"{exp}_GFP_{well}_{fov}_{tp}_labels.tif", b"mask")
                    zf.writestr(f"{exp}_GFP_{well}_{fov}_{tp}_tophat_gfp.tif", b"tophat")
        with (out_dir / f"{csv_prefix}_{well}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for fov in fovs:
                for tp in timepoints:
                    row = {
                        "filename": f"{exp}_GFP_{well}_{fov}_{tp}.tif", "experiment": exp,
                        "channel": "GFP", "well": well, "fov": fov, "timepoint": tp,
                        "timepoint_hours": parse_hours(tp), "nucleus_id": 1, "area_px": 150,
                    }
                    writer.writerow({k: v for k, v in row.items() if k in columns})
    return root
