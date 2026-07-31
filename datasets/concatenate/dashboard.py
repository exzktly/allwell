"""Analyze-style status view for the Concatenate Datasets feature.

Built on :class:`~widgets.plate_progress.PlateProgress` — the same
header/headline/overall-bar/plate+legend chrome
:class:`~widgets.run_dashboard.RunDashboard` uses for Analyze — so the two
features' run-status views can't visually drift apart the way the plain-text
log they replaced did.

Two independent state axes, not one:

* **per well** — ``pending`` / ``merging`` / ``merged`` / ``warned`` (image
  data survived on one side of a well but no measurements CSV did — see
  ``apply.execute_plan``'s ``images_without_csv`` note). There is no per-well
  "failed" state: a well-loop exception aborts the whole run, so no single
  well can fail on its own.
* **per run** — a phase rail (Validate → Merge → Verify → Finalize) plus a
  terminal Done / Failed / Cancelled outcome painted on the headline. This
  matters because ``execute_plan``'s verification and cell-override carry-over
  run *after* every well has already gone green — without a run-level signal,
  a verification failure would leave the plate looking like a complete
  success while the run actually failed.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

import theme
from widgets.plate_progress import PlateProgress, canon_well, status_dot

_PHASES = (("validate", "Validate"), ("merge", "Merge"),
           ("verify", "Verify"), ("finalize", "Finalize"))
_PHASE_KEYS = tuple(key for key, _ in _PHASES)
_WELL_STATES = ("pending", "merging", "merged", "warned")


class ConcatDashboard(PlateProgress):
    """At-a-glance per-well + per-run merge status. Driven by the controller."""

    def __init__(self, parent: QWidget | None = None) -> None:
        self._wells: list[str] = []
        self._well_stage: dict[str, str] = {}
        self._phase = ""
        self._outcome = ""            # "" | "done" | "failed" | "cancelled"
        self._rail: dict[str, tuple] = {}
        super().__init__("Merge Progress", parent)
        self.setObjectName("ConcatDashboard")

    # ── host-facing API ─────────────────────────────────────────────────────
    def reset(self) -> None:
        self._wells = []
        self._well_stage = {}
        self._phase = ""
        self._outcome = ""
        self._bar.setRange(0, 100); self._bar.setValue(0)
        self._plate.clearWellColors()
        self._plate.clearWellMarkers()
        # Empty, not None -- None means "all 96 enabled", which an enabled-
        # but-uncolored well renders as a full plate of "pending" cells. That
        # falsely claims scope (a 14-well merge painted as 96 wells queued)
        # from the moment the page loads, and — since a real planning
        # failure can land here too, before set_wells() ever arrives — a
        # failed run's plate implied 96 wells were queued rather than none.
        self._plate.setEnabledWells([])
        self._build_rail(); self._build_legend()
        self._headline.setText("Idle")
        self._headline.setStyleSheet(f"color:{theme.Colors.text_primary};")
        self._eta.setText(""); self._status.setText("")

    def set_wells(self, well_ids) -> None:
        """The wells actually participating in this merge — only these are
        enabled on the plate, so an N-well merge on a 96-well plate doesn't
        render phantom "pending" cells for wells that aren't part of it."""
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
        self._plate.setWellMarker(w, "⚙" if stage == "merging" else None)
        self.set_overall(sum(1 for s in self._well_stage.values()
                              if s in ("merged", "warned")))
        self._refresh_rail()

    def set_phase(self, phase: str) -> None:
        if phase == self._phase:
            return
        self._phase = phase
        self._refresh_rail()

    def set_done(self, summary: str, *, tooltip: str | None = None) -> None:
        self._outcome = "done"
        self._headline.setText(summary or "Done")
        # The full text (often a full destination path baked into the
        # summary string) survives as a tooltip even when the caller has
        # shortened the displayed headline to a basename.
        self._headline.setToolTip(tooltip if tooltip is not None else (summary or "Done"))
        self._headline.setStyleSheet(f"color:{theme.Colors.success};")
        self._refresh_rail()

    def set_failed(self, message: str) -> None:
        self._outcome = "failed"
        self._headline.setText(f"Failed — {message}" if message else "Failed")
        self._headline.setStyleSheet(f"color:{theme.Colors.danger};")
        self._refresh_rail()

    def set_cancelled(self, message: str = "") -> None:
        self._outcome = "cancelled"
        self._headline.setText(f"Cancelled — {message}" if message else "Cancelled")
        self._headline.setStyleSheet(f"color:{theme.Colors.warn};")
        self._refresh_rail()

    # ── internals ───────────────────────────────────────────────────────────
    def _well_color(self, w: str) -> str | None:
        stage = self._well_stage.get(w)
        if stage == "merged":
            return theme.Colors.success
        if stage == "warned":
            return theme.Colors.accent
        if stage == "merging":
            return theme.Colors.warn
        if stage == "pending":
            return theme.Colors.text_muted
        return None

    def _well_tooltip(self, well_id: str) -> str:
        w = canon_well(well_id)
        stage = self._well_stage.get(w)
        if stage is None:
            return f"{w or well_id}: not part of this merge"
        label = {
            "pending": "pending",
            "merging": "merging",
            "merged": "merged",
            "warned": "merged — no measurements CSV on one side",
        }[stage]
        return f"{w or well_id}: {label}"

    def _build_rail(self) -> None:
        self._clear(self._rail_lay)
        self._rail = {}
        head = QLabel("Phase", self._rail_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._rail_lay.addWidget(head)
        for key, label in _PHASES:
            row_w = QWidget(self._rail_host)
            row = QHBoxLayout(row_w); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(7)
            dot = status_dot(theme.Colors.text_muted)
            row.addWidget(dot)
            nm = QLabel(label, row_w); nm.setFixedWidth(74)
            nm.setStyleSheet(f"color:{theme.Colors.text_primary};")
            row.addWidget(nm)
            bar = cnt = None
            if key == "merge":
                bar = QProgressBar(row_w)
                bar.setRange(0, max(1, len(self._wells))); bar.setValue(0)
                bar.setTextVisible(False); bar.setFixedSize(86, 6)
                row.addWidget(bar)
                cnt = QLabel("0/0", row_w); cnt.setFixedWidth(40)
                cnt.setStyleSheet(f"color:{theme.Colors.text_muted};")
                row.addWidget(cnt)
            row.addStretch(1)
            self._rail_lay.addWidget(row_w)
            self._rail[key] = (dot, nm, bar, cnt)
        self._rail_lay.addStretch(1)
        self._refresh_rail()

    def _refresh_rail(self) -> None:
        merged = sum(1 for s in self._well_stage.values() if s in ("merged", "warned"))
        total = len(self._wells)
        active_idx = _PHASE_KEYS.index(self._phase) if self._phase in _PHASE_KEYS else -1
        for idx, (key, _label) in enumerate(_PHASES):
            widgets = self._rail.get(key)
            if not widgets:
                continue
            dot, nm, bar, cnt = widgets
            active = key == self._phase and self._outcome not in ("failed", "cancelled")
            if self._outcome == "failed" and key == self._phase:
                color = theme.Colors.danger
            elif self._outcome == "done" or idx < active_idx:
                color = theme.Colors.success
            elif active:
                color = theme.Colors.warn
            else:
                color = theme.Colors.text_muted
            dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            nm.setStyleSheet(f"color:{theme.Colors.text_primary};" + ("font-weight:700;" if active else ""))
            if key == "merge" and bar is not None:
                bar.setRange(0, max(1, total)); bar.setValue(merged)
                cnt.setText(f"{merged}/{total}")

    def _build_legend(self) -> None:
        self._clear(self._legend_lay)
        head = QLabel("Wells", self._legend_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._legend_lay.addWidget(head)
        items = [
            (theme.Colors.text_muted, "pending"),
            (theme.Colors.warn, "merging"),
            (theme.Colors.success, "merged"),
            (theme.Colors.accent, "merged — no CSV on one side"),
        ]
        for color, txt in items:
            row_w = QWidget(self._legend_host)
            r = QHBoxLayout(row_w); r.setContentsMargins(0, 0, 0, 0); r.setSpacing(7)
            r.addWidget(status_dot(color))
            t = QLabel(txt, row_w); t.setStyleSheet(f"color:{theme.Colors.text_primary};")
            r.addWidget(t); r.addStretch(1)
            self._legend_lay.addWidget(row_w)
        self._legend_lay.addStretch(1)
