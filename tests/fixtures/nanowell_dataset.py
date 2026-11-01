"""A small nanowell dataset written straight to disk, the way a nanowell run lays one out — without running one.

Detection and segmentation are not run (that is the nanowell end-to-end lane's business); every file is written
with the columns and names the pipeline writes (ARCHITECTURE §6.2i *How a nanowell dataset is laid out*):

``<root>/in/``
    ``<well>.zip`` holding one PHASE and one GFP tile per timepoint (``exp_<CH>_<well>_F0_<tp>.tif``) and
    ``acquisition.json``.
``<root>/out/``
    ``pipeline_info.json`` (``fluor_tokens`` lists PHASE *first*, as a nanowell run writes it, with a ``nanowell``
    block and an ``acquisition`` block carrying the pixel size), ``measurements_<well>.csv`` (the narrow nanowell
    cell table), ``<well>_out.zip`` holding only ``<well>_<tp>_montage.png``, and ``nanowells/<well>.csv`` +
    ``<well>_grid.json``.

The numbers are chosen so the answers a test asks about differ between the plausible wrong implementations
(CLAUDE.md §7.8 #2): a well's cell mean is not its nanowell median, the occupied-nanowell count is not the
nanowell count, and the per-nanowell fold change's median is not the fold change of the medians.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

#: The nanowells of every well, as (q, r).
SITES = ((0, 0), (1, 0), (0, 1), (-1, 1), (-1, 0))
TIMEPOINTS = ("0h", "24h")
PX_UM = 4.3
TILE_PX = 64

#: Cells per nanowell, per well, per timepoint (in ``SITES`` order).
COUNTS = {
    "A01": {"0h": [1, 2, 0, 4, 0], "24h": [3, 2, 0, 4, 5]},
    "B01": {"0h": [2, 1, 1, 0, 0], "24h": [2, 4, 1, 0, 0]},
}
#: GFP mean intensity of each cell of a nanowell, per well (in ``SITES`` order), constant over time.
GFP = {"A01": [10.0, 20.0, 30.0, 40.0, 50.0], "B01": [5.0, 15.0, 25.0, 35.0, 45.0]}


def _site_row(tp: str, q: int, r: int, n: int, gfp: float) -> dict:
    x, y = 100.0 + 20.0 * q, 100.0 + 20.0 * r
    return {"timepoint": tp, "nw_q": q, "nw_r": r, "nw_ring": max(abs(q), abs(r), abs(q + r)),
            "well_x_um": x, "well_y_um": y, "owner": "F0", "radius_um": 8.0, "geometry": "fitted",
            "detected": True, "fused": False, "imaged_fraction": 1.0, "visible_fraction": 1.0,
            "state": "measured" if n else "empty", "partially_visible": False, "focus": 1.0, "n_cells": n,
            "confluence": round(0.1 * n, 3), "area_est_cells": n, "count_disagreement": False,
            "consistency": "consistent", "GFP_integrated": gfp * n, "GFP_mean_bg": 0.0}


def _png(image: np.ndarray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(image.astype(np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def _tif(image: np.ndarray, scratch: Path) -> bytes:
    from tifffile import imwrite

    imwrite(str(scratch), image.astype("float32"))
    return scratch.read_bytes()


def build_nanowell_dataset(root: Path, *, counts: dict | None = None) -> Path:
    """Write the dataset under *root* and return *root* (the folder holding ``in/`` and ``out/``)."""
    from pipeline.nanowell_task import SCHEME_VERSION, write_sites_csv
    from util.acquisition_record import new_record, new_timepoint, write_record_file

    counts = counts or COUNTS
    root = Path(root)
    out, inp = root / "out", root / "in"
    (out / "nanowells").mkdir(parents=True)
    inp.mkdir(parents=True)
    rng = np.random.default_rng(0)
    record = new_record()
    for k, tp in enumerate(TIMEPOINTS):
        meta = new_timepoint(started_at=f"2026-03-0{1 + k}T09:00:00")
        meta.update(pixel_size_um={"x": PX_UM, "y": PX_UM}, tile_size_px={"x": TILE_PX, "y": TILE_PX})
        record["timepoints"][tp] = meta
    scratch = root / ".frame.tif"
    for well, by_tp in counts.items():
        sites, cells, blocks = [], [], {}
        for tp, per_site in by_tp.items():
            hours = float(tp.rstrip("h"))
            for (q, r), n, gfp in zip(SITES, per_site, GFP[well]):
                sites.append(_site_row(tp, q, r, n, gfp))
                for _ in range(n):
                    cells.append({"well": well, "fov": "F0", "timepoint": tp, "timepoint_hours": hours,
                                  "nucleus_id": len(cells) + 1, "area_px": 50.0, "PHASE_mean_intensity": 1.2,
                                  "GFP_mean_intensity": gfp, "nw_q": q, "nw_r": r,
                                  "nw_ring": max(abs(q), abs(r), abs(q + r)), "nw_label": f"{q}_{r}",
                                  "well_x_um": 100.0 + 20.0 * q, "well_y_um": 100.0 + 20.0 * r})
            blocks[tp] = {"status": "ok", "montage_origin_um": [0.0, 0.0], "montage_um_per_px": PX_UM,
                          "tiles": [{"tile": "F0", "left_um": 0.0, "top_um": 0.0, "source": "recorded",
                                     "residual_um": 0.0}]}
            record["tiles"].append({"well": well, "timepoint": tp, "field": "F0", "mosaic_xy_px": [0.0, 0.0],
                                    "stage_xy_um": None})
        write_sites_csv(out / "nanowells" / f"{well}.csv", sites, ["GFP"])
        (out / "nanowells" / f"{well}_grid.json").write_text(json.dumps(
            {"scheme_version": SCHEME_VERSION, "well": well, "timepoints": blocks}))
        pd.DataFrame(cells).to_csv(out / f"measurements_{well}.csv", index=False)
        with zipfile.ZipFile(out / f"{well}_out.zip", "w") as zf:
            for tp in by_tp:
                zf.writestr(f"{well}_{tp}_montage.png", _png(rng.integers(0, 255, (TILE_PX, TILE_PX))))
        with zipfile.ZipFile(inp / f"{well}.zip", "w") as zf:
            for tp in by_tp:
                for ch in ("PHASE", "GFP"):
                    zf.writestr(f"exp_{ch}_{well}_F0_{tp}.tif", _tif(rng.random((TILE_PX, TILE_PX)), scratch))
    scratch.unlink(missing_ok=True)
    write_record_file(inp / "acquisition.json", record)
    info = {
        "schema_version": 2, "schema": "experiment:channel:well:fov:timepoint", "separator": "_",
        "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
        "well_index": 2, "channel_index": 1, "fov_index": 3, "tp_index": 4,
        "nuclear_token": "PHASE", "phase_token": "PHASE", "fluor_tokens": ["PHASE", "GFP"], "smfish_tokens": [],
        "shape_features": [], "segmentation_method": "cellpose_labelfree",
        "available_timepoints": list(TIMEPOINTS), "available_fovs": ["F0"],
        "execution_options": {"phase_token": "PHASE", "nuclear_token": "PHASE", "fluor_tokens": ["GFP"],
                              "nanowell_format": "100um"},
        "nanowell": {"scheme_version": SCHEME_VERSION},
        "acquisition": {tp: {"pixel_size_um": {"x": PX_UM, "y": PX_UM},
                             "tile_size_px": {"x": TILE_PX, "y": TILE_PX}} for tp in TIMEPOINTS},
    }
    (out / "pipeline_info.json").write_text(json.dumps(info))
    return root
