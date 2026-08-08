"""At-a-glance per-well progress view for Import Loose Folder.

Same shape as ``datasets.subset.dashboard.SubsetDashboard`` — a
:class:`~widgets.plate_progress.PlateProgress` subclass with its own well
states and a plain well-count rail (an import run has one phase, so no phase
sequence). Only two live states: a well is either still ``pending``,
mid-conversion (``writing`` — being written to a zip for new-destination mode,
or grouped-and-awaiting-zip for in-place mode), or ``done``. There is no
per-well failure state here either, for the same reason ``ConcatDashboard``
has none: a well-loop exception aborts the whole run rather than failing one
well in isolation.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

import theme
from widgets.plate_progress import PlateProgress, canon_well, status_dot

_WELL_STATES = ("pending", "writing", "done")


class ImportLooseDashboard(PlateProgress):
    def __init__(self, parent: QWidget | None = None) -> None:
        self._wells: list[str] = []
        self._well_stage: dict[str, str] = {}
        super().__init__("Zip Progress", parent)
        self.setObjectName("ImportLooseDashboard")

    def reset(self) -> None:
        self._wells = []
        self._well_stage = {}
        self.clear_outcome()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._plate.clearWellColors()
        self._plate.clearWellMarkers()
        # Empty, not None -- see ConcatDashboard.reset for why a full 96-well
        # "enabled but uncolored" plate misstates scope before set_wells()
        # (or a failure that lands before it ever arrives) has a real answer.
        self._plate.setEnabledWells([])
        self._build_rail()
        self._build_legend()
        self._headline.setText("Idle")
        self._headline.setStyleSheet(f"color:{theme.Colors.text_primary};")
        self._eta.setText("")
        self._status.setText("")

    def set_wells(self, well_ids) -> None:
        wells = sorted({canon_well(w) for w in well_ids} - {""})
        self._wells = wells
        self._well_stage = dict.fromkeys(wells, "pending")
        self._plate.setEnabledWells(wells)
        for w in wells:
            self._plate.setWellColor(w, self._well_color(w))
        self.set_overall_range(len(wells))
        self.set_overall(0)
        self._refresh_rail()

    def set_well_stage(self, well_id: str, stage: str) -> None:
        w = canon_well(well_id)
        if not w or stage not in _WELL_STATES:
            return
        self._well_stage[w] = stage
        self._plate.setWellColor(w, self._well_color(w))
        # Terminal states carry a marker too, not just colour (accessibility
        # — colour alone is unreadable to anyone who can't distinguish
        # success-green from text-muted grey). A well that is *writing*
        # carries none: it is drawn as a progress dial, and a glyph would sit
        # in the middle of exactly the area the dial fills, while the fill and
        # the colour already say the well is being worked on. Every plate map
        # in the app follows that rule.
        marker = {"done": "✓"}.get(stage)
        self._plate.setWellMarker(w, marker)
        self.set_overall(sum(1 for s in self._well_stage.values() if s == "done"))
        self._refresh_rail()

    def _well_color(self, w: str) -> str | None:
        stage = self._well_stage.get(w)
        if stage == "done":
            return theme.Colors.success
        if stage == "writing":
            return theme.Colors.warn
        if stage == "pending":
            return theme.Colors.text_muted
        return None

    def _well_tooltip(self, well_id: str) -> str:
        w = canon_well(well_id)
        stage = self._well_stage.get(w)
        if stage is None:
            return f"{w or well_id}: not part of this conversion"
        label = {"pending": "pending", "writing": "zipping", "done": "zipped"}[stage]
        return f"{w}: {label}"

    def _build_rail(self) -> None:
        self._clear(self._rail_lay)
        head = QLabel("Wells", self._rail_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._rail_lay.addWidget(head)
        for state, label in (("pending", "Pending"), ("writing", "Zipping"), ("done", "Zipped")):
            n = sum(1 for s in self._well_stage.values() if s == state)
            row_w = QWidget(self._rail_host)
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(7)
            row.addWidget(status_dot(self._state_color(state)))
            nm = QLabel(f"{label}: {n}", row_w)
            nm.setStyleSheet(f"color:{theme.Colors.text_primary};")
            row.addWidget(nm)
            row.addStretch(1)
            self._rail_lay.addWidget(row_w)
        self._rail_lay.addStretch(1)

    def _refresh_rail(self) -> None:
        self._build_rail()

    def _state_color(self, state: str) -> str:
        return {
            "pending": theme.Colors.text_muted,
            "writing": theme.Colors.warn,
            "done": theme.Colors.success,
        }[state]

    def _build_legend(self) -> None:
        self._clear(self._legend_lay)
        head = QLabel("Legend", self._legend_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._legend_lay.addWidget(head)
        for state, label in (("pending", "pending"), ("writing", "zipping"), ("done", "zipped")):
            row_w = QWidget(self._legend_host)
            r = QHBoxLayout(row_w)
            r.setContentsMargins(0, 0, 0, 0)
            r.setSpacing(7)
            r.addWidget(status_dot(self._state_color(state)))
            t = QLabel(label, row_w)
            t.setStyleSheet(f"color:{theme.Colors.text_primary};")
            r.addWidget(t)
            r.addStretch(1)
            self._legend_lay.addWidget(row_w)
        self._legend_lay.addStretch(1)
