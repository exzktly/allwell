"""Single source of truth for ordering field-of-view (FOV) tokens.

An FOV reaches the app as a string from three directions — a filename field, a
CSV column, and a combo box — and several places turn it into a number purely to
*sort* it: the pipeline's ``available_fovs`` listing, the remote runner's
refresh, and the viewer's FOV dropdowns (the dataset cache, the review-image
selector, cell gating, the cell-browser picker). Each wrote ``float(token)``
inline, which orders a bare-number token (``"1"``, ``"01"``) correctly but drops
a *prefixed* one (``"f0"``, ``"f10"``) into a lexicographic fallback — so
``"f10"`` sorted before ``"f2"``. They now all call this module, the same
consolidation :mod:`util.timepoint` already did for timepoints.

The grammar, tried in order:

1. A bare number — ``"1"``, ``"01"``, ``"1.5"``.
2. A prefixed ordinal, taking the *trailing* digit run — ``"f10"`` → 10.0,
   ``"F001"`` → 1.0, ``"fov3"`` → 3.0.

Anything else is ``None``: an opaque label the caller keeps as-is. Only sorting
depends on this — equality still compares the raw token strings, so this never
merges two distinct FOVs.

Stdlib-only, so ``process_microscopy`` and ``services/`` can both import it
without breaching the no-``well_viewer`` contract (ARCHITECTURE §6.2) — the same
reason ``util.timepoint`` and ``util.well_token`` live here. Listed in
``services.remote_runner._REQUIRED_REMOTE_FILES`` so a remote run receives it.
"""

from __future__ import annotations

import re
from typing import Any

_TRAILING_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)$")


def _token(value: Any) -> str:
    """Stringify *value* for parsing, treating only ``None`` as absent.

    The same rule, and the same reason, as :func:`util.timepoint._token`:
    ``str(value or "")`` reports a numeric ``0`` as no token at all, and FOV
    ``0`` is real — ``util.plate_grid.position_to_well_fov`` hands out 0-based
    FOV indices. With the falsy spelling, FOV 0 was indistinguishable from a
    missing FOV and sorted into the opaque block at the end
    (``sorted([1, 2, 10, 0], key=sort_key)`` gave ``[1, 2, 10, 0]``).
    """
    return "" if value is None else str(value)


def parse_fov(value: Any) -> float | None:
    """Parse an FOV token into a number, or ``None`` for an opaque label.

    Reads a bare number first, then falls back to the trailing digit run of a
    prefixed token (``"f10"`` → 10.0). ``None`` means "not a number I can read";
    callers keep such tokens as-is and sort them last.
    """
    s = _token(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    m = _TRAILING_NUM_RE.search(s)
    if m:
        return float(m.group(1))
    return None


def sort_key(value: Any) -> tuple[bool, float, str]:
    """Numeric sort key for an FOV token.

    ``(unparseable, number, token)`` — unparseable tokens sort last as a block
    and then alphabetically among themselves, so a dropdown built from mixed
    tokens is stable and puts the real FOVs first, in numeric order regardless of
    an ``f`` prefix.
    """
    token = _token(value).strip()
    n = parse_fov(token)
    return (n is None, n if n is not None else 0.0, token)
