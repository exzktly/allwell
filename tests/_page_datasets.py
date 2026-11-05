"""Data-filled datasets for the page-render tests, and the setup each page needs to show data.

A page that shows "No wells selected" or "Select one or more feature columns" proves
nothing about how its real content fits the screen. So the full-app render tests load
datasets that carry data for the pages they cover, select the wells, and configure the
pages whose content depends on a choice the user makes (comparison groups, feature
columns, a generated gallery). :func:`prepare` does that when the tour reaches a page.

Three datasets, because no single one carries everything:

* ``mini`` — four wells, three timepoints, two fluorescent channels and per-cell shape
  features (so Spatial and Multivariate have columns), no images;
* ``nanowell`` — a nanowell plate with measurements, for every Nanowells page;
* ``images`` — two wells of real TIFFs with segmentation labels, for the Image Browser.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from PySide6.QtWidgets import QAbstractButton

from tests._screens import pump

DATASETS = ("mini", "nanowell", "images")


def build(kind: str, root: Path) -> tuple[Path, set[str]]:
    """The dataset folder Review loads, and the wells to select."""
    if kind == "mini":
        from tests.fixtures.mini_dataset import build_mini_dataset

        return build_mini_dataset(root / "mini", with_shape=True), {"A01", "A02", "B01", "B02"}
    if kind == "nanowell":
        from tests.test_nanowell_page import COUNTS, _write_nanowell_dataset

        return _write_nanowell_dataset(root / "nanowell"), set(COUNTS)
    if kind == "images":
        return image_plate(root / "images") / "out", {"A01", "A02"}
    raise ValueError(kind)


def image_plate(root: Path) -> Path:
    """Two wells × DAPI/GFP, one field, real TIFFs in the pipeline's in/out layout — enough for the Image
    Table to draw a grid. Returns *root*; the dataset Review loads is ``root / "out"``."""
    import numpy as np
    import pytest

    tifffile = pytest.importorskip("tifffile")

    def tif(a):
        b = io.BytesIO()
        tifffile.imwrite(b, a)
        return b.getvalue()

    (root / "in").mkdir(parents=True)
    (root / "out").mkdir()
    info = {"schema": "experiment:channel:well:fov:timepoint", "separator": "_",
            "schema_fields": ["experiment", "channel", "well", "fov", "timepoint"],
            "well_index": 2, "channel_index": 1, "fov_index": 3, "tp_index": 4,
            "nuclear_token": "dapi", "fluor_tokens": ["dapi", "gfp"],
            "available_timepoints": ["0h"], "available_fovs": ["F001"]}
    (root / "out" / "pipeline_info.json").write_text(json.dumps(info))
    lab = np.zeros((64, 64), np.int32)
    lab[10:24, 10:24] = 1
    lab[36:54, 30:50] = 2
    for well in ("A01", "A02"):
        rows = []
        with zipfile.ZipFile(root / "in" / f"{well}.zip", "w") as zin, \
                zipfile.ZipFile(root / "out" / f"{well}_out.zip", "w") as zout:
            for ch, peak in (("DAPI", 3000), ("GFP", 900)):
                img = np.where(lab > 0, peak, 100).astype(np.uint16)
                stem = f"exp_{ch}_{well}_F001_0h"
                zin.writestr(stem + ".tif", tif(img))
                zout.writestr(stem + "_tophat.tif", tif(img))
            zout.writestr(f"exp_DAPI_{well}_F001_0h_labels.tif", tif(lab))
        for i in (1, 2):
            rows.append({"filename": f"exp_DAPI_{well}_F001_0h.tif", "experiment": "exp",
                         "channel": "dapi", "well": well, "fov": "F001",
                         "timepoint": "0h", "timepoint_hours": 0.0, "nucleus_id": i,
                         "area_px": int((lab == i).sum()), "gfp_mean_intensity": 900.0,
                         "dapi_mean_intensity": 3000.0})
        with (root / "out" / f"measurements_{well}.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return root


def press(win, text: str) -> bool:
    """Click the first enabled button whose text contains *text*, wherever it is (a control band hidden behind
    the phone's Controls sheet is still a button)."""
    for button in win.findChildren(QAbstractButton):
        try:
            if text in button.text() and button.isEnabled():
                button.click()
                return True
        except RuntimeError:
            continue
    return False


def prepare(win, qapp, kind: str, page: str) -> None:
    """Give *page* the choices that make it draw data, if it needs any. *page* is ``"Section/Sub-page"`` or
    ``"Section"`` as the tour names it."""
    ctx = win._review._ctx
    if kind == "mini" and page == "Plotting/Heat Map":
        press(win, "Auto-populate from selection")
    elif kind == "mini" and page == "Statistics":
        from review.domain.batch_models import BarGroup

        from review.features.stats import controller as stats

        win._review._app_state.stats.groups = [BarGroup("Ctrl", solo_wells=["A01", "A02"]),
                                               BarGroup("Drug", solo_wells=["B01", "B02"])]
        stats.stats_refresh_group_list(ctx)
        stats.stats_refresh_map(ctx)
        ctx.runtime.stats.run()
    elif kind == "mini" and page == "Multivariate":
        from review.features.multivariate import controller as mvctl
        from review.features.multivariate.collect import available_feature_columns

        families = available_feature_columns(ctx)
        mvctl.mv_set_features(ctx, families.get("Intensity", [])[:2] + families.get("Shape", [])[:1])
    elif kind == "images" and page == "Image Browser/Image Table":
        from review.features.image_browser.image_table import controller as table

        table.image_table_repopulate_dropdowns(ctx)
        win._review._app_state.image_table.active_channels = {"DAPI", "GFP"}
        table.image_table_apply_dims_preset(ctx, ["well"], ["channel"])
        table.image_table_generate(ctx)
    elif kind == "images" and page == "Image Browser/Cell Browser":
        button = getattr(ctx.runtime.cell_browser, "generate_btn", None)
        if button is not None:
            button.click()
    elif kind == "images" and page == "Image Browser/Playground":
        press(win, "Use dataset")
    pump(qapp, 30, 600)
