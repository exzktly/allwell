"""Cell fluorescence (mean per cell) is measured above the same background as nanowell fluorescence.

The run writes the level it took off each nanowell's fluorescence as ``<channel>_background``; Review takes the same
level off the mean of the cells' mean intensities (``series_compute.site_metrics``), after Cell Gating has chosen the
cells. A run from before the column existed left the background in its cell fluorescence, and Review leaves it there
and says so on the axis (``background_compute``). The fixture's cells read 10 and 30, so the raw brightness is 20 and
any background other than 0 separates the subtracted number from the raw one.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from review.features.nanowell import background_compute as bc
from review.features.nanowell.collect import load_nanowell_data, site_backgrounds
from review.features.nanowell.series_compute import site_metrics
from tests.test_nanowell_review_data import STARTS, _dataset, _row

CELLS = {"well": ["A01"] * 2, "timepoint": ["t_c"] * 2, "nw_q": [0, 0], "nw_r": [0, 0],
         "GFP_mean_intensity": [10.0, 30.0], "area_px": [5, 5]}


def _out(tmp_path, bg, settings=None):
    out = _dataset(tmp_path, {"A01": [_row("t_c", 0, 0, n=2, c=0.2, gfp=100.0, bg=bg)]},
                   starts={"t_c": STARTS["t_c"]}, cells=CELLS)
    if settings is not None:
        grid = out / "nanowells" / "A01_grid.json"
        grid.write_text(json.dumps({**json.loads(grid.read_text()), "settings": settings}))
    return out


def _brightness(out) -> float:
    metrics = site_metrics(load_nanowell_data(out))
    return float(metrics.loc[metrics["timepoint"] == "t_c", "brightness_GFP"].iloc[0])


def test_the_cells_mean_less_the_run_s_background(tmp_path):
    out = _out(tmp_path, bg=8.0)
    assert _brightness(out) == pytest.approx(20.0 - 8.0)
    assert load_nanowell_data(out).fluor_backgrounds == {"A01": bc.FLOOR}


def test_the_empty_well_s_background_is_named_from_the_well_s_own_record(tmp_path):
    out = _out(tmp_path, bg=30.0, settings={"empty_well": "H12", "fluor_background": "empty_well"})
    assert _brightness(out) == pytest.approx(20.0 - 30.0)          # below the empty well's floor: negative
    assert load_nanowell_data(out).fluor_backgrounds == {"A01": bc.EMPTY_WELL}
    assert site_backgrounds(out.parent, ["A01"]) == {"A01": bc.FLOOR}   # the header read takes the dataset's settings


def test_a_site_the_run_left_without_a_background_has_no_brightness(tmp_path):
    # An empty-well run at a timepoint where the empty well held cells: blank, never the raw mean in its place.
    out = _dataset(tmp_path, {"A01": [_row("t_c", 0, 0, n=2, c=0.2, gfp=100.0, bg=None)]},
                   starts={"t_c": STARTS["t_c"]}, cells=CELLS)
    assert pd.isna(_brightness(out))


def test_a_run_from_before_the_column_keeps_its_raw_brightness_and_says_so(tmp_path):
    out = _out(tmp_path, bg=8.0)
    table = out / "nanowells" / "A01.csv"
    pd.read_csv(table).drop(columns=["GFP_background"]).to_csv(table, index=False)
    assert _brightness(out) == pytest.approx(20.0)
    assert load_nanowell_data(out).fluor_backgrounds == {"A01": bc.NONE}
    assert site_backgrounds(out.parent, ["A01"]) == {"A01": bc.NONE}
