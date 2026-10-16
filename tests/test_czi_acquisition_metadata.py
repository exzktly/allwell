"""Acquisition metadata survives header-only CZI reads in explicit units.

Scaling uses ZEISS libCZI's metre convention; native stage tags use OME
Bio-Formats' micrometre convention. Synthetic ZISRAW fixtures establish the
parser contract, not which tags a particular ZEN version actually populates.
"""

import json
import subprocess
import sys

import numpy as np
import pytest

from pipeline.czi_read import AcquisitionClock, CziReader, CziReadError, read_czi_layout
from tests._czi_fixture import Tile, write_czi


def _scaling(x, y):
    return ("<ImageDocument><Metadata><Scaling><Items>"
            f'<Distance Id="X"><Value>{x}</Value>'
            "<DefaultUnitFormat>mm</DefaultUnitFormat></Distance>"
            f'<Distance Id="Y"><Value>{y}</Value>'
            "<DefaultUnitFormat>nm</DefaultUnitFormat></Distance>"
            "</Items></Scaling></Metadata></ImageDocument>")


def test_scaling_values_are_metres_regardless_of_display_units(tmp_path):
    path = write_czi(tmp_path / "scale.czi", [Tile(np.zeros((3, 4), "uint16"))],
                     xml=_scaling("6.5e-7", "8e-7"))
    layout = read_czi_layout(path)
    assert layout.pixel_size_um == pytest.approx({"x": 0.65, "y": 0.8}, rel=1e-12)
    layout.pixel_size_um["x"] = 99
    assert read_czi_layout(path).pixel_size_um == pytest.approx({"x": 0.65, "y": 0.8}, rel=1e-12)


@pytest.mark.parametrize("bad", ["", "nan", "inf", "-1", "0", "1e308"])
def test_unreadable_scale_axis_is_not_filled_from_the_other(tmp_path, bad):
    path = write_czi(tmp_path / "bad.czi", [Tile(np.zeros((3, 4), "uint16"))],
                     xml=_scaling("6.5e-7", bad))
    assert read_czi_layout(path).pixel_size_um == {"x": 0.65, "y": None}


def test_conflicting_scale_declarations_are_not_first_wins(tmp_path):
    xml = _scaling("6.5e-7", "8e-7").replace(
        "</Items>", '<Distance Id="X"><Value>2e-6</Value></Distance></Items>')
    path = write_czi(tmp_path / "conflict.czi", [Tile(np.zeros((3, 4), "uint16"))],
                     xml=xml)
    layout = read_czi_layout(path)
    assert layout.pixel_size_um["x"] is None
    assert layout.pixel_size_um["y"] == pytest.approx(0.8, rel=1e-12)


def test_exact_plane_metadata_reads_no_pixel_bytes_or_pixel_modules(tmp_path):
    xml = ("<METADATA><Tags><StageXPosition>-1200.5</StageXPosition>"
           "<StageYPosition>0</StageYPosition>"
           "<AcquisitionTime>2024-06-12T14:02:03+02:00</AcquisitionTime>"
           "</Tags></METADATA>")
    path = write_czi(tmp_path / "tile.czi", [
        Tile(np.zeros((3, 4), "uint16"), x=-32, y=120, metadata_xml=xml),
        Tile(np.ones((3, 4), "uint16"), c=1, x=400, y=-8),
    ])
    # Fresh interpreter exercises the real entry point and fails if either
    # metadata access or construction starts importing/decompressing pixels.
    script = '''
import json, sys
from dataclasses import asdict
import pipeline.czi_read as czi_read
from pipeline.czi_read import CziReader
with CziReader(sys.argv[1]) as reader:
    def pixels_forbidden(*args, **kwargs):
        raise AssertionError("metadata requested pixel bytes")
    reader._read_block_data = pixels_forbidden
    parses = []
    real_parse = czi_read.ET.fromstring
    def counted_parse(xml):
        parses.append(xml)
        return real_parse(xml)
    czi_read.ET.fromstring = counted_parse
    first = reader.read_tile_metadata(0, 0, 0, 0, 0)
    second = reader.read_tile_metadata(0, 0, 0, 1, 0)
    assert len(parses) == 2, 'each subblock document must be parsed only once'
assert 'numpy' not in sys.modules
assert 'tifffile' not in sys.modules
assert 'PySide6' not in sys.modules
print(json.dumps([asdict(first), asdict(second)]))
'''
    result = subprocess.run([sys.executable, "-c", script, str(path)],
                            check=True, capture_output=True, text=True)
    first, second = json.loads(result.stdout)
    assert first == {"mosaic_xy_px": [-32, 120],
                     "stage_xy_um": [-1200.5, 0.0],
                     "acquired_at": "2024-06-12T14:02:03+02:00", "tile_size_px": [4, 3]}
    assert second == {"mosaic_xy_px": [400, -8], "stage_xy_um": None,
                      "acquired_at": None, "tile_size_px": [4, 3]}


@pytest.mark.parametrize("tags", [
    "<StageXPosition>3</StageXPosition>",
    "<StageXPosition>nan</StageXPosition><StageYPosition>0</StageYPosition>",
    '<StageXPosition Unit="mm">3</StageXPosition><StageYPosition>0</StageYPosition>',
    "<StageXPosition>3</StageXPosition><StageXPosition>4</StageXPosition>"
    "<StageYPosition>0</StageYPosition>",
])
def test_incomplete_or_ambiguous_stage_positions_are_null(tmp_path, tags):
    path = write_czi(tmp_path / "stage.czi", [Tile(
        np.zeros((3, 4), "uint16"), metadata_xml=f"<METADATA><Tags>{tags}</Tags></METADATA>")])
    with CziReader(path) as reader:
        assert reader.read_tile_metadata(0, 0, 0, 0, 0).stage_xy_um is None


def test_missing_plane_and_closed_reader_do_not_invent_metadata(tmp_path):
    path = write_czi(tmp_path / "one.czi", [Tile(np.zeros((3, 4), "uint16"))])
    reader = CziReader(path)
    with pytest.raises(CziReadError, match="not open"):
        reader.read_tile_metadata(0, 0, 0, 0, 0)
    with reader:
        with pytest.raises(CziReadError, match="holds no image"):
            reader.read_tile_metadata(0, 0, 5, 0, 0)


def test_unzoned_tile_instant_stays_unzoned(tmp_path):
    path = write_czi(tmp_path / "local-clock.czi", [Tile(
        np.zeros((3, 4), "uint16"), acquired="2024-06-12T14:02:03.123456")])
    with CziReader(path) as reader:
        assert reader.read_tile_metadata(0, 0, 0, 0, 0).acquired_at == "2024-06-12T14:02:03.123456"


def test_acquisition_start_is_earliest_across_tiles_and_channel_z_planes(tmp_path):
    path = write_czi(tmp_path / "clocks.czi", [
        Tile(np.zeros((3, 4), "uint16"), acquired="2024-06-12T12:00:00+02:00"),
        Tile(np.zeros((3, 4), "uint16"), c=1, z=1, acquired="2024-06-12T09:30:00Z"),
        Tile(np.zeros((3, 4), "uint16"), tile=1, acquired="2024-06-12T09:20:00Z"),
        Tile(np.zeros((3, 4), "uint16"), tile=2),
    ])
    clock = AcquisitionClock()
    with CziReader(path) as reader:
        for tile in (0, 1, 2):
            clock.add_tile(item.acquired_at for item in reader.position_metadata(0, tile, 0))
    assert clock.summary() == {"started_at": "2024-06-12T09:20:00+00:00",
                               "start_reason": None, "tiles_without_time": 1}


def test_missing_times_are_counted_per_tile_and_unzoned_starts_stay_unzoned():
    clock = AcquisitionClock()
    clock.add_tile([None, None, "invalid"])
    clock.add_tile(["2024-06-12T11:00:00", None])
    clock.add_tile(["2024-06-12T10:00:00"])
    assert clock.summary() == {"started_at": "2024-06-12T10:00:00",
                               "start_reason": None, "tiles_without_time": 1}


def test_mixed_clocks_are_refused_even_when_they_arrive_in_different_wells():
    first, second = AcquisitionClock(), AcquisitionClock()
    first.add_tile(["2024-06-12T11:00:00Z"])
    second.add_tile(["2024-06-12T10:00:00"])
    second.add_tile([None, None])
    first.merge(second)
    assert first.summary() == {
        "started_at": None, "start_reason": "Tile acquisition times mix zoned and unzoned clocks",
        "tiles_without_time": 1}


def test_an_acquisition_with_no_readable_stamps_has_a_reason():
    clock = AcquisitionClock()
    clock.add_tile([])
    assert clock.summary() == {"started_at": None,
                               "start_reason": "No tiles carry a readable acquisition time",
                               "tiles_without_time": 1}
