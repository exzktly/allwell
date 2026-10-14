"""Plate transforms tested against an independently evaluated screen rotation."""

import math

import numpy as np

from pipeline.nanowell_lattice import fit_plate_rigid, predict_origin


def _moved(xy, angle=3, shift=(600, -400)):
    x, y = xy
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return x * c + y * s + shift[0], -x * s + y * c + shift[1]


def test_rigid_fit_recovers_motion_without_scale_and_rejects_an_outlier_well():
    origins = {f"w{i}": (9000 * (i % 3), 9000 * (i // 3)) for i in range(9)}
    moved = {well: _moved(xy) for well, xy in origins.items()}
    moved["w8"] = (50000, -50000)
    fit = fit_plate_rigid(origins, moved)
    assert fit.ok, fit.reason
    assert fit.inlier_wells == tuple(f"w{i}" for i in range(8))
    rotation = np.asarray(fit.rotation)
    np.testing.assert_allclose(rotation.T @ rotation, np.eye(2), atol=1e-12)
    np.testing.assert_allclose(np.linalg.det(rotation), 1, atol=1e-12)
    np.testing.assert_allclose(fit.translation, (600, -400), atol=1e-9)
    assert dict(fit.residuals)["w8"] > 10000
    assert fit.rms_residual < 1e-8
    prediction = predict_origin(fit, (4500, 17000))
    np.testing.assert_allclose(prediction.position, _moved((4500, 17000)), atol=1e-8)
    assert prediction.ok and prediction.uncertainty < 1e-8


def test_prediction_uncertainty_uses_residuals_and_rotational_leverage():
    origins = {"a": (0, 0), "b": (100, 0), "c": (0, 100), "d": (100, 100)}
    moved = {well: np.array(_moved(xy)) for well, xy in origins.items()}
    for well, noise in zip(moved, [(1, 0), (-1, 1), (0, -1), (1, -1)]):
        moved[well] += noise
    fit = fit_plate_rigid(origins, moved)
    assert fit.ok, fit.reason
    near = predict_origin(fit, fit.source_centroid)
    far = predict_origin(fit, (10000, 10000))
    assert 0 < near.uncertainty < far.uncertainty
    residuals = np.array([dict(fit.residuals)[w] for w in fit.inlier_wells])
    sigma2 = np.sum(residuals ** 2) / (2 * len(fit.inlier_wells) - 3)
    np.testing.assert_allclose(near.uncertainty, math.sqrt(sigma2 * 2 / len(fit.inlier_wells)))


def test_insufficient_invalid_or_coincident_wells_do_not_make_a_transform():
    for source, target in [({"a": (0, 0)}, {"a": (1, 1)}),
                           ({str(i): (0, 0) for i in range(3)},
                            {str(i): (1, 1) for i in range(3)}),
                           ({str(i): (i, 0) for i in range(3)},
                            {str(i): (float("nan"), 0) for i in range(3)})]:
        fit = fit_plate_rigid(source, target)
        assert not fit.ok and fit.reason
        assert fit.rotation is None and fit.translation is None
        prediction = predict_origin(fit, (0, 0))
        assert not prediction.ok and prediction.position is None and prediction.uncertainty is None


def test_uncertainty_matches_empirical_prediction_error_without_trimming_good_wells(request):
    """Independent noisy measurements of both acquisitions, as in G8a review.

    Predictions are evaluated at noiseless coordinates so this measures the
    transform uncertainty, not new measurement noise at the prediction site.
    """
    rng = np.random.default_rng(1984)
    coordinates = np.array([(9000 * col, 9000 * row) for row in range(3) for col in range(4)])
    targets = np.array([_moved(xy) for xy in coordinates])
    sites = [coordinates.mean(axis=0), np.array((100000., 100000.))]
    predicted_variances, squared_errors = np.zeros(2), np.zeros(2)
    complete = 0
    count = 500
    for _ in range(count):
        source = coordinates + rng.normal(0, 5, coordinates.shape)
        target = targets + rng.normal(0, 5, coordinates.shape)
        fit = fit_plate_rigid(dict(enumerate(source)), dict(enumerate(target)))
        assert fit.ok, fit.reason
        complete += len(fit.inlier_wells) == 12
        for index, site in enumerate(sites):
            prediction = predict_origin(fit, site)
            predicted_variances[index] += prediction.uncertainty ** 2
            squared_errors[index] += np.sum((np.asarray(prediction.position) - _moved(site)) ** 2)
    assert complete >= 495, f"Only {complete}/500 fits retained every genuine well"
    predicted = np.sqrt(predicted_variances / count)
    empirical = np.sqrt(squared_errors / count)
    request.node.user_properties.append(("complete_fits", complete))
    request.node.user_properties.append(("predicted_rms", predicted.tolist()))
    request.node.user_properties.append(("empirical_rms", empirical.tolist()))
    np.testing.assert_allclose(predicted / empirical, np.ones(2), rtol=.1,
                               err_msg="Reported uncertainty must match empirical radial RMS, near and far")
