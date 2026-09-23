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

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from ui import theme
from widgets.plate_progress import PlateProgress, canon_well, status_dot

#: How far through a well counts as "being converted" rather than "pending".
#: Any fraction above zero: the first plane written is the tell, and a well
#: sitting at 0.0 has had a dial created for it without anything landing yet.
_STARTED = 0.0


class ConverterDashboard(PlateProgress):
    """Wells of a conversion: planned, in flight, and finished.

    The plate is also where a **subset** of the wells is chosen. It already
    shows the mapping the settings resolve to, so picking on it means picking
    from the wells the source actually has — a selection cannot name a well
    that is not there, which a typed list can. Nothing selected means the
    whole plate, which is the default and what every run did before subsets
    existed.
    """

    #: The wells the user has picked, or ``[]`` for the whole plate.
    wellsSelected = Signal(list)

    def __init__(self, parent: QWidget | None = None, *,
                 object_name: str = "ConverterDashboard") -> None:
        self._wells: list[str] = []
        self._fraction: dict[str, float] = {}
        super().__init__("Convert Progress", parent)
        # A rim rather than a fill, because the same cells carry the run's
        # progress colours: a selection drawn as a fill would be repainted by
        # the first well that started converting.
        self._plate.setSelectionRim(True)
        self._plate.selectionChanged.connect(self._on_selection_changed)
        self._build_pick_row()
        self.set_selection_enabled(True)
        # Named per converter so a QSS rule or a test can address one
        # page's plate without reaching every converter's at once.
        self.setObjectName(object_name)

    def _on_selection_changed(self, ids) -> None:
        self._refresh_pick_note()
        self.wellsSelected.emit(list(ids))

    def selected_wells(self) -> list[str]:
        """The picked wells, or ``[]`` meaning the whole plate."""
        return list(self._plate.selectedWellIds())

    def set_selected_wells(self, ids) -> None:
        """Restore a selection — the Job Queue's edit path, and the buttons."""
        self._plate.setSelectedWellIds(list(ids or []))
        self._refresh_pick_note()

    def set_selection_enabled(self, on: bool) -> None:
        """Allow or forbid picking wells, with everything picking needs.

        Forbidden while a run is in flight: the plate is the progress display
        then, and a selection changed mid-run would describe a conversion
        other than the one being reported on.

        **Four things, not one.** ``PlateProgress`` builds its plate as a
        status map — ``apply_plate_geometry(role="status")`` makes it
        report-only, and the constructor turns off drag-select and the
        row/column headers — because for most of its life that is what it is.
        Turning the selection *mode* on over the top of that left exactly one
        way to pick: clicking wells one at a time. On a 96-well plate, taking
        all but three wells is ninety-three clicks, which is not a way of
        doing it — and none of the usual affordances were there to suggest
        the plate could be clicked at all.

        So picking mode restores the corner select-all and the pointing-hand
        cursor (``setReportOnly(False)``), the drag sweep, and the row and
        column headers. A run puts all four back, because then the plate is
        reporting rather than asking.

        The plate's own action row stays hidden either way. It is shown by
        ``role="picker"``, which also reserves height for it, and a row that
        appeared when a run ended would resize the plate under the user's
        cursor — so this dashboard carries its own buttons instead, in a line
        under the plate that is always there.
        """
        self._plate.setSelectionMode("select" if on else "passive")
        self._plate.setReportOnly(not on)
        self._plate.setDragSelectEnabled(on)
        self._plate.setRowColumnSelectable(on)
        self._pick_row.setEnabled(on)
        self._refresh_pick_note()

    # ── saying that the plate is a picker, and what it has picked ───────────

    def _build_pick_row(self) -> None:
        """A line under the plate: what picking does, and two ways to do it.

        Under the plate rather than in the header, because it describes the
        plate and the eye arrives there after it. The buttons are this
        widget's own rather than the plate's built-in action row for the
        reason in :meth:`set_selection_enabled`, and they say what they do to
        *a conversion* — "Convert all" rather than "None", which is what
        clearing the selection means here and is the opposite of what the word
        would suggest.
        """
        self._pick_row = QWidget(self)
        row = QHBoxLayout(self._pick_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.Spacing.sm)

        self._pick_note = QLabel("", self._pick_row)
        self._pick_note.setObjectName("Muted")
        self._pick_note.setWordWrap(True)
        row.addWidget(self._pick_note, 1)

        self._pick_all = QPushButton("Convert all", self._pick_row)
        self._pick_all.setToolTip(
            "Clear the selection, so every well the source holds is converted")
        self._pick_all.clicked.connect(lambda: self.set_selected_wells([]))
        row.addWidget(self._pick_all)

        self._pick_invert = QPushButton("Invert", self._pick_row)
        self._pick_invert.setToolTip(
            "Convert the wells that are not picked, and skip the ones that are")
        self._pick_invert.clicked.connect(self._invert_selection)
        row.addWidget(self._pick_invert)

        host = self._center_host.layout()
        # Directly under the plate, before the stretch that holds it up.
        host.insertWidget(host.indexOf(self._plate) + 1, self._pick_row)

    def _invert_selection(self) -> None:
        """Swap the wells that convert for the ones that do not.

        The shortest way to say "everything but these", which is how a person
        with three bad wells on a full plate thinks about it — and the one
        thing the include-only model cannot express in fewer clicks than there
        are wells.

        Inverting nothing selects every well rather than leaving the plate
        empty. That is the same conversion either way, but it says so on the
        plate instead of leaving a control that appears to have done nothing.
        """
        picked = set(self._plate.selectedWellIds())
        self.set_selected_wells([w for w in self._wells if w not in picked])

    def _refresh_pick_note(self) -> None:
        """Say which wells convert and which are skipped, in those words.

        The count is what makes a subset legible: a rim drawn on twelve of
        ninety-six cells is visible only once you know to look for it, and a
        plate with nothing picked and a plate with everything picked convert
        the same wells while looking completely different.
        """
        if not hasattr(self, "_pick_note"):
            return
        total = len(self._wells)
        picked = len(self._plate.selectedWellIds())
        if not self._pick_row.isEnabled():
            self._pick_note.setText("")
        elif not total:
            self._pick_note.setText("Pick a source to choose wells.")
        elif not picked:
            self._pick_note.setText(
                f"Converting all {total} well(s) — click to convert only some.")
        else:
            self._pick_note.setText(
                f"Converting {picked} of {total} well(s); "
                f"{total - picked} ignored.")

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
        self._refresh_pick_note()
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
        # The note quotes the well count, which has just changed.
        self._refresh_pick_note()
        self._refresh_rail()

    def well_ids(self) -> list[str]:
        """The wells this plate is currently showing, sorted.

        Public because a page has to be able to ask whether the plate has been
        filled in yet: a run started from the Job Queue begins before the
        source scan has landed, and a plate that is still empty then silently
        discards every well-progress signal of the whole conversion.
        """
        return list(self._wells)

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
