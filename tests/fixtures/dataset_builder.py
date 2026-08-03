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


# What a well archive actually holds beyond its images, on a Mac or a share.
# The two sets are kept apart because the tools treat them differently: OS
# debris carries no data and no tool has any business reconciling two copies of
# it, while a file a user dropped in on purpose is real content whose name can
# genuinely conflict.
JUNK_MEMBERS = {
    ".DS_Store": b"\x00\x01junk",
    "__MACOSX/._sidecar": b"appledouble",
}
STRAY_MEMBERS = {
    "readme.txt": b"free text, parses to nothing",
}


def _add_junk(zf: zipfile.ZipFile, junk: bool, stray: bool) -> None:
    if junk:
        zf.writestr(zipfile.ZipInfo("thumbs/"), b"")
        for name, data in JUNK_MEMBERS.items():
            zf.writestr(name, data)
    if stray:
        for name, data in STRAY_MEMBERS.items():
            zf.writestr(name, data)


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
    junk=False,
    stray=False,
) -> Path:
    """Build a dataset (``in/`` zips, ``out/`` QC zips + measurements CSV +
    sidecar) whose CSV matches the real pipeline's column shape.

    ``csv_prefix`` and ``fovs`` are the two knobs a healthy-dataset fixture
    needs beyond what Concatenate ever exercised: Validate needs a
    non-default ``--csv_prefix`` dataset to test its mixed-prefix finding, and
    Subset needs more than one FOV to filter on.

    ``junk=True`` adds the operating-system debris a real well archive picks
    up — a ``.DS_Store``, a directory entry, an AppleDouble sidecar — and
    ``stray=True`` adds a file a user dropped in on purpose, whose name parses
    to nothing. Both are opt-in because most callers assert exact member
    counts, but any test of a *filter* wants them: a predicate that partitions
    an archive has to decide which members it is entitled to judge, and a
    corpus of nothing but well-formed TIFs cannot tell a correct answer from
    one that never faced the question. They are separate flags because the
    tools treat the two kinds differently — Concatenate skips OS debris rather
    than reconciling two copies of it, but a stray file's name collision is a
    real conflict it still refuses.
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
            _add_junk(zf, junk, stray)
        with zipfile.ZipFile(out_dir / f"{well}_out.zip", "w") as zf:
            for fov in fovs:
                for tp in timepoints:
                    zf.writestr(f"{exp}_GFP_{well}_{fov}_{tp}_labels.tif", b"mask")
                    zf.writestr(f"{exp}_GFP_{well}_{fov}_{tp}_tophat_gfp.tif", b"tophat")
            _add_junk(zf, junk, stray)
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
