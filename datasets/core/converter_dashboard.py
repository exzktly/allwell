"""At-a-glance per-well progress view for a packed-file converter.

A :class:`~widgets.plate_progress.PlateProgress` subclass, the same chrome
every other Datasets tool's status section uses. It differs from the
stage-based dashboards (Zip Loose Folder, Subset) in what a well's progress
*is*: a conversion works one well from start to finish rather than moving it
through phases, so each well is drawn as a dial filling from 0 to 1 and the
rail counts wells rather than steps.

The plate is also the page's answer to "did it read my wells correctly?".
Before a run it is filled in with the mapping the settings currently resolve
to, so the wells a conversion is *about* to write are on screen and wrong ones
are visible while they are still cheap to fix. That matters more here than on
the OME-TIFF page, whose wells always come from a grid the user typed: a CZI's
and an ND2's wells usually come from the file, and the plate is the only place
a user can see what the file said.

Shared between the CZI and ND2 converters. The measurement that kept their
*pages* apart (see :mod:`datasets.core.channel_roles_row`) does not apply
here — this class was already identical between them bar its name, its
docstring and its object name.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ui import theme
from widgets.plate_progress import PlateProgress, canon_well, status_dot

#: How far through a well counts as "being converted" rather than "pending".
#: Any fraction above zero: the first plane written is the tell, and a well
#: sitting at 0.0 has had a dial created for it without anything landing yet.
_STARTED = 0.0


class ConverterDashboard(PlateProgress):
    """Wells of a conversion: planned, in flight, and finished."""

    def __init__(self, parent: QWidget | None = None, *,
                 object_name: str = "ConverterDashboard") -> None:
        self._wells: list[str] = []
        self._fraction: dict[str, float] = {}
        super().__init__("Convert Progress", parent)
        # Named per converter so a QSS rule or a test can address one
        # page's plate without reaching every converter's at once.
        self.setObjectName(object_name)

    def reset(self) -> None:
        """Back to idle, keeping no trace of the previous run's dials."""
        self._fraction = dict.fromkeys(self._wells, 0.0)
        self.clear_outcome()
        self.clear_well_fractions()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._plate.clearWellMarkers()
        for well in self._wells:
            self._plate.setWellColor(well, theme.Colors.text_muted)
        self.set_overall_range(len(self._wells))
        self.set_overall(0)
        self._refresh_rail()
        self._headline.setText("Idle")
        self._headline.setStyleSheet(f"color:{theme.Colors.text_primary};")
        self._eta.setText("")
        self._status.setText("")

    def set_wells(self, well_ids) -> None:
        """Show *well_ids* as the wells this conversion covers, all pending.

        Sorted and de-duplicated, because this is a picture of a plate rather
        than of an acquisition order — the order is what the readout states in
        words, and two wells cannot occupy one cell.

        **An unchanged well set leaves the plate alone.** The page redraws its
        readout whenever anything it depends on changes, and one of those
        moments is the end of a run: the controller drops the running flag and
        refreshes, the plan resolves to the same wells it always did, and a
        version of this method that reset unconditionally wiped every dial and
        every ✓ a second after the run finished — a conversion that had just
        reported "Wrote 2 image(s) for 2 well(s)" above a plate reading
        "Pending: 2" and a bar back at zero. Making the no-op a property of the
        dashboard rather than a flag on the page keeps the other direction
        working: a mapping that really did change is a different well set, and
        resetting is then exactly right.
        """
        wells = sorted({canon_well(w) for w in well_ids} - {""})
        if wells == self._wells:
            return
        self._wells = wells
        self._fraction = dict.fromkeys(wells, 0.0)
        # Empty rather than a full 96-well plate: an "enabled but uncoloured"
        # plate states a scope the conversion does not have, and a failure that
        # lands before the mapping resolves would leave that misstatement as
        # the last thing on screen.
        self._plate.setEnabledWells(wells)
        self.clear_well_fractions()
        self._plate.clearWellMarkers()
        for well in wells:
            self._plate.setWellColor(well, theme.Colors.text_muted)
        self.set_overall_range(len(wells))
        self.set_overall(0)
        self._refresh_rail()

    def set_well_progress(self, well_id: str, done: int, total: int) -> None:
        """Fill *well_id*'s dial, and finish it when it reaches *total*."""
        well = canon_well(well_id)
        if not well or well not in self._fraction:
            return
        fraction = 0.0 if total <= 0 else max(0.0, min(1.0, done / total))
        self._fraction[well] = fraction
        super().set_well_progress(well, done, total)
        self._plate.setWellColor(well, self._well_color(well))
        # Terminal state carries a marker as well as a colour, because colour
        # alone is unreadable to anyone who cannot distinguish success-green
        # from muted grey. A well still filling carries none: the glyph would
        # sit in the middle of the area the dial fills, and the dial already
        # says the well is being worked on.
        self._plate.setWellMarker(well, "✓" if fraction >= 1.0 else None)
        self.set_overall(self._converted())
        self._refresh_rail()

    def _converted(self) -> int:
        return sum(1 for f in self._fraction.values() if f >= 1.0)

    def _well_color(self, well: str) -> str:
        fraction = self._fraction.get(well, 0.0)
        if fraction >= 1.0:
            return theme.Colors.success
        if fraction > _STARTED:
            return theme.Colors.warn
        return theme.Colors.text_muted

    def _well_tooltip(self, well_id: str) -> str:
        well = canon_well(well_id)
        if well not in self._fraction:
            return f"{well or well_id}: not part of this conversion"
        fraction = self._fraction[well]
        if fraction >= 1.0:
            return f"{well}: converted"
        if fraction > _STARTED:
            return f"{well}: converting ({fraction * 100:.0f}%)"
        return f"{well}: pending"

    def _counts(self) -> list[tuple[str, str, int]]:
        """``(state, label, count)`` for the rail and the legend, one source."""
        converted = self._converted()
        started = sum(1 for f in self._fraction.values()
                      if _STARTED < f < 1.0)
        return [
            ("pending", "Pending", len(self._fraction) - converted - started),
            ("converting", "Converting", started),
            ("converted", "Converted", converted),
        ]

    def _build_rail(self) -> None:
        self._clear(self._rail_lay)
        head = QLabel("Wells", self._rail_host)
        head.setStyleSheet(f"color:{theme.Colors.text_primary}; font-weight:600;")
        self._rail_lay.addWidget(head)
        for state, label, count in self._counts():
            row_host = QWidget(self._rail_host)
            row = QHBoxLayout(row_host)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(7)
            row.addWidget(status_dot(self._state_color(state)))
            text = QLabel(f"{label}: {count}", row_host)
            text.setStyleSheet(f"color:{theme.Colors.text_primary};")
            row.addWidget(text)
            row.addStretch(1)
            self._rail_lay.addWidget(row_host)
        self._rail_lay.addStretch(1)

    def _refresh_rail(self) -> None:
        self._build_rail()

    @staticmethod
    def _state_color(state: str) -> str:
        return {
            "pending": theme.Colors.text_muted,
            "converting": theme.Colors.warn,
            "converted": theme.Colors.success,
        }[state]

    def _legend_items(self):
        # Through ``_state_color``, so the legend and the plate cannot disagree
        # about what a state looks like.
        return "Legend", [
            (self._state_color(state), label.lower())
            for state, label, _count in self._counts()
        ]
