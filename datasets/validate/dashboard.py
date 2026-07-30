"""At-a-glance per-well dataset-health view for Validate & Repair.

Built on :class:`~widgets.plate_progress.PlateProgress`, the same base
:class:`~datasets.concatenate.dashboard.ConcatDashboard` and
:class:`~widgets.run_dashboard.RunDashboard` use, so every run-status view in
the app shares one visual language. A scan is single-shot (no phases), so the
rail is just a plain well-count summary rather than a phase sequence.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

import theme
from widgets.plate_progress import PlateProgress, canon_well, status_dot

_WELL_STATES = ("complete", "partial", "missing", "no_output")


class ValidateDashboard(PlateProgress):
    """Per-well dataset-health plate. Driven by the controller."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._wells: list[str] = []
        self._well_state: dict[str, str] = {}
        self._well_missing_count: dict[str, int] = {}
        super().__init__("Dataset Health", parent)
        self.setObjectName("ValidateDashboard")

    # ── host-facing API ──────────────────────────────────────────────────────
    def reset(self) -> None:
        self._wells = []
        self._well_state = {}
        self._well_missing_count = {}
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

    def set_wells(self, well_ids) -> None:
        wells = sorted({canon_well(w) for w in well_ids} - {""})
        self._wells = wells
        self._well_state = dict.fromkeys(wells, "no_output")
        self._plate.setEnabledWells(wells)
        for w in wells:
            self._plate.setWellColor(w, self._well_color(w))
        self.set_overall_range(len(wells))
        self.set_overall(0)
        self._refresh_rail()

    def set_well_state(self, well_id: str, state: str, *, missing_count: int = 0) -> None:
        w = canon_well(well_id)
        if not w or state not in _WELL_STATES:
            return
        self._well_state[w] = state
        self._well_missing_count[w] = missing_count
        self._plate.setWellColor(w, self._well_color(w))
        self._plate.setWellMarker(w, "!" if state in ("partial", "missing") else None)
        self.set_overall(sum(1 for s in self._well_state.values() if s == "complete"))
        self._refresh_rail()

    def set_done(self, summary: str) -> None:
        self._headline.setText(summary or "Done")
        self._headline.setStyleSheet(f"color:{theme.Colors.success};")

    def set_failed(self, message: str) -> None:
        self._headline.setText(f"Failed — {message}" if message else "Failed")
        self._headline.setStyleSheet(f"color:{theme.Colors.danger};")

    # ── internals ────────────────────────────────────────────────────────────
    def _well_color(self, w: str) -> str | None:
        state = self._well_state.get(w)
        if state == "complete":
            return theme.Colors.success
        if state == "partial":
            return theme.Colors.warn
        if state == "missing":
            return theme.Colors.danger
        if state == "no_output":
            return theme.Colors.text_muted
        return None

    def _well_tooltip(self, well_id: str) -> str:
        w = canon_well(well_id)
        state = self._well_state.get(w)
        if state is None:
            return f"{w or well_id}: not part of this dataset"
        if state == "partial":
            n = self._well_missing_count.get(w, 0)
            return f"{w}: {n} (fov, timepoint) pair(s) missing"
        label = {
            "complete": "complete",
            "missing": "no measurements recorded",
            "no_output": "no output yet",
        }[state]
        return f"{w}: {label}"

    def _build_rail(self) -> None:
        self._clear(self._rail_lay)
        head = QLabel("Wells", self._rail_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._rail_lay.addWidget(head)
        counts = {state: sum(1 for s in self._well_state.values() if s == state) for state in _WELL_STATES}
        for state, label in (
            ("complete", "Complete"), ("partial", "Partial"),
            ("missing", "No measurements"), ("no_output", "No output"),
        ):
            row_w = QWidget(self._rail_host)
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(7)
            row.addWidget(status_dot(self._state_color(state)))
            nm = QLabel(f"{label}: {counts[state]}", row_w)
            nm.setStyleSheet(f"color:{theme.Colors.text_primary};")
            row.addWidget(nm)
            row.addStretch(1)
            self._rail_lay.addWidget(row_w)
        self._rail_lay.addStretch(1)

    def _refresh_rail(self) -> None:
        self._build_rail()

    def _state_color(self, state: str) -> str:
        return {
            "complete": theme.Colors.success,
            "partial": theme.Colors.warn,
            "missing": theme.Colors.danger,
            "no_output": theme.Colors.text_muted,
        }[state]

    def _build_legend(self) -> None:
        self._clear(self._legend_lay)
        head = QLabel("Legend", self._legend_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._legend_lay.addWidget(head)
        for state, label in (
            ("complete", "complete"), ("partial", "partial"),
            ("missing", "no measurements"), ("no_output", "no output"),
        ):
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
