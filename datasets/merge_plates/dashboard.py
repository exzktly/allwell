"""At-a-glance per-well merge-progress view for Merge Plates.

Same shape as ``datasets.concatenate.dashboard.ConcatDashboard``, with one
addition Concatenate doesn't need: dataset 1 and dataset 2 wells get
distinguishable colours so a well remap is visually confirmable on the
plate — the same idea ``widgets.plate_order_editor.PlateOrderEditor`` uses to
confirm an OME acquisition path.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

import theme
from widgets.plate_progress import PlateProgress, canon_well, status_dot

_WELL_STATES = ("pending", "merging", "merged")


class MergePlatesDashboard(PlateProgress):
    def __init__(self, parent: QWidget | None = None) -> None:
        self._wells: list[str] = []
        self._well_stage: dict[str, str] = {}
        self._well_origin: dict[str, str] = {}   # "ds1" | "ds2"
        self._outcome = ""
        super().__init__("Merge Progress", parent)
        self.setObjectName("MergePlatesDashboard")

    def reset(self) -> None:
        self._wells = []
        self._well_stage = {}
        self._well_origin = {}
        self._outcome = ""
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._plate.clearWellColors()
        self._plate.clearWellMarkers()
        self._plate.setEnabledWells(None)
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
        self.set_overall_range(len(wells))
        self.set_overall(0)
        self._refresh_rail()

    def set_well_stage(self, well_id: str, stage: str) -> None:
        w = canon_well(well_id)
        if not w or stage not in _WELL_STATES:
            return
        self._well_stage[w] = stage
        self._plate.setWellColor(w, self._well_color(w))
        self._plate.setWellMarker(w, "⚙" if stage == "merging" else None)
        self.set_overall(sum(1 for s in self._well_stage.values() if s == "merged"))
        self._refresh_rail()

    def set_done(self, summary: str) -> None:
        self._outcome = "done"
        self._headline.setText(summary or "Done")
        self._headline.setStyleSheet(f"color:{theme.Colors.success};")

    def set_failed(self, message: str) -> None:
        self._outcome = "failed"
        self._headline.setText(f"Failed — {message}" if message else "Failed")
        self._headline.setStyleSheet(f"color:{theme.Colors.danger};")

    def set_cancelled(self, message: str = "") -> None:
        self._outcome = "cancelled"
        self._headline.setText(f"Cancelled — {message}" if message else "Cancelled")
        self._headline.setStyleSheet(f"color:{theme.Colors.warn};")

    def _well_color(self, w: str) -> str | None:
        stage = self._well_stage.get(w)
        if stage == "merging":
            return theme.Colors.warn
        if stage == "merged":
            return theme.Colors.success if self._well_origin.get(w) == "ds1" else theme.Colors.accent
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

    def _build_legend(self) -> None:
        self._clear(self._legend_lay)
        head = QLabel("Wells", self._legend_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._legend_lay.addWidget(head)
        items = [
            (theme.Colors.text_muted, "pending"),
            (theme.Colors.warn, "merging"),
            (theme.Colors.success, "merged — dataset 1"),
            (theme.Colors.accent, "merged — dataset 2"),
        ]
        for color, txt in items:
            row_w = QWidget(self._legend_host)
            r = QHBoxLayout(row_w)
            r.setContentsMargins(0, 0, 0, 0)
            r.setSpacing(7)
            r.addWidget(status_dot(color))
            t = QLabel(txt, row_w)
            t.setStyleSheet(f"color:{theme.Colors.text_primary};")
            r.addWidget(t)
            r.addStretch(1)
            self._legend_lay.addWidget(row_w)
        self._legend_lay.addStretch(1)
