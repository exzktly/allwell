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


@pytest.mark.parametrize("invalid", ["coverage", "corrupt", "pending"])
def test_actual_cli_refuses_invalid_record_before_pixel_setup(tmp_path, monkeypatch, invalid):
    import process_microscopy as pm
    import sys
    from util.acquisition_record import publication_marker
    root = tmp_path / "dataset"
    record = _dataset(root)
    path = root / "in" / "acquisition.json"
    if invalid == "coverage":
        record["tiles"].pop()
        write_record(root, record)
    elif invalid == "corrupt":
        path.write_text("{invalid")
    else:
        publication_marker(path).write_text("{}")
    monkeypatch.setattr(sys, "argv", ["process_microscopy.py", "--input_dir", str(root / "in"),
                                     "--output_dir", str(root / "out")])
    monkeypatch.setattr(pm._suspend, "install_handlers", lambda **kw: None)
    monkeypatch.setattr(pm._suspend, "watch_parent", lambda *a, **kw: None)
    reached = []

    class ReachedSetup(RuntimeError):
        pass

    def setup(*args):
        reached.append(True)
        raise ReachedSetup()

    monkeypatch.setattr(pm, "plan_parallelism", setup)
    error = None
    try:
        pm.main()
    except AcquisitionRecordError as caught:
        error = caught
    except ReachedSetup:
        pass
    assert not reached, "invalid acquisition metadata reached pixel setup"
    assert error is not None
