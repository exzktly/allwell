"""Serpentine plate-position labelling (``util.plate_grid``)."""

from __future__ import annotations

import pytest

from util.plate_grid import position_to_well_fov, serpentine_well_labels


def test_row_snake_reverses_alternate_rows():
    # Row 0 left-to-right, row 1 right-to-left.
    assert serpentine_well_labels(2, 3) == ["A01", "A02", "A03", "B03", "B02", "B01"]


def test_col_snake_reverses_alternate_columns():
    # Column 0 top-to-bottom, column 1 bottom-to-top.
    assert serpentine_well_labels(2, 3, direction="col") == [
        "A01", "B01", "B02", "A02", "A03", "B03",
    ]


def test_single_row_is_left_to_right():
    assert serpentine_well_labels(1, 4) == ["A01", "A02", "A03", "A04"]


def test_origin_offsets_the_block():
    assert serpentine_well_labels(2, 2, origin="B02") == ["B02", "B03", "C03", "C02"]


def test_length_is_rows_times_cols():
    assert len(serpentine_well_labels(3, 4)) == 12


def test_full_plate_fits():
    labels = serpentine_well_labels(8, 12)
    assert len(labels) == 96
    assert labels[0] == "A01"
    assert labels[-1] == "H01"  # row H (even index 7) snakes back to column 1


@pytest.mark.parametrize("origin", ["A00", "Z01", "I01", "A13", "notawell"])
def test_invalid_origin_rejected(origin):
    with pytest.raises(ValueError):
        serpentine_well_labels(2, 2, origin=origin)


def test_grid_overflowing_plate_rejected():
    with pytest.raises(ValueError):
        serpentine_well_labels(2, 2, origin="H12")  # would need row I, column 13
    with pytest.raises(ValueError):
        serpentine_well_labels(9, 1)  # 9 rows past H


@pytest.mark.parametrize("rows,cols", [(0, 3), (2, 0), (-1, 2)])
def test_nonpositive_dimensions_rejected(rows, cols):
    with pytest.raises(ValueError):
        serpentine_well_labels(rows, cols)


def test_unknown_direction_rejected():
    with pytest.raises(ValueError):
        serpentine_well_labels(2, 2, direction="diagonal")


def test_position_split_is_well_major():
    labels = ["A01", "A02"]
    # 3 FOVs per well: positions 0-2 → A01 f0-f2, positions 3-5 → A02 f0-f2.
    assert position_to_well_fov(0, well_labels=labels, fovs_per_well=3) == ("A01", 0)
    assert position_to_well_fov(2, well_labels=labels, fovs_per_well=3) == ("A01", 2)
    assert position_to_well_fov(3, well_labels=labels, fovs_per_well=3) == ("A02", 0)
    assert position_to_well_fov(5, well_labels=labels, fovs_per_well=3) == ("A02", 2)


def test_position_split_out_of_range_rejected():
    with pytest.raises(ValueError):
        position_to_well_fov(6, well_labels=["A01", "A02"], fovs_per_well=3)
    with pytest.raises(ValueError):
        position_to_well_fov(0, well_labels=["A01"], fovs_per_well=0)
