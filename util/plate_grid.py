"""Serpentine plate-position labelling for packed multi-position acquisitions.

Some acquisition software emits a single file (an OME-TIFF, say) that holds every
stage position visited during a scan, in **serpentine** (boustrophedon) order:
the stage sweeps one row of wells left-to-right, drops to the next row and sweeps
right-to-left, and so on — reversing direction every row so it never has to fly
back across the plate. There are no per-file well tokens to parse, so the well
label of each position has to be *reconstructed* from the acquisition order plus
the dimensions of the imaged block.

This module turns "the user imaged a ``rows`` × ``cols`` block of wells starting
at ``origin``, snaking by row" into the ordered list of 96-well labels the rest
of All-Well already understands. It depends only on the stdlib and
``util.well_token`` (the single source of truth for the 96-well geometry), so the
pipeline can import it without breaching the "no ``well_viewer`` / ``widgets`` /
Qt imports" contract — the same reason ``util.well_token`` and ``util.schema``
live under ``util/``.

The 96-well plate is the only supported vocabulary: rows ``A``–``H`` (8) and
columns ``1``–``12`` (12). A grid that would run past ``H`` or ``12`` from the
chosen origin is rejected rather than silently wrapped.
"""

from __future__ import annotations

from util.well_token import canonical_well_label

# 96-well geometry. Kept here as the labelling bound; ``util.well_token`` owns the
# parsing/validation of an individual token.
PLATE_ROWS = "ABCDEFGH"
PLATE_MAX_COL = 12

# Serpentine snake axis. "row" snakes along rows (sweep a row, step down, sweep
# back); "col" snakes along columns (sweep a column, step right, sweep back).
DIRECTIONS = ("row", "col")


def well_row_col(origin: str) -> tuple[int, int]:
    """Return the ``(row_index, col_index)`` of *origin* as 0-based offsets.

    Row ``A`` → 0, column ``1`` → 0. Raises ``ValueError`` if *origin* is not a
    valid 96-well position. Public because placing an *inferred* block of wells
    at a chosen origin (``datasets.ome_import.ome_import_compute.suggest_layout``)
    is the same question this module already answers for a typed grid.
    """
    canon = canonical_well_label(origin)
    if canon is None:
        raise ValueError(
            f"Origin well {origin!r} is not a valid 96-well position (A01–H12)."
        )
    row_idx = PLATE_ROWS.index(canon[0])
    col_idx = int(canon[1:]) - 1
    return row_idx, col_idx


def serpentine_well_labels(
    rows: int,
    cols: int,
    *,
    origin: str = "A01",
    direction: str = "row",
) -> list[str]:
    """A *rows* × *cols* block in serpentine order — :func:`block_well_labels`.

    Kept as the name for the snaking case, which is what most callers mean and
    what every one of them meant before a scan could say otherwise.
    """
    return block_well_labels(rows, cols, origin=origin, direction=direction,
                             serpentine=True)


def block_well_labels(
    rows: int,
    cols: int,
    *,
    origin: str = "A01",
    direction: str = "row",
    serpentine: bool = True,
) -> list[str]:
    """Return the 96-well labels of a *rows* × *cols* block in acquisition order.

    The block's top-left corner is *origin* (default ``"A01"``). ``direction``
    selects the snake axis:

    * ``"row"`` — sweep a whole row of wells, then step down to the next row.
    * ``"col"`` — sweep a whole column, then step right to the next column.

    *serpentine* decides what happens at the end of each line. True snakes: row 0
    runs left-to-right, row 1 right-to-left, and the stage never travels back
    across the plate empty. False rasters: every row runs left-to-right, the
    stage flying back to the near edge between them. Both are ordinary
    acquisition patterns and a scan cannot be assumed to be either — a raster
    read as a snake mislabels every second row *backwards*, which is a silent
    well-mapping error with the right well count.

    The returned list has length ``rows * cols`` and is in *acquisition order*:
    element ``i`` is the well the stage visited at position ``i``.

    Raises ``ValueError`` for a non-positive dimension, an unknown *direction*, an
    invalid *origin*, or a block that would extend past ``H`` / column ``12`` from
    the origin.
    """
    if rows <= 0 or cols <= 0:
        raise ValueError(f"Grid dimensions must be positive (got {rows}×{cols}).")
    if direction not in DIRECTIONS:
        raise ValueError(
            f"Unknown serpentine direction {direction!r}; expected one of {DIRECTIONS}."
        )

    row0, col0 = well_row_col(origin)
    last_row = row0 + rows - 1
    last_col = col0 + cols - 1
    if last_row >= len(PLATE_ROWS) or last_col >= PLATE_MAX_COL:
        raise ValueError(
            f"A {rows}×{cols} block from origin {origin} runs past the 96-well "
            f"plate (would reach row {PLATE_ROWS[min(last_row, 7)]}"
            f"{'+' if last_row >= len(PLATE_ROWS) else ''}, column {last_col + 1}). "
            f"The plate is rows A–H, columns 1–12."
        )

    def _label(row_idx: int, col_idx: int) -> str:
        return f"{PLATE_ROWS[row_idx]}{col_idx + 1:02d}"

    def _line(length: int, index: int) -> range:
        if serpentine and index % 2:
            return range(length - 1, -1, -1)
        return range(length)

    labels: list[str] = []
    if direction == "row":
        for r in range(rows):
            for c in _line(cols, r):
                labels.append(_label(row0 + r, col0 + c))
    else:  # "col"
        for c in range(cols):
            for r in _line(rows, c):
                labels.append(_label(row0 + r, col0 + c))
    return labels


def normalize_well_order(labels) -> list[str]:
    """Canonicalise an explicit acquisition ordering into 96-well labels.

    Takes an arbitrary sequence of well tokens — the order in which the stage
    visited wells, as defined by clicking a plate mockup rather than derived from
    a rectangular serpentine — and returns the canonical zero-padded uppercase
    labels (``"a1"`` → ``"A01"``) in the same order. Unlike
    :func:`serpentine_well_labels` the wells need not form a rectangle or any
    particular shape; the only constraints are that every token is a valid
    96-well position and no well appears twice (a stage can't be at two places at
    the same acquisition index).

    Raises ``ValueError`` for an empty ordering, an invalid token, or a duplicate.
    """
    order = list(labels or [])
    if not order:
        raise ValueError("The well ordering is empty; add at least one well.")
    seen: set[str] = set()
    result: list[str] = []
    for tok in order:
        canon = canonical_well_label(str(tok))
        if canon is None:
            raise ValueError(
                f"Well {tok!r} is not a valid 96-well position (A01–H12)."
            )
        if canon in seen:
            raise ValueError(
                f"Well {canon} appears more than once in the ordering; each well "
                f"may be visited only once."
            )
        seen.add(canon)
        result.append(canon)
    return result


def position_to_well_fov(
    index: int,
    *,
    well_labels: list[str],
    fovs_per_well: int,
) -> tuple[str, int]:
    """Split a flat stage-position *index* into its ``(well_label, fov_index)``.

    Assumes the stage acquires every field-of-view (FOV) tile of a well before
    advancing to the next well — i.e. FOV tiles are *contiguous* within a well
    (well-major order). With ``fovs_per_well`` tiles each, position ``index``
    belongs to well ``well_labels[index // fovs_per_well]`` and is that well's FOV
    ``index % fovs_per_well`` (0-based).

    Raises ``ValueError`` for a non-positive ``fovs_per_well`` or an *index* that
    falls outside ``len(well_labels) * fovs_per_well``.
    """
    if fovs_per_well <= 0:
        raise ValueError(f"fovs_per_well must be positive (got {fovs_per_well}).")
    total = len(well_labels) * fovs_per_well
    if not (0 <= index < total):
        raise ValueError(
            f"Position index {index} out of range for {len(well_labels)} well(s) "
            f"× {fovs_per_well} FOV(s) = {total} position(s)."
        )
    return well_labels[index // fovs_per_well], index % fovs_per_well
