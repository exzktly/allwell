"""Home's plate map: what the last run actually produced, well by well.

Built on :class:`~widgets.well_plate_selector.WellPlateSelector` directly rather
than on :class:`~widgets.plate_progress.PlateProgress`. That base is a *run
dashboard* — headline, overall progress bar, ETA, done/failed/cancelled outcome
setters — and it makes its plate inert (``WA_TransparentForMouseEvents``, the
``status`` geometry role, which is passive selection). Home's plate is neither:
it has no run to report on, and clicking a well selects it. Undoing inertness on
a base whose whole point is inertness is fighting it, so this borrows that
class's four-line read-only idiom and stops there.

**No well is ever painted in the accent colours.** ``accent`` already means
"anomalous well" on the Concatenate and Subset dashboards, and ``accent2`` means
"belongs to the other of two normal groups" on Merge Plates. A landing page that
spent either on an ordinary state would contradict a convention the rest of the
app has already taught. Colour here is spent on exceptions and the normal states
separate by luminance — which is also why *partial* is the one state given a
shape as well as a hue: it is the state this page exists to make visible, and an
arc survives a small cell, colour blindness and a fast skim.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

import theme
from widgets.plate_geometry import apply_plate_geometry
from widgets.well_plate_selector import WellPlateSelector

from .collect import (
    CELL_ABSENT,
    CELL_COMPLETE,
    CELL_FAILED,
    CELL_INPUT_ONLY,
    CELL_NO_MEASUREMENTS,
    CELL_PARTIAL,
    CELL_UNCHECKED,
)

#: One colour per painted state. Read through ``theme.Colors`` so a theme swap
#: carries the plate with it, and deliberately drawn from the status palette:
#: these *are* status colours, unlike the well-identity palette every plot uses.
STATE_COLORS = {
    CELL_COMPLETE: theme.Colors.success,
    CELL_PARTIAL: theme.Colors.warn,
    CELL_FAILED: theme.Colors.danger,
    CELL_INPUT_ONLY: theme.Colors.panel_elevated,
    CELL_NO_MEASUREMENTS: theme.Colors.threshold,
    CELL_UNCHECKED: theme.Colors.border,
    CELL_ABSENT: theme.Colors.surface,
}

#: States drawn dimmed rather than lit — nothing has been produced for them, so
#: they should recede rather than compete with the wells that carry data.
_MUTED_STATES = (CELL_INPUT_ONLY, CELL_UNCHECKED, CELL_ABSENT)

STATE_LABELS = {
    CELL_COMPLETE: "Complete",
    CELL_PARTIAL: "Partial",
    CELL_FAILED: "Failures recorded",
    CELL_INPUT_ONLY: "Staged, never processed",
    CELL_NO_MEASUREMENTS: "Images, no measurements",
    CELL_UNCHECKED: "Not checked",
    CELL_ABSENT: "Absent",
}


def build_home_plate(parent: QWidget) -> WellPlateSelector:
    """A selectable plate sized like every other in the app.

    ``role="picker"`` rather than ``"status"``: the wells are clickable, which
    is what separates this from a dashboard's read-only map. The built-in
    All / Invert / Clear row comes with that role and is wanted — it is the same
    gesture the rail's own picker offers.
    """
    plate = WellPlateSelector(parent)
    plate.setObjectName("HomePlate")
    plate.setColoredWellsLit(True)
    apply_plate_geometry(plate, role="picker")
    return plate


def paint_plate(plate: WellPlateSelector, cells: dict, fractions: dict) -> None:
    """Paint one state per well, plus a coverage arc for the part-done ones.

    A well the record does not mention is left neutral rather than being given
    a colour that would assert something about it.
    """
    plate.clearWellColors()
    plate.clearWellFractions()
    for well, state in (cells or {}).items():
        colour = STATE_COLORS.get(state)
        if colour is None:
            continue
        prefix = "muted:" if state in _MUTED_STATES else "color:"
        plate.setWellState(well, f"{prefix}{colour}")
    for well, fraction in (fractions or {}).items():
        plate.setWellFraction(well, fraction)


def well_tooltip(well: str, cells: dict, record_wells: dict) -> str:
    """What a well says when the pointer rests on it.

    Reads the numbers the record already holds rather than deriving anything —
    the tooltip and the colour must never be able to disagree.
    """
    state = (cells or {}).get(well)
    if state is None:
        return f"{well} · not in this dataset"
    label = STATE_LABELS.get(state, state)
    rec = (record_wells or {}).get(well)
    if not isinstance(rec, dict):
        return f"{well} · {label.lower()}"
    intent = int(rec.get("intent") or 0)
    covered = int(rec.get("covered_any") or 0)
    bits = [f"{well} · {label.lower()}"]
    if intent:
        bits.append(f"{covered} of {intent} field group(s)")
    if int(rec.get("covered_csv") or 0) != covered:
        bits.append(f"{int(rec.get('covered_csv') or 0)} in the measurements CSV")
    if rec.get("failed"):
        bits.append(f"{len(rec['failed'])} failed group(s)")
    return " · ".join(bits)
