"""Packed-acquisition-file detection in ``services.input_resolution_service``.

Analyze processes folders. ``detect_packed_input`` is how it spots a file that
has to be converted first — a packed OME-TIFF or a Zeiss CZI — and it returns
the message naming *which* Datasets page converts it, because sending the user
to the wrong converter sends them to one that will refuse their file. The
conversions themselves live in ``datasets.ome_import`` and
``datasets.czi_import``.
"""

from __future__ import annotations

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from services.input_resolution_service import (  # noqa: E402
    PACKED_CZI,
    PACKED_CZI_MESSAGE,
    PACKED_OME,
    PACKED_OME_MESSAGE,
    detect_packed_input,
)
from tests._czi_fixture import plate_tiles, write_czi  # noqa: E402


def _write_ome(path, *, positions=4):
    with tifffile.TiffWriter(str(path), ome=True) as tw:
        for pos in range(positions):
            arr = np.full((1, 1, 1, 5, 5), pos, np.uint16)
            tw.write(arr, metadata={"axes": "TCZYX", "Channel": {"Name": ["GFP"]}})
    return path


def _write_czi(path):
    return write_czi(path, plate_tiles(wells=1, fovs=1, timepoints=1,
                                       channels=1))


def test_detect_file_selected_directly(tmp_path):
    p = _write_ome(tmp_path / "plate.ome.tif")
    found = detect_packed_input(p)
    assert found is not None
    assert (found.path, found.action) == (p, PACKED_OME)


def test_detect_lone_file_in_directory(tmp_path):
    p = _write_ome(tmp_path / "plate.ome.tif")
    found = detect_packed_input(tmp_path)
    assert found is not None and found.path == p


def test_detect_ambiguous_directory_returns_none(tmp_path):
    _write_ome(tmp_path / "a.ome.tif")
    _write_ome(tmp_path / "b.ome.tif")
    assert detect_packed_input(tmp_path) is None


def test_detect_plain_directory_returns_none(tmp_path):
    (tmp_path / "A01.tif").write_bytes(b"not-ome")
    assert detect_packed_input(tmp_path) is None


def test_a_czi_selected_directly_is_detected(tmp_path):
    p = _write_czi(tmp_path / "plate.czi")
    found = detect_packed_input(p)
    assert found is not None
    assert (found.path, found.action) == (p, PACKED_CZI)


def test_a_lone_czi_in_a_directory_is_detected(tmp_path):
    p = _write_czi(tmp_path / "plate.czi")
    found = detect_packed_input(tmp_path)
    assert found is not None and found.path == p


def test_several_czis_in_one_directory_are_one_split_plate(tmp_path):
    """The one place the two formats are treated differently, and the reason
    the kinds table carries a column for it.

    Two OME-TIFFs in a folder are two plates and picking either would convert
    one the user did not name. Several ``.czi`` files are ZEN's *Split Scenes*
    export — one file per scene, one plate — which the CZI converter reads as a
    set, so the folder is a recognised input. The *folder* comes back rather
    than a member, because several of these are the plate and naming one of
    them would describe a scene.
    """
    _write_czi(tmp_path / "a.czi")
    _write_czi(tmp_path / "b.czi")
    found = detect_packed_input(tmp_path)

    assert found is not None
    assert (found.path, found.action) == (tmp_path, PACKED_CZI)


def test_two_ome_tiffs_in_one_directory_are_still_ambiguous(tmp_path):
    """The contrast that makes the column above mean something: nothing has
    changed for OME-TIFF, where two files really are two plates."""
    _write_ome(tmp_path / "a.ome.tif")
    _write_ome(tmp_path / "b.ome.tif")
    assert detect_packed_input(tmp_path) is None


def test_each_kind_names_its_own_converter(tmp_path):
    """The whole reason the message travels with the detection: a CZI sent to
    the OME-TIFF page is sent to a converter that will refuse it."""
    (tmp_path / "other").mkdir()
    ome = detect_packed_input(_write_ome(tmp_path / "plate.ome.tif"))
    czi = detect_packed_input(_write_czi(tmp_path / "other" / "plate.czi"))
    assert ome is not None and czi is not None
    assert ome.message == PACKED_OME_MESSAGE
    assert czi.message == PACKED_CZI_MESSAGE
    assert "Convert OME-TIFF" in ome.message
    assert "Convert CZI" in czi.message
    assert "Convert CZI" not in ome.message
    assert "Convert OME-TIFF" not in czi.message


def test_an_ome_tiff_is_not_read_as_a_plain_tif(tmp_path):
    """``.ome.tif`` also ends in ``.tif``; the table is ordered so the more
    specific suffix is tested first and a plain ``.tif`` matches neither."""
    (tmp_path / "A01.tif").write_bytes(b"not-packed")
    assert detect_packed_input(tmp_path / "A01.tif") is None
