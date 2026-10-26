"""Moving a set of wells as one rigid block on a 96-well plate.

Merge Plates' "place dataset 2 starting at well E01" is this, and so is the
Job Queue's projection of what a queued Merge will write
(``datasets/job_queue/projection.py``). It lives in ``datasets/core/`` because
two tools need it and one Datasets tool never imports another (ARCHITECTURE
§6b, ``tests/test_datasets_tool_isolation.py``).

Qt-free.
"""

from __future__ import annotations

from util.well_token import canonical_well_label

_PLATE_ROWS = "ABCDEFGH"
_PLATE_MAX_COL = 12


class WellBlockError(ValueError):
    """The block cannot be placed: a well that is not a plate position, or a
    shift that runs off the plate. The message is user-facing."""


def block_shift_well_map(wells, *, target_origin: str) -> dict[str, str]:
    """Translate *wells* as a rigid block so their minimum (row, col) corner
    lands at *target_origin* — the "place dataset 2 starting at E01" UI
    control. Covers the common quadrant-placement case; an arbitrary,
    non-rectangular remap still goes through ``well_map`` directly.

    Raises :class:`WellBlockError` if the shifted block would run off the
    96-well plate (a column past 12 or a row past H).
    """
    tokens = [canonical_well_label(w) for w in wells]
    if any(t is None for t in tokens):
        raise WellBlockError("Cannot shift: one or more wells aren't valid 96-well positions.")
    if not tokens:
        return {}
    target = canonical_well_label(target_origin)
    if target is None:
        raise WellBlockError(f"{target_origin!r} isn't a valid 96-well position.")

    rcs = [(_PLATE_ROWS.index(t[0]), int(t[1:]) - 1) for t in tokens]
    min_row = min(r for r, _ in rcs)
    min_col = min(c for _, c in rcs)
    target_row = _PLATE_ROWS.index(target[0])
    target_col = int(target[1:]) - 1
    delta_row = target_row - min_row
    delta_col = target_col - min_col

    result: dict[str, str] = {}
    for token, (row, col) in zip(tokens, rcs):
        new_row, new_col = row + delta_row, col + delta_col
        if not (0 <= new_row < len(_PLATE_ROWS)) or not (0 <= new_col < _PLATE_MAX_COL):
            raise WellBlockError(
                f"Shifting to {target_origin} would place well {token} outside "
                "the 96-well plate."
            )
        result[token] = f"{_PLATE_ROWS[new_row]}{new_col + 1:02d}"
    return result
