"""What the phone layout snapshot counts as a change (``tests/phone_snapshot.diff``).

The snapshot itself is a slow, full-app test; this pins its comparison rule cheaply. A geometry field that moved
by one pixel is glyph rounding between machines and is not reported; two pixels, any other field, and a widget
that appeared or went, are.
"""

from __future__ import annotations

from tests import phone_snapshot


def _one(**fields):
    return {"Root/Button": {"x": 10, "y": 20, "w": 108, "h": 44, "min_h": 44, "font": 15.0, **fields}}


def test_a_one_pixel_difference_in_geometry_is_not_a_change():
    for field in ("x", "y", "w", "h", "min_h"):
        before = _one()
        after = _one(**{field: before["Root/Button"][field] + 1})
        assert phone_snapshot.diff(before, after) == [], field
        after = _one(**{field: before["Root/Button"][field] - 1})
        assert phone_snapshot.diff(before, after) == [], field


def test_two_pixels_are_a_change():
    assert phone_snapshot.diff(_one(), _one(w=110)) == ["changed  Root/Button  w: 108 -> 110"]
    assert phone_snapshot.diff(_one(), _one(y=18)) == ["changed  Root/Button  y: 20 -> 18"]


def test_a_field_that_is_not_geometry_must_match_exactly():
    assert phone_snapshot.diff(_one(), _one(font=15.5)) == ["changed  Root/Button  font: 15.0 -> 15.5"]


def test_a_widget_that_appears_or_goes_is_always_a_change():
    gained = {**_one(), "Root/New": {"x": 0, "y": 0, "w": 1, "h": 1}}
    assert phone_snapshot.diff(_one(), gained) == ["appeared Root/New"]
    assert phone_snapshot.diff(gained, _one()) == ["gone     Root/New"]
