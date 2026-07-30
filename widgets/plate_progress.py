"""Shared chrome for a live per-well run-status dashboard.

Holds what :class:`~widgets.run_dashboard.RunDashboard` (Analyze) and
:class:`~datasets.concatenate.dashboard.ConcatDashboard` (Concatenate) have in
common: a header (title/status/eta/collapse toggle), a headline label, one
overall completion bar, and a 3-column body — a subclass-owned left "rail"
slot, the 8x12 well plate map (+ optional per-well sub-progress rails beneath
it), and a legend. Extracted so the two dashboards' layout/plate/legend wiring
can't drift apart from each other the way it had before this module existed.

A subclass sets its own instance state, then calls ``super().__init__(title)``;
the base builds every widget above and finishes by invoking the subclass's own
``_build_rail()`` / ``_build_legend()`` (which must exist by then) to populate
the left slot and the legend — the same "build state, then build chrome, then
populate" order :class:`RunDashboard` used before this extraction.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from widgets.well_plate_selector import WellPlateSelector


def canon_well(well_id: str) -> str:
    """Normalise a well token to ``A01`` form; '' if it isn't a well."""
    m = re.match(r"^([A-Ha-h])(\d{1,2})$", str(well_id).strip())
    return f"{m.group(1).upper()}{int(m.group(2)):02d}" if m else ""


def status_dot(color: str, d: int = 11) -> QLabel:
    lbl = QLabel()
    lbl.setFixedSize(d, d)
    lbl.setStyleSheet(f"background:{color}; border-radius:{d // 2}px;")
    return lbl


class PlateProgress(QWidget):
    """Header/headline/overall-bar/plate+legend chrome, shared by RunDashboard
    and ConcatDashboard. A subclass builds its own left-slot content (a stage
    or phase rail) into ``self._rail_host``/``self._rail_lay`` via its own
    ``_build_rail``/``_refresh_rail``/``_build_legend``, and provides
    ``_well_tooltip`` for the plate's hover text."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlateProgress")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        # header: title + status + collapse toggle
        hdr = QHBoxLayout(); hdr.setSpacing(8)
        self._title_label = QLabel(title, self)
        hdr.addWidget(self._title_label)
        self._status = QLabel("", self); self._status.setObjectName("Muted")
        hdr.addWidget(self._status)
        hdr.addStretch(1)
        self._eta = QLabel("", self); self._eta.setObjectName("Muted")
        hdr.addWidget(self._eta)
        self._toggle = QToolButton(self)
        self._toggle.setText("▾"); self._toggle.setAutoRaise(True)
        self._toggle.setCheckable(True); self._toggle.setChecked(True)
        self._toggle.setToolTip("Collapse / expand the run progress view")
        self._toggle.clicked.connect(self._on_toggle)
        hdr.addWidget(self._toggle)
        root.addLayout(hdr)

        self._headline = QLabel("Idle", self)
        root.addWidget(self._headline)

        # The single authoritative overall-completion bar for the whole run.
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100); self._bar.setValue(0)
        self._bar.setTextVisible(False)
        root.addWidget(self._bar)

        # body: rail | plate (+ per-well sub-progress rails) | legend
        self._body = QWidget(self)
        body = QHBoxLayout(self._body)
        body.setContentsMargins(0, 4, 0, 0); body.setSpacing(14)

        self._rail_host = QWidget(self._body)
        self._rail_lay = QVBoxLayout(self._rail_host)
        self._rail_lay.setContentsMargins(0, 0, 0, 0); self._rail_lay.setSpacing(8)
        self._rail_host.setFixedWidth(238)
        body.addWidget(self._rail_host)

        d1 = QFrame(self._body); d1.setFrameShape(QFrame.VLine); d1.setObjectName("VRule")
        body.addWidget(d1)

        self._center_host = QWidget(self._body)
        center = QVBoxLayout(self._center_host)
        center.setContentsMargins(0, 0, 0, 0); center.setSpacing(6)

        self._plate = WellPlateSelector(self._center_host)
        self._plate.setSelectionMode("passive")
        self._plate.setDragSelectEnabled(False)
        self._plate.setRowColumnSelectable(False)
        self._plate.setActionsVisible(False)
        # Paint coloured wells in the raised/lit style (not the dim recessed
        # tint) so run states read clearly on the status map.
        self._plate.setColoredWellsLit(True)
        self._plate.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._plate.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._plate.setMaximumHeight(210)
        # WellPlateSelector paints a panel background + border across its
        # *entire* allocated box (well_plate_selector.py's #WellPlateSelector
        # QSS rule), independent of how much of that box the grid itself
        # actually draws into — _metrics() sizes the grid to
        # min(avail_w/12, avail_h/8), so once maxHeight above caps the grid's
        # cell size, any extra width the layout hands this widget (the
        # centre column is wide, shared with a fixed-width rail/legend) does
        # NOT grow the grid — it only stretches the panel's own background
        # into a big, mostly-empty grey card with a small grid centred
        # inside it. Cap the width too so the card hugs its content, matching
        # the height-bound cell size's own natural width (12 cols at the
        # same cell size the 210px height already produces).
        self._plate.setMaximumWidth(330)
        self._plate.setWellTooltipProvider(self._well_tooltip)
        center.addWidget(self._plate, 0, Qt.AlignLeft)

        # Per-well sub-progress rails (RunDashboard: FOV·timepoint progress;
        # unused by ConcatDashboard, which has no per-well sub-phases) —
        # height-capped + scrollable so many concurrent wells never grow the
        # dashboard and crush the log below.
        self._fov_host = QWidget()
        self._fov_lay = QVBoxLayout(self._fov_host)
        self._fov_lay.setContentsMargins(0, 0, 0, 0); self._fov_lay.setSpacing(3)
        self._fov_scroll = QScrollArea(self._center_host)
        self._fov_scroll.setWidget(self._fov_host)
        self._fov_scroll.setWidgetResizable(True)
        self._fov_scroll.setFrameShape(QFrame.NoFrame)
        self._fov_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._fov_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._fov_scroll.setMaximumHeight(96)
        self._fov_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._fov_host.setStyleSheet("background: transparent;")
        self._fov_scroll.setVisible(False)
        center.addWidget(self._fov_scroll)
        center.addStretch(1)
        body.addWidget(self._center_host, 1)

        d2 = QFrame(self._body); d2.setFrameShape(QFrame.VLine); d2.setObjectName("VRule")
        body.addWidget(d2)

        self._legend_host = QWidget(self._body)
        self._legend_lay = QVBoxLayout(self._legend_host)
        self._legend_lay.setContentsMargins(0, 0, 0, 0); self._legend_lay.setSpacing(8)
        self._legend_host.setFixedWidth(140)
        body.addWidget(self._legend_host)

        root.addWidget(self._body)
        self._build_rail(); self._build_legend()

    # ── shared host-facing API ──────────────────────────────────────────────
    def set_headline(self, text: str) -> None:
        self._headline.setText(text or "")

    def set_eta(self, text: str) -> None:
        self._eta.setText(text or "")

    def set_status(self, text: str) -> None:
        self._status.setText(text or "")

    def set_overall_range(self, total: int) -> None:
        """Set the denominator of the overall-completion bar."""
        self._bar.setRange(0, max(1, int(total)))

    def set_overall(self, value: int) -> None:
        """Set the numerator of the overall-completion bar."""
        self._bar.setValue(max(0, int(value)))

    def overall_maximum(self) -> int:
        return self._bar.maximum()

    # ── internals shared by subclasses ──────────────────────────────────────
    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)        # un-render immediately
                w.deleteLater()

    def _on_toggle(self) -> None:
        show = self._toggle.isChecked()
        self._body.setVisible(show)
        self._toggle.setText("▾" if show else "▸")

    # ── hooks a subclass must implement ─────────────────────────────────────
    def _build_rail(self) -> None:
        raise NotImplementedError

    def _build_legend(self) -> None:
        raise NotImplementedError

    def _well_tooltip(self, well_id: str) -> str:
        raise NotImplementedError
