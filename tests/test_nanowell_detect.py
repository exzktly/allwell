"""Lattice and site detection on tiles rendered from a known array (100 um and 50 um formats)."""

import numpy as np
import pytest

from pipeline.nanowell_detect import detect_sites, estimate_lattice_fft
from tests._nanowell_synth import TilePlan, render_tiles

#: (pitch um, diameter um, pixel size um): both formats image 40 px per pitch, so the same
#: pixel-scale code is exercised at the two physical scales a plate format implies.
FORMATS = {"100um": (172.0, 100.0, 4.3), "50um": (86.0, 50.0, 2.15)}


def _scene(fmt, *, rotation=7.0, bubble_share=0.10, noise=0.05, seed=1, cells=0, size=900):
    pitch, diameter, px = FORMATS[fmt]
    extent = size * px
    radius = 0.42 * extent
    bubble = None
    if bubble_share:
        bubble = (extent / 2 + 0.4 * radius, extent / 2 + 0.17 * radius,
                  radius * np.sqrt(bubble_share))                 # area share of the array's disk
    rendered = render_tiles(TilePlan(1, 1, (size, size), 0.0, px), pitch_um=pitch, diameter_um=diameter,
                            radius_um=radius, rotation_deg=rotation, noise_sigma=noise, cells=cells,
                            bubble=bubble, seed=seed, blur_px=1.0)
    return rendered, rendered.images["F0"], pitch, diameter, px


def _visible_sites(rendered):
    sites = rendered.sites_um
    if rendered.bubble is None:
        return sites
    bx, by, br = rendered.bubble
    return sites[np.hypot(sites[:, 0] - bx, sites[:, 1] - by) > br + 20]


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_the_fourier_peaks_give_pitch_and_angle_through_a_bubble_over_a_tenth_of_the_array(fmt):
    rendered, image, pitch, _, px = _scene(fmt)
    result = estimate_lattice_fft(image, px, expected_pitch_um=pitch)
    assert result.ok, result.reason
    assert result.kind == "hex"
    assert result.pitch_um == pytest.approx(pitch, rel=0.003)
    assert result.angle_deg == pytest.approx(7.0, abs=0.1)
    # The sites under the bubble are really absent from the image.
    assert len(_visible_sites(rendered)) < len(rendered.sites_um) * 0.93


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_the_estimate_reads_the_image_not_the_expectation(fmt):
    _, image, pitch, _, px = _scene(fmt, rotation=-13.0)
    wrong = estimate_lattice_fft(image, px, expected_pitch_um=pitch * 1.3)          # 30 % off: still in range
    blind = estimate_lattice_fft(image, px)
    for result in (wrong, blind):
        assert result.ok, result.reason
        assert result.pitch_um == pytest.approx(pitch, rel=0.003)
        assert result.angle_deg == pytest.approx(-13.0, abs=0.1)


def test_an_image_with_no_lattice_is_refused_with_a_reason():
    noise = np.random.default_rng(0).normal(size=(400, 400))
    result = estimate_lattice_fft(noise, 4.3, expected_pitch_um=172.0)
    assert result.ok is False and result.pitch_um is None and result.reason
    assert estimate_lattice_fft(np.zeros((30, 30)), 4.3).ok is False
    assert estimate_lattice_fft(np.full((200, 200), np.nan), 4.3).ok is False


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_candidates_are_the_visible_sites_and_the_refined_lattice_is_exact(fmt):
    rendered, image, pitch, diameter, px = _scene(fmt)
    detection = detect_sites(image, px, diameter, expected_pitch_um=pitch)
    assert detection.ok, detection.reason
    truth = rendered.sites_um
    bx, by, br = rendered.bubble
    hidden = np.hypot(truth[:, 0] - bx, truth[:, 1] - by) <= br - 10
    # Precision: every candidate sits on a true site (within a tenth of a pitch).
    nearest = np.min(np.linalg.norm(detection.points_um[:, None, :] - truth[None, :, :], axis=2), axis=1)
    assert nearest.max() < 0.1 * pitch
    # No half-pixel bias: the candidates' mean offset from the truth is a fraction of a pixel.
    offsets = np.array([detection.points_um[i] - truth[np.argmin(np.linalg.norm(truth - detection.points_um[i], axis=1))]
                        for i in range(len(detection.points_um))])
    assert np.abs(np.median(offsets, axis=0)).max() < 0.15 * px
    # Recall: nearly every visible site in the tile is found; almost none under the bubble is.
    inside = ((truth[:, 0] > 0.5 * pitch) & (truth[:, 0] < 900 * px - 0.5 * pitch)
              & (truth[:, 1] > 0.5 * pitch) & (truth[:, 1] < 900 * px - 0.5 * pitch))
    found = np.array([np.min(np.linalg.norm(detection.points_um - site, axis=1)) < 0.15 * pitch
                      for site in truth])
    assert found[inside & ~hidden].mean() > 0.97
    assert found[inside & hidden].mean() < 0.05
    fit = detection.fit
    assert fit.ok and fit.pitch == pytest.approx(pitch, rel=0.002)
    assert fit.angle_deg == pytest.approx(7.0, abs=0.15)
    assert detection.boundary_radius_um == pytest.approx(0.42 * 900 * px, rel=0.06)


def test_a_nanowell_too_small_in_pixels_is_refused_not_mis_detected():
    _, image, pitch, diameter, px = _scene("100um")
    result = detect_sites(image, px, diameter, expected_pitch_um=pitch)
    assert result.ok
    tiny = detect_sites(image, 40.0, diameter, expected_pitch_um=pitch)          # a 2.5 px diameter
    assert tiny.ok is False and "too few pixels" in tiny.reason


# ── the central square: fiducial and the axis check ──────────────────────────
from pipeline.nanowell_detect import (  # noqa: E402  (grouped with the tests that use them)
    FITTED, PREDICTED, check_orientation, refine_sites, score_fiducial, square_score,
)
from pipeline.nanowell_lattice import resolve_origin  # noqa: E402


def _detected(fmt, **scene):
    rendered, image, pitch, diameter, px = _scene(fmt, **scene)
    detection = detect_sites(image, px, diameter, expected_pitch_um=pitch)
    assert detection.ok, detection.reason
    return rendered, image, pitch, diameter, px, detection


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_the_square_outscores_every_circle_by_a_wide_margin_and_is_at_the_centre(fmt):
    rendered, image, pitch, diameter, px, detection = _detected(fmt, bubble_share=0.0)
    candidate, scores = score_fiducial(image, px, diameter, detection.points_um,
                                       centre_um=detection.boundary_centre_um, search_radius_um=2.2 * pitch)
    assert candidate is not None and len(scores) >= 7
    centre = np.array(rendered.array.origin)
    assert np.linalg.norm(np.subtract(candidate.position, centre)) < 0.2 * pitch
    assert candidate.score > 0.5 and candidate.runner_up_score < 0.2          # a circle scores near zero
    assert candidate.score - candidate.runner_up_score > 0.4
    # The score is recorded for every site, not only the winner.
    assert sorted(s.coherence for s in scores)[-1] == pytest.approx(candidate.score)


@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_a_bubble_over_the_square_leaves_no_fiducial_and_the_array_centre_anchors_the_well(fmt):
    pitch, diameter, px = FORMATS[fmt]
    extent = 900 * px
    rendered = render_tiles(TilePlan(1, 1, (900, 900), 0.0, px), pitch_um=pitch, diameter_um=diameter,
                            radius_um=0.42 * extent, rotation_deg=7.0, noise_sigma=0.05, seed=1,
                            bubble=(extent / 2, extent / 2, 0.8 * pitch), blur_px=1.0)
    image = rendered.images["F0"]
    detection = detect_sites(image, px, diameter, expected_pitch_um=pitch)
    assert detection.ok, detection.reason
    candidate, _ = score_fiducial(image, px, diameter, detection.points_um,
                                  centre_um=detection.boundary_centre_um, search_radius_um=2.2 * pitch)
    assert candidate.score - candidate.runner_up_score < 0.15                 # nothing square is left
    resolved = resolve_origin(detection.fit, fiducial=candidate, array_centre=detection.boundary_centre_um,
                              fiducial_margin=0.3)
    assert resolved.status == "anchored" and resolved.method == "array_centre"
    assert np.linalg.norm(np.subtract(resolved.origin_xy, rendered.array.origin)) < 0.1 * pitch


def test_a_rotation_the_lattice_alone_folds_back_is_caught_by_the_squares_edges():
    # Tracker N3a. A hexagonal array rotated 40 degrees is fitted as -20 (the axis nearest east),
    # inside the +-25 limit and wrong by a sixth of a turn; the square still reads 40.
    rendered, image, pitch, diameter, px, detection = _detected("100um", rotation=40.0, bubble_share=0.0)
    assert detection.fit.angle_deg == pytest.approx(-20.0, abs=0.3)
    candidate, scores = score_fiducial(image, px, diameter, detection.points_um,
                                       centre_um=detection.boundary_centre_um, search_radius_um=2.2 * pitch)
    square = max(scores, key=lambda s: s.coherence)
    assert square.orientation_deg == pytest.approx(40.0, abs=1.5)
    verdict = check_orientation(detection.fit, square.orientation_deg)
    assert verdict.ok is False and "folded" in verdict.reason
    assert verdict.discrepancy_deg == pytest.approx(-30.0, abs=2.0)


@pytest.mark.parametrize("rotation", [7.0, -20.0, 24.0])
def test_a_lattice_axis_the_square_agrees_with_is_accepted(rotation):
    _, image, pitch, diameter, px, detection = _detected("100um", rotation=rotation, bubble_share=0.0)
    _, scores = score_fiducial(image, px, diameter, detection.points_um,
                               centre_um=detection.boundary_centre_um, search_radius_um=2.2 * pitch)
    square = max(scores, key=lambda s: s.coherence)
    verdict = check_orientation(detection.fit, square.orientation_deg)
    assert verdict.ok and abs(verdict.discrepancy_deg) < 2.0


def test_the_square_to_axis_angle_is_an_assumption_the_caller_can_change():
    _, image, pitch, diameter, px, detection = _detected("100um", rotation=7.0, bubble_share=0.0)
    _, scores = score_fiducial(image, px, diameter, detection.points_um,
                               centre_um=detection.boundary_centre_um, search_radius_um=2.2 * pitch)
    observed = max(scores, key=lambda s: s.coherence).orientation_deg
    assert check_orientation(detection.fit, observed, square_to_axis_deg=0.0).ok
    # A mask whose square were cut 30 degrees off the lattice row would read exactly as a fold.
    assert check_orientation(detection.fit, observed, square_to_axis_deg=30.0).ok is False
    assert check_orientation(detection.fit, observed + 30.0, square_to_axis_deg=30.0).ok
    assert check_orientation(detection.fit, observed, tolerance_deg=0.01).ok is False or abs(
        check_orientation(detection.fit, observed).discrepancy_deg) < 0.01


def test_scores_are_none_off_the_image_and_a_flat_patch_is_not_a_square():
    _, image, pitch, diameter, px = _scene("100um")
    assert square_score(image, px, diameter, (5.0, 5.0)) is None
    assert square_score(np.zeros((200, 200)), px, diameter, (400.0, 400.0)) is None


# ── per-site centre and radius ───────────────────────────────────────────────
@pytest.mark.parametrize("fmt", sorted(FORMATS))
def test_each_site_is_fitted_or_predicted_never_dropped_and_a_bubble_site_is_predicted(fmt):
    rendered, image, pitch, diameter, px = _scene(fmt)
    truth = rendered.sites_um
    inside = ((truth[:, 0] > 1.5 * pitch) & (truth[:, 0] < 900 * px - 1.5 * pitch)
              & (truth[:, 1] > 1.5 * pitch) & (truth[:, 1] < 900 * px - 1.5 * pitch))
    truth = truth[inside]
    rng = np.random.default_rng(3)
    given = truth + rng.uniform(-0.12 * pitch, 0.12 * pitch, truth.shape)       # a lattice that is a little off
    bx, by, br = rendered.bubble
    distance = np.hypot(truth[:, 0] - bx, truth[:, 1] - by)
    hidden = distance <= br - 0.35 * pitch                  # wholly under the bubble
    clear = distance > br + 0.35 * pitch                    # wholly clear of it; the rim is neither
    sites = refine_sites(image, px, given, diameter)
    assert [s.index for s in sites] == list(range(len(truth)))               # one row per site, in order
    fitted = np.array([s.geometry == FITTED for s in sites])
    assert fitted[clear].mean() > 0.97
    assert not fitted[hidden].any() and hidden.sum() > 10
    # Fitted sites land on the truth to well under a pixel, with the right radius.
    error_px = np.array([np.hypot(*(np.subtract(s.centre_um, t) / px)) for s, t in zip(sites, truth)])
    # (Sites on the bubble's rim are partly covered: whether they fit is for N5's visible fraction.)
    sure = fitted & clear
    assert np.median(error_px[sure]) < 0.25 and error_px[sure].max() < 0.8
    radii = np.array([s.radius_um for s in sites])
    assert np.median(radii[sure]) == pytest.approx(diameter / 2, rel=0.01)
    assert radii[sure] == pytest.approx(diameter / 2, rel=0.06)
    # A predicted site keeps the position it was given and takes the well's median fitted radius.
    for site, g in zip(sites, given):
        if site.geometry == PREDICTED:
            assert site.centre_um == (float(g[0]), float(g[1])) and site.rms_um is None
            assert site.radius_um == pytest.approx(np.median(radii[fitted]))


def test_a_site_clipped_by_the_image_edge_is_predicted_and_never_raises():
    rendered, image, pitch, diameter, px = _scene("100um", bubble_share=0.0)
    corner = [(0.2 * pitch, 0.2 * pitch), (900 * px - 0.1 * pitch, 450 * px), (450 * px, 900 * px + 20.0)]
    ordinary = (np.array(rendered.array.origin) + np.array(rendered.array.a1))[None, :]      # a circle, not the square
    sites = refine_sites(image, px, np.vstack([corner, ordinary]), diameter)
    assert [s.geometry for s in sites[:3]] == [PREDICTED] * 3
    assert sites[3].geometry == FITTED


def test_the_square_is_fitted_only_when_it_is_allowed_to_scatter_more_than_a_circle():
    rendered, image, pitch, diameter, px = _scene("100um", bubble_share=0.0)
    centre = np.array(rendered.array.origin)[None, :]
    strict = refine_sites(image, px, centre, diameter)[0]
    relaxed = refine_sites(image, px, centre, diameter, relaxed={0})[0]
    assert relaxed.geometry == FITTED
    assert np.hypot(*(np.subtract(relaxed.centre_um, centre[0]) / px)) < 1.0
    assert strict.geometry in {FITTED, PREDICTED}
