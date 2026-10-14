"""Assemble one acquisition record from the archives actually published.

Headers identify kept channel/field/timepoint tiles, so cancelled, resumed and
subset conversions do not advertise planned pixels that never reached disk.
Raw plane timestamps stay transient and reduce to one clock per timepoint.
"""

from __future__ import annotations

import copy
import json
import zipfile
from contextlib import contextmanager
from datetime import datetime
from itertools import groupby
from pathlib import Path

from pipeline.czi_read import AcquisitionClock, CziReader
from pipeline.timepoints import elapsed_hour_tokens
from util.acquisition_record import (
    AcquisitionRecordError, RECORD_FILENAME, new_record, new_timepoint,
    publication_marker, write_record_file,
    validate_record,
)
from util.image_exts import is_run_input_member
from util.json_write import write_json_atomic
from util.zip_write import atomic_zip

FRAME_COMMENT_PREFIX = b"allwell:czi-source-timepoint="


def frame_comment(index):
    """A native ordinal for recovery, never a per-image acquisition clock."""
    return FRAME_COMMENT_PREFIX + str(index).encode("ascii")


def shared_timepoint_tokens(timepoints):
    """Shared starts feed the existing rounded naming rules; timezone stays native."""
    indices = sorted(timepoints)
    positional = {index: f"t{index}" for index in indices}
    if any(timepoints[index]["started_at"] is None for index in indices):
        return positional, "Some shared acquisition starts are unknown"
    stamps = {index: datetime.fromisoformat(timepoints[index]["started_at"])
              for index in indices}
    if len({stamp.tzinfo is not None for stamp in stamps.values()}) > 1:
        return positional, "Shared acquisition starts mix zoned and unzoned clocks"
    if not stamps:
        return {}, None
    origin = stamps[indices[0]]
    hours = {ordinal: (stamps[index] - origin).total_seconds() / 3600
             for ordinal, index in enumerate(indices)}
    named = elapsed_hour_tokens(hours, len(indices))
    if named.tokens is None:
        return positional, "; ".join(named.reasons)
    return dict(zip(indices, named.tokens)), named.note or None


def source_stamp(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def _coordinate(readings, attribute) -> list | None:
    positions = {getattr(item, attribute) for item in readings
                 if getattr(item, attribute) is not None}
    return list(next(iter(positions))) if len(positions) == 1 else None


class RecordBuilder:
    """Source catalog and immutable file identity for one conversion."""

    def __init__(self, directory, source, positions, timepoint_plan, experiment, *, stamps=None):
        self.directory = Path(directory)
        self.layouts = dict(zip(source.files, source.layouts))
        self.stamps = {path: source_stamp(path) if stamps is None else stamps[path]
                       for path in source.files}
        self.experiment = experiment
        self.channels = source.layout.channel_names
        self.source_time_count = source.layout.size_t
        self.aliases = {token: index for index, token in timepoint_plan}
        previous = self.directory / RECORD_FILENAME
        if previous.exists():
            payload = json.loads(previous.read_text())
            validation = validate_record(payload)
            if not validation.ok:
                raise AcquisitionRecordError("; ".join(validation.issues))
            self.aliases.update({token: data["source_timepoint_index"]
                                 for token, data in payload["timepoints"].items()
                                 if data.get("source_timepoint_index") is not None})
        self.positions = {}
        for well, fields in positions.items():
            for field, position in enumerate(fields):
                for t_index, _token in timepoint_plan:
                    self.positions[well, f"f{field}", t_index] = position, t_index
        self.clocks = self._source_clocks()
        self.tokens, self.naming_reason = shared_timepoint_tokens(
            {index: clock.summary() for index, clock in self.clocks.items()})
        self.final_tokens = []

    def _source_clocks(self):
        clocks = {}
        fields = {(well, field): position
                  for (well, field, _index), (position, _t) in self.positions.items()}
        ordered = sorted(fields, key=lambda key: (fields[key].path, key))
        for path, keys in groupby(ordered, key=lambda key: fields[key].path):
            with CziReader(path, self.layouts[path]) as reader:
                for key in keys:
                    position = fields[key]
                    for index in range(self.source_time_count):
                        readings = reader.position_metadata(position.scene, position.tile, index)
                        clocks.setdefault(index, AcquisitionClock()).add_tile(
                            item.acquired_at for item in readings)
        self._unchanged()
        return clocks

    def _correct_name(self, info, well):
        key, channel = self._identity(info, well)
        return info.filename == (f"{self.experiment}_{self.channels[channel]}_"
                                 f"{well}_{key[1]}_{self.tokens[key[2]]}.tif")

    def _identity(self, info, well):
        prefix, token = info.filename.rsplit("_", 1)
        token = token.removesuffix(".tif")
        if info.comment.startswith(FRAME_COMMENT_PREFIX):
            index = int(info.comment[len(FRAME_COMMENT_PREFIX):])
        else:
            index = self.aliases.get(token)
        for channel, name in enumerate(self.channels):
            head = f"{self.experiment}_{name}_{well}_"
            if prefix.startswith(head):
                key = well, prefix[len(head):], index
                if key in self.positions:
                    return key, channel
        raise AcquisitionRecordError(
            f"{well}.zip: {info.filename!r} does not belong to this CZI conversion")

    def _published_tiles(self, *, ignore_wells=()):
        tiles = {}
        for path in sorted(self.directory.glob("*.zip")):
            if path.stem in ignore_wells:
                continue
            with zipfile.ZipFile(path) as archive:
                found = False
                for info in archive.infolist():
                    if not is_run_input_member(info.filename):
                        continue
                    key, channel = self._identity(info, path.stem)
                    tiles.setdefault(key, set()).add(channel)
                    found = True
                if not found:
                    raise AcquisitionRecordError(f"{path.name} has no published input images")
        return tiles

    def well_complete(self, well):
        path = self.directory / f"{well}.zip"
        if not path.exists():
            return False
        try:
            with zipfile.ZipFile(path) as archive:
                images = [info for info in archive.infolist()
                          if is_run_input_member(info.filename)]
                if not all(self._correct_name(info, well) for info in images):
                    return False
                actual = [self._identity(info, well) for info in images
                          if is_run_input_member(info.filename)]
        except (AcquisitionRecordError, ValueError, zipfile.BadZipFile):
            return False
        expected = {(key, channel) for key in self.positions if key[0] == well
                    for channel in range(len(self.channels))}
        return len(actual) == len(expected) and set(actual) == expected

    def _relabel(self, tokens):
        for path in sorted(self.directory.glob("*.zip")):
            with zipfile.ZipFile(path) as source:
                renamed = []
                for info in source.infolist():
                    target = copy.copy(info)
                    if is_run_input_member(info.filename):
                        key, channel = self._identity(info, path.stem)
                        well, field, index = key
                        target.filename = (f"{self.experiment}_{self.channels[channel]}_"
                                           f"{well}_{field}_{tokens[index]}.tif")
                        target.comment = frame_comment(index)
                    renamed.append((info, target))
                if all(old.filename == new.filename and old.comment == new.comment
                       for old, new in renamed):
                    continue
            # Windows cannot replace an archive while its old read handle is
            # open. Close the source before atomic_zip publishes the staging file.
            with atomic_zip(path, compression=zipfile.ZIP_STORED) as destination:
                with zipfile.ZipFile(path) as source:
                    destination.comment = source.comment
                    for old, new in renamed:
                        destination.writestr(new, source.read(old))

    def preflight(self, selected_wells):
        """Retained archives must belong to the source before pixels change."""
        self._unchanged()
        retained = self._published_tiles(ignore_wells=selected_wells)
        active = {position.path for key, (position, _t) in self.positions.items()
                  if key[0] in selected_wells or key in retained}
        if len({path.name for path in active}) != len(active):
            raise AcquisitionRecordError("Source filenames are not unique; tile provenance would be ambiguous")
        # One acquisition cannot carry two different known magnifications.
        # Unknown axes remain unknown rather than borrowed from another file.
        for axis in ("x", "y"):
            known = {self.layouts[path].pixel_size_um[axis] for path in active
                     if self.layouts[path].pixel_size_um[axis] is not None}
            if len(known) > 1:
                raise AcquisitionRecordError(
                    f"CZI files disagree about pixel_size_um.{axis}: {sorted(known)}")

    def build(self):
        self._unchanged()
        return self._assemble()

    def _unchanged(self):
        for path, stamp in self.stamps.items():
            if source_stamp(path) != stamp:
                raise AcquisitionRecordError(f"Source changed during conversion: {path.name}")

    def _assemble(self):
        published = self._published_tiles()
        record = new_record()
        clocks = self.clocks
        used_paths = {}
        sizes_by_timepoint = {}
        ordered = sorted(published, key=lambda key: (self.positions[key][0].path, key))
        for path, keys in groupby(ordered, key=lambda key: self.positions[key][0].path):
            with CziReader(path, self.layouts[path]) as reader:
                for key in keys:
                    well, field, token = key
                    position, t_index = self.positions[key]
                    readings = reader.position_metadata(
                        position.scene, position.tile, t_index, channels=sorted(published[key]))
                    used_paths.setdefault(token, set()).add(path)
                    sizes_by_timepoint.setdefault(token, set()).update(
                        item.tile_size_px for item in readings)
                    record["tiles"].append({
                        "well": well, "field": field, "timepoint": token,
                        "mosaic_xy_px": _coordinate(readings, "mosaic_xy_px"),
                        "stage_xy_um": _coordinate(readings, "stage_xy_um"),
                        "source_file": path.name,
                    })
        for token, paths in used_paths.items():
            metadata = new_timepoint()
            metadata.update(clocks[token].summary())
            metadata["source_timepoint_index"] = token
            for axis in ("x", "y"):
                scales = {self.layouts[path].pixel_size_um[axis] for path in paths}
                metadata["pixel_size_um"][axis] = next(iter(scales)) if len(scales) == 1 else None
                sizes = {size[0 if axis == "x" else 1] if size is not None else None
                         for size in sizes_by_timepoint[token]}
                metadata["tile_size_px"][axis] = next(iter(sizes)) if len(sizes) == 1 else None
            record["timepoints"][token] = metadata
        paths = set().union(*used_paths.values()) if used_paths else set()
        record["source_files"] = sorted(path.name for path in paths)
        record["source_file_stats"] = {
            path.name: {"size": self.stamps[path][0], "mtime_ns": self.stamps[path][1]}
            for path in sorted(paths)}
        tokens, reason = self.tokens, self.naming_reason
        record["timepoints"] = {tokens[index]: metadata
                                for index, metadata in record["timepoints"].items()}
        for tile in record["tiles"]:
            tile["timepoint"] = tokens[tile["timepoint"]]
        record["timepoint_naming"] = {"reason": reason}
        self._relabel(tokens)
        self.final_tokens = [tokens[index] for index in sorted(used_paths)]
        self._unchanged()
        return record


@contextmanager
def acquisition_publication(builder):
    """Guard ZIP/JSON changes; preserve precise partial output on exceptions.

    A metadata failure or hard interruption leaves the guard. A matching resume
    reconstructs from source and actual ZIP headers. This is a guarded publish,
    not an assertion that two filesystem replacements are one atomic operation.
    """
    path = builder.directory / RECORD_FILENAME
    marker = publication_marker(path)
    write_json_atomic(marker, {"source_files": sorted(p.name for p in builder.layouts)})
    try:
        yield
    finally:
        record = builder.build()
        write_record_file(path, record)
        marker.unlink()


def prepare_publication(directory, source, well_map, plan, experiment, stamps, selected):
    builder = RecordBuilder(directory, source, dict(zip(well_map.wells, well_map.positions)),
                            plan, experiment, stamps=stamps)
    builder.preflight({well_map.wells[index] for index in selected})
    return builder


def resumable_wells(builder, well_map, selected):
    return {well_map.wells[index] for index in selected
            if builder.well_complete(well_map.wells[index])}


def source_catalog(selected, files):
    from pipeline.czi_set import discover_czi_files
    paths = list(files) if files is not None else discover_czi_files(selected)
    return paths, {path: source_stamp(path) for path in paths}
