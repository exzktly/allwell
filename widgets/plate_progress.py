"""Shared chrome for a live per-well run-status dashboard.

Holds what :class:`~widgets.run_dashboard.RunDashboard` (Analyze) and
:class:`~datasets.concatenate.dashboard.ConcatDashboard` (Concatenate) have in
common: a header (title/status/eta/collapse toggle), a headline label, one
overall completion bar, and a 3-column body — a subclass-owned left "rail"
slot, the 8x12 well plate map (each well drawn as a progress dial while it
is being worked on), and a legend. Extracted so the two dashboards' layout/plate/legend wiring
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
    QFrame, QHBoxLayout, QLabel, QProgressBar, QToolButton, QVBoxLayout, QWidget,
)

from widgets.plate_geometry import PLATE_ALIGNMENT, apply_plate_geometry
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


def fraction_or_none(done: int, total: int) -> float | None:
    """``done / total`` clamped to 0.0–1.0, or ``None`` when *total* is not
    positive.

    A caller that has not counted the work yet is reporting "unknown", and an
    unknown amount of progress must not render as a definite zero-filled dial
    — that is the same picture as a well that has genuinely done nothing.

    Module-level rather than a method because both kinds of dial host need it:
    :meth:`PlateProgress.set_well_progress`, and a controller that paints its
    page's *own* plate instead of a dashboard's (``datasets.subset.controller``
    — see :meth:`PlateProgress.set_well_fraction` for why it has no dashboard
    plate to reach). One rule, one home, no second copy to drift.
    """
    if int(total) <= 0:
        return None
    return max(0.0, min(1.0, max(0, int(done)) / int(total)))


class PlateProgress(QWidget):
    """Header/headline/overall-bar/plate+legend chrome, shared by RunDashboard
    and ConcatDashboard. A subclass builds its own left-slot content (a stage
    or phase rail) into ``self._rail_host``/``self._rail_lay`` via its own
    ``_build_rail``/``_refresh_rail``/``_build_legend``, and provides
    ``_well_tooltip`` for the plate's hover text."""

    def __init__(
        self, title: str, parent: QWidget | None = None, *,
        show_plate: bool = True, show_legend: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PlateProgress")
        self._show_plate = show_plate
        self._show_legend = show_legend

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
        # A real failure message (e.g. naming every colliding well token) is
        # long enough to force a horizontal scrollbar on the whole page and
        # push everything above the dashboard off-screen without this — the
        # one label in this widget with no upper bound on its content length.
        self._headline.setWordWrap(True)
        root.addWidget(self._headline)

        # The single authoritative overall-completion bar for the whole run.
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100); self._bar.setValue(0)
        self._bar.setTextVisible(False)
        root.addWidget(self._bar)

        # body: rail | plate | legend
        self._body = QWidget(self)
        body = QHBoxLayout(self._body)
        body.setContentsMargins(0, 4, 0, 0); body.setSpacing(14)

        self._rail_host = QWidget(self._body)
        self._rail_lay = QVBoxLayout(self._rail_host)
        self._rail_lay.setContentsMargins(0, 0, 0, 0); self._rail_lay.setSpacing(8)
        self._rail_host.setFixedWidth(238)
        body.addWidget(self._rail_host)

        if show_plate or show_legend:
            d1 = QFrame(self._body); d1.setFrameShape(QFrame.VLine); d1.setObjectName("VRule")
            body.addWidget(d1)

        # A subclass whose page already has its own interactive plate map
        # for input (e.g. Subset's well picker) passes show_plate=False —
        # driving progress onto that widget directly instead of a second,
        # redundant status plate here. Skipping the whole centre column
        # (not just hiding self._plate) avoids leaving a wide dead gap
        # between the rail and the legend, the same class of empty-space
        # bug a QSizePolicy/maximumWidth mismatch caused on self._plate
        # itself elsewhere in this file.
        if show_plate:
            self._center_host = QWidget(self._body)
            center = QVBoxLayout(self._center_host)
            center.setContentsMargins(0, 0, 0, 0); center.setSpacing(6)

            self._plate = WellPlateSelector(self._center_host)
            self._plate.setDragSelectEnabled(False)
            self._plate.setRowColumnSelectable(False)
            # Paint coloured wells in the raised/lit style (not the dim recessed
            # tint) so run states read clearly on the status map.
            self._plate.setColoredWellsLit(True)
            self._plate.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            # The app-wide plate geometry (widgets/plate_geometry.py): the
            # "status" role hides the built-in action row and makes the plate
            # read-only, and the caps give it the same cell scale as every other
            # plate map in the app. Centred, like all of them — the centre column
            # is as wide as the page allows, so a plate pinned to one edge of it
            # reads as misaligned against the picker above.
            apply_plate_geometry(self._plate, role="status")
            self._plate.setWellTooltipProvider(self._well_tooltip)
            center.addWidget(self._plate, 0, PLATE_ALIGNMENT)

            center.addStretch(1)
            body.addWidget(self._center_host, 1)

            if show_legend:
                d2 = QFrame(self._body); d2.setFrameShape(QFrame.VLine); d2.setObjectName("VRule")
                body.addWidget(d2)

        if show_legend:
            self._legend_host = QWidget(self._body)
            self._legend_lay = QVBoxLayout(self._legend_host)
            self._legend_lay.setContentsMargins(0, 0, 0, 0); self._legend_lay.setSpacing(8)
            # Wide enough for the longest legend string in current use
            # ("merged — no CSV on one side") without wrapping; subclasses'
            # legend labels also carry setWordWrap(True) as a second line of
            # defence, so a future longer label wraps to two lines instead of
            # being silently clipped the way a fixed-but-too-narrow column did.
            self._legend_host.setFixedWidth(190)
            body.addWidget(self._legend_host)
        if not show_plate:
            # With the centre column skipped there is no widget carrying a
            # stretch factor left in this layout — every remaining item
            # (rail_host, any divider, legend_host) is fixed-width, so with
            # nowhere explicit to put the page's leftover width Qt
            # distributed it as blank padding around the fixed items instead,
            # reading as a wide dead gap. A trailing stretch collects that
            # leftover at the row's own right edge instead, keeping
            # rail/divider/legend packed together at the left the way the
            # column layout intends.
            body.addStretch(1)

        root.addWidget(self._body)
        self._build_rail()
        if show_legend:
            self._build_legend()

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

    # ── per-well progress dials ─────────────────────────────────────────────
    # A well's colour says which stage it reached; it cannot say how far through
    # that stage it is, so a well on its first FOV-group and one on its last look
    # identical for however long the well takes. These three promote
    # ``WellPlateSelector``'s own fraction API onto the dashboard so every tool
    # can draw that progress as a dial on the well itself, and so no subclass has
    # to reach through ``self._plate`` — which does not exist at all when the
    # dashboard is built with ``show_plate=False`` (SubsetDashboard paints run
    # progress onto its page's own picker instead). All three no-op in that case
    # rather than raising.
    #
    # A no-op is the right base-class behaviour, but it is not a progress
    # report: a controller whose dashboard has no plate must paint the plate it
    # does own rather than call these and assume something happened. Subset is
    # that case, and it drives its picker plate's ``setWellFraction`` directly
    # through the shared ``fraction_or_none`` above.

    def set_well_fraction(self, well_id: str, fraction: float | None) -> None:
        """Fill *well_id*'s dial to *fraction* (0.0–1.0), or drop it with ``None``."""
        plate = getattr(self, "_plate", None)
        if plate is None:
            return
        well = canon_well(well_id)
        if not well:
            return
        if fraction is None:
            plate.setWellFraction(well, None)
            return
        plate.setWellFraction(well, max(0.0, min(1.0, float(fraction))))

    def set_well_progress(self, well_id: str, done: int, total: int) -> None:
        """Fill *well_id*'s dial to ``done / total``, per :func:`fraction_or_none`
        — a non-positive *total* clears the dial instead of dividing by it."""
        self.set_well_fraction(well_id, fraction_or_none(done, total))

    def clear_well_fractions(self) -> None:
        """Drop every dial, leaving the wells' own state colours untouched."""
        plate = getattr(self, "_plate", None)
        if plate is not None:
            plate.clearWellFractions()

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
