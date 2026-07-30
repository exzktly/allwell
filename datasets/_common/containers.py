"""Shared well-container (zip/folder) IO for the Datasets-pane features.

Lifted from ``datasets.concatenate.apply``: reading a well's raw-input or
QC-output container as a flat list of ``(member_name, bytes)``, and writing
one back out as a fresh deflated zip with a collision-safe, first-write-wins
policy. Every feature that copies or re-packs a well's archives (Concatenate,
Subset, Merge plates) shares this rather than re-implementing zip/folder IO.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from datasets._common.dataset_paths import DatasetPaths

TIMEPOINT_COLUMN = "timepoint"
TIMEPOINT_HOURS_COLUMN = "timepoint_hours"
FILENAME_COLUMN = "filename"
OVERRIDES_FILENAME = "cell_overrides.json"
OVERRIDES_SCHEMA_VERSION = 2


def read_members(container: Path) -> list[tuple[str, bytes]]:
    """Read a well container's files as ``(member_name, bytes)``.

    Works for both input layouts: a ``.zip`` (its entries) or a folder (its
    files by POSIX-relative name). Directory entries are skipped.
    """
    if container.is_dir():
        members: list[tuple[str, bytes]] = []
        for path in sorted(container.rglob("*")):
            if path.is_file():
                members.append((path.relative_to(container).as_posix(), path.read_bytes()))
        return members
    with zipfile.ZipFile(container) as archive:
        return [
            (info.filename, archive.read(info.filename))
            for info in archive.infolist()
            if not info.is_dir()
        ]


def write_zip(dest: Path, members: list[tuple[str, bytes]]) -> tuple[frozenset[str], frozenset[str]]:
    """Write ``(name, bytes)`` members into a fresh deflated zip at *dest*.

    A later member whose name duplicates an earlier one is skipped rather than
    overwriting it — callers that always list dataset 1's (or the base
    dataset's) members first can rely on a same-named member from elsewhere
    never silently clobbering it. Returns ``(written, dropped)`` — *written*
    is the set of names actually written (so callers can verify a CSV row's
    ``filename`` value actually landed in the archive); *dropped* is the set
    of names that were requested but skipped due to a collision, so a caller
    can tell a genuine duplicate-write apart from data that never made it in
    at all.

    ``compresslevel=1``: microscopy image formats are already
    information-dense, so re-deflating at the default level 6 spends a lot of
    CPU for negligible size reduction — this is a pure container-level merge,
    not a size-optimization pass.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()
    dropped: set[str] = set()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for name, data in members:
            if name in written:
                dropped.add(name)
                continue
            written.add(name)
            archive.writestr(name, data)
    return frozenset(written), frozenset(dropped)


def clear_destination(dest: DatasetPaths) -> None:
    """Remove a destination's ``in``/``out`` content (a failed prior attempt).

    Every Datasets feature rebuilds a destination from its source(s) fresh on
    each ``execute_plan``/apply call — nothing is incremental — so the only
    correct way to resume into a destination that already has leftovers is to
    clear them first; otherwise a partially-written file (or a well from an
    earlier, differently-scoped attempt) would linger in the "finished"
    output.
    """
    for folder in (dest.in_dir, dest.out_dir):
        if folder.exists():
            shutil.rmtree(folder)
