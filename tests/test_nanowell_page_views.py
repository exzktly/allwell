"""The Array map, Trajectories, Position and Array QC data, and the nanowell crops (tracker V2, V2a, V3c, V4,
V6, V7a), on a real nanowell run's output where the convention or the files matter."""

from __future__ import annotations

import math
import shutil

import numpy as np
import pytest

from review.features.nanowell import image_compute as ic
from review.features.nanowell import views_compute as vc
from review.features.nanowell.collect import load_nanowell_data
from review.features.nanowell.series_compute import site_metrics
from tests.test_nanowell_review_data import STARTS, _dataset, _row
from tests.test_nanowell_review_load import run_with_a_cell  # noqa: F401  (module fixture)
from tests.test_nanowell_cli import WELL


@pytest.fixture(scope="module")
def run_data(run_with_a_cell):  # noqa: F811
    data = load_nanowell_data(run_with_a_cell)
    return data, site_metrics(data)


# ── V2a: the lattice convention ─────────────────────────────────────────────
def test_lattice_positions_are_the_measured_positions_up_to_the_well_s_rotation(run_data):
    """Fit a rotation and translation from the lattice positions to the measured ones: no reflection, the
    grid record's angle, and residuals of the order of the localisation error — so the map's layout is the array's."""
    data, metrics = run_data
    rows = metrics[(metrics["timepoint"] == "0h") & metrics["well_x_um"].notna()]
    pitch = vc.well_pitch(data, WELL)
    lx, ly = vc.lattice_xy(rows["nw_q"], rows["nw_r"], pitch)
    src = np.column_stack([lx, ly])
    dst = rows[["well_x_um", "well_y_um"]].to_numpy(float)
    sc, dc = src - src.mean(0), dst - dst.mean(0)
    u, _s, vt = np.linalg.svd(sc.T @ dc)
    rot = (u @ vt).T
    assert np.linalg.det(rot) > 0                                        # not mirrored
    residual = np.abs(sc @ rot.T - dc).max()
    assert residual < 0.02 * pitch          # detected centres carry localisation error; a wrong axis is a pitch off
    angle = math.degrees(math.atan2(-rot[1, 0], rot[0, 0]))           # counter-clockwise on screen, y down
    assert angle == pytest.approx(data.grids[WELL]["0h"]["angle_deg"], abs=0.05)
    assert vc.lattice_xy(0, 0, pitch) == (0.0, 0.0)


def test_the_map_places_every_roster_nanowell_with_its_value_and_state(run_data):
    data, metrics = run_data
    model = vc.map_model(metrics, data, WELL, "0h", "n")
    roster = len(metrics[(metrics["well"] == WELL) & (metrics["timepoint"] == "0h")])
    assert len(model.x) == len(model.values) == len(model.state) == roster
    lit = model.sites.index((WELL, 1, 2))
    assert model.values[lit] == 1.0 and model.state[lit] == "measured"
    assert sum(model.counts.values()) == roster and model.reason == ""
    assert vc.map_model(metrics, data, "Z99", "0h", "n") is None


# ── V7a: montage and tiles ───────────────────────────────────────────────────
def test_the_montage_and_tiles_are_read_in_the_well_frame(run_data):
    data, _metrics = run_data
    montage, why = ic.read_montage(data, WELL, "0h")
    assert why == "" and montage.image.ndim == 2 and montage.image.dtype == np.uint8
    left, right, bottom, top = montage.extent()
    assert right > left and bottom > top
    boxes = ic.tile_boxes(data, WELL, "0h")
    assert len(boxes) == 4 and {b.source for b in boxes} <= {"lattice", "overlap", "recorded"}
    assert all(b.width_um and b.height_um for b in boxes)
    assert ic.read_montage(data, WELL, "9h")[0] is None


# ── V3c: crops ───────────────────────────────────────────────────────────────
def test_a_crop_is_cut_from_the_owner_tile_at_full_resolution_around_the_nanowell(run_data):
    data, metrics = run_data
    crops = ic.site_crops(data, metrics, (WELL, 1, 2))
    crop = crops["0h"]
    assert crop.source == "tile" and crop.image is not None
    px = data.acquisition["0h"]["pixel_size_um"]["x"]
    radius = metrics[(metrics["nw_q"] == 1) & (metrics["nw_r"] == 2)]["radius_um"].iloc[0]
    assert abs(crop.image.shape[0] - 2 * 1.4 * radius / px) <= 2
    centre = crop.image[crop.image.shape[0] // 2 - 3: crop.image.shape[0] // 2 + 3,
                        crop.image.shape[1] // 2 - 3: crop.image.shape[1] // 2 + 3]
    rim = np.concatenate([crop.image[0], crop.image[-1]])
    assert centre.mean() > rim.mean()                                   # a light nanowell on a dark lattice


def test_without_the_input_archive_the_crop_comes_from_the_montage_and_says_so(run_with_a_cell, tmp_path):  # noqa: F811
    copy = tmp_path / "out"
    shutil.copytree(run_with_a_cell, copy)
    data = load_nanowell_data(copy)
    assert data.in_dir is None
    crop = ic.site_crops(data, site_metrics(data), (WELL, 1, 2))["0h"]
    assert crop.source == "montage" and "input archive is not beside" in crop.note and crop.image is not None


# ── V4 / V6 ──────────────────────────────────────────────────────────────────
def test_trajectories_draw_each_nanowell_with_a_value_and_one_median_per_occupancy_class(tmp_path):
    rows = [_row("t_c", 0, 0, n=1, c=0.1), _row("t_a", 0, 0, n=2, c=0.2), _row("t_d", 0, 0, n=4, c=0.4),
            _row("t_c", 1, 0, n=1, c=0.1), _row("t_a", 1, 0, state="occluded"), _row("t_d", 1, 0, n=8, c=0.8),
            _row("t_c", 2, 0, state="occluded"), _row("t_a", 2, 0, state="occluded"),
            _row("t_d", 2, 0, state="occluded")]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={k: STARTS[k] for k in ("t_c", "t_a", "t_d")}))
    panel = vc.trajectory_panels(site_metrics(data), ["A01"], "n")[0]
    assert list(panel.times) == [0.0, 5.0, 10.0] and panel.n_without_value == 1
    assert len(panel.lines) == 2 and np.isnan(panel.lines[1][2][1])     # the occluded timepoint is a gap
    assert list(panel.class_medians) == ["1"] and list(panel.class_medians["1"]) == [1.0, 2.0, 6.0]


def test_position_effects_are_the_median_by_ring_with_each_ring_s_excluded_share(tmp_path):
    rows = [_row("t_c", 0, 0, n=1, c=0.1), _row("t_c", 1, 0, n=3, c=0.1), _row("t_c", 0, 1, n=5, c=0.1),
            _row("t_c", 2, 0, state="clipped"), _row("t_c", 0, 2, n=7, c=0.1)]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={"t_c": STARTS["t_c"]}))
    series = vc.position_series(site_metrics(data), ["A01"], "t_c", "n")[0]
    assert list(series.rings) == [0.0, 1.0, 2.0]
    assert list(series.median) == [1.0, 4.0, 7.0]
    assert list(series.excluded_share) == [0.0, 0.0, 0.5] and list(series.n_used) == [1, 2, 1]
