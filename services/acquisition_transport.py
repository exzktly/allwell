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
from util.nanowell_anchors import ANCHORS_FILENAME
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
    nanowell_anchors: bytes | None = None
    """The bytes of ``in/nanowell_anchors.json`` (``util.nanowell_anchors``), or ``None`` without one. Carried as
    bytes, not parsed: the remote pipeline reads them exactly as a local run would, so a file it cannot use fails
    the well there with the same message, and none is quietly dropped on the way."""


def carry_staged_record(function):
    @wraps(function)
    def wrapped(input_dir, **options):
        record = read_record_file(Path(input_dir) / RECORD_FILENAME)
        anchors = read_anchors_bytes(input_dir)
        result = function(input_dir, **options)
        result.acquisition_record = record
        result.nanowell_anchors = anchors
        result.acquisition_schema = options['filename_schema']
        result.acquisition_sep = options['filename_sep']
        return result
    return wrapped


def read_anchors_bytes(input_dir) -> bytes | None:
    """The anchors file's bytes in *input_dir*, or ``None`` when there is none."""
    try:
        return (Path(input_dir) / ANCHORS_FILENAME).read_bytes()
    except FileNotFoundError:
        return None


def _payload(zipped, paths) -> dict | None:
    record = getattr(zipped, 'acquisition_record', None)
    if record is None:
        return None
    return filter_record_to_paths(paths, record, schema=zipped.acquisition_schema,
                                  sep=zipped.acquisition_sep)


def _anchors_for_wells(anchors: bytes, paths) -> bytes:
    """The anchors of the wells in *paths* only, canonically, for the cache key; the bytes as they are when the file
    cannot be parsed (the remote pipeline will fail those wells on it, and the key must still move with it)."""
    from util.nanowell_anchors import AnchorsFileError, anchors_payload, parse_anchors
    from util.well_token import canonical_well_label

    try:
        parsed = parse_anchors(json.loads(anchors.decode('utf-8')))
    except (AnchorsFileError, ValueError):
        return anchors
    wells = {canonical_well_label(Path(p).stem) for p in paths}
    kept = {key: a for key, a in parsed.items() if key[0] in wells}
    return json.dumps(anchors_payload(kept), sort_keys=True).encode('utf-8')


def acquisition_cache_key(zipped):
    """The upload cache key: the archives, and the record and anchors uploaded beside them. An anchor set since the
    last upload on one of the uploaded wells changes it, so the new file is sent rather than the cache found complete
    without it; an anchor on another well does not, so one click does not re-upload a run that does not include it.
    The whole file is still what is uploaded, so the remote reads exactly what a local run would."""
    base = fingerprint_archives(zipped.well_zips)
    record = _payload(zipped, zipped.well_zips)
    anchors = getattr(zipped, 'nanowell_anchors', None)
    if record is None and anchors is None:
        return base
    digest = hashlib.blake2b(base.encode('ascii'), digest_size=16)
    if record is not None:
        digest.update(json.dumps(record, sort_keys=True, separators=(',', ':')).encode('utf-8'))
    if anchors is not None:
        digest.update(b'\0anchors\0' + _anchors_for_wells(anchors, zipped.well_zips))
    return digest.hexdigest()


def _beside_the_archives(zipped, paths, folder: Path) -> tuple[Path, ...]:
    """Write the record (scoped to *paths*) and the anchors into *folder*; the files to upload beside the archives."""
    files = []
    record = _payload(zipped, paths)
    if record is not None:
        files.append(Path(folder) / RECORD_FILENAME)
        write_record_file(files[-1], record)
    anchors = getattr(zipped, 'nanowell_anchors', None)
    if anchors is not None:
        files.append(Path(folder) / ANCHORS_FILENAME)
        files[-1].write_bytes(anchors)
    return tuple(files)


def carry_upload_record(function):
    @wraps(function)
    def wrapped(cache, zipped, **options):
        with tempfile.TemporaryDirectory(prefix='allwell-acquisition-') as folder:
            files = _beside_the_archives(zipped, zipped.well_zips, Path(folder))
            if not files:
                return function(cache, zipped, **options)
            return function(cache, replace(zipped, extra_upload_files=files), **options)
    return wrapped


def carry_subset_record(function):
    @wraps(function)
    def wrapped(cache, paths, remote_path, *, acquisition_input=None, **options):
        if acquisition_input is None:
            return function(cache, paths, remote_path, **options)
        with tempfile.TemporaryDirectory(prefix='allwell-acquisition-') as folder:
            files = _beside_the_archives(acquisition_input, paths, Path(folder))
            if not files:
                return function(cache, paths, remote_path, **options)
            return function(cache, paths, remote_path, extra_sources=files, **options)
    return wrapped
