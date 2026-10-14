"""Metadata reaches scoped uploads and changes cache identity without pixel edits."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import remote_upload
from services.remote_config import RemoteHost
from services.remote_staging import stage_local_zips
from services.remote_upload import DatasetCache
from services.remote_scratch import source_fingerprint
from pipeline.sidecar import check_acquisition_inputs
from tests.test_acquisition_dataset_tools import _dataset
from util.acquisition_record import AcquisitionRecordError, read_record, write_record

pytestmark = pytest.mark.usefixtures('offline_transport')


def test_full_and_batch_uploads_send_scoped_record_and_metadata_changes_invalidate_cache(tmp_path, monkeypatch):
    root = tmp_path / 'dataset'
    _dataset(root)
    zipped = stage_local_zips(root / 'in', filename_schema='experiment:channel:well:fov:timepoint',
                              filename_sep='_')
    zipped = replace(zipped, well_zips=(zipped.well_zips[0],))
    cache = DatasetCache(RemoteHost(name='test', ssh_alias='test-host'))
    monkeypatch.setattr(cache, '_ssh_run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=''))
    monkeypatch.setattr(cache, '_ssh_script', lambda *a, **k: SimpleNamespace(returncode=0, stdout='1'))
    transfers = []

    def transfer(command, **options):
        files = [Path(part) for part in command if str(part).endswith('/acquisition.json')]
        assert len(files) == 1
        payload = json.loads(files[0].read_text())
        assert {tile['well'] for tile in payload['tiles']} == {'A01'}
        transfers.append(payload)
        return 0, 0.0, 0, 1

    monkeypatch.setattr(remote_upload, '_stream_rsync_with_retry', transfer)
    before = cache.cache_key(zipped)
    assert cache.ensure_uploaded(zipped)[0] == before
    assert cache.upload_subset(list(zipped.well_zips), '/remote/b1', acquisition_input=zipped)[2] == 0
    assert len(transfers) == 2
    fingerprint = source_fingerprint(root / 'in')
    record = read_record(root)
    record['timepoints']['0h']['pixel_size_um']['x'] = 0.65
    write_record(root, record)
    again = stage_local_zips(root / 'in', filename_schema=zipped.acquisition_schema, filename_sep='_')
    again = replace(again, well_zips=zipped.well_zips)
    assert cache.cache_key(again) != before
    assert source_fingerprint(root / 'in') != fingerprint


def test_remote_entrypoint_checks_record_before_processing(tmp_path):
    root = tmp_path / 'dataset'
    record = _dataset(root)
    check_acquisition_inputs(root / 'in', 'experiment:channel:well:fov:timepoint', '_')
    record['tiles'].pop()
    write_record(root, record)
    error = None
    try:
        check_acquisition_inputs(root / 'in', 'experiment:channel:well:fov:timepoint', '_')
    except AcquisitionRecordError as caught:
        error = caught
    assert error is not None and 'missing tile' in str(error)
