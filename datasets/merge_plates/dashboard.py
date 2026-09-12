"""At-a-glance per-well merge-progress view for Merge Plates.

Same shape as ``datasets.concatenate.dashboard.ConcatDashboard``, with one
addition Concatenate doesn't need: dataset 1 and dataset 2 wells get
distinguishable colours so a well remap is visually confirmable on the
plate — the same idea ``widgets.plate_order_editor.PlateOrderEditor`` uses to
confirm an OME acquisition path.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ui import theme
from widgets.plate_progress import PlateProgress, canon_well, status_dot

_WELL_STATES = ("pending", "merging", "merged")


class MergePlatesDashboard(PlateProgress):
    def __init__(self, parent: QWidget | None = None) -> None:
        self._wells: list[str] = []
        self._well_stage: dict[str, str] = {}
        self._well_origin: dict[str, str] = {}   # "ds1" | "ds2"
        super().__init__("Merge Progress", parent)
        self.setObjectName("MergePlatesDashboard")

    def reset(self) -> None:
        self._wells = []
        self._well_stage = {}
        self._well_origin = {}
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

    def set_wells(self, ds1_wells, ds2_wells) -> None:
        ds1 = {canon_well(w) for w in ds1_wells} - {""}
        ds2 = {canon_well(w) for w in ds2_wells} - {""}
        wells = sorted(ds1 | ds2)
        self._wells = wells
        self._well_stage = dict.fromkeys(wells, "pending")
        self._well_origin = {w: ("ds1" if w in ds1 else "ds2") for w in wells}
        self._plate.setEnabledWells(wells)
        for w in wells:
            self._plate.setWellColor(w, self._well_color(w))
            self._plate.setWellMarker(w, self._well_marker(w))
        self.set_overall_range(len(wells))
        self.set_overall(0)
        self._refresh_rail()

    def set_well_stage(self, well_id: str, stage: str) -> None:
        w = canon_well(well_id)
        if not w or stage not in _WELL_STATES:
            return
        self._well_stage[w] = stage
        self._plate.setWellColor(w, self._well_color(w))
        self._plate.setWellMarker(w, self._well_marker(w))
        self.set_overall(sum(1 for s in self._well_stage.values() if s == "merged"))
        self._refresh_rail()

    def _well_marker(self, w: str) -> str | None:
        # A *merging* well carries no in-flight glyph of its own: it is drawn
        # as a progress dial, and a glyph would sit in the middle of exactly
        # the area the dial fills, while the fill and the colour already say
        # the well is being worked on. Every plate map in the app follows that
        # rule. A dataset-2 well keeps its "2" marker throughout, merging
        # included -- that one is origin, not progress, and it is the only
        # thing telling the two datasets apart once both are "merged"
        # green/lilac dots on a small plate; colour alone (success vs.
        # accent2) is unreadable to anyone who can't distinguish the two hues.
        if self._well_origin.get(w) == "ds2":
            return "2"
        # A dataset-1 well's own terminal "merged" state -- ds2's origin
        # marker above already doubles as its own "not just colour" signal,
        # so this only fires for the wells that would otherwise have none.
        if self._well_stage.get(w) == "merged":
            return "✓"
        return None

    def _well_color(self, w: str) -> str | None:
        stage = self._well_stage.get(w)
        if stage == "merging":
            return theme.Colors.warn
        if stage == "merged":
            # accent2, not accent -- accent already means "anomaly" on two
            # other dashboards (Concatenate's merged-with-no-CSV, Subset's
            # no-rows-matched), and a well merely belonging to "dataset 2" is
            # not one (see theme.Colors.accent2's own docstring).
            return theme.Colors.success if self._well_origin.get(w) == "ds1" else theme.Colors.accent2
        if stage == "pending":
            return theme.Colors.text_muted
        return None

    def _well_tooltip(self, well_id: str) -> str:
        w = canon_well(well_id)
        stage = self._well_stage.get(w)
        origin = self._well_origin.get(w)
        if stage is None:
            return f"{w or well_id}: not part of this merge"
        origin_label = "dataset 1" if origin == "ds1" else "dataset 2"
        return f"{w}: {stage} ({origin_label})"

    def _build_rail(self) -> None:
        self._clear(self._rail_lay)
        head = QLabel("Wells", self._rail_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._rail_lay.addWidget(head)
        merged = sum(1 for s in self._well_stage.values() if s == "merged")
        total = len(self._wells)
        row_w = QWidget(self._rail_host)
        row = QHBoxLayout(row_w)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        row.addWidget(status_dot(theme.Colors.success))
        nm = QLabel(f"Merged: {merged}/{total}", row_w)
        nm.setStyleSheet(f"color:{theme.Colors.text_primary};")
        row.addWidget(nm)
        row.addStretch(1)
        self._rail_lay.addWidget(row_w)
        self._rail_lay.addStretch(1)

    def _refresh_rail(self) -> None:
        self._build_rail()

    def _legend_items(self):
        return "Wells", [
            (theme.Colors.text_muted, "pending"),
            (theme.Colors.warn, "merging"),
            (theme.Colors.success, "merged — dataset 1"),
            (theme.Colors.accent2, "merged — dataset 2 (marked “2”)"),
        ]
