"""The acquisition record an OME-TIFF or ND2 conversion leaves beside its archives (tracker I5).

A CZI conversion has always written ``in/acquisition.json`` (:mod:`pipeline.czi_acquisition`); this is the
same record for the other two converters: the calibration and each tile's stage position, for every reader of
the record. It does not by itself place a nanowell well — a stage position needs the stage axes' direction,
which the run does not know (``nanowell_pattern_record.unplaced_wells``, tracker R8) — so a nanowell run on such
a plate still takes the grid described. What goes in is only what the source states:

* **Stage positions** in micrometres, per tile, when the source gives them with a unit it can be converted
  from — an OME ``<Plane>``'s ``PositionX``/``PositionY`` with their ``PositionXUnit``/``PositionYUnit``, or
  an ND2's position-loop points, which the format defines in µm. A coordinate in an unstated unit is ``null``:
  a scale that is wrong is worse than none, because placement trusts it.
* **Pixel size** per timepoint, when every tile of that timepoint agrees about it and the source states it
  as calibrated — an OME ``Pixels``' ``PhysicalSizeX``/``Y`` (whose unit OME defaults to µm), an ND2's
  calibration only when its ``bCalibrated`` flag says it was set.
* **Tile size** per timepoint, from the image dimensions, when every tile agrees.
* **No acquisition start.** Both converters name timepoints by elapsed time already, and neither source's
  absolute clock is stated in a timezone the record could carry honestly, so ``started_at`` is ``null`` with
  the reason written beside it.

**Which wells are recorded.** Every archive in the folder that holds exactly the members this conversion's
settings call for — the wells this run wrote, the wells it resumed, and wells an earlier run with the same
settings wrote. A record must name every archive in ``in/`` (``validate_against_archives``), so a folder
that also holds an archive these settings did not produce gets **no** record; the conversion removed any earlier one before writing its
first archive (:func:`discard_record`), so none is left describing a conversion the folder no longer holds.
An archive this run did not write or resume counts only when the record it replaced attributed it to the same
source file: identical settings over a different file write identical member names. The run log
says which.

Qt-free.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pipeline.explode_common import expected_members, well_is_complete
from util.acquisition_record import (
    AcquisitionRecordError, RECORD_FILENAME, new_record, new_timepoint, validate_record, write_record_file,
)
from util.well_token import canonical_well_label

log = logging.getLogger(__name__)

#: Why a converted timepoint's ``started_at`` is ``null`` — written into the record so a reader can tell a
#: clock that was not read from one the source did not have.
NO_START_REASON = ("The converter names timepoints by elapsed time and does not record an absolute "
                   "acquisition start")


@dataclass(frozen=True)
class TileSource:
    """What the source states about one written tile (one well, field and kept timepoint)."""
    stage_xy_um: tuple[float, float] | None
    pixel_size_um: tuple[float, float] | None
    tile_size_px: tuple[int, int] | None


def _agreed(values):
    """The one value every tile states, or ``None`` when any is unknown or two disagree."""
    values = set(values)
    return next(iter(values)) if len(values) == 1 else None


def build_record(source_name: str, tiles: dict) -> dict:
    """The acquisition record for *tiles*: ``{(well, field, timepoint): TileSource}``."""
    record = new_record(source_files=[source_name])
    by_tp: dict[str, list[TileSource]] = {}
    for (well, field, tp), src in sorted(tiles.items()):
        by_tp.setdefault(tp, []).append(src)
        record["tiles"].append({"well": well, "field": field, "timepoint": tp, "mosaic_xy_px": None,
                                "stage_xy_um": None if src.stage_xy_um is None else
                                [float(src.stage_xy_um[0]), float(src.stage_xy_um[1])],
                                "source_file": source_name})
    for tp, sources in by_tp.items():
        meta = new_timepoint()
        meta["start_reason"] = NO_START_REASON
        for k, axis in enumerate(("x", "y")):
            px = _agreed(None if s.pixel_size_um is None else s.pixel_size_um[k] for s in sources)
            size = _agreed(None if s.tile_size_px is None else s.tile_size_px[k] for s in sources)
            meta["pixel_size_um"][axis] = float(px) if px is not None else None
            meta["tile_size_px"][axis] = int(size) if size is not None else None
        record["timepoints"][tp] = meta
    return record


def publish_record(out_in_dir: Path, source_name: str, plan: dict, *, experiment: str, channels,
                   fovs: int, tp_plan, locate, written=(), earlier=(), log_fn=None) -> bool:
    """Write ``acquisition.json`` for the archives in *out_in_dir* this conversion's settings produced.

    *plan* maps every well label of the conversion to its index in the conversion's own well order; *tp_plan*
    is the conversion's ``(source timepoint index, token)`` list; ``locate(well_index, fov, source_index)``
    returns that tile's :class:`TileSource`. *written* is the wells this conversion wrote or resumed; *earlier*
    the wells the record it replaced attributed to this same source file (:func:`discard_record`). An archive
    that holds this conversion's members but is in neither came from a conversion this one cannot vouch for —
    the same settings over a different file name the same members — so it withholds the record like any other
    archive that is not this conversion's. Returns whether a record
    was written. Never raises for a record that cannot be built: the images are converted either way, and
    the run log says why the record is missing.
    """
    def _say(msg):
        log.info("%s", msg)
        if log_fn is not None:
            log_fn(msg + "\n")

    path = Path(out_in_dir) / RECORD_FILENAME
    complete, foreign = [], []
    for archive in sorted(Path(out_in_dir).glob("*.zip")):
        well = canonical_well_label(archive.stem)
        expected = expected_members(experiment, channels, archive.stem, fovs, [tp for _i, tp in tp_plan])
        vouched = archive.stem in set(written) or archive.stem in set(earlier)
        if well is not None and archive.stem in plan and vouched and well_is_complete(archive, expected):
            complete.append(archive.stem)
        else:
            foreign.append(archive.name)
    if foreign or not complete:        # the conversion's start already removed any earlier record
        listed = ", ".join(foreign[:4]) + (" …" if len(foreign) > 4 else "")
        why = (f"{listed} {'is' if len(foreign) == 1 else 'are'} not this conversion's output" if foreign
               else "no archive was written")
        _say(f"[record] No acquisition record written: {why}. A nanowell run on this folder needs the grid "
             "described instead.")
        return False
    tiles = {}
    for well in complete:
        for fov in range(fovs):
            for ti, tp in tp_plan:
                tiles[(well, f"f{fov}", tp)] = locate(plan[well], fov, ti)
    record = build_record(source_name, tiles)
    try:
        checked = validate_record(record)
        if not checked.ok:
            raise AcquisitionRecordError("; ".join(checked.issues))
        write_record_file(path, record)
    except (AcquisitionRecordError, OSError) as exc:
        _say(f"[record] No acquisition record written: {exc}")
        return False
    placed = sum(t["stage_xy_um"] is not None for t in record["tiles"])
    calibrated = sum(m["pixel_size_um"]["x"] is not None for m in record["timepoints"].values())
    _say(f"[record] Wrote {RECORD_FILENAME} for {len(complete)} well(s): {placed} of {len(record['tiles'])} "
         f"tile(s) with a stage position, {calibrated} of {len(record['timepoints'])} timepoint(s) with a "
         "pixel size.")
    return True


def discard_record(out_in_dir: Path, source_name: str) -> set[str]:
    """Remove the record an earlier conversion left, before this one writes a single archive; return the wells
    it attributed to *source_name*, so a later subset conversion of the same file can record them again.

    Read leniently: a record that cannot be read vouches for nothing, and is removed all the same.
    """
    import json

    path = Path(out_in_dir) / RECORD_FILENAME
    earlier: set[str] = set()
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        earlier = {str(t.get("well")) for t in old.get("tiles") or []
                   if isinstance(t, dict) and t.get("source_file") == source_name}
    except FileNotFoundError:
        return earlier
    except (OSError, ValueError, AttributeError) as exc:
        log.warning("The earlier %s in %s could not be read (%s); it vouches for no well", RECORD_FILENAME,
                    out_in_dir, exc)
    path.unlink(missing_ok=True)
    return earlier
