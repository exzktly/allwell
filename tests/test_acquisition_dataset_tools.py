"""Acquisition metadata follows actual Datasets-tool output identities."""

from datasets.subset.apply import execute_plan
from datasets.subset.subset_compute import SubsetFilter, build_subset_plan
from datasets.validate.validate_compute import build_report
from tests.fixtures.dataset_builder import build_dataset
from util.acquisition_record import new_record, new_timepoint, read_record, write_record


def _dataset(root):
    wells, tokens = ['A01', 'B02'], ['0h', '24h']
    build_dataset(root, wells=wells, timepoints=tokens)
    record = new_record()
    record['source_files'] = ['source.czi']
    record['timepoints'] = {token: new_timepoint() for token in tokens}
    record['tiles'] = [dict(well=well, field='F001', timepoint=token,
                            mosaic_xy_px=[2, 3], stage_xy_um=None, source_file='source.czi')
                       for well in wells for token in tokens]
    write_record(root, record)
    return record


def test_subset_keeps_only_published_wells_fields_and_timepoints(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'target'
    original = _dataset(source)
    plan = build_subset_plan(source, target, SubsetFilter(
        wells=frozenset({'B02'}), timepoint_labels=frozenset({'24h'})))
    execute_plan(plan)
    record = read_record(target)
    assert record is not None
    assert list(record['timepoints']) == ['24h']
    assert record['tiles'] == [original['tiles'][-1]]
    assert read_record(source) == original


def test_validate_reports_record_coverage_and_corrupt_metadata(tmp_path):
    source = tmp_path / 'source'
    record = _dataset(source)
    assert any(f.code == 'acquisition_present' for f in build_report(source).findings)
    record['tiles'].pop()
    write_record(source, record)
    report = build_report(source)
    assert any(f.code == 'acquisition_mismatch' and 'missing tile' in f.message
               for f in report.findings)
    (source / 'in' / 'acquisition.json').write_text('{invalid')
    assert any(f.code == 'acquisition_invalid' for f in build_report(source).findings)


def test_merge_plates_remaps_record_wells_and_refuses_conflicting_starts(tmp_path):
    from datasets.merge_plates.apply import execute_plan as merge
    from datasets.merge_plates.merge_plates_compute import build_plate_merge_plan, MergePlatesError
    first, second, target = tmp_path / 'first', tmp_path / 'second', tmp_path / 'target'
    _dataset(first)
    record = _dataset(second)
    plan = build_plate_merge_plan(first, second, target, well_map={'A01': 'C01', 'B02': 'D02'})
    merge(plan)
    assert {tile['well'] for tile in read_record(target)['tiles']} == {'A01', 'B02', 'C01', 'D02'}
    assert read_record(target)['source_files'] == ['dataset1/source.czi', 'dataset2/source.czi']
    record['timepoints']['0h']['started_at'] = '2024-06-12T09:00:00'
    write_record(second, record)
    refused = tmp_path / 'refused'
    plan = build_plate_merge_plan(first, second, refused, well_map={'A01': 'C01', 'B02': 'D02'})
    error = None
    try:
        merge(plan)
    except MergePlatesError as caught:
        error = caught
    assert error is not None and '0h' in str(error) and 'started_at' in str(error)
    assert not refused.exists()
