"""What a nanowell's fluorescence numbers were measured above, and how a label says so.

Each well of a run records it (``pipeline.nanowell_run.NanowellSettings.fluor_background``, chosen in Analyze as
``--nanowell_fluor_background``), and the site table carries the level itself per channel (``<channel>_background``):

``floor``       the bare nanowell floor of the same tile of the same well (the default)
``empty_well``  the named empty reference well's floor at the same timepoint
``none``        a run from before the column existed: its nanowell fluorescence is above the tile's floor, as
                ``floor``, but its cell fluorescence is the cells' raw mean, the background still in it

Both fluorescence metrics take the same level off: nanowell fluorescence in the pipeline, cell fluorescence (the ``brightness`` metric) in
``series_compute.site_metrics`` (the mean of the cells' means less the site's ``<channel>_background``, after Cell
Gating has chosen the cells). A label names which, because the two metrics are otherwise the same kind of number in
the same channel and a reader cannot tell from the values whose light was taken away.
"""

from __future__ import annotations

FLOOR, EMPTY_WELL, NONE = "floor", "empty_well", "none"
#: The metrics a background applies to: the cells' brightness and the nanowell's fluorescence, and their fold changes.
BRIGHTNESS_METRICS = frozenset({"brightness", "brightness_fc"})
SIGNAL_METRICS = frozenset({"signal", "signal_fc"})

_CLAUSES = {NONE: "background included", FLOOR: "own-well background subtracted",
            EMPTY_WELL: "empty-well background subtracted"}
MIXED_CLAUSE = "background differs between wells"


def well_mode(table_columns, settings) -> str:
    """The mode of one well, from its site table's columns and its grid record's ``settings`` block."""
    if not any(str(c).endswith("_background") for c in table_columns):
        return NONE
    chosen = (settings or {}).get("fluor_background") if isinstance(settings, dict) else None
    return EMPTY_WELL if chosen == EMPTY_WELL else FLOOR


def effective(metric: str, mode: str) -> str:
    """What *metric*'s numbers were measured above under *mode*: a run without the column took the tile's floor off
    its nanowell fluorescence all the same, and nothing off its cell fluorescence."""
    if metric in SIGNAL_METRICS and mode == NONE:
        return FLOOR
    return mode


def clause(metric: str, modes) -> str:
    """The words a label of *metric* carries for wells measured under *modes* (one per well); ``""`` for a metric
    no background applies to."""
    if metric not in BRIGHTNESS_METRICS | SIGNAL_METRICS:
        return ""
    found = {effective(metric, m) for m in modes} or {effective(metric, FLOOR)}
    return _CLAUSES[found.pop()] if len(found) == 1 else MIXED_CLAUSE


_SHORT = {NONE: "with background", FLOOR: "minus own well", EMPTY_WELL: "minus empty well"}


def short_clause(metric: str, modes) -> str:
    """The same as :func:`clause`, short enough for the second line of a site detail row's label."""
    found = {effective(metric, m) for m in modes} or {effective(metric, FLOOR)}
    return _SHORT[found.pop()] if len(found) == 1 else "mixed background"


def describe(modes, empty_well: str | None = None) -> str:
    """One sentence for a figure's notes when the wells' backgrounds are not all the same, or ``""``."""
    found = set(modes)
    if len(found) < 2:
        return ""
    parts = []
    if NONE in found:
        parts.append("some wells come from a run whose cell fluorescence still includes the background (re-run "
                     "them to subtract it)")
    if FLOOR in found and EMPTY_WELL in found:
        parts.append("some wells were measured above their own floor and others above the empty well"
                     + (f" {empty_well}" if empty_well else ""))
    return "Fluorescence backgrounds differ between the wells shown: " + "; ".join(parts) + "." if parts else ""
