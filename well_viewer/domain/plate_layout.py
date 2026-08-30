"""96-well plate row/column constants and shared well palette.

Extracted from ``well_viewer.runtime_app`` so that controllers and views can
reach plate geometry without importing the GUI monolith.
"""

from __future__ import annotations

import theme

PLATE_ROWS = list("ABCDEFGH")
PLATE_COLS = [f"{c:02d}" for c in range(1, 13)]  # "01" … "12"

# One entry per 96-well plate position (see the length note on
# ``theme.WELL_COLORS_TUPLE``): the per-well rank colour is
# ``WELL_COLORS[well_rank(token) % len(WELL_COLORS)]`` and ranks step by 12 down
# a column, so a shorter (submultiple-of-12) palette would repeat colours down
# each column. 96 distinct colours keep every well unique.
WELL_COLORS = list(theme.WELL_COLORS_TUPLE)


#: The two axes a fill can walk. ``"row"`` reads A01, A02 … A12, B01 …;
#: ``"col"`` reads A01, B01 … H01, A02 …
FILL_ALONG_ROWS = "row"
FILL_ALONG_COLS = "col"
FILL_AXES = (FILL_ALONG_ROWS, FILL_ALONG_COLS)


def wells_in_fill_order(wells, axis: str = FILL_ALONG_ROWS) -> list[str]:
    """*wells*, ordered for a fill along *axis*.

    The half of a fill that knows about plate geometry, kept here rather than in
    ``domain/sample_variables`` — that module orders nothing and cycles values
    over whatever sequence it is handed, so "along columns" has exactly one
    definition and it is this one.

    **Only the wells given are ordered**, and gaps are closed rather than
    counted. A plate whose column 6 was never imaged is a plate of eleven
    columns for this purpose: a fill along rows that skipped a slot for the
    missing one would put the value after it on the wrong dose, and the user
    cannot see an absent well to notice. So the sequence walks the wells that
    exist, in plate order, and the cycle is over that.

    A token that does not parse as a well sorts last, in its original order,
    rather than being dropped — a fill that silently ignored a well would leave
    it unassigned with nothing saying which one.
    """
    ordered = sorted(
        (w for w in wells if _rc(w) is not None),
        key=(lambda w: _rc(w)) if axis != FILL_ALONG_COLS
        else (lambda w: (_rc(w)[1], _rc(w)[0])),
    )
    unparsed = [w for w in wells if _rc(w) is None]
    return [*ordered, *unparsed]


def _rc(token: str):
    """``(row_letter, column)`` for *token*, or ``None`` when it is not a well."""
    from well_viewer.domain.viewer_state import parse_rc

    row, col = parse_rc(str(token))
    return None if row is None else (row, col)
