"""At-a-glance progress view for Subset / Split Dataset.

Unlike ``datasets.concatenate.dashboard.ConcatDashboard`` and the other
Datasets dashboards, this one carries **no plate map of its own**
(``PlateProgress(show_plate=False)``) — Subset's page already has an
interactive :class:`~widgets.well_plate_selector.WellPlateSelector` above the
Run button for choosing wells, so progress is overlaid directly onto that
same widget (:mod:`~datasets.subset.controller`) instead of drawing a second,
redundant plate here. It also carries **no separate legend column**
(``show_legend=False``) — the counts rail's own rows already pair a coloured
dot with a state label, so a second column repeating the same four dot+label
pairs added nothing a glance at the rail didn't already say.

``well_color``/``well_tooltip_label`` are exported so the controller colours
the *input* plate with exactly the same mapping this rail uses, rather than
each keeping its own copy that could drift.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ui import theme
from widgets.plate_progress import PlateProgress, canon_well, status_dot

WELL_STATES = ("pending", "copying", "done", "empty")


def well_color(stage: str) -> str | None:
    if stage == "done":
        return theme.Colors.success
    if stage == "empty":
        return theme.Colors.accent
    if stage == "copying":
        return theme.Colors.warn
    if stage == "pending":
        return theme.Colors.text_muted
    return None


def well_tooltip_label(stage: str) -> str:
    return {
        "pending": "pending", "copying": "copying",
        "done": "copied", "empty": "no rows matched the filter",
    }.get(stage, "")


class SubsetDashboard(PlateProgress):
    """No plate map (see the module docstring) *and* no separate legend
    column either — the rail's own rows already pair a coloured dot with a
    state label, so a second column repeating the same four dot+label pairs
    (just without the live count) added nothing but width. One column, both
    pieces of information."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._wells: list[str] = []
        self._well_stage: dict[str, str] = {}
        super().__init__("Subset Progress", parent, show_plate=False, show_legend=False)
        self.setObjectName("SubsetDashboard")

    def reset(self) -> None:
        self._wells = []
        self._well_stage = {}
        self.clear_outcome()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._build_rail()
        self._headline.setText("Idle")
        self._headline.setStyleSheet(f"color:{theme.Colors.text_primary};")
        self._eta.setText("")
        self._status.setText("")

    def set_wells(self, well_ids) -> None:
        wells = sorted({canon_well(w) for w in well_ids} - {""})
        self._wells = wells
        self._well_stage = dict.fromkeys(wells, "pending")
        self.set_overall_range(len(wells))
        self.set_overall(0)
        self._refresh_rail()

    def set_well_stage(self, well_id: str, stage: str) -> None:
        w = canon_well(well_id)
        if not w or stage not in WELL_STATES:
            return
        self._well_stage[w] = stage
        self.set_overall(sum(1 for s in self._well_stage.values() if s in ("done", "empty")))
        self._refresh_rail()

    def _build_rail(self) -> None:
        self._clear(self._rail_lay)
        head = QLabel("Wells", self._rail_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._rail_lay.addWidget(head)
        for state, label in (("pending", "Pending"), ("copying", "Copying"),
                              ("done", "Done"), ("empty", "No match")):
            n = sum(1 for s in self._well_stage.values() if s == state)
            row_w = QWidget(self._rail_host)
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(7)
            row.addWidget(status_dot(well_color(state) or theme.Colors.text_muted))
            nm = QLabel(f"{label}: {n}", row_w)
            nm.setStyleSheet(f"color:{theme.Colors.text_primary};")
            row.addWidget(nm)
            row.addStretch(1)
            self._rail_lay.addWidget(row_w)
        self._rail_lay.addStretch(1)

    def _refresh_rail(self) -> None:
        self._build_rail()
