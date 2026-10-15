"""Findings of an independent review of the geometry modules, each reproduced before it was fixed."""

import math

import numpy as np
import pytest

from pipeline.nanowell_lattice import (
    FiducialCandidate, OriginPrediction, fit_lattice, fit_plate_rigid, predict_origin, resolve_origin, roster)
from tests._nanowell_synth import make_array


def _rotate(xy, angle_deg, shift):
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    x, y = xy
    return x * c + y * s + shift[0], -x * s + y * c + shift[1]


def test_three_wells_are_enough_even_with_measurement_noise():
    # With three wells the winning pair fits exactly, so the median residual is zero and the old
    # cutoff rejected the third well whatever the noise: 39 of 200 noisy fits survived.
    rng = np.random.default_rng(0)
    origins = {"a": (0.0, 0.0), "b": (9000.0, 0.0), "c": (0.0, 9000.0)}
    failures = []
    for trial in range(200):
        moved = {w: tuple(np.add(_rotate(xy, 3.0, (600, -400)), rng.normal(0, 5.0, 2)))
                 for w, xy in origins.items()}
        fit = fit_plate_rigid(origins, moved)
        if not fit.ok or len(fit.inlier_wells) != 3:
            failures.append(trial)
            continue
        assert abs(math.degrees(math.atan2(fit.rotation[1][0], fit.rotation[0][0])) + 3.0) < 0.1
    assert failures == []


def test_the_origin_prediction_error_includes_the_target_wells_own_scatter():
    # Twelve wells and a target, every one scattered 5 um about one rigid motion. The transform is
    # known to a few um at the centroid, but the target's *true origin* is also off by its own 5 um,
    # which is what a gate against a pitch has to be about.
    rng = np.random.default_rng(1)
    sigma = 5.0
    grid = {f"w{i}": (9000.0 * (i % 4), 9000.0 * (i // 4)) for i in range(12)}
    target = (13500.0, 13500.0)                                  # near the anchored wells' centroid
    errors, stated, transform_only = [], [], []
    for _ in range(400):
        moved = {w: tuple(np.add(_rotate(xy, 2.0, (800, 300)), rng.normal(0, sigma, 2))) for w, xy in grid.items()}
        fit = fit_plate_rigid(grid, moved)
        assert fit.ok
        prediction = predict_origin(fit, target)
        truth = np.add(_rotate(target, 2.0, (800, 300)), rng.normal(0, sigma, 2))
        errors.append(np.asarray(prediction.position) - truth)
        stated.append(prediction.uncertainty_with_site)
        transform_only.append(prediction.uncertainty)
    per_axis_rms = float(np.sqrt(np.mean(np.square(errors)) ))
    assert per_axis_rms / np.mean(stated) == pytest.approx(1.0, abs=0.15)
    # The transform's own error is a fraction of what the target's origin actually carries.
    assert per_axis_rms / np.mean(transform_only) > 1.8


def _fit(pitch=172.0):
    array = make_array(radius=900.0, pitch=pitch, diameter=100.0, seed=0)
    fit = fit_lattice(array.points, expected_pitch=pitch)
    assert fit.ok
    return fit, array


def test_array_centre_evidence_far_from_every_site_is_refused_not_snapped():
    fit, array = _fit()
    far = resolve_origin(fit, array_centre=(1e6, 1e6), fiducial_margin=0.2)
    assert far.status == "unaddressed" and far.origin_xy is None
    assert "array_centre" in far.reason and "% of a pitch" in far.reason
    near = resolve_origin(fit, array_centre=(array.origin[0] + 0.2 * fit.pitch, array.origin[1]),
                          fiducial_margin=0.2)
    assert near.status == "anchored" and near.method == "array_centre"
    # Half a pitch off is the worst case for 'nearest site', and it is refused too.
    assert resolve_origin(fit, array_centre=(array.origin[0] + 0.45 * fit.pitch, array.origin[1]),
                          fiducial_margin=0.2).status == "unaddressed"


def test_a_distant_fiducial_is_refused_and_the_next_source_is_used():
    fit, array = _fit()
    bad = FiducialCandidate((array.origin[0] + 0.4 * fit.pitch, array.origin[1] + 0.3 * fit.pitch), 0.9, 0.1)
    result = resolve_origin(fit, fiducial=bad, array_centre=array.origin, fiducial_margin=0.2)
    assert result.status == "anchored" and result.method == "array_centre"
    only = resolve_origin(fit, fiducial=bad, fiducial_margin=0.2)
    assert only.status == "unaddressed" and "fiducial" in only.reason


def test_the_plate_rigid_gate_tests_the_targets_true_origin_not_just_the_transform():
    fit, array = _fit()
    pitch = fit.pitch
    at = tuple(array.origin)
    transform_good_but_site_not = OriginPrediction(True, "", at, 0.1 * pitch, 0.2 * pitch)   # 3 sigma: 0.3 vs 0.6 pitch
    assert resolve_origin(fit, plate_rigid=transform_good_but_site_not, fiducial_margin=0.2).status == "unaddressed"
    precise = OriginPrediction(True, "", at, 0.05 * pitch, 0.1 * pitch)
    assert resolve_origin(fit, plate_rigid=precise, fiducial_margin=0.2).method == "plate_rigid"


def test_roster_is_centred_on_the_origin_address_whatever_the_origin_argument():
    # By design: addresses are relative to the anchored origin and the square sits at the array's
    # physical centre, so the disk is centred on address (0, 0). The argument is validated only.
    fit, array = _fit()
    near = roster(fit, array.origin, 800.0)
    assert near == roster(fit, (1e6, 1e6), 800.0)
    assert (0, 0) in near and set(near) == {(-q, -r) for q, r in near}
