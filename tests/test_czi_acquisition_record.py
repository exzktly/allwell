"""Real conversion publication carries one clock and exact archive coverage."""

import os

import numpy as np
import pytest

from pipeline.czi_explode import CziExplodeCancelled, explode_czi
from tests._czi_fixture import Tile, plate_metadata_xml, write_czi
from util.acquisition_record import (
    AcquisitionRecordError, publication_marker, read_record, read_record_file,
    validate_against_archives, write_record_file, new_record,
)


def _source(tmp_path, *, split=False, scales=("6.5e-7", "6.5e-7")):
    folder = tmp_path / "source"
    folder.mkdir()
    files = []
    groups = [[0], [1]] if split else [[0, 1]]
    for group in groups:
        tiles = []
        wells = {}
        for index in group:
            scene = 0 if split else index
            wells[scene] = ("A01", "B02")[index]
            for field in (0, 1):
                for t in (0, 1):
                    hour = 10 + 4 * t - index
                    stamp = f"2024-06-12T{hour:02d}:00:00Z"
                    if field == 1 and index == 0 and t == 0:
                        stamp = None
                    tiles.append(Tile(np.full((3, 4), index + field, "uint16"),
                                      scene=scene, tile=field, t=t,
                                      x=field * 3 - 20, y=index * 100,
                                      acquired=stamp))
        xml = plate_metadata_xml(scene_wells=wells, channels=["phase"])
        scale = scales[group[0]]
        if scale is not None:
            xml = xml.replace("</Metadata>", '<Scaling><Items><Distance Id="X">'
                              f'<Value>{scale}</Value></Distance><Distance Id="Y">'
                              f'<Value>{scale}</Value></Distance></Items></Scaling></Metadata>')
        files.append(write_czi(folder / f"part-{group[0]}.czi", tiles, xml=xml))
    return folder if split else files[0]


def test_single_file_conversion_writes_exact_tiles_and_one_shared_time(tmp_path):
    source = _source(tmp_path)
    dest = tmp_path / "dataset"
    explode_czi(source, dest / "in", workers=2)
    record = read_record(dest)
    assert validate_against_archives(dest, record).ok
    assert len(record["tiles"]) == 8
    assert record["timepoints"]["0h"]["started_at"] == "2024-06-12T09:00:00+00:00"
    assert record["timepoints"]["0h"]["tiles_without_time"] == 1
    assert record["timepoints"]["4h"]["started_at"] == "2024-06-12T13:00:00+00:00"
    assert record["timepoints"]["4h"]["tiles_without_time"] == 0
    assert record["timepoints"]["0h"]["tile_size_px"] == {"x": 4, "y": 3}
    assert record["timepoints"]["0h"]["pixel_size_um"] == {"x": .65, "y": .65}
    assert record["tiles"][0]["mosaic_xy_px"] == [-20, 0]
    assert all("acquired_at" not in tile and "started_at" not in tile for tile in record["tiles"])
    assert not publication_marker(dest / "in" / "acquisition.json").exists()


@pytest.mark.parametrize("split", [False, True])
def test_subset_keeps_whole_acquisition_clock_but_only_published_tiles(tmp_path, split):
    source = _source(tmp_path, split=split)
    directory = tmp_path / "arbitrary-output-folder"
    explode_czi(source, directory, well_subset=("A01",))
    record = read_record_file(directory / "acquisition.json")
    assert {tile["well"] for tile in record["tiles"]} == {"A01"}
    assert record["timepoints"]["0h"]["started_at"] == "2024-06-12T09:00:00+00:00"
    assert not (directory / "in").exists()
    if split:
        assert set(record.get("clock_source_file_stats", {})) == {"part-0.czi", "part-1.czi"}


def test_cancelled_conversion_records_only_archives_that_were_published(tmp_path):
    source = _source(tmp_path)
    dest = tmp_path / "dataset"
    with pytest.raises(CziExplodeCancelled):
        explode_czi(source, dest / "in", workers=1,
                    should_abort=lambda: (dest / "in" / "A01.zip").exists())
    record = read_record(dest)
    assert {tile["well"] for tile in record["tiles"]} == {"A01"}
    assert validate_against_archives(dest, record).ok
    assert record["timepoints"]["0h"]["started_at"] == "2024-06-12T09:00:00+00:00"


def test_failed_metadata_publish_blocks_reads_and_resume_rebuilds_without_pixels(
        tmp_path, monkeypatch):
    import pipeline.czi_acquisition as acquisition

    source = _source(tmp_path)
    dest = tmp_path / "dataset"
    explode_czi(source, dest / "in", well_subset=("A01",))
    path = dest / "in" / "acquisition.json"
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        def failure(*args):
            raise OSError("metadata publication failed")
        patch.setattr(acquisition, "write_record_file", failure)
        with pytest.raises(OSError, match="metadata publication failed"):
            explode_czi(source, dest / "in", well_subset=("B02",))
    assert path.read_bytes() == before
    with pytest.raises(AcquisitionRecordError, match="publication is incomplete"):
        read_record(dest)
    kept = {p.name: p.stat().st_mtime_ns for p in (dest / "in").glob("*.zip")}
    result = explode_czi(source, dest / "in", resume=True)
    assert result.images_written == 0 and result.wells_resumed == ["A01", "B02"]
    assert {p.name: p.stat().st_mtime_ns for p in (dest / "in").glob("*.zip")} == kept
    assert validate_against_archives(dest, read_record(dest)).ok
    assert read_record(dest)["timepoints"]["0h"]["started_at"] == "2024-06-12T09:00:00+00:00"


def test_source_change_during_conversion_never_publishes_new_provenance(tmp_path):
    source = _source(tmp_path)
    dest = tmp_path / "dataset"
    changed = False
    def abort():
        nonlocal changed
        if (dest / "in" / "A01.zip").exists() and not changed:
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            changed = True
        return False
    error = None
    try:
        explode_czi(source, dest / "in", workers=1, should_abort=abort)
    except AcquisitionRecordError as caught:
        error = caught
    assert error is not None and "Source changed" in str(error)
    assert (dest / "in" / "A01.zip").exists()
    with pytest.raises(AcquisitionRecordError, match="publication is incomplete"):
        read_record(dest)


def test_split_scenes_record_uses_every_written_source_and_its_native_positions(tmp_path):
    source = _source(tmp_path, split=True)
    dest = tmp_path / "dataset"
    explode_czi(source, dest / "in", workers=2)
    record = read_record(dest)
    assert validate_against_archives(dest, record).ok
    assert record["source_files"] == ["part-0.czi", "part-1.czi"]
    assert {tile["source_file"] for tile in record["tiles"] if tile["well"] == "B02"} == {"part-1.czi"}
    assert {tuple(tile["mosaic_xy_px"]) for tile in record["tiles"] if tile["well"] == "B02"} == {(-20, 100), (-17, 100)}
    assert record["timepoints"]["0h"]["started_at"] == "2024-06-12T09:00:00+00:00"


def test_conflicting_known_scales_are_refused_before_any_archive_is_written(tmp_path):
    source = _source(tmp_path, split=True, scales=("6.5e-7", "3.2e-7"))
    directory = tmp_path / "output"
    with pytest.raises(AcquisitionRecordError, match="disagree about pixel_size_um"):
        explode_czi(source, directory)
    assert not list(directory.glob("*.zip"))


def test_unknown_scale_in_one_split_file_is_not_borrowed_from_another(tmp_path):
    source = _source(tmp_path, split=True, scales=("6.5e-7", None))
    directory = tmp_path / "output"
    explode_czi(source, directory)
    assert read_record_file(directory / "acquisition.json")["timepoints"]["0h"]["pixel_size_um"] == {"x": None, "y": None}


def test_record_guard_refuses_even_an_absent_record_in_an_arbitrary_folder(tmp_path):
    path = tmp_path / "inputs" / "acquisition.json"
    write_record_file(path, new_record())
    publication_marker(path).write_text("{}")
    path.unlink()
    error = None
    try:
        read_record_file(path)
    except AcquisitionRecordError as caught:
        error = caught
    assert error is not None and "publication is incomplete" in str(error)


def test_split_files_naming_one_well_keep_their_distinct_field_provenance(tmp_path):
    source = _source(tmp_path, split=True)
    part = source / "part-1.czi"
    part.write_bytes(part.read_bytes().replace(b'Name="B02"', b'Name="A01"'))
    dest = tmp_path / "dataset"
    explode_czi(source, dest / "in")
    record = read_record(dest)
    assert validate_against_archives(dest, record).ok
    assert {tile["well"] for tile in record["tiles"]} == {"A01"}
    assert {tile["field"] for tile in record["tiles"]} == {"f0", "f1", "f2", "f3"}
    assert {tile["source_file"] for tile in record["tiles"] if tile["field"] == "f3"} == {"part-1.czi"}
    assert record["timepoints"]["0h"]["started_at"] == "2024-06-12T09:00:00+00:00"


def test_timepoint_dimensions_do_not_inherit_another_acquisitions_maximum(tmp_path):
    path = write_czi(tmp_path / "different-plans.czi", [
        Tile(np.zeros((3, 4), "uint16"), t=0, acquired="2024-06-12T10:00:00Z"),
        Tile(np.zeros((5, 6), "uint16"), t=1, acquired="2024-06-12T14:00:00Z"),
    ], xml=plate_metadata_xml(scene_wells={0: "A01"}, channels=["phase"]))
    directory = tmp_path / "output"
    explode_czi(path, directory)
    record = read_record_file(directory / "acquisition.json")
    assert record["timepoints"]["0h"]["tile_size_px"] == {"x": 4, "y": 3}
    assert record["timepoints"]["4h"]["tile_size_px"] == {"x": 6, "y": 5}


def test_whole_acquisition_clock_names_partial_and_resumed_conversions_identically(tmp_path):
    import zipfile
    source = _source(tmp_path)
    source.write_bytes(source.read_bytes().replace(b'T13:00:00Z', b'T15:00:00Z'))
    dest = tmp_path / 'dataset'
    first = explode_czi(source, dest / 'in', well_subset=('A01',))
    assert first.timepoints == ['0h', '5h']
    with zipfile.ZipFile(dest / 'in' / 'A01.zip') as archive:
        before = [archive.read(info) for info in archive.infolist()]
    resumed = explode_czi(source, dest / 'in', resume=True)
    assert resumed.timepoints == ['0h', '5h']
    assert resumed.wells_resumed == ['A01']
    assert resumed.images_written == 4
    record = read_record(dest)
    assert record['timepoints']['5h']['started_at'] == '2024-06-12T14:00:00+00:00'
    assert record['timepoints']['5h']['source_timepoint_index'] == 1
    assert validate_against_archives(dest, record).ok
    with zipfile.ZipFile(dest / 'in' / 'A01.zip') as archive:
        assert [archive.read(info) for info in archive.infolist()] == before
        assert any(info.filename.endswith('_5h.tif') for info in archive.infolist())
        assert {info.comment for info in archive.infolist()} == {
            b'allwell:czi-source-timepoint=0', b'allwell:czi-source-timepoint=1'}
    mtimes = {p.name: p.stat().st_mtime_ns for p in (dest / 'in').glob('*.zip')}
    assert explode_czi(source, dest / 'in', resume=True).images_written == 0
    assert {p.name: p.stat().st_mtime_ns for p in (dest / 'in').glob('*.zip')} == mtimes


def test_shared_hour_tokens_use_existing_rounding_and_unknown_clock_reason():
    from pipeline.czi_acquisition import shared_timepoint_tokens
    tokens, reason = shared_timepoint_tokens({
        0: {'started_at': '2024-06-12T09:00:00'},
        1: {'started_at': '2024-06-12T13:00:01.000001'},
    })
    assert reason is None
    assert tokens == {0: '0h', 1: '4h'}
    tokens, reason = shared_timepoint_tokens({0: {'started_at': None}})
    assert tokens == {0: '0h'} and reason == 'Some shared acquisition starts are unknown'


def test_relabel_closes_old_archive_before_atomic_replace(tmp_path, monkeypatch):
    import zipfile
    from util import zip_write
    source = _source(tmp_path)
    target = tmp_path / "dataset" / "in"
    handles = []
    original_zip = zipfile.ZipFile
    original_replace = zip_write.os.replace

    class TrackedZip(original_zip):
        def __init__(self, file, mode="r", *args, **kwargs):
            super().__init__(file, mode, *args, **kwargs)
            if mode == "r" and str(file).endswith("A01.zip"):
                handles.append(self)

    def replace(old, new):
        if str(new).endswith("A01.zip"):
            assert all(handle.fp is None for handle in handles)
        return original_replace(old, new)

    monkeypatch.setattr(zipfile, "ZipFile", TrackedZip)
    monkeypatch.setattr(zip_write.os, "replace", replace)
    explode_czi(source, target, well_subset=("A01",))
    assert handles


def test_resume_reconverts_archives_named_under_sampled_clock_rule(tmp_path):
    import zipfile
    from util.zip_write import atomic_zip
    source = _source(tmp_path)
    source.write_bytes(source.read_bytes().replace(b'T13:00:00Z', b'T15:00:00Z'))
    target = tmp_path / 'dataset' / 'in'
    explode_czi(source, target, well_subset=('A01',))
    path = target / 'A01.zip'
    with zipfile.ZipFile(path) as archive:
        members = [(info.filename.replace('_5h.tif', '_4h.tif'), archive.read(info))
                   for info in archive.infolist()]
    with atomic_zip(path) as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    result = explode_czi(source, target, well_subset=('A01',), resume=True)
    assert result.images_written == 4
    assert result.wells_resumed == []
    assert result.timepoints == ['0h', '5h']
    with zipfile.ZipFile(path) as archive:
        assert all('_4h.tif' not in name for name in archive.namelist())


def test_conversion_without_comparable_starts_keeps_positional_names(tmp_path):
    source = write_czi(tmp_path / 'unknown.czi', [
        Tile(np.ones((3, 4), 'uint16'), t=t, acquired=None) for t in (0, 1)
    ], xml=plate_metadata_xml(scene_wells={0: 'A01'}, channels=['phase']))
    target = tmp_path / 'dataset'
    result = explode_czi(source, target / 'in')
    assert result.timepoints == ['t0', 't1']
    record = read_record(target)
    assert all(data['started_at'] is None for data in record['timepoints'].values())
    assert record['timepoint_naming']['reason'] == 'Some shared acquisition starts are unknown'
    assert validate_against_archives(target, record).ok


def test_unselected_old_rule_archive_is_refused_instead_of_silently_relabelled(tmp_path):
    import zipfile
    from util.zip_write import atomic_zip
    source = _source(tmp_path)
    source.write_bytes(source.read_bytes().replace(b'T13:00:00Z', b'T15:00:00Z'))
    target = tmp_path / 'dataset' / 'in'
    explode_czi(source, target, well_subset=('A01',))
    path = target / 'A01.zip'
    with zipfile.ZipFile(path) as archive:
        members = [(info.filename.replace('_5h.tif', '_4h.tif'), archive.read(info))
                   for info in archive.infolist()]
    with atomic_zip(path) as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    before = path.read_bytes()
    error = None
    try:
        explode_czi(source, target, well_subset=('B02',), resume=True)
    except AcquisitionRecordError as caught:
        error = caught
    assert error is not None and 'A01' in str(error) and 're-converted' in str(error)
    assert path.read_bytes() == before
    assert not (target / 'B02.zip').exists()


def test_mixed_source_tile_clocks_leave_shared_start_unknown(tmp_path):
    source = _source(tmp_path)
    source.write_bytes(source.read_bytes().replace(b'T09:00:00Z', b'T09:00:00 '))
    target = tmp_path / 'dataset'
    result = explode_czi(source, target / 'in', well_subset=('A01',))
    assert result.timepoints == ['t0', 't1']
    record = read_record(target)
    assert record['timepoints']['t0']['started_at'] is None
    assert record['timepoints']['t0']['start_reason'] == 'Tile acquisition times mix zoned and unzoned clocks'
    assert validate_against_archives(target, record).ok


def test_single_frame_without_stamp_is_zero_hours_but_start_is_unknown(tmp_path):
    source = write_czi(tmp_path / 'single.czi', [Tile(np.ones((3, 4), 'uint16'), acquired=None)],
                       xml=plate_metadata_xml(scene_wells={0: 'A01'}, channels=['phase']))
    target = tmp_path / 'dataset'
    result = explode_czi(source, target / 'in')
    assert result.timepoints == ['0h']
    record = read_record(target)
    assert record['timepoints']['0h']['started_at'] is None
    assert record['timepoints']['0h']['tiles_without_time'] == 1
    assert record['timepoints']['0h']['start_reason'] == 'No tiles carry a readable acquisition time'
