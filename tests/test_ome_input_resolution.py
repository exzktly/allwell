"""OME-TIFF detection in ``services.input_resolution_service``.

Analyze uses ``detect_ome_input`` to spot a raw OME-TIFF and redirect the user to
the Datasets converter; the de-interleave itself lives in ``datasets.ome_import``.
"""

from __future__ import annotations

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from services.input_resolution_service import detect_ome_input  # noqa: E402


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
