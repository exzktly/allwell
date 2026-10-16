"""Carry acquisition metadata through staged, scoped and batched input uploads."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path

from services.remote_run_state import LocalInputMode
from services.remote_scratch import fingerprint_archives
from util.acquisition_record import (
    RECORD_FILENAME, read_record_file, write_record_file, filter_record_to_paths,
)
from util.schema import DEFAULT_SCHEMA, DEFAULT_SEP
@dataclass
class ZippedInput:
    """The output of :class:`LocalZipper`: a directory of ``{well}.zip``
    files plus the local input mode the user would have triggered."""

    zip_dir: Path
    mode: LocalInputMode
    well_zips: tuple[Path, ...]
    """Sorted ``{well}.zip`` paths inside ``zip_dir``."""

    cleanup: Callable[[], None] | None = None
    """Optional callback that removes any temporary staging directory the
    zipper created. ``None`` when the input was already in zip form."""

    on_pipeline_success: Callable[[], None] | None = None
    """Optional callback fired ONLY after a clean pipeline run-to-
    completion. Used to delete source loose TIFs / well folders that
    were zipped into ``input_dir`` in-place, so a successful run
    converts the user's input folder from loose-TIF mode to zip mode
    (next run sees only zips, skips the zipper entirely). Never
    fires on failure / cancel / partial completion — loose sources
    stay intact so the user can re-run from scratch."""

    incremental: bool = False
    """True when this is an incremental remote run: the staged zips carry only
    the new-FOV members (client-side split), so the Fetcher must MERGE the
    partial outputs into the existing local CSV / out-zip rather than overwrite."""

    up_to_date_wells: tuple[str, ...] = ()
    """Wells dropped from the upload because their local output already covers
    every input FOV. The runner emits a synthetic ``[remote] ✓ X: up to date``
    for each so the dashboard marks them complete."""

    acquisition_record: dict | None = None
    acquisition_schema: str = DEFAULT_SCHEMA
    acquisition_sep: str = DEFAULT_SEP
    extra_upload_files: tuple[Path, ...] = ()


def carry_staged_record(function):
    @wraps(function)
    def wrapped(input_dir, **options):
        record = read_record_file(Path(input_dir) / RECORD_FILENAME)
        result = function(input_dir, **options)
        result.acquisition_record = record
        result.acquisition_schema = options['filename_schema']
        result.acquisition_sep = options['filename_sep']
        return result
    return wrapped


def _payload(zipped, paths) -> dict | None:
    record = getattr(zipped, 'acquisition_record', None)
    if record is None:
        return None
    return filter_record_to_paths(paths, record, schema=zipped.acquisition_schema,
                                  sep=zipped.acquisition_sep)


def acquisition_cache_key(zipped):
    base = fingerprint_archives(zipped.well_zips)
    record = _payload(zipped, zipped.well_zips)
    if record is None:
        return base
    digest = hashlib.blake2b(base.encode('ascii'), digest_size=16)
    digest.update(json.dumps(record, sort_keys=True, separators=(',', ':')).encode('utf-8'))
    return digest.hexdigest()


def carry_upload_record(function):
    @wraps(function)
    def wrapped(cache, zipped, **options):
        record = _payload(zipped, zipped.well_zips)
        if record is None:
            return function(cache, zipped, **options)
        with tempfile.TemporaryDirectory(prefix='allwell-acquisition-') as folder:
            path = Path(folder) / RECORD_FILENAME
            write_record_file(path, record)
            return function(cache, replace(zipped, extra_upload_files=(path,)), **options)
    return wrapped


def carry_subset_record(function):
    @wraps(function)
    def wrapped(cache, paths, remote_path, *, acquisition_input=None, **options):
        record = _payload(acquisition_input, paths)
        if record is None:
            return function(cache, paths, remote_path, **options)
        with tempfile.TemporaryDirectory(prefix='allwell-acquisition-') as folder:
            path = Path(folder) / RECORD_FILENAME
            write_record_file(path, record)
            return function(cache, paths, remote_path, extra_sources=(path,), **options)
    return wrapped
