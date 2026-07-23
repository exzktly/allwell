"""The per-well rank palette must not repeat colours down a plate column.

Regression for the "well colours repeat down columns in sets of four" bug: the
palette used to hold exactly 48 entries and well colour is
``WELL_COLORS[well_rank(token) % len(WELL_COLORS)]``. Row-major rank steps by 12
(the column count) going *down* a column, so with ``len == 48 == 4 * 12`` the
colours wrapped every 4 rows — rows E–H mirrored A–D. A full 96-entry palette
(one colour per plate position) gives every well a distinct colour.

Pure-domain test: touches only ``plate_layout`` / ``selections_model`` (no Qt,
no numpy), so it runs in the headless test scaffold.
"""
from __future__ import annotations

from well_viewer.domain.plate_layout import PLATE_COLS, PLATE_ROWS, WELL_COLORS
from well_viewer.domain.selections_model import color_for_well


def test_palette_has_one_colour_per_plate_position():
    assert len(WELL_COLORS) == len(PLATE_ROWS) * len(PLATE_COLS) == 96


def test_palette_length_not_a_submultiple_of_column_count():
    # ``well_rank`` steps by len(PLATE_COLS) down a column; any palette length
    # that divides a within-column step (e.g. 48 = 4 * 12) repeats colours down
    # the column. Guard the structural property, not just the current length.
    n_cols = len(PLATE_COLS)
    for step in range(n_cols, len(PLATE_ROWS) * n_cols, n_cols):
        assert step % len(WELL_COLORS) != 0, (
            f"palette length {len(WELL_COLORS)} repeats after {step} ranks "
            "(a down-column step) — colours will wrap within a column"
        )


def test_no_colour_repeats_down_any_column():
    for col in PLATE_COLS:
        colours = [color_for_well(f"{row}{col}") for row in PLATE_ROWS]
        assert len(set(colours)) == len(colours), (
            f"column {col} has repeated well colours: {colours}"
        )


def test_every_well_on_the_plate_has_a_distinct_colour():
    wells = [f"{row}{col}" for row in PLATE_ROWS for col in PLATE_COLS]
    colours = [color_for_well(w) for w in wells]
    assert len(set(colours)) == len(wells) == 96
