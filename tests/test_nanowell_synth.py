"""Ground-truth geometry is checked independently of the fitting code."""

import math

import numpy as np
import pytest

from tests._nanowell_synth import make_array, moved_plate


@pytest.mark.parametrize("diameter,pitch", [(100.0, 172.0), (50.0, 86.0)])
def test_both_formats_have_circular_rosters_and_the_square_at_zero(diameter, pitch):
    array = make_array(radius=3000, pitch=pitch, diameter=diameter)
    assert len(array.addresses) == len(array.roster) == len(array.points)
    assert (0, 0) in array.roster
    assert sum(array.square_mask) == 1
    np.testing.assert_array_equal(array.points[np.asarray(array.square_mask)], [[0, 0]])
    for (q, r), (x, y) in zip(array.addresses, array.points):
        np.testing.assert_allclose((x, y), (pitch * (q + r / 2), -pitch * math.sqrt(3) * r / 2))
        assert math.hypot(x, y) <= 3000 + 1e-8


def test_bubbles_dropout_clipping_and_outliers_never_change_the_roster():
    options = dict(radius=1000, pitch=172, diameter=100, seed=12)
    original = make_array(**options)
    damaged = make_array(**options, bubbles=[(0, 0, 200)], dropout_fraction=0.3,
                         outlier_fraction=0.1, window=(-500, -500, 800, 800))
    assert damaged.roster == original.roster
    assert (0, 0) not in damaged.addresses
    assert not any(damaged.square_mask)
    assert 0 < len(damaged.points) < len(original.points)
    assert None in damaged.addresses
    for address, point in zip(damaged.addresses, damaged.points):
        assert -500 <= point[0] <= 800 and -500 <= point[1] <= 800
        if address is not None:
            assert np.linalg.norm(point) > 200
    repeated = make_array(**options, bubbles=[(0, 0, 200)], dropout_fraction=0.3,
                          outlier_fraction=0.1, window=(-500, -500, 800, 800))
    np.testing.assert_array_equal(damaged.points, repeated.points)
    assert damaged.addresses == repeated.addresses


@pytest.mark.parametrize("mirror_axis", [None, "x", "y"])
def test_pose_uses_screen_counter_clockwise_rotation_and_explicit_mirroring(mirror_axis):
    array = make_array(radius=400, pitch=172, diameter=100, rotation_deg=25,
                       translation=(200, 350), mirror_axis=mirror_axis)
    angle = math.radians(25)
    east = np.array([172 * math.cos(angle), -172 * math.sin(angle)])
    if mirror_axis == "x":
        east[0] *= -1
    elif mirror_axis == "y":
        east[1] *= -1
    np.testing.assert_allclose(array.points[array.addresses.index((1, 0))], east + (200, 350))
    np.testing.assert_allclose(array.a1, east)
    assert array.origin == (200, 350)
    assert not array.points.flags.writeable


def test_plate_motion_exceeds_a_pitch_and_hides_only_the_named_square():
    origins = {"A01": (0, 0), "A02": (9000, 0), "B01": (0, 9000), "B02": (9000, 9000)}
    before, after = moved_plate(origins, rotation_deg=3, translation=(600, -400),
                                hidden_wells={"B02"}, radius=1000, pitch=172, diameter=100)
    angle = math.radians(3)
    for well, (x, y) in origins.items():
        np.testing.assert_allclose(after[well].origin,
                                   (x * math.cos(angle) + y * math.sin(angle) + 600,
                                    -x * math.sin(angle) + y * math.cos(angle) - 400))
        assert before[well].roster == after[well].roster
        assert np.linalg.norm(np.array(after[well].origin) - before[well].origin) > 172
    assert not any(after["B02"].square_mask)
    assert all(sum(after[well].square_mask) == 1 for well in ("A01", "A02", "B01"))
