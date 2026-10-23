"""Nanowell anchors set by hand: where a person says a well's central square is, at one timepoint.

A nanowell well is addressed from its origin, the central square. When the run cannot find the square on its own
(``nanowell_lattice.resolve_origin`` accepts no fiducial, array centre or plate-rigid prediction) the timepoint is
left unaddressed. A person can then click the square on the well's montage in Review (Nanowells → Array QC → Full
montage), and the click is kept here, in ``in/nanowell_anchors.json`` beside the acquisition record. It belongs to
the input images rather than to one run's output: it survives a re-run, and a remote run carries it as it carries
the record.

A position is in the well frame, in micrometres: the frame the grid record's ``montage_origin_um`` and ``tiles``
are stated in, so a pixel of the stored montage maps to it as ``montage_origin_um + pixel * montage_um_per_px``.
The pipeline snaps it to the nearest lattice site, so it need only be within a quarter pitch of the square.

Qt-free and stdlib-only: the pipeline reads it on a remote worker, and Review writes it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from util.json_write import write_json_atomic
from util.well_token import canonical_well_label

ANCHORS_FILENAME = "nanowell_anchors.json"
ANCHORS_VERSION = 1

#: How each origin source reads after "origin from" (``nanowell_lattice.resolve_origin``'s method names).
ORIGIN_PHRASES = {"manual": "the anchor set by hand", "fiducial": "the central square",
                  "array_centre": "the array's centre", "plate_rigid": "the rest of the plate"}


def origin_phrase(method) -> str:
    """*method* as a reader says it: ``"manual"`` is "the anchor set by hand"."""
    return ORIGIN_PHRASES.get(str(method), str(method).replace("_", " "))


class AnchorsFileError(ValueError):
    """The anchors file exists and cannot be used. Never treated as "no anchors": a run that ignored a person's
    anchors would leave the wells they anchored unaddressed with nothing to say why."""


@dataclass(frozen=True)
class Anchor:
    well: str              # canonical (``A01``)
    timepoint: str
    x_um: float            # well frame
    y_um: float

    def position(self) -> tuple[float, float]:
        return (self.x_um, self.y_um)


def anchors_path(input_dir: str | Path) -> Path:
    """Where the anchors of the dataset whose ``in/`` folder is *input_dir* are kept."""
    return Path(input_dir) / ANCHORS_FILENAME


def _anchor(row, index: int) -> Anchor:
    if not isinstance(row, dict):
        raise AnchorsFileError(f"anchor {index + 1} is not an object")
    well = canonical_well_label(str(row.get("well", "")))
    if well is None:
        raise AnchorsFileError(f"anchor {index + 1} names no valid well ({row.get('well')!r})")
    timepoint = row.get("timepoint")
    if not isinstance(timepoint, str) or not timepoint:
        raise AnchorsFileError(f"anchor {index + 1} ({well}) names no timepoint")
    try:
        x, y = float(row["x_um"]), float(row["y_um"])
    except (KeyError, TypeError, ValueError):
        raise AnchorsFileError(f"anchor {index + 1} ({well} at {timepoint}) has no position") from None
    if not (math.isfinite(x) and math.isfinite(y)):
        raise AnchorsFileError(f"anchor {index + 1} ({well} at {timepoint}) has a position that is not a number")
    return Anchor(well, timepoint, x, y)


def parse_anchors(payload) -> dict[tuple[str, str], Anchor]:
    """``(well, timepoint) -> Anchor`` from the file's JSON; raises :class:`AnchorsFileError` on anything else.

    Two anchors for one (well, timepoint) are refused rather than one picked: either could be the one meant.
    """
    if not isinstance(payload, dict) or payload.get("version") != ANCHORS_VERSION:
        raise AnchorsFileError(f"not a version-{ANCHORS_VERSION} anchors file")
    rows = payload.get("anchors")
    if not isinstance(rows, list):
        raise AnchorsFileError("it holds no list of anchors")
    out: dict[tuple[str, str], Anchor] = {}
    for index, row in enumerate(rows):
        anchor = _anchor(row, index)
        key = (anchor.well, anchor.timepoint)
        if key in out:
            raise AnchorsFileError(f"{anchor.well} at {anchor.timepoint} is anchored twice")
        out[key] = anchor
    return out


def read_anchors(path: str | Path) -> dict[tuple[str, str], Anchor]:
    """The anchors in *path*; ``{}`` when there is no such file. Raises :class:`AnchorsFileError`, naming the file,
    when it exists and cannot be used."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnchorsFileError(f"{path} could not be read ({exc})") from None
    try:
        return parse_anchors(payload)
    except AnchorsFileError as exc:
        raise AnchorsFileError(f"{path}: {exc}") from None


def anchors_payload(anchors: dict[tuple[str, str], Anchor]) -> dict:
    rows = [{"well": a.well, "timepoint": a.timepoint, "x_um": round(a.x_um, 3), "y_um": round(a.y_um, 3)}
            for _key, a in sorted(anchors.items())]
    return {"version": ANCHORS_VERSION, "anchors": rows}


def write_anchors(path: str | Path, anchors: dict[tuple[str, str], Anchor]) -> None:
    """Write *anchors* to *path* atomically; with none left, remove the file, so "no anchors" has one spelling."""
    path = Path(path)
    if not anchors:
        path.unlink(missing_ok=True)
        return
    write_json_atomic(path, anchors_payload(anchors))


def set_anchor(path: str | Path, well: str, timepoint: str, position_um) -> Anchor:
    """Set (or replace) *well*'s anchor at *timepoint* in the file at *path*, and return it."""
    canonical = canonical_well_label(well)
    if canonical is None:
        raise AnchorsFileError(f"{well!r} is not a well")
    anchors = read_anchors(path)
    anchor = Anchor(canonical, timepoint, float(position_um[0]), float(position_um[1]))
    anchors[(canonical, timepoint)] = anchor
    write_anchors(path, anchors)
    return anchor


def clear_anchor(path: str | Path, well: str, timepoint: str) -> bool:
    """Remove *well*'s anchor at *timepoint*; whether there was one."""
    anchors = read_anchors(path)
    found = anchors.pop((canonical_well_label(well) or well, timepoint), None) is not None
    if found:
        write_anchors(path, anchors)
    return found


def well_anchors(anchors: dict[tuple[str, str], Anchor], well: str) -> dict[str, tuple[float, float]]:
    """``timepoint -> (x_um, y_um)`` for one well."""
    canonical = canonical_well_label(well) or well
    return {tp: a.position() for (w, tp), a in anchors.items() if w == canonical}
