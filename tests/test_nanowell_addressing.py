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
