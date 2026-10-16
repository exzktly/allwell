"""Recover geometry against independently known lattice coordinates."""

from dataclasses import FrozenInstanceError
import math

import numpy as np
import pytest

from pipeline.nanowell_lattice import (FiducialCandidate, LatticeFit, OriginPrediction,
                                      fit_array_boundary, fit_lattice, index_points,
                                      resolve_origin, roster)
from tests._nanowell_synth import make_array


@pytest.mark.parametrize("pitch,diameter", [(172, 100), (86, 50)])
@pytest.mark.parametrize("kind", ["hex", "auto"])
@pytest.mark.parametrize("seed", [2, 42])
def test_missing_points_and_outliers_do_not_change_lattice_or_addresses(pitch, diameter, kind, seed):
    observation = make_array(radius=10 * pitch, pitch=pitch, diameter=diameter, seed=seed,
                             rotation_deg=17, translation=(631, -327),
                             dropout_fraction=.3, outlier_fraction=.1, noise_sigma=.5)
    fit = fit_lattice(observation.points, kind=kind)
    assert fit.ok, fit.reason
    assert fit.kind == "hex"
    np.testing.assert_allclose(fit.a1, observation.a1, atol=.15)
    np.testing.assert_allclose(fit.a2, observation.a2, atol=.15)
    assert abs(fit.angle_deg - 17) < .05
    assert abs(fit.basis_angle_deg - 60) < .1 and abs(fit.length_ratio - 1) < .002
    assert fit.rms_residual < 1
    result = index_points(observation.points, fit, observation.origin)
    for ground_truth, indexed in zip(observation.addresses, result):
        if ground_truth is not None:
            assert not indexed.outlier and (indexed.q, indexed.r) == ground_truth
    # Random contamination can fall on a genuine lattice site; do not claim
    # all random draws are distinguishable. Most must nevertheless be rejected.
    contaminants = result[-sum(a is None for a in observation.addresses):]
    assert sum(p.outlier for p in contaminants) / len(contaminants) > .7
    assert len(fit.inlier_mask) == len(observation.points)
    with pytest.raises(FrozenInstanceError):
        fit.pitch = 1


@pytest.mark.parametrize("kind", ["hex", "square"])
@pytest.mark.parametrize("angle", range(-25, 26, 5))
def test_nearest_east_and_counter_clockwise_basis_through_placement_tilts(kind, angle):
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    rotation = np.array([[c, s], [-s, c]])
    native = np.array([[100, 50 if kind == "hex" else 0],
                       [0, -math.sqrt(3) * 50 if kind == "hex" else -100]])
    basis = rotation @ native
    coordinates = np.array([(q, r) for q in range(-7, 8) for r in range(-7, 8)])
    points = coordinates @ basis.T + (155, -261)
    fit = fit_lattice(points, kind="auto")
    assert fit.ok and fit.kind == kind, fit.reason
    np.testing.assert_allclose(fit.a1, basis[:, 0], atol=1e-8)
    np.testing.assert_allclose(fit.a2, basis[:, 1], atol=1e-8)
    assert abs(fit.angle_deg - angle) < 1e-8
    assert np.linalg.det(np.column_stack((fit.a1, fit.a2))) < 0
    indexed = index_points(points, fit, (155, -261))
    assert [(p.q, p.r) for p in indexed] == list(map(tuple, coordinates))


@pytest.mark.parametrize("axis", ["x", "y"])
def test_mirrored_camera_uses_the_explicit_unmirrored_frame(axis):
    observation = make_array(radius=1500, pitch=172, diameter=100,
                             mirror_axis=axis, rotation_deg=-20, translation=(331, 211))
    fit = fit_lattice(observation.points, mirrored=True, mirror_axis=axis)
    assert fit.ok and fit.mirrored and fit.mirror_axis == axis, fit.reason
    np.testing.assert_allclose(fit.a1, observation.a1, atol=1e-8)
    np.testing.assert_allclose(fit.a2, observation.a2, atol=1e-8)
    result = index_points(observation.points, fit, observation.origin)
    assert [(p.q, p.r) for p in result] == list(observation.addresses)
    assert not fit_lattice(observation.points, mirrored=True).ok


def test_affine_refinement_measures_departure_from_ideal_instead_of_forcing_it():
    coords = np.array([(q, r) for q in range(-8, 9) for r in range(-8, 9)])
    basis = np.array([[173., 89.], [-4., -150.]])
    points = coords @ basis.T + (237, 333)
    fit = fit_lattice(points, expected_pitch=172)
    assert fit.ok, fit.reason
    np.testing.assert_allclose(np.column_stack((fit.a1, fit.a2)), basis, atol=1e-8)
    assert abs(fit.basis_angle_deg - 60) > .2
    assert abs(fit.length_ratio - 1) > .002
    # phase_origin is an exact lattice site nearest the retained centroid.
    np.testing.assert_allclose(fit.phase_origin, (237, 333), atol=1e-8)


def test_failed_fits_never_fabricate_a_lattice():
    invalid = [np.empty((0, 2)), [[0, 0]] * 10, [[0, 0], [1, float("nan")]],
               np.column_stack((np.arange(20), np.zeros(20))),
               np.random.default_rng(71).uniform(0, 1000, (100, 2))]
    for points in invalid:
        result = fit_lattice(points)
        assert not result.ok and result.reason
        assert result.a1 is None and result.a2 is None and result.phase_origin is None
    assert not fit_lattice([[0, 0]], expected_pitch=-1).ok
    assert not fit_lattice([[0, 0]], kind="triangle").ok


def test_indexing_rejects_residuals_and_keeps_only_the_nearer_duplicate():
    observation = make_array(radius=1000, pitch=100, diameter=50)
    fit = fit_lattice(observation.points)
    result = index_points([[2, 0], [1, 0], [48, -35], [100, 0]], fit, (0, 0))
    assert result[0].outlier and result[0].q is None and result[0].reason == "Duplicate site"
    assert (result[1].q, result[1].r) == (0, 0) and not result[1].outlier
    assert result[2].outlier and result[2].q is None and result[2].residual > 25
    assert (result[3].q, result[3].r) == (1, 0)
    with pytest.raises(ValueError):
        index_points([[0, 0]], LatticeFit(False, "No lattice"), (0, 0))


def test_origin_evidence_priority_conflicts_margin_and_three_sigma_gate():
    fit = fit_lattice(make_array(radius=1000, pitch=100, diameter=50).points)
    square = FiducialCandidate((1, 2), .9, .3)
    accepted = resolve_origin(fit, fiducial=square, array_centre=(0, 0), fiducial_margin=.2)
    assert accepted.status == "anchored" and accepted.method == "fiducial" and accepted.agreement == "agree"
    np.testing.assert_allclose(accepted.origin_xy, (0, 0), atol=1e-9)
    conflict = resolve_origin(fit, fiducial=square, array_centre=(100, 0), fiducial_margin=.2)
    assert conflict.agreement == "conflict" and conflict.method == "fiducial" and conflict.reason
    np.testing.assert_allclose(conflict.origin_xy, (0, 0), atol=1e-9)
    ambiguous = FiducialCandidate((100, 0), .8, .7)
    centre = resolve_origin(fit, fiducial=ambiguous, array_centre=(0, 0), fiducial_margin=.2)
    assert centre.method == "array_centre" and centre.agreement == "single_source"
    for sigma in (fit.pitch / 6, fit.pitch / 5, float("nan"), -1):
        declined = resolve_origin(fit, plate_rigid=OriginPrediction(True, "", (0, 0), sigma), fiducial_margin=.2)
        assert declined.status == "unaddressed" and declined.origin_xy is None and declined.reason
    prediction = resolve_origin(fit, plate_rigid=OriginPrediction(True, "", (1, 1), 2), fiducial_margin=.2)
    assert prediction.method == "plate_rigid"
    np.testing.assert_allclose(prediction.origin_xy, (0, 0), atol=1e-9)
    assert resolve_origin(fit, fiducial_margin=.2).status == "unaddressed"
    assert resolve_origin(LatticeFit(False, "No lattice"), array_centre=(0, 0), fiducial_margin=.2).status == "unaddressed"
    with pytest.raises(TypeError):
        resolve_origin(fit)


@pytest.mark.parametrize("coverage", [120, 180, 360])
def test_boundary_recovers_circle_from_partial_arc_with_interior_points(coverage):
    angles = np.linspace(math.radians(25), math.radians(25 + coverage), 61)
    rim = np.column_stack((np.cos(angles), np.sin(angles))) * 1500 + (274, -635)
    interior = (rim[::2] - (274, -635)) * .6 + (274, -635)
    boundary = fit_array_boundary(np.concatenate((rim, interior)))
    assert boundary.status == "fitted", boundary.reason
    np.testing.assert_allclose(boundary.centre, (274, -635), atol=1e-4)
    assert abs(boundary.radius - 1500) < 1e-4
    assert boundary.arc_coverage_deg >= min(coverage, 354) - .1


def test_boundary_snaps_true_array_centre_and_roster_contains_hidden_sites():
    array = make_array(radius=1500, pitch=172, diameter=100, bubbles=((0, 0, 500),),
                       rotation_deg=15, translation=(331, -222))
    fit = fit_lattice(array.points)
    boundary = fit_array_boundary(array.points)
    assert boundary.status == "fitted", boundary.reason
    assert np.linalg.norm(np.subtract(boundary.centre, array.origin)) < array.pitch / 4
    origin = resolve_origin(fit, array_centre=boundary.centre, fiducial_margin=.2)
    np.testing.assert_allclose(origin.origin_xy, array.origin, atol=1e-8)
    full = roster(fit, origin.origin_xy, boundary.radius)
    basis = np.column_stack((array.a1, array.a2))
    expected = tuple((q, r) for q in range(-20, 21) for r in range(-20, 21)
                     if np.linalg.norm(basis @ (q, r)) <= boundary.radius + fit.pitch / 4)
    assert full == expected and (0, 0) in full
    assert len(full) > len(array.points)


def test_boundary_insufficient_or_straight_points_remain_unresolved():
    for points in [np.zeros((3, 2)), np.column_stack((np.arange(10), np.zeros(10)))]:
        result = fit_array_boundary(points)
        assert result.status == "unresolved" and result.reason and result.centre is None


def test_actual_hex_boundary_with_only_a_120_degree_cap_recovers_the_square():
    array = make_array(radius=1720, pitch=172, diameter=100)
    cap = array.points[array.points[:, 0] >= 860 - 1e-8]
    fit = fit_lattice(cap)
    assert fit.ok, fit.reason
    boundary = fit_array_boundary(cap)
    assert boundary.status == "fitted", boundary.reason
    assert np.linalg.norm(boundary.centre) < 172 / 4
    origin = resolve_origin(fit, array_centre=boundary.centre, fiducial_margin=.2)
    np.testing.assert_allclose(origin.origin_xy, (0, 0), atol=1e-7,
                               err_msg="A 120-degree lattice rim must recover the physical centre, not the cap centre")


def test_outlier_nearest_centroid_cannot_choose_the_lattice_phase():
    array = make_array(radius=1720, pitch=172, diameter=100, seed=2, rotation_deg=-23,
                       translation=(17321, -9363), dropout_fraction=.3,
                       outlier_fraction=.1, noise_sigma=.7)
    fit = fit_lattice(array.points, kind="auto")
    assert fit.ok, f"An outlier near the centroid must not seed a false lattice phase: {fit.reason}"
    indexed = index_points(array.points, fit, array.origin)
    for truth, point in zip(array.addresses, indexed):
        if truth is not None:
            assert not point.outlier and (point.q, point.r) == truth
    # Independently locate the true site nearest the actual inlier centroid;
    # the phase is a site even when the centroid falls between observations.
    centre = array.points[np.array(fit.inlier_mask)].mean(axis=0)
    coordinates = np.array([(q, r) for q in range(-20, 21) for r in range(-20, 21)])
    ideal_sites = coordinates @ np.column_stack((array.a1, array.a2)).T + array.origin
    expected = ideal_sites[np.argmin(np.linalg.norm(ideal_sites - centre, axis=1))]
    np.testing.assert_allclose(fit.phase_origin, expected, atol=1,
                               err_msg="phase_origin must be a lattice site nearest the retained centroid")


@pytest.mark.parametrize("kind,angle", [("hex", -30), ("hex", 30), ("square", -45), ("square", 45)])
def test_exact_nearest_east_ties_do_not_fabricate_an_orientation(kind, angle):
    radians = math.radians(angle)
    rotation = np.array([[math.cos(radians), math.sin(radians)],
                         [-math.sin(radians), math.cos(radians)]])
    native = np.array([[100, 50 if kind == "hex" else 0],
                       [0, -math.sqrt(3) * 50 if kind == "hex" else -100]])
    coordinates = np.array([(q, r) for q in range(-7, 8) for r in range(-7, 8)])
    fit = fit_lattice(coordinates @ (rotation @ native).T, kind=kind)
    assert not fit.ok and "ambiguous" in fit.reason
    assert fit.a1 is None and fit.phase_origin is None


def _noisy_lattice(kind, angle, seed=3):
    """A 21x21 patch rotated *angle* degrees on screen, with 2 units of noise."""
    radians = math.radians(angle)
    rotation = np.array([[math.cos(radians), math.sin(radians)],
                         [-math.sin(radians), math.cos(radians)]])
    native = np.array([[172, 86 if kind == "hex" else 0],
                       [0, -math.sqrt(3) * 86 if kind == "hex" else -172]])
    coordinates = np.array([(q, r) for q in range(-10, 11) for r in range(-10, 11)])
    noise = np.random.default_rng(seed).normal(0, 2, (len(coordinates), 2))
    return coordinates @ (rotation @ native).T + noise


@pytest.mark.parametrize("kind,angle", [
    ("hex", 25.5), ("hex", 29.99), ("hex", 31), ("hex", -25.5), ("hex", -29.99), ("hex", -31),
    ("square", 40.5), ("square", 44.99), ("square", 46), ("square", -40.5), ("square", -44.99)])
def test_orientations_near_the_ambiguity_are_refused_not_relabelled(kind, angle):
    """Near 30° (hex) or 45° (square) noise flips which axis is nearest east.

    Without the limit, 29.99° fitted as -30.03° and 31° as -29.02°, both
    reported as successes with every address turned by a sixth of a turn.
    """
    fit = fit_lattice(_noisy_lattice(kind, angle), kind=kind)
    assert not fit.ok, (
        f"a {kind} lattice at {angle}° was accepted as {fit.angle_deg:.2f}°; "
        f"near the ambiguity the axis choice is not trustworthy and must be refused")
    assert "beyond" in fit.reason and fit.a1 is None and fit.phase_origin is None


@pytest.mark.parametrize("kind,angle", [("hex", 24.9), ("hex", -24.9),
                                        ("square", 39.9), ("square", -39.9)])
def test_orientations_inside_the_limit_keep_their_true_axis(kind, angle):
    fit = fit_lattice(_noisy_lattice(kind, angle), kind=kind)
    assert fit.ok, fit.reason
    assert abs(fit.angle_deg - angle) < 0.1
