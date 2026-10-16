"""Site state on rendered tiles: occlusion, coverage, rim focus, and consistency across timepoints."""

import numpy as np
import pytest

from pipeline.nanowell_detect import FITTED, PREDICTED, SiteGeometry, detect_sites, estimate_lattice_fft, refine_sites
from pipeline.nanowell_lattice import ArrayBoundary
from pipeline.nanowell_sites import (
    TimepointLattice, bubble_mask, check_consistency, check_format, rim_focus, shared_array_radius,
    site_coverage, site_visibility,
)
from tests._nanowell_synth import TilePlan, render_tiles

FORMATS = {"100um": (172.0, 100.0, 4.3), "50um": (86.0, 50.0, 2.15)}


def _scene(fmt, *, bubble_share=0.10, blur=1.0, noise=0.05, size=900, rotation=7.0):
    pitch, diameter, px = FORMATS[fmt]
    extent = size * px
    radius = 0.42 * extent
    bubble = None
    if bubble_share:
        bubble = (extent / 2 + 0.4 * radius, extent / 2 + 0.17 * radius, radius * np.sqrt(bubble_share))
    rendered = render_tiles(TilePlan(1, 1, (size, size), 0.0, px), pitch_um=pitch, diameter_um=diameter,
                            radius_um=radius, rotation_deg=rotation, noise_sigma=noise, bubble=bubble,
                            seed=1, blur_px=blur)
    return rendered, rendered.images["F0"], pitch, diameter, px, radius


def _geometries(rendered, image, px, diameter, pitch):
    """Fitted geometry for every roster site wholly inside the tile (the bubble's sites come out predicted)."""
    sites = rendered.sites_um
    edge = 900 * px
    inside = ((sites[:, 0] > 1.5 * pitch) & (sites[:, 0] < edge - 1.5 * pitch)
              & (sites[:, 1] > 1.5 * pitch) & (sites[:, 1] < edge - 1.5 * pitch))
    sites = sites[inside]
    return sites, refine_sites(image, px, sites, diameter)


# ── N5 ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_the_bubble_mask_finds_the_bubble_and_nothing_else(fmt):
    rendered, image, pitch, diameter, px, radius = _scene(fmt)
    detection = detect_sites(image, px, diameter, expected_pitch_um=pitch)
    assert detection.ok, detection.reason
    mask = bubble_mask(image, px, detection.fit, detection.boundary_centre_um, detection.boundary_radius_um)
    ys, xs = np.mgrid[0:900, 0:900]
    x_um, y_um = (xs + 0.5) * px, (ys + 0.5) * px
    bx, by, br = rendered.bubble
    truth = np.hypot(x_um - bx, y_um - by) <= br
    in_array = np.hypot(x_um - detection.boundary_centre_um[0], y_um - detection.boundary_centre_um[1]) \
        <= detection.boundary_radius_um - pitch / 2
    expected = truth & in_array
    overlap = (mask & expected).sum() / (mask | expected).sum()
    assert overlap > 0.7
    assert not mask[~in_array].any()                           # the flat floor outside the array is no bubble
    clean, clean_image, _, _, _, _ = _scene(fmt, bubble_share=0.0)
    clean_detection = detect_sites(clean_image, px, diameter, expected_pitch_um=pitch)
    clean_mask = bubble_mask(clean_image, px, clean_detection.fit, clean_detection.boundary_centre_um,
                             clean_detection.boundary_radius_um)
    assert clean_mask.sum() < 0.01 * in_array.sum()            # no bubble, so (almost) nothing flagged


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_a_black_disk_on_the_black_lattice_is_occluded_and_a_clear_site_is_not(fmt):
    rendered, image, pitch, diameter, px, radius = _scene(fmt)
    sites, geometries = _geometries(rendered, image, px, diameter, pitch)
    seen = site_visibility(image, px, geometries)
    bx, by, br = rendered.bubble
    distance = np.hypot(sites[:, 0] - bx, sites[:, 1] - by)
    hidden = distance <= br - 0.35 * pitch
    clear = distance > br + 0.35 * pitch
    visible = np.array([s.visible_fraction for s in seen])
    contrast = np.array([s.contrast for s in seen])
    assert hidden.sum() > 10 and (visible[clear] > 0.95).mean() > 0.97
    assert (visible[hidden] < 0.1).all()
    assert np.median(contrast[clear]) == pytest.approx(1.0, abs=0.1)
    assert np.abs(contrast[hidden]).max() < 0.2                # no brighter than the lattice around it
    assert all(s.occluded for s, h in zip(seen, hidden) if h)
    assert not any(s.occluded for s, c in zip(seen, clear) if c)
    # A bubble on the rim covers part of a disk: strictly between the two.
    rim = (distance > br - 0.2 * pitch) & (distance < br + 0.2 * pitch)
    assert rim.sum() > 2 and (visible[rim] > 0.05).any() and (visible[rim] < 0.95).any()


def test_a_bubble_mask_removes_visibility_from_pixels_under_it():
    rendered, image, pitch, diameter, px, radius = _scene("100um", bubble_share=0.0)
    sites, geometries = _geometries(rendered, image, px, diameter, pitch)
    centre = geometries[len(geometries) // 2]
    mask = np.zeros(image.shape, dtype=bool)
    cx, cy = centre.centre_um[0] / px - 0.5, centre.centre_um[1] / px - 0.5
    mask[:, int(round(cx)):] = True                            # everything right of the centre is bubble
    plain = site_visibility(image, px, [centre])[0]
    masked = site_visibility(image, px, [centre], mask=mask)[0]
    assert plain.visible_fraction > 0.95
    assert masked.visible_fraction == pytest.approx(0.5, abs=0.12)


def test_a_site_off_the_image_has_undefined_visibility_not_zero():
    rendered, image, pitch, diameter, px, radius = _scene("100um")
    far = SiteGeometry(0, (-500.0, -500.0), diameter / 2, PREDICTED)
    seen = site_visibility(image, px, [far])[0]
    assert (seen.visible_fraction, seen.contrast, seen.occluded) == (None, None, None)


# ── N6 ───────────────────────────────────────────────────────────────────────
def _site(centre, radius=50.0, index=0):
    return SiteGeometry(index, centre, radius, FITTED)


def test_imaged_fraction_is_the_share_of_the_disk_inside_the_tiles():
    rect = (0.0, 0.0, 1000.0, 1000.0)
    sites = [_site((500.0, 500.0), index=0),            # wholly inside
             _site((1000.0, 500.0), index=1),           # centred on the edge: half
             _site((1500.0, 500.0), index=2),           # wholly outside
             _site((1000.0, 1000.0), index=3)]          # on a corner: a quarter
    imaged = [c.imaged_fraction for c in site_coverage(sites, [rect])]
    assert imaged[0] == 1.0 and imaged[2] == 0.0
    assert imaged[1] == pytest.approx(0.5, abs=0.1) and imaged[3] == pytest.approx(0.25, abs=0.1)


def test_a_site_inside_the_overlap_of_two_tiles_is_not_counted_twice():
    rects = [(0.0, 0.0, 1000.0, 1000.0), (900.0, 0.0, 1900.0, 1000.0)]
    inside_both = site_coverage([_site((950.0, 500.0))], rects)[0]
    assert inside_both.imaged_fraction == 1.0
    # Each tile alone counts: a site in only the first, and one in only the second.
    assert site_coverage([_site((300.0, 500.0))], rects)[0].imaged_fraction == 1.0
    assert site_coverage([_site((1600.0, 500.0))], rects)[0].imaged_fraction == 1.0
    straddling_gap = site_coverage([_site((2000.0, 500.0))], rects)[0]
    assert straddling_gap.imaged_fraction == 0.0


def test_array_and_well_shares_are_undefined_without_a_boundary_and_never_raise():
    site = _site((1000.0, 500.0))
    bare = site_coverage([site], [(0.0, 0.0, 1000.0, 1000.0)])[0]
    assert (bare.inside_array_fraction, bare.inside_well_fraction) == (None, None)
    with_edges = site_coverage([site], [(0.0, 0.0, 2000.0, 1000.0)], array_centre_um=(500.0, 500.0),
                               array_radius_um=500.0, well_centre_um=(1000.0, 500.0), well_radius_um=3000.0)[0]
    assert with_edges.inside_array_fraction == pytest.approx(0.5, abs=0.1)       # centred on the array's edge
    assert with_edges.inside_well_fraction == 1.0
    # Wholly inside and wholly outside the array's edge are told apart (a centred-on-the-edge site is half either way).
    inside, outside = site_coverage([_site((900.0, 500.0)), _site((1100.0, 500.0))], [(0.0, 0.0, 2000.0, 1000.0)],
                                    array_centre_um=(500.0, 500.0), array_radius_um=500.0)
    assert (inside.inside_array_fraction, outside.inside_array_fraction) == (1.0, 0.0)
    assert site_coverage([site], [])[0].imaged_fraction == 0.0                   # no tiles at all
    assert site_coverage([], [(0.0, 0.0, 1.0, 1.0)]) == []


# ── N7 ───────────────────────────────────────────────────────────────────────
def test_rim_focus_falls_as_the_image_is_blurred_and_is_undefined_where_nothing_was_measured():
    values = []
    for blur in (1.0, 2.5, 5.0):
        rendered, image, pitch, diameter, px, _ = _scene("100um", bubble_share=0.0, blur=blur, noise=0.0)
        site = np.array(rendered.array.origin) + np.array(rendered.array.a1)
        geometry = refine_sites(image, px, site[None, :], diameter, contrast=0.05)[0]
        assert geometry.geometry == FITTED
        values.append(rim_focus(image, px, geometry))
    assert all(v is not None for v in values)
    assert values[0] > values[1] > values[2]
    predicted = SiteGeometry(0, tuple(site), diameter / 2, PREDICTED)
    assert rim_focus(image, px, predicted) is None
    off = SiteGeometry(0, (5.0, 5.0), diameter / 2, FITTED)
    assert rim_focus(image, px, off) is None


# ── N8 / N8a ─────────────────────────────────────────────────────────────────
def _tp(name, pitch=172.0, angle=7.0, rotation=0.0, roster=None):
    return TimepointLattice(name, pitch, angle, rotation, roster)


def test_consistency_names_the_violation_and_never_raises():
    roster = frozenset({(0, 0), (1, 0), (0, 1)})
    result = check_consistency([
        _tp("0h", roster=roster),
        _tp("24h", pitch=172.0 * 1.003, angle=7.3, roster=roster),             # inside both tolerances
        _tp("48h", pitch=172.0 * 1.008, roster=roster),
        _tp("72h", angle=8.0, roster=roster),
        _tp("96h", roster=roster | {(5, 5)}),
        _tp("120h", pitch=172.0 * 1.02, angle=9.0, roster=roster - {(0, 1)})])
    status = {c.timepoint: c.status for c in result}
    assert status == {"0h": "consistent", "24h": "consistent", "48h": "pitch_mismatch",
                      "72h": "angle_mismatch", "96h": "roster_mismatch", "120h": "pitch_mismatch"}
    assert "+2.00 %" in result[5].detail and "angle differs" in result[5].detail and "1 addresses" in result[5].detail


def test_the_plate_rotation_is_removed_before_the_angle_is_compared():
    # The plate was put back rotated by 10 degrees: the lattice reads 17 where it read 7. That is
    # the plate, not a change in the lattice.
    result = check_consistency([_tp("0h", angle=7.0), _tp("24h", angle=17.0, rotation=10.0)])
    assert result[1].status == "consistent"
    assert check_consistency([_tp("0h", angle=7.0), _tp("24h", angle=17.0)])[1].status == "angle_mismatch"


def test_an_angle_folds_across_the_lattices_own_period():
    # 29.8 and -29.8 are 0.4 degrees apart on a lattice that repeats every 60.
    assert check_consistency([_tp("0h", angle=29.8), _tp("24h", angle=-29.8)])[1].status == "consistent"
    assert check_consistency([_tp("0h", angle=29.8), _tp("24h", angle=-28.0)])[1].status == "angle_mismatch"


def test_the_reference_can_be_named_and_an_empty_list_is_empty():
    result = check_consistency([_tp("0h", pitch=180.0), _tp("24h"), _tp("48h")], reference="24h")
    assert [c.status for c in result] == ["pitch_mismatch", "consistent", "consistent"]
    assert check_consistency([]) == []


def _boundary(radius, status="fitted"):
    return ArrayBoundary(status, "" if status == "fitted" else "unresolved", (0.0, 0.0), radius, 300.0)


def test_one_radius_per_well_is_the_median_of_the_timepoints_that_fitted():
    boundaries = {"0h": _boundary(3000.0), "24h": _boundary(3010.0), "48h": _boundary(2990.0),
                  "72h": _boundary(3600.0), "96h": ArrayBoundary("unresolved", "arc too short")}
    shared = shared_array_radius(boundaries, pitch_um=172.0)
    assert shared.status == "shared" and shared.radius_um == pytest.approx(3005.0)
    assert shared.deviations_um["72h"] == pytest.approx(595.0) and shared.deviations_um["96h"] is None
    assert shared.deviations_um["0h"] == pytest.approx(-5.0)
    assert shared.flags == {"72h": "radius_disagrees", "96h": "no_boundary"}
    # The median, not the mean: the 3600 outlier would drag a mean to 3120.
    assert shared.radius_um != pytest.approx(np.mean([3000, 3010, 2990, 3600]))


def test_no_fitted_boundary_gives_no_radius_and_says_so():
    shared = shared_array_radius({"0h": ArrayBoundary("unresolved", "x"), "24h": ArrayBoundary("unresolved", "y")}, 172.0)
    assert shared.radius_um is None and shared.status == "unresolved"
    assert shared.flags == {"0h": "no_boundary", "24h": "no_boundary"}
    assert shared_array_radius({}, 172.0).radius_um is None


# ── N9 ───────────────────────────────────────────────────────────────────────
def test_a_50um_array_analysed_with_the_100um_setting_is_a_format_mismatch():
    rendered, image, pitch, diameter, px, _ = _scene("50um")
    estimate = estimate_lattice_fft(image, px)                 # the pitch is read before any filter is chosen
    assert estimate.ok and estimate.pitch_um == pytest.approx(86.0, rel=0.005)
    wrong = check_format(estimate.pitch_um, 100.0)
    assert wrong.ok is False and wrong.status == "format_mismatch"
    assert "100 um" in wrong.reason and ("85." in wrong.reason or "86." in wrong.reason)
    # The right setting passes the pitch test but the pitch alone cannot confirm it: a setting that is
    # too small passes too, so without fitted sites the answer is "unverified", not "ok".
    unverified = check_format(estimate.pitch_um, 50.0)
    assert unverified.ok is None and unverified.status == "unverified"
    assert check_format(estimate.pitch_um, 50.0, [25.0, 24.8, 25.2]).ok is True


def test_a_setting_that_fits_the_pitch_can_still_be_wrong_about_the_fitted_nanowells():
    assert check_format(172.0, 100.0, [50.0, 49.0, 51.0, None, float("nan")]).ok
    mismatch = check_format(172.0, 100.0, [30.0, 31.0, 29.0])            # the nanowells are 60 um across
    assert mismatch.ok is False and "60.0 um across" in mismatch.reason
    for nothing in ([], None, [None, float("nan")]):
        unverified = check_format(172.0, 100.0, nothing)
        assert unverified.ok is None and unverified.status == "unverified"
    assert check_format(None, 100.0).status == "unverified"                    # no measured pitch: no error either
    assert check_format(float("nan"), 100.0, [50.0]).status == "unverified"
    # A 100 um array read with the 50 um setting fits no site; that is unverified, never "ok".
    assert check_format(172.0, 50.0, []).ok is None
    # A hair inside the 25 % tolerance passes, a hair outside fails.
    assert check_format(172.0, 100.0, [37.6]).ok is True
    assert check_format(172.0, 100.0, [37.4]).ok is False


def test_the_detection_with_the_matching_setting_agrees_with_the_format_check():
    for fmt in sorted(FORMATS):
        rendered, image, pitch, diameter, px, _ = _scene(fmt)
        detection = detect_sites(image, px, diameter, expected_pitch_um=pitch)
        geometries = refine_sites(image, px, detection.points_um, diameter)
        radii = [g.radius_um for g in geometries if g.geometry == FITTED]
        assert check_format(detection.fit.pitch, diameter, radii).ok


# ── review findings ──────────────────────────────────────────────────────────
def test_illumination_falling_to_a_third_at_the_rim_is_not_read_as_a_bubble():
    # With one global midpoint, an array whose illumination falls to 36 % at its rim flagged 102 of 313
    # sites occluded with no bubble in the image. The reference is now local.
    rendered, image, pitch, diameter, px, radius = _scene("100um", bubble_share=0.0)
    yy, xx = np.mgrid[0:900, 0:900]
    rho = np.hypot((xx + 0.5) * px - rendered.array.origin[0], (yy + 0.5) * px - rendered.array.origin[1])
    dimmed = (image * (1 - 0.64 * np.clip(rho / radius, 0, 1) ** 2)).astype(np.float32)
    sites, geometries = _geometries(rendered, dimmed, px, diameter, pitch)
    seen = site_visibility(dimmed, px, geometries)
    assert not any(s.occluded for s in seen)
    assert np.mean([s.visible_fraction > 0.9 for s in seen]) > 0.95


def test_a_dark_cell_on_part_of_a_nanowell_is_not_an_occlusion_but_one_covering_most_of_it_is():
    # A cell on a third of a disk leaves it mostly bright. One covering most of it reads as occluded, as a
    # bubble does: telling them apart is the contents section's job, and the limit is stated.
    rendered, image, pitch, diameter, px, radius = _scene("100um", bubble_share=0.0)
    sites, geometries = _geometries(rendered, image, px, diameter, pitch)
    target = geometries[len(geometries) // 2]
    cx, cy = target.centre_um[0] / px - 0.5, target.centre_um[1] / px - 0.5
    celled = image.copy()
    yy, xx = np.mgrid[0:900, 0:900]
    celled[(xx - cx - 3.0) ** 2 + (yy - cy) ** 2 <= 6.5 ** 2] = 0.15       # about a third of the disk
    seen = site_visibility(celled, px, geometries)[len(geometries) // 2]
    assert seen.visible_fraction < 0.8                          # part of the disk really is dark
    assert seen.occluded is False                               # but the nanowell is there
    assert site_visibility(image, px, geometries)[len(geometries) // 2].visible_fraction > 0.95
    big = image.copy()
    big[(xx - cx - 1.0) ** 2 + (yy - cy) ** 2 <= 9.5 ** 2] = 0.15
    assert site_visibility(big, px, geometries)[len(geometries) // 2].occluded is True


def test_the_bubble_mask_survives_most_of_the_array_being_hidden():
    # The typical amplitude used to be the array's own 80th percentile, which is itself bubble when most of
    # the array is hidden: with 84 % hidden the mask flagged 69 % of it, and with all of it hidden, 10 %.
    pitch, diameter, px = FORMATS["100um"]
    extent = 900 * px
    radius = 0.42 * extent
    rendered = render_tiles(TilePlan(1, 1, (900, 900), 0.0, px), pitch_um=pitch, diameter_um=diameter,
                            radius_um=radius, rotation_deg=7.0, noise_sigma=0.05, seed=1, blur_px=1.0,
                            bubble=(extent / 2 + 0.3 * radius, extent / 2, radius * 0.92))
    image = rendered.images["F0"]
    bx, by, br = rendered.bubble
    detection = detect_sites(image, px, diameter, expected_pitch_um=pitch)
    assert detection.ok, detection.reason
    centre = rendered.array.origin
    ys, xs = np.mgrid[0:900, 0:900]
    x_um, y_um = (xs + 0.5) * px, (ys + 0.5) * px
    interior = np.hypot(x_um - centre[0], y_um - centre[1]) <= radius - pitch / 2
    truth = (np.hypot(x_um - bx, y_um - by) <= br) & interior
    assert truth.sum() > 0.7 * interior.sum()                   # the bubble really is most of the array
    mask = bubble_mask(image, px, detection.fit, centre, radius, reference_points_um=detection.points_um)
    assert (mask & truth).sum() / truth.sum() > 0.75            # most of the true bubble is flagged
    assert (mask & ~truth).sum() < 0.1 * interior.sum()         # and little of the clear array
    # Without the sites to read the typical amplitude from, the old percentile reference is the fallback.
    fallback = bubble_mask(image, px, detection.fit, centre, radius)
    assert (fallback & truth).sum() < (mask & truth).sum()


def test_disk_samples_are_spread_evenly_over_the_area():
    from pipeline.nanowell_sites import _disk_samples
    samples = _disk_samples()
    assert np.hypot(samples[:, 0], samples[:, 1]).max() <= 1.0
    # The share of the points beyond a chord at c is the share of the disk's area there.
    for c in (0.0, 0.3, 0.6, 0.9):
        area = (np.arccos(c) - c * np.sqrt(1 - c * c)) / np.pi
        assert (samples[:, 0] > c).mean() == pytest.approx(area, abs=0.02)
    # And a site centred on the array's edge reads as half inside it.
    centred = site_coverage([_site((1000.0, 500.0))], [(0.0, 0.0, 2000.0, 1000.0)],
                            array_centre_um=(500.0, 500.0), array_radius_um=500.0)[0]
    assert centred.inside_array_fraction == pytest.approx(0.5, abs=0.04)
