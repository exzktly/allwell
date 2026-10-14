"""Exact address conventions independent of image detection."""

from dataclasses import FrozenInstanceError

import pytest

from util.nanowell_address import NanowellAddress, parse_label


@pytest.mark.parametrize("kind,sites_per_ring", [("hex", 6), ("square", 8)])
def test_all_labels_through_ring_45_are_unique_exact_inverses(kind, sites_per_ring):
    seen = set()
    for ring in range(46):
        for offset in range(sites_per_ring * ring if ring else 1):
            label = f"{ring}.{offset}"
            address = parse_label(label, kind=kind)
            assert address.ring == ring
            assert address.label == label
            assert address not in seen
            seen.add(address)
    # Independently enumerate coordinates: no address is omitted by the walk.
    expected = {(q, r) for q in range(-45, 46) for r in range(-45, 46)
                if (max(abs(q), abs(r), abs(q + r)) if kind == "hex"
                    else max(abs(q), abs(r))) <= 45}
    assert {(a.q, a.r) for a in seen} == expected


def test_first_two_hex_rings_have_the_specified_counter_clockwise_labels():
    rings = [
        [(1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)],
        [(2, 0), (1, 1), (0, 2), (-1, 2), (-2, 2), (-2, 1),
         (-2, 0), (-1, -1), (0, -2), (1, -2), (2, -2), (2, -1)],
    ]
    for ring, coordinates in enumerate(rings, 1):
        assert [NanowellAddress(q, r).label for q, r in coordinates] == [
            f"{ring}.{i}" for i in range(len(coordinates))]


def test_first_two_square_rings_have_the_specified_counter_clockwise_labels():
    rings = [
        [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)],
        [(2, 0), (2, 1), (2, 2), (1, 2), (0, 2), (-1, 2), (-2, 2), (-2, 1),
         (-2, 0), (-2, -1), (-2, -2), (-1, -2), (0, -2), (1, -2), (2, -2), (2, -1)],
    ]
    for ring, coordinates in enumerate(rings, 1):
        for offset, (q, r) in enumerate(coordinates):
            label = f"{ring}.{offset}"
            assert NanowellAddress(q, r, kind="square").label == label
            assert parse_label(label, kind="square") == NanowellAddress(q, r, kind="square")


@pytest.mark.parametrize("label", ["", "0.1", "1.6", "2.12", "01.0", "1.00",
                                   "-1.0", "1.-1", "1.0 ", "1.0\n", "1", None])
def test_malformed_label_is_named_in_the_error(label):
    with pytest.raises(ValueError) as error:
        parse_label(label)
    assert repr(label) in str(error.value)


def test_address_is_frozen_and_hex_is_the_default():
    address = NanowellAddress(1, -1)
    assert address.kind == "hex" and address.label == "1.5"
    with pytest.raises(FrozenInstanceError):
        address.q = 2
    with pytest.raises(ValueError):
        NanowellAddress(1.5, 0)
    with pytest.raises(ValueError):
        NanowellAddress(True, 0)


def test_shifted_rotated_windows_keep_addresses_and_the_complete_roster():
    """Window centre is several sites from the square: it cannot anchor addresses."""
    import numpy as np

    from pipeline.nanowell_lattice import (fit_array_boundary, fit_lattice, index_points,
                                          resolve_origin, roster)
    from tests._nanowell_synth import make_array

    recovered_rosters = []
    for angle, shift in [(0, (0, 0)), (3, (258, 0))]:
        window = (-860 + shift[0], -1892, 1892 + shift[0], 1892)
        observation = make_array(radius=1720, pitch=172, diameter=100,
                                 rotation_deg=angle, translation=shift, window=window,
                                 bubbles=((0, 0, 200),))
        fit = fit_lattice(observation.points)
        assert fit.ok, fit.reason
        boundary = fit_array_boundary(observation.points)
        assert boundary.status == "fitted", boundary.reason
        anchor = resolve_origin(fit, array_centre=boundary.centre, fiducial_margin=.2)
        assert anchor.status == "anchored" and anchor.method == "array_centre"
        assert np.linalg.norm(np.subtract(anchor.origin_xy, shift)) < 1e-7, (
            "The square at the physical array centre, not the shifted window centre, must anchor addresses")
        indexed = index_points(observation.points, fit, anchor.origin_xy)
        assert [(p.q, p.r) for p in indexed] == list(observation.addresses), (
            "Every observed site must retain its address after shifting 1.5 pitches and rotating 3 degrees")
        recovered_rosters.append(roster(fit, anchor.origin_xy, observation.radius))
    assert recovered_rosters[0] == recovered_rosters[1], "Observation windows must not change the model roster"
    assert (0, 0) in recovered_rosters[0]


def test_moved_plate_recovers_hidden_square_addresses_using_new_origin():
    import numpy as np

    from pipeline.nanowell_lattice import (FiducialCandidate, fit_lattice, fit_plate_rigid,
                                          index_points, predict_origin, resolve_origin)
    from tests._nanowell_synth import moved_plate

    origins = {f"w{i}": (9000 * (i % 4), 9000 * (i // 4)) for i in range(12)}
    before, after = moved_plate(origins, rotation_deg=3, translation=(600, -400),
                                hidden_wells=("w5",), radius=1500, pitch=172, diameter=100)
    anchors_a, anchors_b = {}, {}
    for observations, anchors in [(before, anchors_a), (after, anchors_b)]:
        for well, observation in observations.items():
            if not any(observation.square_mask):
                continue
            fit = fit_lattice(observation.points)
            square = observation.points[np.flatnonzero(observation.square_mask)[0]]
            anchor = resolve_origin(fit, fiducial=FiducialCandidate(tuple(square), .9, .1),
                                    fiducial_margin=.2)
            assert anchor.status == "anchored", anchor.reason
            anchors[well] = anchor.origin_xy
    transform = fit_plate_rigid(anchors_a, anchors_b)
    assert transform.ok, transform.reason
    hidden = after["w5"]
    assert not any(hidden.square_mask)
    # The hidden well has no usable rim in this observation. Only a small
    # interior window remains; neither its centre nor its old origin is evidence.
    offset = np.subtract(hidden.points, hidden.origin)
    mask = (offset[:, 0] > -500) & (offset[:, 0] < 700) & (abs(offset[:, 1]) < 500)
    points = hidden.points[mask]
    fit = fit_lattice(points)
    assert fit.ok, fit.reason
    prediction = predict_origin(transform, anchors_a["w5"])
    anchor = resolve_origin(fit, plate_rigid=prediction, fiducial_margin=.2)
    assert anchor.status == "anchored" and anchor.method == "plate_rigid", anchor.reason
    assert np.linalg.norm(np.subtract(anchor.origin_xy, hidden.origin)) < 1e-7, (
        "A removed plate needs the newly predicted origin; carrying its old origin changes every address")
    indexed = index_points(points, fit, anchor.origin_xy)
    truth = [address for address, keep in zip(hidden.addresses, mask) if keep]
    assert [(p.q, p.r) for p in indexed] == truth, "The hidden square's sites must keep their original addresses"
