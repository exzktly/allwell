"""Registration, montage and fused crops, checked against tiles rendered from a known array."""

import math

import numpy as np
import pytest

from pipeline.nanowell_lattice import LatticeFit, fit_lattice
from pipeline.tile_register import (
    LATTICE, OVERLAP, RECORDED, PairConstraint, TileObservation, build_montage, correlate_overlap,
    fused_crop, lattice_phase_constraints, overlap_constraints, register_tiles, search_radius_px,
    solve_placements,
)
from tests._nanowell_synth import TilePlan, render_tiles

PITCH, DIAMETER, PX = 172.0, 100.0, 4.3        # um, um, um per pixel: the pitch is 40 px


def _observations(rendered, *, noise=0.3, seed=0, with_fits=True):
    """What detection would hand registration: each tile's visible sites in its own
    coordinates (the truth, plus noise), fitted. Detection itself is a later section."""
    rng = np.random.default_rng(seed)
    extent = rendered.plan.extent_um
    sites = rendered.sites_um
    if rendered.bubble is not None:
        bx, by, radius = rendered.bubble
        sites = sites[np.hypot(sites[:, 0] - bx, sites[:, 1] - by) > radius]
    out = []
    for name, (left, top) in rendered.true_left_top_um.items():
        inside = ((sites[:, 0] > left) & (sites[:, 0] < left + extent[0])
                  & (sites[:, 1] > top) & (sites[:, 1] < top + extent[1]))
        points = sites[inside] - (left, top) + rng.normal(0, noise, (int(inside.sum()), 2))
        fit = fit_lattice(points, expected_pitch=PITCH) if with_fits and len(points) >= 6 else None
        out.append(TileObservation(name, rendered.recorded_left_top_um[name], extent, fit))
    return out


def _error(registration, rendered):
    """Largest disagreement between a tile's solved position error and tile F0's, um.

    Only differences between tiles matter: the whole acquisition may sit anywhere."""
    errors = {s.tile_id: np.subtract((s.left_um, s.top_um), rendered.true_left_top_um[s.tile_id])
              for s in registration.tiles}
    base = errors["F0"]
    return max(float(np.linalg.norm(e - base)) for e in errors.values())


def _recorded_error(rendered):
    errors = {n: np.subtract(rendered.recorded_left_top_um[n], rendered.true_left_top_um[n])
              for n in rendered.images}
    return max(float(np.linalg.norm(e - errors["F0"])) for e in errors.values())


PLANS = [
    TilePlan(3, 3, (220, 220), 0.30, PX),
    TilePlan(2, 4, (260, 180), 0.15, PX),          # another plan: nothing may assume 3 x 3 / 30 %
]


@pytest.mark.parametrize("plan", PLANS)
def test_lattice_phase_removes_the_stage_jitter(plan):
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=1800, rotation_deg=6,
                            stage_jitter_um=20, noise_sigma=0.03, cells=20, debris=10, seed=3)
    assert _recorded_error(rendered) > 15            # the jitter is real, so removing it is a result
    result = register_tiles(_observations(rendered))
    assert {s.tile_id: s.source for s in result.tiles} == {n: LATTICE for n in rendered.images}
    assert _error(result, rendered) < 1.0
    assert result.warnings == ()


@pytest.mark.parametrize("plan", PLANS)
def test_overlap_correlation_alone_also_removes_it(plan):
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=1800, rotation_deg=6,
                            stage_jitter_um=20, noise_sigma=0.03, cells=30, debris=15, seed=3)
    observations = _observations(rendered, with_fits=False)
    result = register_tiles(observations, images=rendered.images, pixel_size_um=PX, pitch_um=PITCH)
    assert {s.source for s in result.tiles} == {OVERLAP}
    assert _error(result, rendered) < 1.5            # under a third of a pixel


def test_the_search_is_confined_so_the_periodicity_trap_cannot_be_entered():
    plan = TilePlan(1, 2, (400, 300), 0.5, PX)
    pitch_px = PITCH / PX
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=3500, rotation_deg=5,
                            noise_sigma=0.15, vignette=0.5, cells=0, debris=0, seed=0, blur_px=1.5)
    truth = np.subtract(rendered.true_left_top_um["F1"], rendered.true_left_top_um["F0"]) / PX
    expected = truth + (0.3 * pitch_px, 0.0)         # the stage is 0.3 pitch off
    narrow = correlate_overlap(rendered.images["F0"], rendered.images["F1"], expected,
                               search_radius=int(0.475 * pitch_px))
    wide = correlate_overlap(rendered.images["F0"], rendered.images["F1"], expected,
                             search_radius=int(1.3 * pitch_px))
    # The fixture is only worth anything if the unconstrained search really is fooled: it locks
    # exactly one pitch off, and with a *higher* correlation than the right answer has.
    assert wide.offset_px[0] - truth[0] == pytest.approx(-pitch_px, abs=1.0)
    assert wide.peak > narrow.peak
    # The production path (confined to just under half a pitch) finds the true offset.
    observations = [TileObservation(n, rendered.recorded_left_top_um[n], plan.extent_um, None)
                    for n in ("F0", "F1")]
    observations[1] = TileObservation("F1", tuple(np.add(rendered.true_left_top_um["F1"], (0.3 * PITCH, 0.0))),
                                      plan.extent_um, None)
    constraints, skipped = overlap_constraints(observations, rendered.images, PX, PITCH)
    assert skipped == [] and len(constraints) == 1
    measured = np.subtract(constraints[0].offset_um, np.subtract(rendered.true_left_top_um["F1"],
                                                                 rendered.true_left_top_um["F0"]))
    assert float(np.linalg.norm(measured)) < 1.0
    assert constraints[0].correction_um == pytest.approx(0.3 * PITCH, abs=1.5)


def test_a_narrow_overlap_still_registers_where_a_fixed_template_could_not():
    # An overlap of 31 px is 0.78 of the 40 px pitch, as the real 10 % overlap is. A template that
    # had to stay inside it at every shift of up to half a pitch would not fit at all.
    plan = TilePlan(1, 2, (400, 300), 31 / 400, PX)
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=3500, rotation_deg=5,
                            noise_sigma=0.03, cells=40, debris=20, seed=2, blur_px=1.5)
    truth = np.subtract(rendered.true_left_top_um["F1"], rendered.true_left_top_um["F0"]) / PX
    expected = truth + (-8.0, 3.0)                   # error towards more overlap, where the window is valid
    result = correlate_overlap(rendered.images["F0"], rendered.images["F1"], expected,
                               search_radius=search_radius_px(PITCH, PX))
    assert result.ok, result.reason
    assert np.allclose(np.subtract(result.offset_px, truth), 0, atol=0.7)


def test_tiles_with_no_array_keep_their_recorded_position_and_say_so():
    plan = TilePlan(5, 5, (200, 200), 0.30, PX)
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=1000, rotation_deg=4,
                            stage_jitter_um=15, noise_sigma=0.03, seed=1)
    result = register_tiles(_observations(rendered))
    by_id = result.by_id()
    corners = ["F0", "F4", "F20", "F24"]
    for name in corners:
        solution = by_id[name]
        assert solution.source == RECORDED and solution.n_constraints == 0
        assert solution.residual_um is None and "no lattice fit" in solution.note
        assert (solution.left_um, solution.top_um) == pytest.approx(rendered.recorded_left_top_um[name])
        assert solution.correction_um == pytest.approx(0.0, abs=1e-9)
    assert by_id["F12"].source == LATTICE
    assert any(note.startswith("F0-") for note in result.skipped)


def test_the_solve_weights_each_measurement_by_its_uncertainty():
    tiles = [TileObservation("A", (0.0, 0.0), (100.0, 100.0)), TileObservation("B", (10.0, 0.0), (100.0, 100.0))]
    constraints = [PairConstraint("A", "B", (10.0, 0.0), 1.0, LATTICE),
                   PairConstraint("A", "B", (20.0, 0.0), 2.0, OVERLAP)]
    result = solve_placements(tiles, constraints, prior_sigma_um=1e6)
    a, b = result.by_id()["A"], result.by_id()["B"]
    # Weighted mean of 10 (weight 1) and 20 (weight 1/4) is 12; the prior is too weak to move it.
    assert (b.left_um - a.left_um) == pytest.approx(12.0, abs=1e-3)
    # Each tile's residual is the rms disagreement with its own constraints: 2 and 8 about 12.
    assert a.residual_um == pytest.approx(math.sqrt((2.0 ** 2 + 8.0 ** 2) / 2), rel=1e-3)
    assert a.source == LATTICE and a.n_constraints == 2


def test_a_recorded_error_nearing_half_a_pitch_is_warned_about():
    plan = TilePlan(2, 2, (220, 220), 0.30, PX)
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=1800, rotation_deg=6,
                            noise_sigma=0.03, seed=3)
    observations = _observations(rendered)
    for offset, warned in ((0.2 * PITCH, False), (0.45 * PITCH, True)):
        shifted = [o if o.tile_id != "F1" else
                   TileObservation("F1", (o.recorded_left_top_um[0] + offset, o.recorded_left_top_um[1]),
                                   o.extent_um, o.fit) for o in observations]
        result = register_tiles(shifted)
        assert _error(result, rendered) < 1.0         # still right: under half a pitch
        assert bool(result.warnings) == warned
        if warned:
            assert "F0-F1" in result.warnings[0] and "45 %" in result.warnings[0]


def test_a_recorded_error_past_half_a_pitch_is_wrong_by_one_vector_and_nothing_says_so():
    # The limit of the method, pinned so it is not mistaken for a bug: the lattice cannot tell an
    # error of 0.62 pitch from one of -0.38 pitch the other way. The assumption is that the stage
    # is repeatable to under half a pitch within one acquisition.
    plan = TilePlan(2, 2, (220, 220), 0.30, PX)
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=1800, rotation_deg=0,
                            noise_sigma=0.03, seed=3)
    observations = _observations(rendered)
    off = 0.62 * PITCH
    shifted = [o if o.tile_id != "F1" else
               TileObservation("F1", (o.recorded_left_top_um[0] + off, o.recorded_left_top_um[1]),
                               o.extent_um, o.fit) for o in observations]
    result = register_tiles(shifted)
    assert result.warnings == ()
    assert _error(result, rendered) == pytest.approx(PITCH, rel=0.05)


def test_pairs_that_cannot_be_constrained_are_named_with_the_reason():
    plan = TilePlan(1, 2, (220, 220), 0.30, PX)
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=1800, seed=3)
    good = _observations(rendered)
    assert len(lattice_phase_constraints(good)[0]) == 1
    missing = [good[0], TileObservation(good[1].tile_id, good[1].recorded_left_top_um, good[1].extent_um, None)]
    constraints, skipped = lattice_phase_constraints(missing)
    assert constraints == [] and skipped == ["F0-F1: no lattice fit on F1"]
    stretched = LatticeFit(True, "", tuple(np.multiply(good[1].fit.a1, 1.12)), tuple(np.multiply(good[1].fit.a2, 1.12)),
                           good[1].fit.pitch * 1.12, good[1].fit.angle_deg, "hex", good[1].fit.phase_origin,
                           good[1].fit.rms_residual, good[1].fit.inlier_mask)
    mismatched = [good[0], TileObservation("F1", good[1].recorded_left_top_um, good[1].extent_um, stretched)]
    constraints, skipped = lattice_phase_constraints(mismatched)
    assert constraints == [] and "not one lattice" in skipped[0]


# ── montage ──────────────────────────────────────────────────────────────────
def test_the_montage_blends_overlaps_and_records_its_stretch():
    a = np.full((20, 40), 0.2, dtype=np.float32)
    b = np.full((20, 40), 0.8, dtype=np.float32)
    montage = build_montage({"a": a, "b": b}, {"a": (0.0, 0.0), "b": (30.0, 0.0)}, 1.0,
                            percentiles=(0.0, 100.0))
    assert montage.downsample == 1 and montage.um_per_px == 1.0
    assert montage.stretch == pytest.approx((0.2, 0.8))
    row = montage.image[10].astype(int)
    assert row[0] == 0 and row[69] == 255                 # the ends are the stretch's own ends
    assert row[5] == 0 and row[65] == 255                 # away from the overlap each tile is itself
    overlap = row[30:40]
    assert np.all(np.diff(overlap) > 0)                   # blended, monotonic across the seam
    assert 0 < overlap[0] < overlap[-1] < 255
    assert montage.covered.all()


def test_the_montage_long_side_is_capped_and_the_cap_sets_the_scale():
    images = {f"t{i}": np.random.default_rng(i).random((100, 100)).astype(np.float32) for i in range(3)}
    positions = {f"t{i}": (i * 180.0, 0.0) for i in range(3)}     # 90 px apart at 2 um/px: 280 px long
    montage = build_montage(images, positions, 2.0, max_side_px=100)
    assert montage.downsample == math.ceil(280 / 100) == 3
    assert montage.um_per_px == pytest.approx(6.0)
    assert max(montage.image.shape) <= 100 + 1
    assert montage.image.dtype == np.uint8


def test_uncovered_montage_pixels_are_marked_not_painted_as_data():
    a = np.full((10, 10), 0.5, dtype=np.float32)
    montage = build_montage({"a": a, "b": a.copy()}, {"a": (0.0, 0.0), "b": (30.0, 20.0)}, 1.0)
    assert not montage.covered.all() and not montage.covered[0, 25]
    assert montage.image[~montage.covered].max() == 0


# ── fused-site crops ─────────────────────────────────────────────────────────
def _seam_scene():
    plan = TilePlan(2, 2, (200, 200), 0.10, PX)
    options = dict(pitch_um=PITCH, diameter_um=DIAMETER, radius_um=1500, rotation_deg=0, cells=25, debris=10,
                   seed=5, blur_px=1.5)
    tiled = render_tiles(plan, **options)
    whole = render_tiles(TilePlan(1, 1, (380, 380), 0.0, PX), **options)       # the same field in one image
    return plan, tiled, whole


def test_a_fused_crop_matches_the_same_field_imaged_whole():
    plan, tiled, whole = _seam_scene()
    left, top = (plan.step_um[0], plan.step_um[1])
    # A site whose disk straddles the vertical seam of the two left tiles: no single tile holds it.
    sites = tiled.sites_um
    reach = DIAMETER / 2
    seam = [s for s in sites
            if left - reach < s[0] < plan.extent_um[0] + reach
            and not any(l <= s[0] - reach and s[0] + reach <= l + plan.extent_um[0] for l in (0.0, left))
            and plan.extent_um[1] / 3 < s[1] < plan.extent_um[1]]
    assert seam, "the fixture must have a site on the seam"
    site = seam[0]
    tiles = {n: (tiled.images[n], *tiled.true_left_top_um[n]) for n in tiled.images}
    crop = fused_crop(site, DIAMETER, tiles, PX, margin_um=10.0)
    reference = fused_crop(site, DIAMETER, {"whole": (whole.images["F0"], 0.0, 0.0)}, PX, margin_um=10.0)
    assert len(crop.tiles_used) >= 2 and crop.uncovered_fraction == 0.0 and not np.isnan(crop.crop).any()
    assert np.sqrt(np.mean((crop.crop - reference.crop) ** 2)) < 0.02
    # Using only the tile that holds most of the disk leaves part of the crop empty: the blend is needed.
    alone = fused_crop(site, DIAMETER, {crop.attributed_tile: tiles[crop.attributed_tile]}, PX, margin_um=10.0)
    assert alone.uncovered_fraction > 0 or np.isnan(alone.crop).any()


def test_cells_of_a_fused_site_belong_to_the_tile_holding_most_of_its_disk():
    plan, tiled, _ = _seam_scene()
    assert plan.step_um[0] == pytest.approx(774.0) and plan.extent_um[0] == pytest.approx(860.0)
    tiles = {n: (tiled.images[n], *tiled.true_left_top_um[n]) for n in tiled.images}
    # The overlap is 86 um and the disk 100 um across, so a disk centred between x = 810 and 824
    # um is held whole by neither tile. Near 811 the left tile covers nearly all of it, near 823
    # the right tile does; each cell belongs to the tile with the larger share.
    for x, owner, other in ((811.5, "F0", "F1"), (822.5, "F1", "F0")):
        crop = fused_crop((x, 400.0), DIAMETER, tiles, PX)
        assert crop.attributed_tile == owner
        assert crop.disk_fraction[owner] > crop.disk_fraction[other] > 0.5
        assert sum(crop.disk_fraction.values()) > 1.5        # the overlap is covered by both tiles
    assert crop.fused is True


def test_a_site_nothing_covers_reports_how_much_of_it_is_missing():
    image = np.ones((50, 50), dtype=np.float32)
    crop = fused_crop((0.0, 25.0), 20.0, {"only": (image, 0.0, 0.0)}, 1.0)
    assert crop.uncovered_fraction == pytest.approx(0.5, abs=0.06)
    assert np.isnan(crop.crop).any() and crop.attributed_tile == "only"


def test_only_tiles_whose_rectangles_overlap_are_neighbours():
    from pipeline.tile_register import neighbour_pairs
    extent = (100.0, 100.0)
    row = [TileObservation(n, (x, 0.0), extent) for n, x in (("A", 0.0), ("B", 90.0), ("C", 180.0))]
    diagonal = TileObservation("D", (90.0, 90.0), extent)
    apart = TileObservation("E", (400.0, 0.0), extent)
    assert neighbour_pairs(row + [diagonal, apart]) == [
        ("A", "B"), ("A", "D"), ("B", "C"), ("B", "D"), ("C", "D")]
    # Touching edges share no area, so they are not neighbours either.
    assert neighbour_pairs([TileObservation("P", (0.0, 0.0), extent),
                            TileObservation("Q", (100.0, 0.0), extent)]) == []


def test_a_crop_aligned_to_the_pixel_grid_is_the_image_itself():
    # With the crop's sample points on pixel centres, bilinear sampling must return the pixels
    # unchanged: this pins the pixel-centre convention against an absolute truth, which comparing
    # two crops made by the same function cannot do.
    image = np.random.default_rng(7).random((60, 80)).astype(np.float32)
    left, top, px, n = 12.0, 7.0, 2.0, 21
    i0, j0 = 30, 25                                          # centre pixel (column, row)
    site = (left + (i0 + 0.5) * px, top + (j0 + 0.5) * px)
    crop = fused_crop(site, n * px, {"t": (image, left, top)}, px)
    assert crop.crop.shape == (n, n)
    assert np.allclose(crop.crop, image[j0 - 10:j0 + 11, i0 - 10:i0 + 11], atol=1e-6)
    assert crop.origin_um == pytest.approx((left + (i0 - 10 + 0.5) * px, top + (j0 - 10 + 0.5) * px))


# ── review findings: edge cases and the gates ────────────────────────────────
def _field(seed, size=(400, 400), sigma=4.0):
    """An aperiodic smooth random image: one unambiguous correlation peak."""
    from scipy import ndimage
    field = ndimage.gaussian_filter(np.random.default_rng(seed).normal(size=size), sigma)
    return (field / field.std()).astype(np.float32)


def test_an_overlap_no_wider_than_the_search_radius_is_handled_not_crashed_on():
    # The largest shift in the window then leaves no overlap at all. At 0.325 um/px a 10 %
    # overlap of a 2048 px tile (205 px) is narrower than the 264 px half-pitch radius, which is
    # the real plan, and this used to raise IndexError for the whole well.
    a, b = _field(1, (300, 300)), _field(2, (300, 300))
    for expected in ((270, 0), (270, -20), (250, 0), (200, 0)):
        result = correlate_overlap(a, b, expected, search_radius=40)
        assert result.ok is False and result.reason       # unrelated images: refused, with a reason
    big = correlate_overlap(_field(3, (600, 600)), _field(3, (600, 600)), (520, 0), search_radius=100)
    assert big.reason or big.ok                            # reached a verdict instead of raising


def test_a_flat_or_non_finite_tile_is_refused_for_the_right_reason():
    flat = np.full((200, 200), 65000.0, dtype=np.float32)
    assert correlate_overlap(flat, flat, (120, 0), search_radius=20).ok is False
    noisy = _field(4, (200, 200))
    shifted = noisy.copy()
    shifted[100, 100] = np.nan
    result = correlate_overlap(noisy, shifted, (120, 0), search_radius=20)
    assert result.ok is False and "non-finite" in result.reason


def test_unrelated_tiles_are_not_accepted_as_a_match():
    # Pearson correlation over a window that shrinks with the shift is biased toward the thinnest
    # overlaps, so a gate that is too low accepts unrelated images. 40 independent pairs.
    accepted = [seed for seed in range(40)
                if correlate_overlap(_field(seed, (300, 300)), _field(1000 + seed, (300, 300)),
                                     (250, 0), search_radius=24).ok]
    assert accepted == []


def test_an_ambiguous_peak_is_refused_with_that_reason():
    plan = TilePlan(1, 2, (400, 300), 0.5, PX)
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=3500, rotation_deg=5,
                            noise_sigma=0.15, vignette=0.5, seed=0, blur_px=1.5)
    truth = np.subtract(rendered.true_left_top_um["F1"], rendered.true_left_top_um["F0"]) / PX
    wide = correlate_overlap(rendered.images["F0"], rendered.images["F1"], truth + (12.0, 0.0),
                             search_radius=int(1.3 * PITCH / PX))
    assert wide.ok is False and "ambiguous" in wide.reason


@pytest.mark.parametrize("true_shift", [(2.4, -1.3), (-3.7, 2.6)])
def test_the_overlap_offset_is_refined_below_a_pixel_in_the_right_direction(true_shift):
    from scipy import ndimage
    whole = _field(5, (500, 500), sigma=3.0)
    a = whole[:, :300]
    # Tile j is the same field starting 240 px along, sampled at a fractional offset.
    offset = (240.0 + true_shift[0], true_shift[1])
    j = ndimage.shift(whole, (-offset[1], -offset[0]), order=3, mode="nearest")[:300, :300]
    result = correlate_overlap(a, j, (240, 0), search_radius=10)
    assert result.ok, result.reason
    assert np.allclose(result.offset_px, offset, atol=0.15)
    assert np.allclose(result.correction_px, true_shift, atol=0.15)


def test_downsampling_never_widens_the_search_past_the_stated_radius():
    from scipy import ndimage
    whole = _field(6, (700, 1400), sigma=4.0)
    a = whole[:, :1100]
    radius, expected_x = 40, 1003                         # 1003 is not a multiple of 8
    for step in (-50, 50):                                # the true shift is outside the window
        j = ndimage.shift(whole, (0, -(expected_x + step)), order=1, mode="nearest")[:, :400]
        result = correlate_overlap(a, j, (expected_x, 0), search_radius=radius, downsample=8)
        if result.correction_px is not None:
            assert abs(result.correction_px[0]) <= radius + 1e-9
            assert abs(result.correction_px[1]) <= radius + 1e-9


def test_a_mis_recorded_tile_does_not_show_as_a_correction_on_every_other_tile():
    extent = (100.0, 100.0)
    tiles = [TileObservation(f"T{i}", (i * 90.0 + (100.0 if i == 0 else 0.0), 0.0), extent) for i in range(5)]
    constraints = [PairConstraint(f"T{i}", f"T{i + 1}", (90.0, 0.0), 0.1, LATTICE) for i in range(4)]
    result = solve_placements(tiles, constraints)
    by_id = result.by_id()
    # Every measurement says the tiles are 90 um apart; only T0 was recorded wrongly.
    assert by_id["T0"].correction_um == pytest.approx(100.0, abs=1.0)
    for name in ("T1", "T2", "T3", "T4"):
        assert by_id[name].correction_um == pytest.approx(0.0, abs=1.0)
    assert result.common_shift_um[0] == pytest.approx(20.0, abs=1.0)


def test_lattice_and_overlap_disagreeing_about_a_pair_is_warned_about():
    extent = (100.0, 100.0)
    tiles = [TileObservation("A", (0.0, 0.0), extent), TileObservation("B", (1000.0, 0.0), extent)]
    agree = [PairConstraint("A", "B", (1000.0, 0.0), 0.01, LATTICE),
             PairConstraint("A", "B", (1001.0, 0.0), 2.0, OVERLAP)]
    assert solve_placements(tiles, agree).warnings == ()
    disagree = [PairConstraint("A", "B", (1000.0, 0.0), 0.01, LATTICE),
                PairConstraint("A", "B", (1008.0, 0.0), 2.0, OVERLAP)]
    warnings = solve_placements(tiles, disagree).warnings
    assert len(warnings) == 1 and "A-B" in warnings[0] and "8.0 um" in warnings[0]


def test_a_lattice_uncertainty_shrinks_with_the_number_of_sites_fitted():
    def fit(n):
        return LatticeFit(True, "", (40.0, 0.0), (20.0, 34.64), 40.0, 0.0, "hex", (0.0, 0.0), 2.0, (True,) * n)
    sigmas = {}
    for n in (100, 25):
        tiles = [TileObservation("A", (0.0, 0.0), (100.0, 100.0), fit(n)),
                 TileObservation("B", (60.0, 0.0), (100.0, 100.0), fit(n))]
        sigmas[n] = lattice_phase_constraints(tiles)[0][0].sigma_um
    # 0.25 um systematic allowance in quadrature with 2 * (2.0 um rms)^2 / n.
    assert sigmas[100] == pytest.approx(math.sqrt(0.25 ** 2 + 2 * 4.0 / 100))
    assert sigmas[25] == pytest.approx(math.sqrt(0.25 ** 2 + 2 * 4.0 / 25))


def test_an_overlap_uncertainty_falls_as_the_correlation_rises():
    plan = TilePlan(1, 2, (400, 300), 0.5, PX)
    rendered = render_tiles(plan, pitch_um=PITCH, diameter_um=DIAMETER, radius_um=3500, rotation_deg=5,
                            noise_sigma=0.05, cells=30, debris=10, seed=2, blur_px=1.5)
    observations = [TileObservation(n, rendered.true_left_top_um[n], plan.extent_um) for n in ("F0", "F1")]
    constraint = overlap_constraints(observations, rendered.images, PX, PITCH)[0][0]
    peak = correlate_overlap(rendered.images["F0"], rendered.images["F1"],
                             np.subtract(rendered.true_left_top_um["F1"], rendered.true_left_top_um["F0"]) / PX,
                             search_radius=search_radius_px(PITCH, PX)).peak
    assert constraint.sigma_um == pytest.approx(0.5 * PX / peak, rel=0.01)


def test_the_montage_places_a_tile_at_the_nearest_pixel_not_the_one_below():
    a = np.full((10, 10), 0.2, dtype=np.float32)
    b = np.full((10, 10), 0.8, dtype=np.float32)
    montage = build_montage({"a": a, "b": b}, {"a": (0.0, 0.0), "b": (20.6, 0.0)}, 1.0,
                            percentiles=(0.0, 100.0))
    assert montage.covered[5, 20] == False and montage.covered[5, 21] == True      # noqa: E712 (numpy bools)
    assert montage.image.shape[1] == 31


def test_a_crop_across_a_seam_blends_by_distance_from_each_tile_edge():
    left = np.full((40, 60), 0.2, dtype=np.float32)
    right = np.full((40, 60), 0.8, dtype=np.float32)
    # Overlap is x in [40, 60] um at 1 um/px: the left tile fades out and the right fades in.
    crop = fused_crop((50.0, 20.0), 20.0, {"L": (left, 0.0, 0.0), "R": (right, 40.0, 0.0)}, 1.0)
    row = crop.crop[crop.crop.shape[0] // 2]
    assert np.all(np.diff(row) > 0) and row[0] < 0.5 < row[-1]
    # First sample (x = 40.5): the left tile's edge ramp is 20, the right tile's is 1.
    assert row[0] == pytest.approx((0.2 * 20 + 0.8 * 1) / 21, abs=1e-6)
    assert row[-1] == pytest.approx((0.2 * 1 + 0.8 * 20) / 21, abs=1e-6)


def test_a_disk_both_tiles_hold_whole_is_attributed_to_the_first_id():
    image = np.ones((100, 100), dtype=np.float32)
    tiles = {"B": (image, 0.0, 0.0), "A": (image, 0.0, 0.0)}
    crop = fused_crop((50.0, 50.0), 20.0, tiles, 1.0)
    assert crop.disk_fraction == {"A": 1.0, "B": 1.0} and crop.attributed_tile == "A"
