"""OME-TIFF detection + explode wiring in ``services.input_resolution_service``."""

from __future__ import annotations

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from services.input_resolution_service import (  # noqa: E402
    detect_ome_input,
    explode_ome_if_present,
)


def _write_ome(path, *, positions=4):
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for pos in range(positions):
            arr = np.full((1, 1, 1, 5, 5), pos, np.uint16)
            tw.write(arr, metadata={"axes": "TCZYX", "Channel": {"Name": ["GFP"]}})
    return path


def test_detect_file_selected_directly(tmp_path):
    p = _write_ome(tmp_path / "plate.ome.tif")
    assert detect_ome_input(p) == p


def test_detect_lone_file_in_directory(tmp_path):
    p = _write_ome(tmp_path / "plate.ome.tif")
    assert detect_ome_input(tmp_path) == p


def test_detect_ambiguous_directory_returns_none(tmp_path):
    _write_ome(tmp_path / "a.ome.tif")
    _write_ome(tmp_path / "b.ome.tif")
    assert detect_ome_input(tmp_path) is None


def test_detect_plain_directory_returns_none(tmp_path):
    (tmp_path / "A01.tif").write_bytes(b"not-ome")
    assert detect_ome_input(tmp_path) is None


def test_explode_if_present_noop_without_flag(tmp_path):
    p = _write_ome(tmp_path / "plate.ome.tif")
    assert explode_ome_if_present(p, {"ome_input": False}) is None


def test_explode_if_present_deinterleaves_and_returns_base(tmp_path):
    p = _write_ome(tmp_path / "plate.ome.tif", positions=4)
    opts = {
        "ome_input": True,
        "ome_grid_rows": 2,
        "ome_grid_cols": 2,
        "ome_origin_well": "A01",
        "ome_direction": "row",
    }
    base = explode_ome_if_present(p, opts)
    assert base == tmp_path
    in_dir = tmp_path / "in"
    assert in_dir.is_dir()
    # 4 wells × 1 channel × 1 timepoint = 4 loose TIFs; experiment token = file stem.
    tifs = sorted(x.name for x in in_dir.glob("*.tif"))
    assert len(tifs) == 4
    assert all(name.startswith("plate_GFP_") for name in tifs)


def test_explode_if_present_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="no single"):
        explode_ome_if_present(tmp_path, {"ome_input": True, "ome_grid_rows": 1,
                                          "ome_grid_cols": 1})
