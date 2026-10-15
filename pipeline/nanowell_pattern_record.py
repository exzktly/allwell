"""An acquisition record built from a grid the user describes, for a dataset that has none (tracker P15).

Nanowell mode places tiles from ``in/acquisition.json``, which conversion writes. A dataset converted before
that record existed has only its images. For it the run accepts the grid, the scan pattern, the nominal overlap
and the pixel size on the command line (``--nanowell_grid`` and friends) and builds, in memory, the record the
rest of the run reads: each field's mosaic position from where the scan pattern puts it
(``tile_grid.scan_cells``), the tile size from the images themselves. Nothing is written into the user's input
folder, and nothing about the images is checked against the description — registration then corrects the
nominal positions, as it does recorded ones.

**Fields are taken in the order of their names, numerically** (``F2`` before ``F10``, ``r0c1`` before
``r1c0``), as the stage visited them: that is the one assumption a record-less placement has to make, and the
log line and the sidecar say which pattern and order were used. The record has no acquisition times, so Review
times the timepoints from their labels.

Qt-free; in the remote manifest (the pipeline imports it).
"""

from __future__ import annotations

import re

from pipeline.tile_grid import ScanPattern, scan_cells

#: ``--nanowell_scan`` spellings: ``<corner>-<row|column>`` with an optional ``-serpentine``.
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


def parse_scan(text: str) -> ScanPattern:
    """``"top-left-row"`` / ``"bottom-right-column-serpentine"`` -> :class:`ScanPattern`; ``ValueError`` otherwise."""
    value = str(text or "").strip().lower()
    serpentine = value.endswith("-serpentine")
    if serpentine:
        value = value[: -len("-serpentine")]
    for corner in CORNERS:
        for major in ("row", "column"):
            if value == f"{corner}-{major}":
                return ScanPattern(corner, major, serpentine)
    raise ValueError(f"{text!r} is not a scan pattern: write <corner>-<row|column>[-serpentine], the corner one "
                     f"of {', '.join(CORNERS)} (e.g. top-left-row-serpentine)")


def parse_grid(text: str) -> tuple[int, int]:
    """``"5x5"`` / ``"4 x 6"`` -> ``(rows, columns)``; ``ValueError`` otherwise."""
    found = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", str(text or ""))
    if not found or int(found.group(1)) < 1 or int(found.group(2)) < 1:
        raise ValueError(f"{text!r} is not a grid: write ROWSxCOLUMNS, e.g. 5x5")
    return int(found.group(1)), int(found.group(2))


def _natural(token: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(token))]


def record_from_pattern(well: str, fields_by_timepoint: dict, tile_size_px: dict, pattern: dict) -> dict:
    """The acquisition record for *well* built from *pattern* (the run's ``pattern`` option).

    *fields_by_timepoint* maps a timepoint to its field tokens and *tile_size_px* a timepoint to its tiles'
    ``(width, height)``. *pattern* is ``{"grid": [rows, cols], "scan": "...", "overlap_percent": p,
    "pixel_size_um": s}``. A timepoint with more fields than the grid has cells keeps them all, the extra ones
    below the grid: placement against the grid's shape then refuses it (``bad_grid``, the grid "exceeds the
    expected" shape), as it would refuse a record that disagreed with its images. A timepoint with fewer is
    placed as far as its fields go and refused as ``incomplete_grid`` for the same reason.

    Fields are dealt onto the scan in the order of their names, numerically, so a name that encodes a cell
    (``r0c1``) is not read as one: it is dealt like any other.
    """
    from util.acquisition_record import new_record, new_timepoint

    rows, cols = pattern["grid"]
    cells = scan_cells(rows, cols, parse_scan(pattern["scan"]))
    overlap = float(pattern["overlap_percent"])
    pixel = float(pattern["pixel_size_um"])
    record = new_record()
    for tp, fields in fields_by_timepoint.items():
        width, height = tile_size_px[tp]
        meta = new_timepoint()
        meta.update(pixel_size_um={"x": pixel, "y": pixel}, tile_size_px={"x": int(width), "y": int(height)},
                    declared_overlap_percent={"x": overlap, "y": overlap},
                    start_reason="No acquisition record: placed from the scan pattern given for the run")
        record["timepoints"][tp] = meta
        step = (width * (1 - overlap / 100), height * (1 - overlap / 100))
        ordered = sorted(fields, key=lambda f: (_natural(f), str(f)))     # F1 and F001: name decides
        for k, field in enumerate(ordered):
            row, col = cells[k] if k < len(cells) else (rows + k, 0)      # beyond the grid: refused by placement
            record["tiles"].append({"well": well, "field": field, "timepoint": tp,
                                    "mosaic_xy_px": [round(col * step[0], 3), round(row * step[1], 3)],
                                    "stage_xy_um": None})
    record["placement"] = {"from": "scan pattern", **{k: pattern[k] for k in sorted(pattern)},
                           "field_order": "field names, numerically"}
    return record
