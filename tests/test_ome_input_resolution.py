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


def test_explode_if_present_honours_explicit_fovs_per_well(tmp_path):
    # 6 positions over a 1×3 grid with an explicit 2 FOVs/well.
    p = _write_ome(tmp_path / "plate.ome.tif", positions=6)
    opts = {
        "ome_input": True,
        "ome_grid_rows": 1,
        "ome_grid_cols": 3,
        "ome_fovs_per_well": 2,
        "ome_origin_well": "A01",
        "ome_direction": "row",
    }
    explode_ome_if_present(p, opts)
    tifs = sorted(x.name for x in (tmp_path / "in").glob("*.tif"))
    assert len(tifs) == 6
    # Well-major: position 3 is well A02 (3 // 2), FOV 1.
    assert (tmp_path / "in" / "plate_GFP_A02_f1_t0.tif").exists()


def test_explode_if_present_rejects_fovs_mismatch(tmp_path):
    p = _write_ome(tmp_path / "plate.ome.tif", positions=6)
    opts = {
        "ome_input": True,
        "ome_grid_rows": 2,
        "ome_grid_cols": 2,
        "ome_fovs_per_well": 2,  # 4 wells × 2 = 8 ≠ 6
    }
    with pytest.raises(ValueError, match="stage position"):
        explode_ome_if_present(p, opts)


def test_explode_if_present_zero_fovs_means_auto(tmp_path):
    # ome_fovs_per_well=0 is the "auto" sentinel — derive from the position count.
    p = _write_ome(tmp_path / "plate.ome.tif", positions=4)
    opts = {
        "ome_input": True,
        "ome_grid_rows": 2,
        "ome_grid_cols": 2,
        "ome_fovs_per_well": 0,
    }
    explode_ome_if_present(p, opts)
    assert len(list((tmp_path / "in").glob("*.tif"))) == 4


def test_explode_if_present_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="no single"):
        explode_ome_if_present(tmp_path, {"ome_input": True, "ome_grid_rows": 1,
                                          "ome_grid_cols": 1})
