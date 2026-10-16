"""The Array map, Trajectories, Position and Array QC data, and the nanowell crops (tracker V2, V2a, V3c, V4,
V6, V7a), on a real nanowell run's output where the convention or the files matter."""

from __future__ import annotations

import math
import shutil
import zipfile

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
    found, missing = vc.position_series(site_metrics(data), ["A01"], "t_c", "n")
    series = found[0]
    assert missing == []
    assert list(series.rings) == [0.0, 1.0, 2.0]
    assert list(series.median) == [1.0, 4.0, 7.0]
    assert list(series.excluded_share) == [0.0, 0.0, 0.5] and list(series.n_used) == [1, 2, 1]


def test_a_roster_nanowell_not_seen_at_a_timepoint_counts_as_excluded_in_its_ring(tmp_path):
    """(1, 0) is in the roster but missing from t_a's table, so Review adds it as absent, with its address and
    no ring: the ring is computed from the address, and ring 1 is wholly excluded rather than vanishing. B01
    has no t_a at all, and is named rather than dropped."""
    rows = [_row("t_c", 0, 0, n=1, c=0.1), _row("t_c", 1, 0, n=3, c=0.1), _row("t_c", 1, 1, n=5, c=0.1),
            _row("t_a", 0, 0, n=2, c=0.1), _row("t_a", 1, 1, n=6, c=0.1)]
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows, "B01": [_row("t_c", 0, 0, n=1, c=0.1)]},
                                       starts={k: STARTS[k] for k in ("t_c", "t_a")}))
    metrics = site_metrics(data)
    absent = metrics[(metrics["well"] == "A01") & (metrics["timepoint"] == "t_a") & (metrics["nw_q"] == 1)
                     & (metrics["nw_r"] == 0)]
    assert absent["state"].tolist() == ["absent"]
    found, missing = vc.position_series(metrics, ["A01", "B01"], "t_a", "n")
    assert [s.well for s in found] == ["A01"] and missing == ["B01"]
    # (1, 1) is two steps from the centre on a hex lattice ((|q| + |r| + |q + r|) / 2), not one.
    assert list(found[0].rings) == [0.0, 1.0, 2.0]
    assert list(found[0].excluded_share) == [0.0, 1.0, 0.0]
    assert np.isnan(found[0].median[1]) and list(found[0].n_used) == [1, 0, 1]


def _crop_data(tmp_path, *, archive_bytes=None, members=()):
    """A one-nanowell dataset whose montage has one bright pixel block at a point with x ≠ y, so a crop cut
    with its axes swapped lands on dark pixels."""
    from PIL import Image
    from types import SimpleNamespace
    import io
    import pandas as pd

    image = np.zeros((60, 80), dtype=np.uint8)
    image[17:24, 47:54] = 255      # around well-frame (100, 40) µm = pixel (50, 20) at 2 µm/px from (0, 0)
    out = tmp_path / "out"
    out.mkdir()
    buf = io.BytesIO()
    Image.fromarray(image).save(buf, format="PNG")
    with zipfile.ZipFile(out / "A01_out.zip", "w") as zf:
        zf.writestr("A01_t0_montage.png", buf.getvalue())
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    if archive_bytes is not None:
        (in_dir / "A01.zip").write_bytes(archive_bytes)
    elif members:
        with zipfile.ZipFile(in_dir / "A01.zip", "w") as zf:
            for name, payload in members:
                zf.writestr(name, payload)
    data = SimpleNamespace(
        out_dir=out, in_dir=in_dir, schema="experiment:channel:well:fov:timepoint", separator="_",
        phase_token="phase", acquisition={"t0": {"pixel_size_um": {"x": 0.5, "y": 0.4}}},   # y unused, as the pipeline
        grids={"A01": {"t0": {"status": "ok", "montage_origin_um": [0.0, 0.0], "montage_um_per_px": 2.0,
                              "tiles": [{"tile": "F0", "left_um": 0.0, "top_um": 0.0, "source": "lattice"}]}}})
    metrics = pd.DataFrame([{"well": "A01", "nw_q": 0, "nw_r": 0, "timepoint": "t0", "tp_order": 0,
                             "well_x_um": 100.0, "well_y_um": 40.0, "radius_um": 3.0, "owner": "F0",
                             "state": "measured"}])
    return data, metrics


def _tif(image) -> bytes:
    import io
    import tifffile

    buf = io.BytesIO()
    tifffile.imwrite(buf, image)
    return buf.getvalue()


def test_a_montage_crop_is_cut_at_x_then_y_not_the_other_way_round(tmp_path):
    data, metrics = _crop_data(tmp_path)
    crop = ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]
    assert crop.source == "montage" and crop.image.min() == 255          # every pixel on the bright block


def test_a_tile_crop_is_cut_at_the_site_and_holds_no_reference_to_the_whole_tile(tmp_path):
    tile = np.zeros((120, 260), dtype=np.uint16)
    tile[78:82, 198:202] = 1000                  # (100, 40) µm at 0.5 µm/px from the tile's corner (0, 0)
    data, metrics = _crop_data(tmp_path, members=[
        ("exp_phase_A01_F0_t0/", b""),                               # a directory entry with the image's tokens
        ("exp_phase_A01_F0_t0.txt", b"a sidecar"),                    # the image's stem, not an image
        ("__MACOSX/._exp_phase_A01_F0_t0.tif", b"\x00\x05\x16\x07 AppleDouble"),
        (".DS_Store", b"junk"),
        ("exp_phase_A01_F0_t0_extra.tif", _tif(tile))])              # one token beyond the schema
    crop = ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]
    assert (crop.source, crop.note) == ("tile", "F0, full resolution")
    # ±1.4 radii = ±8.4 px around pixel (200, 80): columns 191–208, rows 71–88.
    assert crop.image.base is None and crop.image.shape == (18, 18)
    assert crop.image[7:11, 7:11].min() == 1000 and crop.image[0].max() == 0


def test_an_unreadable_input_archive_falls_back_to_the_montage_and_names_the_archive(tmp_path):
    data, metrics = _crop_data(tmp_path, archive_bytes=b"PK\x03\x04 truncated")
    crop = ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]
    assert crop.source == "montage"
    assert crop.note.startswith("from the montage: A01.zip could not be read (")


def test_a_nanowell_with_no_owner_tile_says_so_rather_than_blaming_the_archive(tmp_path):
    data, metrics = _crop_data(tmp_path, members=[("exp_phase_A01_F0_t0.tif", _tif(np.zeros((8, 8), np.uint16)))])
    metrics["owner"] = np.nan                                       # a fused site: the pipeline gives no owner
    crop = ic.site_crops(data, metrics, ("A01", 0, 0))["t0"]
    assert crop.note == "from the montage: the nanowell has no owner tile (it is fused or shared)"


def test_a_capture_repaints_the_class_medians_in_their_publication_shades():
    """Roll-up review X14: the thick medians kept their screen shades in a publication capture, where the 3+
    median nearly vanished on white. They are paired like the thin lines, so the capture's repaint reaches them."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.colors import to_hex
    from matplotlib.figure import Figure

    from review.features.nanowell.renderer import _OCCUPANCY_INDEX, render_trajectories
    from ui.theme import Colors, CPub
    from widgets.plot_card import repaint_theme_pairs

    times = np.array([0.0, 5.0])
    panel = vc.TrajectoryPanel("A01", times, ["0h", "5h"], [((0, 0), "3+", np.array([3.0, 4.0]))],
                               {"3+": np.array([3.0, 4.0])}, 0)
    fig = Figure()
    render_trajectories(fig, [panel], label="Cell count")
    ax = fig.axes[0]
    median = next(c for c in ax.collections if c.get_label() == "3+ at baseline")
    screen, publication = Colors.occupancy_ramp[_OCCUPANCY_INDEX["3+"]], CPub.occupancy_ramp[_OCCUPANCY_INDEX["3+"]]
    assert to_hex(screen) != to_hex(publication)
    repaint_theme_pairs(ax, "screen")
    assert to_hex(median.get_edgecolor()[0]) == to_hex(screen)
    repaint_theme_pairs(ax, "publication")
    assert to_hex(median.get_edgecolor()[0]) == to_hex(publication)


def test_a_square_lattice_well_is_mapped_on_a_square_lattice_and_ringed_by_square_rings(tmp_path):
    """Tracker X35: a well whose grid records say its lattice is square is laid out square and ringed by
    ``max(|q|, |r|)``; a hexagonal well beside it keeps the hexagonal layout and hex distance."""
    rows = [_row("t_c", 0, 0, n=1, c=0.1), _row("t_c", 1, 1, n=3, c=0.1), _row("t_c", 0, 1, n=5, c=0.1)]
    grids = {w: {"t_c": {"status": "ok", "pitch_um": 100.0, "lattice_kind": kind}}
             for w, kind in (("A01", "square"), ("B01", "hex"))}
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows, "B01": rows}, starts={"t_c": STARTS["t_c"]},
                                       grids=grids))
    metrics = site_metrics(data)
    assert (vc.lattice_kind(data, "A01", "t_c"), vc.lattice_kind(data, "B01", "t_c")) == ("square", "hex")
    square = vc.map_model(metrics, data, "A01", "t_c", "n")
    at = {site[1:]: (x, y) for site, x, y in zip(square.sites, square.x, square.y)}
    assert at[(1, 1)] == pytest.approx((100.0, -100.0)) and at[(0, 1)] == pytest.approx((0.0, -100.0))
    hexagonal = vc.map_model(metrics, data, "B01", "t_c", "n")
    at = {site[1:]: (x, y) for site, x, y in zip(hexagonal.sites, hexagonal.x, hexagonal.y)}
    assert at[(1, 1)] == pytest.approx((150.0, -100.0 * math.sqrt(3) / 2))
    found, _ = vc.position_series(metrics, ["A01", "B01"], "t_c", "n",
                                  kinds={w: vc.lattice_kind(data, w, "t_c") for w in ("A01", "B01")})
    rings = {s.well: list(s.rings) for s in found}
    assert rings == {"A01": [0.0, 1.0], "B01": [0.0, 1.0, 2.0]}       # (1, 1) is one square step, two hex steps


def test_the_page_s_position_view_rings_a_square_well_by_square_rings(tmp_path, monkeypatch):
    """The same, through the page's one composition (``controller.draw_view``), which is what passes each well's
    lattice kind to the ring computation."""
    from matplotlib.figure import Figure

    from review.features.nanowell import controller

    rows = [_row("t_c", 0, 0, n=1, c=0.1), _row("t_c", 1, 1, n=3, c=0.1), _row("t_c", 0, 1, n=5, c=0.1)]
    grids = {"A01": {"t_c": {"status": "ok", "pitch_um": 100.0, "lattice_kind": "square"}}}
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={"t_c": STARTS["t_c"]}, grids=grids))
    drawn = []
    monkeypatch.setattr(controller, "render_position", lambda fig, series, **k: drawn.append(series))
    controller.draw_view(Figure(), "position", site_metrics(data), data, groups={"g": ["A01"]}, metric="n",
                         channel=None, axis="groups", timepoint="t_c", control="", baseline="", cohort=False,
                         well="A01", well_timepoint="t_c")
    (series,), = drawn
    assert list(series.rings) == [0.0, 1.0]


def test_each_timepoint_is_laid_out_on_the_lattice_its_own_grid_record_names(tmp_path):
    """Review X35-R4: the kind is read per timepoint, never guessed for the well (a well whose records disagree was
    drawn hexagonal at every timepoint)."""
    rows = [_row(tp, q, r, n=1, c=0.1) for tp in ("t_c", "t_a") for q, r in ((0, 0), (1, 1))]
    grids = {"A01": {"t_c": {"status": "ok", "pitch_um": 100.0, "lattice_kind": "square"},
                     "t_a": {"status": "ok", "pitch_um": 100.0, "lattice_kind": "hex"}}}
    data = load_nanowell_data(_dataset(tmp_path, {"A01": rows}, starts={k: STARTS[k] for k in ("t_c", "t_a")},
                                       grids=grids))
    metrics = site_metrics(data)
    for tp, expected in (("t_c", (100.0, -100.0)), ("t_a", (150.0, -100.0 * math.sqrt(3) / 2))):
        model = vc.map_model(metrics, data, "A01", tp, "n")
        at = {site[1:]: (x, y) for site, x, y in zip(model.sites, model.x, model.y)}
        assert at[(1, 1)] == pytest.approx(expected), tp
