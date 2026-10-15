"""The record built from a described grid for a dataset without one (tracker P15)."""

from __future__ import annotations

import pytest

from pipeline.nanowell_pattern_record import parse_grid, parse_scan, record_from_pattern
from pipeline.nanowell_run import NanowellSettings, CUSTOM_FORMAT
from pipeline.tile_grid import ScanPattern, place_from_record, tiles_from_record


def _positions(record, tp="0h"):
    return {t["field"]: tuple(t["mosaic_xy_px"]) for t in record["tiles"] if t["timepoint"] == tp}


def test_fields_are_placed_in_the_order_of_their_names_numerically_along_the_scan():
    """F2 before F10, and a serpentine's second row runs right to left."""
    fields = [f"F{k}" for k in (10, 1, 2, 3, 4, 5)]
    record = record_from_pattern("A01", {"0h": fields}, {"0h": (100, 50)},
                                 {"grid": [2, 3], "scan": "top-left-row-serpentine", "overlap_percent": 10.0,
                                  "pixel_size_um": 2.0})
    step_x, step_y = 90.0, 45.0
    assert _positions(record) == {"F1": (0.0, 0.0), "F2": (step_x, 0.0), "F3": (2 * step_x, 0.0),
                                  "F4": (2 * step_x, step_y), "F5": (step_x, step_y), "F10": (0.0, step_y)}
    meta = record["timepoints"]["0h"]
    assert meta["tile_size_px"] == {"x": 100, "y": 50} and meta["pixel_size_um"] == {"x": 2.0, "y": 2.0}
    tiles, size, pixel, overlap = tiles_from_record(record, "A01", "0h")
    plan = place_from_record(tiles, tile_size_px=size, pixel_size_um=pixel, declared_overlap_percent=overlap)
    assert plan.status == "ok" and (plan.rows, plan.cols) == (2, 3)


def test_more_fields_than_the_grid_holds_is_refused_by_placement_not_dropped():
    record = record_from_pattern("A01", {"0h": ["F1", "F2", "F3"]}, {"0h": (100, 100)},
                                 {"grid": [1, 2], "scan": "top-left-row", "overlap_percent": 0.0,
                                  "pixel_size_um": 1.0})
    tiles, size, pixel, overlap = tiles_from_record(record, "A01", "0h")
    assert len(tiles) == 3
    assert place_from_record(tiles, tile_size_px=size, pixel_size_um=pixel,
                             declared_overlap_percent=overlap).status != "ok"


def test_the_spellings_parse_and_a_wrong_one_is_named():
    assert parse_grid("5x5") == (5, 5) and parse_grid(" 4 X 6 ") == (4, 6)
    assert parse_scan("bottom-right-column-serpentine") == ScanPattern("bottom-right", "column", True)
    with pytest.raises(ValueError, match="is not a grid"):
        parse_grid("0x5")
    with pytest.raises(ValueError, match="is not a scan pattern"):
        parse_scan("left-row")


def test_a_custom_format_takes_its_geometry_and_keys_it_and_a_known_format_s_key_is_unchanged():
    custom = NanowellSettings(CUSTOM_FORMAT, custom=(120.0, 60.0))
    assert (custom.pitch_um, custom.diameter_um, custom.margin) == (120.0, 60.0, 6.0)
    assert custom.key()["pitch_um"] == 120.0 and custom.key()["diameter_um"] == 60.0
    assert set(NanowellSettings("100um").key()) == {"plate_format", "margin_um", "cell_diameter_um",
                                                    "square_edge_deg", "occluded_below", "clipped_below"}
    with pytest.raises(ValueError):
        NanowellSettings(CUSTOM_FORMAT)
    with pytest.raises(ValueError):
        NanowellSettings("100um", custom=(120.0, 60.0))
